"""用户数据流（UserStream）：WebSocket 实时接收币安账户的订单变更（《引擎设计》用户数据流一节）。

币安现货已于 2026-02-20 下线 listenKey，改为 WebSocket API 的
`userDataStream.subscribe.signature`；ccxt.pro 的 watch_orders 已实现该订阅，这里直接复用。
WebSocket 做实时收敛，定时对账（Engine.reconcile_*）做兜底，两者互不依赖。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select

from bamy.core.crypto import decrypt
from bamy.core.redact import describe_exc
from bamy.db.models import Account, Order
from bamy.db.session import session_scope

log = logging.getLogger("bamy.userstream")

RECONNECT_MAX = 60
KEEP_ALIVE_MS = 20000
HEALTHY_SECONDS = 30      # 连接撑过这么久再断，说明链路是好的，退避从头算，不接着往 60 秒涨


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
        """所有已填写、可用的账号一起建立数据流；不再只看"有运行中实例"的账号。

        几个账号的任务在同一轮里同时创建，不再有先有后。已失效或被停用的账号不连。
        """
        if self.engine is None:
            return
        async with session_scope() as db:
            rows = (await db.execute(select(Account).where(
                Account.deleted_at.is_(None), Account.status.notin_(("invalid", "disabled"))))).scalars().all()
        wanted = {a.id for a in rows}
        for aid in [a for a in self._tasks if a not in wanted]:
            await self.drop(aid)
        await asyncio.gather(*(self.ensure(acc) for acc in rows))

    async def close(self) -> None:
        for aid in list(self._tasks):
            await self.drop(aid)

    def _client(self, account: Account) -> Any:
        import ccxt.pro as ccxtpro

        opts: dict[str, Any] = {"apiKey": decrypt(account.api_key_enc), "secret": decrypt(account.api_secret_enc),
                                "enableRateLimit": True,
                                # ccxt 对币安默认 180 秒才发一次心跳，而这台机器的链路约 60 秒空闲就会被掐断（code 1006，
                                # 324 次连接存活中位数 65 秒）；缩到 20 秒，对照测试里默认心跳 96 秒断开、20 秒心跳连了 190 秒没断
                                "streaming": {"keepAlive": KEEP_ALIVE_MS}}
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
        connected_at: float | None = None
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
                    connected_at = time.monotonic()
                    log.info("用户数据流订阅 account=%s 类型=%s", account_id, account.market_type)
                orders = await client.watch_orders()
                backoff = 1
                for raw in orders:
                    await self._on_order(account_id, raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 断线重连 + 状态补拉
                lived = time.monotonic() - connected_at if connected_at is not None else 0.0
                connected_at = None       # 下一轮没建成新连接就不能沿用这次的起点
                if lived >= HEALTHY_SECONDS:
                    backoff = 1       # 空仓时没有订单事件，原来的退避永远不重置，一路涨到 60 秒
                log.warning("用户数据流断开 account=%s：%s，连了 %.0f 秒，%ss 后重连",
                            account_id, describe_exc(exc, 160), lived, backoff)
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
        result = _parse_order(raw, coid)
        # 推送里的手续费只是这一笔成交的，不是订单累计；清掉，让成交落库时按订单号查一次累计手续费
        result.fee, result.fee_currency = 0.0, None
        await self.engine._apply_result(oid, result)


user_stream = UserStream()
