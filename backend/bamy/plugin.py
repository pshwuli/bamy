"""框架对插件包暴露的公共接口（插件里 `from bamy.plugin import ...`）。"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from bamy.extensions.base import ALLOW, DENY, MODIFY, BasePlugin, StateStore


class RestrictedHttp:
    """受限 HTTP 客户端：只能访问 network.hosts 白名单内的主机。

    - 只允许 http/https，URL 不得带 userinfo
    - 不跟随重定向（避免白名单主机 302 跳到任意地址）
    - 目标解析出的任一地址是私网/回环/链路本地/保留地址即拒绝（防 SSRF 打内网）
    - 只透传少数安全参数，插件不能改 transport、verify、follow_redirects
    """

    ALLOWED_KW = {"params", "headers", "json", "data", "content", "timeout"}

    def __init__(self, hosts: list[str], timeout_ms: int = 5000) -> None:
        self._hosts = {h.lower().strip(".") for h in (hosts or []) if h}
        self._timeout = min(timeout_ms, 30000) / 1000

    def _check(self, url: str) -> str:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise PermissionError("只允许 http/https")
        if parts.username or parts.password or "@" in parts.netloc:
            raise PermissionError("URL 不得包含用户信息")
        host = (parts.hostname or "").lower().strip(".")
        # "*" = 任意公网主机（仍要过下面的解析检查：内网/本机/保留地址一律拒绝）
        if not host or ("*" not in self._hosts and not any(host == h or host.endswith("." + h) for h in self._hosts)):
            raise PermissionError(f"主机 {host} 不在出网白名单内")
        return host

    async def _check_resolved(self, host: str, port: int) -> None:
        import asyncio
        import ipaddress
        import socket

        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise PermissionError(f"无法解析 {host}") from exc
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global or ip.is_multicast:
                raise PermissionError(f"{host} 解析到非公网地址 {ip}，已拒绝")

    async def request(self, method: str, url: str, **kw: Any) -> Any:
        import httpx

        bad = set(kw) - self.ALLOWED_KW
        if bad:
            raise PermissionError(f"不允许的请求参数：{', '.join(sorted(bad))}")
        host = self._check(url)
        port = httpx.URL(url).port or (443 if url.lower().startswith("https") else 80)
        await self._check_resolved(host, port)
        kw["timeout"] = min(float(kw.get("timeout") or self._timeout), self._timeout)
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            return await client.request(method, url, **kw)

    async def get(self, url: str, **kw: Any) -> Any:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> Any:
        return await self.request("POST", url, **kw)


class PluginContext:
    """插件上下文：框架给插件的唯一能力入口（《插件规范》8.4）。"""

    plugin_id: str
    binding_id: int
    scope: str
    scope_id: int | None
    settings: dict[str, Any]
    data_scope: str
    state: StateStore
    http: RestrictedHttp | None

    # 框架只注入三个窄能力（写日志、发通知、只读概览），以闭包形式持有；插件拿不到引擎对象，也就拿不到任何下单接口
    __slots__ = ("plugin_id", "binding_id", "scope", "scope_id", "settings", "data_scope", "state", "http",
                 "_log_fn", "_notify_fn", "_overview_fn")

    def __init__(self, *, log_fn: Callable[[str, str], None] | None = None,
                 notify_fn: Callable[[str, str, str], Awaitable[None]] | None = None,
                 overview_fn: Callable[[int | None], Awaitable[dict[str, Any]]] | None = None, **kw: Any) -> None:
        for name in ("plugin_id", "binding_id", "scope", "scope_id", "settings", "data_scope", "state", "http"):
            object.__setattr__(self, name, kw.get(name))
        if self.settings is None:
            object.__setattr__(self, "settings", {})
        object.__setattr__(self, "_log_fn", log_fn or (lambda level, msg: None))
        object.__setattr__(self, "_notify_fn", notify_fn)
        object.__setattr__(self, "_overview_fn", overview_fn)

    def __getattribute__(self, name: str) -> Any:
        # 《插件规范》：访问私有成员（ctx._xxx）运行时拦截；框架自身经 object.__getattribute__ 读取
        if name.startswith("_") and not name.startswith("__"):
            raise AttributeError(f"插件不可访问上下文私有成员 {name}")
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("插件上下文只读")

    def setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def log(self, message: str, level: str = "info") -> None:
        object.__getattribute__(self, "_log_fn")(level, f"[plugin:{self.plugin_id}#{self.binding_id}] {message}")

    def log_info(self, message: str) -> None:
        self.log(message, "info")

    def log_warn(self, message: str) -> None:
        self.log(message, "warn")

    def log_error(self, message: str) -> None:
        self.log(message, "error")

    async def notify(self, title: str, body: str, level: str = "info") -> None:
        fn = object.__getattribute__(self, "_notify_fn")
        if fn is not None:
            await fn(title, body, level)

    async def overview(self, owner_id: int | None = None) -> dict[str, Any]:
        """只读概览：本绑定可见用户的实例（状态、权益、初始资金、今日成交笔数）。

        user/account 级绑定只看绑定对象自己的数据；global 级绑定需传 owner_id（通常取事件里的 owner_id）。
        """
        fn = object.__getattribute__(self, "_overview_fn")
        return await fn(owner_id) if fn is not None else {"instances": []}

    def dump(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)


__all__ = ["ALLOW", "DENY", "MODIFY", "BasePlugin", "PluginContext", "RestrictedHttp", "StateStore"]
