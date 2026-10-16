"""每日战报：靠 system.heartbeat（每 5 分钟）判断是否到了推送时间，每天只推一次。

ctx.state 键：
- dr.last.<用户ID>  上次推送的日期（按设置的时区，YYYY-MM-DD）
- dr.base.<用户ID>  {day, equity}：当日首次心跳时的总权益，用来算"今日盈亏"
"""
from __future__ import annotations

import time
from typing import Any

from bamy.plugin import BasePlugin

STATUS_TXT = {"running": "运行中", "paused": "已暂停", "stopped": "已停止", "error": "出错", "created": "未启动"}


def _data(event: Any) -> dict[str, Any]:
    d = event.get("data") if isinstance(event, dict) else None
    return d if isinstance(d, dict) else {}


async def push_bot(ctx, channel: str, url: str, title: str, text: str, chat_id: str = "") -> None:
    """把一段文字发到群机器人。只做"发出去"，失败写插件日志，不抛给框架。"""
    if channel == "none" or not url or ctx.http is None:
        return
    if channel == "feishu":
        payload = {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
    elif channel == "dingtalk":
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    elif channel == "wecom":
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    elif channel == "telegram":
        payload = {"chat_id": chat_id, "text": f"{title}\n{text}"}
    else:
        payload = {"title": title, "text": text}
    try:
        resp = await ctx.http.post(url, json=payload)
        if getattr(resp, "status_code", 200) >= 400:
            ctx.log_error(f"{channel} 推送失败：HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        ctx.log_error(f"{channel} 推送失败：{type(exc).__name__}")   # 不打印 URL，避免密钥进日志


class DailyReport(BasePlugin):
    """每日战报。"""

    async def on_bind(self) -> None:
        self.ctx.log_info(f"每日战报已绑定：每天 {int(self.ctx.setting('hour', 21))} 点推送")

    async def on_heartbeat(self, event: Any) -> None:
        d = _data(event)
        owner = d.get("owner_id")
        if self.ctx.scope == "user" and owner != self.ctx.scope_id:
            return
        if self.ctx.scope == "global" and owner is None:
            return
        tz = int(self.ctx.setting("tz_offset", 8))
        local = time.gmtime(time.time() + tz * 3600)
        day = time.strftime("%Y-%m-%d", local)
        ov = await self.ctx.overview(owner)
        items = ov.get("instances") or []
        total = sum(i["equity"] for i in items if i.get("equity") is not None)
        key = f"dr.base.{owner}"
        base = await self.ctx.state.get(key) or {}
        if base.get("day") != day:
            base = {"day": day, "equity": total}
            await self.ctx.state.set(key, base)
        if local.tm_hour < int(self.ctx.setting("hour", 21)):
            return
        last_key = f"dr.last.{owner}"
        if await self.ctx.state.get(last_key) == day:
            return
        await self.ctx.state.set(last_key, day)
        await self._report(day, items, total, float(base.get("equity") or total))

    async def _report(self, day: str, items: list[dict[str, Any]], total: float, base: float) -> None:
        pnl = total - base
        initial = sum(i["initial"] for i in items if i.get("initial"))
        lines = [f"总权益（模拟盘）{total:,.2f} USDT · 今日 {pnl:+,.2f}"
                 + (f" · 累计 {(total / initial - 1) * 100:+.2f}%" if initial else "")]
        for i in items:
            eq = f"{i['equity']:,.2f}" if i.get("equity") is not None else "实盘"
            ret = f" {(i['equity'] / i['initial'] - 1) * 100:+.2f}%" if i.get("equity") is not None and i.get("initial") else ""
            lines.append(f"· {i['name']}（{STATUS_TXT.get(i['status'], i['status'])}）{eq}{ret} · 今日成交 {i['fills_today']} 笔")
        if not items:
            lines.append("还没有实例")
        title = f"BAMY 战报 {day}"
        text = "\n".join(lines)
        await self.ctx.notify(title, text, "info")
        await push_bot(self.ctx, str(self.ctx.setting("channel", "none")), str(self.ctx.setting("webhook_url") or ""),
                       title, text, str(self.ctx.setting("telegram_chat_id") or ""))
        self.ctx.log_info(f"已推送 {day} 战报")


PLUGIN = DailyReport
