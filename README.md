# BAMY

> 多用户交易自动化后台。策略可插拔，插件可扩展。

把策略挂到交易所 API 账号上，自动运行、自动保护、自动记录，所有行为可看、可管、可追溯；框架自身的行为可由插件增强。

## 一句话

BAMY = 多用户注册 + 多账号接入 + 策略可插拔 + 插件可扩展 + 自动保护 + 全量可视化 + 模拟盘对比。

## 核心特征

| 特征 | 说明 |
|---|---|
| 多用户 | 注册、登录、角色、数据按归属隔离 |
| 多账号 | 每个交易所账号独立连接、独立限频，互不影响 |
| 策略可插拔 | 交易逻辑以策略包接入，框架不解释业务含义 |
| 插件可扩展 | 框架行为可由插件增强，作用域可配 |
| 实例制 | 实例是唯一运行单元，账号、策略、参数、交易对在实例上组合 |
| 自动保护 | 开仓带保护，保护缺失被检测，异常自动收敛 |
| 全量可视化 | 订单、持仓、日志、审计、对账差异全部可见 |
| 模拟盘 | 与实盘共享行情与策略代码，独立资金与持仓，支持多盘对比 |
| 可对账 | 状态未知的订单进对账队列，持仓以交易所为准修正 |
| 可恢复 | 进程崩溃后先对账再恢复，不带着脏状态继续跑 |

## 四条贯穿全局的原则

1. **框架不懂业务，策略定义业务。** 框架代码里不出现杠杆、网格、止损这类业务词，一切由策略声明。
2. **插件增强行为，但不越权交易。** 插件只改变框架对意图的处理，拿不到任何下单接口，策略对插件的存在无感知。
3. **实例是唯一运行单元。** 用户不跑策略，账号不跑策略，策略本身也不跑，只有实例在跑。
4. **所有外部行为都要能对账、能追溯、能恢复。**

## 架构层次

```
用户层        注册登录 · 用户空间 · 角色权限 · 管理员控制台
接口层        REST（用户端/管理员端物理分离） · WebSocket 实时推送
服务层        用户 · 账号 · 策略 · 插件 · 实例 · 订单 · 模拟 · 风控 · 审计
              策略中心 StrategyHub（策略包加载、校验、热更新）
              插件中心 PluginHub（插件包加载、绑定解析、拦截链执行）
核心引擎      行情中心 · 事件总线 · 策略调度 · 对账引擎
              模拟撮合 · 保护中心 · 风控中心 · 连接池 · 用户数据流
扩展层        extensions/strategies/ 下每个目录一个策略包，可产生交易意图
              extensions/plugins/ 下每个目录一个插件包，只增强框架行为
数据层        用户 · 账号 · 策略元 · 插件元 · 插件绑定 · 实例 · 参数 · 订单
              持仓 · 日志 · 快照 · 审计 · 对账差异 · 熔断记录 · 用户 Token
接入适配层    交易所适配器（REST 限频管理 + WebSocket 用户数据流 + 错误码映射）
```

完整的模块清单、依赖关系与边界见 [DESIGN.md](DESIGN.md)。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11 及以上 |
| 前端 | Node.js 18 及以上 + Vue 3 + Element Plus + Vite |
| 数据库 | PostgreSQL 14 及以上（生产），SQLite（开发与小规模） |
| 缓存 | Redis 6 及以上（可选） |
| 反向代理 | Nginx |
| 部署 | Docker 与 Docker Compose（主），apt + pip（备，含 Termux） |
| 监控 | Prometheus + Grafana |

具体版本与依赖清单见 [docs/design/12-部署与运行.md](docs/design/12-部署与运行.md)。

## 目录结构

```
bamy/
├── README.md                 项目门面
├── DESIGN.md                 框架总纲
├── DOCS_STANDARD.md          文档规范
├── CHANGELOG.md              变更日志
├── docs/                     所有文档
│   ├── design/               设计文档（编号 01-13）
│   ├── guides/               使用指南
│   ├── reference/            参考手册
│   ├── changelog/            版本记录
│   └── archive/              归档
├── backend/                  Python 后端
├── frontend/                 Node 前端
├── extensions/               扩展目录
│   ├── strategies/           策略包目录
│   └── plugins/              插件包目录
├── config/                   配置文件
├── docker/                   Docker 配置
├── scripts/                  运维脚本
└── tests/                    测试
```

## 文档导航

### 设计文档

按顺序阅读，前一份是后一份的前提。

