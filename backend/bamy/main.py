"""BAMY 应用入口：组装接口、启动引擎、优雅停机（《部署与运行》第七节）。

启动顺序：配置 → 建表 → 系统配置 → 初始管理员 → 扫描扩展 → 插件绑定 → 崩溃恢复（先对账再恢复）→ 定时任务。
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from bamy import __version__
from bamy.core.config import settings
from bamy.core.errors import ApiError
from bamy.core.ratelimit import limiter
from bamy.core.response import fail
from bamy.db.models import User
from bamy.db.session import init_db, session_scope

log = logging.getLogger("bamy")

MAX_BODY = 1024 * 1024  # 《接口契约》20.7 默认 1MB
READONLY_ALLOW = ("/api/auth/login", "/api/auth/refresh", "/api/auth/logout")


def _setup_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        log_dir = Path(settings.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "bamy.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger().addHandler(fh)
    except OSError:  # pragma: no cover - 只读文件系统时只打 stdout
        pass


async def _init_admin() -> None:
    """INIT_ADMIN_USER / INIT_ADMIN_PASS：系统无用户时创建第一个 super_admin。"""
    if not (settings.init_admin_user and settings.init_admin_pass):
        return
    from bamy.core.security import hash_password

    async with session_scope() as db:
        if (await db.execute(select(func.count(User.id)))).scalar_one():
            return
        db.add(User(username=settings.init_admin_user, password_hash=hash_password(settings.init_admin_pass),
                    role="super_admin", status="active"))
        await db.commit()
        log.info("已创建初始管理员 %s", settings.init_admin_user)


async def startup() -> None:
    from bamy.api.extensions import sync_extension_tables
    from bamy.api.ws import hub
    from bamy.engine.eventbus import bus
    from bamy.engine.pluginhub import plugin_hub
    from bamy.engine.runtime import engine
    from bamy.engine.scheduler import scheduler
    from bamy.engine.userstream import user_stream
    from bamy.extensions.registry import registry
    from bamy.services import system_config

    await init_db()
    async with session_scope() as db:
        await system_config.ensure_defaults(db)
    await _init_admin()
    result = registry.scan()
    log.info("扩展扫描：策略 %s / 插件 %s", result["strategies"], result["plugins"])
    async with session_scope() as db:
        await sync_extension_tables(db)
    engine.set_broadcaster(hub.broadcast)
    bus.add_plugin_dispatcher(plugin_hub.dispatch)
    await plugin_hub.load_bindings()
    user_stream.engine = engine
    if settings.auto_recover_on_boot:
        try:
            if settings.reconcile_before_recover and settings.enable_live_trading:
                await engine.reconcile_all()
            summary = await engine.recover()
            log.info("崩溃恢复：%s", summary)
        except Exception:  # noqa: BLE001 - 恢复失败不阻止服务启动
            log.exception("崩溃恢复失败")
    scheduler.start(engine)
    if settings.enable_live_trading:
        scheduler.every("user_stream_sync", 15, user_stream.sync, delay=3)
    engine.ready = True
    log.info("BAMY %s 已就绪（env=%s，实盘=%s）", __version__, settings.app_env, settings.enable_live_trading)


async def shutdown() -> None:
    from bamy.engine.runtime import engine
    from bamy.engine.scheduler import scheduler
    from bamy.engine.userstream import user_stream

    engine.ready = False
    await scheduler.stop()
    await user_stream.close()
    await engine.shutdown()
    log.info("BAMY 已停止")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _setup_logging()
    await startup()
    try:
        yield
    finally:
        await shutdown()


def _user_ident(request: Request) -> str:
    """限流按用户计数：JWT 取 sub（不验签，只做计数键；鉴权仍由接口依赖完成），API Token 取前缀，匿名按 IP。"""
    auth = request.headers.get("authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if token.startswith("bamy_"):
        return "tok:" + token[:16]
    if token.count(".") == 2:
        import jwt

        from bamy.core.security import ALGORITHM

        try:
            sub = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM],
                             options={"verify_exp": False}).get("sub")
            if sub:
                return f"uid:{sub}"
        except jwt.PyJWTError:
            pass
    return "ip:" + (request.client.host if request.client else "-")


def create_app() -> FastAPI:
    app = FastAPI(title="BAMY", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs" if settings.app_env == "dev" else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if settings.app_env == "dev" else None)

    origins = [o.strip() for o in __import__("os").getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    if settings.app_env == "dev" and not origins:
        origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        from bamy.api.admin import READONLY
        from bamy.core import metrics as m

        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        path = request.url.path
        started = time.perf_counter()
        # 请求大小限制
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY and not path.startswith("/api/super/backups"):
            return fail(ApiError(40001, "请求体超过 1MB"), rid)
        # 只读模式（备份恢复中）
        if READONLY["on"] and request.method not in {"GET", "HEAD", "OPTIONS"} and not path.startswith(READONLY_ALLOW):
            return fail(ApiError(50302), rid)
        # 限流
        if limiter.enabled:
            rule = limiter.rule_for(path, request.method)
            if rule is not None:
                prefix, window, limit, dim = rule
                if dim == "ip":
                    ident = request.client.host if request.client else "-"
                elif dim == "path":
                    ident = path
                else:
                    ident = _user_ident(request)
                wait = limiter.hit(f"{prefix}|{ident}", window, limit)
                if wait:
                    resp = fail(ApiError(42901), rid)
                    resp.headers["Retry-After"] = str(wait)
                    return resp
        response = await call_next(request)
        cost = time.perf_counter() - started
        response.headers["X-Request-Id"] = rid
        response.headers["X-API-Version"] = "v1"
        route = request.scope.get("route")
        label = getattr(route, "path", "unmatched")
        m.HTTP_REQUESTS.labels(request.method, label, str(response.status_code)).inc()
        m.HTTP_LATENCY.labels(request.method, label).observe(cost)
        if path.startswith("/api/"):
            logging.getLogger("bamy.access").info(
                "%s %s %s %.1fms uid=%s ip=%s rid=%s", request.method, path, response.status_code, cost * 1000,
                getattr(request.state, "user_id", None), request.client.host if request.client else None, rid)
        return response

    @app.middleware("http")
    async def api_version(request: Request, call_next):
        # /api/v1/xxx 与 Accept: application/vnd.bamy.v1+json 都映射到当前版本（2.9）
        path = request.scope["path"]
        if path.startswith("/api/v1/"):
            request.scope["path"] = "/api/" + path[len("/api/v1/"):]
        elif path.startswith("/api/v") and path[6:7].isdigit():
            return fail(ApiError(40401, "不支持的接口版本"))
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return fail(exc, getattr(request.state, "request_id", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors, code = [], 40001
        for e in exc.errors():
            loc = [str(x) for x in e.get("loc", []) if x not in ("body", "query", "path")]
            errors.append({"field": ".".join(loc) or "body", "reason": e.get("msg", "")})
            t = e.get("type", "")
            if t == "missing":
                code = 40004
            elif code == 40001 and ("type" in t or "parsing" in t):
                code = 40002
            elif code == 40001 and t in {"greater_than", "less_than", "greater_than_equal", "less_than_equal",
                                         "too_long", "too_short", "string_too_long", "string_too_short"}:
                code = 40003
        return fail(ApiError(code, errors=errors), getattr(request.state, "request_id", None))

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):
        log.info("唯一约束冲突：%s", exc.orig)
        return fail(ApiError(40901), getattr(request.state, "request_id", None))

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(request: Request, exc: SQLAlchemyError):
        log.exception("数据库错误")
        return fail(ApiError(50002), getattr(request.state, "request_id", None))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: 40401, 405: 40001, 401: 40101, 403: 40301}.get(exc.status_code, exc.status_code * 100 + 1)
        err = ApiError(code, str(exc.detail) if exc.detail and exc.status_code != 404 else None)
        err.http_status = exc.status_code
        return fail(err, getattr(request.state, "request_id", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("未处理异常 %s %s", request.method, request.url.path)
        return fail(ApiError(50001), getattr(request.state, "request_id", None))

    from bamy.api import accounts, admin, auth, extensions, instances, paper, ws

    for module in (auth, accounts, extensions, instances, paper, admin, ws):
        app.include_router(module.router)

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """方案 B：后端托管 frontend/dist（Termux / 无 nginx 部署）。有 nginx 时不影响。"""
    dist = settings.frontend_dist
    if not (dist / "index.html").exists():
        return
    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request):
        if full_path.startswith(("api/", "ws/")) or full_path in {"health", "metrics"}:
            return fail(ApiError(40401))
        target = (dist / full_path).resolve()
        if full_path and target.is_file() and dist.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(index)


app = create_app()
