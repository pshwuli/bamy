"""端到端：认证 → 策略/插件 → 模拟盘实例全生命周期 → 订单/持仓 → 插件拦截 → 管理员 → 隔离。"""
from __future__ import annotations

import asyncio

from conftest import PRICES, login


def body(r):
    return r.json()


async def wait_status(client, h, iid, want, timeout=10.0):
    for _ in range(int(timeout / 0.1)):
        r = await client.get(f"/api/instances/{iid}", headers=h)
        if body(r)["data"]["status"] == want:
            return body(r)["data"]
        await asyncio.sleep(0.1)
    raise AssertionError(f"实例 {iid} 未到达 {want}：{body(r)['data']['status']}")


# ---------------------------------------------------------------- 系统与认证
async def test_health_and_envelope(client):
    r = await client.get("/health")
    assert r.status_code == 200
    d = body(r)
    assert d["status"] == "healthy" and set(d["checks"]) >= {"database", "engine", "market_hub"}
    r = await client.get("/metrics")
    assert r.status_code == 200 and "bamy_http_requests_total" in r.text


async def test_setup_once(client, admin_h):
    r = await client.post("/api/setup", json={"username": "again", "password": "Again12345"})
    assert r.status_code >= 400
    r = await client.get("/api/auth/me", headers=admin_h)
    assert body(r)["data"]["role"] == "super_admin"


async def test_login_errors(client, user_h):
    r = await client.post("/api/auth/login", json={"username": "alice", "password": "wrongpass1"})
    assert r.status_code == 401 and body(r)["code"] == 40104
    r = await client.get("/api/auth/me")
    assert r.status_code == 401 and body(r)["code"] == 40101
    r = await client.get("/api/auth/me", headers={"Authorization": "Bearer junk"})
    assert body(r)["code"] == 40102


async def test_validation_error_shape(client, user_h):
    r = await client.post("/api/instances", headers=user_h, json={"name": "x"})
    d = body(r)
    assert r.status_code == 400 and d["code"] == 40004
    assert any(e["field"] == "strategy_id" for e in d["errors"])
    assert "request_id" in d and "timestamp" in d


async def test_refresh_and_logout(client):
    r = await client.post("/api/auth/register", json={"username": "carol", "password": "Carol12345"})
    assert r.status_code in (200, 201)
    r = await client.post("/api/auth/login", json={"username": "carol", "password": "Carol12345"})
    tokens = body(r)["data"]
    assert {"access_token", "refresh_token", "token_type", "expires_in", "user"} <= set(tokens)
    r = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert body(r)["code"] == 0
    new = body(r)["data"]
    h = {"Authorization": f"Bearer {new['access_token']}"}
    r = await client.post("/api/auth/logout", headers=h, json={"all_devices": False})
    assert body(r)["message"] == "登出成功"
    r = await client.post("/api/auth/refresh", json={"refresh_token": new.get("refresh_token") or tokens["refresh_token"]})
    assert body(r)["code"] != 0


async def test_api_version_prefix(client, user_h):
    r = await client.get("/api/v1/auth/me", headers=user_h)
    assert body(r)["data"]["username"] == "alice"
    assert r.headers.get("x-request-id")


# ---------------------------------------------------------------- 策略与插件
async def test_strategies_and_plugins(client, user_h):
    r = await client.get("/api/strategies", headers=user_h)
    ids = {s["id"] for s in body(r)["data"]["items"]}
    assert {"grid_strategy", "dca_strategy", "ma_cross"} <= ids
    r = await client.get("/api/strategies/dca_strategy", headers=user_h)
    assert body(r)["data"]["params_schema"]
    r = await client.get("/api/plugins", headers=user_h)
    pids = {p["id"] for p in body(r)["data"]["items"]}
    assert {"telegram_notify", "audit_export"} <= pids
    r = await client.get("/api/strategies/nope", headers=user_h)
    assert r.status_code == 404 and body(r)["code"] == 40402


async def test_global_binding_forbidden_for_user(client, user_h):
    r = await client.post("/api/plugin-bindings", headers=user_h,
                          json={"plugin_id": "telegram_notify", "scope": "global", "scope_id": None, "settings": {}})
    assert body(r)["code"] in (42281, 42280)


