"""策略、插件与插件绑定接口（《接口契约》第六节，22.8–22.10）。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from bamy.api.deps import Ctx, Page, admin, current, iso, paging, super_admin
from bamy.api.serializers import binding_out
from bamy.core.crypto import encrypt
from bamy.core.errors import ApiError
from bamy.core.response import ok, paginated
from bamy.db.models import Account, Instance, Plugin, PluginBinding, PluginHealth, Strategy, User
from bamy.engine.pluginhub import plugin_hub
from bamy.extensions.registry import Extension, registry
from bamy.extensions.schema import SchemaError, validate
from bamy.services import system_config
from bamy.services.audit import write_audit

router = APIRouter()


# ======================================================================== 同步元表
async def sync_extension_tables(db) -> None:
    """把内存中的扩展注册表写入 strategies / plugins 元表。"""
    for row in registry.strategy_rows():
        obj = await db.get(Strategy, row["id"])
        data = {k: v for k, v in row.items() if hasattr(Strategy, k) and k != "kind"}
        data["loaded"] = bool(data["loaded"])
        if obj is None:
            db.add(Strategy(kind="strategy", **data))
        else:
            for k, v in data.items():
                setattr(obj, k, v)
    for row in registry.plugin_rows():
        obj = await db.get(Plugin, row["id"])
        data = {k: v for k, v in row.items() if hasattr(Plugin, k) and k != "kind"}
        data["loaded"], data["allow_multiple"] = bool(data["loaded"]), bool(data["allow_multiple"])
        if obj is None:
            db.add(Plugin(kind="plugin", **data))
        else:
            uninstalled = obj.uninstalled
            for k, v in data.items():
                setattr(obj, k, v)
            obj.uninstalled = uninstalled
    await db.commit()
    # 消失或加载失败的插件：其全部绑定置为 invalid；恢复后置回 enabled
    ok_ids = {pid for pid, e in registry.plugins.items() if e.loaded}
    uninstalled = set((await db.execute(select(Plugin.id).where(Plugin.uninstalled.is_(True)))).scalars().all())
    rows = (await db.execute(select(PluginBinding).where(PluginBinding.deleted_at.is_(None)))).scalars().all()
    for b in rows:
        if b.plugin_id not in ok_ids or b.plugin_id in uninstalled:
            if b.status != "invalid":
                b.status = "invalid"
                b.last_error = "插件未加载、加载失败或已卸载"
        elif b.status == "invalid":
            b.status = "enabled"
            b.last_error = None
    await db.commit()


# ======================================================================== 输出
def _strategy_out(ext: Extension, detail: bool = False) -> dict[str, Any]:
    m = ext.meta
    out = {"id": ext.id, "kind": "strategy", "name": m.get("name") or ext.id, "version": m.get("version"),
           "author": m.get("author"), "description": m.get("description"), "tags": m.get("tags") or [],
           "capabilities": ext.capabilities, "params_count": len(ext.params_schema), "loaded": ext.loaded,
           "load_status": "loaded" if ext.loaded else "failed", "load_error": ext.error,
           "warnings": m.get("_warnings") or [], "icon": (m.get("ui") or {}).get("icon"),
           # 适用场景：资金规模、行情、交易对（策略包在 strategy.yaml 的 suitability 中声明）
           "suitability": m.get("suitability") or None, "backtest": m.get("backtest") or None}
    if detail:
        out |= {"params_schema": ext.params_schema, "runtime": m.get("runtime") or {},
                "dependencies": m.get("dependencies") or {}, "homepage": m.get("homepage"), "license": m.get("license"),
                "min_bamy_version": m.get("min_bamy_version")}
    return out


def _hooks_out(ext: Extension) -> list[dict[str, Any]]:
    out = []
    for h in ext.hooks:
        if h.get("event"):
            out.append({"type": "event", "event": h["event"], "handler": h.get("handler"),
                        "throttle": h.get("throttle")})
        else:
            out.append({"type": "checkpoint", "checkpoint": h["checkpoint"], "handler": h.get("handler"),
                        "fail_mode": h.get("fail_mode", "open"),
                        "timeout_ms": int(h.get("timeout_ms", system_config.get("plugin.intercept_timeout_ms", 200)))})
    return out


def _plugin_out(ext: Extension, user: User, detail: bool = False, uninstalled: bool = False) -> dict[str, Any]:
    m = ext.meta
    is_admin = user.role in {"admin", "super_admin"}
    scopes = ext.scopes
    installable = ext.loaded and not uninstalled and (is_admin or (ext.data_scope != "all" and bool({"user", "account"} & set(scopes))))
    net = ext.network or {}
    hooks = _hooks_out(ext)
    out = {"id": ext.id, "kind": "plugin", "name": m.get("name") or ext.id, "version": m.get("version"),
           "author": m.get("author"), "description": m.get("description"), "tags": m.get("tags") or [],
           "scopes": scopes, "default_scope": m.get("default_scope") or (scopes[0] if scopes else None),
           "allow_multiple": bool(m.get("allow_multiple")), "data_scope": ext.data_scope,
           "priority": int(m.get("priority", 100)), "loaded": ext.loaded and not uninstalled,
           "load_status": "uninstalled" if uninstalled else ("loaded" if ext.loaded else "failed"),
           "load_error": ext.error, "installable": installable, "network_hosts": list(net.get("hosts") or []),
           "has_closed_fail_mode": any(h.get("fail_mode") == "closed" for h in hooks if h["type"] == "checkpoint"),
           "hooks_summary": {"events": [h["event"] for h in hooks if h["type"] == "event"],
                             "checkpoints": [h["checkpoint"] for h in hooks if h["type"] == "checkpoint"]},
           "icon": (m.get("ui") or {}).get("icon")}
    if detail:
        out |= {"settings_schema": ext.settings_schema, "hooks": hooks,
                "network": {"enabled": bool(net), "hosts": list(net.get("hosts") or []),
                            "timeout_ms": net.get("timeout_ms")} if net else {"enabled": False, "hosts": []},
                "dependencies": m.get("dependencies") or {}, "homepage": m.get("homepage"), "license": m.get("license")}
    return out


async def _uninstalled(ctx: Ctx) -> set[str]:
    return set((await ctx.db.execute(select(Plugin.id).where(Plugin.uninstalled.is_(True)))).scalars().all())


# ======================================================================== 6.1–6.4
@router.get("/api/strategies")
async def list_strategies(ctx: Ctx = Depends(current), keyword: str | None = None, tag: str | None = None,
                          capability: str | None = None, loaded: bool | None = None):
    items = []
    for ext in sorted(registry.strategies.values(), key=lambda e: e.id):
        if keyword and keyword.lower() not in f"{ext.id} {ext.meta.get('name')} {ext.meta.get('author')}".lower():
            continue
        if tag and tag not in (ext.meta.get("tags") or []):
            continue
        if capability and not ext.capabilities.get(capability):
            continue
        if loaded is not None and ext.loaded != loaded:
            continue
        items.append(_strategy_out(ext))
    counts = dict((await ctx.db.execute(select(Instance.strategy_id, func.count(Instance.id))
                                        .where(Instance.deleted_at.is_(None)).group_by(Instance.strategy_id))).all())
    for item in items:
        item["instance_count"] = counts.get(item["id"], 0)
    return ok({"items": items, "failed": [{"id": k, "reason": v} for k, v in registry.failed.items()
                                          if k in registry.strategies and not registry.strategies[k].loaded]})


@router.get("/api/strategies/{strategy_id}")
async def get_strategy(strategy_id: str, ctx: Ctx = Depends(current)):
    ext = registry.get_strategy(strategy_id)
    if ext is None or not ext.loaded:
        raise ApiError(40402)
    out = _strategy_out(ext, detail=True)
    if ctx.is_admin:
        rows = (await ctx.db.execute(select(Instance.strategy_version, func.count(Instance.id))
                                     .where(Instance.strategy_id == strategy_id, Instance.deleted_at.is_(None))
                                     .group_by(Instance.strategy_version))).all()
        users = await ctx.db.scalar(select(func.count(func.distinct(Instance.owner_id)))
                                    .where(Instance.strategy_id == strategy_id, Instance.deleted_at.is_(None))) or 0
        out["usage"] = {"version_distribution": dict(rows), "user_count": users,
                        "instance_count": sum(c for _, c in rows)}
    return ok(out)


@router.get("/api/plugins")
async def list_plugins(ctx: Ctx = Depends(current), tags: str | None = None, scope: str | None = None,
                       keyword: str | None = None, network: bool | None = None):
    gone = await _uninstalled(ctx)
    items = []
    for ext in sorted(registry.plugins.values(), key=lambda e: e.id):
        if tags and not set(tags.split(",")) & set(ext.meta.get("tags") or []):
            continue
        if scope and scope not in ext.scopes:
            continue
        if keyword and keyword.lower() not in f"{ext.id} {ext.meta.get('name')}".lower():
            continue
        if network is not None and bool(ext.network) != network:
            continue
        items.append(_plugin_out(ext, ctx.user, uninstalled=ext.id in gone))
    if ctx.is_admin:
        counts = dict((await ctx.db.execute(select(PluginBinding.plugin_id, func.count(PluginBinding.id))
                                            .where(PluginBinding.deleted_at.is_(None))
                                            .group_by(PluginBinding.plugin_id))).all())
        for item in items:
            item["binding_count"] = counts.get(item["id"], 0)
    return ok({"items": items})


@router.get("/api/plugins/{plugin_id}")
async def get_plugin(plugin_id: str, ctx: Ctx = Depends(current)):
    ext = registry.get_plugin(plugin_id)
    if ext is None or not ext.loaded:
        raise ApiError(40406)
    return ok(_plugin_out(ext, ctx.user, detail=True, uninstalled=plugin_id in await _uninstalled(ctx)))


# ======================================================================== 绑定公共逻辑
def _validate_settings(ext: Extension, raw: dict[str, Any], old: dict[str, Any] | None = None) -> str:
    schema = ext.settings_schema
    merged = dict(old or {})
    secrets = {p["key"] for p in schema if p.get("type") == "secret" or p.get("secret")}
    for k, v in (raw or {}).items():
        if k in secrets and v in ("******", None, ""):
            continue  # secret 不传或回传脱敏值则保留原值
        merged[k] = v
    try:
        from bamy.core.crypto import decrypt

        plain = {k: (decrypt(v) if k in secrets and isinstance(v, str) and v.startswith("v1:") else v)
                 for k, v in merged.items()}
        clean = validate(schema, plain, missing_code=42228, invalid_code=42227)
    except SchemaError as exc:
        code = exc.code if exc.code in {42227, 42228} else 42227
        raise ApiError(code, exc.message, errors=[{"field": exc.field, "reason": exc.message}]) from exc
    for k in secrets:
        if isinstance(clean.get(k), str) and clean[k]:
            clean[k] = encrypt(clean[k])
    return json.dumps(clean, ensure_ascii=False)


async def _binding_health(db, b: PluginBinding) -> dict[str, Any]:
    row = (await db.execute(select(PluginHealth).where(PluginHealth.binding_id == b.id))).scalar_one_or_none()
    if row is None:
        return {"binding_id": b.id, "plugin_id": b.plugin_id, "status": b.status,
                "auto_disabled": bool(b.enabled and b.status == "disabled"), "event_calls": 0, "checkpoint_calls": 0,
                "error_count": 0, "timeout_count": 0, "consecutive_errors": 0, "avg_latency_ms": None,
                "last_error": None, "since": iso(b.created_at)}
    return {"binding_id": b.id, "plugin_id": b.plugin_id, "status": b.status,
            "auto_disabled": bool(b.enabled and b.status == "disabled"),
            "event_calls": row.event_calls, "checkpoint_calls": row.checkpoint_calls, "error_count": row.error_count,
            "timeout_count": row.timeout_count, "consecutive_errors": row.consecutive_errors,
            "avg_latency_ms": row.avg_cost_ms,
            "last_error": {"message": row.last_error, "at": iso(row.last_error_at)} if row.last_error else None,
            "since": iso(row.created_at)}


async def _target_names(db, bindings: list[PluginBinding]) -> dict[int, str | None]:
    acc_ids = [b.scope_id for b in bindings if b.scope == "account" and b.scope_id]
    user_ids = [b.scope_id for b in bindings if b.scope == "user" and b.scope_id]
    accs = dict((await db.execute(select(Account.id, Account.name).where(Account.id.in_(acc_ids)))).all()) if acc_ids else {}
    users = dict((await db.execute(select(User.id, User.username).where(User.id.in_(user_ids)))).all()) if user_ids else {}
    return {b.id: (accs.get(b.scope_id) if b.scope == "account" else users.get(b.scope_id) if b.scope == "user" else None)
            for b in bindings}


async def _out_many(db, rows: list[PluginBinding], with_health: bool = True) -> list[dict[str, Any]]:
    names = await _target_names(db, rows)
    out = []
    for b in rows:
        ext = registry.get_plugin(b.plugin_id)
        health = await _binding_health(db, b) if with_health else None
        out.append(binding_out(b, plugin_name=ext.meta.get("name") if ext else b.plugin_id,
                               schema=ext.settings_schema if ext else [],
                               health={k: health[k] for k in ("event_calls", "checkpoint_calls", "error_count")} if health else None,
                               target_name=names.get(b.id)))
    return out


def _in_my_scope(ctx: Ctx, b: PluginBinding, my_accounts: set[int]) -> bool:
    if b.scope == "user":
        return b.scope_id == ctx.user.id
    if b.scope == "account":
        return b.scope_id in my_accounts
    return False


async def _my_accounts(ctx: Ctx) -> set[int]:
    return set((await ctx.db.execute(select(Account.id).where(Account.owner_id == ctx.user.id,
                                                              Account.deleted_at.is_(None)))).scalars().all())


async def _get_binding(ctx: Ctx, binding_id: int, *, admin_mode: bool = False) -> PluginBinding:
    b = await ctx.db.get(PluginBinding, binding_id)
    if b is None or b.deleted_at is not None:
        raise ApiError(40407)
    if admin_mode:
        return b
    if not _in_my_scope(ctx, b, await _my_accounts(ctx)):
        raise ApiError(40407)  # 越权以 404 呈现
    return b


# ======================================================================== 6.5–6.9 用户端
@router.get("/api/plugin-bindings")
async def list_bindings(ctx: Ctx = Depends(current), pg: Page = Depends(paging), scope: str | None = None,
                        scope_id: int | None = None, plugin_id: str | None = None, status: str | None = None):
    accounts = await _my_accounts(ctx)
    stmt = select(PluginBinding).where(PluginBinding.deleted_at.is_(None),
                                       ((PluginBinding.scope == "user") & (PluginBinding.scope_id == ctx.user.id))
                                       | ((PluginBinding.scope == "account") & (PluginBinding.scope_id.in_(accounts or {-1}))))
    if scope:
        stmt = stmt.where(PluginBinding.scope == scope)
    if scope_id is not None:
        stmt = stmt.where(PluginBinding.scope_id == scope_id)
    if plugin_id:
        stmt = stmt.where(PluginBinding.plugin_id == plugin_id)
    if status:
        stmt = stmt.where(PluginBinding.status == status)
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(PluginBinding.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    items = await _out_many(ctx.db, list(rows))
    # 生效提示：同一插件在全局也有绑定时，标注覆盖关系（全局绑定只读展示）
    global_ids = set((await ctx.db.execute(select(PluginBinding.plugin_id).where(
        PluginBinding.scope == "global", PluginBinding.deleted_at.is_(None)))).scalars().all())
    for item in items:
        item["overrides_global"] = item["plugin_id"] in global_ids
    globals_ = (await ctx.db.execute(select(PluginBinding).where(PluginBinding.scope == "global",
                                                                 PluginBinding.deleted_at.is_(None)))).scalars().all()
    return ok({"items": items, "pagination": {"page": pg.page, "page_size": pg.page_size, "total": total,
                                              "total_pages": (total + pg.page_size - 1) // pg.page_size,
                                              "has_next": pg.page * pg.page_size < total, "has_prev": pg.page > 1},
               "global_bindings": [{"plugin_id": g.plugin_id, "status": g.status, "enabled": g.enabled,
                                    "readonly": True} for g in globals_]})


class BindingIn(BaseModel):
    plugin_id: str
    scope: str
    scope_id: int | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    priority: int | None = None
    enabled: bool = True


async def create_binding_core(ctx: Ctx, body: BindingIn, *, admin_mode: bool) -> PluginBinding:
    ext = registry.get_plugin(body.plugin_id)
    if ext is None or not ext.loaded or body.plugin_id in await _uninstalled(ctx):
        raise ApiError(40406)
    if body.scope == "global" and not admin_mode:
        raise ApiError(42281)
    if body.scope not in {"global", "user", "account"} or (body.scope == "global") != (body.scope_id is None):
        raise ApiError(42280)
    if body.scope not in ext.scopes:
        raise ApiError(42229)
    if ext.data_scope == "all" and not (admin_mode or ctx.is_admin):
        raise ApiError(42282)
    if body.scope == "user" and body.scope_id != ctx.user.id and not admin_mode:
        raise ApiError(40303)
    if body.scope == "account":
        acc = await ctx.db.get(Account, body.scope_id)
        if acc is None or acc.deleted_at is not None or (acc.owner_id != ctx.user.id and not admin_mode):
            raise ApiError(40303 if acc is not None and acc.deleted_at is None else 40404)
    if body.scope != "global" and not admin_mode:
        limit = int(system_config.get("plugin.max_per_user", 20))
        count = await ctx.db.scalar(select(func.count(PluginBinding.id)).where(
            PluginBinding.owner_id == ctx.user.id, PluginBinding.deleted_at.is_(None),
            PluginBinding.scope != "global")) or 0
        if count >= limit:
            raise ApiError(40915, f"插件绑定数已达上限 {limit}")
    existing = (await ctx.db.execute(select(PluginBinding).where(
        PluginBinding.plugin_id == body.plugin_id, PluginBinding.scope == body.scope,
        PluginBinding.scope_id.is_(None) if body.scope_id is None else PluginBinding.scope_id == body.scope_id))).scalars().all()
    live_existing = [e for e in existing if e.deleted_at is None]
    if live_existing and not ext.meta.get("allow_multiple"):
        raise ApiError(40913)
    settings_json = _validate_settings(ext, body.settings)
    # 软删除的旧记录占着唯一约束：复用该行
    reuse = next((e for e in existing if e.deleted_at is not None), None) if not ext.meta.get("allow_multiple") else None
    b = reuse or PluginBinding(plugin_id=body.plugin_id, scope=body.scope, scope_id=body.scope_id)
    b.plugin_version, b.owner_id, b.enabled = ext.version, ctx.user.id, body.enabled
    b.settings, b.priority = settings_json, body.priority if body.priority is not None else int(ext.meta.get("priority", 100))
    b.status, b.deleted_at, b.last_error = "enabled", None, None
    if reuse is None:
        ctx.db.add(b)
    await ctx.db.commit()
    await ctx.db.refresh(b)
    await plugin_hub.activate(b)
    await write_audit(ctx.db, action="binding.create", target_type="plugin_binding", target_id=b.id,
                      new_value={"plugin_id": b.plugin_id, "scope": b.scope, "scope_id": b.scope_id,
                                 "priority": b.priority, "enabled": b.enabled}, **ctx.audit_kw())
    return b


@router.post("/api/plugin-bindings")
async def create_binding(body: BindingIn, ctx: Ctx = Depends(current)):
    b = await create_binding_core(ctx, body, admin_mode=False)
    return ok({"id": b.id, "plugin_id": b.plugin_id, "plugin_version": b.plugin_version, "scope": b.scope,
               "scope_id": b.scope_id, "status": b.status}, status_code=201)


class BindingPatch(BaseModel):
    settings: dict[str, Any] | None = None
    priority: int | None = None
    enabled: bool | None = None


async def update_binding_core(ctx: Ctx, b: PluginBinding, body: BindingPatch, *, admin_mode: bool) -> PluginBinding:
    ext = registry.get_plugin(b.plugin_id)
    old = {"enabled": b.enabled, "status": b.status, "priority": b.priority}
    if body.enabled:
        if b.status == "invalid":
            raise ApiError(42283)
        if b.status == "disabled" and not admin_mode:
            raise ApiError(40914)
    settings_changed = False
    if body.settings is not None:
        if ext is None or not ext.loaded:
            raise ApiError(42283)
        b.settings = _validate_settings(ext, body.settings, json.loads(b.settings or "{}"))
        settings_changed = True
    if body.priority is not None:
        b.priority = body.priority
    recovered = False
    if body.enabled is not None:
        if body.enabled and b.status == "disabled":
            recovered = True  # 管理员手动恢复被自动停用的绑定，健康计数归零
            b.status, b.last_error = "enabled", None
            for row in (await ctx.db.execute(select(PluginHealth).where(PluginHealth.binding_id == b.id))).scalars():
                row.consecutive_errors = 0
                row.error_count = 0
                row.timeout_count = 0
        b.enabled = body.enabled
    await ctx.db.commit()
    await ctx.db.refresh(b)
    if body.enabled is False:
        await plugin_hub.deactivate(b.id)
    elif body.enabled or recovered:
        await plugin_hub.activate(b)
    elif settings_changed or body.priority is not None:
        await plugin_hub.settings_changed(b)
    await write_audit(ctx.db, action="binding.recover" if recovered else "binding.update",
                      target_type="plugin_binding", target_id=b.id, old_value=old,
                      new_value={"enabled": b.enabled, "status": b.status, "priority": b.priority,
                                 "settings_changed": settings_changed}, **ctx.audit_kw())
    return b


@router.patch("/api/plugin-bindings/{binding_id}")
async def update_binding(binding_id: int, body: BindingPatch, ctx: Ctx = Depends(current)):
    b = await _get_binding(ctx, binding_id)
    b = await update_binding_core(ctx, b, body, admin_mode=False)
    return ok((await _out_many(ctx.db, [b]))[0])


async def delete_binding_core(ctx: Ctx, b: PluginBinding) -> None:
    b.deleted_at = datetime.utcnow()
    b.enabled = False
    await ctx.db.commit()
    await plugin_hub.deactivate(b.id)
    await write_audit(ctx.db, action="binding.delete", target_type="plugin_binding", target_id=b.id,
                      old_value={"plugin_id": b.plugin_id, "scope": b.scope, "scope_id": b.scope_id}, **ctx.audit_kw())


@router.delete("/api/plugin-bindings/{binding_id}", status_code=204)
async def delete_binding(binding_id: int, ctx: Ctx = Depends(current)):
    b = await _get_binding(ctx, binding_id)
    await delete_binding_core(ctx, b)
    from fastapi import Response

    return Response(status_code=204)


@router.get("/api/plugin-bindings/{binding_id}/health")
async def binding_health(binding_id: int, ctx: Ctx = Depends(current)):
    b = await _get_binding(ctx, binding_id, admin_mode=ctx.is_admin)
    return ok(await _binding_health(ctx.db, b))


# ======================================================================== 6.10–6.14 管理员
@router.post("/api/admin/plugins/reload")
async def reload_extensions(ctx: Ctx = Depends(admin)):
    result = registry.scan()
    before = {b.id for b in (await ctx.db.execute(select(PluginBinding).where(
        PluginBinding.status == "invalid", PluginBinding.deleted_at.is_(None)))).scalars()}
    await sync_extension_tables(ctx.db)
    after = {b.id for b in (await ctx.db.execute(select(PluginBinding).where(
        PluginBinding.status == "invalid", PluginBinding.deleted_at.is_(None)))).scalars()}
    await plugin_hub.reload_all()
    result["plugins"]["invalidated_bindings"] = len(after - before)
    await write_audit(ctx.db, action="extension.reload", target_type="system", new_value=result, **ctx.audit_kw())
    return ok(result)


@router.get("/api/admin/plugin-bindings")
async def admin_global_bindings(ctx: Ctx = Depends(admin), pg: Page = Depends(paging), plugin_id: str | None = None,
                                status: str | None = None):
    stmt = select(PluginBinding).where(PluginBinding.scope == "global", PluginBinding.deleted_at.is_(None))
    if plugin_id:
        stmt = stmt.where(PluginBinding.plugin_id == plugin_id)
    if status:
        stmt = stmt.where(PluginBinding.status == status)
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(PluginBinding.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    return paginated(await _out_many(ctx.db, list(rows)), total, pg.page, pg.page_size)


@router.get("/api/admin/plugin-bindings/all")
async def admin_all_bindings(ctx: Ctx = Depends(admin), pg: Page = Depends(paging), owner_id: int | None = None,
                             plugin_id: str | None = None, scope: str | None = None, status: str | None = None):
    stmt = select(PluginBinding).where(PluginBinding.deleted_at.is_(None))
    for col, val in ((PluginBinding.owner_id, owner_id), (PluginBinding.plugin_id, plugin_id),
                     (PluginBinding.scope, scope), (PluginBinding.status, status)):
        if val is not None:
            stmt = stmt.where(col == val)
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.db.execute(stmt.order_by(PluginBinding.id.desc()).offset(pg.offset).limit(pg.page_size))).scalars().all()
    items = await _out_many(ctx.db, list(rows))
    owners = dict((await ctx.db.execute(select(User.id, User.username))).all())
    for item in items:
        item["owner_username"] = owners.get(item["owner_id"])
    # 作用域分布：按插件聚合，覆盖账号/实例数按生效链去重
    dist: dict[str, dict[str, Any]] = {}
    all_rows = (await ctx.db.execute(select(PluginBinding).where(PluginBinding.deleted_at.is_(None),
                                                                 PluginBinding.status == "enabled",
                                                                 PluginBinding.enabled.is_(True)))).scalars().all()
    accounts = (await ctx.db.execute(select(Account.id, Account.owner_id).where(Account.deleted_at.is_(None)))).all()
    instances = (await ctx.db.execute(select(Instance.id, Instance.owner_id, Instance.account_id)
                                      .where(Instance.deleted_at.is_(None)))).all()
    for b in all_rows:
        d = dist.setdefault(b.plugin_id, {"plugin_id": b.plugin_id, "global": 0, "user": 0, "account": 0})
        d[b.scope] += 1
    for pid, d in dist.items():
        binds = [b for b in all_rows if b.plugin_id == pid]
        has_global = any(b.scope == "global" for b in binds)
        users = {b.scope_id for b in binds if b.scope == "user"}
        accs = {b.scope_id for b in binds if b.scope == "account"}
        d["covered_accounts"] = len([a for a, o in accounts if has_global or o in users or a in accs])
        d["covered_instances"] = len([i for i, o, a in instances if has_global or o in users or (a in accs)])
    return ok({"items": items, "pagination": {"page": pg.page, "page_size": pg.page_size, "total": total,
                                              "total_pages": (total + pg.page_size - 1) // pg.page_size,
                                              "has_next": pg.page * pg.page_size < total, "has_prev": pg.page > 1},
               "distribution": list(dist.values())})


class GlobalBindingIn(BaseModel):
    plugin_id: str
    settings: dict[str, Any] = Field(default_factory=dict)
    priority: int | None = None
    enabled: bool = True


@router.post("/api/admin/plugin-bindings")
async def admin_create_global(body: GlobalBindingIn, ctx: Ctx = Depends(admin)):
    b = await create_binding_core(ctx, BindingIn(plugin_id=body.plugin_id, scope="global", scope_id=None,
                                                 settings=body.settings, priority=body.priority, enabled=body.enabled),
                                  admin_mode=True)
    return ok({"id": b.id, "plugin_id": b.plugin_id, "plugin_version": b.plugin_version, "scope": "global",
               "scope_id": None, "status": b.status}, status_code=201)


@router.patch("/api/admin/plugin-bindings/{binding_id}")
async def admin_update_binding(binding_id: int, body: BindingPatch, ctx: Ctx = Depends(admin)):
    b = await _get_binding(ctx, binding_id, admin_mode=True)
    b = await update_binding_core(ctx, b, body, admin_mode=True)
    return ok((await _out_many(ctx.db, [b]))[0])


@router.delete("/api/admin/plugin-bindings/{binding_id}", status_code=204)
async def admin_delete_binding(binding_id: int, ctx: Ctx = Depends(admin)):
    b = await _get_binding(ctx, binding_id, admin_mode=True)
    await delete_binding_core(ctx, b)
    from fastapi import Response

    return Response(status_code=204)


@router.get("/api/admin/plugins/network-hosts")
async def network_hosts(ctx: Ctx = Depends(admin)):
    counts = dict((await ctx.db.execute(select(PluginBinding.plugin_id, func.count(PluginBinding.id))
                                        .where(PluginBinding.deleted_at.is_(None))
                                        .group_by(PluginBinding.plugin_id))).all())
    hosts: dict[str, dict[str, Any]] = {}
    no_network = []
    for ext in registry.plugins.values():
        net = ext.network
        if not net:
            no_network.append(ext.id)
            continue
        for h in net.get("hosts") or []:
            entry = hosts.setdefault(h, {"host": h, "plugins": [], "bindings": 0})
            entry["plugins"].append(ext.id)
            entry["bindings"] += counts.get(ext.id, 0)
    return ok({"items": sorted(hosts.values(), key=lambda x: x["host"]), "no_network_plugins": no_network})


@router.get("/api/admin/plugins/{plugin_id}/health")
async def plugin_health(plugin_id: str, ctx: Ctx = Depends(admin)):
    if registry.get_plugin(plugin_id) is None:
        raise ApiError(40406)
    binds = (await ctx.db.execute(select(PluginBinding).where(PluginBinding.plugin_id == plugin_id,
                                                              PluginBinding.deleted_at.is_(None)))).scalars().all()
    rows = (await ctx.db.execute(select(PluginHealth).where(PluginHealth.plugin_id == plugin_id))).scalars().all()
    total = next((r for r in rows if r.binding_id is None), None)
    per = {r.binding_id: r for r in rows if r.binding_id is not None}
    by_bind = {b.id: b for b in binds}
    worst = sorted((r for r in per.values() if r.binding_id in by_bind), key=lambda r: -(r.error_count or 0))[:10]
    last = max((r for r in per.values() if r.last_error_at), key=lambda r: r.last_error_at, default=None)
    return ok({
        "plugin_id": plugin_id,
        "bindings": {"total": len(binds), "enabled": sum(1 for b in binds if b.status == "enabled" and b.enabled),
                     "disabled": sum(1 for b in binds if b.status == "disabled" or not b.enabled),
                     "invalid": sum(1 for b in binds if b.status == "invalid"),
                     "auto_disabled": sum(1 for b in binds if b.enabled and b.status == "disabled")},
        "event_calls": total.event_calls if total else 0, "checkpoint_calls": total.checkpoint_calls if total else 0,
        "error_count": total.error_count if total else 0, "timeout_count": total.timeout_count if total else 0,
        "avg_latency_ms": total.avg_cost_ms if total else None,
        "last_error": {"binding_id": last.binding_id, "message": last.last_error, "at": iso(last.last_error_at)} if last else None,
        "worst_bindings": [{"binding_id": r.binding_id, "scope": by_bind[r.binding_id].scope,
                            "scope_id": by_bind[r.binding_id].scope_id, "error_count": r.error_count} for r in worst],
    })


@router.delete("/api/admin/plugins/{plugin_id}")
async def uninstall_plugin(plugin_id: str, ctx: Ctx = Depends(super_admin)):
    row = await ctx.db.get(Plugin, plugin_id)
    if row is None:
        raise ApiError(40406)
    row.uninstalled = True
    binds = (await ctx.db.execute(select(PluginBinding).where(PluginBinding.plugin_id == plugin_id,
                                                              PluginBinding.deleted_at.is_(None)))).scalars().all()
    for b in binds:
        b.status = "invalid"
        b.last_error = "插件已卸载"
    await ctx.db.commit()
    for b in binds:
        await plugin_hub.deactivate(b.id)
    await write_audit(ctx.db, action="plugin.uninstall", target_type="plugin", new_value={"plugin_id": plugin_id,
                                                                                         "invalidated": len(binds)},
                      **ctx.audit_kw())
    return ok({"plugin_id": plugin_id, "invalidated_bindings": len(binds)})


@router.post("/api/admin/plugins/{plugin_id}/install")
async def reinstall_plugin(plugin_id: str, ctx: Ctx = Depends(super_admin)):
    """卸载的反操作：解除卸载标记，绑定在下次重新扫描时恢复。"""
    row = await ctx.db.get(Plugin, plugin_id)
    if row is None:
        raise ApiError(40406)
    row.uninstalled = False
    await ctx.db.commit()
    await sync_extension_tables(ctx.db)
    await plugin_hub.reload_all()
    return ok({"plugin_id": plugin_id})
