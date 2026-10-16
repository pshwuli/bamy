import { http } from './http'
import { P } from './paths'
import type { CompareMetric, CompareResult, Id, Order, Page, Query } from './types'

export interface Fill {
  id?: number | string
  price: number
  amount: number
  fee?: number
  fee_asset?: string
  trade_id?: string
  created_at?: string
  timestamp?: string
}

export const ordersApi = {
  list: (q: Query) => http.get<Page<Order>>(P.orders.list, q),
  get: (id: Id) => http.get<Order>(P.orders.one(id)),
  fills: (id: Id) => http.get<Fill[] | { items: Fill[] }>(P.orders.fills(id)),
  export: (q: Query) => http.download(P.orders.export, q, `orders-${Date.now()}.csv`),
}

export const paperApi = {
  compare: (q: Query) => http.get<CompareResult>(P.paper.compare, q),
  metrics: (q: Query) => http.get<{ metrics: CompareMetric[] }>(P.paper.metrics, q),
  export: (q: Query) => http.download(P.paper.export, q, `paper-compare-${Date.now()}.csv`),
}
