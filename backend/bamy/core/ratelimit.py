"""接口限流（《接口契约》第十七节）：进程内滑动窗口，超限返回 429 + Retry-After。

单进程部署（《部署与运行》约定 uvicorn 单 worker，引擎与接口同进程）下内存计数即可。
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

# (路径前缀, 窗口秒, 次数, 计数维度) ——按顺序匹配，第一条命中即用
RULES: list[tuple[str, int, int, str]] = [
    ("/api/auth/login", 60, 10, "ip"),
    ("/api/auth/register", 3600, 5, "ip"),
    ("/api/auth/forgot-password", 3600, 3, "ip"),
    ("/api/super/", 60, 300, "user"),
    ("/api/admin/", 60, 300, "user"),
    ("/api/view/", 60, 60, "path"),
    ("/api/public/", 60, 60, "ip"),
    ("/view/", 60, 60, "path"),
    ("/api/", 60, 120, "user"),
]


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self.enabled = True

    def rule_for(self, path: str, method: str) -> tuple[str, int, int, str] | None:
        for rule in RULES:
            if path.startswith(rule[0]):
                if rule[0].startswith("/api/auth/") and method != "POST":
                    continue
                return rule
        return None

    def hit(self, key: str, window: int, limit: int) -> int:
        """记一次；未超限返回 0，超限返回建议等待秒数。"""
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            return max(1, int(window - (now - q[0])) + 1)
        q.append(now)
        return 0

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()
