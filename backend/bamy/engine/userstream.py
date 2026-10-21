"""用户数据流（UserStream）：WebSocket 实时接收币安账户的订单变更（《引擎设计》用户数据流一节）。

币安现货已于 2026-02-20 下线 listenKey，改为 WebSocket API 的
`userDataStream.subscribe.signature`；ccxt.pro 的 watch_orders 已实现该订阅，这里直接复用。
WebSocket 做实时收敛，定时对账（Engine.reconcile_*）做兜底，两者互不依赖。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from bamy.core.crypto import decrypt
from bamy.db.models import Account, Order
from bamy.db.session import session_scope

log = logging.getLogger("bamy.userstream")

RECONNECT_MAX = 60


class UserStream:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._clients: dict[int, Any] = {}
        self.engine: Any = None
        self.enabled = True

    def status(self) -> dict[int, str]:
        return {aid: ("running" if not t.done() else "stopped") for aid, t in self._tasks.items()}

    async def ensure(self, account: Account) -> None:
        """为一个实盘在用的账号启动数据流（幂等）。"""
        if not self.enabled or account.id in self._tasks and not self._tasks[account.id].done():
            return
        self._tasks[account.id] = asyncio.create_task(self._run(account.id), name=f"userstream:{account.id}")

    async def drop(self, account_id: int) -> None:
        task = self._tasks.pop(account_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        client = self._clients.pop(account_id, None)
        if client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass

    async def sync(self) -> None:
        """按"有运行中实盘实例的账号"收敛数据流集合。"""
        if self.engine is None:
            return
        wanted = {r.account_id for r in self.engine.running.values() if not r.paper and r.account_id}
        for aid in [a for a in self._tasks if a not in wanted]:
            await self.drop(aid)
        if not wanted:
            return
        async with session_scope() as db:
            rows = (await db.execute(select(Account).where(Account.id.in_(wanted)))).scalars().all()
        for acc in rows:
            await self.ensure(acc)

    async def close(self) -> None:
        for aid in list(self._tasks):
            await self.drop(aid)

    def _client(self, account: Account) -> Any:
        import ccxt.pro as ccxtpro

        opts: dict[str, Any] = {"apiKey": decrypt(account.api_key_enc), "secret": decrypt(account.api_secret_enc),
                                "enableRateLimit": True}
        if account.market_type == "futures":
            # 合约账号的订单、止损单触发（ALGO_UPDATE）都走合约用户流；以前一律按现货订阅，
            # 合约成交推不过来，只能等 30/60 秒一轮的对账发现
            client = ccxtpro.binanceusdm(opts)
        else:
            client = ccxtpro.binance({**opts, "options": {"defaultType": "spot"}})
        if account.env == "testnet":
            client.set_sandbox_mode(True)
        return client

    async def _run(self, account_id: int) -> None:
        backoff = 1
        while True:
            try:
                async with session_scope() as db:
                    account = await db.get(Account, account_id)
                if account is None or account.deleted_at is not None or account.status in {"invalid", "disabled"}:
                    return
                fresh = account_id not in self._clients
                client = self._clients.get(account_id) or self._client(account)
                self._clients[account_id] = client
                if fresh:
                    log.info("用户数据流订阅 account=%s 类型=%s", account_id, account.market_type)
                orders = await client.watch_orders()
                backoff = 1
                for raw in orders:
                    await self._on_order(account_id, raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 断线重连 + 状态补拉
                log.warning("用户数据流断开 account=%s：%s，%ss 后重连", account_id, exc, backoff)
                client = self._clients.pop(account_id, None)
                if client is not None:
                    try:
                        await client.close()
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX)
                if self.engine is not None:
                    try:
                        await self.engine.reconcile_open()
                    except Exception:  # noqa: BLE001
                        log.exception("重连后补拉失败")

    async def _on_order(self, account_id: int, raw: dict[str, Any]) -> None:
        from bamy.engine.adapters import _parse_order

        coid = raw.get("clientOrderId")
        if not coid or self.engine is None:
            return
        async with session_scope() as db:
            oid = (await db.execute(select(Order.id).where(Order.client_order_id == coid,
                                                           Order.account_id == account_id))).scalar_one_or_none()
        if oid is None:
            return  # 不是 BAMY 下的单，交给持仓对账处理
        await self.engine._apply_result(oid, _parse_order(raw, coid))


user_stream = UserStream()
