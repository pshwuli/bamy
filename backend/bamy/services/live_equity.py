"""实盘实例的资金口径：本金、权益、收益率（按实例自己的成交算，不用账号总余额）。

为什么不用"账号余额"：一个币安账号最多绑 10 个实例，还可能有用户自己手动下的单、转进转出；
用账号余额当某个实例的"总资产"会把别的实例和人工操作的盈亏算进来，收益率没有意义。

口径（与模拟盘一致）：
- 本金 initial：实例上设置的 live_capital（建实例或改参数时填，存在 paper_config.live_capital）；
  没填时用首次启动时记下的账号 USDT 余额（paper_config.auto_capital，只记一次，之后固定）；
  两者都没有（比如启动时取不到余额）则为空，页面只显示盈亏金额、不显示收益率。
- 已实现盈亏：本实例已成交订单按交易对配对平仓算出，扣手续费（手续费按 USDT 计；以 BNB 等抵扣时按 0 处理并标注）。
- 未实现盈亏：剩余净持仓 × (现价 - 均价)。
- 权益 equity = 本金 + 已实现 + 未实现（没填本金时 equity 为空，只给 pnl）。
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bamy.db.models import Instance, Order
from bamy.engine.market import market


def _cfg(inst: Instance) -> dict[str, Any]:
    try:
        cfg = json.loads(inst.paper_config or "{}")
    except (TypeError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _positive(v: Any) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def live_capital(inst: Instance) -> float | None:
    """用户手动填的本金；没填返回 None。"""
    return _positive(_cfg(inst).get("live_capital"))


def auto_capital(inst: Instance) -> float | None:
    """实例首次启动时记下的账号 USDT 余额，用户没填本金时当基准。"""
    return _positive(_cfg(inst).get("auto_capital"))


def capital_of(inst: Instance) -> float | None:
    """生效的本金：手填优先，否则用启动时记下的基准，都没有返回 None。"""
    return live_capital(inst) or auto_capital(inst)


async def snapshot_auto_capital(db: AsyncSession, inst: Instance, client: Any) -> float | None:
    """实盘实例没填本金、也还没记过基准时，按当前账号 USDT 余额记一次，之后不再改。

    只记一次是为了让基准固定：余额里已经含了已实现盈亏，如果每次刷新都重新取，
    权益 = 余额 + 盈亏 会把盈亏算两遍。取不到余额就不记，页面退回只显示盈亏金额。
    """
    if inst.paper or capital_of(inst) is not None:
        return capital_of(inst)
    try:
        usdt = next((b.total for b in await client.fetch_balance() if b.asset == "USDT"), None)
    except Exception:  # noqa: BLE001
        return None
    base = _positive(usdt)
    if base is None:
        return None
    inst.paper_config = json.dumps(_cfg(inst) | {"auto_capital": base})
    await db.commit()
    return base


async def live_pnl(db: AsyncSession, inst: Instance) -> dict[str, Any]:
    """返回 {initial, equity, realized, unrealized, pnl, fees, fee_other}；fee_other=True 表示有非 USDT 手续费没计入。"""
    rows = (await db.execute(select(Order).where(Order.instance_id == inst.id, Order.status.in_(("filled", "partial")))
                             .order_by(Order.id))).scalars().all()
    futures = inst.market_type == "futures"
    pos: dict[tuple[str, str], list[float]] = {}  # (symbol, position_side) -> [signed quantity, average]
    realized, fees, fee_other = 0.0, 0.0, False
    for o in rows:
        qty = float(o.filled_amount or 0)
        px = float(o.filled_price or o.price or 0)
        if qty <= 0 or px <= 0:
            continue
        cur = (o.fee_currency or "USDT").upper()
        if cur in {"USDT", ""}:
            fees += float(o.fee or 0)
        elif float(o.fee or 0) > 0:
            fee_other = True
        position_side = o.position_side if futures else "long"
        if position_side not in {"long", "short"}:
            # 老订单没有方向时，只能按单向模式的买卖净额兼容。
            position_side = "long" if o.side == "buy" else "short"
        key = (o.symbol, position_side)
        net, avg = pos.get(key, [0.0, 0.0])
        signed = qty if o.side == "buy" else -qty
        if net == 0 or (net > 0) == (signed > 0):
            total = abs(net) + qty
            avg = (abs(net) * avg + qty * px) / total
            net += signed
        else:
            closed = min(abs(net), qty)
            realized += (px - avg) * closed * (1 if net > 0 else -1)
            net += signed
            if abs(net) < 1e-12:
                net, avg = 0.0, 0.0
            elif (net > 0) == (signed > 0):       # 反手：剩余部分按本次成交价开新仓
                avg = px
        if not futures and net < 0:               # 现货不会有净空（多卖的是账号里原本就有的币），不计
            net, avg = 0.0, 0.0
        pos[key] = [net, avg]
    unrealized = 0.0
    for (sym, position_side), (net, avg) in pos.items():
        if abs(net) < 1e-12:
            continue
        tick = market.cached(sym)
        if tick is None:
            try:
                tick = await market.ticker(sym)
            except Exception:  # noqa: BLE001 - 行情拉不到就按均价估（未实现记 0）
                continue
        unrealized += (float(tick["price"]) - avg) * net
    pnl = realized + unrealized - fees
    initial = capital_of(inst)
    return {"initial": initial, "equity": initial + pnl if initial else None, "realized": realized - fees,
            "unrealized": unrealized, "pnl": pnl, "fees": fees, "fee_other": fee_other}


async def backfill_capital(db: AsyncSession, inst: Instance, lp: dict[str, Any]) -> dict[str, Any]:
    """没填本金、启动时也没记过基准的实盘实例（功能上线前启动的）：查看时按账号当前 USDT 余额补记一次。

    余额里已含本实例的已实现盈亏，所以基准 = 余额 - 已实现盈亏，这样权益 ≈ 当前余额，不会把盈亏算两遍。
    补记成功后返回重新算过的结果；取不到余额就原样返回。
    """
    if inst.paper or lp.get("initial") is not None or not inst.account_id:
        return lp
    try:
        from bamy.db.models import Account
        from bamy.engine.client_pool import pool

        client = await pool.get(await db.get(Account, inst.account_id))
        usdt = next((b.total for b in await client.fetch_balance() if b.asset == "USDT"), None)
    except Exception:  # noqa: BLE001
        return lp
    base = _positive((usdt or 0) - lp.get("realized", 0.0))
    if base is None:
        return lp
    inst.paper_config = json.dumps(_cfg(inst) | {"auto_capital": base})
    await db.commit()
    return await live_pnl(db, inst)
