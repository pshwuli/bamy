"""Alembic 环境。两种入口：

- 应用启动（bamy.db.migrate）：把已打开的连接放进 config.attributes["connection"]，直接在这条连接上跑；
- 命令行（alembic upgrade head）：用应用配置里的 DATABASE_URL 建异步引擎再跑。
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from bamy.core.config import settings
from bamy.db.models import Base

config = context.config
target_metadata = Base.metadata

# 只有命令行入口才接管日志配置；应用内调用不能覆盖应用自己的日志
if config.config_file_name is not None and config.attributes.get("connection") is None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _configure(connection) -> None:  # noqa: ANN001
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",   # SQLite 改列要走 batch
    )


def _run_sync(connection) -> None:  # noqa: ANN001
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(_run_sync)
            await conn.commit()
    finally:
        await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
elif config.attributes.get("connection") is not None:
    _run_sync(config.attributes["connection"])
else:
    asyncio.run(_run_async())
