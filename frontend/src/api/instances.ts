import { http } from './http'
import { P } from './paths'
import type { AuditLog, Id, Instance, LogEntry, Order, Page, Position, Query, ShareToken } from './types'

export interface InstanceInput {
  name: string
  strategy_id: string
  symbol: string
  market_type?: 'spot' | 'futures'
  account_id: number | null
  paper: boolean
  paper_balance: number | null
  paper_config: Record<string, unknown> | null
  params: Record<string, unknown>
  stop_action: string
}

export interface InstanceStatusInfo {
  instance_id: number
  status: Instance['status']
  running_since: string | null
  equity: number | null
  balance: number | null
  positions: Position[]
  last_tick_at: string | null
  consecutive_errors: number
  strategy_status: { summary: string; next_at?: number; at?: number } | null
  last_log: { at: string; message: string } | null
}

export interface PaperAccount {
  instance_id: number
  balance: number
  equity: number
  realized_pnl: number
  unrealized_pnl: number
  initial_balance: number
  peak_equity: number
  positions: Position[]
}

export interface Snapshot {
  timestamp?: string
  ts?: string
  at?: string
  equity: number
}

export interface ShareInput {
  expires_in_days: number
  show_equity: boolean
  show_positions: boolean
  show_orders: boolean
  show_params: boolean
  show_logs: boolean
}

type Action = { instance_id: number; status: Instance['status'] }

export const instancesApi = {
  list: (q: Query) => http.get<Page<Instance>>(P.instances.list, q),
  get: (id: Id) => http.get<Instance>(P.instances.one(id)),
  create: (body: InstanceInput) => http.post<Instance>(P.instances.list, body, { silent: true }),
  update: (id: Id, body: { name?: string; params?: Record<string, unknown>; stop_action?: string; paper_config?: Record<string, unknown> }) =>
    http.patch<Instance>(P.instances.one(id), body, { silent: true }),
  start: (id: Id) => http.post<Action>(P.instances.start(id), undefined, { silent: true }),
  stop: (id: Id) => http.post<Action>(P.instances.stop(id), undefined, { silent: true }),
  pause: (id: Id) => http.post<Action>(P.instances.pause(id), undefined, { silent: true }),
  resume: (id: Id) => http.post<Action>(P.instances.resume(id), undefined, { silent: true }),
  status: (id: Id) => http.get<InstanceStatusInfo>(P.instances.status(id), undefined, { silent: true }),
  orders: (id: Id, q: Query) => http.get<Page<Order>>(P.instances.orders(id), q),
  positions: (id: Id) => http.get<{ positions: Position[] }>(P.instances.positions(id)),
  logs: (id: Id, q: Query) => http.get<Page<LogEntry>>(P.instances.logs(id), q),
  auditLogs: (id: Id, q: Query) => http.get<Page<AuditLog>>(P.instances.auditLogs(id), q),
  changeStrategy: (id: Id, body: { strategy_id: string; params: Record<string, unknown> }) =>
    http.post<Instance>(P.instances.changeStrategy(id), body, { silent: true }),
  remove: (id: Id) => http.delete<void>(P.instances.one(id), { silent: true }),
  resetPaper: (id: Id, body: { keep_orders: boolean; keep_snapshots: boolean }) => http.post<void>(P.instances.resetPaper(id), body),
  closePosition: (id: Id, pid: Id, amount?: number | null) =>
    http.post<{ result?: unknown }>(P.instances.closePosition(id, pid), { amount: amount ?? null }, { silent: true }),
  closeBatch: (id: Id, position_ids: string[]) => http.post<void>(P.instances.closeBatch(id), { position_ids }, { silent: true }),
  closeAll: (id: Id) => http.post<{ results?: unknown[] }>(P.instances.closeAll(id), undefined, { silent: true }),
  // position_id 不传 = 平掉该实例全部持仓；返回 failed = 未成交笔数（全部未成交时接口直接报错）
  forceClose: (id: Id, position_id?: string) =>
    http.post<{ results: unknown[]; failed: number }>(P.instances.forceClose(id), position_id ? { position_id } : {}, { silent: true }),
  cancelOrder: (id: Id, oid: Id) => http.post<void>(P.instances.cancelOrder(id, oid), undefined, { silent: true }),
  cancelAll: (id: Id) => http.post<void>(P.instances.cancelAll(id), undefined, { silent: true }),
  paperAccount: (id: Id) => http.get<PaperAccount>(P.instances.paperAccount(id), undefined, { silent: true }),
  paperSnapshots: (id: Id, q: Query) =>
    http.get<Snapshot[] | Page<Snapshot> | { items: Snapshot[] }>(P.instances.paperSnapshots(id), q, { silent: true }),
  createShare: (id: Id, body: ShareInput) => http.post<ShareToken>(P.instances.shareTokens(id), body),
}

export const shareApi = {
  list: (q: Query) => http.get<Page<ShareToken>>(P.share.list, q),
  revoke: (id: Id) => http.delete<void>(P.share.one(id)),
}
