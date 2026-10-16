"""事件总线：订阅型钩子与内部订阅都挂在这里（《引擎设计》事件总线一节）。"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Awaitable, Callable

log = logging.getLogger("bamy.eventbus")

Handler = Callable[[dict[str, Any]], Awaitable[None]]

# 《插件规范》4.1 的事件清单
EVENTS = {
    "order.created", "order.submitted", "order.filled", "order.partial", "order.failed",
    "order.rejected", "position.changed", "guard.placed", "guard.triggered", "guard.missing",
    "risk.breached", "risk.circuit_break", "risk.circuit_recover", "account.invalid",
    "account.recovered", "account.limit_hit", "instance.error", "instance.auto_stopped",
    "reconcile.diff", "reconcile.completed", "system.alert", "tick",
    "instance.status",
}


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[tuple[str, Handler]]] = defaultdict(list)
        self._plugin_dispatchers: list[Callable[[str, dict[str, Any]], Awaitable[None]]] = []
        self._history: list[dict[str, Any]] = []

    def subscribe(self, event: str, handler: Handler, name: str = "") -> None:
        self._subs[event].append((name or getattr(handler, "__qualname__", "handler"), handler))

    def unsubscribe(self, event: str, name: str) -> None:
        self._subs[event] = [(n, h) for n, h in self._subs[event] if n != name]

    def add_plugin_dispatcher(self, dispatcher: Callable[[str, dict[str, Any]], Awaitable[None]]) -> None:
        self._plugin_dispatchers.append(dispatcher)

    async def publish(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """异常隔离：单个 handler 失败不影响其他 handler。"""
        body = {"event": event, "data": payload or {}, "ts": int(datetime.utcnow().timestamp() * 1000)}
        self._history.append(body)
        if len(self._history) > 500:
            self._history = self._history[-200:]
        for name, handler in list(self._subs.get(event, [])):
            try:
                await handler(body)
            except Exception:  # noqa: BLE001 - 订阅者失败必须隔离
                log.exception("事件处理失败 event=%s handler=%s", event, name)
        for dispatcher in list(self._plugin_dispatchers):
            try:
                await dispatcher(event, body)
            except Exception:  # noqa: BLE001
                log.exception("插件分发失败 event=%s", event)

    def publish_nowait(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """在同步上下文（下单路径等）里投递，不阻塞调用方。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - 无事件循环时丢弃
            return
        loop.create_task(self.publish(event, payload))

    def recent(self, limit: int = 50, event: str | None = None) -> list[dict[str, Any]]:
        items = [h for h in self._history if not event or h["event"] == event]
        return items[-limit:]


bus = EventBus()


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
