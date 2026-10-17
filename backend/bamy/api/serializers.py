"""对象 → 接口响应字段（《接口契约》各节的响应示例）。"""
from __future__ import annotations

from typing import Any

from bamy.api.deps import iso, jload, num
from bamy.core.crypto import decrypt, mask
from bamy.db.models import Account, Instance, Order, PluginBinding, User


def user_out(u: User) -> dict[str, Any]:
    return {"id": u.id, "username": u.username, "email": u.email, "nickname": u.nickname, "role": u.role,
            "status": u.status, "two_factor_enabled": bool(u.two_factor_enabled),
            "last_login_at": iso(u.last_login_at), "last_login_ip": u.last_login_ip,
            "created_at": iso(u.created_at)}


def account_out(a: Account, instance_count: int = 0) -> dict[str, Any]:
    try:
        key = mask(decrypt(a.api_key_enc))
    except Exception:  # noqa: BLE001
        key = "****"
    return {"id": a.id, "owner_id": a.owner_id, "name": a.name, "api_key_masked": key, "exchange": a.exchange,
            "env": a.env, "market_type": a.market_type, "position_mode": a.position_mode,
            "readonly": bool(a.readonly), "status": a.status,
            "remark": a.remark, "ip_whitelist": jload(a.ip_whitelist, []),
            "last_verified_at": iso(a.last_verified_at), "last_active_at": iso(a.last_active_at),
            "instance_count": instance_count, "created_at": iso(a.created_at), "updated_at": iso(a.updated_at)}


def instance_out(i: Instance, *, strategy_name: str | None = None, account_name: str | None = None,
                 equity: float | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {"id": i.id, "owner_id": i.owner_id, "name": i.name, "account_id": i.account_id,
           "account_name": account_name or ("模拟盘" if i.paper else None),
           "strategy_id": i.strategy_id, "strategy_name": strategy_name, "strategy_version": i.strategy_version,
           "symbol": i.symbol, "market_type": i.market_type, "paper": bool(i.paper),
           "paper_balance": num(i.paper_balance), "paper_config": jload(i.paper_config, {}),
           "params": _mask_params(i), "enabled": bool(i.enabled), "status": i.status,
           "stop_action": i.stop_action, "last_started_at": iso(i.last_started_at),
           "last_stopped_at": iso(i.last_stopped_at), "last_error": i.last_error,
           "consecutive_errors": i.consecutive_errors, "created_at": iso(i.created_at),
           "updated_at": iso(i.updated_at), "equity": equity}
    if extra:
        out.update(extra)
    return out


def order_out(o: Order) -> dict[str, Any]:
    return {"id": o.id, "client_order_id": o.client_order_id, "exchange_order_id": o.exchange_order_id,
            "instance_id": o.instance_id, "owner_id": o.owner_id, "account_id": o.account_id, "symbol": o.symbol,
            "market_type": o.market_type, "side": o.side, "position_side": o.position_side, "type": o.type,
            "amount": num(o.amount), "price": num(o.price), "stop_price": num(o.stop_price),
            "filled_amount": num(o.filled_amount), "filled_price": num(o.filled_price), "fee": num(o.fee),
            "fee_currency": o.fee_currency, "status": o.status, "source": o.source, "is_guard": bool(o.is_guard),
            "guard_type": o.guard_type, "reason": o.reason, "intercepted_by": o.intercepted_by,
            "intercepted_action": o.intercepted_action, "created_at": iso(o.created_at),
            "submitted_at": iso(o.submitted_at), "filled_at": iso(o.filled_at), "canceled_at": iso(o.canceled_at),
            "updated_at": iso(o.updated_at)}


def binding_out(b: PluginBinding, *, plugin_name: str | None = None, schema: list[dict[str, Any]] | None = None,
                health: dict[str, Any] | None = None, target_name: str | None = None) -> dict[str, Any]:
    settings = jload(b.settings, {}) or {}
    for p in schema or []:
        if (p.get("type") == "secret" or p.get("secret")) and p["key"] in settings:
            settings[p["key"]] = "******"
    auto_disabled = bool(b.enabled and b.status == "disabled")
    return {"id": b.id, "plugin_id": b.plugin_id, "plugin_name": plugin_name, "plugin_version": b.plugin_version,
            "scope": b.scope, "scope_id": b.scope_id, "target_name": target_name, "owner_id": b.owner_id,
            "enabled": bool(b.enabled), "status": b.status, "auto_disabled": auto_disabled, "settings": settings,
            "priority": b.priority, "last_error": b.last_error, "health": health,
            "created_at": iso(b.created_at), "updated_at": iso(b.updated_at)}


def _mask_params(i: Instance) -> dict[str, Any]:
    from bamy.extensions.registry import registry
    from bamy.extensions.schema import mask_secrets

    ext = registry.get_strategy(i.strategy_id)
    return mask_secrets(ext.params_schema if ext else [], jload(i.params, {}))
