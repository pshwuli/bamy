# BAMY 前端镜像（多阶段构建）
#
# 构建上下文是仓库根目录（见 docker/docker-compose.yml 的 build.context）：
#   docker build -f frontend/Dockerfile -t bamy-frontend .
#
# 提供两种产物落地方式，见文件末尾“方案 A / 方案 B”。
# 默认（最后一个阶段）是方案 A：nginx 镜像自带静态产物。

# ---------- 阶段 1：构建 ----------
FROM node:20-alpine AS build

WORKDIR /app

# 前端目录当前可能尚未就绪（只有 .gitkeep）。
# 若缺少 package.json / vite.config.*，本阶段会构建失败——这是预期行为，
# 前端代码落地后即可直接构建。标准 Vue 3 + Vite 项目的构建流程如下：
#   1) 装依赖：有 package-lock.json 用 npm ci，否则用 npm install
#   2) 构建：  npm run build  → 产物在 /app/dist
COPY frontend/ /app/

RUN set -eux; \
    if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \
    else npm install --no-audit --no-fund; fi; \
    npm run build

# ---------- 阶段 2：导出产物（方案 B 用） ----------
# 只装静态产物，供“后端托管前端”的部署方式把 dist 拷到宿主机的 frontend/dist
#   docker build -f frontend/Dockerfile --target dist-export -t bamy-frontend-dist .
#   docker run --rm -v "$PWD/frontend/dist:/out" bamy-frontend-dist
FROM busybox AS dist-export

COPY --from=build /app/dist /dist
VOLUME ["/out"]
CMD ["sh", "-c", "cp -a /dist/. /out/"]

# ---------- 阶段 3：nginx 托管（方案 A，默认目标） ----------
# 静态文件由 nginx 直接托管，nginx.conf 见 docker/nginx.conf
FROM nginx:alpine AS nginx

COPY --from=build /app/dist /usr/share/nginx/html

# 运行时由编排层把 docker/nginx.conf 挂到 /etc/nginx/nginx.conf
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/nginx-health || exit 1

CMD ["nginx", "-g", "daemon off;"]

# 方案 A：nginx 托管静态（docker-compose.yml 里的 nginx 服务默认走这条）
#   build: { context: .., dockerfile: frontend/Dockerfile, target: nginx }
#
# 方案 B：前端产物交给后端托管（无独立 nginx 静态根，或由应用进程直接返回 /view）
#   1) 导出产物到宿主机：
#        docker build -f frontend/Dockerfile --target dist-export -t bamy-frontend-dist .
#        docker run --rm -v "$PWD/frontend/dist:/out" bamy-frontend-dist
#   2) docker-compose.yml 里把 nginx 换成官方镜像并挂载产物（见该文件的注释块），
#      同时给 app 服务加挂载：  - ../frontend/dist:/app/frontend/dist:ro
#      由应用进程从 frontend/dist 托管前端静态（config.py 的 frontend_dist），
#      nginx 只做反代；Termux / 无 nginx 的 apt+pip 部署同样走这条。
#
# 两种方案的取舍：
#   A 静态由 nginx 直出，性能好、与应用解耦，推荐生产使用；
#   B 少一个静态根、少一次构建产物拷贝，适合单机最小部署与调试。
