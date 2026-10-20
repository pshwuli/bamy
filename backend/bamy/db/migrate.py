"""启动时执行数据库迁移（alembic upgrade head）。

多 worker 同时启动时只能有一个在跑迁移：
- Postgres：用 advisory lock 串行，后到的等前一个跑完，再看到已是 head，什么也不做；
- SQLite：靠写锁串行；两个进程同时发现没有版本表而撞车时，失败的一方由 init_db 在新事务里重试。
重试不能写在这里：这里跑在调用方打开的事务里，回滚会结束那个事务，之后的 DDL 就没人提交了。
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

log = logging.getLogger("bamy.db.migrate")

SCRIPT_LOCATION = str(Path(__file__).resolve().parent.parent / "migrations")
LOCK_KEY = 7_265_001          # 任意固定整数，只要整个应用里不和别的 advisory lock 重复


def _config(connection) -> Config:  # noqa: ANN001
    cfg = Config()
    cfg.set_main_option("script_location", SCRIPT_LOCATION)
    cfg.attributes["connection"] = connection
    return cfg


def upgrade_sync(sync_conn) -> None:  # noqa: ANN001
    """在同步连接上升级到 head；供 AsyncConnection.run_sync 调用。"""
    if sync_conn.dialect.name == "postgresql":
        sync_conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})   # 事务结束自动释放
    command.upgrade(_config(sync_conn), "head")
