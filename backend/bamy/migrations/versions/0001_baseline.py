"""baseline：按当前模型建出所有缺失的表

这是迁移链的起点。新库从这里建出全部表；已有的旧库（之前用 create_all 建的）表已存在，这里什么也不会动。
注意：它调用的是 create_all，所以只在「起点」这一处允许依赖当前模型；
从 0002 起的每个迁移都必须是写死的、可重复执行的 DDL，不能再依赖模型，否则以后模型一变，老迁移的结果就跟着变。

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from alembic import op

from bamy.db.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise NotImplementedError("基线不可回滚：回滚会删除全部业务表")
