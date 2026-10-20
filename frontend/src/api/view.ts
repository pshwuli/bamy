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
  get: async (token: string) => {
    const d = await http.get<ViewData>(P.share.view(token), undefined, {
      skipAuth: true,
      silent: true,
      headers: { Accept: 'application/json' },
    })
    // 拿到的不是对象（比如被缓存层换成了 HTML 页面）就按加载失败处理，不要渲染出一个全是 "-" 的空页面
    if (!d || typeof d !== 'object' || !('instance_name' in d)) throw new Error('invalid share response')
    return d
  },
}
