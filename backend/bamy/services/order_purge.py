"""平仓之后清理这一段持仓期间产生的已撤销空单。

保护单（止损单）会随行情被反复撤掉再挂新的，一次持仓下来能攒十几条"已撤销、成交量为 0"的记录，
订单列表被刷屏。持仓还没平的时候这些记录是排查用的现场，不能动；等仓位真正平掉（净持仓回到 0）之后，
这一段里没有任何成交的已撤销单就没有保留价值了。

规则：
- 按订单 id 顺序，用已成交的数量推算净持仓；净持仓从 0 变成非 0 的那笔成交开启一段持仓，
  回到 0 的那笔成交结束这一段。
- 只处理已经结束的持仓段：id 大于该段开仓成交、且小于下一段开仓成交的已撤销空单（含平仓之后才撤的最后一张保护单）。
- 成交过一部分再撤的单子（filled_amount > 0）是真实的成交记录，不动；开仓之前撤的单子也不动。
- 持仓还没平时返回空。
"""
from __future__ import annotations

from typing import Any, Iterable

EPS = 1e-12


def _position_side(o: Any, futures: bool) -> str:
    """现货只有一个多头仓位；合约 hedge 订单带 long/short；单向持仓的订单没有方向，多空互相抵消，归到同一个净仓位。"""
    if not futures:
        return "long"
    return o.position_side if o.position_side in {"long", "short"} else "net"


def purgeable_ids(rows: Iterable[Any], *, futures: bool) -> list[int]:
    """rows 是同一个实例、同一个交易对的全部订单（要有 id、side、position_side、status、filled_amount）。"""
    ordered = sorted(rows, key=lambda o: o.id)
    net: dict[str, float] = {}
    cycles: list[list[Any]] = []                         # [开仓成交的订单 id, 是否已平仓]

    def flat() -> bool:
        return all(abs(v) <= EPS for v in net.values())

    for o in ordered:
        qty = float(o.filled_amount or 0)
        if qty <= 0:
            continue
        was_flat = flat()
        ps = _position_side(o, futures)
        net[ps] = net.get(ps, 0.0) + (qty if o.side == "buy" else -qty)
        if not futures and net[ps] < 0:                  # 现货没有净空，卖多了的部分是账号里原本的币
            net[ps] = 0.0
        now_flat = flat()
        if was_flat and not now_flat:
            cycles.append([o.id, False])
        elif not was_flat and now_flat and cycles:
            cycles[-1][1] = True

    out: list[int] = []
    for i, (start, closed) in enumerate(cycles):
        if not closed:
            continue
        end = cycles[i + 1][0] if i + 1 < len(cycles) else None
        for o in ordered:
            if (o.status == "canceled" and float(o.filled_amount or 0) <= 0
                    and o.id > start and (end is None or o.id < end)):
                out.append(o.id)
    return out
