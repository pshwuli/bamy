"""数据库会话与建表。"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import UniqueConstraint, inspect, text
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


def _sync_missing_columns(sync_conn) -> list[str]:
    """补齐新增列，并在可执行时补齐模型声明的索引/唯一约束。"""
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    dialect = sync_conn.dialect
    added: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            try:
                col_type = column.type.compile(dialect=dialect)
            except Exception as exc:  # noqa: BLE001
                log.warning("无法推断字段类型，跳过 %s.%s：%s", table.name, column.name, exc)
                continue
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
            sync_conn.execute(text(ddl))
            added.append(f"{table.name}.{column.name}")

    existing_indexes = {index["name"] for table in existing_tables
                        for index in inspector.get_indexes(table)}
    existing_constraints = {constraint["name"] for table in existing_tables
                            for constraint in inspector.get_unique_constraints(table)}
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        for constraint in table.constraints:
            if not isinstance(constraint, UniqueConstraint):
                continue
            name = constraint.name
            if not name or name in existing_constraints:
                continue
            columns = ", ".join(f'"{column.name}"' for column in constraint.columns)
            try:
                sync_conn.execute(text(f'CREATE UNIQUE INDEX "{name}" ON "{table.name}" ({columns})'))
                existing_constraints.add(name)
                added.append(f"{table.name}.{name}")
            except Exception as exc:  # noqa: BLE001 - duplicate legacy rows need manual cleanup
                log.warning("无法补齐唯一约束 %s.%s：%s", table.name, name, exc)
        for index in table.indexes:
            name = index.name
            if not name or name in existing_indexes:
                continue
            columns = ", ".join(f'"{column.name}"' for column in index.columns)
            try:
                sync_conn.execute(text(f'CREATE INDEX "{name}" ON "{table.name}" ({columns})'))
                existing_indexes.add(name)
                added.append(f"{table.name}.{name}")
            except Exception as exc:  # noqa: BLE001
                log.warning("无法补齐索引 %s.%s：%s", table.name, name, exc)

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
