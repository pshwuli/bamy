"""策略参数 / 插件设置的 schema 校验（两者共用同一套类型系统，见《策略规范》第五节）。"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any

PARAM_TYPES = {
    "int", "float", "string", "text", "bool", "select", "multiselect",
    "secret", "symbol", "date", "time", "datetime", "duration", "percent",
}


class SchemaError(Exception):
    """参数/设置校验失败，code 为业务错误码。"""

    def __init__(self, message: str, code: int = 42222, field: str | None = None):
        self.message = message
        self.code = code
        self.field = field
        super().__init__(message)


def normalize_schema(raw: Any) -> list[dict[str, Any]]:
    """过滤掉框架不认识的 type（加载时警告，跳过该参数）。"""
    if not raw:
        return []
    if not isinstance(raw, list):
        raise SchemaError("schema 必须是数组", 40002)
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or "key" not in item:
            raise SchemaError("schema 条目必须是含 key 的对象", 40002)
        if item.get("type") not in PARAM_TYPES:
            continue
        out.append(item)
    return out


def defaults_for(schema: list[dict[str, Any]]) -> dict[str, Any]:
    return {p["key"]: p["default"] for p in schema if "default" in p}


def _dep_ok(param: dict[str, Any], values: dict[str, Any]) -> bool:
    dep = param.get("depends_on")
    if not dep:
        return True
    return values.get(dep.get("key")) == dep.get("value")


def coerce(param: dict[str, Any], value: Any) -> Any:
    t = param["type"]
    if t == "int":
        return int(value)
    if t in {"float", "percent"}:
        return float(value)
    if t == "bool":
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if t == "multiselect":
        if not isinstance(value, list):
            raise SchemaError("必须是数组", 40002, param.get("key"))
        return [str(v) for v in value]
    if t == "duration":
        return int(value)
    return str(value) if not isinstance(value, (dict, list)) else value


def validate(schema: list[dict[str, Any]], values: dict[str, Any], *, missing_code: int = 42223,
             invalid_code: int = 42222) -> dict[str, Any]:
    """按 schema 校验并归一化。依赖不满足的参数不参与必填校验，也不返回。"""
    clean: dict[str, Any] = {}
    for param in schema:
        key = param["key"]
        if not _dep_ok(param, values):
            continue
        present = key in values and values[key] is not None and values[key] != ""
        if not present:
            # 有默认值的必填项用默认值补齐（与前端表单预填一致）；无默认值才报缺失
            if "default" in param and param["default"] is not None:
                clean[key] = param["default"]
            elif param.get("required"):
                raise SchemaError(f"{param.get('label', key)} 为必填", missing_code, key)
            continue
        value = values[key]
        try:
            value = coerce(param, value)
        except (TypeError, ValueError) as exc:
            raise SchemaError(f"{param.get('label', key)} 类型错误", 40002, key) from exc
        _range_check(param, value, invalid_code)
        clean[key] = value
    return clean


def _range_check(param: dict[str, Any], value: Any, code: int) -> None:
    t = param["type"]
    key = param.get("key")
    label = param.get("label", key)
    if t in {"int", "float", "percent", "duration"}:
        if "min" in param and value < param["min"]:
            raise SchemaError(f"{label} 不能小于 {param['min']}", code, key)
        if "max" in param and value > param["max"]:
            raise SchemaError(f"{label} 不能大于 {param['max']}", code, key)
        if t == "percent":
            if value < 0 or value > 100:
                raise SchemaError(f"{label} 必须是 0-100", code, key)
    elif t in {"select", "multiselect"}:
        allowed = {str(o.get("value")) for o in param.get("options", [])}
        vals = value if isinstance(value, list) else [value]
        for v in vals:
            if allowed and str(v) not in allowed:
                raise SchemaError(f"{label} 取值不在允许范围内", code, key)
    elif t in {"string", "text", "secret", "symbol"}:
        if "max_length" in param and len(str(value)) > param["max_length"]:
            raise SchemaError(f"{label} 长度超出上限", code, key)
        pattern = param.get("pattern")
        if pattern and not re.match(pattern, str(value)):
            raise SchemaError(f"{label} 格式不合法", code, key)
    elif t == "date":
        date.fromisoformat(str(value))
    elif t == "time":
        time.fromisoformat(str(value))
    elif t == "datetime":
        datetime.fromisoformat(str(value))


# ---------------------------------------------------------------- secret 参数
MASK = "******"


def secret_keys(schema: list[dict[str, Any]]) -> set[str]:
    return {p["key"] for p in schema if p.get("type") == "secret" or p.get("secret")}


def merge_secrets(schema: list[dict[str, Any]], incoming: dict[str, Any], stored: dict[str, Any] | None) -> dict[str, Any]:
    """前端回传脱敏值或留空时沿用库里已存的密文（解密后参与校验）。"""
    from bamy.core.crypto import decrypt

    keys = secret_keys(schema)
    out = dict(incoming or {})
    for k in keys:
        if out.get(k) in (MASK, None, "") and stored and stored.get(k):
            v = stored[k]
            out[k] = decrypt(v) if isinstance(v, str) and v.startswith("v1:") else v
    return out


def encrypt_secrets(schema: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    from bamy.core.crypto import encrypt

    keys = secret_keys(schema)
    return {k: (encrypt(v) if k in keys and isinstance(v, str) and v and not v.startswith("v1:") else v)
            for k, v in params.items()}


def decrypt_secrets(schema: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    from bamy.core.crypto import decrypt

    keys = secret_keys(schema)
    out = {}
    for k, v in params.items():
        if k in keys and isinstance(v, str) and v.startswith("v1:"):
            try:
                v = decrypt(v)
            except Exception:  # noqa: BLE001
                pass
        out[k] = v
    return out


def mask_secrets(schema: list[dict[str, Any]], params: dict[str, Any] | None) -> dict[str, Any]:
    keys = secret_keys(schema)
    return {k: (MASK if k in keys and v not in (None, "") else v) for k, v in (params or {}).items()}
