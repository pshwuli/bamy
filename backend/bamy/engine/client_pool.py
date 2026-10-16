"""连接池：每个账号一份适配器，按需创建、闲置回收、账号删除时清理。"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from bamy.core.crypto import decrypt
from bamy.engine.adapters import BinanceSpotAdapter

log = logging.getLogger("bamy.pool")

IDLE_TTL = 600  # 闲置回收秒数


class ClientPool:
    def __init__(self) -> None:
        self._clients: dict[int, tuple[BinanceSpotAdapter, float]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, account_id: int) -> asyncio.Lock:
        return self._locks.setdefault(account_id, asyncio.Lock())

    async def get(self, account) -> BinanceSpotAdapter:
        async with self._lock(account.id):
            entry = self._clients.get(account.id)
            if entry is not None:
                client, _ = entry
                self._clients[account.id] = (client, time.time())
                return client
            client = BinanceSpotAdapter(
                decrypt(account.api_key_enc), decrypt(account.api_secret_enc),
                env=account.env, readonly=bool(account.readonly),
            )
            self._clients[account.id] = (client, time.time())
            return client

    async def drop(self, account_id: int) -> None:
        entry = self._clients.pop(account_id, None)
        if entry is not None:
            await entry[0].close()
            log.info("已关闭账号 %s 的连接", account_id)

    async def close_all(self) -> None:
        for account_id in list(self._clients):
            await self.drop(account_id)

    async def reap_idle(self) -> int:
        now = time.time()
        stale = [aid for aid, (_, ts) in self._clients.items() if now - ts > IDLE_TTL]
        for aid in stale:
            await self.drop(aid)
        return len(stale)

    def stats(self) -> list[dict[str, Any]]:
        now = time.time()
        return [{"account_id": aid, "idle_seconds": int(now - ts), "env": c.env, "readonly": c.readonly}
                for aid, (c, ts) in self._clients.items()]


pool = ClientPool()
