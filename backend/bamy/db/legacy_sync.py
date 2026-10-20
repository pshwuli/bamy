"""旧库追平（只给迁移 0002 用）。

迁移链建立之前，库是 create_all 建的，之后模型加的列和索引都靠启动时「缺什么补什么」。
这个模块把那套逻辑原样保留下来，仅用于把没有版本记录的旧库一次性追平到 0002 的状态。
新的结构变更一律写成新的迁移文件，不要再往这里加。

和旧实现相比修了三处：
- 补列时带上 NOT NULL 和默认值（旧实现补出来的列一律可空、没有默认值）；
- 补索引时保留 UNIQUE 和部分索引的 WHERE 条件（旧实现会把它们补成普通索引）；
- Postgres 下每条语句包在 SAVEPOINT 里，一条失败不会让整个迁移事务作废。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

from sqlalchemy import UniqueConstraint, inspect, text
from sqlalchemy.schema import CreateIndex

from bamy.db.models import Base

log = logging.getLogger("bamy.db.legacy")


@contextmanager
def _attempt(conn, what: str, added: list[str] | None = None):
    """尝试执行一条 DDL；失败只记警告，不中断整个追平。"""
    nested = conn.begin_nested() if conn.dialect.name == "postgresql" else None
    try:
        yield
        if nested is not None:
            nested.commit()
        if added is not None:
            added.append(what)
    except Exception as exc:  # noqa: BLE001 - 旧数据有重复值等情况需要人工处理
        if nested is not None:
            nested.rollback()
        log.warning("无法补齐 %s：%s", what, exc)


def _default_sql(column) -> str:  # noqa: ANN001
    sd = column.server_default
    if sd is None or not hasattr(sd, "arg"):
        return ""
    arg = sd.arg
    if isinstance(arg, str):               # server_default="1" / "pending"：数字原样，其余加引号
        quoted = arg if arg.lstrip("-").replace(".", "", 1).isdigit() else "'" + arg.replace("'", "''") + "'"
        return f" DEFAULT {quoted}"
    return f" DEFAULT {arg.text}" if hasattr(arg, "text") else ""    # text("...") 表达式；其他形式不猜，留空


def sync(conn) -> list[str]:  # noqa: ANN001
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    added: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            try:
                col_type = column.type.compile(dialect=conn.dialect)
            except Exception as exc:  # noqa: BLE001
                log.warning("无法推断字段类型，跳过 %s.%s：%s", table.name, column.name, exc)
                continue
            default = _default_sql(column)
            # 没有默认值的 NOT NULL 列没法给已有行补，只能先建成可空
            null = " NOT NULL" if (not column.nullable and default) else ""
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}{default}{null}'
            with _attempt(conn, f"{table.name}.{column.name}", added):
                conn.execute(text(ddl))

    existing = {ix["name"] for t in tables for ix in inspector.get_indexes(t)}
    existing |= {c["name"] for t in tables for c in inspector.get_unique_constraints(t) if c.get("name")}
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and constraint.name and constraint.name not in existing:
                cols = ", ".join(f'"{c.name}"' for c in constraint.columns)
                with _attempt(conn, f"{table.name}.{constraint.name}", added):
                    conn.execute(text(f'CREATE UNIQUE INDEX "{constraint.name}" ON "{table.name}" ({cols})'))
                existing.add(constraint.name)
        for index in table.indexes:
            if not index.name or index.name in existing:
                continue
            with _attempt(conn, f"{table.name}.{index.name}", added):
                # 直接用模型里的 Index 对象生成 DDL，UNIQUE 和部分索引的 WHERE 都会保留
                conn.execute(CreateIndex(index))
            existing.add(index.name)
    return added
