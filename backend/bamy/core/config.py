"""配置：环境变量 > config/config.yaml > 数据库配置 > 内置默认值（见《配置项》第二节）。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=os.getenv("ENV_FILE", str(ROOT_DIR / ".env")), extra="ignore")

    app_name: str = Field("BAMY", alias="APP_NAME")
    app_env: str = Field("dev", alias="APP_ENV")
    app_host: str = Field("0.0.0.0", alias="APP_HOST")
    app_port: int = Field(8000, alias="APP_PORT")
    log_level: str = Field("info", alias="LOG_LEVEL")
    log_dir: str = Field("./logs", alias="LOG_DIR")

    secret_key: str = Field("dev-secret-key-change-me-0123456789abcdef", alias="SECRET_KEY")
    encrypt_key: str = Field("dev-encrypt-key-change-me-0123456789abcdef", alias="ENCRYPT_KEY")
    access_token_expire: int = Field(1800, alias="ACCESS_TOKEN_EXPIRE")
    refresh_token_expire: int = Field(604800, alias="REFRESH_TOKEN_EXPIRE")
    password_min_length: int = 8

    database_url: str = Field("sqlite+aiosqlite:///./data/bamy.db", alias="DATABASE_URL")
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

    enable_live_trading: bool = Field(False, alias="ENABLE_LIVE_TRADING")
    allow_register: bool = Field(True, alias="ALLOW_REGISTER")
    allow_email_register: bool = Field(False, alias="ALLOW_EMAIL_REGISTER")
    allow_invite_register: bool = Field(False, alias="ALLOW_INVITE_REGISTER")
    auto_recover_on_boot: bool = Field(True, alias="AUTO_RECOVER_ON_BOOT")
    recover_concurrency: int = Field(5, alias="RECOVER_CONCURRENCY")
    recover_timeout: int = Field(60, alias="RECOVER_TIMEOUT")
    reconcile_before_recover: bool = Field(True, alias="RECONCILE_BEFORE_RECOVER")
    reconcile_interval: int = Field(30, alias="RECONCILE_INTERVAL")
    health_check_interval: int = Field(60, alias="HEALTH_CHECK_INTERVAL")
    max_instances_per_user: int = Field(50, alias="MAX_INSTANCES_PER_USER")
    extension_dir: str = Field(str(ROOT_DIR / "extensions"), alias="EXTENSION_DIR")
    strategy_dir_env: str = Field("", alias="STRATEGY_DIR")
    plugin_dir_env: str = Field("", alias="PLUGIN_DIR")
    init_admin_user: str = Field("", alias="INIT_ADMIN_USER")
    init_admin_pass: str = Field("", alias="INIT_ADMIN_PASS")

    # 数据库配置的默认值（system_configs）
    defaults: dict[str, Any] = {
        "register.enabled": True,
        "register.invite_only": False,
        "instance.max_per_user": 50,
        "account.max_per_user": 20,
        "plugin.max_per_user": 20,
        "instance.auto_recover": True,
        "order.max_per_minute": 60,
        "order.max_per_day": 1000,
        "order.purge_canceled_on_close": True,
        "risk.max_daily_loss": 0.1,
        "risk.max_drawdown": 0.3,
        "paper.default_fee_rate": 0.001,
        "paper.default_slippage": 0.0005,
        "paper.snapshot_interval": 60,
        "plugin.intercept_timeout_ms": 200,
        "plugin.auto_disable_threshold": 10,
    }

    # 引擎参数（配置文件段）
    engine: dict[str, Any] = {
        "recover_concurrency": 5,
        "recover_timeout": 60,
        "reconcile_interval": 30,
        "health_check_interval": 60,
        "market_stale_threshold": 5,
    }
    risk: dict[str, Any] = {
        "max_orders_per_minute": 60,
        "max_orders_per_day": 1000,
        "max_daily_loss": 0.1,
        "max_drawdown": 0.3,
    }
    extensions: dict[str, Any] = {}
    plugins: dict[str, Any] = {
        "intercept_timeout_ms": 200,
        "error_threshold": 10,
        "network_enabled": True,
        "max_per_checkpoint": 16,
        "max_hooks": 32,
    }

    @property
    def strategy_dir(self) -> Path:
        return _abs(self.strategy_dir_env or self.extensions.get("strategy_dir") or Path(self.extension_dir) / "strategies")

    @property
    def plugin_dir(self) -> Path:
        return _abs(self.plugin_dir_env or self.extensions.get("plugin_dir") or Path(self.extension_dir) / "plugins")

    @property
    def frontend_dist(self) -> Path:
        return ROOT_DIR / "frontend" / "dist"

    @field_validator("app_env")
    @classmethod
    def _env(cls, v: str) -> str:
        if v not in {"dev", "prod"}:
            raise ValueError("APP_ENV 必须是 dev 或 prod")
        return v

    def load_yaml(self) -> None:
        path = ROOT_DIR / "config" / "config.yaml"
        if not path.exists():
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for section in ("engine", "risk", "plugins", "extensions"):
            if isinstance(data.get(section), dict):
                getattr(self, section).update(data[section])
        db = data.get("database") or {}
        for key, attr in (("pool_size", "db_pool_size"), ("max_overflow", "db_max_overflow"), ("pool_timeout", "db_pool_timeout")):
            if key in db:
                setattr(self, attr, db[key])
        sec = data.get("security") or {}
        if "password_min_length" in sec:
            self.password_min_length = sec["password_min_length"]
        if self.app_env == "prod":
            missing = [k for k in ("SECRET_KEY", "ENCRYPT_KEY", "DATABASE_URL") if not os.getenv(k)]
            if missing:
                raise SystemExit(f"生产环境缺少必需环境变量：{', '.join(missing)}")

    def check(self) -> None:
        if len(self.secret_key) < 32 or len(self.encrypt_key) < 32:
            raise SystemExit("SECRET_KEY / ENCRYPT_KEY 长度不足 32")
        if self.secret_key == self.encrypt_key:
            raise SystemExit("SECRET_KEY 与 ENCRYPT_KEY 必须不同")


def _abs(p: str | Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (ROOT_DIR / path).resolve()


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.load_yaml()
    s.check()
    return s


settings = get_settings()