| 序号 | 文档 | 核心内容 |
|---|---|---|
| 01 | [用户与权限设计](docs/design/01-用户与权限设计.md) | 注册、登录、角色、数据归属、隔离规则 |
| 02 | [账号模块设计](docs/design/02-账号模块设计.md) | 多账号接入、验证、失效、隔离、连接池 |
| 03 | [策略规范](docs/design/03-策略规范.md) | 元信息结构、参数 schema、能力声明、生命周期、标准 API |
| 04 | [实例模型设计](docs/design/04-实例模型设计.md) | 字段、状态机、生命周期、换策略保护 |
| 05 | [引擎设计](docs/design/05-引擎设计.md) | 行情、事件、调度、保护、风控、对账的详细逻辑 |
| 06 | [订单与持仓设计](docs/design/06-订单与持仓设计.md) | 下单五段处理、插件拦截、对账、平仓粒度、保护联动 |
| 07 | [模拟盘设计](docs/design/07-模拟盘设计.md) | 撮合规则、快照、多盘对比、归一化 |
| 08 | [数据模型](docs/design/08-数据模型.md) | 表结构、字段、索引、加密、迁移策略 |
| 09 | [接口契约](docs/design/09-接口契约.md) | HTTP 与 WebSocket 标准协议、错误码 |
| 10 | [前端页面结构](docs/design/10-前端页面结构.md) | 用户后台与管理员后台页面、字段、操作 |
| 11 | [异常与容错](docs/design/11-异常与容错.md) | 异常兜底表、降级策略、恢复流程 |
| 12 | [部署与运行](docs/design/12-部署与运行.md) | 环境、依赖、启动、日志、健康检查 |
| 13 | [插件规范](docs/design/13-插件规范.md) | 作用域与绑定、钩子与检查点、拦截协议、失败语义 |

### 指南

| 文档 | 用途 |
|---|---|
| [快速开始](docs/guides/快速开始.md) | 从零跑通第一笔模拟盘交易 |
| [策略开发指南](docs/guides/策略开发指南.md) | 从零写一个策略 |
| [插件开发指南](docs/guides/插件开发指南.md) | 从零写一个插件 |
| [运维手册](docs/guides/运维手册.md) | 日常巡检、备份、恢复、升级 |
| [故障处理](docs/guides/故障处理.md) | 出问题时怎么定位与恢复 |

### 参考手册

| 文档 | 用途 |
|---|---|
| [配置项](docs/reference/配置项.md) | 全部配置项与默认值 |
| [错误码](docs/reference/错误码.md) | 全部错误码与处理建议 |
| [术语表](docs/reference/术语表.md) | 名词定义与易混词辨析 |
| [API速查](docs/reference/API速查.md) | 全部端点、频道、限流索引 |

## 快速开始

在一台装好 Docker、Node 18+、git 的 Linux 服务器上：

```
git clone https://github.com/pshwuli/bamy.git && cd bamy
cp docker/.env.docker.example docker/.env   # 填 SECRET_KEY、ENCRYPT_KEY、POSTGRES_PASSWORD、BAMY_DB_PASSWORD
(cd frontend && npm ci && npm run build)
BAMY_MODE=docker scripts/start.sh            # 默认端口见 docker/.env 的 HTTP_PORT
sudo bash scripts/install-updater.sh         # 可选：装上后台"系统更新"按钮
```

然后访问 `http://服务器地址/setup` 创建第一个管理员。不用 Docker 时见快速开始 3.2（`BAMY_MODE=native scripts/start.sh`，端口 8000）。

默认只能建模拟盘。要跑实盘，把 `docker/.env` 里的 `ENABLE_LIVE_TRADING` 改成 `true` 再重启。

### 更新

装了更新服务后，用超级管理员登录后台 → 超级管理 → 系统更新 → 立即更新。它会先备份数据库，再拉取本仓库 main 分支的新代码、构建、重启；任何一步失败都会自动回滚。没装的话在服务器上执行 `scripts/update.sh apply`，效果一样。

**注意**：如果通过 `git pull` 手工更新代码，更新完成后必须重启服务才能生效（`BAMY_MODE=docker scripts/start.sh` 或 `BAMY_MODE=native scripts/start.sh`）。推荐使用上述自动更新流程，它会自动处理重启。

开发与测试：

```
.venv/bin/python -m pytest              # 后端测试（tests/backend）
cd frontend && npm run typecheck        # 前端类型检查
```

完整步骤、环境要求与成功判据见 [快速开始](docs/guides/快速开始.md)。

## 写文档之前

所有文档必须遵守 [DOCS_STANDARD.md](DOCS_STANDARD.md)。新增文档前先读它。

要点：设计文档放 `docs/design/` 并带编号，指南放 `docs/guides/`，参考放 `docs/reference/`，每份文档都要有引用块头部与边界说明。

## 当前状态

1.0 可用：现货与 U 本位合约模拟盘、币安现货实盘、全市场扫描策略、插件、后台一键更新。合约实盘适配器已写好但只做过离线验证。

## 许可

待定。以仓库根目录的 LICENSE 文件为准。
