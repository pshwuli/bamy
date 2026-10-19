"""QQ 群通知（QQ 官方机器人 API v2，群消息）。

流程：
1. POST https://bots.qq.com/app/getAppAccessToken  {"appId", "clientSecret"} → {"access_token", "expires_in"}
2. POST https://api.sgroup.qq.com/v2/groups/{group_openid}/messages
   头 Authorization: QQBot {access_token}，体 {"content", "msg_type": 0, "msg_seq"}
   沙箱环境主机为 sandbox.api.sgroup.qq.com。

注意：这里发的是主动消息（没有 msg_id），QQ 平台对主动消息有数量限制；超限或未获权限时接口会报错，
插件只记日志，不影响交易流程。

ctx.state 键：
  qq.token  {token, expires_at}   access_token 缓存，提前 60 秒视为过期
  qq.seq    int                   msg_seq 递增计数
  qq.rate   {minute, count}       每分钟限频
"""
from __future__ import annotations

import time
from typing import Any

from bamy.plugin import BasePlugin

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_HOST = "https://api.sgroup.qq.com"
SANDBOX_HOST = "https://sandbox.api.sgroup.qq.com"
TOKEN_MARGIN = 60          # 秒：提前刷新，避免发送途中过期
MAX_CONTENT = 1500         # 字符：文档里没查到确切上限，取保守值
LEVELS = {"info": 0, "warn": 1, "error": 2}
TOKEN_ERRORS = {401, 11244}   # HTTP 401 或业务码 11244（token 过期）时刷新后重试一次


def _data(event: Any) -> dict[str, Any]:
    d = event.get("data") if isinstance(event, dict) else None
    return d if isinstance(d, dict) else {}


def _body(resp: Any) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


class QQNotify(BasePlugin):
    """QQ 群通知。"""

    async def on_bind(self) -> None:
        self.ctx.log_info(f"已绑定到 {self.ctx.scope}#{self.ctx.scope_id}")

    async def on_settings(self) -> None:
        # 凭据可能已改：丢弃旧 token 和限频计数
        await self.ctx.state.delete("qq.token")
        await self.ctx.state.delete("qq.rate")
        self.ctx.log_info("设置已更新，token 缓存与限频计数已清零")

    async def on_unbind(self) -> None:
        for key in ("qq.token", "qq.seq", "qq.rate"):
            await self.ctx.state.delete(key)

    # ---- 订阅型 ----
    async def on_filled(self, event: Any) -> None:
        if not self.ctx.setting("notify_fills", True):
            return
        d = _data(event)
        await self._send("info", f"成交 · 实例#{d.get('instance_id', '')} {d.get('symbol', '')} "
                                 f"{d.get('side', '')} {d.get('filled_amount') or d.get('amount') or ''} "
                                 f"@ {d.get('filled_price') or ''}")

    async def on_error(self, event: Any) -> None:
        d = _data(event)
        await self._send("error", f"实例出错 · #{d.get('instance_id', '')} {d.get('error', '')}")

    async def on_auto_stopped(self, event: Any) -> None:
        d = _data(event)
        await self._send("error", f"实例自动停止 · #{d.get('instance_id', '')} 原因：{d.get('reason', '未知')}")

    async def on_risk(self, event: Any) -> None:
        d = _data(event)
        await self._send("error", f"风控触发 · #{d.get('instance_id', '')} {d.get('message') or d.get('rule') or ''}")

    async def on_alert(self, event: Any) -> None:
        d = _data(event)
        level = str(d.get("level") or "warn")
        body = d.get("message") or d.get("reason") or ""
        await self._send(level, f"系统告警 · {d.get('title', '')} {body}")

    # ---- 发送 ----
    async def _send(self, level: str, text: str) -> None:
        if LEVELS.get(level, 1) < LEVELS.get(str(self.ctx.setting("min_level", "info")), 0):
            return
        if self.ctx.http is None:
            self.ctx.log_error("未开放出网，无法推送")
            return
        if not await self._take_quota():
            self.ctx.log_warn("超过每分钟推送上限，本条丢弃")
            return
        content = f"BAMY {text}"[:MAX_CONTENT]
        try:
            for attempt in (0, 1):
                token = await self._token(force=attempt == 1)
                if token is None:
                    return
                resp = await self._post_message(token, content)
                data = _body(resp)
                status = getattr(resp, "status_code", 200)
                if status < 400:
                    return
                if attempt == 0 and (status in TOKEN_ERRORS or data.get("code") in TOKEN_ERRORS):
                    continue
                # 只记状态码、业务码和平台返回的说明，不记凭据、token 和请求 URL
                self.ctx.log_error(f"QQ 推送失败：HTTP {status} code={data.get('code', '')} "
                                   f"{str(data.get('message', ''))[:200]}")
                return
        except Exception as exc:  # noqa: BLE001
            self.ctx.log_error(f"QQ 推送失败：{type(exc).__name__}")

    async def _post_message(self, token: str, content: str) -> Any:
        host = SANDBOX_HOST if self.ctx.setting("sandbox", False) else API_HOST
        group = str(self.ctx.setting("group_openid") or "")
        url = f"{host}/v2/groups/{group}/messages"
        return await self.ctx.http.post(
            url,
            headers={"Authorization": f"QQBot {token}"},
            json={"content": content, "msg_type": 0, "msg_seq": await self._next_seq()},
        )

    async def _token(self, *, force: bool = False) -> str | None:
        cached = await self.ctx.state.get("qq.token") or {}
        if not force and cached.get("token") and float(cached.get("expires_at", 0)) > time.time() + TOKEN_MARGIN:
            return str(cached["token"])
        app_id = str(self.ctx.setting("app_id") or "")
        secret = str(self.ctx.setting("app_secret") or "")
        if not app_id or not secret or not self.ctx.setting("group_openid"):
            self.ctx.log_error("AppID、AppSecret、群 openid 未填写完整")
            return None
        resp = await self.ctx.http.post(TOKEN_URL, json={"appId": app_id, "clientSecret": secret})
        data = _body(resp)
        token = data.get("access_token")
        if getattr(resp, "status_code", 200) >= 400 or not token:
            self.ctx.log_error(f"QQ 获取 access_token 失败：HTTP {getattr(resp, 'status_code', '')} "
                               f"code={data.get('code', '')} {str(data.get('message', ''))[:200]}")
            return None
        try:
            ttl = int(data.get("expires_in") or 7200)    # 平台以字符串返回秒数
        except (TypeError, ValueError):
            ttl = 7200
        await self.ctx.state.set("qq.token", {"token": token, "expires_at": time.time() + ttl})
        return str(token)

    async def _next_seq(self) -> int:
        """msg_seq：同一 msg_id 下用于去重；主动消息每条递增，避免被平台当重复消息丢弃。"""
        seq = (int(await self.ctx.state.get("qq.seq") or 0) % 2_000_000_000) + 1
        await self.ctx.state.set("qq.seq", seq)
        return seq

    async def _take_quota(self) -> bool:
        minute = int(time.time() // 60)
        rate = await self.ctx.state.get("qq.rate") or {}
        count = int(rate.get("count", 0)) if rate.get("minute") == minute else 0
        if count >= int(self.ctx.setting("rate_limit", 10)):
            return False
        await self.ctx.state.set("qq.rate", {"minute": minute, "count": count + 1})
        return True


PLUGIN = QQNotify
