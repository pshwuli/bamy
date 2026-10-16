"""数据库会话与建表。"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bamy.core.config import settings
from bamy.db.models import Base

_kwargs: dict = {"echo": False, "future": True}
if not settings.database_url.startswith("sqlite"):
    _kwargs |= {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_pre_ping": True,
    }
else:
    Path("./data").mkdir(exist_ok=True)
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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
