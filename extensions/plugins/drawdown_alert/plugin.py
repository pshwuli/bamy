"""回撤提醒：每次心跳（5 分钟）按实例峰值权益算回撤，跨过新档位就提醒；强平、熔断等风控事件立即转发。

ctx.state 键：
- dd.<实例ID>  {peak, sent: 已提醒的最高档位}
"""
from __future__ import annotations

from typing import Any

from bamy.plugin import BasePlugin


def _data(event: Any) -> dict[str, Any]:
    d = event.get("data") if isinstance(event, dict) else None
    return d if isinstance(d, dict) else {}


def parse_levels(text: str) -> list[float]:
    out = []
    for x in str(text or "").replace("，", ",").split(","):
        try:
            v = float(x.strip())
        except ValueError:
            continue
        if 0 < v < 100:
            out.append(v)
    return sorted(set(out))


async def push_bot(ctx, channel: str, url: str, text: str) -> None:
    if channel == "none" or not url or ctx.http is None:
        return
    payload = ({"msg_type": "text", "content": {"text": text}} if channel == "feishu"
               else {"msgtype": "text", "text": {"content": text}})
    try:
        resp = await ctx.http.post(url, json=payload)
        if getattr(resp, "status_code", 200) >= 400:
            ctx.log_error(f"{channel} 推送失败：HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        ctx.log_error(f"{channel} 推送失败：{type(exc).__name__}")


class DrawdownAlert(BasePlugin):
    """回撤提醒。"""

    async def _alert(self, title: str, text: str, level: str = "warn") -> None:
        await self.ctx.notify(title, text, level)
        await push_bot(self.ctx, str(self.ctx.setting("channel", "none")), str(self.ctx.setting("webhook_url") or ""),
                       f"{title}\n{text}")

    async def on_heartbeat(self, event: Any) -> None:
        owner = _data(event).get("owner_id")
        if owner is None or (self.ctx.scope == "user" and owner != self.ctx.scope_id):
            return
        levels = parse_levels(self.ctx.setting("levels", "5,10,20"))
        if not levels:
            return
        for i in (await self.ctx.overview(owner)).get("instances") or []:
            eq = i.get("equity")
            if eq is None or i.get("status") not in {"running", "paused"}:
                continue
            key = f"dd.{i['id']}"
            st = await self.ctx.state.get(key) or {}
            peak = max(float(st.get("peak") or 0), float(i.get("peak") or 0), eq)
            sent = float(st.get("sent") or 0)
            if eq >= peak:
                sent = 0          # 创新高，档位重置
            dd = (1 - eq / peak) * 100 if peak > 0 else 0
            hit = [lv for lv in levels if dd >= lv and lv > sent]
            if hit:
                sent = max(hit)
                await self._alert(f"回撤提醒：{i['name']}",
                                  f"权益 {eq:,.2f}，比最高点 {peak:,.2f} 回撤 {dd:.1f}%（跨过 {sent:g}% 档）。"
                                  "插件只提醒、不平仓；需要自动停止请在风控规则里设置最大回撤。")
            await self.ctx.state.set(key, {"peak": peak, "sent": sent})

    async def on_breached(self, event: Any) -> None:
        d = _data(event)
        if self.ctx.scope == "user" and d.get("owner_id") != self.ctx.scope_id:
            return
        await self._alert(f"风控触发：实例 #{d.get('instance_id', '')}", str(d.get("message") or d.get("rule") or ""), "error")


PLUGIN = DrawdownAlert
