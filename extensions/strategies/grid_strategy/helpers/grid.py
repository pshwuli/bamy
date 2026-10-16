"""网格价格计算（纯函数，不依赖框架）。"""
from __future__ import annotations


def grid_prices(lower: float, upper: float, num: int) -> list[float]:
    """把 [lower, upper] 等差分成 num 格，返回 num + 1 条网格线（升序）。"""
    if num < 1:
        raise ValueError("grid_num 至少为 1")
    if lower <= 0 or upper <= lower:
        raise ValueError("价格区间不合法：需要 0 < lower_price < upper_price")
    step = (upper - lower) / num
    return [round(lower + step * i, 8) for i in range(num + 1)]


def grid_index(price: float, lines: list[float]) -> int:
    """返回价格所在的格子下标：低于下界为 -1，高于上界为 len(lines) - 1。"""
    if price < lines[0]:
        return -1
    for i in range(len(lines) - 1):
        if lines[i] <= price < lines[i + 1]:
            return i
    return len(lines) - 1


def stop_loss_price(lower: float, pct: float) -> float:
    """止损线：下界再向下 pct%。"""
    return round(lower * (1 - pct / 100), 8)
