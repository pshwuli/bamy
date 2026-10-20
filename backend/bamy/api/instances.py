"""实例、持仓与平仓、订单接口（《接口契约》第七至九节，22.1）。"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, delete, func, or_, select, update

from bamy.api.deps import Ctx, Page, current, iso, num, own_account, own_instance, paging
from bamy.api.serializers import instance_out, order_out
from bamy.core.config import settings
from bamy.core.errors import ApiError
from bamy.core.response import csv_safe, ok, paginated
from bamy.db.models import (
    Account, Instance, InstanceState, Order, PaperAccount, PaperSnapshot, PositionSnapshot,
    ReconcileDiff, ShareToken, SystemLog,
)
from bamy.engine.market import market, scan_symbol
from bamy.engine.pluginhub import plugin_hub
from bamy.engine.risk import risk_engine
from bamy.engine.runtime import _jsonable, engine
from bamy.extensions.registry import registry
from bamy.extensions.schema import (SchemaError, encrypt_secrets, mask_secrets, merge_secrets, secret_keys,
                                    validate)
from bamy.services import system_config
from bamy.services.live_equity import live_pnl
from bamy.services.audit import audit_dict, int_filter, query_audit, system_log_dict, write_audit

router = APIRouter()

SYSTEM_MAX_INSTANCES = 1000
PER_ACCOUNT_MAX = 10
EXPORT_MAX = 100_000


# ====================================================================== 公共
def _validate_params(ext, params: dict[str, Any], stored: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回明文参数（仅在内存中使用）；secret 字段回传脱敏值时沿用已存的密文。"""
    try:
        return validate(ext.params_schema, merge_secrets(ext.params_schema, params or {}, stored))
    except SchemaError as exc:
        raise ApiError(exc.code, exc.message, errors=[{"field": exc.field, "reason": exc.message}]) from exc


def _store(ext, params: dict[str, Any]) -> str:
    """落库前加密 secret 字段。"""
    return json.dumps(encrypt_secrets(ext.params_schema if ext else [], params), ensure_ascii=False)


def _masked(ext, params: dict[str, Any] | None) -> dict[str, Any]:
    return mask_secrets(ext.params_schema if ext else [], params)


async def _params_checkpoint(inst_view: dict[str, Any], params: dict[str, Any], owner_id: int,
                             account_id: int | None, ext=None) -> dict[str, Any]:
    schema = ext.params_schema if ext else []
    res = await plugin_hub.intercept("params.before_save", {"instance": inst_view, "params": mask_secrets(schema, params)},
                                     owner_id=owner_id, account_id=account_id)
    if not res.allowed:
        raise ApiError(res.code or 42290, res.message, data=res.detail)
    out = dict(res.payload.get("params", params))
    for k in secret_keys(schema):  # 插件看不到也改不了 secret
        if k in params:
            out[k] = params[k]
    return out


async def _equity(inst: Instance) -> float | None:
    if inst.paper:
        snap = (await _latest_snapshot(inst.id))
        if snap is not None:
            return float(snap.equity)
        return float(inst.paper_balance) if inst.paper_balance is not None else None
    return None


async def _latest_snapshot(instance_id: int):
    from bamy.db.session import session_scope

    async with session_scope() as db:
        return (await db.execute(select(PaperSnapshot).where(PaperSnapshot.instance_id == instance_id)
                                 .order_by(PaperSnapshot.snapshot_at.desc()).limit(1))).scalar_one_or_none()


async def _out(ctx: Ctx, rows: list[Instance]) -> list[dict[str, Any]]:
    acc_ids = {i.account_id for i in rows if i.account_id}
    names = dict((await ctx.db.execute(select(Account.id, Account.name).where(Account.id.in_(acc_ids)))).all()) if acc_ids else {}
    out = []
    for i in rows:
        ext = registry.get_strategy(i.strategy_id)
        equity = None
        initial = None
        pnl = None
        if not i.paper:
            lp = await live_pnl(ctx.db, i)
            equity, initial, pnl = lp["equity"], lp["initial"], lp["pnl"]
        if i.paper:
            acct = (await ctx.db.execute(select(PaperAccount).where(PaperAccount.instance_id == i.id))).scalar_one_or_none()
            run = engine.running.get(i.id)
            if run and run.book is not None:
                equity = float(run.book.equity(await engine._prices(run.book)))
            elif acct is not None:
                snap = (await ctx.db.execute(select(PaperSnapshot).where(PaperSnapshot.instance_id == i.id)
                                             .order_by(PaperSnapshot.snapshot_at.desc()).limit(1))).scalar_one_or_none()
                equity = float(snap.equity) if snap else float(acct.balance)
            initial = float(acct.initial_balance) if acct else num(i.paper_balance)
        running_secs = int((datetime.utcnow() - i.last_started_at).total_seconds()) if i.status in {"running", "paused"} and i.last_started_at else None
        out.append(instance_out(i, strategy_name=ext.meta.get("name") if ext else None,
                                account_name=names.get(i.account_id), equity=equity,
                                extra={"initial_equity": initial, "pnl": pnl if pnl is not None else (
                                           equity - initial if equity is not None and initial else None),
                                       "return_pct": round((equity - initial) / initial * 100, 4) if equity is not None and initial else None,
                                       "running_seconds": running_secs,
                                       "capabilities": ext.capabilities if ext else {}}))
    return out


