"""安全回归：安全审查发现的 12 个问题逐条锁定。"""
from __future__ import annotations

import json

import pytest


def body(r):
    return r.json()


# #1 重置 token 不进系统日志
async def test_reset_token_not_logged(client, admin_h):
    r = await client.post("/api/auth/register", json={"username": "mallory", "password": "Mallory123",
                                                      "email": "m@example.com"})
    assert r.status_code in (200, 201), r.text
    await client.post("/api/auth/forgot-password", json={"email": "m@example.com"})
    logs = body(await client.get("/api/admin/system-logs", headers=admin_h, params={"page_size": 100}))["data"]["items"]
    assert not any("token=" in (l.get("message") or "") or "reset-password?" in (l.get("message") or "") for l in logs)


# #2 2FA 失败计数 + 伪造 XFF 不改变来源 IP
async def test_totp_failures_lock_and_xff_ignored(client):
    import pyotp

    await client.post("/api/auth/register", json={"username": "totpuser", "password": "Totpuser12"})
    login = body(await client.post("/api/auth/login", json={"username": "totpuser", "password": "Totpuser12"}))
    h = {"Authorization": "Bearer " + login["data"]["access_token"]}
    secret = body(await client.post("/api/auth/2fa/enable", headers=h))["data"]["secret"]
    r = await client.post("/api/auth/2fa/verify", headers=h, json={"code": pyotp.TOTP(secret).now()})
    assert body(r)["code"] == 0, r.text
    codes = []
    for i in range(6):
        r = await client.post("/api/auth/login", headers={"X-Forwarded-For": f"10.9.9.{i}"},
                              json={"username": "totpuser", "password": "Totpuser12", "two_factor_code": "000000"})
        codes.append(body(r)["code"])
    assert 40105 in codes, codes  # 连续 2FA 失败后锁定


def test_ip_ignores_forwarded_header():
    from types import SimpleNamespace

    from bamy.api.auth import _ip

    req = SimpleNamespace(headers={"x-forwarded-for": "6.6.6.6"}, client=SimpleNamespace(host="127.0.0.1"))
    assert _ip(req) == "127.0.0.1"


# #3 插件上下文拿不到引擎、不能写属性
def test_plugin_ctx_sealed():
    from bamy.plugin import PluginContext

    logged = []
    ctx = PluginContext(plugin_id="p", binding_id=1, scope="user", scope_id=1, settings={}, data_scope="own",
                        state=None, http=None, log_fn=lambda lv, m: logged.append(m))
    ctx.log_info("hi")
    assert logged and "hi" in logged[0]
    for name in ("_engine", "_log_fn", "_notify_fn"):
        with pytest.raises(AttributeError):
            getattr(ctx, name)
    with pytest.raises(AttributeError):
        ctx.settings = {"x": 1}
    assert not hasattr(ctx, "place_order")


# #4 出网：userinfo、非白名单、内网解析、危险参数都拒绝
@pytest.mark.parametrize("url,kw", [
    ("https://api.telegram.org@evil.com/x", {}),
    ("https://evil.com/", {}),
    ("file:///etc/passwd", {}),
    ("https://api.telegram.org/", {"follow_redirects": True}),
    ("https://localhost/", {}),
])
async def test_restricted_http_rejects(url, kw):
    from bamy.plugin import RestrictedHttp

    http = RestrictedHttp(["api.telegram.org", "localhost"])
    with pytest.raises(PermissionError):
        await http.get(url, **kw)


# #5 策略 secret 参数：落库加密、输出与审计脱敏、策略拿到明文
async def test_strategy_secret_param(client, user_h, monkeypatch):
    from bamy.extensions.registry import registry

    ext = registry.get_strategy("dca_strategy")
    patched = list(ext.params_schema) + [{"key": "api_note", "type": "secret", "label": "备注密钥"}]
    monkeypatch.setattr(type(ext), "params_schema", property(lambda self: patched if self.id == "dca_strategy"
                                                             else self.meta.get("params") or []))
    r = await client.post("/api/instances", headers=user_h, json={
        "name": "secret-inst", "strategy_id": "dca_strategy", "symbol": "BTC/USDT", "paper": True,
        "paper_balance": 100, "params": {"api_note": "TOPSECRET"}})
    assert r.status_code == 201, r.text
    d = body(r)["data"]
    assert d["params"]["api_note"] == "******"
    from bamy.db.models import Instance
    from bamy.db.session import session_scope

    async with session_scope() as db:
        raw = (await db.get(Instance, d["id"])).params
    assert "TOPSECRET" not in raw and json.loads(raw)["api_note"].startswith("v1:")
    # 回传脱敏值不覆盖原值
    r = await client.patch(f"/api/instances/{d['id']}", headers=user_h, json={"params": {"api_note": "******"}})
    assert body(r)["code"] == 0, r.text
    from bamy.engine.runtime import engine

    ctx = await engine.ctx_for(d["id"])
    assert ctx.params["api_note"] == "TOPSECRET"
    audits = body(await client.get(f"/api/instances/{d['id']}/audit-logs", headers=user_h))["data"]["items"]
    assert "TOPSECRET" not in json.dumps(audits, ensure_ascii=False)


