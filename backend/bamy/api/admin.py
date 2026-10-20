"""对账、风控、分享、审计、系统与管理员接口（《接口契约》第十一至十五节，22.5–22.13）。"""
from __future__ import annotations

import json
import logging
import secrets
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text, update

from bamy import __version__
from bamy.api.deps import Ctx, Page, admin, current, iso, num, own_instance, paging, super_admin
from bamy.api.serializers import account_out, instance_out, order_out, user_out
from bamy.core.config import ROOT_DIR, settings
from bamy.core.crypto import new_token
from bamy.core.errors import ApiError
from bamy.core.response import ok, paginated
from bamy.core.security import hash_password
from bamy.db.models import (
    Account, AuditLog, Backup, CircuitBreaker, Instance, InviteCode, InviteUsage, Notification, Order,
    PaperSnapshot, PluginBinding, ReconcileDiff, RefreshToken, RiskRule, ShareAccess, ShareToken, SystemConfig,
    SystemLog, User,
)
from bamy.db.session import get_session, session_scope
from bamy.engine.client_pool import pool
from bamy.engine.market import market
from bamy.engine.risk import RULE_TYPES
from bamy.engine.runtime import _jsonable, engine
from bamy.extensions.registry import registry
from bamy.services import system_config
from bamy.services.audit import audit_dict, query_audit, system_log_dict, write_audit

router = APIRouter()
BACKUP_DIR = ROOT_DIR / "data" / "backups"
READONLY = {"on": False}


# ====================================================================== 第十一节 对账
class ReconcileIn(BaseModel):
    account_id: int | None = None
    type: str = "all"   # all / orders / positions


@router.post("/api/admin/reconcile/trigger")
async def reconcile_trigger(body: ReconcileIn, ctx: Ctx = Depends(admin)):
    if body.type not in {"all", "orders", "positions"}:
        raise ApiError(40003)
    if body.account_id is not None:
        if await ctx.db.get(Account, body.account_id) is None:
            raise ApiError(40404)
        result: Any = {}
        if body.type in {"all", "orders"}:
            result["unknown_orders"] = await engine.reconcile_unknown()
        if body.type in {"all", "positions"}:
            result["account"] = await engine.reconcile_account(body.account_id, reason="manual")
    elif body.type == "orders":
        result = {"unknown_orders": await engine.reconcile_unknown(), "open_orders": await engine.reconcile_open()}
    else:
        result = await engine.reconcile_all()
    await write_audit(ctx.db, action="reconcile.trigger", target_type="account", target_id=body.account_id,
                      new_value={"type": body.type, "result": result}, **ctx.audit_kw())
    return ok(result)


def _diff_out(d: ReconcileDiff, names: dict[int, str] | None = None) -> dict[str, Any]:
    return {"id": d.id, "instance_id": d.instance_id, "account_id": d.account_id,
            "account_name": (names or {}).get(d.account_id), "diff_type": d.diff_type,
            "expected": json.loads(d.expected) if d.expected else None,
            "actual": json.loads(d.actual) if d.actual else None, "resolved": d.resolved,
            "resolved_at": iso(d.resolved_at), "created_at": iso(d.created_at)}


