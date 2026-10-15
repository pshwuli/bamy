# API速查

> 本文档是 BAMY 接口的速查索引：按模块列出全部 HTTP 端点、WebSocket 频道、限流额度与公共约定。
> 只做索引，不重复请求体与响应体的细节。
> 本文档是完整设计，不是第一版，是最终态。

---

## 一、文档要回答的问题

1. 系统一共有哪些端点
2. 端点按模块怎么分组
3. 每个端点的用途是什么，属于哪类鉴权
4. 策略、插件与插件绑定分别有哪些端点
5. 有哪些 WebSocket 频道和事件
6. 限流额度是多少
7. 请求与响应有哪些公共约定

详细请求体、响应体、字段含义见《接口契约》。
策略与插件的区别、插件作用域的生效链见《策略规范》与《插件规范》。

---

## 二、公共约定

### 2.1 基地址与内容类型

| 项 | 约定 |
|---|---|
| 前缀 | 无全局前缀；按模块区分 `/api`、`/api/admin`、`/api/super`、`/view` |
| 内容类型 | `application/json` |
| 认证头 | `Authorization: Bearer <token>` |
| 追踪头 | `X-Request-Id`（可选） |
| 时间格式 | ISO 8601，UTC |

### 2.2 响应包装

成功：

```json
{ "code": 0, "message": "success", "data": {}, "request_id": "xxx", "timestamp": 1690000000000 }
```

失败：`code` 为非 0 的错误码，`data` 为 `null` 或字段级错误。完整错误码见《错误码》。

### 2.3 分页

请求参数：`page`（默认 1）、`page_size`（默认 20）、`cursor`、`sort`（默认 `created_at`）、`order`（默认 `desc`）。

响应 `data.pagination` 包含 `page`、`page_size`、`total`、`total_pages`、`has_next`、`has_prev`、`next_cursor`。

### 2.4 幂等

| 方法 | 幂等性 |
|---|---|
| GET / PUT / DELETE | 天然幂等 |
| POST | 通过 `client_order_id` 或请求 ID 幂等，契约中逐个注明 |

### 2.5 鉴权分级

| 类别 | 前缀 | 鉴权 | 数据范围 |
|---|---|---|---|
| 认证 | `/api/auth/*` | 无 | 注册、登录、找回密码 |
| 初始化 | `/api/setup` | 无（仅首次） | 创建第一个管理员 |
| 用户 | `/api/*` | JWT | 强制按 `owner_id` 过滤 |
| 管理员 | `/api/admin/*` | JWT + 角色≥admin | 全局 |
| 超级管理员 | `/api/super/*` | JWT + super_admin | 全局 + 系统级 |
| 访客 | `/view/{token}` | 分享 token | 只读，限 token 绑定的实例 |
| 系统 | `/health`、`/metrics` | 无 | 健康检查、监控 |
| WebSocket | `/ws/*` | JWT 或分享 token | 实时推送 |

---

## 三、认证与用户

### 3.1 认证

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/setup` | 创建第一个管理员（仅系统无用户时可用） |
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/refresh` | 刷新 Token |
| POST | `/api/auth/logout` | 登出 |
| POST | `/api/auth/change-password` | 修改密码 |
| POST | `/api/auth/forgot-password` | 发起找回 |
| POST | `/api/auth/reset-password` | 重置密码 |
| GET | `/api/auth/me` | 获取当前用户 |
| POST | `/api/auth/2fa/enable` | 开启二次验证 |
| POST | `/api/auth/2fa/verify` | 校验二次验证 |
| POST | `/api/auth/2fa/disable` | 关闭二次验证 |
| POST | `/api/auth/2fa/recovery-codes` | 生成恢复码 |

