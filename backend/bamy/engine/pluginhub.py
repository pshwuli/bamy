"""插件中心：绑定解析（account > user > global，按插件去重）、拦截链执行、订阅分发、健康度。

插件挂在检查点与事件总线上，不挂在实例上；插件拿不到任何下单接口（《插件规范》）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from bamy.core.crypto import decrypt
from bamy.db.models import Account, PluginBinding, PluginHealth
from bamy.db.session import engine as db_engine, session_scope
from bamy.extensions.base import ALLOW, StateStore, _Decision
from bamy.extensions.registry import registry
from bamy.services import system_config

log = logging.getLogger("bamy.pluginhub")

SCOPE_RANK = {"account": 3, "user": 2, "global": 1}
PAYLOAD_EDITABLE = {
    "instance.before_start": set(),
    "order.before_submit": {"amount", "price", "type"},
    "position.before_close": {"amount"},
    "params.before_save": {"params"},
}


@dataclass
class InterceptResult:
    allowed: bool
    payload: dict[str, Any]
    code: int | None = None           # 42290/42291/42292
    message: str | None = None
    detail: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LiveBinding:
    binding: PluginBinding
    obj: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_tick: dict[str, float] = field(default_factory=dict)
    tasks: list[asyncio.Task] = field(default_factory=list)   # 插件声明的后台服务


class PluginHub:
    def __init__(self) -> None:
        self._live: dict[int, LiveBinding] = {}
        self._account_owner: dict[int, int] = {}

    # ---------- 生命周期 ----------
    async def load_bindings(self) -> None:
        async with session_scope() as db:
            rows = (await db.execute(select(PluginBinding).where(PluginBinding.deleted_at.is_(None)))).scalars().all()
            accounts = (await db.execute(select(Account.id, Account.owner_id))).all()
        self._account_owner = {a: o for a, o in accounts}
        for row in rows:
            await self.activate(row)

    def remember_account(self, account_id: int, owner_id: int) -> None:
        self._account_owner[account_id] = owner_id

    async def activate(self, binding: PluginBinding) -> None:
        """绑定生效：实例化插件对象并调用 on_bind()。"""
        await self.deactivate(binding.id, call_unbind=False)
        if not binding.enabled or binding.status != "enabled" or binding.deleted_at is not None:
            return
        ext = registry.get_plugin(binding.plugin_id)
        if ext is None or not ext.loaded or ext.cls is None:
            return
        obj = ext.cls()
        obj.ctx = self._make_ctx(binding, ext)
        live = LiveBinding(binding=binding, obj=obj)
        self._live[binding.id] = live
        await self._call_lifecycle(live, "on_bind")
        self._start_services(live)

    async def deactivate(self, binding_id: int, call_unbind: bool = True) -> None:
        live = self._live.pop(binding_id, None)
        if live is not None:
            await self._stop_services(live)
            if call_unbind:
                await self._call_lifecycle(live, "on_unbind")

    def _start_services(self, live: LiveBinding) -> None:
        ext = registry.get_plugin(live.binding.plugin_id)
        for svc in (ext.services if ext else []):
            handler = getattr(live.obj, str(svc["handler"]))
            live.tasks.append(asyncio.create_task(self._supervise(live, handler),
                                                  name=f"plugin-{live.binding.id}-{svc['id']}"))

    async def _stop_services(self, live: LiveBinding) -> None:
        tasks, live.tasks = live.tasks, []
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        """关机：取消全部绑定的后台服务。"""
        for live in list(self._live.values()):
            await self._stop_services(live)

    async def _supervise(self, live: LiveBinding, handler) -> None:
        """后台服务崩溃后退避重启；正常返回表示服务主动结束（如凭据没填），不再重启。"""
        delay = 5.0
        while True:
            started = time.monotonic()
            try:
                await handler()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("插件 %s#%s 后台服务异常：%s", live.binding.plugin_id, live.binding.id, type(exc).__name__)
            if time.monotonic() - started > 60:
                delay = 5.0
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300.0)

    async def settings_changed(self, binding: PluginBinding) -> None:
        live = self._live.get(binding.id)
        if live is None:
            await self.activate(binding)
            return
        ext = registry.get_plugin(binding.plugin_id)
        await self._stop_services(live)
        live.binding = binding
        live.obj.ctx = self._make_ctx(binding, ext)
        await self._call_lifecycle(live, "on_settings")
        self._start_services(live)

    async def reload_all(self) -> None:
        for bid in list(self._live):
            await self.deactivate(bid, call_unbind=False)
        await self.load_bindings()

    async def _call_lifecycle(self, live: LiveBinding, name: str) -> None:
        try:
            await asyncio.wait_for(getattr(live.obj, name)(), timeout=5)
        except Exception as exc:  # noqa: BLE001 - 生命周期异常不阻断绑定
            log.warning("插件 %s#%s %s 异常：%s", live.binding.plugin_id, live.binding.id, name, exc)
            await self._record(live.binding, error=f"{name}: {exc}", cost_ms=0, kind="event")

    def _make_ctx(self, binding: PluginBinding, ext) -> Any:
        from bamy.engine.runtime import engine
        from bamy.plugin import PluginContext, RestrictedHttp, RestrictedWs

        settings = _decode_settings(binding.settings, ext.settings_schema if ext else [])
        net = ext.network if ext else None
        http = RestrictedHttp(net.get("hosts") or [], int(net.get("timeout_ms", 5000))) if net else None
        ws = RestrictedWs(net.get("hosts") or [], int(net.get("timeout_ms", 5000))) if net else None

        async def load(key: str) -> str | None:
            from bamy.db.models import PluginState

            async with session_scope() as db:
                row = (await db.execute(select(PluginState).where(
                    PluginState.binding_id == binding.id, PluginState.key == key))).scalar_one_or_none()
                return row.value if row else None

        async def save(key: str, value: str) -> None:
            await engine.plugin_state_set(binding.id, key, value)

        async def delete(key: str) -> None:
            await engine.plugin_state_delete(binding.id, key)

        return PluginContext(
            plugin_id=binding.plugin_id, binding_id=binding.id, scope=binding.scope,
            scope_id=binding.scope_id, settings=settings, data_scope=ext.data_scope if ext else "own",
            state=StateStore(load, save, delete), http=http, ws=ws,
            log_fn=lambda level, msg: engine.log(None, level, msg, module="plugin"),
            notify_fn=lambda title, body, level, _pid=binding.plugin_id, _bid=binding.id: engine.notify(
                title, body, level, plugin_id=_pid, binding_id=_bid),
            overview_fn=lambda owner_id, _b=binding: self._overview(_b, owner_id),
        )

    async def _overview(self, binding: PluginBinding, owner_id: int | None) -> dict[str, Any]:
        """插件只读概览：按绑定作用域裁剪（user 只看自己，account 只看该账号，global 看传入的 owner_id）。"""
        from datetime import datetime as _dt

        from sqlalchemy import func

        from bamy.db.models import Instance, Order, PaperAccount
        from bamy.engine.runtime import engine

        async with session_scope() as db:
            stmt = select(Instance).where(Instance.deleted_at.is_(None))
            if binding.scope == "user":
                stmt = stmt.where(Instance.owner_id == binding.scope_id)
            elif binding.scope == "account":
                stmt = stmt.where(Instance.account_id == binding.scope_id)
            elif owner_id is not None:
                stmt = stmt.where(Instance.owner_id == owner_id)
            else:
                return {"instances": []}
            rows = (await db.execute(stmt.order_by(Instance.id))).scalars().all()
            day = _dt.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            out = []
            for i in rows:
                equity = initial = None
                if i.paper:
                    acct = (await db.execute(select(PaperAccount).where(PaperAccount.instance_id == i.id))).scalar_one_or_none()
                    if acct is not None:
                        initial = float(acct.initial_balance)
                        try:
                            book = await engine._get_book(i.id)
                            equity = float(book.equity(await engine._prices(book)))
                        except Exception:  # noqa: BLE001
                            equity = float(acct.balance)
                        peak = float(acct.peak_equity or initial)
                    else:
                        peak = None
                else:
                    peak = None
                fills = await db.scalar(select(func.count(Order.id)).where(
                    Order.instance_id == i.id, Order.status == "filled", Order.created_at >= day)) or 0
                out.append({"id": i.id, "name": i.name, "owner_id": i.owner_id, "status": i.status,
                            "strategy_id": i.strategy_id, "symbol": i.symbol, "market_type": i.market_type,
                            "paper": bool(i.paper), "equity": equity, "initial": initial, "peak": peak,
                            "fills_today": int(fills)})
        return {"instances": out}

    # ---------- 生效链 ----------
    def effective(self, *, owner_id: int | None, account_id: int | None) -> list[tuple[LiveBinding, str]]:
        """按 account > user > global 收集，按插件去重，按 priority 排序。

        模拟盘实例 account_id 为空，账号级绑定不生效。
        """
        best: dict[str, tuple[LiveBinding, str]] = {}
        for live in self._live.values():
            b = live.binding
            if b.scope == "account":
                if account_id is None or b.scope_id != account_id:
                    continue
            elif b.scope == "user":
                if owner_id is None or b.scope_id != owner_id:
                    continue
            elif b.scope != "global":
                continue
            current = best.get(b.plugin_id)
            if current is None or SCOPE_RANK[b.scope] > SCOPE_RANK[current[1]]:
                best[b.plugin_id] = (live, b.scope)
        return sorted(best.values(), key=lambda x: (x[0].binding.priority, x[0].binding.id))

    # ---------- 拦截链 ----------
    async def intercept(self, checkpoint: str, payload: dict[str, Any], *, owner_id: int | None,
                        account_id: int | None) -> InterceptResult:
        chain = []
        for live, scope in self.effective(owner_id=owner_id, account_id=account_id):
            ext = registry.get_plugin(live.binding.plugin_id)
            if ext is None or not ext.loaded:
                continue
            for hook in ext.checkpoint_hooks():
                if hook["checkpoint"] == checkpoint:
                    chain.append((live, scope, ext, hook))
        result = InterceptResult(allowed=True, payload=dict(payload))
        if not chain:
            return result
        editable = PAYLOAD_EDITABLE.get(checkpoint, set())
        default_timeout = int(system_config.get("plugin.intercept_timeout_ms", 200))
        for live, scope, ext, hook in chain:
            b = live.binding
            timeout_ms = min(int(hook.get("timeout_ms", default_timeout)), 2000)
            fail_mode = hook.get("fail_mode", "open")
            handler = getattr(live.obj, hook["handler"])
            started = time.perf_counter()
            locate = {"plugin_id": b.plugin_id, "plugin_name": ext.meta.get("name"), "binding_id": b.id,
                      "effective_scope": scope, "checkpoint": checkpoint}
            try:
                async with live.lock:
                    decision = await asyncio.wait_for(handler(_redact(dict(result.payload))),
                                                      timeout=timeout_ms / 1000)
                cost = int((time.perf_counter() - started) * 1000)
            except asyncio.TimeoutError:
                cost = int((time.perf_counter() - started) * 1000)
                await self._record(b, error="拦截超时", cost_ms=cost, timeout=True)
                result.trace.append(locate | {"conclusion": "TIMEOUT", "cost_ms": cost, "fail_mode": fail_mode})
                if fail_mode == "closed":
                    return _deny(result, 42291, "插件拦截超时", locate)
                continue
            except Exception as exc:  # noqa: BLE001 - 插件异常按 fail_mode 处理
                cost = int((time.perf_counter() - started) * 1000)
                await self._record(b, error=f"拦截异常：{exc}", cost_ms=cost)
                result.trace.append(locate | {"conclusion": "ERROR", "cost_ms": cost, "fail_mode": fail_mode,
                                              "error": str(exc)})
                if fail_mode == "closed":
                    return _deny(result, 42292, "插件拦截出错", locate | {"error": str(exc)})
                continue

            if decision == ALLOW:
                await self._record(b, cost_ms=cost)
                result.trace.append(locate | {"conclusion": "ALLOW", "cost_ms": cost})
                continue
            if isinstance(decision, _Decision) and decision.kind == "DENY":
                await self._record(b, cost_ms=cost)
                result.trace.append(locate | {"conclusion": "DENY", "cost_ms": cost, "reason": decision.reason})
                return _deny(result, 42290, "被插件拒绝", locate | {"reason": decision.reason or ""})
            if isinstance(decision, _Decision) and decision.kind == "MODIFY" and isinstance(decision.payload, dict):
                await self._record(b, cost_ms=cost)
                changed = {k: v for k, v in decision.payload.items() if k in editable}
                if checkpoint == "params.before_save" and "params" in changed and not isinstance(changed["params"], dict):
                    changed.pop("params")
                result.payload.update(changed)
                result.trace.append(locate | {"conclusion": "MODIFY", "cost_ms": cost, "changed": list(changed)})
                continue
            # 返回非法结论按 PLUGIN_ERROR 处理
            await self._record(b, error=f"非法结论：{decision!r}", cost_ms=cost)
            result.trace.append(locate | {"conclusion": "ERROR", "cost_ms": cost, "fail_mode": fail_mode})
            if fail_mode == "closed":
                return _deny(result, 42292, "插件拦截出错", locate | {"error": "非法结论"})
        return result

    # ---------- 订阅分发 ----------
    async def dispatch(self, event: str, body: dict[str, Any]) -> None:
        data = body.get("data") or {}
        owner_id = data.get("owner_id")
        account_id = data.get("account_id")
        if account_id is not None and owner_id is None:
            owner_id = self._account_owner.get(account_id)
        for live in list(self._live.values()):
            b = live.binding
            ext = registry.get_plugin(b.plugin_id)
            if ext is None or not ext.loaded:
                continue
            hooks = [h for h in ext.subscription_hooks() if h["event"] == event]
            if not hooks or not self._visible(b, ext.data_scope, owner_id, account_id):
                continue
            for hook in hooks:
                throttle = float(hook.get("throttle") or 0)
                if throttle:
                    key = f"{event}:{data.get('symbol', '')}"
                    last = live.last_tick.get(key, 0)
                    if time.time() - last < throttle:
                        continue
                    live.last_tick[key] = time.time()
                asyncio.create_task(self._run_sub(live, hook["handler"], _redact(body)))

    def _visible(self, b: PluginBinding, data_scope: str, owner_id: int | None, account_id: int | None) -> bool:
        """数据裁剪（《插件规范》8.2）。"""
        if data_scope == "all":
            return True
        if b.scope == "account":
            return account_id is not None and account_id == b.scope_id
        if b.scope == "user":
            return owner_id is not None and owner_id == b.scope_id
        return owner_id is None  # 全局 own 只收系统事件

    async def _run_sub(self, live: LiveBinding, handler_name: str, body: dict[str, Any]) -> None:
        started = time.perf_counter()
        try:
            async with live.lock:
                await asyncio.wait_for(getattr(live.obj, handler_name)(body), timeout=10)
            await self._record(live.binding, cost_ms=int((time.perf_counter() - started) * 1000), kind="event")
        except Exception as exc:  # noqa: BLE001 - 订阅型异常只记日志与计数
            log.warning("插件订阅处理失败 %s#%s：%s", live.binding.plugin_id, live.binding.id, exc)
            await self._record(live.binding, error=str(exc) or type(exc).__name__,
                               cost_ms=int((time.perf_counter() - started) * 1000), kind="event",
                               timeout=isinstance(exc, asyncio.TimeoutError))

    # ---------- 健康度 ----------
    async def _record(self, b: PluginBinding, *, error: str | None = None, cost_ms: int = 0,
                      kind: str = "checkpoint", timeout: bool = False) -> None:
        threshold = int(system_config.get("plugin.auto_disable_threshold", 10))
        auto_disabled = False
        async with session_scope() as db:
            dialect = db_engine.dialect.name
            insert_fn = sqlite_insert if dialect == "sqlite" else postgresql_insert if dialect == "postgresql" else None
            if insert_fn is None:
                raise RuntimeError(f"不支持插件健康统计的数据库方言：{dialect}")
            for binding_id in (b.id, None):
                now = datetime.utcnow()
                is_error = int(bool(error))
                is_event = int(kind == "event")
                is_checkpoint = int(kind != "event")
                values = {
                    "plugin_id": b.plugin_id,
                    "binding_id": binding_id,
                    "invoke_count": 1,
                    "error_count": is_error,
                    "consecutive_errors": is_error,
                    "event_calls": is_event,
                    "checkpoint_calls": is_checkpoint,
                    "timeout_count": int(timeout),
                    "avg_cost_ms": cost_ms if is_checkpoint else None,
                    "last_error": error[:2000] if error else None,
                    "last_error_at": now if error else None,
                    "updated_at": now,
                }
                insert = insert_fn(PluginHealth).values(**values)
                if binding_id is not None:
                    insert = insert.on_conflict_do_update(
                        index_elements=[PluginHealth.plugin_id, PluginHealth.binding_id],
                        index_where=PluginHealth.binding_id.is_not(None),
                        set_={
                            "invoke_count": PluginHealth.invoke_count + 1,
                            "error_count": PluginHealth.error_count + is_error,
                            "consecutive_errors": PluginHealth.consecutive_errors + 1 if is_error else 0,
                            "event_calls": PluginHealth.event_calls + is_event,
                            "checkpoint_calls": PluginHealth.checkpoint_calls + is_checkpoint,
                            "avg_cost_ms": case(
                                (is_checkpoint == 1,
                                 (PluginHealth.avg_cost_ms * PluginHealth.checkpoint_calls + cost_ms) /
                                 (PluginHealth.checkpoint_calls + 1)),
                                else_=PluginHealth.avg_cost_ms,
                            ),
                            "timeout_count": PluginHealth.timeout_count + int(timeout),
                            "last_error": values["last_error"] if is_error else PluginHealth.last_error,
                            "last_error_at": values["last_error_at"] if is_error else PluginHealth.last_error_at,
                            "updated_at": now,
                        },
                    )
                else:
                    insert = insert.on_conflict_do_update(
                        index_elements=[PluginHealth.plugin_id],
                        index_where=PluginHealth.binding_id.is_(None),
                        set_={
                            "invoke_count": PluginHealth.invoke_count + 1,
                            "error_count": PluginHealth.error_count + is_error,
                            "consecutive_errors": PluginHealth.consecutive_errors + 1 if is_error else 0,
                            "event_calls": PluginHealth.event_calls + is_event,
                            "checkpoint_calls": PluginHealth.checkpoint_calls + is_checkpoint,
                            "avg_cost_ms": case(
                                (is_checkpoint == 1,
                                 (PluginHealth.avg_cost_ms * PluginHealth.checkpoint_calls + cost_ms) /
                                 (PluginHealth.checkpoint_calls + 1)),
                                else_=PluginHealth.avg_cost_ms,
                            ),
                            "timeout_count": PluginHealth.timeout_count + int(timeout),
                            "last_error": values["last_error"] if is_error else PluginHealth.last_error,
                            "last_error_at": values["last_error_at"] if is_error else PluginHealth.last_error_at,
                            "updated_at": now,
                        },
                    )
                await db.execute(insert)
                if binding_id is not None and is_error:
                    consecutive = await db.scalar(select(PluginHealth.consecutive_errors).where(
                        PluginHealth.plugin_id == b.plugin_id, PluginHealth.binding_id == binding_id))
                    if consecutive is not None and consecutive >= threshold:
                        auto_disabled = True
            if auto_disabled:
                binding = await db.get(PluginBinding, b.id)
                if binding is not None and binding.status == "enabled":
                    binding.status = "disabled"
                    binding.last_error = f"连续异常 {threshold} 次，已自动停用"
                    from bamy.services.audit import write_audit

                    await write_audit(db, action="binding.auto_disable", target_type="plugin_binding",
                                      target_id=b.id, new_value={"status": "disabled"}, source="system",
                                      commit=False)
                else:
                    auto_disabled = False
            await db.commit()
        if auto_disabled:
            await self.deactivate(b.id, call_unbind=False)
            from bamy.engine.runtime import engine

            # 经 engine.push：同时进事件总线、WebSocket（推给绑定所属用户）和通知中心
            await engine.push("system.alert", {"level": "error", "title": "插件绑定已自动停用",
                                               "reason": f"插件 {b.plugin_id} 绑定 #{b.id} 连续异常 {threshold} 次，已自动停用，需管理员恢复",
                                               "plugin_id": b.plugin_id, "binding_id": b.id,
                                               "owner_id": b.owner_id if b.scope != "global" else None})

    def live_ids(self) -> list[int]:
        return list(self._live)

    async def run_action(self, binding_id: int, action_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """执行插件声明的手动动作（如扫码绑定）。

        同一绑定的动作串行执行；返回值必须是 dict。动作失败不计入健康度（是用户手动触发，不是运行期故障）。
        """
        live = self._live.get(binding_id)
        if live is None:
            raise LookupError("绑定未生效，请先启用")
        ext = registry.get_plugin(live.binding.plugin_id)
        action = next((a for a in (ext.actions if ext else []) if a.get("id") == action_id), None)
        if action is None:
            raise LookupError(f"插件未声明动作 {action_id}")
        handler = getattr(live.obj, str(action["handler"]))
        async with live.lock:
            result = await asyncio.wait_for(handler(payload), timeout=30)
        if not isinstance(result, dict):
            raise TypeError("动作必须返回 dict")
        return result


def _deny(result: InterceptResult, code: int, message: str, detail: dict[str, Any]) -> InterceptResult:
    result.allowed = False
    result.code = code
    result.message = message
    result.detail = detail
    return result


SENSITIVE = {"api_key", "api_secret", "api_key_enc", "api_secret_enc", "password", "password_hash",
             "two_factor_secret", "recovery_codes"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if k in SENSITIVE else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _decode_settings(raw: str | None, schema: list[dict[str, Any]]) -> dict[str, Any]:
    if not raw:
        return {}
    data = json.loads(raw)
    secret_keys = {p["key"] for p in schema if p.get("type") == "secret" or p.get("secret")}
    for key in secret_keys:
        if isinstance(data.get(key), str):
            try:
                data[key] = decrypt(data[key])
            except Exception:  # noqa: BLE001
                pass
    return data


plugin_hub = PluginHub()
