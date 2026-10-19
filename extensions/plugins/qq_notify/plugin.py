"""QQ 通知（QQ 官方机器人 API v2，私聊 / 群消息）。

发送流程：
1. POST https://bots.qq.com/app/getAppAccessToken  {"appId", "clientSecret"} → {"access_token", "expires_in"}
2. POST https://api.sgroup.qq.com/v2/users/{user_openid}/messages 或 /v2/groups/{group_openid}/messages
   头 Authorization: QQBot {access_token}，体 {"content", "msg_type": 0}（主动消息不带 msg_id / msg_seq）。
   沙箱环境主机为 sandbox.api.sgroup.qq.com。

扫码绑定（action_scan_bind，协议参考 tencent-connect/dsh-qqbot 所依赖的 qqbot-connector）：
1. start：POST https://q.qq.com/lite/create_bind_task {"key": base64(32 随机字节)} → task_id，
   二维码内容为 https://q.qq.com/qqbot/openclaw/connect.html?task_id=...
2. poll：POST https://q.qq.com/lite/poll_bind_result {"task_id"} → status 1 等待 / 2 完成 / 3 过期，
   完成时给出 bot_appid、AES-256-GCM 加密的 bot_encrypt_secret（IV 12 字节 + 密文 + 标签 16 字节）和 user_openid。
解密用的 key 只留在本绑定对象的内存里，不落库；AppSecret 通过 settings_patch 交给框架加密保存，不返回浏览器。

注意：发的是主动消息，QQ 平台对主动消息有数量限制；超限或未获权限时接口会报错，插件只记日志，不影响交易流程。

ctx.state 键：
  qq.token  {token, expires_at}   access_token 缓存，提前 60 秒视为过期
  qq.rate   {minute, count}       每分钟限频
"""
from __future__ import annotations

import base64
import secrets
import time
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bamy.plugin import BasePlugin

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_HOST = "https://api.sgroup.qq.com"
SANDBOX_HOST = "https://sandbox.api.sgroup.qq.com"
BIND_HOST = "https://q.qq.com"
TOKEN_MARGIN = 60          # 秒：提前刷新，避免发送途中过期
MAX_CONTENT = 1500         # 字符：文档里没查到确切上限，取保守值
POLL_INTERVAL = 2          # 秒：前端轮询间隔
LEVELS = {"info": 0, "warn": 1, "error": 2}
TOKEN_ERRORS = {401, 11244}   # HTTP 401 或业务码 11244（token 过期）时刷新后重试一次
BIND_PENDING, BIND_DONE, BIND_EXPIRED = 1, 2, 3


def _data(event: Any) -> dict[str, Any]:
    d = event.get("data") if isinstance(event, dict) else None
    return d if isinstance(d, dict) else {}


def _body(resp: Any) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def decrypt_secret(encrypted_b64: str, key_b64: str) -> str:
    """AES-256-GCM：base64 密文 = IV(12) + 密文 + 标签(16)，与 cryptography 的 密文||标签 格式一致。"""
    raw = base64.b64decode(encrypted_b64)
    return AESGCM(base64.b64decode(key_b64)).decrypt(raw[:12], raw[12:], None).decode("utf-8")


