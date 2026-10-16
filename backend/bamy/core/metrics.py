"""Prometheus 指标（/metrics）。"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("bamy_http_requests_total", "HTTP 请求数", ["method", "path", "status"])
HTTP_LATENCY = Histogram("bamy_http_request_seconds", "HTTP 请求耗时", ["method", "path"])
RUNNING_INSTANCES = Gauge("bamy_running_instances", "运行中实例数")
MARKET_SUBSCRIPTIONS = Gauge("bamy_market_subscriptions", "行情订阅交易对数")
LIVE_BINDINGS = Gauge("bamy_plugin_live_bindings", "生效中的插件绑定数")
POOL_CLIENTS = Gauge("bamy_client_pool_size", "连接池中的账号连接数")
WS_CONNECTIONS = Gauge("bamy_ws_connections", "WebSocket 连接数")
ENGINE_UP = Gauge("bamy_engine_ready", "引擎是否就绪")


def refresh(engine) -> None:
    stats = engine.stats()
    RUNNING_INSTANCES.set(stats["running_instances"])
    MARKET_SUBSCRIPTIONS.set(len(stats["subscriptions"]))
    LIVE_BINDINGS.set(stats["live_bindings"])
    POOL_CLIENTS.set(len(stats["pool"]))
    ENGINE_UP.set(1 if stats["ready"] else 0)