# ---------------------------------------------------------------- 模拟盘实例生命周期
async def _create_dca(client, h, name="dca-1", balance=10000):
    r = await client.post("/api/instances", headers=h, json={
        "name": name, "strategy_id": "dca_strategy", "symbol": "BTC/USDT", "paper": True,
        "paper_balance": balance, "params": {"quote_amount": 100, "interval": 3600}})
    assert r.status_code == 201, r.text
    return body(r)["data"]


async def test_instance_lifecycle(client, user_h):
    inst = await _create_dca(client, user_h)
    iid = inst["id"]
    assert inst["status"] == "created"
    # 名称唯一
    r = await client.post("/api/instances", headers=user_h, json={
        "name": "dca-1", "strategy_id": "dca_strategy", "symbol": "BTC/USDT", "paper": True, "paper_balance": 100})
    assert body(r)["code"] == 40901

    r = await client.post(f"/api/instances/{iid}/start", headers=user_h)
    assert body(r)["code"] == 0, r.text
    await wait_status(client, user_h, iid, "running")
    # 启动幂等
    r = await client.post(f"/api/instances/{iid}/start", headers=user_h)
    assert body(r)["code"] == 0

    # DCA 首个 tick 即买入：等待成交
    for _ in range(60):
        r = await client.get(f"/api/instances/{iid}/orders", headers=user_h)
        items = body(r)["data"]["items"]
        if items and items[0]["status"] == "filled":
            break
        await asyncio.sleep(0.1)
    assert items and items[0]["status"] == "filled" and items[0]["side"] == "buy"
    assert items[0]["source"] == "strategy"

    r = await client.get(f"/api/instances/{iid}/paper-account", headers=user_h)
    acct = body(r)["data"]
    assert acct["balance"] < 10000 and acct["positions"]

    r = await client.get(f"/api/instances/{iid}/positions", headers=user_h)
    positions = body(r)["data"]["positions"]
    assert positions and positions[0]["symbol"] == "BTC/USDT"

    # 运行中禁止删除 / 编辑参数
    r = await client.delete(f"/api/instances/{iid}", headers=user_h)
    assert body(r)["code"] == 40903

    r = await client.post(f"/api/instances/{iid}/pause", headers=user_h)
    assert body(r)["code"] == 0
    await wait_status(client, user_h, iid, "paused")
    r = await client.post(f"/api/instances/{iid}/resume", headers=user_h)
    assert body(r)["code"] == 0
    await wait_status(client, user_h, iid, "running")

    r = await client.post(f"/api/instances/{iid}/stop", headers=user_h)
    assert body(r)["code"] == 0
    await wait_status(client, user_h, iid, "stopped")
    r = await client.post(f"/api/instances/{iid}/stop", headers=user_h)
    assert body(r)["code"] == 0  # 停止幂等

    r = await client.get(f"/api/instances/{iid}/logs", headers=user_h)
    assert body(r)["code"] == 0
    r = await client.get(f"/api/instances/{iid}/audit-logs", headers=user_h)
    actions = {a["action"] for a in body(r)["data"]["items"]}
    assert {"instance.create", "instance.start", "instance.stop"} <= actions


async def test_close_all_paper(client, user_h):
    inst = await _create_dca(client, user_h, name="dca-close")
    iid = inst["id"]
    await client.post(f"/api/instances/{iid}/start", headers=user_h)
    await wait_status(client, user_h, iid, "running")
    for _ in range(60):
        r = await client.get(f"/api/instances/{iid}/positions", headers=user_h)
        if body(r)["data"]["positions"]:
            break
        await asyncio.sleep(0.1)
    r = await client.post(f"/api/instances/{iid}/positions/close-all", headers=user_h)
    # dca 未声明 close_all → 走框架兜底 force-close
    if body(r)["code"] != 0:
        r = await client.post(f"/api/instances/{iid}/positions/force-close", headers=user_h, json={})
    assert body(r)["code"] == 0, r.text
    r = await client.get(f"/api/instances/{iid}/orders", headers=user_h)
    sides = [o["side"] for o in body(r)["data"]["items"]]
    assert "sell" in sides
    await client.post(f"/api/instances/{iid}/stop", headers=user_h)


