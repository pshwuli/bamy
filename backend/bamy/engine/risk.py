"""风控中心：单笔/单日限额、频率、白名单/黑名单、熔断、回撤、冷却（《引擎设计》风控一节）。

风控固定先于插件拦截执行。
"""
from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bamy.db.models import CircuitBreaker, Order, RiskRule
from bamy.services import system_config

RULE_TYPES = {
    "max_order_value",       # 单笔名义价值上限 {"value": 1000}
    "max_daily_value",       # 单日名义价值上限
    "max_daily_orders",      # 单日下单次数上限
    "max_orders_per_minute",
    "symbol_whitelist",      # {"symbols": [...]}
    "symbol_blacklist",
    "max_drawdown",          # {"value": 0.3}
    "max_daily_loss",        # {"value": 0.1}
    "max_symbol_loss",       # {"value": 100}
    "symbol_cooldown",       # {"seconds": 300}
    "strategy_cooldown",
}


class RiskRejected(Exception):
    def __init__(self, code: int, message: str, rule: str | None = None):
        self.code = code
        self.rule = rule
        super().__init__(message)


class RiskEngine:
    def __init__(self) -> None:
        self._recent: dict[int, deque[float]] = defaultdict(deque)   # 实例/账号近 60 秒下单时间
        self._cooldown_until: dict[str, float] = {}

    # ---- 熔断 ----
    async def active_breakers(self, db: AsyncSession, *, owner_id: int | None, account_id: int | None) -> list[CircuitBreaker]:
        rows = (await db.execute(select(CircuitBreaker).where(CircuitBreaker.recovered_at.is_(None)))).scalars().all()
        hit = []
        for cb in rows:
            if cb.level == "global":
                hit.append(cb)
            elif cb.level == "user" and owner_id is not None and cb.target_id == owner_id:
                hit.append(cb)
            elif cb.level == "account" and account_id is not None and cb.target_id == account_id:
                hit.append(cb)
        return hit

    def set_cooldown(self, key: str, seconds: float) -> None:
        self._cooldown_until[key] = time.time() + seconds

    async def _rules(self, db: AsyncSession, owner_id: int, account_id: int | None) -> list[RiskRule]:
        stmt = select(RiskRule).where(RiskRule.enabled.is_(True))
        rows = (await db.execute(stmt)).scalars().all()
        out = []
        for r in rows:
            if r.owner_id is not None and r.owner_id != owner_id:
                continue
            if r.account_id is not None and r.account_id != account_id:
                continue
            out.append(r)
        return out

    async def check_order(self, db: AsyncSession, *, instance, side: str, amount: float, price: float,
                          reduce_only: bool) -> None:
        """前置风控。reduce_only（平仓/卖出已持仓）在熔断期间仍允许。"""
        owner_id, account_id = instance.owner_id, instance.account_id
        breakers = await self.active_breakers(db, owner_id=owner_id, account_id=account_id)
        if breakers and not reduce_only:
            raise RiskRejected(40910, f"熔断中：{breakers[0].reason}", "circuit_breaker")

        notional = float(amount) * float(price or 0)
        key_inst = instance.id
        window = self._recent[key_inst]
        now = time.time()
        while window and now - window[0] > 60:
            window.popleft()

        per_minute = int(system_config.get("order.max_per_minute", 60))
        per_day = int(system_config.get("order.max_per_day", 1000))
        if len(window) >= per_minute:
            raise RiskRejected(42248, "每分钟下单次数超限", "order.max_per_minute")

        day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        scope_col = Order.account_id == account_id if account_id is not None else Order.instance_id == instance.id
        daily_count = await db.scalar(select(func.count(Order.id)).where(
            scope_col, Order.created_at >= day_start, Order.status != "rejected")) or 0
        if daily_count >= per_day:
            raise RiskRejected(42248, "单日下单次数超限", "order.max_per_day")

        for rule in await self._rules(db, owner_id, account_id):
            value = json.loads(rule.rule_value or "{}")
            t = rule.rule_type
            if t == "max_order_value" and not reduce_only and notional > float(value.get("value", 0) or 0) > 0:
                raise RiskRejected(42247, f"单笔金额 {notional:.2f} 超出上限 {value['value']}", t)
            if t == "max_daily_value" and not reduce_only:
                spent = await db.scalar(select(func.coalesce(func.sum(Order.amount * func.coalesce(Order.filled_price, Order.price, 0)), 0)).where(
                    scope_col, Order.created_at >= day_start, Order.status.in_(("filled", "partial", "submitted")))) or 0
                if float(spent) + notional > float(value.get("value", 0) or 0) > 0:
                    raise RiskRejected(42248, "超出单日金额上限", t)
            if t == "max_daily_orders" and daily_count >= int(value.get("value", 0) or 0) > 0:
                raise RiskRejected(42248, "超出单日下单次数", t)
            if t == "max_orders_per_minute" and len(window) >= int(value.get("value", 0) or 0) > 0:
                raise RiskRejected(42248, "超出每分钟下单次数", t)
            if t == "symbol_whitelist" and instance.symbol not in set(value.get("symbols") or []):
                raise RiskRejected(42249, "交易对不在白名单", t)
            if t == "symbol_blacklist" and instance.symbol in set(value.get("symbols") or []):
                raise RiskRejected(42249, "交易对在黑名单", t)
            if t in {"symbol_cooldown", "strategy_cooldown"} and not reduce_only:
                key = f"{t}:{instance.symbol if t == 'symbol_cooldown' else instance.strategy_id}:{owner_id}"
                if self._cooldown_until.get(key, 0) > now:
                    raise RiskRejected(40911, "冷却中", t)
        window.append(now)

    def after_fill(self, instance, rules: list[RiskRule]) -> None:
        for rule in rules:
            value = json.loads(rule.rule_value or "{}")
            if rule.rule_type == "symbol_cooldown":
                self.set_cooldown(f"symbol_cooldown:{instance.symbol}:{instance.owner_id}", float(value.get("seconds", 0)))
            if rule.rule_type == "strategy_cooldown":
                self.set_cooldown(f"strategy_cooldown:{instance.strategy_id}:{instance.owner_id}", float(value.get("seconds", 0)))

    async def check_drawdown(self, db: AsyncSession, instance, equity: float, peak: float, day_start_equity: float | None) -> RiskRejected | None:
        """回撤与单日亏损检查，返回触发的结果（由调用方决定熔断/停机）。"""
        max_dd = float(system_config.get("risk.max_drawdown", 0.3))
        max_loss = float(system_config.get("risk.max_daily_loss", 0.1))
        for rule in await self._rules(db, instance.owner_id, instance.account_id):
            v = json.loads(rule.rule_value or "{}")
            if rule.rule_type == "max_drawdown":
                max_dd = float(v.get("value", max_dd))
            if rule.rule_type == "max_daily_loss":
                max_loss = float(v.get("value", max_loss))
        if peak > 0 and (peak - equity) / peak >= max_dd > 0:
            return RiskRejected(42271, f"达到最大回撤 {max_dd:.0%}", "max_drawdown")
        if day_start_equity and day_start_equity > 0 and (day_start_equity - equity) / day_start_equity >= max_loss > 0:
            return RiskRejected(42272, f"达到单日最大亏损 {max_loss:.0%}", "max_daily_loss")
        return None


risk_engine = RiskEngine()


def decimal(v: Any) -> Decimal:
    return Decimal(str(v or 0))


def today() -> datetime:
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


def minutes_ago(n: int) -> datetime:
    return datetime.utcnow() - timedelta(minutes=n)