@router.get("/api/admin/reconcile/diffs")
async def diffs(ctx: Ctx = Depends(admin), pg: Page = Depends(paging), account_id: int | None = None,
                diff_type: str | None = None, resolved: bool | None = None, start_time: str | None = None,
                end_time: str | None = None):
    stmt = select(ReconcileDiff)
    for col, val in ((ReconcileDiff.account_id, account_id), (ReconcileDiff.diff_type, diff_type),
                     (ReconcileDiff.resolved, resolved)):
        if val is not None:
            stmt = stmt.where(col == val)
    if start_time:
        stmt = stmt.where(ReconcileDiff.created_at >= _dt(start_time))
    if end_time:
        stmt = stmt.where(ReconcileDiff.created_at <= _dt(end_time))
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(ReconcileDiff.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    names = dict((await ctx.db.execute(select(Account.id, Account.name))).all())
    return paginated([_diff_out(r, names) for r in rows], total, pg.page, pg.page_size)


@router.get("/api/admin/reconcile/diffs/{diff_id}")
async def diff_detail(diff_id: int, ctx: Ctx = Depends(admin)):
    d = await ctx.db.get(ReconcileDiff, diff_id)
    if d is None:
        raise ApiError(40401)
    return ok(_diff_out(d))


@router.post("/api/admin/reconcile/diffs/{diff_id}/resolve")
async def diff_resolve(diff_id: int, request: Request, ctx: Ctx = Depends(admin)):
    d = await ctx.db.get(ReconcileDiff, diff_id)
    if d is None:
        raise ApiError(40401)
    note = None
    try:
        note = (await request.json()).get("note")
    except Exception:  # noqa: BLE001
        pass
    d.resolved, d.resolved_at = True, datetime.utcnow()
    await ctx.db.commit()
    await write_audit(ctx.db, action="reconcile.resolve", target_type="reconcile_diff", target_id=diff_id,
                      new_value={"note": note}, **ctx.audit_kw())
    return ok(_diff_out(d))


@router.post("/api/admin/reconcile/diffs/{diff_id}/auto_fix")
async def diff_auto_fix(diff_id: int, ctx: Ctx = Depends(admin)):
    """
    自动修正对账差异：根据交易所实际状态更新本地持仓记录。
    仅支持持仓差异（position_short/position_extra/futures_*），不支持订单差异。
    """
    d = await ctx.db.get(ReconcileDiff, diff_id)
    if d is None:
        raise ApiError(40401)
    if d.resolved:
        raise ApiError(42248, "差异已标记为解决，不能重复修正")

    import json
    expected = json.loads(d.expected or "{}")
    actual = json.loads(d.actual or "{}")

    # 本地持仓由已成交订单和 PaperAccount 派生，当前没有独立 Position 表。
    # 不能把交易所实际数量直接写进最近一条记录，否则会破坏订单审计和 hedge 双边语义。
    raise ApiError(42250, "当前差异不能自动写回本地持仓，请先完成交易所复核后手动处理")



# ====================================================================== 第十二节 风控
def _cb_out(cb: CircuitBreaker, names: dict[str, dict[int, str]] | None = None) -> dict[str, Any]:
    target = None
    if names and cb.target_id is not None:
        target = names.get(cb.level, {}).get(cb.target_id)
    return {"id": cb.id, "level": cb.level, "target_id": cb.target_id, "target_name": target, "reason": cb.reason,
            "triggered_by": cb.triggered_by, "triggered_at": iso(cb.triggered_at), "recovered_at": iso(cb.recovered_at),
            "recovered_by": cb.recovered_by, "recover_reason": cb.recover_reason,
            "status": "active" if cb.recovered_at is None else "recovered"}


async def _cb_names(ctx: Ctx) -> dict[str, dict[int, str]]:
    return {"account": dict((await ctx.db.execute(select(Account.id, Account.name))).all()),
            "user": dict((await ctx.db.execute(select(User.id, User.username))).all())}


@router.get("/api/admin/circuit-breakers")
async def cb_list(ctx: Ctx = Depends(admin), pg: Page = Depends(paging), level: str | None = None,
                  active: bool | None = None):
    stmt = select(CircuitBreaker)
    if level:
        stmt = stmt.where(CircuitBreaker.level == level)
    if active is True:
        stmt = stmt.where(CircuitBreaker.recovered_at.is_(None))
    elif active is False:
        stmt = stmt.where(CircuitBreaker.recovered_at.is_not(None))
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(CircuitBreaker.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    names = await _cb_names(ctx)
    return paginated([_cb_out(r, names) for r in rows], total, pg.page, pg.page_size)


@router.get("/api/admin/circuit-breakers/{cb_id}")
async def cb_detail(cb_id: int, ctx: Ctx = Depends(admin)):
    cb = await ctx.db.get(CircuitBreaker, cb_id)
    if cb is None:
        raise ApiError(40401)
    return ok(_cb_out(cb, await _cb_names(ctx)))


class BreakIn(BaseModel):
    level: str
    target_id: int | None = None
    reason: str = Field(..., min_length=1)


@router.post("/api/admin/circuit-breakers")
async def cb_trigger(body: BreakIn, ctx: Ctx = Depends(admin)):
    if body.level not in {"account", "user", "global"} or (body.level == "global") != (body.target_id is None):
        raise ApiError(40003, "level 与 target_id 不匹配")
    if body.level == "global" and ctx.user.role != "super_admin":
        raise ApiError(40302, "全局熔断需要 super_admin")
    if body.level == "account":
        acc = await ctx.db.get(Account, body.target_id)
        if acc is None or acc.deleted_at is not None:
            raise ApiError(40404, "账号不存在")
    elif body.level == "user" and await ctx.db.get(User, body.target_id) is None:
        raise ApiError(40401, "用户不存在")
    cb = CircuitBreaker(level=body.level, target_id=body.target_id, reason=body.reason, triggered_by="admin")
    ctx.db.add(cb)
    await ctx.db.commit()
    await write_audit(ctx.db, action="risk.circuit_break", target_type=body.level, target_id=body.target_id,
                      new_value={"reason": body.reason, "id": cb.id}, **ctx.audit_kw())
    payload = {"id": cb.id, "level": cb.level, "target_id": cb.target_id, "reason": cb.reason}
    if body.level == "account":
        payload["account_id"] = body.target_id
        acc = await ctx.db.get(Account, body.target_id)
        payload["owner_id"] = acc.owner_id if acc else None
    elif body.level == "user":
        payload["owner_id"] = body.target_id
    await engine.push("risk.circuit_break", payload)
    return ok(_cb_out(cb, await _cb_names(ctx)), status_code=201)


class RecoverIn(BaseModel):
    reason: str = Field(..., min_length=1)


@router.post("/api/admin/circuit-breakers/{cb_id}/recover")
async def cb_recover(cb_id: int, body: RecoverIn, ctx: Ctx = Depends(admin)):
    cb = await ctx.db.get(CircuitBreaker, cb_id)
    if cb is None:
        raise ApiError(40401)
    if cb.recovered_at is not None:
        return ok(_cb_out(cb))
    if cb.level == "global" and ctx.user.role != "super_admin":
        raise ApiError(40302, "解除全局熔断需要 super_admin")
    cb.recovered_at, cb.recovered_by, cb.recover_reason = datetime.utcnow(), ctx.user.id, body.reason
    await ctx.db.commit()
    await write_audit(ctx.db, action="risk.circuit_recover", target_type=cb.level, target_id=cb.target_id,
                      new_value={"reason": body.reason, "id": cb.id}, **ctx.audit_kw())
    await engine.push("risk.circuit_recover", {"id": cb.id, "level": cb.level, "target_id": cb.target_id,
                                               "account_id": cb.target_id if cb.level == "account" else None})
    return ok(_cb_out(cb))


def _rule_out(r: RiskRule) -> dict[str, Any]:
    return {"id": r.id, "owner_id": r.owner_id, "account_id": r.account_id, "rule_type": r.rule_type,
            "rule_value": json.loads(r.rule_value or "{}"), "enabled": r.enabled, "created_at": iso(r.created_at),
            "updated_at": iso(r.updated_at), "scope": "global" if r.owner_id is None else ("account" if r.account_id else "user")}



# 各类风控规则的取值格式（前端按这个渲染表单；后端独立校验）
RULE_SHAPES: dict[str, tuple[str, float, float]] = {
    "max_order_value": ("value", 1, 1e9),          # 单笔金额上限 USDT
    "max_daily_value": ("value", 1, 1e10),         # 单日累计下单金额上限 USDT
    "max_daily_orders": ("value", 1, 1e6),         # 单日下单次数
    "max_orders_per_minute": ("value", 1, 1e4),    # 每分钟下单次数
    "max_drawdown": ("value", 0.01, 1),            # 最大回撤比例（0.3 = 30%）
    "max_daily_loss": ("value", 0.01, 1),          # 单日最大亏损比例
    "symbol_cooldown": ("seconds", 1, 86400 * 7),  # 同一交易对成交后冷却秒数
    "strategy_cooldown": ("seconds", 1, 86400 * 7),
    "max_leverage": ("value", 1, 20),              # 合约最大杠杆（倍）
}


def _check_rule_value(rule_type: str, value: dict[str, Any]) -> dict[str, Any]:
    if rule_type in {"symbol_whitelist", "symbol_blacklist"}:
        syms = value.get("symbols")
        if not isinstance(syms, list) or not syms or not all(isinstance(x, str) and "/" in x and len(x) <= 32 for x in syms):
            raise ApiError(40003, "symbols 需为非空交易对列表，如 [\"BTC/USDT\"]")
        return {"symbols": sorted({x.strip().upper() for x in syms})[:200]}
    key, lo, hi = RULE_SHAPES[rule_type]
    try:
        v = float(value.get(key))
    except (TypeError, ValueError):
        raise ApiError(40003, f"{rule_type} 需要数值字段 {key}") from None
    if not (lo <= v <= hi):
        raise ApiError(40003, f"{key} 取值范围 {lo}–{hi}")
    return {key: v}


@router.get("/api/risk-rules")
async def rules(ctx: Ctx = Depends(current)):
    stmt = select(RiskRule)
    if not ctx.is_admin:
        stmt = stmt.where((RiskRule.owner_id == ctx.user.id) | (RiskRule.owner_id.is_(None)))
    rows = (await ctx.db.execute(stmt.order_by(RiskRule.id))).scalars().all()
    shapes = {k: {"field": v[0], "min": v[1], "max": v[2]} for k, v in RULE_SHAPES.items()}
    shapes |= {k: {"field": "symbols", "list": True} for k in ("symbol_whitelist", "symbol_blacklist")}
    return ok({"items": [_rule_out(r) for r in rows], "rule_types": sorted(RULE_TYPES), "shapes": shapes})


class RuleIn(BaseModel):
    rule_type: str
    rule_value: dict[str, Any]
    account_id: int | None = None
    enabled: bool = True
    global_rule: bool = False


@router.post("/api/risk-rules")
async def create_rule(body: RuleIn, ctx: Ctx = Depends(current)):
    if body.rule_type not in RULE_TYPES:
        raise ApiError(40003, f"rule_type 取值：{', '.join(sorted(RULE_TYPES))}")
    if body.global_rule and not ctx.is_admin:
        raise ApiError(40302)
    if body.account_id is not None:
        acc = await ctx.db.get(Account, body.account_id)
        if acc is None or (acc.owner_id != ctx.user.id and not ctx.is_admin):
            raise ApiError(40404)
    clean = _check_rule_value(body.rule_type, body.rule_value)
    r = RiskRule(owner_id=None if body.global_rule else ctx.user.id, account_id=body.account_id,
                 rule_type=body.rule_type, rule_value=json.dumps(clean), enabled=body.enabled)
    ctx.db.add(r)
    await ctx.db.commit()
    await write_audit(ctx.db, action="risk.rule_create", target_type="risk_rule", target_id=r.id,
                      new_value=_rule_out(r), **ctx.audit_kw())
    return ok(_rule_out(r), status_code=201)


async def _rule(ctx: Ctx, rule_id: int) -> RiskRule:
    r = await ctx.db.get(RiskRule, rule_id)
    if r is None or (r.owner_id != ctx.user.id and not ctx.is_admin):
        raise ApiError(40401)
    if r.owner_id is None and not ctx.is_admin:
        raise ApiError(40302)
    return r


class RulePatch(BaseModel):
    rule_value: dict[str, Any] | None = None
    enabled: bool | None = None


@router.patch("/api/risk-rules/{rule_id}")
async def update_rule(rule_id: int, body: RulePatch, ctx: Ctx = Depends(current)):
    r = await _rule(ctx, rule_id)
    old = _rule_out(r)
    if body.rule_value is not None:
        r.rule_value = json.dumps(_check_rule_value(r.rule_type, body.rule_value))
    if body.enabled is not None:
        r.enabled = body.enabled
    await ctx.db.commit()
    await write_audit(ctx.db, action="risk.rule_update", target_type="risk_rule", target_id=r.id, old_value=old,
                      new_value=_rule_out(r), **ctx.audit_kw())
    return ok(_rule_out(r))


@router.delete("/api/risk-rules/{rule_id}")
async def delete_rule(rule_id: int, ctx: Ctx = Depends(current)):
    r = await _rule(ctx, rule_id)
    await ctx.db.delete(r)
    await ctx.db.commit()
    await write_audit(ctx.db, action="risk.rule_delete", target_type="risk_rule", target_id=rule_id, **ctx.audit_kw())
    return ok(None)


# ====================================================================== 第十三节 分享
class ShareIn(BaseModel):
    expires_in_days: int | None = Field(7, ge=1, le=365)
    show_equity: bool = True
    show_positions: bool = True
    show_orders: bool = True
    show_params: bool = False
    show_logs: bool = False


def _share_out(s: ShareToken, request: Request | None = None) -> dict[str, Any]:
    # 配了 PUBLIC_BASE_URL（对外域名）就用它；否则用请求的 Host（nginx 透传 $http_host，带端口）
    import os

    base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/") or (str(request.base_url).rstrip("/") if request else "")
    return {"id": s.id, "token": s.token, "url": f"{base}/view/{s.token}", "instance_id": s.instance_id,
            "show_equity": s.show_equity, "show_positions": s.show_positions, "show_orders": s.show_orders,
            "show_params": s.show_params, "show_logs": s.show_logs, "access_count": s.access_count,
            "last_access_at": iso(s.last_access_at), "created_at": iso(s.created_at),
            "expires_at": iso(s.expires_at), "revoked_at": iso(s.revoked_at),
            "status": "revoked" if s.revoked_at else ("expired" if s.expires_at and s.expires_at < datetime.utcnow() else "active")}


@router.post("/api/instances/{instance_id}/share-tokens")
async def create_share(instance_id: int, body: ShareIn, request: Request, ctx: Ctx = Depends(current)):
    await own_instance(ctx.db, ctx.user, instance_id)
    s = ShareToken(token=new_token(24), instance_id=instance_id, owner_id=ctx.user.id,
                   expires_at=datetime.utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
                   **body.model_dump(exclude={"expires_in_days"}))
    ctx.db.add(s)
    await ctx.db.commit()
    await write_audit(ctx.db, action="share.create", target_type="instance", target_id=instance_id,
                      new_value={"share_id": s.id}, **ctx.audit_kw())
    return ok(_share_out(s, request), status_code=201)


@router.get("/api/share-tokens")
async def list_shares(request: Request, ctx: Ctx = Depends(current), instance_id: int | None = None):
    stmt = select(ShareToken).where(ShareToken.owner_id == ctx.user.id)
    if instance_id:
        stmt = stmt.where(ShareToken.instance_id == instance_id)
    rows = (await ctx.db.execute(stmt.order_by(ShareToken.id.desc()))).scalars().all()
    return ok({"items": [_share_out(s, request) for s in rows]})


@router.delete("/api/share-tokens/{share_id}")
async def revoke_share(share_id: int, ctx: Ctx = Depends(current)):
    s = await ctx.db.get(ShareToken, share_id)
    if s is None or s.owner_id != ctx.user.id:
        raise ApiError(40401)
    s.revoked_at = datetime.utcnow()
    await ctx.db.commit()
    from bamy.api.ws import hub

    await hub.close_views(share_id=s.id)
    await write_audit(ctx.db, action="share.revoke", target_type="instance", target_id=s.instance_id,
                      new_value={"share_id": s.id}, **ctx.audit_kw())
    return ok(None)


async def resolve_share(db, token: str) -> tuple[ShareToken, Instance]:
    s = (await db.execute(select(ShareToken).where(ShareToken.token == token))).scalar_one_or_none()
    if s is None:
        raise ApiError(40102, "链接无效")
    if s.revoked_at is not None:
        raise ApiError(40305, "链接已被撤销")
    if s.expires_at and s.expires_at < datetime.utcnow():
        raise ApiError(40103, "链接已过期")
    inst = await db.get(Instance, s.instance_id)
    if inst is None or inst.deleted_at is not None:
        raise ApiError(40305, "实例已不可访问")
    owner = await db.get(User, s.owner_id)
    if owner is None or owner.status != "active":
        raise ApiError(40305, "链接已失效")
    return s, inst


@router.get("/view/{token}")
async def view_page(token: str, request: Request, db=Depends(get_session)):
    """契约 13.4：同一路径既是访客页面也是数据接口——要 JSON 时返回数据，否则交给前端 SPA。"""
    accept = request.headers.get("accept", "")
    if "application/json" in accept or "text/html" not in accept:
        return await view(token, request, db)
    index = settings.frontend_dist / "index.html"
    if index.exists():
        return FileResponse(index)
    return PlainTextResponse("前端未构建：请执行 frontend 的 npm run build", status_code=503)


@router.get("/view/{token}/data")
@router.get("/api/view/{token}")
async def view(token: str, request: Request, db=Depends(get_session)):
    s, inst = await resolve_share(db, token)
    s.access_count = (s.access_count or 0) + 1
    s.last_access_at = datetime.utcnow()
    db.add(ShareAccess(share_token_id=s.id, ip=request.client.host if request.client else None,
                       user_agent=request.headers.get("user-agent")))
    await db.commit()
    ext = registry.get_strategy(inst.strategy_id)
    data: dict[str, Any] = {"instance_name": inst.name, "strategy_name": ext.meta.get("name") if ext else inst.strategy_id,
                            "symbol": inst.symbol, "paper": inst.paper, "status": inst.status, "readonly": True}
    if s.show_equity:
        snaps = (await db.execute(select(PaperSnapshot).where(PaperSnapshot.instance_id == inst.id)
                                  .order_by(PaperSnapshot.snapshot_at))).scalars().all()
        if inst.paper:
            initial = float(inst.paper_balance or 0)
            book = await engine._get_book(inst.id)
            equity = float(book.equity(await engine._prices(book)))
            data |= {"equity": equity, "return_rate": round((equity - initial) / initial, 6) if initial else None,
                     "equity_curve": [{"t": iso(x.snapshot_at), "v": float(x.equity)} for x in snaps[-500:]]}
        else:
            # 实盘：有快照取最新快照；没有快照（新实例、从没写过快照）就按实时盈亏口径算，不能整段不返回
            if not snaps:
                from bamy.services.live_equity import backfill_capital, live_pnl

                lp = await backfill_capital(db, inst, await live_pnl(db, inst))
                if lp["equity"] is not None:
                    initial = lp["initial"]
                    data |= {"equity": lp["equity"],
                             "return_rate": round(lp["pnl"] / initial, 6) if initial else None,
                             "equity_curve": []}
                else:
                    data |= {"pnl": lp["pnl"]}
            if snaps:
                latest = snaps[-1]
                equity = float(latest.equity)
                initial = float(latest.balance) - float(latest.realized_pnl or 0) if latest.balance and latest.realized_pnl else None
                data |= {"equity": equity,
                         "return_rate": round((equity - initial) / initial, 6) if initial and initial > 0 else None,
                         "equity_curve": [{"t": iso(x.snapshot_at), "v": float(x.equity)} for x in snaps[-500:]]}
    if s.show_positions:
        data["positions"] = _jsonable(await engine.framework_positions(inst.id))
    if s.show_orders:
        rows = (await db.execute(select(Order).where(Order.instance_id == inst.id).order_by(Order.id.desc()).limit(50))).scalars().all()
        data["orders"] = [{k: v for k, v in order_out(o).items() if k not in {"owner_id", "account_id", "raw_request"}} for o in rows]
    if s.show_params:
        data["params"] = {k: v for k, v in json.loads(inst.params or "{}").items()
                          if not any(p["key"] == k and (p.get("type") == "secret" or p.get("secret"))
                                     for p in (ext.params_schema if ext else []))}
    if s.show_logs:
        rows = (await db.execute(select(SystemLog).where(SystemLog.instance_id == inst.id)
                                 .order_by(SystemLog.id.desc()).limit(100))).scalars().all()
        data["logs"] = [system_log_dict(r) for r in rows]
    return ok(data)


# ====================================================================== 第十四节 审计
@router.get("/api/admin/audit-logs")
async def admin_audit(request: Request, ctx: Ctx = Depends(admin), pg: Page = Depends(paging)):
    rows, total = await query_audit(ctx.db, dict(request.query_params), pg.page, pg.page_size)
    return paginated([audit_dict(r) for r in rows], total, pg.page, pg.page_size)


@router.get("/api/admin/audit-logs/{log_id}")
async def admin_audit_detail(log_id: int, ctx: Ctx = Depends(admin)):
    r = await ctx.db.get(AuditLog, log_id)
    if r is None:
        raise ApiError(40401)
    return ok(audit_dict(r))


# ====================================================================== 第十五节 系统
@router.get("/health")
async def health(db=Depends(get_session)):
    checks = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001 - 详情只进日志，不对外暴露
        logging.getLogger("bamy.health").exception("数据库健康检查失败")
        checks["database"] = "error"
    checks["engine"] = "ok" if engine.ready else "starting"
    stale = [s for s in market.subscriptions() if market.is_stale(s)]
    checks["market_hub"] = "ok" if not stale else f"stale: {','.join(stale)}"
    healthy = checks["database"] == "ok" and checks["market_hub"] == "ok" and engine.ready
    body = {"status": "healthy" if healthy else "unhealthy", "version": __version__,
            "uptime": int(time.time() - engine.started_at), "checks": checks,
            "readonly": READONLY["on"]}
    from fastapi.responses import JSONResponse

    return JSONResponse(body, status_code=200 if healthy else 503)


@router.get("/metrics")
async def metrics():
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    from bamy.core import metrics as m

    m.refresh(engine)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/admin/configs")
async def configs(ctx: Ctx = Depends(admin)):
    rows = {r.key: r for r in (await ctx.db.execute(select(SystemConfig))).scalars()}
    items = []
    for key, default in settings.defaults.items():
        r = rows.get(key)
        items.append({"key": key, "value": system_config.get(key), "default": default,
                      "type": type(default).__name__, "group": key.split(".")[0],
                      "updated_at": iso(r.updated_at) if r else None, "updated_by": r.updated_by if r else None,
                      "description": r.description if r else None})
    env = {"APP_ENV": settings.app_env, "ENABLE_LIVE_TRADING": settings.enable_live_trading,
           "ALLOW_REGISTER": settings.allow_register, "AUTO_RECOVER_ON_BOOT": settings.auto_recover_on_boot,
           "MAX_INSTANCES_PER_USER": settings.max_instances_per_user,
           "DATABASE": settings.database_url.split(":")[0]}
    return ok({"items": items, "env": env, "readonly": READONLY["on"]})


class ConfigsIn(BaseModel):
    configs: dict[str, Any]


ENV_ALIASES = {"MAX_INSTANCES_PER_USER": "instance.max_per_user"}

# 数值配置的合法范围（《配置项》第四节）；不在表里的数值项至少要求非负
CONFIG_RANGES: dict[str, tuple[float, float]] = {
    "instance.max_per_user": (1, 1000),
    "account.max_per_user": (1, 200),
    "plugin.max_per_user": (0, 200),
    "plugin.auto_disable_threshold": (0, 1000),
    "paper.default_fee_rate": (0, 0.01),
    "paper.default_slippage": (0, 0.05),
    "paper.snapshot_interval": (10, 86400),
    "risk.max_orders_per_minute": (1, 10000),
    "risk.max_orders_per_day": (1, 1_000_000),
    "risk.max_daily_loss": (0, 1),
    "risk.max_drawdown": (0, 1),
}


def _range_for(key: str, value) -> tuple[float, float] | None:
    if key in CONFIG_RANGES:
        return CONFIG_RANGES[key]
    return (0, float("inf")) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@router.patch("/api/super/configs")
async def update_configs(body: ConfigsIn, ctx: Ctx = Depends(super_admin)):
    changed = {}
    for key, value in body.configs.items():
        key = ENV_ALIASES.get(key, key)
        if key not in settings.defaults:
            raise ApiError(40003, f"配置项 {key} 不存在")
        default = settings.defaults[key]
        try:
            value = type(default)(value) if not isinstance(default, bool) else (value in (True, "true", 1, "1"))
        except (TypeError, ValueError) as exc:
            raise ApiError(40002, f"{key} 类型应为 {type(default).__name__}") from exc
        rng = _range_for(key, value)
        if rng is not None and not isinstance(value, bool) and not (rng[0] <= value <= rng[1]):
            raise ApiError(40003, f"{key} 取值范围 {rng[0]}–{rng[1]}")
        old = system_config.get(key)
        await system_config.set_value(ctx.db, key, value, ctx.user.id)
        changed[key] = {"old": old, "new": value}
    await write_audit(ctx.db, action="config.update", target_type="system", new_value=changed, **ctx.audit_kw())
    return ok({"updated": changed})


@router.get("/api/admin/overview")
async def overview(ctx: Ctx = Depends(admin)):
    db = ctx.db
    day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total_inst = await db.scalar(select(func.count(Instance.id)).where(Instance.deleted_at.is_(None))) or 0
    paper = await db.scalar(select(func.count(Instance.id)).where(Instance.deleted_at.is_(None), Instance.paper.is_(True))) or 0
    volume = await db.scalar(select(func.coalesce(func.sum(Order.filled_amount * Order.filled_price), 0)).where(
        Order.created_at >= day, Order.status.in_(("filled", "partial")))) or 0
    active_cb = await db.scalar(select(func.count(CircuitBreaker.id)).where(CircuitBreaker.recovered_at.is_(None))) or 0
    unresolved = await db.scalar(select(func.count(ReconcileDiff.id)).where(ReconcileDiff.resolved.is_(False))) or 0
    auto_disabled = await db.scalar(select(func.count(PluginBinding.id)).where(
        PluginBinding.deleted_at.is_(None), PluginBinding.enabled.is_(True), PluginBinding.status == "disabled")) or 0
    recent_cb = (await db.execute(select(CircuitBreaker).order_by(CircuitBreaker.id.desc()).limit(5))).scalars().all()
    recent_diff = (await db.execute(select(ReconcileDiff).order_by(ReconcileDiff.id.desc()).limit(5))).scalars().all()
    alerts = (await db.execute(select(Notification).where(Notification.owner_id.is_(None))
                               .order_by(Notification.id.desc()).limit(10))).scalars().all()
    top = (await db.execute(select(Order.owner_id, func.count(Order.id)).where(Order.created_at >= day - timedelta(days=7))
                            .group_by(Order.owner_id).order_by(func.count(Order.id).desc()).limit(10))).all()
    names = dict((await db.execute(select(User.id, User.username))).all())
    return ok({
        "total_users": await db.scalar(select(func.count(User.id))) or 0,
        "total_accounts": await db.scalar(select(func.count(Account.id)).where(Account.deleted_at.is_(None))) or 0,
        "total_instances": total_inst,
        "running_instances": await db.scalar(select(func.count(Instance.id)).where(Instance.status == "running")) or 0,
        "paper_instances": paper, "live_instances": total_inst - paper,
        "today_orders": await db.scalar(select(func.count(Order.id)).where(Order.created_at >= day)) or 0,
        "today_volume": float(volume), "active_alerts": active_cb + unresolved + auto_disabled,
        "engine": engine.stats(), "extensions": {"strategies": len(registry.strategies), "plugins": len(registry.plugins),
                                                 "failed": registry.failed},
        "system": _resources(),
        "recent_circuit_breakers": [_cb_out(c) for c in recent_cb],
        "recent_reconcile_diffs": [_diff_out(d) for d in recent_diff],
        "recent_alerts": [{"id": a.id, "level": a.level, "title": a.title, "body": a.body, "created_at": iso(a.created_at)} for a in alerts],
        "active_users_top10": [{"user_id": u, "username": names.get(u), "orders": c} for u, c in top],
        "plugin_alerts": {"auto_disabled_bindings": auto_disabled,
                          "failed_plugins": [k for k, e in registry.plugins.items() if not e.loaded]},
    })


def _resources() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        import os

        load = os.getloadavg()
        out["load_avg"] = [round(x, 2) for x in load]
        with open("/proc/meminfo") as fh:
            info = {line.split(":")[0]: int(line.split()[1]) for line in fh if ":" in line}
        out["memory_total_mb"] = info.get("MemTotal", 0) // 1024
        out["memory_available_mb"] = info.get("MemAvailable", 0) // 1024
        du = shutil.disk_usage(ROOT_DIR)
        out["disk_free_gb"] = round(du.free / 1e9, 2)
    except Exception:  # noqa: BLE001
        pass
    return out


# ---- 用户管理
@router.get("/api/admin/users")
async def users(ctx: Ctx = Depends(admin), pg: Page = Depends(paging), keyword: str | None = None,
                role: str | None = None, status: str | None = None):
    stmt = select(User)
    if keyword:
        stmt = stmt.where(User.username.contains(keyword) | User.nickname.contains(keyword))
    if role:
        stmt = stmt.where(User.role == role)
    if status:
        stmt = stmt.where(User.status == status)
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(User.id).offset(pg.offset).limit(pg.page_size))).scalars().all()
    ids = [u.id for u in rows] or [-1]
    acc = dict((await ctx.db.execute(select(Account.owner_id, func.count(Account.id)).where(
        Account.owner_id.in_(ids), Account.deleted_at.is_(None)).group_by(Account.owner_id))).all())
    ins = dict((await ctx.db.execute(select(Instance.owner_id, func.count(Instance.id)).where(
        Instance.owner_id.in_(ids), Instance.deleted_at.is_(None)).group_by(Instance.owner_id))).all())
    bnd = dict((await ctx.db.execute(select(PluginBinding.owner_id, func.count(PluginBinding.id)).where(
        PluginBinding.owner_id.in_(ids), PluginBinding.deleted_at.is_(None)).group_by(PluginBinding.owner_id))).all())
    return paginated([user_out(u) | {"account_count": acc.get(u.id, 0), "instance_count": ins.get(u.id, 0),
                                     "binding_count": bnd.get(u.id, 0)} for u in rows], total, pg.page, pg.page_size)


