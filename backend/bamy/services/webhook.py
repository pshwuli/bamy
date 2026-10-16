"""用户通知 Webhook：用户在"设置 → 通知"里填了 webhook_url 并勾选事件类型后，这些事件会 POST 到该地址。

- 只发用户自己的事件（owner_id 匹配）
- 目标地址做与插件出网相同的检查：只允许 http(s)、不跟随重定向、拒绝解析到内网/回环的地址（防 SSRF）
- 失败不重试、不阻塞引擎；按用户限频（每分钟 30 条）
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from typing import Any

from bamy.db.models import UserPreference
from bamy.db.session import session_scope
from sqlalchemy import select

log = logging.getLogger("bamy.webhook")

PER_MIN = 30
_sent: dict[int, deque[float]] = defaultdict(deque)
_cache: dict[int, tuple[float, dict[str, Any]]] = {}   # owner_id -> (取到的时间, 设置)
CACHE_TTL = 30


async def _settings(owner_id: int) -> dict[str, Any]:
    hit = _cache.get(owner_id)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    async with session_scope() as db:
        row = (await db.execute(select(UserPreference).where(UserPreference.user_id == owner_id))).scalar_one_or_none()
    data = json.loads(row.notification_settings or "{}") if row else {}
    _cache[owner_id] = (time.time(), data)
    return data


def invalidate(owner_id: int) -> None:
    _cache.pop(owner_id, None)


async def deliver(event: str, data: dict[str, Any]) -> None:
    owner_id = data.get("owner_id")
    if not owner_id:
        return
    try:
        cfg = await _settings(int(owner_id))
        url = (cfg.get("webhook_url") or "").strip()
        types = set(cfg.get("event_types") or [])
        if not url or event not in types:
            return
        q = _sent[int(owner_id)]
        now = time.time()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= PER_MIN:
            return
        q.append(now)
        from bamy.plugin import RestrictedHttp
        from urllib.parse import urlsplit

        host = urlsplit(url).hostname or ""
        client = RestrictedHttp([host], timeout_ms=5000)   # 复用出网检查：协议、userinfo、内网地址、重定向
        payload = {"event": event, "data": data, "ts": int(now * 1000), "source": "bamy"}
        await asyncio.wait_for(client.post(url, json=payload, headers={"User-Agent": "BAMY-Webhook/1.0"}), timeout=6)
    except Exception as exc:  # noqa: BLE001 - 用户的 webhook 出问题不能影响交易引擎
        log.info("webhook 投递失败 owner=%s event=%s：%s", owner_id, event, type(exc).__name__)
