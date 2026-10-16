# BAMY 后端镜像
#
# 构建上下文是仓库根目录（见 docker/docker-compose.yml 的 build.context）：
#   docker build -f backend/Dockerfile -t bamy-backend .
#
# 镜像内的目录布局必须与 backend/bamy/core/config.py 的 ROOT_DIR 假设一致
# （ROOT_DIR = <repo>/backend/bamy/core/config.py 的 parents[3]），即：
#   /app/backend/bamy/**     代码
#   /app/config/config.yaml  配置文件
#   /app/extensions/**       策略包与插件包（EXTENSION_DIR 的父目录）
#   /app/data/**             SQLite 库与运行数据
#   /app/logs/**             日志

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend \
    TZ=Asia/Shanghai \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

# libpq5: asyncpg/psycopg 的本地库；tzdata: 时区；curl: 健康检查
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 tzdata curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户，uid/gid 固定，便于宿主机卷挂载后 chown
RUN groupadd -g 10001 bamy \
    && useradd --uid 10001 --gid bamy --home-dir /home/bamy --create-home --shell /usr/sbin/nologin bamy

# 独立虚拟环境，避免与系统 Python 相互污染
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# 依赖单独一层，requirements.txt 不变时复用缓存
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r /app/backend/requirements.txt

# 代码、配置、扩展目录
COPY --chown=bamy:bamy backend/bamy /app/backend/bamy
COPY --chown=bamy:bamy config /app/config
COPY --chown=bamy:bamy extensions /app/extensions

# 运行时目录 + 前端产物挂载点（前端由后端托管时使用 /view）
RUN mkdir -p /app/data /app/logs /app/frontend/dist /app/backups \
    && chown -R bamy:bamy /app

# 容器内健康检查：直接探应用进程，不依赖 nginx
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${APP_PORT}/health" || exit 1

USER bamy

EXPOSE 8000

# 优雅停止：uvicorn 收到 SIGTERM 后走应用内的停机流程（见《部署与运行》7.2）
STOPSIGNAL SIGTERM

# exec 形式让 uvicorn 成为 PID 1 的替代进程，信号可直接送达
# APP_HOST / APP_PORT 来自环境变量，默认 0.0.0.0:8000
ENTRYPOINT ["sh", "-c", "exec uvicorn bamy.main:app --host \"${APP_HOST:-0.0.0.0}\" --port \"${APP_PORT:-8000}\" --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16}\" --timeout-graceful-shutdown 60"]
