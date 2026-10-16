"""模拟盘接口（《接口契约》第十节，22.2）：账户、快照、多盘对比、指标。"""
from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select

from bamy.api.deps import Ctx, current, iso, own_instance
from bamy.core.errors import ApiError
from bamy.core.response import csv_safe, ok
from bamy.db.models import Instance, Order, PaperAccount, PaperSnapshot
from bamy.engine.runtime import engine

router = APIRouter()

MAX_COMPARE = 10
BUCKET = {"minute": 60, "hour": 3600, "day": 86400}


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None) if v else None


@router.get("/api/instances/{instance_id}/paper-account")
async def paper_account(instance_id: int, ctx: Ctx = Depends(current)):
    inst = await own_instance(ctx.db, ctx.user, instance_id, admin_ok=ctx.is_admin)
    if not inst.paper:
        raise ApiError(42260, "不是模拟盘实例")
    run = engine.running.get(instance_id)
    book = run.book if run and run.book else await engine._get_book(instance_id)
    acct = (await ctx.db.execute(select(PaperAccount).where(PaperAccount.instance_id == instance_id))).scalar_one()
    prices = await engine._prices(book)
    equity = book.equity(prices)
    return ok({"instance_id": instance_id, "balance": float(book.balance), "equity": float(equity),
               "realized_pnl": float(book.realized_pnl), "unrealized_pnl": float(book.unrealized(prices)),
               "initial_balance": float(acct.initial_balance),
               "peak_equity": float(max(acct.peak_equity or 0, equity)),
               "fee_rate": book.config.fee_rate, "slippage": book.config.slippage,
               "positions": book.positions_list(prices, inst.symbol.split("/")[1]),
               "open_orders": [{"client_order_id": k, **v} for k, v in book.open_orders.items()]})


def _aggregate(rows: list[PaperSnapshot], granularity: str | None) -> list[PaperSnapshot]:
    if not granularity or granularity not in BUCKET:
        return rows
    size = BUCKET[granularity]
    buckets: dict[int, PaperSnapshot] = {}
    for r in rows:
        key = int(r.snapshot_at.timestamp()) // size
        buckets[key] = r  # 取每个桶的最后一个点
    return [buckets[k] for k in sorted(buckets)]


@router.get("/api/instances/{instance_id}/paper-snapshots")
async def paper_snapshots(instance_id: int, ctx: Ctx = Depends(current), start_time: str | None = None,
                          end_time: str | None = None, granularity: str | None = None):
    inst = await own_instance(ctx.db, ctx.user, instance_id, admin_ok=ctx.is_admin)
    if not inst.paper:
        raise ApiError(42260, "不是模拟盘实例")
    rows = await _snapshots(ctx, [instance_id], _dt(start_time), _dt(end_time))
    items = _aggregate(rows.get(instance_id, []), granularity)
    return ok({"instance_id": instance_id, "granularity": granularity or "raw",
               "items": [{"snapshot_at": iso(r.snapshot_at), "equity": float(r.equity), "balance": float(r.balance),
                          "realized_pnl": float(r.realized_pnl or 0), "unrealized_pnl": float(r.unrealized_pnl or 0),
                          "peak_equity": float(r.peak_equity or 0), "drawdown": float(r.drawdown or 0)} for r in items]})


async def _snapshots(ctx: Ctx, ids: list[int], start: datetime | None, end: datetime | None) -> dict[int, list[PaperSnapshot]]:
    stmt = select(PaperSnapshot).where(PaperSnapshot.instance_id.in_(ids))
    if start:
        stmt = stmt.where(PaperSnapshot.snapshot_at >= start)
    if end:
        stmt = stmt.where(PaperSnapshot.snapshot_at <= end)
    out: dict[int, list[PaperSnapshot]] = defaultdict(list)
    for r in (await ctx.db.execute(stmt.order_by(PaperSnapshot.snapshot_at))).scalars():
        out[r.instance_id].append(r)
    return out


async def _parse_ids(ctx: Ctx, instance_ids: str) -> list[Instance]:
    try:
        ids = [int(x) for x in instance_ids.split(",") if x.strip()]
    except ValueError as exc:
        raise ApiError(42262, "instance_ids 格式错误") from exc
    if not ids or len(ids) > MAX_COMPARE:
        raise ApiError(42262, f"对比对象需为 1-{MAX_COMPARE} 个")
    out = []
    for iid in ids:
        out.append(await own_instance(ctx.db, ctx.user, iid, admin_ok=ctx.is_admin))
    return out