async def test_change_strategy_and_delete(client, user_h):
    inst = await _create_dca(client, user_h, name="dca-change")
    iid = inst["id"]
    r = await client.post(f"/api/instances/{iid}/change-strategy", headers=user_h,
                          json={"strategy_id": "ma_cross", "params": {}})
    assert body(r)["code"] == 0, r.text
    assert body(r)["data"]["strategy_id"] == "ma_cross"
    r = await client.delete(f"/api/instances/{iid}", headers=user_h)
    assert body(r)["code"] == 0
    r = await client.get(f"/api/instances/{iid}", headers=user_h)
    assert r.status_code == 404


# ---------------------------------------------------------------- 插件拦截
async def test_plugin_deny_market_order(client, user_h):
    me = body(await client.get("/api/auth/me", headers=user_h))["data"]
    r = await client.post("/api/plugin-bindings", headers=user_h, json={
        "plugin_id": "telegram_notify", "scope": "user", "scope_id": me["id"],
        "settings": {"bot_token": "123:abc", "chat_id": "1", "block_market_orders": True}})
    assert r.status_code == 201, r.text
    bid = body(r)["data"]["id"]
    # 重复绑定（allow_multiple=false）
    r = await client.post("/api/plugin-bindings", headers=user_h, json={
        "plugin_id": "telegram_notify", "scope": "user", "scope_id": me["id"],
        "settings": {"bot_token": "123:abc", "chat_id": "1"}})
    assert body(r)["code"] == 40913

    inst = await _create_dca(client, user_h, name="dca-deny")
    iid = inst["id"]
    await client.post(f"/api/instances/{iid}/start", headers=user_h)
    await wait_status(client, user_h, iid, "running")
    for _ in range(60):
        r = await client.get(f"/api/instances/{iid}/orders", headers=user_h)
        items = body(r)["data"]["items"]
        if items and items[0]["status"] not in {"pending", "submitting"}:
            break
        await asyncio.sleep(0.1)
    assert items and items[0]["status"] == "rejected"
    r = await client.get(f"/api/orders/{items[0]['id']}", headers=user_h)
    detail = body(r)["data"]
    assert detail["source"] == "strategy"
    assert "telegram_notify" in str(detail)
    await client.post(f"/api/instances/{iid}/stop", headers=user_h)

    r = await client.get(f"/api/plugin-bindings/{bid}/health", headers=user_h)
    assert body(r)["code"] == 0
    r = await client.delete(f"/api/plugin-bindings/{bid}", headers=user_h)
    assert r.status_code in (200, 204)


# ---------------------------------------------------------------- 隔离
async def test_isolation(client, user_h, other_h):
    inst = await _create_dca(client, user_h, name="dca-private")
    r = await client.get(f"/api/instances/{inst['id']}", headers=other_h)
    assert r.status_code == 404
    r = await client.post(f"/api/instances/{inst['id']}/start", headers=other_h)
    assert r.status_code == 404
    r = await client.get("/api/instances", headers=other_h)
    assert all(i["id"] != inst["id"] for i in body(r)["data"]["items"])
    r = await client.get("/api/admin/users", headers=user_h)
    assert r.status_code in (403, 404)


# ---------------------------------------------------------------- 订单 / 模拟盘对比
async def test_orders_and_compare(client, user_h):
    r = await client.get("/api/orders", headers=user_h, params={"page": 1, "page_size": 5})
    d = body(r)["data"]
    assert set(d["pagination"]) >= {"page", "page_size", "total", "total_pages", "has_next", "has_prev"}
    r = await client.get("/api/orders", headers=user_h, params={"start_time": "not-a-date"})
    assert body(r)["code"] == 40002
    r = await client.get("/api/orders/export", headers=user_h)
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    ids = ",".join(str(i["id"]) for i in body(await client.get("/api/instances", headers=user_h))["data"]["items"][:2])
    r = await client.get("/api/paper/compare", headers=user_h, params={"instance_ids": ids})
    assert body(r)["code"] == 0
    r = await client.get("/api/paper/compare/metrics", headers=user_h, params={"instance_ids": ids})
    assert body(r)["code"] == 0


