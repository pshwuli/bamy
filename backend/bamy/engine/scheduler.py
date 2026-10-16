"""定时任务：对账、探活、快照、保护检测、日志落库、连接回收（《引擎设计》调度一节）。"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import delete, select

from bamy.core.config import settings
from bamy.db.models import Account, AccountHealth, ScheduledTask, SystemLog
from bamy.db.session import session_scope
from bamy.engine.client_pool import pool
from bamy.services import system_config

log = logging.getLogger("bamy.scheduler")

ABNORMAL_THRESHOLD = 3


class Scheduler:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._fail_streak: dict[int, int] = {}

    def every(self, name: str, seconds: float, fn: Callable[[], Awaitable[Any]], *, delay: float = 1.0) -> None:
        async def loop() -> None:
            await asyncio.sleep(delay)
            while True:
                started = time.time()
                status, error = "success", None
                try:
                    await fn()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 定时任务失败不退出
                    status, error = "failed", str(exc)[:2000]
                    log.exception("定时任务失败 %s", name)
                await self._record(name, status, error)
                await asyncio.sleep(max(0.5, seconds - (time.time() - started)))

        self._tasks.append(asyncio.create_task(loop(), name=f"task:{name}"))

    async def _record(self, name: str, status: str, error: str | None) -> None:
        try:
            async with session_scope() as db:
                row = (await db.execute(select(ScheduledTask).where(ScheduledTask.task_name == name))).scalar_one_or_none()
                if row is None:
                    row = ScheduledTask(task_name=name, run_count=0, fail_count=0)
                    db.add(row)
                row.last_run_at = datetime.utcnow()
                row.last_status = status
                row.last_error = error
                row.run_count = (row.run_count or 0) + 1
                if status != "success":
                    row.fail_count = (row.fail_count or 0) + 1
                await db.commit()
        except Exception:  # noqa: BLE001
            log.exception("记录定时任务失败")

    def start(self, engine) -> None:
        interval = int(settings.engine.get("reconcile_interval", settings.reconcile_interval))
        self.every("reconcile_unknown", interval, engine.reconcile_unknown)
        self.every("reconcile_open", 60, engine.reconcile_open, delay=15)
        self.every("reconcile_accounts", 3600, engine.reconcile_all, delay=60)
        self.every("account_health", int(settings.engine.get("health_check_interval", settings.health_check_interval)),
                   lambda: self.health_check(engine), delay=5)
        self.every("snapshot", int(system_config.get("paper.snapshot_interval", 60)), engine.snapshot, delay=10)
        self.every("guard_check", 30, engine.check_guards, delay=20)
        self.every("flush_logs", 2, engine.flush_logs)
        self.every("pool_reap", 300, pool.reap_idle, delay=300)
        self.every("log_retention", 86400, self.retention, delay=120)
        self.every("plugin_heartbeat", 300, lambda: self.heartbeat(engine), delay=30)

    async def health_check(self, engine) -> None:
        """账号探活：失败标记异常并暂停其下实例；密钥失效 → 失效 → 停用实例 → 告警。"""
        async with session_scope() as db:
            accounts = (await db.execute(select(Account).where(Account.deleted_at.is_(None),
                                                               Account.status.in_(("online", "offline", "error"))))).scalars().all()
        sem = asyncio.Semaphore(10)

        async def probe(account: Account) -> None:
            async with sem:
                ok, latency, code, msg = True, None, None, None
                try:
                    client = await pool.get(account)
                    latency = await asyncio.wait_for(client.ping(), timeout=10)
                    await asyncio.wait_for(client.verify(), timeout=15)
                except Exception as exc:  # noqa: BLE001
                    ok, msg = False, str(exc)[:1000]
                    code = str(getattr(exc, "code", "error"))
                async with session_scope() as db:
                    db.add(AccountHealth(account_id=account.id, success=ok, latency_ms=latency, error_code=code,
                                         error_message=msg))
                    row = await db.get(Account, account.id)
                    if ok:
                        self._fail_streak[account.id] = 0
                        if row.status != "online":
                            row.status = "online"
                            await engine.push("account.recovered", {"account_id": account.id, "owner_id": account.owner_id})
                        row.last_active_at = datetime.utcnow()
                    else:
                        streak = self._fail_streak.get(account.id, 0) + 1
                        self._fail_streak[account.id] = streak
                        if code in {"42210", "42211"}:
                            await db.commit()
                            await engine.account_invalid(account.id, msg or "密钥失效")
                            return
                        if streak >= ABNORMAL_THRESHOLD and row.status == "online":
                            row.status = "error"
                            await db.commit()
                            await engine.account_abnormal(account.id, msg or "探活失败")
                            await engine.push("system.alert", {"level": "warn", "title": "账号探活失败",
                                                               "account_id": account.id, "owner_id": account.owner_id,
                                                               "message": msg})
                            return
                    await db.commit()

        await asyncio.gather(*(probe(a) for a in accounts))

    async def heartbeat(self, engine) -> None:
        """给插件的定时事件（每 5 分钟）：日报、回撤提醒这类插件靠它定时运行。

        每个有运行中实例的用户各发一份（带 owner_id，用户级绑定能收到），再发一份系统级的（全局绑定）。
        """
        from bamy.db.models import User

        now = int(time.time())
        owners = sorted({r.owner_id for r in engine.running.values()})
        async with session_scope() as db:
            active = (await db.execute(select(User.id).where(User.status == "active"))).scalars().all()
        for uid in sorted(set(owners) | set(active)):
            await engine.push("system.heartbeat", {"owner_id": uid, "ts": now})
        await engine.push("system.heartbeat", {"owner_id": None, "ts": now})

    async def retention(self) -> None:
        cutoff = datetime.utcnow() - timedelta(days=30)
        async with session_scope() as db:
            await db.execute(delete(SystemLog).where(SystemLog.timestamp < cutoff))
            await db.execute(delete(AccountHealth).where(AccountHealth.checked_at < cutoff))
            await db.commit()

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def status(self) -> list[str]:
        return [t.get_name() for t in self._tasks if not t.done()]


scheduler = Scheduler()
