"""WebSocket：/ws/user、/ws/admin、/ws/view/{token}（《接口契约》第十六节）。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from bamy.core.errors import ApiError
from bamy.core.security import decode_token
from bamy.db.models import User
from bamy.db.session import session_scope

log = logging.getLogger("bamy.ws")
router = APIRouter()

MAX_MSG_PER_MIN = 100


@dataclass(eq=False)  # 按对象身份哈希，才能放进 set
class Conn:
    ws: WebSocket
    kind: str                       # user / admin / view
    user_id: int | None = None
    instance_id: int | None = None  # 访客视图
    share_id: int | None = None
    share_owner_id: int | None = None
    share_expires_at: Any = None
    share_flags: dict[str, bool] = field(default_factory=dict)
    topics: set[str] = field(default_factory=set)
    msg_times: list[float] = field(default_factory=list)


class Hub:
    def __init__(self) -> None:
        self.conns: set[Conn] = set()

    def count(self) -> int:
        return len(self.conns)

    async def broadcast(self, event: str, data: dict[str, Any]) -> None:
        if not self.conns:
            return
        owner = data.get("owner_id")
        iid = data.get("instance_id")
        topic = _topic(event, data)
        msg = json.dumps({"topic": topic, "event": event, "data": data, "timestamp": int(time.time() * 1000)},
                         ensure_ascii=False, default=str)
        dead = []
        for c in list(self.conns):
            if c.kind == "admin":
                pass
            elif c.kind == "user":
                if owner is None or owner != c.user_id:
                    continue
            elif c.kind == "view":
                if iid != c.instance_id or event not in PUBLIC_EVENTS:
                    continue
                if c.share_expires_at is not None and c.share_expires_at < _utcnow():
                    dead.append(c)
                    continue
                need = VIEW_FLAG.get(event)
                if need and not c.share_flags.get(need):
                    continue
                try:
                    await c.ws.send_text(json.dumps({"topic": topic, "event": event, "data": _public(event, data),
                                                     "timestamp": int(time.time() * 1000)},
                                                    ensure_ascii=False, default=str))
                except Exception:  # noqa: BLE001
                    dead.append(c)
                continue
            if c.topics and topic not in c.topics and not any(topic.startswith(t.rstrip("*")) for t in c.topics if t.endswith("*")):
                # 订阅了主题时按主题过滤；状态类事件始终下发
                if event not in ALWAYS:
                    continue
            try:
                await c.ws.send_text(msg)
            except Exception:  # noqa: BLE001
                dead.append(c)
        for c in dead:
            self.conns.discard(c)
            if c.kind == "view":
                await _close(c, 4401, "链接已失效")

    async def close_views(self, *, share_id: int | None = None, owner_id: int | None = None) -> None:
        """分享被撤销、所属用户被禁用时，断开对应的访客连接。"""
        for c in [c for c in self.conns if c.kind == "view" and (
                (share_id is not None and c.share_id == share_id) or
                (owner_id is not None and c.share_owner_id == owner_id))]:
            self.conns.discard(c)
            await _close(c, 4401, "链接已失效")


async def _close(c: Conn, code: int, reason: str) -> None:
    try:
        await c.ws.close(code=code, reason=reason)
    except Exception:  # noqa: BLE001
        pass


def _utcnow():
    from datetime import datetime

    return datetime.utcnow()


# 访客事件需要的分享开关；不在表里的公开事件（实例状态）总是下发
VIEW_FLAG = {"order.filled": "show_orders", "position.changed": "show_positions"}
PUBLIC_FIELDS = {
    "instance.status": {"instance_id", "status"},
    "order.filled": {"instance_id", "symbol", "side", "type", "amount", "filled_amount", "filled_price", "status"},
    "position.changed": {"instance_id", "symbol"},
}


def _public(event: str, data: dict[str, Any]) -> dict[str, Any]:
    keep = PUBLIC_FIELDS.get(event, {"instance_id"})
    return {k: v for k, v in data.items() if k in keep}


PUBLIC_EVENTS = {"instance.status", "order.filled", "position.changed"}
ALWAYS = {"instance.status", "system.alert", "notification", "instance.auto_stopped", "account.invalid",
          "risk.circuit_break", "risk.circuit_recover"}


def _topic(event: str, data: dict[str, Any]) -> str:
    iid, aid = data.get("instance_id"), data.get("account_id")
    if event.startswith("order.") and iid:
        return f"instance.{iid}.orders"
    if event == "instance.log" and iid:
        return f"instance.{iid}.logs"
    if event.startswith("position.") and iid:
        return f"instance.{iid}.positions"
    if event.startswith("instance.") and iid:
        return f"instance.{iid}.status"
    if event.startswith("account.") and aid:
        return f"account.{aid}.status"
    return event


hub = Hub()


async def _auth_user(token: str | None) -> User:
    if not token:
        raise ApiError(40101)
    payload = decode_token(token)
    async with session_scope() as db:
        user = await db.get(User, int(payload["sub"]))
    if user is None or user.status != "active":
        raise ApiError(40106)
    return user


async def _serve(conn: Conn) -> None:
    from bamy.core import metrics as m

    hub.conns.add(conn)
    m.WS_CONNECTIONS.set(hub.count())
    await conn.ws.send_text(json.dumps({"event": "connected", "data": {"kind": conn.kind}, "timestamp": int(time.time() * 1000)}))
    try:
        while True:
            try:
                raw = await asyncio.wait_for(conn.ws.receive_text(), timeout=60)
            except asyncio.TimeoutError:
                await conn.ws.send_text(json.dumps({"event": "ping", "timestamp": int(time.time() * 1000)}))
                continue
            now = time.time()
            conn.msg_times = [t for t in conn.msg_times if now - t < 60] + [now]
            if len(conn.msg_times) > MAX_MSG_PER_MIN:
                await conn.ws.send_text(json.dumps({"event": "error", "code": 42904, "message": "WebSocket 消息过频"}))
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            action = msg.get("action")
            topics = {str(t) for t in msg.get("topics") or []}
            if action == "subscribe":
                conn.topics |= topics
            elif action == "unsubscribe":
                conn.topics -= topics
            elif action == "ping":
                await conn.ws.send_text(json.dumps({"event": "pong", "timestamp": int(now * 1000)}))
                continue
            await conn.ws.send_text(json.dumps({"event": "subscribed", "data": sorted(conn.topics)}))
    except WebSocketDisconnect:
        pass
    finally:
        hub.conns.discard(conn)
        m.WS_CONNECTIONS.set(hub.count())


@router.websocket("/ws/user")
async def ws_user(ws: WebSocket):
    await ws.accept()
    try:
        user = await _auth_user(ws.query_params.get("token"))
    except ApiError as exc:
        await ws.close(code=4401, reason=exc.message)
        return
    await _serve(Conn(ws=ws, kind="user", user_id=user.id))


@router.websocket("/ws/admin")
async def ws_admin(ws: WebSocket):
    await ws.accept()
    try:
        user = await _auth_user(ws.query_params.get("token"))
    except ApiError as exc:
        await ws.close(code=4401, reason=exc.message)
        return
    if user.role not in {"admin", "super_admin"}:
        await ws.close(code=4403, reason="角色不足")
        return
    await _serve(Conn(ws=ws, kind="admin", user_id=user.id))


@router.websocket("/ws/view/{token}")
async def ws_view(ws: WebSocket, token: str):
    await ws.accept()
    from bamy.api.admin import resolve_share

    try:
        async with session_scope() as db:
            share, inst = await resolve_share(db, token)
    except ApiError as exc:
        await ws.close(code=4401, reason=exc.message)
        return
    flags = {k: bool(getattr(share, k)) for k in ("show_equity", "show_positions", "show_orders")}
    await _serve(Conn(ws=ws, kind="view", instance_id=inst.id, share_id=share.id, share_owner_id=share.owner_id,
                      share_expires_at=share.expires_at, share_flags=flags))