@router.get("/api/admin/users/{user_id}")
async def user_detail(user_id: int, ctx: Ctx = Depends(admin)):
    u = await ctx.db.get(User, user_id)
    if u is None:
        raise ApiError(40401)
    counts = {
        "account_count": await ctx.db.scalar(select(func.count(Account.id)).where(Account.owner_id == u.id, Account.deleted_at.is_(None))) or 0,
        "instance_count": await ctx.db.scalar(select(func.count(Instance.id)).where(Instance.owner_id == u.id, Instance.deleted_at.is_(None))) or 0,
        "binding_count": await ctx.db.scalar(select(func.count(PluginBinding.id)).where(PluginBinding.owner_id == u.id, PluginBinding.deleted_at.is_(None))) or 0,
        "order_count": await ctx.db.scalar(select(func.count(Order.id)).where(Order.owner_id == u.id)) or 0,
    }
    return ok(user_out(u) | counts)


async def _target_user(ctx: Ctx, user_id: int) -> User:
    u = await ctx.db.get(User, user_id)
    if u is None:
        raise ApiError(40401)
    if u.id == ctx.user.id:
        raise ApiError(40301, "不能对自己执行该操作")
    if u.role == "super_admin" and ctx.user.role != "super_admin":
        raise ApiError(40302)
    return u


