"""数据库会话与建表。"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bamy.core.config import settings
from bamy.db.models import Base

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


def _sync_missing_columns(sync_conn) -> list[str]:
    """给已存在的表补齐 ORM 模型里新增、但库里还没有的列。

    `Base.metadata.create_all` 只建新表，不会改已有表结构。新增字段后不补列，
    查询会直接报 `no such column: xxx`（例如 accounts.position_mode）。

    只做「加列」，不做改名/改类型/删列——那些需要人工迁移，避免误删数据。
    新增列一律允许 NULL：历史行没有值，强制 NOT NULL 会加不上。
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    dialect = sync_conn.dialect
    added: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all 已经建好了新表
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            try:
                # column.type 是 TypeEngine 实例，传入方言实例即可编译成 DDL 片段
                col_type = column.type.compile(dialect=dialect)
            except Exception as exc:  # noqa: BLE001 - 推断失败就跳过，不阻塞启动
                log.warning("无法推断字段类型，跳过 %s.%s：%s", table.name, column.name, exc)
                continue
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
            sync_conn.execute(text(ddl))
            added.append(f"{table.name}.{column.name}")

    return added


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 兼容旧库：补齐模型里新增的列
        added = await conn.run_sync(_sync_missing_columns)
    if added:
        log.warning("数据库已补齐缺失字段：%s", ", ".join(added))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