### 3.2 用户自身

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/user/profile` | 获取自己的信息 |
| PATCH | `/api/user/profile` | 更新自己的信息 |
| GET | `/api/user/audit-logs` | 自己的审计日志 |
| GET | `/api/user/api-tokens` | API Token 列表 |
| POST | `/api/user/api-tokens` | 创建 API Token |
| DELETE | `/api/user/api-tokens/{id}` | 撤销 API Token |
| GET | `/api/user/sessions` | 会话列表 |
| DELETE | `/api/user/sessions/{id}` | 撤销会话 |

---

## 四、账号

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/accounts` | 账号列表 |
| GET | `/api/accounts/{id}` | 账号详情 |
| POST | `/api/accounts` | 创建账号 |
| POST | `/api/accounts/batch` | 批量导入 |
| PATCH | `/api/accounts/{id}` | 编辑账号 |
| POST | `/api/accounts/{id}/credentials` | 更新密钥 |
| POST | `/api/accounts/{id}/verify` | 主动验证 |
| GET | `/api/accounts/{id}/balance` | 余额 |
| GET | `/api/accounts/{id}/positions` | 持仓 |
| GET | `/api/accounts/{id}/orders` | 订单 |
| GET | `/api/accounts/{id}/health` | 健康数据 |
| DELETE | `/api/accounts/{id}` | 删除账号 |

---

## 五、策略、插件与绑定

### 5.1 策略

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/strategies` | 策略列表 |
| GET | `/api/strategies/{id}` | 策略详情 |

### 5.2 插件市场

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/plugins` | 插件列表 |
| GET | `/api/plugins/{id}` | 插件详情 |

### 5.3 插件绑定

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/plugin-bindings` | 绑定列表 |
| POST | `/api/plugin-bindings` | 新建绑定 |
| PATCH | `/api/plugin-bindings/{id}` | 修改绑定（启用、停用、设置、优先级） |
| DELETE | `/api/plugin-bindings/{id}` | 删除绑定 |
| GET | `/api/plugin-bindings/{id}/health` | 绑定健康度 |

### 5.4 管理员端插件绑定

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/admin/plugin-bindings` | 全系统绑定列表 |
| POST | `/api/admin/plugin-bindings` | 新建全局绑定 |
| PATCH | `/api/admin/plugin-bindings/{id}` | 修改全局绑定 |
| DELETE | `/api/admin/plugin-bindings/{id}` | 删除全局绑定 |
| GET | `/api/admin/plugins/{id}/health` | 插件全系统健康度 |

被自动停用的全局绑定，用 `PATCH /api/admin/plugin-bindings/{id}` 传 `enabled: true` 手动恢复，不设单独的 recover 端点。

### 5.5 扩展管理

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/admin/plugins/reload` | 重新扫描扩展（同时扫描策略与插件） |

策略与插件的区别、插件作用域与生效链见《策略规范》与《插件规范》。重新扫描只加载磁盘上新增或变更的扩展，不自动创建绑定。

---

## 六、实例

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/instances` | 实例列表 |
| GET | `/api/instances/{id}` | 实例详情 |
| POST | `/api/instances` | 创建实例 |
| PATCH | `/api/instances/{id}` | 编辑实例 |
| POST | `/api/instances/{id}/start` | 启动 |
| POST | `/api/instances/{id}/stop` | 停止 |
| POST | `/api/instances/{id}/pause` | 暂停 |
| POST | `/api/instances/{id}/resume` | 恢复 |
| GET | `/api/instances/{id}/status` | 状态 |
| POST | `/api/instances/{id}/change-strategy` | 换策略 |
| GET | `/api/instances/{id}/orders` | 实例订单 |
| GET | `/api/instances/{id}/positions` | 实例持仓 |
| GET | `/api/instances/{id}/logs` | 实例日志 |
| GET | `/api/instances/{id}/audit-logs` | 实例审计 |
| POST | `/api/instances/{id}/reset-paper` | 重置模拟盘 |
| DELETE | `/api/instances/{id}` | 删除实例 |

实例的请求体与响应体中，引用策略的字段为 `strategy_id` 与 `strategy_version`。实例只引用策略，不引用插件。