@router.post("/api/admin/users/{user_id}/disable")
async def disable_user(user_id: int, ctx: Ctx = Depends(admin)):
    u = await _target_user(ctx, user_id)
    u.status = "disabled"
    now = datetime.utcnow()
    await ctx.db.execute(update(RefreshToken).where(RefreshToken.user_id == u.id).values(revoked_at=now))
    # 《用户与权限设计》：禁用后其分享链接立即失效
    await ctx.db.execute(update(ShareToken).where(ShareToken.owner_id == u.id, ShareToken.revoked_at.is_(None))
                         .values(revoked_at=now))
    await ctx.db.commit()
    from bamy.api.ws import hub

    await hub.close_views(owner_id=u.id)
    # 禁用后其实例全部停止，数据保留
    for iid in [i for i, r in engine.running.items() if r.owner_id == u.id]:
        await engine.stop(iid, actor=ctx.user, reason="用户被禁用")
    await write_audit(ctx.db, action="user.disable", target_type="user", target_id=u.id, **ctx.audit_kw())
    return ok(user_out(u))


@router.post("/api/admin/users/{user_id}/enable")
async def enable_user(user_id: int, ctx: Ctx = Depends(admin)):
    u = await _target_user(ctx, user_id)
    u.status, u.failed_login_count, u.locked_until = "active", 0, None
    await ctx.db.commit()
    await write_audit(ctx.db, action="user.enable", target_type="user", target_id=u.id, **ctx.audit_kw())
    return ok(user_out(u))


