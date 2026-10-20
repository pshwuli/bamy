"""错误信息脱敏：交易所请求失败时，异常文字里常带完整请求地址，其中有签名和密钥参数。

写进日志、实例 last_error、接口响应之前先过一遍，免得这些值被存下来或展示出去。
"""
from __future__ import annotations

import re

_SECRET_PARAMS = re.compile(r"(?i)\b(signature|apikey|api_key|secret|listenkey|x-mbx-apikey)=[^&\s\"']+")


def scrub(text: object) -> str:
    """去掉文字里的签名、密钥类参数值，其余原样保留。"""
    return _SECRET_PARAMS.sub(lambda m: f"{m.group(1)}=***", str(text))


def describe_exc(exc: BaseException, limit: int = 300) -> str:
    """异常类型加脱敏后的内容。只有类型才分得出是超时、断线还是交易所拒绝。"""
    msg = scrub(exc)
    msg = msg if len(msg) <= limit else msg[:limit] + "…"
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__
