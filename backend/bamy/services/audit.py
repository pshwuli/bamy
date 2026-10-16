"""审计：谁、什么时候、对什么、做了什么、结果如何（《用户与权限设计》与《数据模型》4.19）。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bamy.db.models import AuditLog, SystemLog

AUDIT_ACTIONS = {
    "user.register", "user.login", "user.logout", "user.password_change", "user.reset_password",
    "user.disable", "user.enable", "user.role_change", "user.delete", "user.2fa_enable",
    "user.2fa_disable", "user.update",
    "account.create", "account.update", "account.credentials", "account.verify", "account.delete",
    "account.disable", "account.enable", "account.invalid",
    "strategy.reload", "plugin.reload", "plugin.uninstall",
    "binding.create", "binding.update", "binding.delete", "binding.auto_disable", "binding.recover",
    "instance.create", "instance.update", "instance.start", "instance.stop", "instance.pause", "instance.resume",
    "instance.change_strategy", "instance.delete", "instance.auto_stopped", "instance.reset_paper",
    "order.submit", "order.cancel", "order.rejected", "order.intercepted",
    "position.close", "position.force_close",
    "risk.rule_create", "risk.rule_update", "risk.rule_delete",
    "risk.circuit_break", "risk.circuit_recover",
    "reconcile.trigger", "reconcile.resolve",
    "config.update", "extension.reload", "invite.create", "invite.disable", "invite.enable",
    "share.create", "share.revoke", "backup.create", "backup.restore", "api_token.create",
    "api_token.revoke", "session.revoke",
}
SOURCES = {"strategy", "manual", "guard", "system", "admin", "api"}


async def write_audit(
    db: AsyncSession,
    *,
    action: str,
    actor_id: int | None = None,
    actor_role: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    old_value: Any = None,
    new_value: Any = None,
    result: str = "success",
    fail_reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    source: str = "manual",
    commit: bool = True,
) -> AuditLog:
    row = AuditLog(
        actor_id=actor_id, actor_role=actor_role, action=action,
        target_type=target_type, target_id=target_id,
        old_value=json.dumps(old_value, ensure_ascii=False, default=str) if old_value is not None else None,
        new_value=json.dumps(new_value, ensure_ascii=False, default=str) if new_value is not None else None,
        result=result, fail_reason=fail_reason, ip=ip, user_agent=user_agent,
        request_id=request_id, source=source if source in SOURCES else "manual",
    )
    db.add(row)
    if commit:
        await db.commit()
    return row


def audit_dict(row: AuditLog) -> dict[str, Any]:
    def _load(v: str | None) -> Any:
        if not v:
            return None
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return v

    return {
        "id": row.id,
        "timestamp": row.timestamp.isoformat() + "Z",
        "actor_id": row.actor_id,
        "actor_role": row.actor_role,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "old_value": _load(row.old_value),
        "new_value": _load(row.new_value),
        "result": row.result,
        "fail_reason": row.fail_reason,
        "ip": row.ip,
        "user_agent": row.user_agent,
        "request_id": row.request_id,
        "source": row.source,
    }


async def query_audit(db: AsyncSession, filters: dict[str, Any], page: int, page_size: int,
                      owner_ids: list[int] | None = None) -> tuple[list[AuditLog], int]:
    stmt = select(AuditLog)
    if owner_ids is not None:
        stmt = stmt.where(AuditLog.actor_id.in_(owner_ids))
    for field, column in (("actor_id", AuditLog.actor_id), ("target_type", AuditLog.target_type),
                          ("target_id", AuditLog.target_id), ("action", AuditLog.action),
                          ("source", AuditLog.source), ("result", AuditLog.result)):
        if filters.get(field) not in (None, ""):
            value = filters[field]
            if field in {"actor_id", "target_id"}:
                value = int_filter(field, value)   # 查询串是字符串；PostgreSQL 不允许 bigint = varchar
            stmt = stmt.where(column == value)
    if filters.get("start_time"):
        stmt = stmt.where(AuditLog.timestamp >= _parse_dt(filters["start_time"]))
    if filters.get("end_time"):
        stmt = stmt.where(AuditLog.timestamp <= _parse_dt(filters["end_time"]))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await db.execute(stmt.order_by(AuditLog.timestamp.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return list(rows), int(total)


def int_filter(field: str, value: Any) -> int:
    try:
        return int(str(value).strip())
    except ValueError:
        from bamy.core.errors import ApiError

        raise ApiError(40002, f"{field} 必须是整数") from None


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        from bamy.core.errors import ApiError

        raise ApiError(40002, f"时间格式错误：{value}") from exc


async def write_system_log(db: AsyncSession, level: str, message: str, *, module: str | None = None,
                           instance_id: int | None = None, account_id: int | None = None,
                           extra: Any = None, commit: bool = True) -> SystemLog:
    row = SystemLog(level=level, message=message, module=module, instance_id=instance_id,
                    account_id=account_id,
                    extra=json.dumps(extra, ensure_ascii=False, default=str) if extra is not None else None)
    db.add(row)
    if commit:
        await db.commit()
    return row


def system_log_dict(row: SystemLog) -> dict[str, Any]:
    return {
        "id": row.id, "timestamp": row.timestamp.isoformat() + "Z", "level": row.level,
        "module": row.module, "instance_id": row.instance_id, "account_id": row.account_id,
        "message": row.message,
        "extra": json.loads(row.extra) if row.extra else None,
    }