---

## 七、订单与持仓

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/orders` | 订单列表 |
| GET | `/api/orders/{id}` | 订单详情 |
| GET | `/api/orders/{id}/fills` | 成交明细 |
| POST | `/api/instances/{id}/orders/{order_id}/cancel` | 实例内撤单 |
| POST | `/api/instances/{id}/orders/cancel-all` | 撤全部单 |
| POST | `/api/instances/{id}/positions/{position_id}/close` | 平单个仓 |
| POST | `/api/instances/{id}/positions/close-batch` | 平多个仓 |
| POST | `/api/instances/{id}/positions/close-all` | 平全部仓 |
| POST | `/api/instances/{id}/positions/force-close` | 强制平仓（兜底） |

---

## 八、模拟盘

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/instances/{id}/paper-account` | 模拟账户 |
| GET | `/api/instances/{id}/paper-snapshots` | 净值快照 |
| GET | `/api/paper/compare` | 多盘对比 |
| GET | `/api/paper/compare/metrics` | 对比指标 |

查询参数：`start_time`、`end_time`、`granularity`（minute/hour/day）、`instance_ids`、`normalize`。

---

## 九、对账

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/admin/reconcile/trigger` | 手动触发对账 |
| GET | `/api/admin/reconcile/diffs` | 差异列表 |
| GET | `/api/admin/reconcile/diffs/{id}` | 差异详情 |
| POST | `/api/admin/reconcile/diffs/{id}/resolve` | 标记已解决 |

---

## 十、风控

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/risk-rules` | 规则列表 |
| POST | `/api/risk-rules` | 创建规则 |
| PATCH | `/api/risk-rules/{id}` | 更新规则 |
| DELETE | `/api/risk-rules/{id}` | 删除规则 |
| GET | `/api/admin/circuit-breakers` | 熔断列表 |
| GET | `/api/admin/circuit-breakers/{id}` | 熔断详情 |
| POST | `/api/admin/circuit-breakers` | 手动触发熔断 |
| POST | `/api/admin/circuit-breakers/{id}/recover` | 解除熔断 |

---

## 十一、分享与访客

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/instances/{id}/share-tokens` | 生成分享 token |
| GET | `/api/share-tokens` | 分享 token 列表 |
| DELETE | `/api/share-tokens/{id}` | 撤销分享 token |
| GET | `/view/{token}` | 访客只读访问 |

---

## 十二、审计与系统

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/admin/audit-logs` | 审计日志列表（管理员） |
| GET | `/api/admin/audit-logs/{id}` | 审计日志详情 |
| GET | `/api/admin/system-logs` | 全局日志 |
| GET | `/api/admin/overview` | 系统总览 |
| GET | `/api/admin/configs` | 读取系统配置 |
| PATCH | `/api/super/configs` | 更新系统配置 |
| GET | `/api/admin/users` | 所有用户 |
| POST | `/api/admin/users/{id}/disable` | 禁用用户 |
| POST | `/api/admin/users/{id}/enable` | 启用用户 |
| PATCH | `/api/super/users/{id}/role` | 修改角色 |
| DELETE | `/api/super/users/{id}` | 硬删除用户 |
| GET | `/api/admin/instances` | 所有实例 |
| POST | `/api/admin/instances/{id}/force-stop` | 强制停止实例 |
| GET | `/health` | 健康检查 |
| GET | `/metrics` | 监控指标 |

---

## 十二之二、补充接口

