"""系统配置：数据库覆盖内置默认值，支持热生效（《配置项》第五节）。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bamy.core.config import settings
from bamy.db.models import SystemConfig

_cache: dict[str, Any] = {}


async def load_all(db: AsyncSession) -> dict[str, Any]:
    global _cache
    rows = (await db.execute(select(SystemConfig))).scalars().all()
    _cache = dict(settings.defaults)
    for row in rows:
        try:
            _cache[row.key] = json.loads(row.value)
        except (TypeError, ValueError):
            _cache[row.key] = row.value
    return _cache


def get(key: str, default: Any = None) -> Any:
    return _cache.get(key, settings.defaults.get(key, default))


async def set_value(db: AsyncSession, key: str, value: Any, updated_by: int | None = None) -> SystemConfig:
    if key not in settings.defaults:
        from bamy.core.errors import ApiError

        raise ApiError(40003, f"配置项 {key} 不存在")
    row = (await db.execute(select(SystemConfig).where(SystemConfig.key == key))).scalar_one_or_none()
    text = json.dumps(value, ensure_ascii=False)
    if row is None:
        row = SystemConfig(key=key, value=text, description=None, updated_by=updated_by)
        db.add(row)
    else:
        row.value = text
        row.updated_by = updated_by
    await db.commit()
    _cache[key] = value
    return row


async def ensure_defaults(db: AsyncSession) -> None:
    existing = {r.key for r in (await db.execute(select(SystemConfig))).scalars().all()}
    for key, value in settings.defaults.items():
        if key not in existing:
            db.add(SystemConfig(key=key, value=json.dumps(value, ensure_ascii=False)))
    await db.commit()
    await load_all(db)
