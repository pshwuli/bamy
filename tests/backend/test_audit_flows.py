"""流程审计回归：审计确认的缺口逐条锁定（编号对应审计报告）。"""
from __future__ import annotations

import asyncio

from conftest import login


def body(r):
    return r.json()


async def _instance(client, h, **kw):
    payload = {"name": kw.pop("name", "audit-inst"), "strategy_id": "dca_strategy", "symbol": "BTC/USDT",
               "paper": True, "paper_balance": 1000, "params": {"quote_amount": 10, "interval": 3600}} | kw
    return await client.post("/api/instances", headers=h, json=payload)


# #1 模拟盘负手续费/负滑点被拒
async def test_paper_config_rejects_negative(client, user_h):
    r = await _instance(client, user_h, name="neg-fee", paper_config={"fee_rate": -0.5, "slippage": -1})
    assert r.status_code == 422 or body(r)["code"] != 0
    r = await _instance(client, user_h, name="bad-key", paper_config={"fee_rate": 0.001, "evil": 1})
    assert r.status_code == 422 or body(r)["code"] != 0
    r = await _instance(client, user_h, name="ok-fee", paper_config={"fee_rate": 0.001, "slippage": 0.0005})
    assert body(r)["code"] == 0, r.text


# #2 名称纯空格被拒；前后空格被去掉
async def test_instance_name_strip(client, user_h):
    r = await _instance(client, user_h, name="   ")
    assert r.status_code == 422 or body(r)["code"] != 0
    r = await _instance(client, user_h, name="  trimmed  ")
    assert body(r)["code"] == 0 and body(r)["data"]["name"] == "trimmed"
    iid = body(r)["data"]["id"]
    r = await client.patch(f"/api/instances/{iid}", headers=user_h, json={"name": "  "})
    assert r.status_code == 422 or body(r)["code"] != 0


# #3 int 参数传小数报错，不截断
async def test_int_param_rejects_fraction(client, user_h):
    r = await client.post("/api/instances", headers=user_h, json={
        "name": "frac", "strategy_id": "trend_pullback", "symbol": "BTC/USDT", "paper": True, "paper_balance": 1000,
        "params": {"trend_period": 50.7}})
    assert body(r)["code"] != 0


# #9 初始资金上限
async def test_paper_balance_bounds(client, user_h):
    for v in (0, -5, 1e30):
        r = await _instance(client, user_h, name=f"bal-{v}", paper_balance=v)
        assert r.status_code == 422 or body(r)["code"] != 0, v


# #4 系统配置范围
async def test_config_range(client, admin_h):
    r = await client.patch("/api/super/configs", headers=admin_h, json={"configs": {"instance.max_per_user": -5}})
    assert body(r)["code"] == 40003
    r = await client.patch("/api/super/configs", headers=admin_h, json={"configs": {"risk.max_drawdown": 5}})
    assert body(r)["code"] == 40003


# #5 登出只撤当前设备；access 随会话一起失效
async def test_logout_current_device_only(client):
    await client.post("/api/auth/register", json={"username": "twodev", "password": "Twodev1234"})
    a = body(await client.post("/api/auth/login", json={"username": "twodev", "password": "Twodev1234"}))["data"]
    b = body(await client.post("/api/auth/login", json={"username": "twodev", "password": "Twodev1234"}))["data"]
    ha = {"Authorization": "Bearer " + a["access_token"]}
    r = await client.post("/api/auth/logout", headers=ha, json={})
    assert body(r)["code"] == 0
    # A 的 access 立即失效
    assert (await client.get("/api/auth/me", headers=ha)).status_code == 401
    # B 不受影响
    hb = {"Authorization": "Bearer " + b["access_token"]}
    assert body(await client.get("/api/auth/me", headers=hb))["code"] == 0
    assert body(await client.post("/api/auth/refresh", json={"refresh_token": b["refresh_token"]}))["code"] == 0


# #11 #12 强平：停止状态也能平；结果如实返回
async def test_force_close_reports_truth(client, user_h):
    r = await _instance(client, user_h, name="fc-truth")
    iid = body(r)["data"]["id"]
    await client.post(f"/api/instances/{iid}/start", headers=user_h)
    for _ in range(60):
        pos = body(await client.get(f"/api/instances/{iid}/positions", headers=user_h))["data"]
        items = pos.get("items") if isinstance(pos, dict) else pos
        if items:
            break
        await asyncio.sleep(0.1)
    await client.post(f"/api/instances/{iid}/stop", headers=user_h, json={"action": "keep"})
    # 停止状态下强平应真的成交
    r = await client.post(f"/api/instances/{iid}/positions/force-close", headers=user_h, json={})
    d = body(r)
    assert d["code"] == 0, r.text
    assert d["data"]["failed"] == 0 and d["data"]["results"][0]["order"]["status"] == "filled"
    # 再平一次：已无持仓 → 业务错误，而不是假的 success
    r = await client.post(f"/api/instances/{iid}/positions/force-close", headers=user_h, json={})
    assert body(r)["code"] != 0


