"""单元测试：模拟撮合、参数 schema、限流、插件拦截链的 fail_mode 语义。"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------- 模拟撮合
def _book(balance=1000):
    from bamy.engine.paper import PaperBook, PaperConfig

    return PaperBook(Decimal(balance), {}, {}, Decimal(0), PaperConfig(fee_rate=0.001, slippage=0.0))


def test_paper_buy_sell_pnl():
    b = _book()
    r = b.fill("c1", "BTC/USDT", "buy", 0.01, 50000)
    assert r.status == "filled" and b.positions["BTC"] == Decimal("0.01")
    assert b.balance == Decimal(1000) - Decimal("500") - Decimal("0.5")
    b.fill("c2", "BTC/USDT", "sell", 0.01, 51000)
    assert "BTC" not in b.positions
    assert b.realized_pnl == Decimal("10") - Decimal("0.51")


def test_paper_insufficient():
    from bamy.engine.adapters import ExchangeError

    b = _book(10)
    with pytest.raises(ExchangeError) as e:
        b.fill("c1", "BTC/USDT", "buy", 1, 50000)
    assert e.value.code == 42261
    with pytest.raises(ExchangeError) as e:
        b.fill("c2", "BTC/USDT", "sell", 1, 50000)
    assert e.value.code == 42244


def test_paper_limit_match_and_cancel():
    b = _book()
    b.place_limit("l1", "BTC/USDT", "buy", 0.01, 49000)
    assert b.match("BTC/USDT", 49500) == []
    filled = b.match("BTC/USDT", 48900)
    assert filled and filled[0][1].filled_price == 49000
    b.place_limit("l2", "BTC/USDT", "buy", 0.001, 40000)
    assert b.cancel("l2") and not b.cancel("l2")


# ---------------------------------------------------------------- schema
SCHEMA = [
    {"key": "n", "type": "int", "required": True, "min": 1, "max": 10},
    {"key": "flag", "type": "bool", "default": False},
    {"key": "pct", "type": "percent", "default": 5, "depends_on": {"key": "flag", "value": True}},
    {"key": "with_default", "type": "int", "required": True, "default": 3},
]


def test_schema_validate():
    from bamy.extensions.schema import SchemaError, validate

    out = validate(SCHEMA, {"n": "4"})
    assert out == {"n": 4, "flag": False, "with_default": 3}
    out = validate(SCHEMA, {"n": 4, "flag": True})
    assert out["pct"] == 5
    with pytest.raises(SchemaError) as e:
        validate(SCHEMA, {})
    assert e.value.code == 42223
    with pytest.raises(SchemaError) as e:
        validate(SCHEMA, {"n": 99})
    assert e.value.code == 42222
    with pytest.raises(SchemaError) as e:
        validate(SCHEMA, {"n": "abc"})
    assert e.value.code == 40002


# ---------------------------------------------------------------- 限流
def test_rate_limiter():
    from bamy.core.ratelimit import RateLimiter

    rl = RateLimiter()
    assert rl.rule_for("/api/auth/login", "POST")[2] == 10
    assert rl.rule_for("/api/admin/users", "GET")[2] == 300
    assert rl.rule_for("/api/instances", "GET")[2] == 600
    assert all(rl.hit("k", 60, 3) == 0 for _ in range(3))
    assert rl.hit("k", 60, 3) > 0


# ---------------------------------------------------------------- 插件拦截链
class _Obj:
    def __init__(self, behavior):
        self.behavior = behavior

    async def handler(self, payload):
        from bamy.extensions.base import ALLOW, DENY, MODIFY

        if self.behavior == "slow":
            await asyncio.sleep(1)
        if self.behavior == "boom":
            raise RuntimeError("boom")
        if self.behavior == "deny":
            return DENY("no")
        if self.behavior == "modify":
            return MODIFY({**payload, "amount": 1, "symbol": "HACK/USDT"})
        if self.behavior == "junk":
            return 42
        return ALLOW


def _hub(behavior, fail_mode):
    from bamy.engine.pluginhub import LiveBinding, PluginHub

    hub = PluginHub()
    binding = SimpleNamespace(id=1, plugin_id="p", scope="global", scope_id=None, owner_id=1, priority=1,
                              enabled=True, status="enabled")
    ext = SimpleNamespace(loaded=True, meta={"name": "P"},
                          checkpoint_hooks=lambda: [{"checkpoint": "order.before_submit", "handler": "handler",
                                                     "fail_mode": fail_mode, "timeout_ms": 50}])
    live = LiveBinding.__new__(LiveBinding)
    live.binding, live.obj, live.lock = binding, _Obj(behavior), asyncio.Lock()

    async def _record(*a, **k):
        return None

    hub._record = _record
    hub.effective = lambda **k: [(live, "global")]
    return hub, ext


@pytest.mark.parametrize("behavior,fail_mode,allowed,code", [
    ("allow", "open", True, None),
    ("deny", "open", False, 42290),
    ("slow", "open", True, None),
    ("slow", "closed", False, 42291),
    ("boom", "open", True, None),
    ("boom", "closed", False, 42292),
    ("junk", "closed", False, 42292),
])
async def test_intercept_fail_mode(monkeypatch, behavior, fail_mode, allowed, code):
    from bamy.engine import pluginhub as ph

    hub, ext = _hub(behavior, fail_mode)
    monkeypatch.setattr(ph.registry, "get_plugin", lambda pid: ext)
    res = await hub.intercept("order.before_submit", {"symbol": "BTC/USDT", "amount": 5, "side": "buy"},
                              owner_id=1, account_id=None)
    assert res.allowed is allowed
    if code:
        assert res.code == code and res.detail["plugin_id"] == "p"


async def test_intercept_modify_only_editable(monkeypatch):
    from bamy.engine import pluginhub as ph

    hub, ext = _hub("modify", "open")
    monkeypatch.setattr(ph.registry, "get_plugin", lambda pid: ext)
    res = await hub.intercept("order.before_submit", {"symbol": "BTC/USDT", "amount": 5, "side": "buy"},
                              owner_id=1, account_id=None)
    assert res.allowed and res.payload["amount"] == 1
    assert res.payload["symbol"] == "BTC/USDT"  # 交易对不可被插件改写


# ---------------------------------------------------------------- 加密与安全
def test_crypto_roundtrip():
    from bamy.core.crypto import decrypt, encrypt, mask

    token = encrypt("secret-value-123")
    assert token != "secret-value-123" and decrypt(token) == "secret-value-123"
    assert mask("ABCDEFGHIJKL") == "ABCD****IJKL"


def test_password_and_jwt():
    from bamy.core.errors import ApiError
    from bamy.core.security import create_access_token, decode_token, hash_password, verify_password

    h = hash_password("Abcdef123")
    assert verify_password("Abcdef123", h) and not verify_password("x", h)
    a, b = create_access_token(1, "user"), create_access_token(1, "user")
    assert a != b and decode_token(a)["sub"] == "1"
    with pytest.raises(ApiError):
        decode_token(a, expected="refresh")


# ---------------------------------------------------------------- 用户数据流
async def test_userstream_applies_order(monkeypatch):
    from bamy.engine.userstream import UserStream

    applied = []
    us = UserStream()
    us.engine = SimpleNamespace(_apply_result=lambda oid, r: _rec(applied, oid, r))

    class _Res:
        def scalar_one_or_none(self):
            return 7

    class _DB:
        async def execute(self, *_):
            return _Res()

    class _Scope:
        async def __aenter__(self):
            return _DB()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("bamy.engine.userstream.session_scope", lambda: _Scope())
    await us._on_order(1, {"clientOrderId": "bamy1x", "id": "99", "status": "closed", "filled": 0.5, "average": 100})
    assert applied and applied[0][0] == 7 and applied[0][1].status == "filled"


async def _rec(store, oid, r):
    store.append((oid, r))
