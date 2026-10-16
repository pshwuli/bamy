"""账号接口（《接口契约》第五节）。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from bamy.api.deps import Ctx, Page, current, iso, own_account, paging
from bamy.api.serializers import account_out, order_out
from bamy.core.crypto import decrypt, encrypt
from bamy.core.errors import ApiError
from bamy.core.response import ok, paginated
from bamy.db.models import Account, AccountHealth, Instance, Order
from bamy.engine.adapters import BinanceSpotAdapter, ExchangeError
from bamy.engine.client_pool import pool
from bamy.engine.pluginhub import plugin_hub
from bamy.engine.runtime import engine
from bamy.services import system_config
from bamy.services.audit import write_audit

router = APIRouter(prefix="/api/accounts")

KEY_RE = re.compile(r"^[A-Za-z0-9]{16,128}$")
SYSTEM_MAX = 1000
BATCH_MAX = 50


class AccountIn(BaseModel):
    name: str = Field(..., min_length=4, max_length=32)
    api_key: str
    api_secret: str
    env: str = "mainnet"
    market_type: str = "spot"
    readonly: bool = False
    remark: str | None = None
    ip_whitelist: list[str] | None = None


async def _verify(api_key: str, api_secret: str, env: str, readonly: bool) -> dict[str, Any]:
    """1 格式 → 2 连通 → 3 凭证 → 4 权限 → 5 环境 → 6 交易类型（《账号模块设计》4.2）。"""
    if not KEY_RE.match(api_key) or not KEY_RE.match(api_secret):
        raise ApiError(42210, "API Key/Secret 格式不符合币安格式")
    client = BinanceSpotAdapter(api_key, api_secret, env=env, readonly=readonly)
    try:
        return await client.verify()
    except ExchangeError as exc:
        raise ApiError(exc.code if exc.code in {42210, 42211, 50203} else 42213, str(exc)) from exc
    finally:
        await client.close()


async def _check_new(ctx: Ctx, body: AccountIn, *, verify: bool = True) -> dict[str, Any]:
    if body.env not in {"mainnet", "testnet"}:
        raise ApiError(40003, "env 必须是 mainnet 或 testnet")
    if body.market_type != "spot":
        raise ApiError(42224, "只支持现货（market_type=spot）")
    limit = int(system_config.get("account.max_per_user", 20))
    count = await ctx.db.scalar(select(func.count(Account.id)).where(Account.owner_id == ctx.user.id,
                                                                     Account.deleted_at.is_(None))) or 0
    if count >= limit:
        raise ApiError(40908, f"账号数已达上限 {limit}")
    total = await ctx.db.scalar(select(func.count(Account.id)).where(Account.deleted_at.is_(None))) or 0
    if total >= SYSTEM_MAX:
        raise ApiError(40908, "系统账号总数已达上限")
    if await ctx.db.scalar(select(Account.id).where(Account.owner_id == ctx.user.id, Account.name == body.name,
                                                     Account.deleted_at.is_(None))):
        raise ApiError(40901, "账号名已存在")
    for row in (await ctx.db.execute(select(Account).where(Account.owner_id == ctx.user.id,
                                                           Account.deleted_at.is_(None)))).scalars():
        if decrypt(row.api_key_enc) == body.api_key:
            raise ApiError(42214)
    return await _verify(body.api_key, body.api_secret, body.env, body.readonly) if verify else {}


async def _count_instances(ctx: Ctx, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    rows = await ctx.db.execute(select(Instance.account_id, func.count(Instance.id))
                                .where(Instance.account_id.in_(ids), Instance.deleted_at.is_(None))
                                .group_by(Instance.account_id))
    return dict(rows.all())


@router.get("")
async def list_accounts(ctx: Ctx = Depends(current), pg: Page = Depends(paging), status: str | None = None,
                        env: str | None = None, market_type: str | None = None, keyword: str | None = None):
    stmt = select(Account).where(Account.owner_id == ctx.user.id, Account.deleted_at.is_(None))
    if status:
        stmt = stmt.where(Account.status == status)
    if env:
        stmt = stmt.where(Account.env == env)
    if market_type:
        stmt = stmt.where(Account.market_type == market_type)
    if keyword:
        stmt = stmt.where(Account.name.contains(keyword))
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(Account.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    counts = await _count_instances(ctx, [r.id for r in rows])
    return paginated([account_out(r, counts.get(r.id, 0)) for r in rows], total, pg.page, pg.page_size)


@router.get("/{account_id}")
async def get_account(account_id: int, ctx: Ctx = Depends(current)):
    acc = await own_account(ctx.db, ctx.user, account_id)
    counts = await _count_instances(ctx, [acc.id])
    return ok(account_out(acc, counts.get(acc.id, 0)))


@router.post("")
async def create_account(body: AccountIn, ctx: Ctx = Depends(current)):
    info = await _check_new(ctx, body)
    acc = Account(owner_id=ctx.user.id, name=body.name, api_key_enc=encrypt(body.api_key),
                  api_secret_enc=encrypt(body.api_secret), env=body.env, market_type="spot",
                  readonly=body.readonly or not info.get("can_trade", True), status="online", remark=body.remark,
                  ip_whitelist=json.dumps(body.ip_whitelist) if body.ip_whitelist else None,
                  last_verified_at=datetime.utcnow(), last_active_at=datetime.utcnow())
    ctx.db.add(acc)
    await ctx.db.commit()
    plugin_hub.remember_account(acc.id, ctx.user.id)
    await write_audit(ctx.db, action="account.create", target_type="account", target_id=acc.id,
                      new_value={"name": acc.name, "env": acc.env, "readonly": acc.readonly}, **ctx.audit_kw())
    return ok({"id": acc.id, "name": acc.name, "status": acc.status, "readonly": acc.readonly,
               "can_withdraw": info.get("can_withdraw")}, status_code=201)


class BatchIn(BaseModel):
    accounts: list[AccountIn]


@router.post("/batch")
async def batch_import(body: BatchIn, ctx: Ctx = Depends(current)):
    if len(body.accounts) > BATCH_MAX:
        raise ApiError(40003, f"单次最多导入 {BATCH_MAX} 个")
    success, errors = 0, []
    for idx, item in enumerate(body.accounts, start=1):
        try:
            info = await _check_new(ctx, item)
        except ApiError as exc:
            errors.append({"index": idx, "name": item.name, "code": exc.code, "reason": exc.message})
            continue
        acc = Account(owner_id=ctx.user.id, name=item.name, api_key_enc=encrypt(item.api_key),
                      api_secret_enc=encrypt(item.api_secret), env=item.env, market_type="spot",
                      readonly=item.readonly or not info.get("can_trade", True), status="online", remark=item.remark,
                      last_verified_at=datetime.utcnow())
        ctx.db.add(acc)
        await ctx.db.commit()
        plugin_hub.remember_account(acc.id, ctx.user.id)
        success += 1
        await write_audit(ctx.db, action="account.create", target_type="account", target_id=acc.id,
                          new_value={"name": acc.name, "batch": True}, **ctx.audit_kw())
    return ok({"success": success, "failed": len(errors), "errors": errors})


class AccountPatch(BaseModel):
    name: str | None = Field(None, min_length=4, max_length=32)
    readonly: bool | None = None
    remark: str | None = None
    ip_whitelist: list[str] | None = None


@router.patch("/{account_id}")
async def update_account(account_id: int, body: AccountPatch, ctx: Ctx = Depends(current)):
    acc = await own_account(ctx.db, ctx.user, account_id)
    old = account_out(acc)
    if body.name and body.name != acc.name:
        if await ctx.db.scalar(select(Account.id).where(Account.owner_id == ctx.user.id, Account.name == body.name,
                                                         Account.deleted_at.is_(None))):
            raise ApiError(40901, "账号名已存在")
        acc.name = body.name
    if body.readonly is not None:
        if body.readonly and await _has_running(ctx, acc.id):
            raise ApiError(40905, "账号下有运行中的实盘实例，不能改为只读")
        acc.readonly = body.readonly
    if body.remark is not None:
        acc.remark = body.remark
    if body.ip_whitelist is not None:
        acc.ip_whitelist = json.dumps(body.ip_whitelist)
    await ctx.db.commit()
    await pool.drop(acc.id)
    await write_audit(ctx.db, action="account.update", target_type="account", target_id=acc.id, old_value=old,
                      new_value=body.model_dump(exclude_none=True), **ctx.audit_kw())
    return ok(account_out(acc))


async def _has_running(ctx: Ctx, account_id: int) -> bool:
    return bool(await ctx.db.scalar(select(func.count(Instance.id)).where(
        Instance.account_id == account_id, Instance.deleted_at.is_(None),
        Instance.status.in_(("running", "paused", "starting", "pausing", "resuming", "stopping")))))


class CredIn(BaseModel):
    api_key: str
    api_secret: str


@router.post("/{account_id}/credentials")
async def update_credentials(account_id: int, body: CredIn, ctx: Ctx = Depends(current)):
    acc = await own_account(ctx.db, ctx.user, account_id)
    info = await _verify(body.api_key, body.api_secret, acc.env, acc.readonly)
    acc.api_key_enc, acc.api_secret_enc = encrypt(body.api_key), encrypt(body.api_secret)
    acc.status, acc.last_verified_at = "online", datetime.utcnow()
    if not info.get("can_trade", True):
        acc.readonly = True
    await ctx.db.commit()
    await pool.drop(acc.id)
    # 账号恢复后必须与币安对账
    await engine.reconcile_account(acc.id, reason="credentials")
    await write_audit(ctx.db, action="account.credentials", target_type="account", target_id=acc.id, **ctx.audit_kw())
    return ok(account_out(acc))


@router.post("/{account_id}/verify")
async def verify_account(account_id: int, ctx: Ctx = Depends(current)):
    acc = await own_account(ctx.db, ctx.user, account_id)
    started = datetime.utcnow()
    try:
        info = await _verify(decrypt(acc.api_key_enc), decrypt(acc.api_secret_enc), acc.env, acc.readonly)
    except ApiError as exc:
        ctx.db.add(AccountHealth(account_id=acc.id, success=False, error_code=str(exc.code), error_message=exc.message))
        if exc.code in {42210, 42211}:
            await ctx.db.commit()
            await engine.account_invalid(acc.id, exc.message)
        else:
            acc.status = "error"
            await ctx.db.commit()
        await write_audit(ctx.db, action="account.verify", target_type="account", target_id=acc.id, result="failed",
                          fail_reason=exc.message, **ctx.audit_kw())
        return ok({"ok": False, "code": exc.code, "reason": exc.message, "status": acc.status})
    was = acc.status
    acc.status, acc.last_verified_at, acc.last_active_at = "online", datetime.utcnow(), datetime.utcnow()
    ctx.db.add(AccountHealth(account_id=acc.id, success=True,
                             latency_ms=int((datetime.utcnow() - started).total_seconds() * 1000)))
    await ctx.db.commit()
    if was != "online":
        await engine.reconcile_account(acc.id, reason="verify")
    await write_audit(ctx.db, action="account.verify", target_type="account", target_id=acc.id, **ctx.audit_kw())
    return ok({"ok": True, "status": acc.status, **info})


@router.get("/{account_id}/balance")
async def balance(account_id: int, ctx: Ctx = Depends(current)):
    acc = await own_account(ctx.db, ctx.user, account_id)
    try:
        client = await pool.get(acc)
        items = await client.fetch_balance()
    except Exception as exc:  # noqa: BLE001
        raise ApiError(50201, f"获取余额失败：{exc}") from exc
    assets = {b.asset: b.total for b in items}
    total = assets.get("USDT", 0.0)
    for b in items:
        if b.asset in {"USDT"}:
            continue
        try:
            total += b.total * float((await client.fetch_ticker(f"{b.asset}/USDT"))["price"])
        except Exception:  # noqa: BLE001 - 无 USDT 交易对的资产不计入
            continue
    return ok({"account_id": acc.id, "total_usdt": round(total, 8), "assets": assets,
               "details": [b.to_dict() for b in items]})


@router.get("/{account_id}/positions")
async def positions(account_id: int, ctx: Ctx = Depends(current)):
    acc = await own_account(ctx.db, ctx.user, account_id)
    insts = (await ctx.db.execute(select(Instance).where(Instance.account_id == acc.id,
                                                         Instance.deleted_at.is_(None)))).scalars().all()
    items = []
    for inst in insts:
        for pos in await engine.framework_positions(inst.id):
            items.append(pos | {"instance_id": inst.id, "instance_name": inst.name})
    return ok({"items": items})


@router.get("/{account_id}/orders")
async def account_orders(account_id: int, ctx: Ctx = Depends(current), pg: Page = Depends(paging),
                         status: str | None = None):
    acc = await own_account(ctx.db, ctx.user, account_id)
    stmt = select(Order).where(Order.account_id == acc.id)
    if status:
        stmt = stmt.where(Order.status == status)
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(Order.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    return paginated([order_out(o) for o in rows], total, pg.page, pg.page_size)


@router.delete("/{account_id}")
async def delete_account(account_id: int, ctx: Ctx = Depends(current)):
    acc = await own_account(ctx.db, ctx.user, account_id)
    in_use = await ctx.db.scalar(select(func.count(Instance.id)).where(Instance.account_id == acc.id,
                                                                       Instance.deleted_at.is_(None))) or 0
    if in_use:
        raise ApiError(40905, f"账号正被 {in_use} 个实例使用")
    acc.status, acc.deleted_at = "deleted", datetime.utcnow()
    await ctx.db.commit()
    await pool.drop(acc.id)
    await write_audit(ctx.db, action="account.delete", target_type="account", target_id=acc.id, **ctx.audit_kw())
    return ok(None)


@router.get("/{account_id}/health")
async def health(account_id: int, ctx: Ctx = Depends(current)):
    acc = await own_account(ctx.db, ctx.user, account_id)
    since = datetime.utcnow() - timedelta(hours=24)
    rows = (await ctx.db.execute(select(AccountHealth).where(AccountHealth.account_id == acc.id,
                                                             AccountHealth.checked_at >= since)
                                 .order_by(AccountHealth.checked_at.desc()))).scalars().all()
    ok_rows = [r for r in rows if r.success]
    lat = [r.latency_ms for r in ok_rows if r.latency_ms is not None]
    return ok({"online_rate": round(len(ok_rows) / len(rows), 4) if rows else None,
               "avg_latency_ms": int(sum(lat) / len(lat)) if lat else None,
               "last_check_at": iso(rows[0].checked_at) if rows else None,
               "status": acc.status,
               "recent_checks": [{"checked_at": iso(r.checked_at), "success": r.success, "latency_ms": r.latency_ms,
                                  "error_code": r.error_code, "error_message": r.error_message} for r in rows[:50]]})