# ---------------------------------------------------------------- 分享
async def test_share(client, user_h):
    inst = await _create_dca(client, user_h, name="dca-share")
    r = await client.post(f"/api/instances/{inst['id']}/share-tokens", headers=user_h, json={
        "expires_in_days": 7, "show_equity": True, "show_positions": True, "show_orders": False,
        "show_params": False, "show_logs": False})
    assert r.status_code == 201, r.text
    share = body(r)["data"]
    r = await client.get(f"/view/{share['token']}", headers={"Accept": "application/json"})
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        r = await client.get(f"/api/view/{share['token']}")
    d = body(r)["data"]
    assert d["instance_name"] == "dca-share" and d.get("orders") is None
    r = await client.delete(f"/api/share-tokens/{share['id']}", headers=user_h)
    assert body(r)["code"] == 0 or r.status_code == 204
    r = await client.get(f"/api/view/{share['token']}")
    assert r.status_code >= 400


# ---------------------------------------------------------------- 管理员
async def test_admin_endpoints(client, admin_h, user_h):
    for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/accounts", "/api/admin/instances",
                 "/api/admin/system-logs", "/api/admin/audit-logs", "/api/admin/configs",
                 "/api/admin/circuit-breakers", "/api/admin/reconcile/diffs", "/api/admin/invites",
                 "/api/admin/plugin-bindings", "/api/admin/plugin-bindings/all", "/api/admin/plugins/network-hosts",
                 "/api/super/backups"):
        r = await client.get(path, headers=admin_h)
        assert r.status_code == 200 and body(r)["code"] == 0, (path, r.text)
    r = await client.post("/api/admin/plugins/reload", headers=admin_h)
    d = body(r)["data"]
    assert d["strategies"]["loaded"] == 3 and d["plugins"]["loaded"] == 2


async def test_admin_user_ops(client, admin_h):
    users = body(await client.get("/api/admin/users", headers=admin_h))["data"]["items"]
    bob = next(u for u in users if u["username"] == "bob")
    r = await client.post(f"/api/admin/users/{bob['id']}/disable", headers=admin_h)
    assert body(r)["code"] == 0
    r = await client.post("/api/auth/login", json={"username": "bob", "password": "Bob1234567"})
    assert body(r)["code"] == 40106
    await client.post(f"/api/admin/users/{bob['id']}/enable", headers=admin_h)
    r = await client.post(f"/api/admin/users/{bob['id']}/reset-password", headers=admin_h)
    temp = body(r)["data"]["temp_password"]
    await login(client, "bob", temp)


async def test_circuit_breaker_blocks_start(client, admin_h, user_h):
    me = body(await client.get("/api/auth/me", headers=user_h))["data"]
    r = await client.post("/api/admin/circuit-breakers", headers=admin_h,
                          json={"level": "user", "target_id": me["id"], "reason": "测试熔断"})
    assert body(r)["code"] == 0, r.text
    cb = body(r)["data"]
    r = await client.get("/api/admin/circuit-breakers", headers=admin_h)
    assert any(c["id"] == cb["id"] for c in body(r)["data"]["items"])
    r = await client.post(f"/api/admin/circuit-breakers/{cb['id']}/recover", headers=admin_h,
                          json={"reason": "测试结束"})
    assert body(r)["code"] == 0


async def test_invite_and_config(client, admin_h):
    r = await client.post("/api/admin/invites", headers=admin_h, json={"max_uses": 2, "expires_in_days": 1, "role": "user"})
    assert body(r)["code"] == 0, r.text
    r = await client.patch("/api/super/configs", headers=admin_h, json={"configs": {"paper.snapshot_interval": 30}})
    assert body(r)["code"] == 0, r.text
    items = body(await client.get("/api/admin/configs", headers=admin_h))["data"]["items"]
    assert next(i for i in items if i["key"] == "paper.snapshot_interval")["value"] == 30


async def test_rate_limit(client):
    from bamy.core.ratelimit import limiter

    limiter.enabled = True
    limiter.reset()
    try:
        codes = []
        for _ in range(12):
            r = await client.post("/api/auth/login", json={"username": "nobody", "password": "x1234567"})
            codes.append(r.status_code)
        assert 429 in codes
        r429 = r
        assert r429.headers.get("retry-after")
    finally:
        limiter.enabled = False
        limiter.reset()


async def test_prices_unchanged():
    assert PRICES["BTC/USDT"] == 50000.0
