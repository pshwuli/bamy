import { http } from './http'
import { P } from './paths'
import type { Account, Id, Order, Page, Position, Query } from './types'

export interface AccountInput {
  name: string
  api_key: string
  api_secret: string
  env: string
  market_type: string
  readonly: boolean
  remark?: string
}

export interface AccountBalance {
  account_id: number
  total_usdt: number
  assets: Record<string, number | { free?: number; locked?: number; total?: number }>
}

export interface AccountHealth {
  online_rate: number | null
  avg_latency_ms: number | null
  last_check_at: string | null
  recent_checks: {
    at?: string
    checked_at?: string
    ok?: boolean
    success?: boolean
    latency_ms?: number | null
    error?: string | null
    error_code?: string | null
    error_message?: string | null
  }[]
}

export const accountsApi = {
  list: (q: Query) => http.get<Page<Account>>(P.accounts.list, q),
  get: (id: Id) => http.get<Account>(P.accounts.one(id)),
  create: (body: AccountInput) => http.post<Account>(P.accounts.list, body),
  batch: (accounts: AccountInput[]) =>
    http.post<{ success: number; failed: number; errors: { index: number; reason: string }[] }>(P.accounts.batch, { accounts }),
  update: (id: Id, body: { name?: string; readonly?: boolean; remark?: string }) => http.patch<Account>(P.accounts.one(id), body),
  credentials: (id: Id, body: { api_key: string; api_secret: string }) => http.post<void>(P.accounts.credentials(id), body),
  verify: (id: Id) => http.post<{ ok?: boolean; status?: string; message?: string; permissions?: string[] }>(P.accounts.verify(id)),
  balance: (id: Id) => http.get<AccountBalance>(P.accounts.balance(id)),
  positions: (id: Id) => http.get<{ positions: Position[] } | Position[]>(P.accounts.positions(id)),
  orders: (id: Id, q: Query) => http.get<Page<Order>>(P.accounts.orders(id), q),
  health: (id: Id, silent = false) => http.get<AccountHealth>(P.accounts.health(id), undefined, { silent }),
  remove: (id: Id) => http.delete<void>(P.accounts.one(id)),
}