# #13 熔断目标必须存在
async def test_breaker_target_must_exist(client, admin_h):
    r = await client.post("/api/admin/circuit-breakers", headers=admin_h, json={"level": "account", "target_id": 999999, "reason": "x"})
    assert body(r)["code"] in (40404, 40401)


# #10 优先级范围
async def test_binding_priority_range(client, user_h):
    me = body(await client.get("/api/auth/me", headers=user_h))["data"]
    r = await client.post("/api/plugin-bindings", headers=user_h, json={
        "plugin_id": "audit_export", "scope": "user", "scope_id": me["id"], "settings": {}, "priority": -99999999999})
    assert r.status_code == 422 or body(r)["code"] != 0


# #14 偏好：非法时区 / 时间格式被拒
async def test_pref_validation(client, user_h):
    for k, v in (("timezone", "Mars/Base"), ("time_format", "99h")):
        r = await client.patch("/api/user/preferences", headers=user_h, json={k: v})
        assert body(r)["code"] == 40003, (k, r.text)
    r = await client.patch("/api/user/preferences", headers=user_h, json={"timezone": "UTC"})
    assert body(r)["code"] == 0


# #14 webhook：内网地址与未知事件被拒
async def test_webhook_validation(client, user_h):
    r = await client.patch("/api/user/notification-settings", headers=user_h, json={"webhook_url": "http://127.0.0.1:9/x"})
    assert body(r)["code"] == 40003
    r = await client.patch("/api/user/notification-settings", headers=user_h, json={"event_types": ["tick"]})
    assert body(r)["code"] == 40003


# #15 API Token 过期时间在过去被拒
async def test_token_past_expiry(client, user_h):
    r = await client.post("/api/user/api-tokens", headers=user_h, json={"name": "old", "scope": "read", "expires_at": "2000-01-01T00:00:00Z"})
    assert body(r)["code"] == 40003


# #16 TOTP 不能重放
async def test_totp_no_replay(client):
    import pyotp

    await client.post("/api/auth/register", json={"username": "replay", "password": "Replay1234"})
    h = {"Authorization": "Bearer " + body(await client.post("/api/auth/login", json={"username": "replay", "password": "Replay1234"}))["data"]["access_token"]}
    secret = body(await client.post("/api/auth/2fa/enable", headers=h))["data"]["secret"]
    await client.post("/api/auth/2fa/verify", headers=h, json={"code": pyotp.TOTP(secret).now()})
    code = pyotp.TOTP(secret).now()
    first = await client.post("/api/auth/login", json={"username": "replay", "password": "Replay1234", "two_factor_code": code})
    second = await client.post("/api/auth/login", json={"username": "replay", "password": "Replay1234", "two_factor_code": code})
    assert body(first)["code"] == 0 and body(second)["code"] != 0


# 后端有、界面没用到 → 补了界面：风控规则 CRUD 带取值校验
async def test_risk_rule_validation(client, user_h):
    r = await client.post("/api/risk-rules", headers=user_h, json={"rule_type": "max_order_value", "rule_value": {"value": -1}})
    assert body(r)["code"] == 40003
    r = await client.post("/api/risk-rules", headers=user_h, json={"rule_type": "symbol_whitelist", "rule_value": {"symbols": []}})
    assert body(r)["code"] == 40003
    r = await client.post("/api/risk-rules", headers=user_h, json={"rule_type": "max_order_value", "rule_value": {"value": 500}})
    assert body(r)["code"] == 0
    lst = body(await client.get("/api/risk-rules", headers=user_h))["data"]
    assert "shapes" in lst and any(x["rule_type"] == "max_order_value" for x in lst["items"])


# 通知持久化：关键事件写入通知表，能通过接口取回
async def test_notifications_persisted(client, user_h):
    from bamy.engine.runtime import engine

    me = body(await client.get("/api/auth/me", headers=user_h))["data"]
    await engine.push("instance.error", {"instance_id": 1, "owner_id": me["id"], "error": "测试错误"})
    items = body(await client.get("/api/notifications", headers=user_h))["data"]["items"]
    assert any(n["title"].startswith("实例出错") for n in items)