@router.post("/api/admin/users/{user_id}/reset-password")
async def reset_user_password(user_id: int, ctx: Ctx = Depends(admin)):
    u = await _target_user(ctx, user_id)
    temp = secrets.token_urlsafe(9) + "a1"
    u.password_hash = hash_password(temp)
    u.status = "active" if u.status == "locked" else u.status
    u.failed_login_count, u.locked_until = 0, None
    await ctx.db.execute(update(RefreshToken).where(RefreshToken.user_id == u.id).values(revoked_at=datetime.utcnow()))
    await ctx.db.commit()
    await write_audit(ctx.db, action="user.reset_password", target_type="user", target_id=u.id, **ctx.audit_kw())
    return ok({"temp_password": temp})


class RoleIn(BaseModel):
    role: str


@router.patch("/api/super/users/{user_id}/role")
async def change_role(user_id: int, body: RoleIn, ctx: Ctx = Depends(super_admin)):
    if body.role not in {"user", "admin", "super_admin"}:
        raise ApiError(40003)
    u = await _target_user(ctx, user_id)
    old = u.role
    u.role = body.role
    await ctx.db.commit()
    await write_audit(ctx.db, action="user.role_change", target_type="user", target_id=u.id,
                      old_value={"role": old}, new_value={"role": body.role}, **ctx.audit_kw())
    return ok(user_out(u))


