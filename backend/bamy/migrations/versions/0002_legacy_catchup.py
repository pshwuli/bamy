"""legacy catch-up：把迁移链建立之前用 create_all 建的旧库追平

旧库只有表，没有版本记录，之后模型新增的列和索引（orders.position_side、accounts.position_mode、
instance_state/plugin_state 的唯一约束、plugin_health 的部分唯一索引等）靠启动时「缺什么补什么」。
这里把那套逻辑执行最后一次。新库在 0001 里已经是最新结构，这里什么也补不出来。
只做追平，不可回滚。从 0003 起的迁移必须写成写死的、可重复执行的 DDL。

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import logging

from alembic import op

from bamy.db import legacy_sync

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

log = logging.getLogger("bamy.migrations")


def upgrade() -> None:
    added = legacy_sync.sync(op.get_bind())
    if added:
        log.warning("旧库已追平，补齐：%s", ", ".join(added))


def downgrade() -> None:
    pass    # 追平不可逆，也没有回滚的意义