async def compare_data(ctx: Ctx, instance_ids: str, start_time: str | None, end_time: str | None,
                       normalize: bool, granularity: str | None = None) -> dict[str, Any]:
    insts = await _parse_ids(ctx, instance_ids)
    start, end = _dt(start_time), _dt(end_time)
    snaps = await _snapshots(ctx, [i.id for i in insts], start, end)
    gran = granularity
    if not gran:
        span = 0
        all_ts = [r.snapshot_at for rows in snaps.values() for r in rows]
        if all_ts:
            span = (max(all_ts) - min(all_ts)).total_seconds()
        gran = "minute" if span <= 2 * 86400 else "hour" if span <= 60 * 86400 else "day"
    size = BUCKET[gran]
    # 统一时间轴：所有实例出现过的桶的并集，缺失点向前填充
    series_points: dict[int, dict[int, float]] = {}
    keys: set[int] = set()
    for inst in insts:
        pts: dict[int, float] = {}
        for r in snaps.get(inst.id, []):
            k = int(r.snapshot_at.timestamp()) // size
            pts[k] = float(r.equity)
        series_points[inst.id] = pts
        keys |= set(pts)
    timeline = sorted(keys)
    series = []
    for inst in insts:
        pts = series_points[inst.id]
        values: list[float | None] = []
        last = None
        for k in timeline:
            last = pts.get(k, last)
            values.append(last)
        base = next((v for v in values if v), None)
        if normalize and base:
            values = [round(v / base, 6) if v is not None else None for v in values]
        series.append({"instance_id": inst.id, "name": inst.name, "paper": bool(inst.paper),
                       "account_id": inst.account_id, "values": values})
    return {"timeline": [datetime.utcfromtimestamp(k * size).isoformat() + "Z" for k in timeline],
            "series": series, "normalize": normalize, "granularity": gran,
            # 含实盘实例时提示：模拟盘不挂账号级插件，拦截结果可能与实盘不同（《模拟盘设计》14.2）
            "account_plugin_notice": any(not i.paper for i in insts) and any(i.paper for i in insts)}


@router.get("/api/paper/compare")
async def compare(instance_ids: str, ctx: Ctx = Depends(current), start_time: str | None = None,
                  end_time: str | None = None, normalize: bool = False, granularity: str | None = None):
    return ok(await compare_data(ctx, instance_ids, start_time, end_time, normalize, granularity))


@router.get("/api/paper/compare/export")
async def compare_export(instance_ids: str, ctx: Ctx = Depends(current), start_time: str | None = None,
                         end_time: str | None = None, normalize: bool = False):
    data = await compare_data(ctx, instance_ids, start_time, end_time, normalize)
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(["time"] + [csv_safe(f"{s['name']}#{s['instance_id']}") for s in data["series"]])
    for idx, ts in enumerate(data["timeline"]):
        w.writerow([ts] + [s["values"][idx] for s in data["series"]])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=paper-compare.csv"})


def _metrics(equities: list[float], trades: list[float]) -> dict[str, Any]:
    if not equities:
        return {"total_return": None, "max_drawdown": None, "sharpe": None}
    peak, mdd = equities[0], 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    rets = [(b - a) / a for a, b in zip(equities, equities[1:]) if a]
    sharpe = None
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        std = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
        sharpe = round(mean / std * math.sqrt(len(rets)), 4) if std else None
    return {"total_return": round((equities[-1] - equities[0]) / equities[0], 6) if equities[0] else None,
            "max_drawdown": round(mdd, 6), "sharpe": sharpe}


@router.get("/api/paper/compare/metrics")
async def compare_metrics(instance_ids: str, ctx: Ctx = Depends(current)):
    insts = await _parse_ids(ctx, instance_ids)
    snaps = await _snapshots(ctx, [i.id for i in insts], None, None)
    out = []
    for inst in insts:
        eq = [float(r.equity) for r in snaps.get(inst.id, [])]
        acct = (await ctx.db.execute(select(PaperAccount).where(PaperAccount.instance_id == inst.id))).scalar_one_or_none()
        if acct and eq:
            eq = [float(acct.initial_balance)] + eq
        # 按卖出成交估算单笔盈亏：卖出价相对此前买入均价
        orders = (await ctx.db.execute(select(Order).where(Order.instance_id == inst.id, Order.status == "filled")
                                       .order_by(Order.id))).scalars().all()
        qty, cost, pnls = 0.0, 0.0, []
        for o in orders:
            a, p = float(o.filled_amount or 0), float(o.filled_price or 0)
            if o.side == "buy":
                qty, cost = qty + a, cost + a * p
            elif qty > 0:
                avg = cost / qty
                take = min(a, qty)
                pnls.append((p - avg) * take - float(o.fee or 0))
                cost -= avg * take
                qty -= take
        wins = [x for x in pnls if x > 0]
        losses = [-x for x in pnls if x < 0]
        running = None
        if inst.last_started_at:
            endt = datetime.utcnow() if inst.status in {"running", "paused"} else (inst.last_stopped_at or datetime.utcnow())
            running = round((endt - inst.last_started_at).total_seconds() / 3600, 2)
        out.append({"instance_id": inst.id, "name": inst.name, "strategy_id": inst.strategy_id,
                    "symbol": inst.symbol, "initial_balance": float(acct.initial_balance) if acct else None,
                    "equity": eq[-1] if eq else None, **_metrics(eq, pnls),
                    "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
                    "profit_factor": round(sum(wins) / sum(losses), 4) if losses else None,
                    "order_count": await ctx.db.scalar(select(func.count(Order.id)).where(Order.instance_id == inst.id)) or 0,
                    "running_hours": running})
    return ok({"metrics": out})
