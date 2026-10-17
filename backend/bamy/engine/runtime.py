"""引擎运行时：策略调度（Runner）、订单中心（五段处理）、保护中心、对账、崩溃恢复。

实例是唯一运行单元；所有下单必须经过订单中心的完整路径（《DESIGN》原则 3、边界 3）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from bamy.core.config import settings
from bamy.core.errors import ApiError
from bamy.db.models import (
    Account, Instance, InstanceState, Notification, Order, PaperAccount, PaperSnapshot,
    PluginState, PositionSnapshot, ReconcileDiff, RiskRule, SystemLog, User,
)
from bamy.db.session import session_scope
from bamy.engine.adapters import ExchangeError, ExchangeOrder, live_trading_enabled
from bamy.engine.client_pool import pool
from bamy.engine.eventbus import bus
from bamy.engine.market import is_scan_symbol, market
from bamy.engine.paper import FuturesBook, PaperBook, PaperConfig, is_futures_symbol, load_futures_positions, load_json_map
from bamy.engine.pluginhub import plugin_hub
from bamy.engine.risk import RiskRejected, risk_engine
from bamy.extensions.base import OrderView, StateStore, Tick
from bamy.extensions.registry import registry
from bamy.extensions.schema import SchemaError, decrypt_secrets, mask_secrets, validate
from bamy.services import system_config
from bamy.services.audit import write_audit

log = logging.getLogger("bamy.engine")
HEARTBEAT_SEC = 900   # 每个运行中实例至少 15 分钟写一行"运行中"日志

TRANSITIONAL = {"starting", "pausing", "resuming", "stopping"}
STATE_LIMIT = 65536
START_TIMEOUT = 30
STOP_TIMEOUT = 30
MAX_CONSECUTIVE_ERRORS = 5


@dataclass
class Running:
    """内存中的运行实例。"""
    instance_id: int
    owner_id: int
    account_id: int | None
    symbol: str
    paper: bool
    strategy: Any
    ctx: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    paused: bool = False
    book: PaperBook | FuturesBook | None = None
    day_start_equity: float | None = None
    day_key: str = ""
    extra_subs: set[str] = field(default_factory=set)   # 扫描实例：已持仓币种的单币订阅（模拟撮合/强平用）
    market_type: str = "spot"


class Engine:
    def __init__(self) -> None:
        self.running: dict[int, Running] = {}
        self._op_locks: dict[int, asyncio.Lock] = {}
        self._books: dict[int, PaperBook | FuturesBook] = {}
        self._book_locks: dict[int, asyncio.Lock] = {}
        self.ready = False
        self.started_at = time.time()
        self._log_buffer: list[SystemLog] = []
        self._ws_broadcast: Any = None
        self._hb_at: dict[int, float] = {}   # 实例心跳：上次写"运行中"日志的时间

    # ======================================================================
    # 公共工具
    # ======================================================================
    def op_lock(self, instance_id: int) -> asyncio.Lock:
        return self._op_locks.setdefault(instance_id, asyncio.Lock())

    def set_broadcaster(self, fn) -> None:
        self._ws_broadcast = fn

    async def push(self, event: str, data: dict[str, Any]) -> None:
        await bus.publish(event, data)
        if event in NOTIFY_EVENTS:
            await self._persist_notice(event, data)
            from bamy.services import webhook

            asyncio.get_running_loop().create_task(webhook.deliver(event, data))   # 不等待：用户 webhook 慢不拖累引擎
        if self._ws_broadcast is not None:
            try:
                await self._ws_broadcast(event, data)
            except Exception:  # noqa: BLE001
                log.exception("WebSocket 推送失败")

    def log(self, instance_id: int | None, level: str, message: str, *, module: str = "strategy",
            account_id: int | None = None) -> None:
        level = {"warning": "warn"}.get(level, level)
        getattr(log, {"warn": "warning"}.get(level, level), log.info)("[实例 %s] %s", instance_id, message)
        self._log_buffer.append(SystemLog(level=level, module=module, instance_id=instance_id,
                                          account_id=account_id, message=message[:4000]))
        if self._ws_broadcast is not None and instance_id is not None:
            run = self.running.get(instance_id)
            asyncio.get_event_loop().create_task(self._ws_broadcast("instance.log", {
                "instance_id": instance_id, "owner_id": run.owner_id if run else None,
                "level": level, "message": message, "ts": int(time.time() * 1000)}))

    async def flush_logs(self) -> None:
        if not self._log_buffer:
            return
        batch, self._log_buffer = self._log_buffer, []
        async with session_scope() as db:
            db.add_all(batch)
            await db.commit()

    async def _persist_notice(self, event: str, data: dict[str, Any]) -> None:
        """关键事件写入通知表：刷新页面、换设备后通知中心仍能看到（WS 推送只负责实时）。"""
        level, title = NOTIFY_EVENTS[event]
        body = str(data.get("reason") or data.get("error") or data.get("message") or "")[:2000] or None
        ref = data.get("instance_id") or data.get("account_id")
        try:
            async with session_scope() as db:
                db.add(Notification(owner_id=data.get("owner_id"), level=level, source=event,
                                    title=f"{title}{f' #{ref}' if ref else ''}"[:128], body=body))
                await db.commit()
        except Exception:  # noqa: BLE001 - 通知落库失败不影响主流程
            log.exception("通知写入失败 event=%s", event)

    async def notify(self, title: str, body: str, level: str = "info", *, owner_id: int | None = None,
                     plugin_id: str | None = None, binding_id: int | None = None) -> None:
        async with session_scope() as db:
            if owner_id is None and binding_id is not None:
                from bamy.db.models import PluginBinding

                b = await db.get(PluginBinding, binding_id)
                owner_id = b.owner_id if b and b.scope != "global" else None
            db.add(Notification(owner_id=owner_id, level=level, title=title[:128], body=body,
                                source=f"plugin:{plugin_id}" if plugin_id else "system"))
            await db.commit()
        if self._ws_broadcast is not None:
            await self._ws_broadcast("notification", {"owner_id": owner_id, "title": title, "body": body,
                                                      "level": level})

    # ---- 实例级状态（ctx.state） ----
    async def state_get(self, instance_id: int, key: str) -> str | None:
        async with session_scope() as db:
            row = (await db.execute(select(InstanceState).where(
                InstanceState.instance_id == instance_id, InstanceState.key == key))).scalar_one_or_none()
            return row.value if row else None

    async def state_set(self, instance_id: int, key: str, value: str) -> None:
        async with session_scope() as db:
            rows = (await db.execute(select(InstanceState).where(InstanceState.instance_id == instance_id))).scalars().all()
            size = sum(len(r.value or "") for r in rows if r.key != key) + len(value)
            if size > STATE_LIMIT:
                raise ValueError(f"策略状态超过上限 {STATE_LIMIT} 字节")
            row = next((r for r in rows if r.key == key), None)
            if row is None:
                db.add(InstanceState(instance_id=instance_id, key=key[:64], value=value))
            else:
                row.value = value
            await db.commit()

    async def state_delete(self, instance_id: int, key: str) -> None:
        async with session_scope() as db:
            await db.execute(delete(InstanceState).where(InstanceState.instance_id == instance_id,
                                                         InstanceState.key == key))
            await db.commit()

    async def plugin_state_set(self, binding_id: int, key: str, value: str) -> None:
        async with session_scope() as db:
            row = (await db.execute(select(PluginState).where(PluginState.binding_id == binding_id,
                                                              PluginState.key == key))).scalar_one_or_none()
            if len(value) > STATE_LIMIT:
                raise ValueError("插件状态超过上限")
            if row is None:
                db.add(PluginState(binding_id=binding_id, key=key[:64], value=value))
            else:
                row.value = value
            await db.commit()

    async def plugin_state_delete(self, binding_id: int, key: str) -> None:
        async with session_scope() as db:
            await db.execute(delete(PluginState).where(PluginState.binding_id == binding_id, PluginState.key == key))
            await db.commit()

    # ======================================================================
    # 上下文
    # ======================================================================
    def _make_ctx(self, inst: Instance, params: dict[str, Any]):
        from bamy.strategy import StrategyContext

        iid = inst.id

        async def load(key: str) -> str | None:
            return await self.state_get(iid, key)

        async def save(key: str, value: str) -> None:
            await self.state_set(iid, key, value)

        async def remove(key: str) -> None:
            await self.state_delete(iid, key)

        return StrategyContext(
            instance_id=iid, symbol=inst.symbol, params=dict(params), account_id=inst.account_id,
            paper=bool(inst.paper), market_type=inst.market_type, state=StateStore(load, save, remove),
            _engine=self, _name=inst.name, _owner_id=inst.owner_id,
        )

    async def _paper_book(self, db, inst: Instance) -> PaperBook | FuturesBook:
        book = self._books.get(inst.id)
        if book is not None:
            return book
        # 详情页会同时发好几个请求，都可能是第一次加载：同一实例串行，避免重复建模拟账户撞唯一约束（→ 409 资源冲突）
        async with self._book_locks.setdefault(inst.id, asyncio.Lock()):
            book = self._books.get(inst.id)
            if book is not None:
                return book
            acct = (await db.execute(select(PaperAccount).where(PaperAccount.instance_id == inst.id))).scalar_one_or_none()
            cfg = PaperConfig.from_json(inst.paper_config, {
                "fee_rate": system_config.get("paper.default_fee_rate", 0.001),
                "slippage": system_config.get("paper.default_slippage", 0.0005),
                "leverage": _leverage_of(inst),
            })
            if acct is None:
                initial = Decimal(str(inst.paper_balance or 10000))
                try:
                    async with session_scope() as wdb:
                        wdb.add(PaperAccount(instance_id=inst.id, balance=initial, positions="{}", avg_price="{}",
                                             realized_pnl=Decimal(0), peak_equity=initial, initial_balance=initial))
                        await wdb.commit()
                except IntegrityError:
                    pass   # 另一进程/请求刚建好，下面重新读
                acct = (await db.execute(select(PaperAccount).where(PaperAccount.instance_id == inst.id)
                                         .execution_options(populate_existing=True))).scalar_one()
            if inst.market_type == "futures":
                book = FuturesBook(acct.balance, load_futures_positions(acct.positions), acct.realized_pnl or Decimal(0), cfg)
            else:
                book = PaperBook(acct.balance, load_json_map(acct.positions), load_json_map(acct.avg_price),
                                 acct.realized_pnl or Decimal(0), cfg)
            self._books[inst.id] = book
            return book

    async def _save_book(self, db, instance_id: int, book: PaperBook | FuturesBook) -> None:
        acct = (await db.execute(select(PaperAccount).where(PaperAccount.instance_id == instance_id))).scalar_one()
        acct.balance = book.balance
        acct.positions = book.dump_positions()
        acct.avg_price = book.dump_avg()
        acct.realized_pnl = book.realized_pnl

    # ======================================================================
    # 实例生命周期（《实例模型设计》第五至十二节）
    # ======================================================================
    async def _set_status(self, inst_id: int, status: str, **fields: Any) -> Instance:
        async with session_scope() as db:
            inst = await db.get(Instance, inst_id)
            inst.status = status
            for k, v in fields.items():
                setattr(inst, k, v)
            await db.commit()
            await db.refresh(inst)
        await self.push("instance.status", {"instance_id": inst_id, "owner_id": inst.owner_id,
                                            "account_id": inst.account_id, "status": status,
                                            "last_error": inst.last_error})
        return inst

    async def start(self, instance_id: int, *, actor: User | None = None, recovering: bool = False) -> Instance:
        lock = self.op_lock(instance_id)
        if lock.locked():
            raise ApiError(40902, "实例操作进行中")
        async with lock:
            async with session_scope() as db:
                inst = await db.get(Instance, instance_id)
                if inst is None or inst.deleted_at is not None:
                    raise ApiError(40403)
                if inst.status == "running" and instance_id in self.running:
                    return inst  # 幂等：已在运行
                if inst.status not in {"created", "stopped", "error"} and not recovering:
                    raise ApiError(40902, f"当前状态 {inst.status} 不能启动")
            await self._set_status(instance_id, "starting", enabled=True)
            try:
                await asyncio.wait_for(self._do_start(instance_id, actor=actor, recovering=recovering),
                                       timeout=START_TIMEOUT)
            except ApiError as exc:
                await self._set_status(instance_id, "error", last_error=exc.message)
                raise
            except asyncio.TimeoutError as exc:
                await self._teardown(instance_id)
                await self._set_status(instance_id, "error", last_error="启动超时")
                raise ApiError(42232, "启动超时") from exc
            except Exception as exc:  # noqa: BLE001 - on_start 等异常 → error
                await self._teardown(instance_id)
                await self._set_status(instance_id, "error", last_error=f"启动失败：{exc}")
                raise ApiError(42232, f"启动失败：{exc}") from exc
            inst = await self._set_status(instance_id, "running", last_started_at=datetime.utcnow(),
                                          last_error=None, consecutive_errors=0)
            async with session_scope() as db:
                await write_audit(db, action="instance.start", actor_id=actor.id if actor else None,
                                  actor_role=actor.role if actor else None, target_type="instance",
                                  target_id=instance_id, source="manual" if actor else "system",
                                  new_value={"recovering": recovering})
            return inst

    async def _do_start(self, instance_id: int, *, actor: User | None, recovering: bool) -> None:
        async with session_scope() as db:
            inst = await db.get(Instance, instance_id)
            # 1-4 策略与参数
            ext = registry.get_strategy(inst.strategy_id)
            if ext is None or not ext.loaded or ext.cls is None:
                raise ApiError(42232, "策略未加载或加载失败")
            try:
                params = validate(ext.params_schema, decrypt_secrets(ext.params_schema, json.loads(inst.params or "{}")))
            except SchemaError as exc:
                raise ApiError(exc.code, exc.message, errors=[{"field": exc.field, "reason": exc.message}]) from exc
            runtime_cfg = ext.meta.get("runtime") or {}
            if inst.market_type not in (runtime_cfg.get("market_type") or ["spot"]):
                raise ApiError(42224, "策略不支持该市场类型")
            owner = await db.get(User, inst.owner_id)
            if owner is None or owner.status != "active":
                raise ApiError(42232, "实例所属用户不可用")
            account = None
            if not inst.paper:
                # 5-8 实盘账号
                if not live_trading_enabled():
                    raise ApiError(42232, "实盘交易未开启（ENABLE_LIVE_TRADING=false）")
                account = await db.get(Account, inst.account_id) if inst.account_id else None
                if account is None or account.deleted_at is not None:
                    raise ApiError(40404)
                if account.status not in {"online"}:
                    raise ApiError(40906, f"账号状态为 {account.status}")
                if account.readonly:
                    raise ApiError(40304)
                breakers = await risk_engine.active_breakers(db, owner_id=inst.owner_id, account_id=account.id)
                if breakers:
                    raise ApiError(40910, f"熔断中：{breakers[0].reason}")
            # 10 交易对
            allowed = runtime_cfg.get("symbols")
            if allowed and inst.symbol not in allowed:
                raise ApiError(42231, "策略不支持该交易对")
            if not await market.is_valid_symbol(inst.symbol):
                raise ApiError(42231)
            # 9 模拟账户
            book = await self._paper_book(db, inst) if inst.paper else None
            snapshot = {"id": inst.id, "name": inst.name, "symbol": inst.symbol, "paper": inst.paper,
                        "owner_id": inst.owner_id, "account_id": inst.account_id,
                        "strategy_id": inst.strategy_id, "params": mask_secrets(ext.params_schema, params)}
            acct_view = ({"id": account.id, "name": account.name, "env": account.env, "status": account.status}
                         if account else None)
        # 实盘：启动前对账
        if account is not None:
            await self.reconcile_account(account.id, reason="start")
        # 检查点：instance.before_start
        res = await plugin_hub.intercept("instance.before_start",
                                         {"instance": snapshot, "account": acct_view,
                                          "strategy_meta": {"id": ext.id, "version": ext.version,
                                                            "name": ext.meta.get("name")}},
                                         owner_id=snapshot["owner_id"], account_id=snapshot["account_id"])
        if not res.allowed:
            raise ApiError(res.code or 42290, res.message, data=res.detail)
        # 上下文与策略对象
        async with session_scope() as db:
            inst = await db.get(Instance, instance_id)
            ctx = self._make_ctx(inst, params)
        strategy = ext.cls()
        strategy.ctx = ctx
        strategy._sink = lambda level, msg: self.log(instance_id, level, msg)
        run = Running(instance_id=instance_id, owner_id=snapshot["owner_id"], account_id=snapshot["account_id"],
                      symbol=snapshot["symbol"], paper=snapshot["paper"], strategy=strategy, ctx=ctx, book=book,
                      market_type=ctx.market_type)
        self.running[instance_id] = run
        await strategy.on_start()
        self._subscribe(run)

    def _subscribe(self, run: Running) -> None:
        iid = run.instance_id
        market.subscribe(run.symbol, f"inst:{iid}", lambda tick, _id=iid: self._on_tick(_id, tick))
        self._sync_extra_subs(run)

    def _unsubscribe(self, run: Running) -> None:
        market.unsubscribe(run.symbol, f"inst:{run.instance_id}")
        for sym in list(run.extra_subs):
            market.unsubscribe(sym, f"inst:{run.instance_id}:pos")
        run.extra_subs.clear()

    def _sync_extra_subs(self, run: Running) -> None:
        """扫描实例：持仓/挂单涉及的币单独订阅 1 秒行情，用于模拟撮合与强平；平仓后退订。"""
        if not is_scan_symbol(run.symbol) or run.paused:
            return
        want: set[str] = set(getattr(run.strategy, "watched", None) or ())
        if run.book is not None:
            want |= set(run.book.price_keys().values())
            want |= {o["symbol"] for o in run.book.open_orders.values()}
        for sym in want - run.extra_subs:
            market.subscribe(sym, f"inst:{run.instance_id}:pos", lambda tick, _id=run.instance_id: self._on_pos_tick(_id, tick))
        for sym in run.extra_subs - want:
            market.unsubscribe(sym, f"inst:{run.instance_id}:pos")
        run.extra_subs = want

    async def _on_pos_tick(self, instance_id: int, raw: dict[str, Any]) -> None:
        run = self.running.get(instance_id)
        if run is None or run.paused:
            return
        if run.book is not None:
            await self._match_paper(run, float(raw["price"]), raw["symbol"])
            await self._check_liquidation(run, {raw["symbol"]: float(raw["price"])})
        # 持仓币的 1 秒行情也交给策略（止盈止损要及时）；与扫描 tick 共用实例锁，串行执行
        if run.lock.locked():
            return
        tick = Tick(symbol=raw["symbol"], price=float(raw["price"]), bid=_f(raw.get("bid")),
                    ask=_f(raw.get("ask")), volume=_f(raw.get("volume")), ts=int(raw.get("ts") or 0))
        async with run.lock:
            try:
                await run.strategy.on_tick(tick)
            except Exception as exc:  # noqa: BLE001
                await self._strategy_error(instance_id, exc)
        self._sync_extra_subs(run)

    # ---- 运行 ----
    async def _on_tick(self, instance_id: int, raw: dict[str, Any]) -> None:
        run = self.running.get(instance_id)
        if run is None or run.paused:
            return
        tick = Tick(symbol=raw["symbol"], price=float(raw["price"]), bid=_f(raw.get("bid")),
                    ask=_f(raw.get("ask")), volume=_f(raw.get("volume")), ts=int(raw.get("ts") or 0))
        scan = bool(raw.get("scan"))
        if run.book is not None and not scan:
            await self._match_paper(run, tick.price, tick.symbol)
            await self._check_liquidation(run, {tick.symbol: tick.price})
        now = time.time()
        if now - self._hb_at.get(instance_id, 0) >= HEARTBEAT_SEC:
            # 心跳：即使策略在等机会、一笔不下，日志里也能看到它活着、看着什么价格
            self._hb_at[instance_id] = now
            pos_txt = run.book.held_text() if run.book is not None else "实盘"
            what = f"扫描 {int(tick.volume or 0)} 个交易对" if scan else f"{tick.symbol} 最新价 {tick.price:g}"
            self.log(instance_id, "info", f"运行中 · {what} · {pos_txt}", module="engine")
        if run.lock.locked():
            return  # 同一实例串行：上一个 tick 未处理完则跳过本次
        async with run.lock:
            try:
                await run.strategy.on_tick(tick)
                await self._reset_errors(instance_id)
            except Exception as exc:  # noqa: BLE001 - 策略异常由框架捕获
                await self._strategy_error(instance_id, exc)
        if scan:
            self._sync_extra_subs(run)

    async def _reset_errors(self, instance_id: int) -> None:
        run = self.running.get(instance_id)
        if run is not None and getattr(run, "_err_count", 0):
            run._err_count = 0  # type: ignore[attr-defined]
            async with session_scope() as db:
                inst = await db.get(Instance, instance_id)
                inst.consecutive_errors = 0
                await db.commit()

    async def _strategy_error(self, instance_id: int, exc: Exception) -> None:
        run = self.running.get(instance_id)
        if run is None:
            return
        count = getattr(run, "_err_count", 0) + 1
        run._err_count = count  # type: ignore[attr-defined]
        self.log(instance_id, "error", f"策略异常（连续第 {count} 次）：{exc!r}")
        async with session_scope() as db:
            inst = await db.get(Instance, instance_id)
            inst.consecutive_errors = count
            inst.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            await db.commit()
        await self.push("instance.error", {"instance_id": instance_id, "owner_id": run.owner_id,
                                           "account_id": run.account_id, "error": str(exc), "count": count})
        if count >= MAX_CONSECUTIVE_ERRORS:
            asyncio.create_task(self.auto_stop(instance_id, f"连续异常 {count} 次，已自动停止"))

    async def auto_stop(self, instance_id: int, reason: str) -> None:
        try:
            await self.stop(instance_id, actor=None, reason=reason, final_status="error")
        except ApiError:
            pass
        run_owner = None
        async with session_scope() as db:
            inst = await db.get(Instance, instance_id)
            run_owner = inst.owner_id if inst else None
            await write_audit(db, action="instance.auto_stopped", target_type="instance", target_id=instance_id,
                              new_value={"reason": reason}, source="system")
        await self.push("instance.auto_stopped", {"instance_id": instance_id, "owner_id": run_owner,
                                                  "reason": reason})

    async def auto_pause(self, instance_id: int, reason: str) -> None:
        if instance_id not in self.running:
            return
        try:
            await self.pause(instance_id, actor=None)
        except ApiError:
            return
        await self._set_status(instance_id, "paused", last_error=reason)

    # ---- 暂停 / 恢复 ----
    async def pause(self, instance_id: int, *, actor: User | None) -> Instance:
        async with self.op_lock(instance_id):
            run = self.running.get(instance_id)
            async with session_scope() as db:
                inst = await db.get(Instance, instance_id)
            if inst is None or inst.deleted_at is not None:
                raise ApiError(40403)
            if inst.status == "paused":
                return inst
            if inst.status != "running" or run is None:
                raise ApiError(40902, f"当前状态 {inst.status} 不能暂停")
            await self._set_status(instance_id, "pausing")
            self._unsubscribe(run)
            run.paused = True
            try:
                await asyncio.wait_for(run.strategy.on_pause(), timeout=10)
            except Exception as exc:  # noqa: BLE001
                self.log(instance_id, "warn", f"on_pause 异常：{exc}")
            inst = await self._set_status(instance_id, "paused")
            async with session_scope() as db:
                await write_audit(db, action="instance.pause", actor_id=actor.id if actor else None,
                                  actor_role=actor.role if actor else None, target_type="instance",
                                  target_id=instance_id, source="manual" if actor else "system")
            return inst

    async def resume(self, instance_id: int, *, actor: User | None) -> Instance:
        async with self.op_lock(instance_id):
            run = self.running.get(instance_id)
            async with session_scope() as db:
                inst = await db.get(Instance, instance_id)
            if inst is None or inst.deleted_at is not None:
                raise ApiError(40403)
            if inst.status == "running":
                return inst
            if inst.status != "paused" or run is None:
                raise ApiError(40902, f"当前状态 {inst.status} 不能恢复")
            await self._set_status(instance_id, "resuming")
            try:
                await asyncio.wait_for(run.strategy.on_resume(), timeout=10)
            except Exception as exc:  # noqa: BLE001
                self.log(instance_id, "warn", f"on_resume 异常：{exc}")
            run.paused = False
            self._subscribe(run)
            inst = await self._set_status(instance_id, "running", last_error=None)
            async with session_scope() as db:
                await write_audit(db, action="instance.resume", actor_id=actor.id if actor else None,
                                  actor_role=actor.role if actor else None, target_type="instance",
                                  target_id=instance_id, source="manual" if actor else "system")
            return inst

    # ---- 停止 ----
    async def stop(self, instance_id: int, *, actor: User | None, reason: str | None = None,
                   final_status: str = "stopped", force: bool = False) -> Instance:
        async with self.op_lock(instance_id):
            async with session_scope() as db:
                inst = await db.get(Instance, instance_id)
            if inst is None or inst.deleted_at is not None:
                raise ApiError(40403)
            if inst.status in {"stopped", "created"} and instance_id not in self.running:
                return inst
            if inst.status in TRANSITIONAL and not force and instance_id in self.running and inst.status != "stopping":
                raise ApiError(40902, "实例操作进行中")
            stop_action = inst.stop_action
            await self._set_status(instance_id, "stopping")
            run = self.running.get(instance_id)
            close_error = None
            if run is not None:
                self._unsubscribe(run)
                try:
                    await asyncio.wait_for(run.strategy.on_stop(), timeout=10)
                except Exception as exc:  # noqa: BLE001 - on_stop 异常不阻塞停止
                    self.log(instance_id, "warn", f"on_stop 异常：{exc}")
                if stop_action == "close":
                    try:
                        await asyncio.wait_for(self.close_all_positions(run.ctx, is_manual=False), timeout=STOP_TIMEOUT)
                    except Exception as exc:  # noqa: BLE001
                        close_error = f"停止时平仓失败：{exc}"
            await self._teardown(instance_id)
            status = "error" if close_error else final_status
            inst = await self._set_status(instance_id, status, enabled=False, last_stopped_at=datetime.utcnow(),
                                          last_error=close_error or (reason if final_status == "error" else inst.last_error))
            async with session_scope() as db:
                await write_audit(db, action="instance.stop", actor_id=actor.id if actor else None,
                                  actor_role=actor.role if actor else None, target_type="instance",
                                  target_id=instance_id, source="manual" if actor else "system",
                                  new_value={"stop_action": stop_action, "reason": reason, "force": force},
                                  result="failed" if close_error else "success", fail_reason=close_error)
            return inst

    async def _teardown(self, instance_id: int) -> None:
        run = self.running.pop(instance_id, None)
        if run is not None:
            self._unsubscribe(run)
            if run.book is not None:
                async with session_scope() as db:
                    await self._save_book(db, instance_id, run.book)
                    await db.commit()

    # ======================================================================
    # 订单中心：五段处理（《订单与持仓设计》第六节）
    # ======================================================================
    async def place_order(self, ctx, side: str, amount: float, price: float | None = None,
                          type_: str = "market", *, source: str = "strategy", is_guard: bool = False,
                          guard_type: str | None = None, stop_price: float | None = None,
                          reduce_only: bool | None = None, client_order_id: str | None = None,
                          actor_id: int | None = None, symbol: str | None = None) -> OrderView:
        instance_id = ctx.instance_id
        side = side.lower()
        symbol = _order_symbol(ctx, symbol)
        if side not in {"buy", "sell"}:
            raise ValueError("side 必须是 buy 或 sell")
        if type_ not in {"market", "limit", "stop_market", "stop_limit", "take_profit", "trailing_stop"}:
            raise ValueError(f"不支持的订单类型 {type_}")
        cid = client_order_id or f"bamy{instance_id}x{uuid.uuid4().hex[:20]}"
        async with session_scope() as db:
            # 幂等
            existing = (await db.execute(select(Order).where(Order.client_order_id == cid))).scalar_one_or_none()
            if existing is not None:
                return _view(existing)
            inst = await db.get(Instance, instance_id)
            order = Order(client_order_id=cid, instance_id=instance_id, owner_id=inst.owner_id,
                          account_id=inst.account_id, symbol=symbol, market_type=inst.market_type,
                          side=side, type=type_, amount=Decimal(str(amount)),
                          price=Decimal(str(price)) if price is not None else None,
                          stop_price=Decimal(str(stop_price)) if stop_price is not None else None,
                          status="pending", source=source, is_guard=is_guard, guard_type=guard_type)
            db.add(order)
            await db.commit()
            await db.refresh(order)
        await self.push("order.created", _event(order))
        if reduce_only is None:
            if inst.market_type == "futures":
                book = self._live_book(instance_id)
                reduce_only = bool(book and book.is_reducing(symbol, side))
            else:
                reduce_only = side == "sell"

        # ---- 阶段 1：前置校验 ----
        try:
            ref_price, amount, price = await self._precheck(instance_id, side, float(amount), price, type_, reduce_only,
                                                            allow_stopped=(guard_type == "force_close" and bool(reduce_only)),
                                                            symbol=symbol)
        except (RiskRejected, ApiError, ExchangeError, ValueError) as exc:
            code = getattr(exc, "code", 42201)
            return await self._reject(order.id, str(getattr(exc, "message", exc)), code=code,
                                      rule=getattr(exc, "rule", None))

        # ---- 阶段 2：插件拦截 ----
        async with session_scope() as db:
            inst = await db.get(Instance, instance_id)
            payload = {"instance": {"id": inst.id, "name": inst.name, "owner_id": inst.owner_id,
                                    "account_id": inst.account_id, "paper": inst.paper,
                                    "strategy_id": inst.strategy_id},
                       "order": {"client_order_id": cid, "source": source, "is_guard": is_guard},
                       "symbol": symbol, "side": side, "amount": amount, "price": price, "type": type_,
                       "market_type": inst.market_type, "reduce_only": bool(reduce_only), "ref_price": ref_price}
        res = await plugin_hub.intercept("order.before_submit", payload, owner_id=inst.owner_id,
                                         account_id=inst.account_id)
        if not res.allowed:
            return await self._reject(order.id, res.detail.get("reason") or res.message, code=res.code,
                                      intercepted_by=f"{res.detail['plugin_id']}#{res.detail['binding_id']}",
                                      intercepted_action="DENY", detail=res.detail)
        modified = [t for t in res.trace if t["conclusion"] == "MODIFY"]
        if modified:
            amount = float(res.payload.get("amount", amount))
            price = res.payload.get("price", price)
            type_ = res.payload.get("type", type_)
            async with session_scope() as db:
                o = await db.get(Order, order.id)
                o.amount = Decimal(str(amount))
                o.price = Decimal(str(price)) if price is not None else None
                o.type = type_
                o.intercepted_by = ",".join(f"{t['plugin_id']}#{t['binding_id']}" for t in modified)[:64]
                o.intercepted_action = "MODIFY"
                await db.commit()
            if amount <= 0:
                return await self._reject(order.id, "插件改写后数量不合法", code=42201)

        # ---- 阶段 3：发送 ----
        async with session_scope() as db:
            o = await db.get(Order, order.id)
            o.status = "submitting"
            o.raw_request = json.dumps({"symbol": o.symbol, "side": side, "amount": amount, "price": price,
                                        "type": type_, "stop_price": stop_price})
            await db.commit()
        result: ExchangeOrder | None = None
        error: ExchangeError | None = None
        run = self.running.get(instance_id)
        try:
            if inst.paper:
                book = run.book if run and run.book else await self._get_book(instance_id)
                if type_ == "market":
                    result = book.fill(cid, symbol, side, amount, ref_price, reduce_only=bool(reduce_only) and inst.market_type == "futures")
                else:
                    result = book.place_limit(cid, symbol, side, amount, float(price or ref_price),
                                              reduce_only=bool(reduce_only) and inst.market_type == "futures")
                async with session_scope() as db:
                    await self._save_book(db, instance_id, book)
                    await db.commit()
                if run is not None:
                    self._sync_extra_subs(run)
            else:
                result = await self._send_live(inst, side, amount, price, type_, cid, stop_price, symbol=symbol,
                                               reduce_only=bool(reduce_only))
        except ExchangeError as exc:
            error = exc

        # ---- 阶段 4：处理返回 ----
        if error is not None:
            if error.unknown:
                async with session_scope() as db:
                    o = await db.get(Order, order.id)
                    o.status = "unknown"
                    o.reason = str(error)[:2000]
                    await db.commit()
                    await db.refresh(o)
                await self.push("order.failed", _event(o) | {"unknown": True})
                return _view(o)
            if error.code == 40102 or "authentication" in str(error).lower():
                asyncio.create_task(self.account_invalid(inst.account_id, str(error)))
            return await self._reject(order.id, str(error), code=error.code)
        return await self._apply_result(order.id, result, submitted=True)

    async def _get_book(self, instance_id: int) -> PaperBook:
        async with session_scope() as db:
            inst = await db.get(Instance, instance_id)
            return await self._paper_book(db, inst)

    async def _precheck(self, instance_id: int, side: str, amount: float, price: float | None, type_: str,
                        reduce_only: bool, *, allow_stopped: bool = False,
                        symbol: str | None = None) -> tuple[float, float, float | None]:
        run = self.running.get(instance_id)
        async with session_scope() as db:
            inst = await db.get(Instance, instance_id)
            symbol = symbol or inst.symbol
            allowed = {"running", "stopping", "starting", "paused"} | ({"stopped", "error", "created"} if allow_stopped else set())
            if inst.status not in allowed:
                raise ApiError(40902, "实例未运行")
            if amount <= 0:
                raise ApiError(40003, "数量必须大于 0")
            if price is not None and float(price) <= 0:
                raise ApiError(40003, "价格必须大于 0")
            if type_ == "limit" and price is None:
                raise ApiError(40004, "限价单必须提供价格")
            if is_scan_symbol(symbol) or "/" not in symbol:
                raise ApiError(42231, "扫描实例下单必须指定具体交易对")
            if is_futures_symbol(symbol) != (inst.market_type == "futures"):
                raise ApiError(42231, "交易对与实例的市场类型不一致（合约交易对形如 BTC/USDT:USDT）")
            if not inst.paper:
                account = await db.get(Account, inst.account_id)
                if account is None or account.status != "online":
                    raise ApiError(40906, "账号不可用")
                if account.readonly:
                    raise ApiError(40304)
                if not live_trading_enabled():
                    raise ApiError(42201, "实盘交易未开启")
            if market.is_stale(symbol) and not reduce_only:
                # 行情延迟超阈值 → 停止开仓
                try:
                    await market.fetch(symbol)
                except Exception as exc:  # noqa: BLE001
                    raise ApiError(50203, "行情不可用，禁止开仓") from exc
            tick = await market.ticker(symbol)
            ref_price = float(price or tick["price"])
            if not inst.paper:
                client = await pool.get(await db.get(Account, inst.account_id))
                amount, price = await client.normalize(symbol, amount, price)
            await risk_engine.check_order(db, instance=inst, side=side, amount=amount, price=ref_price,
                                          reduce_only=reduce_only, symbol=symbol)
            # 余额
            if inst.paper:
                book = run.book if run and run.book else await self._paper_book(db, inst)
                code = book.precheck(symbol, side, amount, ref_price, reduce_only=reduce_only)
                if code:
                    raise ApiError(code)
            elif inst.market_type == "futures":
                client = await pool.get(await db.get(Account, inst.account_id))
                if not reduce_only:
                    free = {b.asset: b.free for b in await client.fetch_balance()}.get("USDT", 0)
                    lev = _leverage_of(inst)
                    if free < amount * ref_price / lev:
                        raise ApiError(42243, "合约账户可用保证金不足")
            else:
                client = await pool.get(await db.get(Account, inst.account_id))
                balances = {b.asset: b for b in await client.fetch_balance()}
                base, quote = symbol.split("/")
                if side == "buy" and (balances.get(quote).free if balances.get(quote) else 0) < amount * ref_price:
                    raise ApiError(42243)
                if side == "sell" and (balances.get(base).free if balances.get(base) else 0) < amount:
                    raise ApiError(42244)
        return ref_price, amount, price

    async def _send_live(self, inst: Instance, side: str, amount: float, price: float | None, type_: str,
                         cid: str, stop_price: float | None, *, symbol: str | None = None,
                         reduce_only: bool = False) -> ExchangeOrder:
        symbol = symbol or inst.symbol
        async with session_scope() as db:
            account = await db.get(Account, inst.account_id)
        client = await pool.get(account)
        extra = {}
        if inst.market_type == "futures":
            extra["reduce_only"] = reduce_only
            # 传入持仓模式供下单方法使用
            extra["position_mode"] = account.position_mode
        delays = [2, 5, 5]
        for attempt in range(len(delays) + 1):
            try:
                return await asyncio.wait_for(
                    client.create_order(symbol, side, amount, price, type_, cid, stop_price, **extra), timeout=10)
            except asyncio.TimeoutError as exc:
                raise ExchangeError("下单超时，结果未知", 50401, unknown=True) from exc
            except ExchangeError as exc:
                if exc.rejected or attempt >= len(delays):
                    raise
                # 5xx 与可重试网络错误：先用 client_order_id 查一次，防止重复下单
                try:
                    return await client.fetch_order_by_client_id(cid, symbol)
                except Exception:  # noqa: BLE001
                    pass
                if exc.unknown:
                    raise
                await asyncio.sleep(delays[attempt])
        raise ExchangeError("下单失败", 50201, unknown=True)

    async def _reject(self, order_id: int, reason: str, *, code: int | None = None, rule: str | None = None,
                      intercepted_by: str | None = None, intercepted_action: str | None = None,
                      detail: dict[str, Any] | None = None) -> OrderView:
        async with session_scope() as db:
            o = await db.get(Order, order_id)
            o.status = "rejected"
            o.reason = f"[{code}] {reason}" if code else reason
            if intercepted_by:
                o.intercepted_by = intercepted_by[:64]
                o.intercepted_action = intercepted_action
            await db.commit()
            await db.refresh(o)
            await write_audit(db, action="order.intercepted" if intercepted_by else "order.rejected",
                              actor_id=None, target_type="order", target_id=o.id,
                              new_value={"reason": reason, "code": code, "rule": rule, "detail": detail,
                                         "instance_id": o.instance_id},
                              result="failed", fail_reason=reason,
                              source="system" if intercepted_by else o.source if o.source in {"strategy", "manual", "guard", "system", "admin"} else "system")
        await self.push("order.rejected", _event(o) | {"code": code, "detail": detail})
        view = _view(o)
        view.reason = o.reason
        return view

    async def _apply_result(self, order_id: int, result: ExchangeOrder, *, submitted: bool = False) -> OrderView:
        """把交易所/撮合结果写回本地，发订单事件，回调策略 on_order。"""
        async with session_scope() as db:
            o = await db.get(Order, order_id)
            before = o.status
            if result.exchange_order_id:
                o.exchange_order_id = result.exchange_order_id
            if submitted and o.submitted_at is None:
                o.submitted_at = datetime.utcnow()
            o.filled_amount = Decimal(str(result.filled_amount or 0))
            if result.filled_price:
                o.filled_price = Decimal(str(result.filled_price))
            o.fee = Decimal(str(result.fee or 0))
            if result.fee_currency:
                o.fee_currency = result.fee_currency[:16]
            elif o.account_id is None and result.fee:
                o.fee_currency = "USDT"           # 模拟盘手续费一律按 USDT 扣
            o.status = result.status
            if result.status == "filled":
                o.filled_at = datetime.utcnow()
            if result.status == "canceled":
                o.canceled_at = datetime.utcnow()
            if result.raw is not None:
                o.raw_response = json.dumps(result.raw, default=str)[:20000]
            await db.commit()
            await db.refresh(o)
            if result.status == "filled":
                rules = (await db.execute(select(RiskRule).where(RiskRule.enabled.is_(True)))).scalars().all()
                inst = await db.get(Instance, o.instance_id)
                risk_engine.after_fill(inst, list(rules), o.symbol)
        if before != o.status or submitted:
            if submitted:
                await self.push("order.submitted", _event(o))
            if o.status == "filled":
                await self.push("order.filled", _event(o))
                await self.push("position.changed", {"instance_id": o.instance_id, "owner_id": o.owner_id,
                                                     "account_id": o.account_id, "symbol": o.symbol})
            elif o.status == "partial":
                await self.push("order.partial", _event(o))
            elif o.status == "rejected":
                await self.push("order.rejected", _event(o))
            await self._notify_strategy(o)
        return _view(o)

    async def _notify_strategy(self, o: Order) -> None:
        run = self.running.get(o.instance_id)
        if run is None:
            return
        try:
            await asyncio.wait_for(run.strategy.on_order(_view(o)), timeout=10)
        except Exception as exc:  # noqa: BLE001
            self.log(o.instance_id, "warn", f"on_order 异常：{exc}")

    async def _check_liquidation(self, run: Running, prices: dict[str, float]) -> None:
        """合约模拟盘：标记价到强平价 → 按强平价平仓，记一笔强平订单、发通知。"""
        if run.book is None or not run.book.positions:
            return
        hits = run.book.liquidations(prices)
        if not hits:
            return
        async with session_scope() as db:
            await self._save_book(db, run.instance_id, run.book)
            for h in hits:
                now_ = datetime.utcnow()
                db.add(Order(client_order_id=f"liq{run.instance_id}x{uuid.uuid4().hex[:20]}", instance_id=run.instance_id,
                             owner_id=run.owner_id, account_id=None, symbol=h["symbol"], market_type="futures",
                             side="sell" if h["side"] == "long" else "buy", position_side=h["side"], type="market",
                             amount=Decimal(str(h["amount"])), filled_amount=Decimal(str(h["amount"])),
                             filled_price=Decimal(str(h["price"])), status="filled", source="system", is_guard=True,
                             guard_type="liquidation", reason=f"强平：亏掉保证金 {h['loss']:.2f} USDT",
                             submitted_at=now_, filled_at=now_))
            await db.commit()
        for h in hits:
            msg = (f"强平：{h['symbol']} {'多' if h['side'] == 'long' else '空'}单 {h['amount']:g} 张在 {h['price']:g} 被强平，"
                   f"亏掉保证金 {h['loss']:.2f} USDT")
            self.log(run.instance_id, "error", msg, module="engine")
            await self.push("risk.breached", {"instance_id": run.instance_id, "owner_id": run.owner_id,
                                              "rule": "liquidation", "message": msg})
            await self.push("position.changed", {"instance_id": run.instance_id, "owner_id": run.owner_id,
                                                 "account_id": None, "symbol": h["symbol"]})
        self._sync_extra_subs(run)

    async def _match_paper(self, run: Running, last: float, symbol: str | None = None) -> None:
        if not run.book or not run.book.open_orders:
            return
        fills = run.book.match(symbol or run.symbol, last)
        if not fills:
            return
        async with session_scope() as db:
            await self._save_book(db, run.instance_id, run.book)
            await db.commit()
            ids = {cid: oid for cid, oid in (await db.execute(
                select(Order.client_order_id, Order.id).where(Order.client_order_id.in_([c for c, _ in fills])))).all()}
        for cid, result in fills:
            if cid in ids:
                await self._apply_result(ids[cid], result)

    # ---- 撤单 ----
    async def cancel_order(self, ctx, order_id: str | int, *, actor_id: int | None = None) -> OrderView:
        async with session_scope() as db:
            o = await self._find_order(db, ctx.instance_id, order_id)
            if o.status in {"canceled", "canceling"}:
                return _view(o)  # 状态幂等
            if o.status not in {"submitted", "partial"}:
                raise ApiError(42246, f"订单状态 {o.status} 不可撤销")
            prev = o.status
            o.status = "canceling"
            await db.commit()
            inst = await db.get(Instance, o.instance_id)
        try:
            if inst.paper:
                run = self.running.get(inst.id)
                book = run.book if run and run.book else await self._get_book(inst.id)
                book.cancel(o.client_order_id)
                result = ExchangeOrder(o.exchange_order_id, "canceled", float(o.filled_amount), _f(o.filled_price))
            else:
                async with session_scope() as db:
                    client = await pool.get(await db.get(Account, inst.account_id))
                result = await asyncio.wait_for(client.cancel_order(o.exchange_order_id, o.symbol), timeout=10)
        except asyncio.TimeoutError:
            async with session_scope() as db:
                row = await db.get(Order, o.id)
                row.status = "unknown"
                await db.commit()
            raise ApiError(50401, "撤单超时，订单进入对账")
        except Exception as exc:  # noqa: BLE001 - 失败恢复原状态
            async with session_scope() as db:
                row = await db.get(Order, o.id)
                row.status = prev
                row.reason = f"撤单失败：{exc}"[:2000]
                await db.commit()
            raise ApiError(50201, f"撤单失败：{exc}") from exc
        view = await self._apply_result(o.id, result)
        async with session_scope() as db:
            await write_audit(db, action="order.cancel", actor_id=actor_id, target_type="order", target_id=o.id,
                              source="manual" if actor_id else "strategy")
        return view

    async def cancel_all_orders(self, ctx, *, actor_id: int | None = None) -> dict[str, Any]:
        async with session_scope() as db:
            rows = (await db.execute(select(Order).where(Order.instance_id == ctx.instance_id,
                                                         Order.status.in_(("submitted", "partial"))))).scalars().all()
        ok, failed = [], []
        for o in rows:
            try:
                await self.cancel_order(ctx, o.id, actor_id=actor_id)
                ok.append(o.id)
            except ApiError as exc:
                failed.append({"order_id": o.id, "reason": exc.message})
        return {"canceled": ok, "failed": failed}

    async def _find_order(self, db, instance_id: int, order_id: str | int) -> Order:
        stmt = select(Order).where(Order.instance_id == instance_id)
        if isinstance(order_id, int) or str(order_id).isdigit():
            stmt = stmt.where(Order.id == int(order_id))
        else:
            stmt = stmt.where(Order.client_order_id == str(order_id))
        o = (await db.execute(stmt)).scalar_one_or_none()
        if o is None:
            raise ApiError(40405)
        return o

    # ---- 持仓与平仓（持仓定义权在策略，框架只拿清单） ----
    async def list_positions(self, ctx) -> list[dict[str, Any]]:
        run = self.running.get(ctx.instance_id)
        if run and run.book is not None and not _has_own(run.strategy, "list_positions"):
            return run.book.positions_list(await self._prices(run.book), "USDT")
        if run and _has_own(run.strategy, "list_positions"):
            return list(await run.strategy.list_positions())
        return await self.framework_positions(ctx.instance_id)

    async def framework_positions(self, instance_id: int) -> list[dict[str, Any]]:
        """框架视角的持仓：模拟盘读模拟账户，实盘按本实例已成交订单净额推算。"""
        async with session_scope() as db:
            inst = await db.get(Instance, instance_id)
            if inst.paper:
                book = await self._paper_book(db, inst)
                return book.positions_list(await self._prices(book), "USDT")
            rows = (await db.execute(select(Order).where(Order.instance_id == instance_id,
                                                         Order.status.in_(("filled", "partial"))))).scalars().all()
        # 按交易对分别推算净持仓（扫描实例一个实例会有多个币）；合约允许净空
        nets: dict[str, list[float]] = {}
        for o in rows:
            qty = float(o.filled_amount or 0)
            px = float(o.filled_price or o.price or 0)
            net, cost = nets.setdefault(o.symbol, [0.0, 0.0])
            signed = qty if o.side == "buy" else -qty
            if net == 0 or (net > 0) == (signed > 0):
                net, cost = net + signed, cost + abs(signed) * px
            else:
                closed = min(abs(signed), abs(net))
                cost -= cost / abs(net) * closed
                net += signed
                if abs(net) > 1e-12 and (net > 0) == (signed > 0):   # 反手：剩余部分按本次价格开新仓
                    cost = abs(net) * px
            nets[o.symbol] = [net, cost]
        out = []
        for sym, (net, cost) in nets.items():
            if abs(net) <= 1e-12 or (inst.market_type != "futures" and net < 0):
                continue
            qty = abs(net)
            avg = cost / qty if qty else 0
            try:
                mark = float((await market.ticker(sym))["price"])
            except Exception:  # noqa: BLE001
                mark = avg
            side = "long" if net > 0 else "short"
            pnl = (mark - avg) * qty * (1 if side == "long" else -1)
            out.append({"id": sym, "symbol": sym, "side": side, "amount": qty, "entry_price": avg,
                        "mark_price": mark, "unrealized_pnl": pnl,
                        "unrealized_pnl_pct": pnl / (avg * qty) * 100 if avg else 0.0, "label": sym})
        return out

    async def _prices(self, book: PaperBook | FuturesBook) -> dict[str, float]:
        """持仓估值价：键与 book.positions 一致（现货按币名，合约按交易对）。"""
        out = {}
        for key, sym in book.price_keys().items():
            tick = market.cached(sym)
            if tick is None:
                try:
                    tick = await market.ticker(sym)
                except Exception:  # noqa: BLE001
                    continue
            out[key] = float(tick["price"])
        return out

    async def _before_close(self, ctx, position_id: str, amount: float | None, is_manual: bool) -> float | None:
        async with session_scope() as db:
            inst = await db.get(Instance, ctx.instance_id)
        res = await plugin_hub.intercept("position.before_close",
                                         {"instance": {"id": inst.id, "name": inst.name, "owner_id": inst.owner_id,
                                                       "account_id": inst.account_id, "paper": inst.paper},
                                          "position_id": position_id,
                                          "symbol": position_id if "/" in str(position_id) else inst.symbol, "amount": amount,
                                          "is_manual": is_manual},
                                         owner_id=inst.owner_id, account_id=inst.account_id)
        if not res.allowed:
            async with session_scope() as db:
                await write_audit(db, action="position.close", target_type="instance", target_id=inst.id,
                                  result="failed", fail_reason=res.detail.get("reason") or res.message,
                                  new_value={"intercepted_by": res.detail, "position_id": position_id},
                                  source="system")
            raise ApiError(res.code or 42290, res.message, data=res.detail)
        return res.payload.get("amount", amount)

    async def close_position(self, ctx, position_id: str, amount: float | None = None, *, is_manual: bool = True,
                             actor_id: int | None = None) -> Any:
        amount = await self._before_close(ctx, position_id, amount, is_manual)
        run = self.running.get(ctx.instance_id)
        if is_manual and run and _has_own(run.strategy, "close_position"):
            return await run.strategy.close_position(position_id, amount)
        return await self._market_close(ctx, position_id, amount, source="manual" if is_manual else "strategy",
                                        actor_id=actor_id)

    async def close_positions(self, ctx, position_ids: list[str], *, is_manual: bool = True,
                              actor_id: int | None = None) -> list[dict[str, Any]]:
        run = self.running.get(ctx.instance_id)
        results = []
        if is_manual and run and _has_own(run.strategy, "close_positions"):
            for pid in position_ids:
                await self._before_close(ctx, pid, None, is_manual)
            return [{"result": await run.strategy.close_positions(position_ids)}]
        for pid in position_ids:
            try:
                results.append({"position_id": pid, "ok": True,
                                "result": _jsonable(await self.close_position(ctx, pid, None, is_manual=is_manual,
                                                                              actor_id=actor_id))})
            except ApiError as exc:
                results.append({"position_id": pid, "ok": False, "reason": exc.message, "code": exc.code})
        return results

    async def close_all_positions(self, ctx, *, is_manual: bool = True, actor_id: int | None = None) -> Any:
        run = self.running.get(ctx.instance_id)
        if is_manual and run and _has_own(run.strategy, "close_all_positions"):
            for pos in await self.list_positions(ctx):
                await self._before_close(ctx, str(pos.get("id")), pos.get("amount"), is_manual)
            return await run.strategy.close_all_positions()
        positions = await self.list_positions(ctx)
        return await self.close_positions(ctx, [str(p.get("id")) for p in positions], is_manual=is_manual,
                                          actor_id=actor_id)

    async def force_close(self, instance_id: int, *, actor: User, position_id: str | None = None) -> list[dict[str, Any]]:
        """框架兜底平仓：直接市价平掉该实例交易对的持仓，不依赖策略；给 position_id 时只平该仓。"""
        ctx = await self.ctx_for(instance_id)
        positions = await self.framework_positions(instance_id)
        if position_id is not None:
            positions = [p for p in positions if str(p["id"]) == str(position_id)]
            if not positions:
                raise ApiError(42245)
        results = []
        for pos in positions:
            amount = await self._before_close(ctx, str(pos["id"]), float(pos["amount"]), True)
            view = await self._market_close(ctx, str(pos["id"]), amount, source="manual", actor_id=actor.id,
                                            guard_type="force_close")
            results.append({"position_id": pos["id"], "order": _jsonable(view)})
        async with session_scope() as db:
            await write_audit(db, action="position.force_close", actor_id=actor.id, actor_role=actor.role,
                              target_type="instance", target_id=instance_id, new_value={"results": results},
                              source="manual")
        return results

    async def _market_close(self, ctx, position_id: str, amount: float | None, *, source: str,
                            actor_id: int | None, guard_type: str | None = None) -> OrderView:
        positions = {str(p["id"]): p for p in await self.framework_positions(ctx.instance_id)}
        pos = positions.get(str(position_id)) or (next(iter(positions.values())) if len(positions) == 1 else None)
        if pos is None:
            raise ApiError(42245)
        qty = float(amount or pos["amount"])
        if qty > float(pos["amount"]) + 1e-12:
            raise ApiError(42244)
        side = "sell" if pos.get("side", "long") == "long" else "buy"
        view = await self.place_order(ctx, side, qty, None, "market", source=source, reduce_only=True,
                                      guard_type=guard_type, actor_id=actor_id, symbol=pos.get("symbol"))
        async with session_scope() as db:
            await write_audit(db, action="position.close", actor_id=actor_id, target_type="instance",
                              target_id=ctx.instance_id, new_value={"position_id": position_id, "amount": qty,
                                                                    "order_id": view.id, "status": view.status},
                              source=source if source in {"manual", "strategy", "guard"} else "system")
        return view

    async def ctx_for(self, instance_id: int):
        run = self.running.get(instance_id)
        if run is not None:
            return run.ctx
        async with session_scope() as db:
            inst = await db.get(Instance, instance_id)
            ext = registry.get_strategy(inst.strategy_id)
            raw = json.loads(inst.params or "{}")
            return self._make_ctx(inst, decrypt_secrets(ext.params_schema, raw) if ext else raw)

    # ---- 账户与行情 API ----
    async def fetch_balance(self, ctx) -> dict[str, Any]:
        if ctx.paper:
            run = self.running.get(ctx.instance_id)
            book = run.book if run and run.book else await self._get_book(ctx.instance_id)
            return book.balance_view()
        async with session_scope() as db:
            client = await pool.get(await db.get(Account, ctx.account_id))
        return {b.asset: {"free": b.free, "used": b.used, "total": b.total} for b in await client.fetch_balance()}

    async def set_leverage(self, ctx, n: int, symbol: str | None = None) -> None:
        """合约杠杆：模拟盘改模拟账户（只影响之后新开的仓）；实盘调币安。上限受风控 max_leverage 约束。"""
        if ctx.market_type != "futures":
            raise ApiError(42224, "现货不支持设杠杆")
        n = int(n)
        cap = await risk_engine.max_leverage(getattr(ctx, "_owner_id", None))
        if not 1 <= n <= cap:
            raise ApiError(42247, f"杠杆必须在 1–{cap} 倍之间（风控上限）")
        async with session_scope() as db:
            inst = await db.get(Instance, ctx.instance_id)
            cfg = json.loads(inst.paper_config or "{}") if inst.paper else {}
            if inst.paper:
                cfg["leverage"] = n
                inst.paper_config = json.dumps(cfg)
                await db.commit()
                book = self._books.get(inst.id)
                if book is not None:
                    book.config.leverage = n
                return
            client = await pool.get(await db.get(Account, inst.account_id))
            sym = _order_symbol(ctx, symbol)
        await client.set_leverage(sym, n)

    async def set_margin_mode(self, ctx, mode: str, symbol: str | None = None) -> None:
        if ctx.market_type != "futures":
            raise ApiError(42224, "现货不支持保证金模式")
        if mode != "isolated":
            raise ApiError(42224, "目前只支持逐仓（isolated）")
        if ctx.paper:
            return   # 模拟合约账户固定逐仓
        async with session_scope() as db:
            inst = await db.get(Instance, ctx.instance_id)
            client = await pool.get(await db.get(Account, inst.account_id))
        await client.set_margin_mode(_order_symbol(ctx, symbol), mode)

    async def set_position_mode(self, ctx, mode: str) -> None:
        if ctx.market_type != "futures":
            raise ApiError(42224, "现货不支持持仓模式")
        if mode != "oneway":
            raise ApiError(42224, "目前只支持单向持仓（oneway）")

    def _live_book(self, instance_id: int) -> PaperBook | FuturesBook | None:
        run = self.running.get(instance_id)
        return run.book if run and run.book is not None else self._books.get(instance_id)

    async def all_tickers(self, market_type: str) -> dict[str, dict[str, Any]]:
        return await market.all_tickers(market_type)

    async def window_stats(self, symbols: list[str], window: str) -> dict[str, dict[str, float]]:
        return await market.window_stats(symbols, window)

    def get_ticker(self, symbol: str) -> dict[str, Any] | None:
        tick = market.cached(symbol)
        return {k: v for k, v in tick.items() if k != "received_at"} if tick else None

    async def get_klines(self, symbol: str, timeframe: str, limit: int) -> list[list[Any]]:
        return await market.klines(symbol, timeframe, limit)

    async def get_orderbook(self, symbol: str, depth: int) -> dict[str, Any]:
        return await market.orderbook(symbol, depth)

    # ---- 保护中心（GuardHub） ----
    async def place_protection(self, ctx, side: str, amount: float, stop_price: float) -> OrderView:
        if ctx.paper:
            # 模拟盘：本地跟踪，触发价穿越后按市价成交
            return await self.place_order(ctx, side, amount, stop_price, "limit", source="guard", is_guard=True,
                                          guard_type="stop_loss", stop_price=stop_price, reduce_only=True)
        view = await self.place_order(ctx, side, amount, stop_price, "stop_market", source="guard", is_guard=True,
                                      guard_type="stop_loss", stop_price=stop_price, reduce_only=True)
        if view.status not in {"rejected", "failed"}:
            await self.push("guard.placed", {"instance_id": ctx.instance_id, "owner_id": getattr(ctx, "_owner_id", None),
                                             "account_id": ctx.account_id, "order_id": view.id})
        return view

    async def cancel_protection(self, ctx, protection_id: str | int) -> Any:
        return await self.cancel_order(ctx, protection_id)

    async def list_protections(self, ctx) -> list[OrderView]:
        async with session_scope() as db:
            rows = (await db.execute(select(Order).where(Order.instance_id == ctx.instance_id, Order.is_guard.is_(True),
                                                         Order.status.in_(("submitted", "partial"))))).scalars().all()
        return [_view(o) for o in rows]

    async def check_guards(self) -> int:
        """保护缺失检测：本地有、交易所无 → 告警失去保护。"""
        missing = 0
        async with session_scope() as db:
            rows = (await db.execute(select(Order).where(Order.is_guard.is_(True), Order.account_id.is_not(None),
                                                         Order.status.in_(("submitted", "partial"))))).scalars().all()
        by_account: dict[int, list[Order]] = {}
        for o in rows:
            by_account.setdefault(o.account_id, []).append(o)
        for account_id, orders in by_account.items():
            try:
                async with session_scope() as db:
                    client = await pool.get(await db.get(Account, account_id))
                # 只查这些保护单所在的交易对，每次带 symbol（不带 symbol 时币安按 40 倍权重计）
                open_ids = {o.exchange_order_id for o in
                            await self._open_orders_by_symbol(client, {o.symbol for o in orders})}
            except Exception as exc:  # noqa: BLE001
                log.warning("保护检测失败 account=%s：%s", account_id, exc)
                continue
            for o in orders:
                if o.exchange_order_id not in open_ids:
                    missing += 1
                    await self.push("guard.missing", _event(o))
                    await self.reconcile_order(o.id)
        return missing

    # ======================================================================
    # 对账引擎（Reconciler）
    # ======================================================================
    async def reconcile_order(self, order_id: int) -> str:
        async with session_scope() as db:
            o = await db.get(Order, order_id)
            if o is None or o.account_id is None:
                return o.status if o else "missing"
            account = await db.get(Account, o.account_id)
            before = {"status": o.status, "filled_amount": str(o.filled_amount)}
        try:
            client = await pool.get(account)
            try:
                result = await client.fetch_order_by_client_id(o.client_order_id, o.symbol)
            except Exception as exc:  # noqa: BLE001
                if "not found" in str(exc).lower() or "does not exist" in str(exc).lower() or "-2013" in str(exc):
                    result = ExchangeOrder(None, "failed", 0.0, None, raw={"reconcile": "not_found"})
                else:
                    raise
        except Exception as exc:  # noqa: BLE001
            log.warning("订单对账失败 order=%s：%s", order_id, exc)
            return before["status"]
        view = await self._apply_result(order_id, result)
        if view.status != before["status"]:
            async with session_scope() as db:
                db.add(ReconcileDiff(instance_id=o.instance_id, account_id=o.account_id, diff_type="order",
                                     expected=json.dumps(before), actual=json.dumps({"status": view.status,
                                                                                    "filled_amount": view.filled_amount}),
                                     resolved=True, resolved_at=datetime.utcnow()))
                await db.commit()
            await self.push("reconcile.diff", {"order_id": order_id, "account_id": o.account_id,
                                               "owner_id": o.owner_id, "from": before["status"], "to": view.status})
        return view.status

    async def reconcile_unknown(self) -> int:
        async with session_scope() as db:
            ids = (await db.execute(select(Order.id).where(Order.status.in_(("unknown", "submitting")),
                                                           Order.account_id.is_not(None)))).scalars().all()
        for oid in ids:
            await self.reconcile_order(oid)
        return len(ids)

    async def reconcile_open(self) -> int:
        async with session_scope() as db:
            ids = (await db.execute(select(Order.id).where(Order.status.in_(("submitted", "partial")),
                                                           Order.account_id.is_not(None)))).scalars().all()
        for oid in ids:
            await self.reconcile_order(oid)
        return len(ids)

    async def _account_symbols(self, account_id: int) -> set[str]:
        """需要查挂单的交易对：只看本地还没完结的订单（对账只核对它们在交易所还在不在），没有就一次都不查。"""
        async with session_scope() as db:
            rows = (await db.execute(select(Order.symbol).where(
                Order.account_id == account_id, Order.status.in_(("submitted", "partial", "unknown")))
                .distinct())).scalars().all()
        return {s for s in rows if s and not is_scan_symbol(s)}

    async def _open_orders_by_symbol(self, client, symbols: set[str]) -> list[ExchangeOrder]:
        """逐个交易对查挂单（每次都带 symbol：现货权重 6、合约 1；不带 symbol 时现货 80、合约 40）。"""
        out: list[ExchangeOrder] = []
        for sym in sorted(symbols):
            out.extend(await client.fetch_open_orders(sym))
        return out

    async def reconcile_account(self, account_id: int, reason: str = "schedule") -> dict[str, Any]:
        """持仓/余额对账：以币安为准修正并留痕。"""
        async with session_scope() as db:
            account = await db.get(Account, account_id)
            if account is None or account.deleted_at is not None:
                return {"account_id": account_id, "skipped": True}
        diffs = 0
        symbols = await self._account_symbols(account_id)
        try:
            client = await pool.get(account)
            balances = {b.asset: b.total for b in await client.fetch_balance()}
            open_orders = {o.client_order_id: o for o in await self._open_orders_by_symbol(client, symbols)}
        except Exception as exc:  # noqa: BLE001
            if reason == "start":
                raise ApiError(40912, f"对账失败：{exc}") from exc
            log.warning("账号对账失败 account=%s：%s", account_id, exc)
            return {"account_id": account_id, "error": str(exc)}
        async with session_scope() as db:
            local_open = (await db.execute(select(Order).where(Order.account_id == account_id,
                                                               Order.status.in_(("submitted", "partial", "unknown"))))).scalars().all()
        for o in local_open:
            if o.client_order_id not in open_orders:
                await self.reconcile_order(o.id)
                diffs += 1
        # 本地推算持仓 vs 交易所余额
        async with session_scope() as db:
            insts = (await db.execute(select(Instance).where(Instance.account_id == account_id,
                                                             Instance.deleted_at.is_(None)))).scalars().all()
        expected: dict[str, float] = {}
        for inst in insts:
            if inst.market_type == "futures":
                continue   # 合约持仓不在现货余额里，按仓位接口对账（见 BinanceFuturesAdapter.fetch_positions）
            for pos in await self.framework_positions(inst.id):
                base = pos["symbol"].split("/")[0]
                expected[base] = expected.get(base, 0.0) + float(pos["amount"])
        async with session_scope() as db:
            for base, qty in expected.items():
                actual = float(balances.get(base, 0.0))
                if actual + 1e-8 < qty:
                    diffs += 1
                    db.add(ReconcileDiff(account_id=account_id, diff_type="position",
                                         expected=json.dumps({"asset": base, "amount": qty}),
                                         actual=json.dumps({"asset": base, "amount": actual})))
            await db.commit()
        if diffs:
            await self.push("reconcile.diff", {"account_id": account_id, "owner_id": account.owner_id, "count": diffs})
        await self.push("reconcile.completed", {"account_id": account_id, "owner_id": account.owner_id,
                                                "diffs": diffs, "reason": reason})
        return {"account_id": account_id, "diffs": diffs}

    async def reconcile_all(self) -> dict[str, Any]:
        unknown = await self.reconcile_unknown()
        async with session_scope() as db:
            ids = (await db.execute(select(Account.id).where(Account.deleted_at.is_(None),
                                                             Account.status == "online"))).scalars().all()
        results = [await self.reconcile_account(aid) for aid in ids]
        return {"unknown_orders": unknown, "accounts": results}

    # ======================================================================
    # 账号事件
    # ======================================================================
    async def account_invalid(self, account_id: int | None, reason: str) -> None:
        if account_id is None:
            return
        async with session_scope() as db:
            account = await db.get(Account, account_id)
            if account is None or account.status == "invalid":
                return
            account.status = "invalid"
            await db.commit()
            await write_audit(db, action="account.invalid", target_type="account", target_id=account_id,
                              new_value={"reason": reason}, source="system")
            inst_ids = (await db.execute(select(Instance.id).where(Instance.account_id == account_id,
                                                                   Instance.deleted_at.is_(None)))).scalars().all()
        await pool.drop(account_id)
        for iid in inst_ids:
            if iid in self.running:
                await self.auto_stop(iid, f"账号失效：{reason}")
        await self.push("account.invalid", {"account_id": account_id, "owner_id": account.owner_id, "reason": reason})
        await self.push("system.alert", {"level": "error", "title": "账号失效", "account_id": account_id,
                                         "owner_id": account.owner_id, "message": reason})

    async def account_abnormal(self, account_id: int, reason: str) -> None:
        async with session_scope() as db:
            inst_ids = (await db.execute(select(Instance.id).where(Instance.account_id == account_id,
                                                                   Instance.deleted_at.is_(None)))).scalars().all()
        for iid in inst_ids:
            await self.auto_pause(iid, f"账号异常：{reason}")

    # ======================================================================
    # 快照与回撤
    # ======================================================================
    async def snapshot(self) -> int:
        n = 0
        for iid, run in list(self.running.items()):
            try:
                n += await self._snapshot_one(run)
            except Exception:  # noqa: BLE001
                log.exception("快照失败 instance=%s", iid)
        return n

    async def _snapshot_one(self, run: Running) -> int:
        positions = await self.list_positions(run.ctx)
        unreal = sum(float(p.get("unrealized_pnl") or 0) for p in positions)
        value = sum(float(p.get("amount") or 0) * float(p.get("mark_price") or 0) for p in positions)
        async with session_scope() as db:
            db.add(PositionSnapshot(instance_id=run.instance_id, owner_id=run.owner_id,
                                    positions=json.dumps(positions, default=str),
                                    total_value=Decimal(str(value)), unrealized_pnl=Decimal(str(unreal))))
            if run.book is not None:
                prices = await self._prices(run.book)
                equity = run.book.equity(prices)
                acct = (await db.execute(select(PaperAccount).where(PaperAccount.instance_id == run.instance_id))).scalar_one()
                peak = max(Decimal(str(acct.peak_equity or 0)), equity)
                acct.peak_equity = peak
                drawdown = (peak - equity) / peak if peak > 0 else Decimal(0)
                db.add(PaperSnapshot(instance_id=run.instance_id, owner_id=run.owner_id, equity=equity,
                                     balance=run.book.balance, positions=run.book.dump_positions(),
                                     realized_pnl=run.book.realized_pnl, unrealized_pnl=run.book.unrealized(prices),
                                     peak_equity=peak, drawdown=drawdown.quantize(Decimal("0.000001"))))
                await self._save_book(db, run.instance_id, run.book)
                day = datetime.utcnow().strftime("%Y-%m-%d")
                if run.day_key != day:
                    run.day_key, run.day_start_equity = day, float(equity)
                inst = await db.get(Instance, run.instance_id)
                breach = await risk_engine.check_drawdown(db, inst, float(equity), float(peak), run.day_start_equity)
            else:
                # 实盘：按本实例成交推算权益（口径见 services/live_equity.py），没设本金就只记盈亏不做回撤风控
                from bamy.services.live_equity import live_pnl

                inst = await db.get(Instance, run.instance_id)
                lp = await live_pnl(db, inst)
                breach = None
                if lp["equity"] is not None:
                    equity = Decimal(str(lp["equity"]))
                    prev = (await db.execute(select(PaperSnapshot.peak_equity).where(
                        PaperSnapshot.instance_id == run.instance_id).order_by(PaperSnapshot.id.desc()).limit(1))).scalar()
                    peak = max(Decimal(str(prev or 0)), equity, Decimal(str(lp["initial"])))
                    drawdown = (peak - equity) / peak if peak > 0 else Decimal(0)
                    db.add(PaperSnapshot(instance_id=run.instance_id, owner_id=run.owner_id, equity=equity,
                                         balance=Decimal(str(lp["initial"] + lp["realized"])),
                                         positions=json.dumps(positions, default=str),
                                         realized_pnl=Decimal(str(lp["realized"])),
                                         unrealized_pnl=Decimal(str(lp["unrealized"])),
                                         peak_equity=peak, drawdown=drawdown.quantize(Decimal("0.000001"))))
                    day = datetime.utcnow().strftime("%Y-%m-%d")
                    if run.day_key != day:
                        run.day_key, run.day_start_equity = day, float(equity)
                    breach = await risk_engine.check_drawdown(db, inst, float(equity), float(peak), run.day_start_equity)
            await db.commit()
        if breach is not None:
            await self.push("risk.breached", {"instance_id": run.instance_id, "owner_id": run.owner_id,
                                              "rule": breach.rule, "message": str(breach)})
            await self.auto_stop(run.instance_id, str(breach))
        return 1

    # ======================================================================
    # 崩溃恢复：先对账再恢复（《实例模型设计》第十二节）
    # ======================================================================
    async def recover(self) -> dict[str, Any]:
        async with session_scope() as db:
            rows = (await db.execute(select(Instance).where(
                Instance.enabled.is_(True), Instance.deleted_at.is_(None),
                Instance.status.in_(("running", "starting", "pausing", "resuming", "stopping", "paused"))))).scalars().all()
            targets = [(i.id, i.status, i.owner_id) for i in rows]
            for inst in rows:
                inst.status = "stopped"
            await db.commit()
        if not system_config.get("instance.auto_recover", True) or not settings.auto_recover_on_boot:
            async with session_scope() as db:
                for iid, _, _ in targets:
                    inst = await db.get(Instance, iid)
                    inst.enabled = False
                await db.commit()
            return {"recovered": [], "failed": [], "skipped": [t[0] for t in targets]}
        sem = asyncio.Semaphore(int(settings.engine.get("recover_concurrency", settings.recover_concurrency)))
        recovered, failed = [], []

        async def one(iid: int, prev: str, owner_id: int) -> None:
            async with sem:
                async with session_scope() as db:
                    owner = await db.get(User, owner_id)
                if owner is None or owner.status != "active":
                    await self._set_status(iid, "stopped", enabled=False, last_error="用户已禁用，不自动恢复")
                    failed.append({"instance_id": iid, "reason": "用户已禁用"})
                    return
                try:
                    await asyncio.wait_for(self.start(iid, actor=None, recovering=True),
                                           timeout=int(settings.engine.get("recover_timeout", settings.recover_timeout)))
                    if prev == "paused":
                        await self.pause(iid, actor=None)
                    recovered.append(iid)
                except Exception as exc:  # noqa: BLE001
                    reason = getattr(exc, "message", str(exc))
                    failed.append({"instance_id": iid, "reason": reason})
                    async with session_scope() as db:
                        inst = await db.get(Instance, iid)
                        if inst.status != "error":
                            inst.status = "error"
                            inst.last_error = f"崩溃恢复失败：{reason}"
                            await db.commit()

        await asyncio.gather(*(one(*t) for t in targets))
        async with session_scope() as db:
            await write_audit(db, action="instance.start", target_type="system", source="system",
                              new_value={"crash_recovery": True, "recovered": recovered, "failed": failed})
        return {"recovered": recovered, "failed": failed, "skipped": []}

    async def shutdown(self) -> None:
        """优雅停机：保存状态但不改 enabled，下次启动可恢复。"""
        for iid, run in list(self.running.items()):
            self._unsubscribe(run)
            if run.book is not None:
                async with session_scope() as db:
                    await self._save_book(db, iid, run.book)
                    await db.commit()
        self.running.clear()
        await self.flush_logs()
        await market.close()
        await pool.close_all()

    def stats(self) -> dict[str, Any]:
        return {"running_instances": len(self.running), "subscriptions": market.subscriptions(),
                "live_bindings": len(plugin_hub.live_ids()), "pool": pool.stats(),
                "uptime_seconds": int(time.time() - self.started_at), "ready": self.ready}


# ---------------------------------------------------------------------------
def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _has_own(obj: Any, method: str) -> bool:
    from bamy.extensions.base import BaseStrategy

    return getattr(type(obj), method, None) is not getattr(BaseStrategy, method, None)


def _view(o: Order) -> OrderView:
    return OrderView(id=o.id, client_order_id=o.client_order_id, exchange_order_id=o.exchange_order_id,
                     symbol=o.symbol, side=o.side, type=o.type, amount=float(o.amount),
                     price=_f(o.price), filled_amount=float(o.filled_amount or 0), filled_price=_f(o.filled_price),
                     status=o.status, reason=o.reason, source=o.source, is_guard=bool(o.is_guard))


def _event(o: Order) -> dict[str, Any]:
    return {"order_id": o.id, "client_order_id": o.client_order_id, "instance_id": o.instance_id,
            "owner_id": o.owner_id, "account_id": o.account_id, "symbol": o.symbol, "side": o.side,
            "type": o.type, "amount": float(o.amount), "price": _f(o.price),
            "filled_amount": float(o.filled_amount or 0), "filled_price": _f(o.filled_price),
            "status": o.status, "source": o.source, "is_guard": bool(o.is_guard), "reason": o.reason}


def _jsonable(v: Any) -> Any:
    if isinstance(v, OrderView):
        return v.__dict__
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return v


# 需要进通知中心的事件：(级别, 标题)。高频事件（tick、order.created/submitted）不进
NOTIFY_EVENTS: dict[str, tuple[str, str]] = {
    "instance.error": ("error", "实例出错"),
    "instance.auto_stopped": ("error", "实例已自动停止"),
    "order.failed": ("warn", "下单失败"),
    "order.rejected": ("warn", "订单被拒"),
    "order.filled": ("info", "订单成交"),
    "guard.missing": ("error", "保护单缺失"),
    "guard.triggered": ("warn", "保护单触发"),
    "risk.breached": ("warn", "触发风控"),
    "risk.circuit_break": ("error", "已熔断"),
    "risk.circuit_recover": ("info", "熔断已解除"),
    "account.invalid": ("error", "账号失效"),
    "account.recovered": ("info", "账号已恢复"),
    "reconcile.diff": ("warn", "对账发现差异"),
    "system.alert": ("error", "系统告警"),
}

engine = Engine()


def _order_symbol(ctx, symbol: str | None) -> str:
    """下单交易对：策略显式传入优先（扫描实例必须传），否则用实例绑定的交易对。"""
    sym = (symbol or ctx.symbol or "").strip()
    if getattr(ctx, "market_type", "spot") == "futures" and sym and ":" not in sym and sym.endswith("/USDT"):
        sym = f"{sym}:USDT"   # 合约实例里写 BTC/USDT 也认，自动转成 BTC/USDT:USDT
    return sym


def _leverage_of(inst: Instance) -> int:
    try:
        return max(1, int(json.loads(inst.paper_config or "{}").get("leverage") or 1))
    except (TypeError, ValueError):
        return 1