@router.delete("/api/super/users/{user_id}")
async def hard_delete_user(user_id: int, request: Request, ctx: Ctx = Depends(super_admin)):
    u = await _target_user(ctx, user_id)
    try:
        confirm = (await request.json()).get("confirm")
    except Exception:  # noqa: BLE001
        confirm = None
    if confirm != u.username:
        raise ApiError(40004, "请在请求体 confirm 字段填入用户名以确认硬删除")
    if any(r.owner_id == u.id for r in engine.running.values()):
        raise ApiError(40903, "该用户仍有运行中的实例")
    inst_ids = (await ctx.db.execute(select(Instance.id).where(Instance.owner_id == u.id))).scalars().all()
    from bamy.db import models as M

    for model, col in ((M.Order, M.Order.owner_id), (M.PaperSnapshot, M.PaperSnapshot.owner_id),
                       (M.PositionSnapshot, M.PositionSnapshot.owner_id), (M.ShareToken, M.ShareToken.owner_id),
                       (M.ApiToken, M.ApiToken.user_id), (M.RefreshToken, M.RefreshToken.user_id),
                       (M.UserSession, M.UserSession.user_id), (M.UserPreference, M.UserPreference.user_id),
                       (M.RiskRule, M.RiskRule.owner_id), (M.Notification, M.Notification.owner_id)):
        await ctx.db.execute(delete(model).where(col == u.id))
    if inst_ids:
        for model, col in ((M.PaperAccount, M.PaperAccount.instance_id), (M.InstanceState, M.InstanceState.instance_id)):
            await ctx.db.execute(delete(model).where(col.in_(inst_ids)))
    binding_ids = (await ctx.db.execute(select(PluginBinding.id).where(PluginBinding.owner_id == u.id))).scalars().all()
    if binding_ids:
        await ctx.db.execute(delete(M.PluginState).where(M.PluginState.binding_id.in_(binding_ids)))
        await ctx.db.execute(delete(M.PluginHealth).where(M.PluginHealth.binding_id.in_(binding_ids)))
    await ctx.db.execute(delete(PluginBinding).where(PluginBinding.owner_id == u.id))
    await ctx.db.execute(delete(Instance).where(Instance.owner_id == u.id))
    acc_ids = (await ctx.db.execute(select(Account.id).where(Account.owner_id == u.id))).scalars().all()
    if acc_ids:
        await ctx.db.execute(delete(M.AccountHealth).where(M.AccountHealth.account_id.in_(acc_ids)))
    await ctx.db.execute(delete(Account).where(Account.owner_id == u.id))
    await ctx.db.delete(u)
    await ctx.db.commit()
    for aid in acc_ids:
        await pool.drop(aid)
    await write_audit(ctx.db, action="user.delete", target_type="user", target_id=user_id,
                      old_value={"username": u.username}, **ctx.audit_kw())
    return ok(None)


