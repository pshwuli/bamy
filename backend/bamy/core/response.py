"""统一响应信封（《接口契约》2.5）。"""
from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi.responses import JSONResponse

from bamy.core.errors import ApiError


def request_id() -> str:
    return uuid.uuid4().hex


def ok(data: Any = None, status_code: int = 200, message: str = "success", rid: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": 0, "message": message, "data": data, "request_id": rid or request_id(),
                 "timestamp": int(time.time() * 1000)},
    )


def fail(err: ApiError, rid: str | None = None) -> JSONResponse:
    body = err.to_dict()
    body.update({"request_id": rid or request_id(), "timestamp": int(time.time() * 1000)})
    body.setdefault("data", None)
    return JSONResponse(status_code=err.http_status, content=body)


def paginated(items: list[Any], total: int, page: int, page_size: int, rid: str | None = None) -> JSONResponse:
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return ok(
        {
            "items": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
                "next_cursor": None,
            },
        },
        rid=rid,
    )


def csv_safe(value):
    """CSV 公式注入防护：以 = + - @ 制表符 回车 开头的文本单元格前加单引号（OWASP 建议）。"""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value