class QQNotify(BasePlugin):
    """QQ 通知。"""

    def __init__(self) -> None:
        super().__init__()
        self._bind: dict[str, str] | None = None     # 扫码中的 {task_id, key}，只放内存

    async def on_bind(self) -> None:
        self.ctx.log_info(f"已绑定到 {self.ctx.scope}#{self.ctx.scope_id}")

    async def on_settings(self) -> None:
        # 凭据可能已改：丢弃旧 token、限频计数和扫码中的任务
        self._bind = None
        await self.ctx.state.delete("qq.token")
        await self.ctx.state.delete("qq.rate")
        self.ctx.log_info("设置已更新，token 缓存与限频计数已清零")

    async def on_unbind(self) -> None:
        self._bind = None
        for key in ("qq.token", "qq.rate"):
            await self.ctx.state.delete(key)

    # ---- 动作：扫码绑定 ----
    async def action_scan_bind(self, payload: dict[str, Any]) -> dict[str, Any]:
        """payload.step = start 申请二维码；poll 查询扫码结果，完成时返回 settings_patch。"""
        if self.ctx.http is None:
            raise ValueError("未开放出网")
        step = str(payload.get("step") or "start")
        if step == "start":
            return await self._bind_start()
        if step == "poll":
            return await self._bind_poll()
        raise ValueError("step 只能是 start 或 poll")

    async def _bind_start(self) -> dict[str, Any]:
        key = base64.b64encode(secrets.token_bytes(32)).decode()
        data = await self._bind_call("create_bind_task", {"key": key})
        task_id = data.get("task_id")
        if not task_id:
            raise ValueError("QQ 没有返回绑定任务")
        self._bind = {"task_id": str(task_id), "key": key}
        return {"status": "pending", "interval": POLL_INTERVAL,
                "qr_url": f"{BIND_HOST}/qqbot/openclaw/connect.html?task_id={task_id}&source=&_wv=2"}

    async def _bind_poll(self) -> dict[str, Any]:
        if self._bind is None:
            raise ValueError("没有进行中的扫码任务，请重新点击扫码绑定")
        data = await self._bind_call("poll_bind_result", {"task_id": self._bind["task_id"]})
        status = int(data.get("status") or 0)
        if status == BIND_EXPIRED:
            self._bind = None
            return {"status": "expired"}
        if status != BIND_DONE:
            return {"status": "pending", "interval": POLL_INTERVAL}
        app_id = str(data.get("bot_appid") or "")
        encrypted = str(data.get("bot_encrypt_secret") or "")
        if not app_id or not encrypted:
            self._bind = None
            raise ValueError("扫码已完成，但 QQ 没有返回机器人凭据")
        try:
            app_secret = decrypt_secret(encrypted, self._bind["key"])
        except Exception as exc:  # noqa: BLE001 - 不把密文或异常细节带出去
            self._bind = None
            raise ValueError("机器人凭据解密失败，请重新扫码") from exc
        self._bind = None
        patch: dict[str, Any] = {"app_id": app_id, "app_secret": app_secret}
        openid = str(data.get("user_openid") or "")
        if openid:
            patch |= {"user_openid": openid, "target_type": "c2c"}
        self.ctx.log_info(f"扫码绑定成功，AppID={app_id}")
        return {"status": "done", "app_id": app_id, "user_openid": openid or None, "settings_patch": patch}

    async def _bind_call(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = await self.ctx.http.post(f"{BIND_HOST}/lite/{name}", json=body)
        if getattr(resp, "status_code", 200) != 200:
            raise ValueError(f"QQ 绑定服务返回 HTTP {resp.status_code}")
        payload = _body(resp)
        if payload.get("retcode") != 0:
            raise ValueError(str(payload.get("msg") or "QQ 绑定服务返回错误")[:100])
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

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
        path = self._target_path()
        if path is None:
            self.ctx.log_error("未配置推送目标：请扫码绑定，或填写用户 openid / 群 openid")
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
                resp = await self._post_message(path, token, content)
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

    def _target_path(self) -> str | None:
        if self.ctx.setting("target_type", "c2c") == "group":
            gid = str(self.ctx.setting("group_openid") or "")
            return f"/v2/groups/{gid}/messages" if gid else None
        uid = str(self.ctx.setting("user_openid") or "")
        return f"/v2/users/{uid}/messages" if uid else None

    async def _post_message(self, path: str, token: str, content: str) -> Any:
        host = SANDBOX_HOST if self.ctx.setting("sandbox", False) else API_HOST
        return await self.ctx.http.post(
            f"{host}{path}",
            headers={"Authorization": f"QQBot {token}"},
            json={"content": content, "msg_type": 0},
        )

    async def _token(self, *, force: bool = False) -> str | None:
        cached = await self.ctx.state.get("qq.token") or {}
        if not force and cached.get("token") and float(cached.get("expires_at", 0)) > time.time() + TOKEN_MARGIN:
            return str(cached["token"])
        app_id = str(self.ctx.setting("app_id") or "")
        secret = str(self.ctx.setting("app_secret") or "")
        if not app_id or not secret:
            self.ctx.log_error("AppID、AppSecret 未填写：请扫码绑定或手动填写")
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

    async def _take_quota(self) -> bool:
        minute = int(time.time() // 60)
        rate = await self.ctx.state.get("qq.rate") or {}
        count = int(rate.get("count", 0)) if rate.get("minute") == minute else 0
        if count >= int(self.ctx.setting("rate_limit", 10)):
            return False
        await self.ctx.state.set("qq.rate", {"minute": minute, "count": count + 1})
        return True


PLUGIN = QQNotify