# ---- 账号、实例、日志
@router.get("/api/admin/accounts")
async def admin_accounts(ctx: Ctx = Depends(admin), pg: Page = Depends(paging), owner_id: int | None = None,
                         status: str | None = None):
    stmt = select(Account).where(Account.deleted_at.is_(None))
    if owner_id:
        stmt = stmt.where(Account.owner_id == owner_id)
    if status:
        stmt = stmt.where(Account.status == status)
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(Account.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    names = dict((await ctx.db.execute(select(User.id, User.username))).all())
    return paginated([account_out(a) | {"owner_username": names.get(a.owner_id)} for a in rows], total, pg.page, pg.page_size)


@router.post("/api/admin/accounts/{account_id}/disable")
async def admin_disable_account(account_id: int, ctx: Ctx = Depends(admin)):
    a = await ctx.db.get(Account, account_id)
    if a is None or a.deleted_at is not None:
        raise ApiError(40404)
    a.status = "disabled"
    await ctx.db.commit()
    inst_ids = (await ctx.db.execute(select(Instance.id).where(Instance.account_id == a.id))).scalars().all()
    for iid in inst_ids:
        if iid in engine.running:
            await engine.stop(iid, actor=ctx.user, reason="账号被管理员禁用")
    await pool.drop(a.id)
    await write_audit(ctx.db, action="account.disable", target_type="account", target_id=a.id, **ctx.audit_kw())
    return ok(account_out(a))


@router.post("/api/admin/accounts/{account_id}/enable")
async def admin_enable_account(account_id: int, ctx: Ctx = Depends(admin)):
    a = await ctx.db.get(Account, account_id)
    if a is None or a.deleted_at is not None:
        raise ApiError(40404)
    a.status = "offline"  # 启用后需重新验证才回到 online
    await ctx.db.commit()
    await write_audit(ctx.db, action="account.enable", target_type="account", target_id=a.id, **ctx.audit_kw())
    return ok(account_out(a))


@router.get("/api/admin/instances")
async def admin_instances(ctx: Ctx = Depends(admin), pg: Page = Depends(paging), owner_id: int | None = None,
                          status: str | None = None, strategy_id: str | None = None, paper: bool | None = None):
    stmt = select(Instance).where(Instance.deleted_at.is_(None))
    for col, val in ((Instance.owner_id, owner_id), (Instance.status, status), (Instance.strategy_id, strategy_id),
                     (Instance.paper, paper)):
        if val is not None:
            stmt = stmt.where(col == val)
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(Instance.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    names = dict((await ctx.db.execute(select(User.id, User.username))).all())
    accs = dict((await ctx.db.execute(select(Account.id, Account.name))).all())
    out = []
    for i in rows:
        ext = registry.get_strategy(i.strategy_id)
        out.append(instance_out(i, strategy_name=ext.meta.get("name") if ext else None,
                                account_name=accs.get(i.account_id)) | {"owner_username": names.get(i.owner_id)})
    return paginated(out, total, pg.page, pg.page_size)


@router.post("/api/admin/instances/{instance_id}/force-stop")
async def admin_force_stop(instance_id: int, ctx: Ctx = Depends(admin)):
    inst = await ctx.db.get(Instance, instance_id)
    if inst is None or inst.deleted_at is not None:
        raise ApiError(40403)
    inst = await engine.stop(instance_id, actor=ctx.user, reason="管理员强制停止", force=True)
    return ok({"instance_id": instance_id, "status": inst.status})


@router.get("/api/admin/system-logs")
async def system_logs(ctx: Ctx = Depends(admin), pg: Page = Depends(paging), level: str | None = None,
                      module: str | None = None, instance_id: int | None = None, account_id: int | None = None,
                      keyword: str | None = None, start_time: str | None = None, end_time: str | None = None):
    await engine.flush_logs()
    stmt = select(SystemLog)
    for col, val in ((SystemLog.level, level), (SystemLog.module, module), (SystemLog.instance_id, instance_id),
                     (SystemLog.account_id, account_id)):
        if val is not None:
            stmt = stmt.where(col == val)
    if keyword:
        stmt = stmt.where(SystemLog.message.contains(keyword))
    if start_time:
        stmt = stmt.where(SystemLog.timestamp >= _dt(start_time))
    if end_time:
        stmt = stmt.where(SystemLog.timestamp <= _dt(end_time))
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(SystemLog.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    return paginated([system_log_dict(r) for r in rows], total, pg.page, pg.page_size)


# ---- 22.11 邀请码
def _invite_out(i: InviteCode) -> dict[str, Any]:
    status = "disabled" if not i.enabled else ("expired" if i.expires_at and i.expires_at < datetime.utcnow()
                                               else "exhausted" if i.used_count >= i.max_uses else "active")
    return {"id": i.id, "code": i.code, "max_uses": i.max_uses, "used_count": i.used_count, "role": i.role,
            "enabled": i.enabled, "status": status, "expires_at": iso(i.expires_at), "created_by": i.created_by,
            "created_at": iso(i.created_at)}


@router.get("/api/admin/invites")
async def invites(ctx: Ctx = Depends(admin), pg: Page = Depends(paging)):
    total = await ctx.db.scalar(select(func.count(InviteCode.id))) or 0
    rows = (await ctx.db.execute(select(InviteCode).order_by(InviteCode.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    return paginated([_invite_out(i) for i in rows], total, pg.page, pg.page_size)


class InviteIn(BaseModel):
    max_uses: int = Field(1, ge=1, le=10000)
    expires_in_days: int | None = Field(7, ge=1, le=365)
    role: str = "user"


@router.post("/api/admin/invites")
async def create_invite(body: InviteIn, ctx: Ctx = Depends(admin)):
    if body.role not in {"user", "admin"}:
        raise ApiError(40003, "role 取值 user/admin")
    if body.role == "admin" and ctx.user.role != "super_admin":
        raise ApiError(40302, "只有 super_admin 能生成管理员邀请码")
    code = secrets.token_hex(4).upper()
    inv = InviteCode(code=code, max_uses=body.max_uses, role=body.role, created_by=ctx.user.id,
                     expires_at=datetime.utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None)
    ctx.db.add(inv)
    await ctx.db.commit()
    await write_audit(ctx.db, action="invite.create", target_type="invite", target_id=inv.id,
                      new_value=_invite_out(inv), **ctx.audit_kw())
    return ok(_invite_out(inv), status_code=201)


async def _toggle_invite(ctx: Ctx, invite_id: int, enabled: bool):
    inv = await ctx.db.get(InviteCode, invite_id)
    if inv is None:
        raise ApiError(40401)
    inv.enabled = enabled
    await ctx.db.commit()
    await write_audit(ctx.db, action="invite.enable" if enabled else "invite.disable", target_type="invite",
                      target_id=inv.id, **ctx.audit_kw())
    return ok(_invite_out(inv))


@router.post("/api/admin/invites/{invite_id}/disable")
async def disable_invite(invite_id: int, ctx: Ctx = Depends(admin)):
    return await _toggle_invite(ctx, invite_id, False)


@router.post("/api/admin/invites/{invite_id}/enable")
async def enable_invite(invite_id: int, ctx: Ctx = Depends(admin)):
    return await _toggle_invite(ctx, invite_id, True)


@router.get("/api/admin/invites/{invite_id}/usages")
async def invite_usages(invite_id: int, ctx: Ctx = Depends(admin)):
    rows = (await ctx.db.execute(select(InviteUsage).where(InviteUsage.invite_id == invite_id)
                                 .order_by(InviteUsage.id.desc()))).scalars().all()
    return ok({"items": [{"id": r.id, "user_id": r.user_id, "username": r.username, "used_at": iso(r.used_at)} for r in rows]})


# ---- 22.12 备份与恢复
def _backup_out(b: Backup) -> dict[str, Any]:
    return {"id": b.id, "filename": b.filename, "size": b.size, "status": b.status, "created_by": b.created_by,
            "created_at": iso(b.created_at)}


@router.get("/api/super/backups")
async def backups(ctx: Ctx = Depends(super_admin)):
    rows = (await ctx.db.execute(select(Backup).order_by(Backup.id.desc()))).scalars().all()
    online = _sqlite_path() is not None
    items = [_backup_out(b) | {"restorable": online and b.filename.endswith(".sqlite")} for b in rows]
    # online_restore=false 时（PostgreSQL），前端隐藏"恢复"按钮并提示用 scripts/restore.sh
    return ok({"items": items, "online_restore": online})


def _sqlite_path() -> Path | None:
    url = settings.database_url
    if not url.startswith("sqlite"):
        return None
    path = url.split("///", 1)[1]
    p = Path(path)
    return p if p.is_absolute() else (Path.cwd() / p).resolve()


@router.post("/api/super/backups")
async def create_backup(ctx: Ctx = Depends(super_admin)):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    src = _sqlite_path()
    if src is not None:
        dest = BACKUP_DIR / f"bamy-{stamp}.sqlite"
        # 在线一致性备份：sqlite3 backup API
        with sqlite3.connect(src) as s_conn, sqlite3.connect(dest) as d_conn:
            s_conn.backup(d_conn)
    else:
        import asyncio
        import os

        dest = BACKUP_DIR / f"bamy-{stamp}.sql"
        dsn = settings.database_url.replace("postgresql+asyncpg", "postgresql")
        try:
            proc = await asyncio.create_subprocess_exec("pg_dump", "--no-owner", "-f", str(dest), dsn,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                        env=os.environ.copy())
        except FileNotFoundError as exc:
            raise ApiError(50003, "服务器未安装 pg_dump（postgresql-client），无法在线备份；请用 scripts/backup.sh") from exc
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise ApiError(50003, f"pg_dump 失败：{err.decode()[:300]}")
    b = Backup(filename=dest.name, size=dest.stat().st_size, created_by=ctx.user.id)
    ctx.db.add(b)
    await ctx.db.commit()
    await write_audit(ctx.db, action="backup.create", target_type="backup", target_id=b.id,
                      new_value=_backup_out(b), **ctx.audit_kw())
    return ok(_backup_out(b), status_code=201)


@router.get("/api/super/backups/{backup_id}/download")
async def download_backup(backup_id: int, ctx: Ctx = Depends(super_admin)):
    b = await ctx.db.get(Backup, backup_id)
    if b is None or not (BACKUP_DIR / b.filename).exists():
        raise ApiError(40401)
    return FileResponse(BACKUP_DIR / b.filename, filename=b.filename, media_type="application/octet-stream")


class RestoreIn(BaseModel):
    confirm: str


@router.post("/api/super/backups/{backup_id}/restore")
async def restore_backup(backup_id: int, body: RestoreIn, ctx: Ctx = Depends(super_admin)):
    if body.confirm != "RESTORE":
        raise ApiError(40004, '请传 confirm: "RESTORE" 确认恢复')
    b = await ctx.db.get(Backup, backup_id)
    path = BACKUP_DIR / b.filename if b else None
    if b is None or not path.exists():
        raise ApiError(40401)
    # 先判断能不能在线恢复，再动系统状态：PG 不支持在线恢复，此时不能先进只读、先停实例
    src = _sqlite_path()
    if src is None or not path.name.endswith(".sqlite"):
        raise ApiError(50004, "PostgreSQL 部署请在停机状态下用 scripts/restore.sh 恢复（在线恢复只支持 SQLite）")
    await write_audit(ctx.db, action="backup.restore", target_type="backup", target_id=b.id, **ctx.audit_kw())
    # 进入只读模式并停止所有实例；恢复完成后需要重启进程
    READONLY["on"] = True
    for iid in list(engine.running):
        await engine.stop(iid, actor=ctx.user, reason="备份恢复")
    await ctx.db.close()
    from bamy.db.session import engine as db_engine

    await db_engine.dispose()
    with sqlite3.connect(path) as s_conn, sqlite3.connect(src) as d_conn:
        s_conn.backup(d_conn)
    return ok({"restored": b.filename, "restart_required": True})


# ---- 通知中心
@router.get("/api/notifications")
async def notifications(ctx: Ctx = Depends(current), pg: Page = Depends(paging), unread: bool | None = None):
    stmt = select(Notification).where(Notification.owner_id == ctx.user.id) if not ctx.is_admin else \
        select(Notification).where((Notification.owner_id == ctx.user.id) | (Notification.owner_id.is_(None)))
    if unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(Notification.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    return paginated([{"id": n.id, "level": n.level, "title": n.title, "body": n.body, "source": n.source,
                       "read": n.read_at is not None, "created_at": iso(n.created_at)} for n in rows],
                     total, pg.page, pg.page_size)


@router.post("/api/notifications/read")
async def mark_read(request: Request, ctx: Ctx = Depends(current)):
    try:
        ids = (await request.json()).get("ids")
    except Exception:  # noqa: BLE001
        ids = None
    stmt = update(Notification).where(Notification.owner_id == ctx.user.id, Notification.read_at.is_(None))
    if ids:
        stmt = stmt.where(Notification.id.in_(ids))
    await ctx.db.execute(stmt.values(read_at=datetime.utcnow()))
    await ctx.db.commit()
    return ok(None)


def _dt(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None)
