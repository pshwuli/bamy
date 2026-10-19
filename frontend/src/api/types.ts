export type Id = number | string
export type Role = 'user' | 'admin' | 'super_admin'

export interface Envelope<T = unknown> {
  code: number
  message: string
  data: T
  request_id?: string
  timestamp?: number
  errors?: { field: string; reason: string }[]
}

export interface Pagination {
  page: number
  page_size: number
  total: number
  total_pages: number
  has_next: boolean
  has_prev: boolean
  next_cursor?: string | null
}

export interface Page<T> {
  items: T[]
  pagination: Pagination
}

export type Query = Record<string, string | number | boolean | null | undefined | (string | number)[]>

export interface User {
  id: number
  username: string
  role: Role
  email?: string | null
  nickname?: string | null
  status?: string
  two_factor_enabled?: boolean
  created_at?: string
}

export interface LoginResult {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: User
}

export type InstanceStatus =
  | 'created' | 'starting' | 'running' | 'pausing' | 'paused'
  | 'resuming' | 'stopping' | 'stopped' | 'error' | 'deleted'

export type OrderStatus =
  | 'pending' | 'submitting' | 'submitted' | 'partial' | 'filled'
  | 'canceling' | 'canceled' | 'rejected' | 'unknown' | 'failed'

export interface Account {
  id: number
  name: string
  api_key_masked?: string
  exchange?: string
  env: 'mainnet' | 'testnet' | string
  market_type: 'spot' | 'futures' | string
  position_mode?: 'oneway' | 'hedge' | null
  readonly: boolean
  status: string
  remark?: string | null
  instance_count?: number
  last_active_at?: string | null
  last_verified_at?: string | null
  created_at?: string
  owner_id?: number
  owner_username?: string
}

export interface Instance {
  id: number
  name: string
  strategy_id: string
  strategy_name?: string
  strategy_version?: string
  symbol: string
  market_type?: string
  paper: boolean
  account_id: number | null
  account_name?: string | null
  status: InstanceStatus
  enabled?: boolean
  equity?: number | null
  initial_balance?: number | null
  return_rate?: number | null
  return_pct?: number | null
  initial_equity?: number | null
  pnl?: number | null
  paper_config?: Record<string, unknown> | null
  running_since?: string | null
  last_active_at?: string | null
  params?: Record<string, unknown>
  stop_action?: 'keep' | 'close' | string
  last_error?: string | null
  created_at?: string
  owner_id?: number
  owner_username?: string
}

export interface Position {
  id: string
  symbol: string
  side: string
  position_side?: 'long' | 'short' | string | null
  amount: number
  entry_price?: number
  mark_price?: number
  unrealized_pnl?: number
  pnl_rate?: number
  label?: string
  leverage?: number | null
  margin?: number | null
  liquidation_price?: number | null
}

export interface Order {
  id: number
  client_order_id: string
  instance_id: number
  instance_name?: string
  account_id?: number | null
  symbol: string
  side: 'buy' | 'sell' | string
  position_side?: 'long' | 'short' | string | null
  type: string
  amount: number
  price?: number | null
  filled_amount?: number
  filled_price?: number | null
  status: OrderStatus
  source: string
  reason?: string | null
  reject_reason?: string | null
  plugin_rejection?: PluginRejection | null
  created_at: string
}

export type FieldType =
  | 'int' | 'float' | 'string' | 'text' | 'bool' | 'select' | 'multiselect'
  | 'secret' | 'symbol' | 'date' | 'time' | 'datetime' | 'duration' | 'percent'

export interface SchemaField {
  key: string
  label?: string
  type: FieldType
  default?: unknown
  required?: boolean
  help?: string
  placeholder?: string
  group?: string
  depends_on?: { key: string; value: unknown }
  min?: number
  max?: number
  step?: number
  suffix?: string
  pattern?: string
  options?: { value: string | number | boolean; label: string }[]
  max_length?: number
  secret?: boolean
}

export type LoadState = 'loaded' | 'failed' | 'outdated'

export interface Strategy {
  id: string
  name: string
  version: string
  author?: string
  description?: string
  icon?: string
  tags?: string[]
  capabilities?: Record<string, boolean>
  loaded: boolean
  load_status?: LoadState
  error?: string | null
  params_count?: number
  params_schema?: SchemaField[]
  runtime?: { tick_interval?: number; symbols?: string[]; market_type?: string[]; scan?: boolean }
  dependencies?: Record<string, unknown>
  instance_count?: number
  suitability?: StrategySuitability | null
  backtest?: StrategyBacktest | null
}

