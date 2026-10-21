"""下单前的账号状态闸门。

账号探活连续失败会被标成 error，并自动暂停其下的实例；暂停时策略要先平仓，
可平仓单也要过"账号必须在线"这一关，结果平仓单全被拒，只能靠交易所上的止损单兜底。

只减仓的单子只会降低风险，所以账号处于 error / offline（探活不通、状态未知）时放行；
开仓单仍然必须账号在线。invalid（密钥失效）和 disabled（被停用）一律不放行，因为那时下单本来就会失败或不该下。
"""
from __future__ import annotations

from typing import Any

DEGRADED = frozenset({"error", "offline"})


def account_blocks_order(account: Any, reduce_only: bool) -> bool:
    """True 表示这笔单不能下。account 为空（已删除）一律不能下。"""
    if account is None:
        return True
    if account.status == "online":
        return False
    return not (reduce_only and account.status in DEGRADED)
