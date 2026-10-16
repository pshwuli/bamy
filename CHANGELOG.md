# CHANGELOG

本文件记录 BAMY 的版本变更。格式遵循语义化版本：主版本号用于不兼容变更，次版本号用于新增功能，修订号用于修复缺陷。

完整的历史版本记录见 `docs/changelog/`。

---

## [1.0.0] - 2026-10-16

首个完整版：按 `DESIGN.md` 与 13 篇设计文档实现全部模块。

### 新增

- 后端（`backend/bamy/`，FastAPI + SQLAlchemy 2 异步）：用户与权限（JWT、刷新轮换、2FA、API Token、邀请码）、账号（币安现货验证、加密存储、探活）、策略中心与插件中心（扫描、校验、热加载、四个检查点拦截链、fail_mode）、实例生命周期与崩溃恢复、订单五段处理、模拟撮合、风控与熔断、保护单、对账、审计、分享、备份恢复、WebSocket 推送、Prometheus 指标、接口限流
- 用户数据流（`engine/userstream.py`）：基于 ccxt.pro `watch_orders`，走币安 WebSocket API 签名订阅
- 前端（`frontend/`，Vue 3 + Element Plus）：《前端页面结构》列出的全部用户、管理员、超级管理员与访客页面，中英文、亮暗主题、响应式
- 内置扩展：`grid_strategy`、`dca_strategy`、`ma_cross` 三个策略，`telegram_notify`、`audit_export` 两个插件
- 部署：`docker/docker-compose.yml`（PostgreSQL + 应用 + nginx）、`backend/Dockerfile`、`frontend/Dockerfile`、`scripts/{start,stop,backup,restore,healthcheck}.sh`（docker / native 两种模式）
- 测试：`tests/backend/`（44 个用例：接口全流程、隔离、插件拦截语义、模拟撮合、WebSocket、边界原则、崩溃恢复）

### 变更

- 用户数据流文档由 listenKey 改为 WebSocket API 签名订阅（币安现货已于 2026-02-20 下线 listenKey）
- 快速开始与 README 的启动命令改为实际可用的 `scripts/start.sh` 流程

## [未发布]

### 新增

- 项目根目录 `README.md`：项目门面，含特征、架构层次、技术栈、文档导航
- `DOCS_STANDARD.md`：文档元规则，规定目录、命名、格式、内容与写作风格
- `DESIGN.md`：框架总纲，定义模块清单、职责边界、依赖关系与关键边界
- 十二份设计文档（`docs/design/01` 至 `12`）：用户与权限、账号模块、插件规范、实例模型、引擎、订单与持仓、模拟盘、数据模型、接口契约、前端页面结构、异常与容错、部署与运行
- 四份指南（`docs/guides/`）：快速开始、插件开发指南、运维手册、故障处理
- 四份参考手册（`docs/reference/`）：配置项、错误码、术语表、API速查
- 项目骨架目录：`backend/`、`frontend/`、`plugins/`、`config/`、`docker/`、`scripts/`、`tests/`
- `.gitignore`：忽略密钥、依赖目录、运行时数据、打包产物
- `.editorconfig`：统一编码、换行符与缩进
- 设计文档 `13-插件规范.md`：插件的作用域、绑定、钩子、检查点、拦截协议、失败语义、数据范围
- 指南 `插件开发指南.md`：从零写一个插件

### 变更

- 设计文档由扁平目录迁入 `docs/design/`，按规范加上两位数字编号
- `bamy设计框架.md` 更名为 `DESIGN.md` 并移至项目根目录
- `看看这个.md` 更名为 `DOCS_STANDARD.md`
- **把「策略」从「插件」中拆出，两者并列**：策略是交易决策逻辑，挂在实例上，可产生交易意图；插件是框架行为的扩展，挂在作用域上，不产生交易意图
- `docs/design/03-插件规范.md` 更名为 `03-策略规范.md`，内容按新定义重写
- `docs/guides/插件开发指南.md` 更名为 `策略开发指南.md`
- 骨架目录 `plugins/` 改为 `extensions/`，下设 `strategies/` 与 `plugins/` 两个子目录
- 实例模型：`plugin_id` / `plugin_version` 更名为 `strategy_id` / `strategy_version`，「换插件」更名为「换策略」；实例不再关联插件
- 订单与持仓：下单路径由四段处理改为五段处理，新增「插件拦截」段
- 引擎：子引擎由 10 个增至 12 个，新增策略中心（StrategyHub）与插件中心（PluginHub）
- 数据模型：原 `plugins` 单表拆为 `strategies`、`plugins`、`plugin_bindings` 三张表
- 权限：「触发插件热加载」改为「触发扩展热加载」，新增插件绑定的挂载与停用权限
- 术语表：重写「插件 / 策略」辨析，新增扩展、策略包、插件包、绑定、钩子、检查点、拦截链等术语
- 术语表：实例状态以《实例模型设计》为准（补 `resuming`、`deleted`），订单状态以《订单与持仓设计》为准（补 `submitting`、`unknown`）；引擎设计的订单状态表同步
- 部署：前端定为 Vue 3 + Element Plus（Pinia、Vue Router）；后端依赖以 PyJWT 与 bcrypt（可换 argon2-cffi）替换 python-jose 与 passlib；Docker Compose 为主，apt + pip 为备（含 Termux）
- 交易范围：只做币安现货，`market_type` 只接受 `spot`，U 本位合约保留为扩展位、不实现；模拟盘去掉强平与资金费率模拟
- 沙箱：明确只防误操作，策略与插件同进程运行，只装可信扩展，不做子进程隔离
- 模拟盘：明确账号级插件对模拟盘不生效、不设影子账号（07、03、13、10）
- 前端页面结构：新增第十五节「页面与接口对照」，接口一律引用《接口契约》，并列出 09 尚无接口的页面动作
- API 速查：与《接口契约》对齐，重新扫描扩展为 `POST /api/admin/plugins/reload`，删除 09 未定义的 `/api/admin/plugin-bindings/{id}/recover`、`/api/admin/plugin-bindings/{id}/health`、`/api/orders/{id}/cancel`，补 `GET /api/admin/plugins/{id}/health`

### 修复

- 无

### 破坏性变更

- 设计文档的路径与文件名全部变更，外部引用需同步更新
- 「插件」一词的所指发生变更：原先兼指策略，现仅指框架行为扩展。所有引用该词的文档、代码与接口需按新定义校正
- 扩展目录由 `plugins/` 改为 `extensions/{strategies,plugins}/`

---

## 版本说明

### 版本号规则

| 段 | 何时递增 |
|---|---|
| 主版本 | 不兼容的接口或数据变更 |
| 次版本 | 向后兼容的新增功能 |
| 修订号 | 向后兼容的缺陷修复 |

### 记录规则

每次发版在 `docs/changelog/vX.Y.Z.md` 新建一份记录，本文件只保留索引与摘要。

单份记录的格式：

```
# vX.Y.Z

## 新增
- xxx

## 变更
- xxx

## 修复
- xxx

## 破坏性变更
- xxx
```