见《接口契约》第二十二节。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/orders/export` | 导出订单 CSV |
| GET | `/api/paper/compare/export` | 导出多盘对比 CSV |
| GET | `/api/user/notification-settings` | 通知设置 |
| PATCH | `/api/user/notification-settings` | 修改通知设置 |
| GET | `/api/user/preferences` | 偏好 |
| PATCH | `/api/user/preferences` | 修改偏好 |
| POST | `/api/admin/users/{id}/reset-password` | 重置用户密码 |
| GET | `/api/admin/users/{id}` | 用户详情 |
| GET | `/api/admin/accounts` | 所有账号 |
| POST | `/api/admin/accounts/{id}/disable` | 强制禁用账号 |
| POST | `/api/admin/accounts/{id}/enable` | 启用账号 |
| GET | `/api/admin/plugin-bindings/all` | 全部三级绑定 |
| DELETE | `/api/admin/plugins/{id}` | 卸载插件 |
| GET | `/api/admin/plugins/network-hosts` | 出网 hosts 汇总 |
| GET | `/api/admin/invites` | 邀请码列表 |
| POST | `/api/admin/invites` | 生成邀请码 |
| POST | `/api/admin/invites/{id}/disable` | 禁用邀请码 |
| POST | `/api/admin/invites/{id}/enable` | 启用邀请码 |
| GET | `/api/admin/invites/{id}/usages` | 使用记录 |
| GET | `/api/super/backups` | 备份列表 |
| POST | `/api/super/backups` | 手动备份 |
| GET | `/api/super/backups/{id}/download` | 下载备份 |
| POST | `/api/super/backups/{id}/restore` | 恢复备份 |

## 十三、WebSocket 频道

| 频道 | 路径 | 鉴权 | 用途 |
|---|---|---|---|
| 用户 | `/ws/user` | `?token=xxx` | 订阅自己实例的订单、日志、余额 |
| 管理员 | `/ws/admin` | `?token=xxx` | 订阅全局事件 |
| 访客 | `/ws/view/{token}` | 路径 token | 订阅该实例的公开事件 |

订阅格式：

```json
{ "action": "subscribe", "topics": ["instance.1.orders", "instance.1.logs", "account.1.balance"] }
```

推送格式：

```json
{ "topic": "instance.1.orders", "event": "order.filled", "data": {}, "timestamp": 1690000000000 }
```

事件类型：

| 事件 | 说明 |
|---|---|
| `instance.status` | 实例状态变更 |
| `instance.error` | 实例错误 |
| `order.created` | 订单创建 |
| `order.filled` | 订单成交 |
| `order.failed` | 订单失败 |
| `position.changed` | 持仓变更 |
| `account.status` | 账号状态变更 |
| `risk.alert` | 风控告警 |
| `circuit_breaker.triggered` | 熔断触发 |
| `system.alert` | 系统告警 |

插件绑定被自动停用、插件加载失败等插件侧变动，统一通过 `system.alert` 下发到 `/ws/admin`，不新增事件名。

---

## 十四、限流

| 范围 | 限制 |
|---|---|
| `/api/auth/login` | 每 IP 每分钟 10 次 |
| `/api/auth/register` | 每 IP 每小时 5 次 |
| `/api/auth/forgot-password` | 每邮箱每小时 3 次 |
| `/api/*` | 每用户每分钟 120 次 |
| `/api/admin/*` | 每管理员每分钟 300 次 |
| `/api/super/*` | 每 super_admin 每分钟 300 次 |
| `/view/{token}` | 每 token 每分钟 60 次 |
| WebSocket | 每连接每分钟 100 条消息 |

超限返回 429 与 `Retry-After` 头，错误码见《错误码》429xx 段。

---

## 十五、本文档的边界

**定义了：**

- 全部 HTTP 端点的索引与归属
- 策略、插件市场、插件绑定与扩展管理的端点
- WebSocket 频道与事件清单
- 公共约定：认证头、响应包装、分页、幂等
- 限流额度

**未定义（留给后续文档）：**

- 每个端点的请求体与响应体全文 → 《接口契约》
- 错误码含义与处理建议 → 《错误码》
- 插件作用域与拦截协议的完整规则 → 《插件规范》
- 页面如何使用这些接口 → 《前端页面结构》
- 限流的具体实现与存储 → 《引擎设计》

**本文档确认无偏后，再开《术语表》。**