# ====================================================================== 7.1–7.4
@router.get("/api/instances")
async def list_instances(ctx: Ctx = Depends(current), pg: Page = Depends(paging), status: str | None = None,
                         paper: bool | None = None, strategy_id: str | None = None, account_id: int | None = None,
                         keyword: str | None = None, include_deleted: bool = False):
    stmt = select(Instance).where(Instance.owner_id == ctx.user.id)
    if not include_deleted:
        stmt = stmt.where(Instance.deleted_at.is_(None))
    for col, val in ((Instance.status, status), (Instance.paper, paper), (Instance.strategy_id, strategy_id),
                     (Instance.account_id, account_id)):
        if val is not None:
            stmt = stmt.where(col == val)
    if keyword:
        stmt = stmt.where(Instance.name.contains(keyword))
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(Instance.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    return paginated(await _out(ctx, list(rows)), total, pg.page, pg.page_size)


@router.get("/api/instances/{instance_id}")
async def get_instance(instance_id: int, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id, admin_ok=ctx.is_admin)
    return ok((await _out(ctx, [inst]))[0])


# 模拟盘初始资金上限：Numeric(20, 8) 能存 10^12 量级；这里限 1 亿 USDT，足够覆盖大资金模拟
PAPER_BALANCE_MAX = 100_000_000
PAPER_BALANCE_DEFAULT = 10_000     # 模拟盘不填初始资金时的默认值（USDT）


class PaperConfigIn(BaseModel):
    """模拟盘撮合参数。负数会让手续费变成补贴、滑点变成白赚，必须拒绝。"""
    model_config = {"extra": "forbid"}
    fee_rate: float | None = Field(None, ge=0, le=0.01)
    slippage: float | None = Field(None, ge=0, le=0.05)
    limit_mode: str | None = Field(None, pattern="^(book|immediate)$")
    quote_asset: str | None = Field(None, pattern="^USDT$")
    leverage: int | None = Field(None, ge=1, le=20)     # 仅 U 本位合约
    live_capital: float | None = Field(None, gt=0, le=PAPER_BALANCE_MAX)   # 仅实盘：本实例本金（算收益率、回撤用）


def _normalize_symbol(raw: str, market_type: str, *, scan: bool) -> str:
    """交易对规范化：扫描类策略固定 ALL/USDT(:USDT)；合约补 :USDT 后缀；统一大写。"""
    if scan:
        return scan_symbol(market_type)
    sym = (raw or "").strip().upper()
    if not sym or "/" not in sym:
        raise ApiError(40003, "请填写交易对，例如 BTC/USDT")
    if sym.startswith("ALL/"):
        raise ApiError(40003, "只有全市场扫描类策略才能扫描全部交易对")
    if market_type == "futures" and ":" not in sym:
        sym = f"{sym}:USDT"
    if market_type == "spot" and ":" in sym:
        raise ApiError(40003, "现货交易对不能带 :USDT 后缀")
    return sym


def _clean_name(v: str) -> str:
    v = (v or "").strip()
    if not v:
        raise ValueError("名称不能为空或只有空格")
    return v


class InstanceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    strategy_id: str
    symbol: str = ""                                    # 扫描类策略可不填，自动设为 ALL/USDT
    market_type: str = Field("spot", pattern="^(spot|futures)$")
    account_id: int | None = Field(None, ge=1)
    paper: bool = False
    paper_balance: float | None = Field(None, gt=0, le=PAPER_BALANCE_MAX)
    paper_config: PaperConfigIn | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    stop_action: str = "keep"

    _n = field_validator("name")(classmethod(lambda cls, v: _clean_name(v)))


@router.post("/api/instances")
async def create_instance(body: InstanceIn, ctx: Ctx = Depends(current)):
    limit = int(system_config.get("instance.max_per_user", 50))
    count = await ctx.db.scalar(select(func.count(Instance.id)).where(Instance.owner_id == ctx.user.id,
                                                                      Instance.deleted_at.is_(None))) or 0
    if count >= limit:
        raise ApiError(40907, f"实例数已达上限 {limit}")
    if (await ctx.db.scalar(select(func.count(Instance.id)).where(Instance.deleted_at.is_(None))) or 0) >= SYSTEM_MAX_INSTANCES:
        raise ApiError(40907, "系统实例总数已达上限")
    if await ctx.db.scalar(select(Instance.id).where(Instance.owner_id == ctx.user.id, Instance.name == body.name,
                                                      Instance.deleted_at.is_(None))):
        raise ApiError(40901, "实例名已存在")
    if body.stop_action not in {"keep", "close"}:
        raise ApiError(40003, "stop_action 必须是 keep 或 close")
    ext = registry.get_strategy(body.strategy_id)
    if ext is None or not ext.loaded:
        raise ApiError(40402)
    runtime = ext.meta.get("runtime") or {}
    if body.market_type not in (runtime.get("market_type") or ["spot"]):
        raise ApiError(42224, "该策略不支持" + ("U 本位合约" if body.market_type == "futures" else "现货"))
    symbol = _normalize_symbol(body.symbol, body.market_type, scan=bool(runtime.get("scan")))
    if runtime.get("symbols") and symbol.split(":")[0] not in runtime["symbols"] and symbol not in runtime["symbols"]:
        raise ApiError(42231, "策略不支持该交易对")
    if not await market.is_valid_symbol(symbol):
        raise ApiError(42231, f"交易对 {symbol} 不存在或已下架")
    if body.paper_config and body.paper_config.leverage and body.market_type != "futures":
        raise ApiError(40003, "现货不能设杠杆")
    if body.paper_config and body.paper_config.live_capital and body.paper:
        raise ApiError(40003, "模拟盘的本金就是初始资金，不用另设 live_capital")
    if body.market_type == "futures":
        cap = await risk_engine.max_leverage(ctx.user.id)
        lev = (body.paper_config.leverage if body.paper_config else None) or 1
        if lev > cap:
            raise ApiError(42247, f"杠杆 {lev} 倍超过风控上限 {cap} 倍")
    account = None
    if body.paper:
        if body.paper_balance is None:
            body.paper_balance = PAPER_BALANCE_DEFAULT          # 不填就用默认值，不再强制手填
        if body.paper_balance <= 0:
            raise ApiError(40003, "模拟盘初始资金必须大于 0")
        account_id = None
    else:
        if not settings.enable_live_trading:
            raise ApiError(42232, "服务器没有开启实盘交易（ENABLE_LIVE_TRADING=false），现在只能建模拟盘")
        if body.account_id is None:
            raise ApiError(40004, "实盘实例必须选择账号")
        account = await own_account(ctx.db, ctx.user, body.account_id)
        if account.status != "online":
            raise ApiError(40906, f"账号状态为 {account.status}")
        if account.readonly:
            raise ApiError(40304, "只读账号不能创建实盘实例")
        if account.market_type != body.market_type:
            raise ApiError(42224, "账号类型与实例市场类型不一致（现货账号不能跑合约）")
        per_acc = await ctx.db.scalar(select(func.count(Instance.id)).where(Instance.account_id == account.id,
                                                                            Instance.deleted_at.is_(None))) or 0
        if per_acc >= PER_ACCOUNT_MAX:
            raise ApiError(40907, f"每个账号最多绑定 {PER_ACCOUNT_MAX} 个实例")
        account_id = account.id
    params = _validate_params(ext, body.params)
    view = {"id": None, "name": body.name, "symbol": symbol, "paper": body.paper, "owner_id": ctx.user.id,
            "account_id": account_id, "strategy_id": ext.id}
    params = await _params_checkpoint(view, params, ctx.user.id, account_id, ext)
    params = _validate_params(ext, params)
    inst = Instance(owner_id=ctx.user.id, name=body.name, account_id=account_id, strategy_id=ext.id,
                    strategy_version=ext.version, symbol=symbol, market_type=body.market_type, paper=body.paper,
                    paper_balance=Decimal(str(body.paper_balance)) if body.paper else None,
                    paper_config=json.dumps(body.paper_config.model_dump(exclude_none=True)) if body.paper_config else None,
                    params=_store(ext, params), status="created", stop_action=body.stop_action)
    ctx.db.add(inst)
    await ctx.db.commit()
    await ctx.db.refresh(inst)
    await write_audit(ctx.db, action="instance.create", target_type="instance", target_id=inst.id,
                      new_value={"name": inst.name, "strategy_id": inst.strategy_id, "paper": inst.paper,
                                 "symbol": inst.symbol, "params": _masked(ext, params)}, **ctx.audit_kw())
    return ok((await _out(ctx, [inst]))[0], status_code=201)


class InstancePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    params: dict[str, Any] | None = None
    stop_action: str | None = None
    paper_config: PaperConfigIn | None = None

    _n = field_validator("name")(classmethod(lambda cls, v: _clean_name(v) if v is not None else v))


@router.patch("/api/instances/{instance_id}")
async def update_instance(instance_id: int, body: InstancePatch, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id)
    cur_ext = registry.get_strategy(inst.strategy_id)
    old = {"name": inst.name, "params": _masked(cur_ext, json.loads(inst.params)), "stop_action": inst.stop_action}
    if body.name and body.name != inst.name:
        if await ctx.db.scalar(select(Instance.id).where(Instance.owner_id == ctx.user.id, Instance.name == body.name,
                                                          Instance.deleted_at.is_(None))):
            raise ApiError(40901, "实例名已存在")
        inst.name = body.name
    if body.stop_action is not None:
        if body.stop_action not in {"keep", "close"}:
            raise ApiError(40003, "stop_action 必须是 keep 或 close")
        inst.stop_action = body.stop_action
    new_cfg = body.paper_config.model_dump(exclude_none=True) if body.paper_config is not None else {}
    only_capital = bool(new_cfg) and set(new_cfg) == {"live_capital"}
    if body.params is not None or (new_cfg and not only_capital):
        if inst.status not in {"created", "stopped", "error"}:
            raise ApiError(40903, "运行中的实例不能改参数，请先停止")
    if new_cfg.get("live_capital") is not None and inst.paper:
        raise ApiError(40003, "模拟盘的本金就是初始资金，不用另设")
    if new_cfg and not inst.paper:
        # 实盘只允许改本金（只影响收益率/回撤的计算口径，不影响下单，运行中也能改）
        extra = set(new_cfg) - {"live_capital", "leverage"}
        if extra:
            raise ApiError(40003, f"实盘实例不能设置 {', '.join(sorted(extra))}")
        if new_cfg.get("leverage") and inst.market_type != "futures":
            raise ApiError(40003, "现货不能设杠杆")
        inst.paper_config = json.dumps(json.loads(inst.paper_config or "{}") | new_cfg)
    if new_cfg and inst.paper:
        if new_cfg.get("leverage"):
            if inst.market_type != "futures":
                raise ApiError(40003, "现货不能设杠杆")
            cap = await risk_engine.max_leverage(inst.owner_id)
            if new_cfg["leverage"] > cap:
                raise ApiError(42247, f"杠杆 {new_cfg['leverage']} 倍超过风控上限 {cap} 倍")
        inst.paper_config = json.dumps(json.loads(inst.paper_config or "{}") | new_cfg)
        engine._books.pop(inst.id, None)   # 停止状态下改配置：下次加载按新配置
    if body.params is not None:
        ext = registry.get_strategy(inst.strategy_id)
        if ext is None or not ext.loaded:
            raise ApiError(40402)
        stored = json.loads(inst.params or "{}")
        params = _validate_params(ext, body.params, stored)
        view = {"id": inst.id, "name": inst.name, "symbol": inst.symbol, "paper": inst.paper,
                "owner_id": inst.owner_id, "account_id": inst.account_id, "strategy_id": inst.strategy_id}
        params = _validate_params(ext, await _params_checkpoint(view, params, inst.owner_id, inst.account_id, ext))
        inst.params = _store(ext, params)
    await ctx.db.commit()
    await write_audit(ctx.db, action="instance.update", target_type="instance", target_id=inst.id, old_value=old,
                      new_value={**body.model_dump(exclude_none=True),
                                 **({"params": _masked(cur_ext, body.params)} if body.params is not None else {})},
                      **ctx.audit_kw())
    return ok((await _out(ctx, [inst]))[0])


# ====================================================================== 7.5–7.9 生命周期
@router.post("/api/instances/batch/{action}")
async def batch(action: str, request: Request, ctx: Ctx = Depends(current)):
    """批量启动/停止：按队列串行执行（《实例模型设计》15.3）。"""
    if action not in {"start", "stop"}:
        raise ApiError(40003)
    ids = (await request.json()).get("instance_ids") or []
    results = []
    for iid in ids[:100]:
        try:
            await own_instance(ctx.db, ctx.user, int(iid))
            inst = await (engine.start(int(iid), actor=ctx.user) if action == "start" else engine.stop(int(iid), actor=ctx.user))
            results.append({"instance_id": iid, "ok": True, "status": inst.status})
        except ApiError as exc:
            results.append({"instance_id": iid, "ok": False, "code": exc.code, "reason": exc.message})
    return ok({"results": results})


@router.post("/api/instances/{instance_id}/start")
async def start_instance(instance_id: int, ctx: Ctx = Depends(current)):
    await own_instance(ctx.db, ctx.user, instance_id)
    inst = await engine.start(instance_id, actor=ctx.user)
    return ok({"instance_id": instance_id, "status": inst.status})


@router.post("/api/instances/{instance_id}/stop")
async def stop_instance(instance_id: int, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id)
    if inst.status not in {"running", "paused", "error", "stopped", "created", "stopping"}:
        raise ApiError(40902, "实例操作进行中")
    inst = await engine.stop(instance_id, actor=ctx.user)
    return ok({"instance_id": instance_id, "status": inst.status, "last_error": inst.last_error})


@router.post("/api/instances/{instance_id}/pause")
async def pause_instance(instance_id: int, ctx: Ctx = Depends(current)):
    await own_instance(ctx.db, ctx.user, instance_id)
    ext_inst = await ctx.db.get(Instance, instance_id)
    ext = registry.get_strategy(ext_inst.strategy_id)
    if ext and ext.capabilities.get("pause") is False:
        raise ApiError(42221, "策略未声明暂停能力")
    inst = await engine.pause(instance_id, actor=ctx.user)
    return ok({"instance_id": instance_id, "status": inst.status})


@router.post("/api/instances/{instance_id}/resume")
async def resume_instance(instance_id: int, ctx: Ctx = Depends(current)):
    await own_instance(ctx.db, ctx.user, instance_id)
    inst = await engine.resume(instance_id, actor=ctx.user)
    return ok({"instance_id": instance_id, "status": inst.status})


@router.get("/api/instances/{instance_id}/status")
async def instance_status(instance_id: int, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id, admin_ok=ctx.is_admin)
    run = engine.running.get(instance_id)
    positions, equity, balance = [], None, None
    try:
        positions = await engine.list_positions(await engine.ctx_for(instance_id))
    except Exception:  # noqa: BLE001
        positions = []
    live = None
    if inst.paper:
        book = run.book if run and run.book else await engine._get_book(instance_id)
        equity = float(book.equity(await engine._prices(book)))
        balance = float(book.balance)
    else:
        live = await live_pnl(ctx.db, inst)
        equity = live["equity"]
        balance = live["initial"] + live["realized"] if live["initial"] else None
    tick = market.cached(inst.symbol)
    strategy_status = None
    raw = await engine.state_get(inst.id, "ui.status")
    if raw:
        try:
            strategy_status = json.loads(raw)
        except ValueError:
            strategy_status = {"summary": raw}
    await engine.flush_logs()
    last_log = (await ctx.db.execute(select(SystemLog).where(SystemLog.instance_id == inst.id)
                                     .order_by(SystemLog.id.desc()).limit(1))).scalar_one_or_none()
    return ok({"instance_id": inst.id, "status": inst.status, "strategy_status": strategy_status,
               "last_log": {"at": iso(last_log.timestamp), "message": last_log.message} if last_log else None,
               "running_since": iso(inst.last_started_at) if inst.status in {"running", "paused"} else None,
               "equity": equity, "balance": balance, "positions": positions,
               "last_tick_at": datetime.utcfromtimestamp(tick["received_at"]).isoformat() + "Z" if tick else None,
               "consecutive_errors": inst.consecutive_errors, "last_error": inst.last_error, "live": live,
               "in_memory": run is not None})


@router.get("/api/instances/{instance_id}/orders")
async def instance_orders(instance_id: int, ctx: Ctx = Depends(current), pg: Page = Depends(paging),
                          status: str | None = None, side: str | None = None, start_time: str | None = None,
                          end_time: str | None = None):
    await own_instance(ctx.db, ctx.user, instance_id, admin_ok=ctx.is_admin)
    return await _orders(ctx, pg, {"instance_id": instance_id, "status": status, "side": side,
                                   "start_time": start_time, "end_time": end_time}, owner_filter=False)


@router.get("/api/instances/{instance_id}/positions")
async def instance_positions(instance_id: int, ctx: Ctx = Depends(current)):
    await own_instance(ctx.db, ctx.user, instance_id, admin_ok=ctx.is_admin)
    return ok({"positions": _jsonable(await engine.list_positions(await engine.ctx_for(instance_id)))})


@router.get("/api/instances/{instance_id}/logs")
async def instance_logs(instance_id: int, ctx: Ctx = Depends(current), pg: Page = Depends(paging),
                        level: str | None = None, start_time: str | None = None, end_time: str | None = None):
    await own_instance(ctx.db, ctx.user, instance_id, admin_ok=ctx.is_admin)
    await engine.flush_logs()
    stmt = select(SystemLog).where(SystemLog.instance_id == instance_id)
    if level:
        stmt = stmt.where(SystemLog.level == level)
    if start_time:
        stmt = stmt.where(SystemLog.timestamp >= _dt(start_time))
    if end_time:
        stmt = stmt.where(SystemLog.timestamp <= _dt(end_time))
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(SystemLog.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    return paginated([system_log_dict(r) for r in rows], total, pg.page, pg.page_size)


@router.get("/api/instances/{instance_id}/audit-logs")
async def instance_audit(instance_id: int, ctx: Ctx = Depends(current), pg: Page = Depends(paging)):
    await own_instance(ctx.db, ctx.user, instance_id, admin_ok=ctx.is_admin)
    rows, total = await query_audit(ctx.db, {"target_type": "instance", "target_id": instance_id}, pg.page, pg.page_size)
    return paginated([audit_dict(r) for r in rows], total, pg.page, pg.page_size)


# ====================================================================== 7.14–7.16
class ChangeStrategyIn(BaseModel):
    strategy_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    close_positions: bool = False


@router.post("/api/instances/{instance_id}/change-strategy")
async def change_strategy(instance_id: int, body: ChangeStrategyIn, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id)
    if inst.status not in {"stopped", "error", "created"}:
        raise ApiError(40903)
    ext = registry.get_strategy(body.strategy_id)
    if ext is None or not ext.loaded:
        raise ApiError(40402)
    runtime = ext.meta.get("runtime") or {}
    if runtime.get("symbols") and inst.symbol not in runtime["symbols"]:
        raise ApiError(42231, "新策略不支持当前交易对")
    if inst.market_type not in (runtime.get("market_type") or ["spot"]):
        raise ApiError(42224, "新策略不支持当前市场类型")
    if bool(runtime.get("scan")) != inst.symbol.startswith("ALL/"):
        raise ApiError(42231, "扫描类策略与单币策略之间不能互换，请新建实例")
    open_orders = await ctx.db.scalar(select(func.count(Order.id)).where(
        Order.instance_id == inst.id, Order.status.in_(("submitted", "partial", "unknown")))) or 0
    if open_orders:
        raise ApiError(40902, "实例有未撤挂单，请先撤单")
    positions = await engine.framework_positions(inst.id)
    if positions:
        if not body.close_positions:
            raise ApiError(40904)
        # 有持仓：触发保护平仓（框架兜底），失败则中止换策略
        try:
            inst.status = "stopping"
            await ctx.db.commit()
            results = await engine.force_close(inst.id, actor=ctx.user)
        except ApiError as exc:
            inst.status, inst.last_error = "error", f"换策略平仓失败：{exc.message}"
            await ctx.db.commit()
            raise
        finally:
            await ctx.db.refresh(inst)
        if await engine.framework_positions(inst.id):
            inst.status, inst.last_error = "error", "换策略平仓未完成"
            await ctx.db.commit()
            raise ApiError(42201, "平仓未完成，已中止换策略", data={"results": results})
    params = _validate_params(ext, body.params)
    view = {"id": inst.id, "name": inst.name, "symbol": inst.symbol, "paper": inst.paper,
            "owner_id": inst.owner_id, "account_id": inst.account_id, "strategy_id": ext.id}
    params = _validate_params(ext, await _params_checkpoint(view, params, inst.owner_id, inst.account_id, ext))
    old = {"strategy_id": inst.strategy_id, "strategy_version": inst.strategy_version,
           "params": _masked(registry.get_strategy(inst.strategy_id), json.loads(inst.params))}
    # 名字是新建页自动生成的"旧策略名-xxxx"时一起改成新策略名，避免"定投加仓"实例实际跑着趋势回调
    old_ext = registry.get_strategy(inst.strategy_id)
    old_name = (old_ext.meta.get("name") if old_ext else None) or inst.strategy_id
    m = re.fullmatch(re.escape(old_name) + r"-([0-9a-z]{4})", inst.name or "")
    if m:
        inst.name = f"{ext.meta.get('name') or ext.id}-{m.group(1)}"[:64]
    inst.strategy_id, inst.strategy_version = ext.id, ext.version
    inst.params = _store(ext, params)  # 旧参数全部丢弃
    inst.status, inst.last_error = "stopped", None
    await ctx.db.commit()
    await write_audit(ctx.db, action="instance.change_strategy", target_type="instance", target_id=inst.id,
                      old_value=old, new_value={"strategy_id": ext.id, "strategy_version": ext.version,
                                                "params": _masked(ext, params), "had_positions": bool(positions)},
                      **ctx.audit_kw())
    return ok((await _out(ctx, [inst]))[0])


@router.delete("/api/instances/{instance_id}")
async def delete_instance(instance_id: int, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id)
    if inst.status not in {"stopped", "error", "created"}:
        raise ApiError(40903, "请先停止实例")
    if await ctx.db.scalar(select(func.count(Order.id)).where(
            Order.instance_id == inst.id, Order.status.in_(("submitted", "partial", "unknown")))):
        raise ApiError(40902, "实例有未撤挂单，请先撤单")
    if not inst.paper and await engine.framework_positions(inst.id):
        raise ApiError(40904, "实例有未平持仓，请先平仓")
    inst.status, inst.deleted_at, inst.enabled = "deleted", datetime.utcnow(), False
    await ctx.db.execute(update(ShareToken).where(ShareToken.instance_id == inst.id, ShareToken.revoked_at.is_(None))
                         .values(revoked_at=datetime.utcnow()))

    # 级联清理关联数据
    # 删除订单（保留已成交的订单作为历史记录，只删除未完成的）
    # 已撤订单只删没有成交的：撤单前可能已经部分成交，那是真实的成交记录，不能跟着删
    await ctx.db.execute(delete(Order).where(
        Order.instance_id == inst.id,
        or_(Order.status.in_(("submitted", "partial", "unknown")),
            and_(Order.status == "canceled", Order.filled_amount == 0))))
    # 删除持仓快照
    await ctx.db.execute(delete(PositionSnapshot).where(PositionSnapshot.instance_id == inst.id))
    # 删除模拟盘账户
    await ctx.db.execute(delete(PaperAccount).where(PaperAccount.instance_id == inst.id))
    # 删除模拟盘快照
    await ctx.db.execute(delete(PaperSnapshot).where(PaperSnapshot.instance_id == inst.id))
    # 删除对账差异记录
    await ctx.db.execute(delete(ReconcileDiff).where(ReconcileDiff.instance_id == inst.id))
    # 删除策略状态（ms.pos / ms.cool / ui.status 等）——不清理会永久残留，
    # 若实例 id 被复用或将来支持恢复实例，会读到不存在的虚拟持仓
    await ctx.db.execute(delete(InstanceState).where(InstanceState.instance_id == inst.id))
    await ctx.db.commit()
    engine._books.pop(inst.id, None)
    await write_audit(ctx.db, action="instance.delete", target_type="instance", target_id=inst.id, **ctx.audit_kw())
    return ok(None)


class ResetPaperIn(BaseModel):
    keep_orders: bool = False
    keep_snapshots: bool = False


@router.post("/api/instances/{instance_id}/reset-paper")
async def reset_paper(instance_id: int, body: ResetPaperIn, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id)
    if not inst.paper:
        raise ApiError(42260, "不是模拟盘实例")
    if inst.status not in {"stopped", "error", "created"}:
        raise ApiError(40903)
    await ctx.db.execute(delete(PaperAccount).where(PaperAccount.instance_id == inst.id))
    # 策略状态（ms.pos / ms.cool 等）记的是旧模拟账户里的虚拟持仓，账户清空后不清掉就会对不上
    await ctx.db.execute(delete(InstanceState).where(InstanceState.instance_id == inst.id))
    if not body.keep_orders:
        await ctx.db.execute(delete(Order).where(Order.instance_id == inst.id))
    if not body.keep_snapshots:
        await ctx.db.execute(delete(PaperSnapshot).where(PaperSnapshot.instance_id == inst.id))
        await ctx.db.execute(delete(PositionSnapshot).where(PositionSnapshot.instance_id == inst.id))
    await ctx.db.commit()
    engine._books.pop(inst.id, None)
    await write_audit(ctx.db, action="instance.reset_paper", target_type="instance", target_id=inst.id,
                      new_value=body.model_dump(), **ctx.audit_kw())
    return ok({"instance_id": inst.id, "balance": num(inst.paper_balance)})


# ====================================================================== 第八节 平仓
async def _cap(inst: Instance, cap: str) -> bool:
    ext = registry.get_strategy(inst.strategy_id)
    return bool(ext and ext.capabilities.get(cap))


class CloseIn(BaseModel):
    amount: float | None = Field(None, gt=0)


@router.post("/api/instances/{instance_id}/positions/{position_id:path}/close")
async def close_one(instance_id: int, position_id: str, body: CloseIn | None = None, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id)
    if not await _cap(inst, "close_position"):
        raise ApiError(42221, "策略未声明 close_position 能力，请使用强制平仓")
    if body and body.amount and not await _cap(inst, "close_partial"):
        raise ApiError(42221, "策略未声明部分平仓能力")
    if instance_id not in engine.running:
        raise ApiError(40902, "实例未运行，无法调用策略平仓；可使用强制平仓")
    ctx_ = await engine.ctx_for(instance_id)
    result = await engine.close_position(ctx_, position_id, body.amount if body else None, is_manual=True,
                                         actor_id=ctx.user.id)
    await write_audit(ctx.db, action="position.close", target_type="instance", target_id=instance_id,
                      new_value={"position_id": position_id, "amount": body.amount if body else None}, **ctx.audit_kw())
    return ok({"result": _jsonable(result)})


class CloseBatchIn(BaseModel):
    position_ids: list[str]


@router.post("/api/instances/{instance_id}/positions/close-batch")
async def close_batch(instance_id: int, body: CloseBatchIn, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id)
    if not await _cap(inst, "close_positions"):
        raise ApiError(42221, "策略未声明 close_positions 能力")
    if instance_id not in engine.running:
        raise ApiError(40902, "实例未运行")
    result = await engine.close_positions(await engine.ctx_for(instance_id), body.position_ids, is_manual=True,
                                          actor_id=ctx.user.id)
    await write_audit(ctx.db, action="position.close", target_type="instance", target_id=instance_id,
                      new_value={"position_ids": body.position_ids}, **ctx.audit_kw())
    return ok({"results": _jsonable(result)})


@router.post("/api/instances/{instance_id}/positions/close-all")
async def close_all(instance_id: int, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id)
    if not await _cap(inst, "close_all"):
        raise ApiError(42221, "策略未声明 close_all 能力")
    if instance_id not in engine.running:
        raise ApiError(40902, "实例未运行")
    result = await engine.close_all_positions(await engine.ctx_for(instance_id), is_manual=True, actor_id=ctx.user.id)
    await write_audit(ctx.db, action="position.close", target_type="instance", target_id=instance_id,
                      new_value={"all": True}, **ctx.audit_kw())
    return ok({"results": _jsonable(result)})


class ForceCloseIn(BaseModel):
    position_id: str | None = None


@router.post("/api/instances/{instance_id}/positions/force-close")
async def force_close(instance_id: int, body: ForceCloseIn | None = None, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id)
    if await _cap(inst, "close_all") or await _cap(inst, "close_position"):
        raise ApiError(42201, "策略已声明平仓能力，请使用常规平仓")
    if inst.status not in {"running", "paused", "stopped", "error"}:
        raise ApiError(40902)
    pid = body.position_id if body else None
    results = await engine.force_close(instance_id, actor=ctx.user, position_id=pid)
    return _close_outcome(results)


FILLED = {"filled", "partial", "submitted"}


def _close_outcome(results: list[dict[str, Any]]):
    """平仓接口的结果以订单真实状态为准：全部被拒 → 业务错误；部分被拒 → code 0 但 message 写明。"""
    if not results:
        raise ApiError(42245, "没有可平的持仓")
    bad = [r for r in results if (r.get("order") or {}).get("status") not in FILLED]
    if len(bad) == len(results):
        first = bad[0].get("order") or {}
        raise ApiError(42246, f"平仓未成交：{first.get('reason') or first.get('status') or '未知原因'}", data={"results": results})
    msg = "success" if not bad else f"{len(results) - len(bad)} 笔已平，{len(bad)} 笔未成交"
    return ok({"results": results, "failed": len(bad)}, message=msg)


@router.post("/api/instances/{instance_id}/orders/{order_id}/cancel")
async def cancel(instance_id: int, order_id: int, ctx: Ctx = Depends(current)):
    await own_instance(ctx.db, ctx.user, instance_id)
    run = engine.running.get(instance_id)
    if run and _has(run.strategy, "cancel_order"):
        result = await run.strategy.cancel_order(str(order_id))
        return ok({"result": _jsonable(result)})
    view = await engine.cancel_order(await engine.ctx_for(instance_id), order_id, actor_id=ctx.user.id)
    return ok(_jsonable(view))


@router.post("/api/instances/{instance_id}/orders/cancel-all")
async def cancel_all(instance_id: int, ctx: Ctx = Depends(current)):
    await own_instance(ctx.db, ctx.user, instance_id)
    run = engine.running.get(instance_id)
    if run and _has(run.strategy, "cancel_all_orders"):
        return ok({"result": _jsonable(await run.strategy.cancel_all_orders())})
    return ok(await engine.cancel_all_orders(await engine.ctx_for(instance_id), actor_id=ctx.user.id))


def _has(obj: Any, method: str) -> bool:
    from bamy.extensions.base import BaseStrategy

    return getattr(type(obj), method, None) is not getattr(BaseStrategy, method, None)


# ====================================================================== 第九节 订单
def _dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise ApiError(40002, f"时间格式错误：{value}", errors=[{"field": "time", "reason": "需为 ISO 8601"}]) from exc


def _order_stmt(ctx: Ctx, f: dict[str, Any], owner_filter: bool = True):
    stmt = select(Order)
    if owner_filter:
        stmt = stmt.where(Order.owner_id == ctx.user.id)
    for key, col in (("instance_id", Order.instance_id), ("account_id", Order.account_id), ("symbol", Order.symbol),
                     ("status", Order.status), ("source", Order.source), ("side", Order.side)):
        if f.get(key) not in (None, ""):
            stmt = stmt.where(col == (int_filter(key, f[key]) if key.endswith("_id") else f[key]))
    if f.get("start_time"):
        stmt = stmt.where(Order.created_at >= _dt(f["start_time"]))
    if f.get("end_time"):
        stmt = stmt.where(Order.created_at <= _dt(f["end_time"]))
    return stmt


async def _orders(ctx: Ctx, pg: Page, f: dict[str, Any], owner_filter: bool = True):
    stmt = _order_stmt(ctx, f, owner_filter)
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(Order.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    names = dict((await ctx.db.execute(select(Instance.id, Instance.name).where(
        Instance.id.in_({o.instance_id for o in rows} or {-1})))).all())
    items = [order_out(o) | {"instance_name": names.get(o.instance_id)} for o in rows]
    return paginated(items, total, pg.page, pg.page_size)


@router.get("/api/orders")
async def list_orders(request: Request, ctx: Ctx = Depends(current), pg: Page = Depends(paging)):
    return await _orders(ctx, pg, dict(request.query_params))


@router.get("/api/orders/export")
async def export_orders(request: Request, ctx: Ctx = Depends(current)):
    stmt = _order_stmt(ctx, dict(request.query_params))
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if total > EXPORT_MAX:
        raise ApiError(42201, f"导出行数 {total} 超过上限 {EXPORT_MAX}，请缩小筛选范围")
    rows = (await ctx.db.execute(stmt.order_by(Order.id.desc()))).scalars().all()
    buf = io.StringIO()
    buf.write("﻿")
    fields = ["id", "client_order_id", "exchange_order_id", "instance_id", "symbol", "side", "type", "amount",
              "price", "filled_amount", "filled_price", "fee", "status", "source", "is_guard", "reason",
              "intercepted_by", "created_at", "filled_at"]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for o in rows:
        w.writerow({k: csv_safe(v) for k, v in order_out(o).items()})
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=orders.csv"})


@router.get("/api/orders/{order_id}")
async def get_order(order_id: int, ctx: Ctx = Depends(current)):
    o = await ctx.db.get(Order, order_id)
    if o is None or (o.owner_id != ctx.user.id and not ctx.is_admin):
        raise ApiError(40405)
    out = order_out(o)
    out["raw_request"] = json.loads(o.raw_request) if o.raw_request else None
    if o.intercepted_by:
        rows, _ = await query_audit(ctx.db, {"target_type": "order", "target_id": o.id}, 1, 5)
        out["intercept_detail"] = [audit_dict(r) for r in rows]
    return ok(out)


@router.get("/api/orders/{order_id}/fills")
async def order_fills(order_id: int, ctx: Ctx = Depends(current)):
    o = await ctx.db.get(Order, order_id)
    if o is None or (o.owner_id != ctx.user.id and not ctx.is_admin):
        raise ApiError(40405)
    raw = json.loads(o.raw_response) if o.raw_response else {}
    trades = raw.get("trades") or []
    if not trades and float(o.filled_amount or 0) > 0:
        trades = [{"amount": float(o.filled_amount), "price": num(o.filled_price), "fee": num(o.fee),
                   "timestamp": iso(o.filled_at or o.updated_at)}]
    return ok({"order_id": o.id, "fills": trades})