# 实例详情"策略状态"：接口返回 ui.status 与最近一行日志
async def test_status_exposes_strategy_status(client, user_h):
    import json as _json

    from bamy.engine.runtime import engine

    iid = body(await _instance(client, user_h, name="status-inst"))["data"]["id"]
    await engine.state_set(iid, "ui.status", _json.dumps({"summary": "等待：趋势向下", "next_at": 1, "at": 0}))
    engine.log(iid, "info", "心跳测试", module="engine")
    d = body(await client.get(f"/api/instances/{iid}/status", headers=user_h))["data"]
    assert d["strategy_status"]["summary"] == "等待：趋势向下"
    assert d["last_log"]["message"] == "心跳测试"


# 换策略：自动生成的名字跟着改
async def test_change_strategy_renames_auto_name(client, user_h):
    from bamy.extensions.registry import registry

    old = registry.get_strategy("dca_strategy").meta["name"]
    iid = body(await _instance(client, user_h, name=f"{old}-ab12"))["data"]["id"]
    r = await client.post(f"/api/instances/{iid}/change-strategy", headers=user_h,
                          json={"strategy_id": "trend_pullback", "params": {}})
    assert body(r)["code"] == 0, r.text
    assert body(r)["data"]["name"] == registry.get_strategy("trend_pullback").meta["name"] + "-ab12"


# 详情页并发首次加载模拟账户：不应撞唯一约束返回 409
async def test_paper_book_concurrent_first_load(client, user_h):
    from bamy.engine.runtime import engine

    iid = body(await _instance(client, user_h, name="race-book"))["data"]["id"]
    engine._books.pop(iid, None)
    rs = await asyncio.gather(*(client.get(f"/api/instances/{iid}/{p}", headers=user_h)
                                for p in ("positions", "status", "paper-account", "positions", "status")))
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs if r.status_code != 200]


# 服务器没开实盘：建实盘实例直接说清楚，而不是建出来启动才报错
async def test_live_instance_blocked_when_disabled(client, user_h):
    r = await client.post("/api/instances", headers=user_h, json={
        "name": "live-off", "strategy_id": "dca_strategy", "symbol": "BTC/USDT", "paper": False, "account_id": 1,
        "params": {"quote_amount": 10, "interval": 3600}})
    assert body(r)["code"] == 42232 and "ENABLE_LIVE_TRADING" in body(r)["message"]
    assert body(await client.get("/api/setup/status"))["data"]["live_trading"] is False


# 审计/订单按 id 过滤：查询串是字符串，必须转成整数（PostgreSQL 下 bigint = varchar 会报数据库错误）
async def test_id_filters_are_ints(client, user_h):
    from bamy.services.audit import query_audit

    for path in ("/api/user/audit-logs?target_type=account&target_id=3", "/api/user/audit-logs?actor_id=1",
                 "/api/orders?instance_id=1", "/api/orders?account_id=3"):
        r = await client.get(path, headers=user_h)
        assert body(r)["code"] == 0, (path, r.text)
    r = await client.get("/api/user/audit-logs?target_id=abc", headers=user_h)
    assert body(r)["code"] == 40002
    from sqlalchemy.dialects import postgresql

    captured = {}

    class FakeDB:
        async def scalar(self, stmt):
            captured["sql"] = stmt
            return 0

        async def execute(self, stmt):
            class R:
                def scalars(self):
                    return self

                def all(self):
                    return []
            return R()

    await query_audit(FakeDB(), {"target_id": "3"}, 1, 10)
    params = captured["sql"].compile(dialect=postgresql.dialect()).params
    assert 3 in params.values() and "3" not in params.values(), params


# 系统更新：只有超级管理员能用；请求只写 check/apply 到共享目录，不在应用里执行任何命令
async def test_system_update_request(client, admin_h, user_h, tmp_path, monkeypatch):
    import json as _json

    monkeypatch.setenv("UPDATE_DIR", str(tmp_path))
    assert (await client.get("/api/super/update", headers=user_h)).status_code == 403
    d = body(await client.get("/api/super/update", headers=admin_h))["data"]
    assert d["installed"] is False and d["state"] == "unknown"
    r = await client.post("/api/super/update", headers=admin_h, json={"action": "rm -rf /"})
    assert body(r)["code"] == 40003
    r = await client.post("/api/super/update", headers=admin_h, json={"action": "apply"})
    assert body(r)["code"] == 0 and (tmp_path / "request").read_text() == "apply"
    (tmp_path / "request").unlink()
    (tmp_path / "status.json").write_text(_json.dumps({"state": "running", "message": "构建前端", "updater": True}))
    r = await client.post("/api/super/update", headers=admin_h, json={"action": "check"})
    assert body(r)["code"] == 40902
    (tmp_path / "update.log").write_text("a\nb\n")
    d = body(await client.get("/api/super/update", headers=admin_h))["data"]
    assert d["installed"] and d["log"] == ["a", "b"]