# #6 访客推送按分享开关过滤、裁掉 owner/account
async def test_view_ws_filters():
    from bamy.api.ws import Conn, hub

    sent = []

    class W:
        async def send_text(self, t):
            sent.append(json.loads(t))

        async def close(self, **_):
            sent.append("closed")

    c = Conn(ws=W(), kind="view", instance_id=77, share_id=5, share_owner_id=9,
             share_flags={"show_orders": False, "show_positions": True})
    hub.conns.add(c)
    try:
        await hub.broadcast("order.filled", {"instance_id": 77, "owner_id": 9, "account_id": 3, "filled_price": 1})
        await hub.broadcast("position.changed", {"instance_id": 77, "owner_id": 9, "account_id": 3, "symbol": "X"})
        assert [m["event"] for m in sent] == ["position.changed"]
        assert "owner_id" not in sent[0]["data"] and "account_id" not in sent[0]["data"]
        await hub.close_views(share_id=5)
        assert sent[-1] == "closed" and c not in hub.conns
    finally:
        hub.conns.discard(c)


# #7 禁用用户后其分享立即失效
async def test_disable_user_revokes_share(client, admin_h):
    await client.post("/api/auth/register", json={"username": "sharer", "password": "Sharer1234"})
    h = {"Authorization": "Bearer " + body(await client.post(
        "/api/auth/login", json={"username": "sharer", "password": "Sharer1234"}))["data"]["access_token"]}
    iid = body(await client.post("/api/instances", headers=h, json={
        "name": "s1", "strategy_id": "dca_strategy", "symbol": "BTC/USDT", "paper": True, "paper_balance": 10}))["data"]["id"]
    tok = body(await client.post(f"/api/instances/{iid}/share-tokens", headers=h, json={"expires_in_days": 1}))["data"]["token"]
    assert (await client.get(f"/api/view/{tok}")).status_code == 200
    uid = body(await client.get("/api/auth/me", headers=h))["data"]["id"]
    await client.post(f"/api/admin/users/{uid}/disable", headers=admin_h)
    assert (await client.get(f"/api/view/{tok}")).status_code >= 400


# #8 卸载/重装插件需要 super_admin
def test_uninstall_requires_super():
    from bamy.main import app

    for r in app.routes:
        if getattr(r, "path", "") in {"/api/admin/plugins/{plugin_id}", "/api/admin/plugins/{plugin_id}/install"} \
                and {"DELETE", "POST"} & set(getattr(r, "methods", set())):
            names = {getattr(d.call, "__name__", "") for d in r.dependant.dependencies}
            assert "super_admin" in names, r.path


# #9 伪造 JWT 不能占用他人的限流额度
def test_ratelimit_ident_requires_signature():
    from types import SimpleNamespace

    import jwt

    from bamy.main import _user_ident

    forged = jwt.encode({"sub": "1", "type": "access"}, "wrong-key-wrong-key-wrong-key-123", algorithm="HS256")
    req = SimpleNamespace(headers={"authorization": f"Bearer {forged}"}, client=SimpleNamespace(host="1.2.3.4"))
    assert _user_ident(req) == "ip:1.2.3.4"


# #10 /health 不暴露异常详情（正常时也只有固定取值）
async def test_health_values(client):
    d = body(await client.get("/health"))
    assert set(d["checks"].values()) <= {"ok", "error", "starting"} or d["checks"]["market_hub"].startswith("stale")


# #11 CSV 公式注入
def test_csv_safe():
    from bamy.core.response import csv_safe

    assert csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert csv_safe("@SUM") == "'@SUM" and csv_safe("ok") == "ok" and csv_safe(-1) == -1


# 硬删除用户在外键约束开启时也能完整清理（含实例、订单、分享、绑定）
async def test_hard_delete_user_with_fk(client, admin_h):
    import asyncio

    await client.post("/api/auth/register", json={"username": "goner", "password": "Goner12345"})
    h = {"Authorization": "Bearer " + body(await client.post(
        "/api/auth/login", json={"username": "goner", "password": "Goner12345"}))["data"]["access_token"]}
    uid = body(await client.get("/api/auth/me", headers=h))["data"]["id"]
    iid = body(await client.post("/api/instances", headers=h, json={
        "name": "g1", "strategy_id": "dca_strategy", "symbol": "BTC/USDT", "paper": True,
        "paper_balance": 100, "params": {"quote_amount": 5, "interval": 3600}}))["data"]["id"]
    await client.post(f"/api/instances/{iid}/start", headers=h)
    for _ in range(50):
        if body(await client.get(f"/api/instances/{iid}/orders", headers=h))["data"]["items"]:
            break
        await asyncio.sleep(0.1)
    await client.post(f"/api/instances/{iid}/stop", headers=h)
    await client.post(f"/api/instances/{iid}/share-tokens", headers=h, json={"expires_in_days": 1})
    await client.post("/api/plugin-bindings", headers=h, json={
        "plugin_id": "audit_export", "scope": "user", "scope_id": uid, "settings": {}})
    r = await client.request("DELETE", f"/api/super/users/{uid}", headers=admin_h, json={"confirm": "goner"})
    assert body(r)["code"] == 0, r.text
    users = body(await client.get("/api/admin/users", headers=admin_h, params={"page_size": 200}))["data"]["items"]
    assert all(u["username"] != "goner" for u in users)
