import { http } from './http'
import { P } from './paths'
import type { LogEntry, Order, Position } from './types'

export interface ViewData {
  instance_name: string
  strategy_name: string
  symbol: string
  paper?: boolean
  status?: string
  equity?: number | null
  initial_balance?: number | null
  return_rate?: number | null
  equity_curve?: { t?: string; timestamp?: string; equity: number }[]
  positions?: Position[] | null
  orders?: Order[] | null
  params?: Record<string, unknown> | null
  logs?: LogEntry[] | null
  expires_at?: string | null
}

export const viewApi = {
  get: (token: string) =>
    http.get<ViewData>(P.share.view(token), undefined, {
      skipAuth: true,
      silent: true,
      headers: { Accept: 'application/json' },
    }),
}
