"""WebSocket 推送（《接口契约》第十六节）：鉴权、事件按 owner 隔离、订阅主题。"""
from __future__ import annotations

import json

from starlette.testclient import TestClient


def _token(h: dict) -> str:
    return h["Authorization"].split(" ", 1)[1]


async def test_ws_hub_isolation(user_h, other_h, admin_h):
    from bamy.api.ws import Conn, hub

    sent: dict[str, list] = {"alice": [], "bob": [], "admin": []}

    class FakeWS:
        def __init__(self, name):
            self.name = name

        async def send_text(self, text):
            sent[self.name].append(json.loads(text))

    from bamy.core.security import decode_token

    alice = int(decode_token(_token(user_h))["sub"])
    bob = int(decode_token(_token(other_h))["sub"])
    conns = [Conn(ws=FakeWS("alice"), kind="user", user_id=alice), Conn(ws=FakeWS("bob"), kind="user", user_id=bob),
             Conn(ws=FakeWS("admin"), kind="admin", user_id=0)]
    hub.conns.update(conns)
    try:
        from bamy.engine.runtime import engine

        await engine.push("order.filled", {"instance_id": 99, "owner_id": alice, "status": "filled"})
        await engine.push("system.alert", {"owner_id": None, "title": "全局"})
    finally:
        for c in conns:
            hub.conns.discard(c)
    assert [m["event"] for m in sent["alice"]] == ["order.filled"]
    assert sent["alice"][0]["topic"] == "instance.99.orders"
    assert sent["bob"] == []
    assert {m["event"] for m in sent["admin"]} == {"order.filled", "system.alert"}


def test_ws_auth_rejects_bad_token(app):
    tc = TestClient(app)  # 不进入 with：不重复跑 lifespan
    if True:
        try:
            with tc.websocket_connect("/ws/user?token=bad") as ws:
                ws.receive_text()
            closed = False
        except Exception:  # noqa: BLE001 - 服务端以 4401 关闭
            closed = True
    assert closed
