"""ORM 模型，字段与《数据模型》第四节一一对应。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    two_factor_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    two_factor_secret: Mapped[str | None] = mapped_column(String(255))
    recovery_codes: Mapped[str | None] = mapped_column(Text)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_ip: Mapped[str | None] = mapped_column(String(45))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    session_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), default="read", nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    use_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    device_info: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    api_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), default="binance", nullable=False)
    env: Mapped[str] = mapped_column(String(16), default="mainnet", nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), default="spot", nullable=False)
    readonly: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="offline", nullable=False)
    remark: Mapped[str | None] = mapped_column(Text)
    ip_whitelist: Mapped[str | None] = mapped_column(Text)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("uq_accounts_owner_name", "owner_id", "name", unique=True, sqlite_where=text("deleted_at IS NULL"),
                            postgresql_where=text("deleted_at IS NULL")),)


class AccountHealth(Base):
    __tablename__ = "account_health"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_account_health_account_checked", "account_id", "checked_at"),)


class Strategy(Base):
    __tablename__ = "strategies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="strategy", nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    author: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text)
    capabilities: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    params_schema: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    runtime: Mapped[str | None] = mapped_column(Text)
    dependencies: Mapped[str | None] = mapped_column(Text)
    min_bamy_version: Mapped[str | None] = mapped_column(String(32))
    loaded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    load_error: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class Plugin(Base):
    __tablename__ = "plugins"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="plugin", nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    author: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    default_scope: Mapped[str | None] = mapped_column(String(16))
    allow_multiple: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data_scope: Mapped[str] = mapped_column(String(16), default="own", nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    network: Mapped[str | None] = mapped_column(Text)
    hooks: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    settings_schema: Mapped[str | None] = mapped_column(Text)
    dependencies: Mapped[str | None] = mapped_column(Text)
    min_bamy_version: Mapped[str | None] = mapped_column(String(32))
    uninstalled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    loaded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    load_error: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class PluginBinding(Base, TimestampMixin):
    __tablename__ = "plugin_bindings"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    plugin_id: Mapped[str] = mapped_column(ForeignKey("plugins.id"), index=True, nullable=False)
    plugin_version: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="enabled", nullable=False, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        Index("ix_plugin_bindings_scope", "scope", "scope_id"),
        Index("ix_binding_plugin_scope", "plugin_id", "scope", "scope_id"),
    )


class PluginHealth(Base):
    __tablename__ = "plugin_health"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    plugin_id: Mapped[str] = mapped_column(ForeignKey("plugins.id"), nullable=False)
    binding_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    invoke_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    event_calls: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    checkpoint_calls: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    timeout_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    avg_cost_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)
    __table_args__ = (Index("ix_plugin_health_plugin_binding", "plugin_id", "binding_id"),)


class Instance(Base, TimestampMixin):
    __tablename__ = "instances"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    strategy_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), default="spot", nullable=False)
    paper: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paper_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    paper_config: Mapped[str | None] = mapped_column(Text)
    params: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="created", nullable=False, index=True)
    stop_action: Mapped[str] = mapped_column(String(16), default="keep", nullable=False)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_stopped_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("uq_instances_owner_name", "owner_id", "name", unique=True, sqlite_where=text("deleted_at IS NULL"),
                            postgresql_where=text("deleted_at IS NULL")),)


class InstanceState(Base):
    __tablename__ = "instance_state"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)
    __table_args__ = (UniqueConstraint("instance_id", "key", name="uq_instance_state_key"),)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64))
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), default="spot", nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    position_side: Mapped[str | None] = mapped_column(String(8))
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    filled_amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    filled_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    fee_currency: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), default="strategy", nullable=False, index=True)
    is_guard: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    guard_type: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    intercepted_by: Mapped[str | None] = mapped_column(String(64))
    intercepted_action: Mapped[str | None] = mapped_column(String(16))
    raw_request: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)
    __table_args__ = (
        Index("ix_orders_instance_created", "instance_id", "created_at"),
        Index("ix_orders_owner_created", "owner_id", "created_at"),
        Index("ix_orders_account_created", "account_id", "created_at"),
        Index("ix_orders_symbol_created", "symbol", "created_at"),
    )


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    positions: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    total_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    __table_args__ = (Index("ix_position_snapshots_instance_at", "instance_id", "snapshot_at"),)


class PaperAccount(Base):
    __tablename__ = "paper_accounts"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), unique=True, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    positions: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    avg_price: Mapped[str | None] = mapped_column(Text)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    peak_equity: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    initial_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)


class PaperSnapshot(Base):
    __tablename__ = "paper_snapshots"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    positions: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    peak_equity: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    drawdown: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    __table_args__ = (Index("ix_paper_snapshots_instance_at", "instance_id", "snapshot_at"),)


class CircuitBreaker(Base):
    __tablename__ = "circuit_breakers"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime)
    recovered_by: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    recover_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_circuit_breakers_level_target", "level", "target_id"),)


class RiskRule(Base, TimestampMixin):
    __tablename__ = "risk_rules"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    rule_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    rule_value: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    actor_role: Mapped[str | None] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    fail_reason: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    __table_args__ = (
        Index("ix_audit_logs_actor_ts", "actor_id", "timestamp"),
        Index("ix_audit_logs_target_ts", "target_type", "target_id", "timestamp"),
        Index("ix_audit_logs_action_ts", "action", "timestamp"),
    )


class SystemLog(Base):
    __tablename__ = "system_logs"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    module: Mapped[str | None] = mapped_column(String(32))
    instance_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    account_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    extra: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_system_logs_level_ts", "level", "timestamp"),)


class SystemConfig(Base):
    __tablename__ = "system_configs"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))


class ShareToken(Base):
    __tablename__ = "share_tokens"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True, nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    show_equity: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_positions: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_orders: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_params: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    show_logs: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    access_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_access_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class ShareAccess(Base):
    __tablename__ = "share_access"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    share_token_id: Mapped[int] = mapped_column(ForeignKey("share_tokens.id"), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    accessed_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class ReconcileDiff(Base):
    __tablename__ = "reconcile_diffs"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    instance_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    account_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    diff_type: Mapped[str] = mapped_column(String(32), nullable=False)
    expected: Mapped[str | None] = mapped_column(Text)
    actual: Mapped[str | None] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    task_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)
    run_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    fail_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)


class InviteCode(Base):
    __tablename__ = "invite_codes"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class InviteUsage(Base):
    __tablename__ = "invite_usages"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    invite_id: Mapped[int] = mapped_column(ForeignKey("invite_codes.id"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    username: Mapped[str | None] = mapped_column(String(32))
    used_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class Migration(Base):
    __tablename__ = "migrations"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64))


class PluginState(Base):
    """绑定级状态（插件 ctx.state），与实例级状态分表存放。"""
    __tablename__ = "plugin_state"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    binding_id: Mapped[int] = mapped_column(ForeignKey("plugin_bindings.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)
    __table_args__ = (UniqueConstraint("binding_id", "key", name="uq_plugin_state_key"),)


class Notification(Base):
    """通知中心（ctx.notify 与系统告警）。"""
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), index=True)
    level: Mapped[str] = mapped_column(String(8), default="info", nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(64))
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)


class UserPreference(Base):
    """用户偏好与通知设置（《接口契约》22.3、22.4）。"""
    __tablename__ = "user_preferences"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    preferences: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    notification_settings: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)


class Backup(Base):
    __tablename__ = "backups"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