/** strategy.yaml 的 suitability 段：适合多少资金、什么行情、什么交易对、风险档位 */
export interface StrategySuitability {
  capital?: { min_usdt?: number; sweet_spot_usdt?: [number, number]; note?: string }
  market?: string
  symbols?: string
  risk?: { level: '低' | '中' | '高' | '极高' | string; note?: string }
}

/** strategy.yaml 的 backtest 段：样本外回测摘要（字段为已格式化的字符串，原样展示） */
export interface StrategyBacktest {
  period?: string
  cost?: string
  summary?: string
  caveat?: string
  results?: { symbol: string; win_rate?: string; profit_factor?: number; cagr?: string; max_drawdown?: string; buy_hold_drawdown?: string }[]
}

export type Scope = 'global' | 'user' | 'account'

export interface PluginHook {
  type: 'event' | 'checkpoint'
  event?: string
  checkpoint?: string
  fail_mode?: 'open' | 'closed'
  timeout_ms?: number
}

export interface Plugin {
  id: string
  name: string
  version: string
  author?: string
  description?: string
  icon?: string
  tags?: string[]
  scopes: Scope[]
  default_scope?: Scope
  data_scope: 'own' | 'all'
  priority?: number
  loaded: boolean
  load_status?: LoadState
  error?: string | null
  installable?: boolean
  allow_multiple?: boolean
  network_hosts?: string[]
  hooks?: PluginHook[]
  settings_schema?: SchemaField[]
  network?: { enabled: boolean; hosts: string[]; timeout_ms?: number }
  binding_count?: number
  bindings_by_scope?: Partial<Record<Scope, number>>
}

export type BindingStatus = 'enabled' | 'disabled' | 'invalid'

export interface Binding {
  id: number
  plugin_id: string
  plugin_name?: string
  plugin_version?: string
  scope: Scope
  scope_id: number | null
  scope_name?: string | null
  owner_id?: number | null
  owner_username?: string | null
  enabled: boolean
  status: BindingStatus
  auto_disabled?: boolean
  priority: number
  settings?: Record<string, unknown>
  overridden?: boolean
  calls?: number
  errors?: number
  created_at?: string
  updated_at?: string
}

export interface BindingHealth {
  binding_id: number
  plugin_id: string
  status: BindingStatus
  auto_disabled: boolean
  event_calls: number
  checkpoint_calls: number
  error_count: number
  timeout_count: number
  consecutive_errors: number
  avg_latency_ms: number
  last_error?: { message: string; at: string } | null
  since?: string
}

export interface PluginHealth {
  plugin_id: string
  bindings: { total: number; enabled: number; disabled: number; invalid: number; auto_disabled: number }
  event_calls: number
  checkpoint_calls: number
  error_count: number
  timeout_count: number
  avg_latency_ms: number
  last_error?: { binding_id?: number; message: string; at: string } | null
  worst_bindings?: { binding_id: number; scope: Scope; scope_id: number | null; error_count: number }[]
}

export interface PluginRejection {
  plugin_id: string
  plugin_name?: string
  binding_id?: number
  effective_scope?: Scope
  checkpoint?: string
  reason?: string
}

export interface AuditLog {
  id: number
  timestamp?: string
  created_at?: string
  actor_id?: number | null
  actor_username?: string | null
  action: string
  target_type?: string
  target_id?: string | number | null
  result?: 'success' | 'failure' | string
  source?: string
  old_value?: unknown
  new_value?: unknown
  detail?: unknown
  ip?: string
  user_agent?: string
  effective_scope?: Scope
}

export interface LogEntry {
  id?: number | string
  timestamp?: string
  created_at?: string
  level: string
  module?: string
  message: string
  instance_id?: number | null
  account_id?: number | null
}

export interface ShareToken {
  id: number
  token: string
  url?: string
  instance_id: number
  instance_name?: string
  expires_at?: string | null
  revoked?: boolean
  view_count?: number
  last_viewed_at?: string | null
  show_equity?: boolean
  show_positions?: boolean
  show_orders?: boolean
  show_params?: boolean
  show_logs?: boolean
  created_at?: string
}

export interface CompareResult {
  timeline: string[]
  series: { instance_id: number; name: string; values: (number | null)[] }[]
}

export interface CompareMetric {
  instance_id: number
  total_return?: number
  max_drawdown?: number
  sharpe?: number
  win_rate?: number
  profit_factor?: number
  order_count?: number
  running_hours?: number
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Row = any
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Filters = Record<string, any>
