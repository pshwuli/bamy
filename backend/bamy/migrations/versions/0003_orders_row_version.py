"""orders.row_version：订单乐观锁版本号

多 worker 部署下，用户流、REST 对账、撤单可能同时回写同一笔订单。ORM 的 version_id_col 让每次 UPDATE
都带 WHERE row_version = 旧值 并加 1，抢输的一方抛 StaleDataError，由引擎重读重试。
列必须 NOT NULL 且带默认值 1：已有订单要回填成 1，否则 NULL 永远匹配不上，每次更新都会冲突。
写成可重复执行：0002 追平旧库时可能已经把列补上了。

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _has_column() -> bool:
    return any(c["name"] == "row_version" for c in sa.inspect(op.get_bind()).get_columns("orders"))


def upgrade() -> None:
    if not _has_column():
        op.add_column("orders", sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"))
        return
    op.execute(sa.text("UPDATE orders SET row_version = 1 WHERE row_version IS NULL"))


def downgrade() -> None:
    if _has_column():
        with op.batch_alter_table("orders") as batch:
            batch.drop_column("row_version")
