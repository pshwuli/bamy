"""实盘实例的资金口径：本金、权益、收益率（按实例自己的成交算，不用账号总余额）。

为什么不用"账号余额"：一个币安账号最多绑 10 个实例，还可能有用户自己手动下的单、转进转出；
用账号余额当某个实例的"总资产"会把别的实例和人工操作的盈亏算进来，收益率没有意义。

口径（与模拟盘一致）：
- 本金 initial：实例上设置的 live_capital（建实例或改参数时填，存在 paper_config.live_capital）；
  没填时为空，页面只显示盈亏金额、不显示收益率。
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


def live_capital(inst: Instance) -> float | None:
    try:
        v = json.loads(inst.paper_config or "{}").get("live_capital")
    except (TypeError, ValueError):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


async def live_pnl(db: AsyncSession, inst: Instance) -> dict[str, Any]:
    """返回 {initial, equity, realized, unrealized, pnl, fees, fee_other}；fee_other=True 表示有非 USDT 手续费没计入。"""
    rows = (await db.execute(select(Order).where(Order.instance_id == inst.id, Order.status.in_(("filled", "partial")))
                             .order_by(Order.id))).scalars().all()
    futures = inst.market_type == "futures"
    pos: dict[str, list[float]] = {}          # symbol -> [净数量（多为正、空为负）, 均价]
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
        net, avg = pos.get(o.symbol, [0.0, 0.0])
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
        pos[o.symbol] = [net, avg]
    unrealized = 0.0
    for sym, (net, avg) in pos.items():
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
    initial = live_capital(inst)
    return {"initial": initial, "equity": initial + pnl if initial else None, "realized": realized - fees,
            "unrealized": unrealized, "pnl": pnl, "fees": fees, "fee_other": fee_other}
