"""审计导出插件：把关键事件累积进绑定级 ctx.state，按批次通过 ctx.notify 汇报。

ctx.state 键：
- audit.events   最近的事件列表（受 max_events 限制，先进先出）
- audit.counters {事件名: 累计次数}
- audit.bound_at 绑定时间（毫秒）
"""
from __future__ import annotations

import time
from typing import Any

from bamy.plugin import BasePlugin

IMPORTANT = {"order.rejected", "instance.auto_stopped", "reconcile.diff"}


def _to_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return dict(event)
    if hasattr(event, "to_dict"):
        return event.to_dict()
    return {k: v for k, v in vars(event).items() if not k.startswith("_")} if hasattr(event, "__dict__") else {"value": str(event)}


class AuditExport(BasePlugin):
    """审计导出插件。"""

    async def on_bind(self) -> None:
        if await self.ctx.state.get("audit.bound_at") is None:
            await self.ctx.state.set("audit.bound_at", int(time.time() * 1000))
        await self.ctx.notify("审计导出已启用", f"作用域 {self.ctx.scope}#{self.ctx.scope_id}，数据范围 {self.ctx.data_scope}")

    async def on_settings(self) -> None:
        # 上限调小时立即裁剪
        events = await self.ctx.state.get("audit.events", []) or []
        limit = int(self.ctx.setting("max_events", 500))
        if len(events) > limit:
            await self.ctx.state.set("audit.events", events[-limit:])
        self.ctx.log_info(f"设置已更新：max_events={limit}")

    async def on_unbind(self) -> None:
        counters = await self.ctx.state.get("audit.counters", {}) or {}
        summary = "，".join(f"{k}={v}" for k, v in sorted(counters.items())) or "无事件"
        await self.ctx.notify("审计导出已停用", f"累计：{summary}")
        if self.ctx.setting("clear_on_unbind"):
            for key in ("audit.events", "audit.counters", "audit.bound_at"):
                await self.ctx.state.delete(key)

    # ---- 订阅型 ----
    async def on_order_filled(self, event: Any) -> None:
        await self._record("order.filled", event)

    async def on_order_rejected(self, event: Any) -> None:
        await self._record("order.rejected", event)

    async def on_auto_stopped(self, event: Any) -> None:
        await self._record("instance.auto_stopped", event)

    async def on_reconcile_diff(self, event: Any) -> None:
        await self._record("reconcile.diff", event)

    # ---- 内部 ----
    async def _record(self, name: str, event: Any) -> None:
        events = await self.ctx.state.get("audit.events", []) or []
        counters = await self.ctx.state.get("audit.counters", {}) or {}
        events.append({"event": name, "at": int(time.time() * 1000), "data": _to_dict(event)})
        events = events[-int(self.ctx.setting("max_events", 500)):]
        counters[name] = int(counters.get(name, 0)) + 1
        await self.ctx.state.set("audit.events", events)
        await self.ctx.state.set("audit.counters", counters)

        if name in IMPORTANT and self.ctx.setting("notify_important", True):
            await self.ctx.notify(f"审计：{name}", self.ctx.dump(_to_dict(event))[:500], "warn")

        batch = int(self.ctx.setting("notify_every", 50))
        total = sum(counters.values())
        if batch > 0 and total % batch == 0:
            await self.ctx.notify("审计累计", f"已记录 {total} 条事件，最近保留 {len(events)} 条")


PLUGIN = AuditExport
