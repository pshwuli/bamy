"""对账差异的判定与落库。

合约账号钱包里的 USDT 是保证金，USDT、USDC 这类计价资产也只是现金，都不是"持仓"。
以前把交易所余额里本地没记录的资产一律记成 position_extra，合约账号每次对账都会多出一条
"USDT 多了"的假差异，而且同一条差异没解决时每轮对账都再新增一条。

- spot_balance_diffs：只对现货账号比较余额；计价资产不算"多出来的持仓"
- record_diffs：同一账号、同一类型、同一对象的差异还没解决时，更新原记录而不是新增
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bamy.db.models import ReconcileDiff

QUOTE_ASSETS = frozenset({"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI"})
TOLERANCE = 1e-8

Diff = tuple[str, dict[str, Any], dict[str, Any]]      # (差异类型, 期望, 实际)


def spot_balance_diffs(expected: dict[str, float], balances: dict[str, float], *,
                       futures_account: bool) -> list[Diff]:
    """本地推算的现货持仓 vs 交易所余额。合约账号的余额是保证金，不参与比较。"""
    if futures_account:
        return []
    out: list[Diff] = []
    for base, qty in expected.items():
        actual = float(balances.get(base, 0.0))
        if abs(actual - qty) > TOLERANCE:
            out.append(("position_short" if actual < qty else "position_extra",
                        {"asset": base, "amount": qty}, {"asset": base, "amount": actual}))
    for base, actual_qty in balances.items():
        if base not in expected and base not in QUOTE_ASSETS and float(actual_qty) > TOLERANCE:
            out.append(("position_extra", {"asset": base, "amount": 0.0},
                        {"asset": base, "amount": float(actual_qty)}))
    return out


def _identity(exp: dict[str, Any]) -> tuple[Any, ...]:
    return (exp.get("asset"), exp.get("symbol"), exp.get("side"))


async def record_diffs(db: AsyncSession, account_id: int, items: list[Diff]) -> int:
    """写入差异，返回新增条数；同一对象的未解决差异只更新期望和实际值。"""
    if not items:
        return 0
    rows = (await db.execute(select(ReconcileDiff).where(
        ReconcileDiff.account_id == account_id, ReconcileDiff.resolved.is_(False)))).scalars().all()
    open_rows: dict[tuple[Any, ...], ReconcileDiff] = {}
    for r in rows:
        try:
            exp = json.loads(r.expected or "{}")
        except ValueError:
            continue
        if isinstance(exp, dict):
            open_rows[(r.diff_type, _identity(exp))] = r
    added = 0
    for diff_type, exp, act in items:
        row = open_rows.get((diff_type, _identity(exp)))
        if row is not None:
            row.expected, row.actual = json.dumps(exp), json.dumps(act)
            continue
        db.add(ReconcileDiff(account_id=account_id, diff_type=diff_type,
                             expected=json.dumps(exp), actual=json.dumps(act)))
        added += 1
    await db.commit()
    return added
