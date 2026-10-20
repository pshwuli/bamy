"""数据库会话与建表。"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bamy.core.config import settings

log = logging.getLogger("bamy.db")

_kwargs: dict = {"echo": False, "future": True}
if not settings.database_url.startswith("sqlite"):
    _kwargs |= {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_pre_ping": True,
    }
else:
    # 从 DATABASE_URL 提取实际路径并创建目录
    # sqlite+aiosqlite:///./data/bamy.db -> ./data/bamy.db
    # sqlite+aiosqlite:////absolute/path/db.sqlite -> /absolute/path/db.sqlite
    db_path_part = settings.database_url.split("://", 1)[1]
    # 去掉开头的斜杠（相对路径是 /./data/bamy.db，绝对路径是 ////path/db）
    if db_path_part.startswith("/"):
        db_path_part = db_path_part.lstrip("/")
    db_file = Path(db_path_part)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    # 并发写（崩溃恢复并发启动、定时任务、接口请求）时等锁而不是立刻报 database is locked
    _kwargs["connect_args"] = {"timeout": 30}

engine = create_async_engine(settings.database_url, **_kwargs)

if settings.database_url.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")      # 读写互不阻塞
        cur.execute("PRAGMA busy_timeout=30000")    # 写锁等待 30 秒
        cur.execute("PRAGMA synchronous=NORMAL")    # WAL 下的推荐取值，崩溃不丢已提交事务
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """把数据库升级到最新结构（alembic upgrade head）。新库建全部表，旧库按版本链逐步追平。"""
    import asyncio

    from bamy.db.migrate import upgrade_sync

    for attempt in range(1, 4):
        try:
            async with engine.begin() as conn:       # 每次重试都是新事务
                await conn.run_sync(upgrade_sync)
            return
        except Exception as exc:  # noqa: BLE001 - 多进程同时启动时 SQLite 可能撞车，第二次会看到已是 head
            if attempt == 3:
                raise
            log.warning("数据库迁移第 %s 次失败，重试：%s", attempt, exc)
            await asyncio.sleep(0.5 * attempt)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
