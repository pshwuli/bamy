"""策略中心与插件中心：扫描、校验、热更新（《策略规范》第三节、《插件规范》第九至十节）。"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import logging
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from bamy.core.config import settings
from bamy.extensions.schema import SchemaError, normalize_schema

log = logging.getLogger("bamy.extensions")

CHECKPOINTS = {"instance.before_start", "order.before_submit", "position.before_close", "params.before_save"}
SUBSCRIPTION_EVENTS = {
    "order.created", "order.submitted", "order.filled", "order.partial", "order.failed",
    "order.rejected", "position.changed", "guard.placed", "guard.triggered", "guard.missing",
    "risk.breached", "risk.circuit_break", "risk.circuit_recover", "account.invalid",
    "account.recovered", "account.limit_hit", "instance.error", "instance.auto_stopped",
    "reconcile.diff", "reconcile.completed", "system.alert", "tick",
    "system.heartbeat",          # 每 5 分钟一次的定时事件（日报、回撤提醒等定时插件用）
}
SCORE_CHECK = {"global": 0, "user": 1, "account": 2}


@dataclass
class Extension:
    kind: str                    # strategy / plugin
    id: str
    path: Path
    meta: dict[str, Any] = field(default_factory=dict)
    cls: type | None = None
    module_name: str = ""
    loaded: bool = False
    error: str | None = None

    @property
    def version(self) -> str:
        return str(self.meta.get("version", "0.0.0"))

    @property
    def capabilities(self) -> dict[str, bool]:
        return {k: bool(v) for k, v in (self.meta.get("capabilities") or {}).items()}

    @property
    def params_schema(self) -> list[dict[str, Any]]:
        return normalize_schema(self.meta.get("params"))

    @property
    def settings_schema(self) -> list[dict[str, Any]]:
        return normalize_schema(self.meta.get("settings"))

    @property
    def scopes(self) -> list[str]:
        return [str(s) for s in (self.meta.get("scopes") or [])]

    @property
    def data_scope(self) -> str:
        return str(self.meta.get("data_scope", "own"))

    @property
    def hooks(self) -> list[dict[str, Any]]:
        return [h for h in (self.meta.get("hooks") or []) if isinstance(h, dict)]

    @property
    def actions(self) -> list[dict[str, Any]]:
        """插件声明的手动动作（如扫码绑定）：{id, label, handler}，由用户在设置页触发。"""
        return [a for a in (self.meta.get("actions") or []) if isinstance(a, dict)]

    @property
    def services(self) -> list[dict[str, Any]]:
        """插件声明的后台服务（如常驻连接）：{id, handler}，绑定生效时由插件中心启动、失效时取消。"""
        return [a for a in (self.meta.get("services") or []) if isinstance(a, dict)]

    @property
    def network(self) -> dict[str, Any] | None:
        net = self.meta.get("network")
        return net if isinstance(net, dict) and net.get("enabled") else None

    def subscription_hooks(self) -> list[dict[str, Any]]:
        return [h for h in self.hooks if h.get("event")]

    def checkpoint_hooks(self) -> list[dict[str, Any]]:
        return [h for h in self.hooks if h.get("checkpoint")]


class ExtensionRegistry:
    """加载 extensions/strategies 与 extensions/plugins 下的全部扩展包。"""

    def __init__(self) -> None:
        self.strategies: dict[str, Extension] = {}
        self.plugins: dict[str, Extension] = {}
        self.failed: dict[str, str] = {}

    # ---- 扫描 ----
    def scan(self) -> dict[str, Any]:
        result = {"strategies": self._scan_kind("strategy"), "plugins": self._scan_kind("plugin")}
        return result

    def _scan_kind(self, kind: str) -> dict[str, Any]:
        base = Path(settings.strategy_dir if kind == "strategy" else settings.plugin_dir)
        target = self.strategies if kind == "strategy" else self.plugins
        loaded, failed, errors = 0, 0, []
        seen: set[str] = set()
        base.mkdir(parents=True, exist_ok=True)
        for path in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("__")):
            ext = self._load_package(kind, path)
            if ext is None:
                continue
            seen.add(ext.id)
            if ext.loaded:
                target[ext.id] = ext
                loaded += 1
                self.failed.pop(ext.id, None)
            else:
                failed += 1
                self.failed[ext.id] = ext.error or "未知错误"
                errors.append({"id": ext.id, "reason": ext.error})
                # 加载失败保留上一版
                if ext.id not in target:
                    target[ext.id] = ext
        for stale in [k for k in target if k not in seen]:
            if target[stale].loaded:
                target.pop(stale)
        return {"loaded": loaded, "failed": failed, "errors": errors}

    def _load_package(self, kind: str, path: Path) -> Extension | None:
        meta_file = path / ("strategy.yaml" if kind == "strategy" else "plugin.yaml")
        code_file = path / ("strategy.py" if kind == "strategy" else "plugin.py")
        if not meta_file.exists() or not code_file.exists():
            log.warning("扩展目录缺少必需文件：%s", path)
            return Extension(kind=kind, id=path.name, path=path, error="缺少 strategy.yaml/strategy.py" if kind == "strategy" else "缺少 plugin.yaml/plugin.py")
        ext = Extension(kind=kind, id=path.name, path=path)
        try:
            meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
            if not isinstance(meta, dict):
                raise ValueError("元信息不是对象")
            if str(meta.get("kind")) != kind:
                raise ValueError(f"kind 必须是 {kind}")
            if str(meta.get("id", path.name)) != path.name:
                raise ValueError("id 必须等于目录名")
            for key in ("name", "version"):
                if not meta.get(key):
                    raise ValueError(f"缺少必需字段 {key}")
            ext.meta = meta
            self._validate_meta(ext)
        except (ValueError, yaml.YAMLError, SchemaError) as exc:
            ext.error = f"元信息错误：{exc}"
            return ext

        try:
            ext.cls = self._import_class(ext)
        except Exception as exc:  # noqa: BLE001 - 加载失败记入失败列表
            ext.error = f"代码加载失败：{exc}"
            return ext

        try:
            self._validate_impl(ext)
        except ValueError as exc:
            ext.error = str(exc)
            return ext
        ext.loaded = True
        return ext

    def _validate_meta(self, ext: Extension) -> None:
        if ext.kind == "strategy":
            caps = ext.meta.get("capabilities") or {}
            if not isinstance(caps, dict):
                raise ValueError("capabilities 必须是对象")
        else:
            scopes = ext.scopes
            if not scopes:
                raise ValueError("scopes 至少一个")
            if any(s not in SCORE_CHECK for s in scopes):
                raise ValueError("scopes 取值必须是 global / user / account")
            default_scope = ext.meta.get("default_scope")
            if default_scope and default_scope not in scopes:
                raise ValueError("default_scope 必须在 scopes 内")
            if ext.data_scope not in {"own", "all"}:
                raise ValueError("data_scope 必须是 own 或 all")
            net = ext.meta.get("network")
            if isinstance(net, dict) and net.get("enabled") and not net.get("hosts"):
                raise ValueError("声明出网但 hosts 为空")
            hooks = ext.hooks
            if not hooks:
                raise ValueError("hooks 至少一个")
            if len(hooks) > int(settings.plugins.get("max_hooks", 32)):
                raise ValueError("钩子数量超过上限")
            subs = ext.subscription_hooks()
            if len(subs) > 16:
                raise ValueError("订阅事件超过 16 个")
            per_cp: dict[str, int] = {}
            for hook in hooks:
                if hook.get("event") and hook["event"] not in SUBSCRIPTION_EVENTS:
                    raise ValueError(f"未知事件 {hook['event']}")
                if hook.get("event") == "tick" and not hook.get("throttle"):
                    raise ValueError("订阅 tick 必须声明 throttle")
                if hook.get("checkpoint"):
                    if hook["checkpoint"] not in CHECKPOINTS:
                        raise ValueError(f"未知检查点 {hook['checkpoint']}")
                    per_cp[hook["checkpoint"]] = per_cp.get(hook["checkpoint"], 0) + 1
                    if hook.get("fail_mode", "open") not in {"open", "closed"}:
                        raise ValueError("fail_mode 必须是 open 或 closed")
                    if int(hook.get("timeout_ms", settings.plugins.get("intercept_timeout_ms", 200))) > 2000:
                        raise ValueError("timeout_ms 上限 2000")
            limit = int(settings.plugins.get("max_per_checkpoint", 16))
            if any(v > limit for v in per_cp.values()):
                raise ValueError("单个检查点的拦截钩子超过上限")
            action_ids: set[str] = set()
            for action in ext.actions:
                aid = str(action.get("id") or "")
                if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", aid) or aid in action_ids:
                    raise ValueError(f"动作 id 非法或重复：{aid or '空'}")
                if not action.get("handler") or not action.get("label"):
                    raise ValueError(f"动作 {aid} 缺少 handler 或 label")
                action_ids.add(aid)
            service_ids: set[str] = set()
            if len(ext.services) > 4:
                raise ValueError("后台服务最多 4 个")
            for svc in ext.services:
                sid = str(svc.get("id") or "")
                if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", sid) or sid in service_ids or not svc.get("handler"):
                    raise ValueError(f"后台服务 id 非法、重复或缺少 handler：{sid or '空'}")
                service_ids.add(sid)

    def _import_class(self, ext: Extension) -> type:
        module_name = f"bamy_ext_{ext.kind}_{ext.id}"
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(
            module_name, ext.path / f"{ext.kind}.py",
            submodule_search_locations=[str(ext.path)],
        )
        if spec is None or spec.loader is None:
            raise ImportError("无法加载模块")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        # 让 helpers/ 可被导入
        helpers = str(ext.path)
        _purge_helpers()
        sys.path.insert(0, helpers)
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(helpers)
            # 每个包的 helpers/ 独立：导入后把 helpers.* 改名挂到包自己的命名空间下，避免跨包缓存冲突
            for name in [n for n in sys.modules if n == "helpers" or n.startswith("helpers.")]:
                sys.modules[f"{module_name}.{name}"] = sys.modules.pop(name)
        ext.module_name = module_name
        name = "STRATEGY" if ext.kind == "strategy" else "PLUGIN"
        cls = getattr(module, name, None)
        if cls is None:
            raise ImportError(f"必须导出 {name} 变量")
        if not isinstance(cls, type):
            raise ValueError(f"{name} 必须是类")
        expected = ("BaseStrategy", "StrategyContext") if ext.kind == "strategy" else ("BasePlugin", "PluginContext")
        mro = {c.__name__ for c in cls.__mro__}
        if expected[0] not in mro:
            raise ValueError(f"{name} 必须继承 {expected[0]}")
        return cls

    def _validate_impl(self, ext: Extension) -> None:
        assert ext.cls is not None
        if ext.kind == "strategy":
            for method in ("on_start", "on_tick", "on_stop"):
                handler = getattr(ext.cls, method, None)
                if handler is None or not callable(handler):
                    raise ValueError(f"策略必须实现 {method}")
            impl_map = {
                "close_position": "close_position", "close_positions": "close_positions",
                "close_all": "close_all_positions", "cancel_order": "cancel_order",
                "cancel_all": "cancel_all_orders", "list_positions": "list_positions",
                "pause": "pause", "resume": "resume", "reset": "reset",
            }
            from bamy.extensions.base import BaseStrategy

            impl_map |= {"close_partial": "close_position", "set_leverage": "set_leverage",
                         "set_margin_mode": "set_margin_mode", "set_position_mode": "set_position_mode"}
            caps = dict(ext.meta.get("capabilities") or {})
            warnings = []
            for cap, method in impl_map.items():
                if not caps.get(cap):
                    continue
                impl = getattr(ext.cls, method, None)
                if impl is None or impl is getattr(BaseStrategy, method, None):
                    # 《策略规范》8.2：校验失败只隐藏该能力，策略整体不失败
                    caps[cap] = False
                    warnings.append(f"声明了能力 {cap} 但未实现 {method}，该能力已隐藏")
            ext.meta["capabilities"] = caps
            ext.meta["_warnings"] = warnings
            for w in warnings:
                log.warning("策略 %s：%s", ext.id, w)
        else:
            from bamy.extensions.base import BasePlugin
            for hook in ext.hooks:
                handler_name = hook.get("handler")
                if not handler_name:
                    raise ValueError("钩子缺少 handler")
                handler = getattr(ext.cls, handler_name, None)
                if handler is None or not callable(handler):
                    raise ValueError(f"未实现钩子处理函数 {handler_name}")
                if hook.get("checkpoint") and getattr(ext.cls, handler_name, None) is getattr(BasePlugin, handler_name, None):
                    raise ValueError(f"钩子 {handler_name} 未实现")
            for action in ext.actions:
                if not callable(getattr(ext.cls, str(action["handler"]), None)):
                    raise ValueError(f"未实现动作处理函数 {action['handler']}")
            for svc in ext.services:
                fn = getattr(ext.cls, str(svc["handler"]), None)
                if not callable(fn) or not asyncio.iscoroutinefunction(fn):
                    raise ValueError(f"后台服务 {svc['handler']} 未实现或不是 async")

    # ---- 查询 ----
    def get_strategy(self, sid: str) -> Extension | None:
        return self.strategies.get(sid)

    def get_plugin(self, pid: str) -> Extension | None:
        return self.plugins.get(pid)

    def strategy_rows(self) -> list[dict[str, Any]]:
        return [self._row(e) for e in self.strategies.values()]

    def plugin_rows(self) -> list[dict[str, Any]]:
        return [self._row(e) for e in self.plugins.values()]

    def _row(self, ext: Extension) -> dict[str, Any]:
        m = ext.meta
        row = {
            "id": ext.id, "kind": ext.kind, "name": m.get("name"), "version": m.get("version"),
            "author": m.get("author"), "description": m.get("description"),
            "tags": json.dumps(m.get("tags") or [], ensure_ascii=False), "min_bamy_version": m.get("min_bamy_version"),
            "homepage": m.get("homepage"), "license": m.get("license"),
            "loaded": 1 if ext.loaded else 0, "load_error": ext.error,
            "loaded_at": datetime.utcnow(),
        }
        if ext.kind == "strategy":
            row |= {"capabilities": json.dumps(ext.capabilities, ensure_ascii=False),
                    "params_schema": json.dumps(ext.params_schema, ensure_ascii=False),
                    "runtime": json.dumps(m.get("runtime") or {}, ensure_ascii=False),
                    "dependencies": json.dumps(m.get("dependencies") or {}, ensure_ascii=False)}
        else:
            row |= {"scopes": json.dumps(ext.scopes, ensure_ascii=False),
                    "default_scope": m.get("default_scope"),
                    "allow_multiple": 1 if m.get("allow_multiple") else 0,
                    "data_scope": ext.data_scope,
                    "priority": int(m.get("priority", 100)),
                    "network": json.dumps(ext.network or {}, ensure_ascii=False),
                    "hooks": json.dumps(ext.hooks, ensure_ascii=False),
                    "settings_schema": json.dumps(ext.settings_schema, ensure_ascii=False),
                    "dependencies": json.dumps(m.get("dependencies") or {}, ensure_ascii=False)}
        return row


def _purge_helpers() -> None:
    for name in [n for n in sys.modules if n == "helpers" or n.startswith("helpers.")]:
        sys.modules.pop(name, None)


registry = ExtensionRegistry()
