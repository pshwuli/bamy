"""QQ 通知（QQ 官方机器人 API v2，私聊 / 群消息）。

发送流程：
1. POST https://bots.qq.com/app/getAppAccessToken  {"appId", "clientSecret"} → {"access_token", "expires_in"}
2. POST https://api.sgroup.qq.com/v2/users/{user_openid}/messages 或 /v2/groups/{group_openid}/messages
   头 Authorization: QQBot {access_token}，体 {"markdown": {"content"}, "msg_type": 2}（Markdown）。
   Markdown 被平台拒绝时自动改发纯文本，消息不会丢。沙箱环境主机为 sandbox.api.sgroup.qq.com。

消息内容面向群友：用实例名而不是编号，买卖方向翻成"开多 / 平空"这类说法，不放技术字段。

扫码绑定（action_scan_bind，协议参考 tencent-connect/dsh-qqbot 所依赖的 qqbot-connector）：
1. start：POST https://q.qq.com/lite/create_bind_task {"key": base64(32 随机字节)} → task_id，
   二维码内容为 https://q.qq.com/qqbot/openclaw/connect.html?task_id=...
2. poll：POST https://q.qq.com/lite/poll_bind_result {"task_id"} → status 1 等待 / 2 完成 / 3 过期，
   完成时给出 bot_appid、AES-256-GCM 加密的 bot_encrypt_secret（IV 12 字节 + 密文 + 标签 16 字节）和 user_openid。
解密用的 key 只留在本绑定对象的内存里，不落库；AppSecret 通过 settings_patch 交给框架加密保存，不返回浏览器。

发送失败只记日志，不影响交易流程。

ctx.state 键：
  qq.token  {token, expires_at}   access_token 缓存，提前 60 秒视为过期
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import time
from decimal import Decimal, InvalidOperation
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
NAME_TTL = 60              # 秒：实例名缓存时长
LEVELS = {"info": 0, "warn": 1, "error": 2}
LEVEL_LABELS = {"info": "提示", "warn": "警告", "error": "严重"}
# 合约 (持仓方向, 买卖方向) → 群友看得懂的说法
FUTURES_ACTIONS = {("long", "buy"): "开多", ("long", "sell"): "平多",
                   ("short", "sell"): "开空", ("short", "buy"): "平空"}
TOKEN_ERRORS = {401, 11244}   # HTTP 401 或业务码 11244（token 过期）时刷新后重试一次
BIND_PENDING, BIND_DONE, BIND_EXPIRED = 1, 2, 3
GATEWAY_INTENTS = 1 << 25      # GROUP_AND_C2C_EVENT：群 @机器人消息和私聊消息
_OWNERS: dict[str, int] = {}   # app_id → 正在监听网关的绑定 id；同一个机器人只留一条连接，避免重复回复


def _data(event: Any) -> dict[str, Any]:
    d = event.get("data") if isinstance(event, dict) else None
    return d if isinstance(d, dict) else {}


def _body(resp: Any) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _dec(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except InvalidOperation:
        return None


def _fmt(x: Decimal | None) -> str | None:
    """数字去掉多余的 0，加千分位：65000.00000000 → 65,000；0.0100 → 0.01。"""
    if x is None:
        return None
    return f"{x:,.8f}".rstrip("0").rstrip(".") or "0"


def _clip(v: Any, limit: int = 120) -> str | None:
    s = " ".join(str(v or "").split())
    return (s[:limit] + "…") if len(s) > limit else (s or None)


URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)


def _scrub(v: Any) -> str:
    """QQ 群消息不允许出现 URL（错误码 40054010），整条会被拒；交易所报错文本常带请求地址，发送前去掉。"""
    return URL_RE.sub("", str(v)).strip()


def _render(title: str, rows: list[tuple[str, Any]]) -> tuple[str, str]:
    """返回 (Markdown, 纯文本)。整条消息是一个引用块：加粗标题，隔一个空引用行，之后每项一行「名称：内容」；纯文本版去掉引用符号。"""
    lines = [f"{k}：{_scrub(v)}" for k, v in rows if v is not None and _scrub(v) not in ("", "None")]
    plain = "\n".join(lines)
    quoted = "\n".join([f"> **{title}**", ">", *(f"> {line}" for line in lines)])
    return quoted, f"{title}\n\n{plain}"


def decrypt_secret(encrypted_b64: str, key_b64: str) -> str:
    """AES-256-GCM：base64 密文 = IV(12) + 密文 + 标签(16)，与 cryptography 的 密文||标签 格式一致。"""
    raw = base64.b64decode(encrypted_b64)
    return AESGCM(base64.b64decode(key_b64)).decrypt(raw[:12], raw[12:], None).decode("utf-8")


class QQNotify(BasePlugin):
    """QQ 通知。"""

    def __init__(self) -> None:
        super().__init__()
        self._bind: dict[str, str] | None = None     # 扫码中的 {task_id, key}，只放内存
        self._names: dict[Any, tuple[str, float]] = {}   # 实例 id → (显示名, 过期时间)，只放内存
        self._relogin = False                            # 网关报会话无效后，下次连接强制换新 token

    async def on_bind(self) -> None:
        self.ctx.log_info(f"已绑定到 {self.ctx.scope}#{self.ctx.scope_id}")

    async def on_settings(self) -> None:
        # 凭据可能已改：丢弃旧 token、实例名缓存和扫码中的任务
        self._bind = None
        self._names.clear()
        await self.ctx.state.delete("qq.token")
        self.ctx.log_info("设置已更新，token 缓存已清零")

    async def on_unbind(self) -> None:
        self._bind = None
        await self.ctx.state.delete("qq.token")

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

    # ---- 后台服务：监听 QQ 网关，群里 @机器人 /id 回复群 ID ----
    async def service_gateway(self) -> None:
        app_id = str(self.ctx.setting("app_id") or "")
        if not app_id or not self.ctx.setting("app_secret") or self.ctx.ws is None or self.ctx.http is None:
            return      # 凭据没填就不连；填好保存后框架会重启本服务
        me = self.ctx.binding_id
        while _OWNERS.setdefault(app_id, me) != me:
            await asyncio.sleep(30)
        try:
            await self._gateway_session()
        finally:
            if _OWNERS.get(app_id) == me:
                del _OWNERS[app_id]

    async def _gateway_session(self) -> None:
        """连上网关，一直收事件；任何需要重连的情况都抛 ConnectionError，由框架退避后重启本服务。"""
        token = await self._token(force=self._relogin)
        self._relogin = False
        if token is None:
            raise ConnectionError("取不到 access_token")
        host = SANDBOX_HOST if self.ctx.setting("sandbox", False) else API_HOST
        resp = await self.ctx.http.get(f"{host}/gateway", headers={"Authorization": f"QQBot {token}"})
        url = _body(resp).get("url")
        if not url:
            raise ConnectionError(f"获取网关地址失败：HTTP {getattr(resp, 'status_code', '')}")
        async with self.ctx.ws.connect(str(url)) as ws:
            interval = float(json.loads(await ws.recv())["d"]["heartbeat_interval"]) / 1000
            await ws.send(json.dumps({"op": 2, "d": {"token": f"QQBot {token}", "intents": GATEWAY_INTENTS,
                                                     "shard": [0, 1], "properties": {}}}))
            seq, last_ack = None, time.time()
            next_beat = last_ack + interval
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, next_beat - time.time()))
                except asyncio.TimeoutError:
                    raw = None
                now = time.time()
                if now >= next_beat:
                    if now - last_ack > interval * 3:
                        raise ConnectionError("网关心跳无响应")
                    await ws.send(json.dumps({"op": 1, "d": seq}))
                    next_beat = now + interval
                if raw is None:
                    continue
                msg = json.loads(raw)
                seq = msg.get("s") or seq
                op = msg.get("op")
                if op == 11:
                    last_ack = now
                elif op == 7:
                    raise ConnectionError("网关要求重连")
                elif op == 9:
                    self._relogin = True
                    raise ConnectionError("网关会话无效")
                elif op == 0:
                    await self._on_dispatch(msg.get("t"), msg.get("d") or {})

    async def _on_dispatch(self, event: str | None, d: dict[str, Any]) -> None:
        """群里 @机器人 发 /id → 回复群 ID；私聊发 /id → 回复你的 openid。其余消息一律忽略。"""
        if event not in ("GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"):
            return
        if "".join(str(d.get("content") or "").split()) != "/id":    # 群消息 content 前面带空格
            return
        if event == "GROUP_AT_MESSAGE_CREATE":
            gid = d.get("group_openid") or d.get("group_id")
            path, text = f"/v2/groups/{gid}/messages", f"群 ID：{gid}"
        else:
            uid = (d.get("author") or {}).get("user_openid")
            gid, path, text = uid, f"/v2/users/{uid}/messages", f"你的 openid：{uid}"
        if not gid or not d.get("id"):
            return
        try:
            status, data = await self._deliver(path, {"content": text, "msg_type": 0, "msg_id": d["id"], "msg_seq": 1})
        except Exception as exc:  # noqa: BLE001 - 回复失败不能让网关连接断掉
            self.ctx.log_warn(f"/id 回复失败：{type(exc).__name__}")
            return
        if status is not None and status >= 400:
            self.ctx.log_warn(f"/id 回复失败：HTTP {status} code={data.get('code', '')} {str(data.get('message', ''))[:120]}")

    # ---- 订阅型 ----
    async def on_filled(self, event: Any) -> None:
        if not self.ctx.setting("notify_fills", True):
            return
        d = _data(event)
        side, pos = str(d.get("side") or ""), d.get("position_side")
        action = FUTURES_ACTIONS.get((pos, side)) if pos else {"buy": "买入", "sell": "卖出"}.get(side, side)
        qty = _dec(d.get("filled_amount")) or _dec(d.get("amount"))
        price = _dec(d.get("filled_price")) or _dec(d.get("price"))
        total = qty * price if qty is not None and price is not None else None
        rows = [("策略", await self._name(d)), ("交易对", d.get("symbol")), ("操作", action),
                ("数量", _fmt(qty)), ("成交价", _fmt(price)),
                ("成交额", f"{_fmt(total)} USDT" if total is not None else None)]
        if d.get("is_guard") and d.get("reason"):
            rows.append(("说明", _clip(d.get("reason"))))
        await self._send("info", "成交通知", rows)

    async def on_error(self, event: Any) -> None:
        d = _data(event)
        await self._send("error", "策略运行出错", [
            ("策略", await self._name(d)), ("原因", _clip(d.get("error"))),
            ("连续出错", f"{d['count']} 次" if d.get("count") else None)])

    async def on_auto_stopped(self, event: Any) -> None:
        d = _data(event)
        await self._send("error", "策略已自动停止", [
            ("策略", await self._name(d)), ("原因", _clip(d.get("reason")) or "未知")])

    async def on_risk(self, event: Any) -> None:
        d = _data(event)
        title = "合约强平" if d.get("rule") == "liquidation" else "风控触发"
        await self._send("error", title, [
            ("策略", await self._name(d)), ("内容", _clip(d.get("message") or d.get("rule"), 200))])

    async def on_alert(self, event: Any) -> None:
        d = _data(event)
        level = str(d.get("level") or "warn")
        await self._send(level, "系统告警", [
            ("级别", LEVEL_LABELS.get(level, "提示")), ("事项", _clip(d.get("title"))),
            ("内容", _clip(d.get("message") or d.get("reason"), 200))])

    async def _name(self, d: dict[str, Any]) -> str | None:
        """实例 id 翻成实例名（模拟盘加标记）；查不到就不显示，不拿编号给群友看。"""
        iid = d.get("instance_id")
        if iid is None:
            return None
        hit = self._names.get(iid)
        if hit is None or hit[1] < time.time():
            try:
                items = (await self.ctx.overview(d.get("owner_id"))).get("instances", [])
            except Exception:  # noqa: BLE001
                items = []
            expires = time.time() + NAME_TTL
            for i in items:
                self._names[i["id"]] = (f"{i['name']}（模拟盘）" if i.get("paper") else str(i["name"]), expires)
            hit = self._names.get(iid)
        return hit[0] if hit else None

    # ---- 发送 ----
    async def _send(self, level: str, title: str, rows: list[tuple[str, Any]]) -> None:
        if LEVELS.get(level, 1) < LEVELS.get(str(self.ctx.setting("min_level", "info")), 0):
            return
        if self.ctx.http is None:
            self.ctx.log_error("未开放出网，无法推送")
            return
        path = self._target_path()
        if path is None:
            self.ctx.log_error("未配置推送目标：请扫码绑定，或填写用户 openid / 群 openid")
            return
        markdown, plain = _render(title, rows)
        try:
            status, data = await self._deliver(path, {"markdown": {"content": markdown[:MAX_CONTENT]}, "msg_type": 2})
            if status is None or status < 400:
                return
            # Markdown 被平台拒绝（例如机器人没有 Markdown 权限）：改发纯文本，消息不丢
            self.ctx.log_warn(f"Markdown 发送被拒，改发纯文本：HTTP {status} code={data.get('code', '')} "
                              f"{str(data.get('message', ''))[:120]}")
            status, data = await self._deliver(path, {"content": plain[:MAX_CONTENT], "msg_type": 0})
            if status is not None and status >= 400:
                # 只记状态码、业务码和平台返回的说明，不记凭据、token 和请求 URL
                self.ctx.log_error(f"QQ 推送失败：HTTP {status} code={data.get('code', '')} "
                                   f"{str(data.get('message', ''))[:200]}")
        except Exception as exc:  # noqa: BLE001
            self.ctx.log_error(f"QQ 推送失败：{type(exc).__name__}")

    async def _deliver(self, path: str, body: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
        """发一次；token 过期（401 / 11244）时刷新后重试一次。返回 (HTTP 状态, 响应体)，拿不到 token 返回 (None, {})。"""
        host = SANDBOX_HOST if self.ctx.setting("sandbox", False) else API_HOST
        for attempt in (0, 1):
            token = await self._token(force=attempt == 1)
            if token is None:
                return None, {}
            resp = await self.ctx.http.post(f"{host}{path}", headers={"Authorization": f"QQBot {token}"}, json=body)
            data, status = _body(resp), getattr(resp, "status_code", 200)
            if attempt == 0 and status >= 400 and (status in TOKEN_ERRORS or data.get("code") in TOKEN_ERRORS):
                continue
            return status, data
        return None, {}

    def _target_path(self) -> str | None:
        if self.ctx.setting("target_type", "c2c") == "group":
            gid = str(self.ctx.setting("group_openid") or "")
            return f"/v2/groups/{gid}/messages" if gid else None
        uid = str(self.ctx.setting("user_openid") or "")
        return f"/v2/users/{uid}/messages" if uid else None

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


PLUGIN = QQNotify
