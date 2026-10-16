import axios, { AxiosError, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { tokenStore } from '@/utils/token'
import { P } from './paths'
import type { Envelope, Query } from './types'

export class ApiError extends Error {
  code: number
  status: number
  errors: { field: string; reason: string }[]
  data: unknown
  requestId?: string
  network: boolean

  constructor(opts: {
    code: number
    status: number
    message: string
    errors?: { field: string; reason: string }[]
    data?: unknown
    requestId?: string
    network?: boolean
  }) {
    super(opts.message)
    this.code = opts.code
    this.status = opts.status
    this.errors = opts.errors ?? []
    this.data = opts.data
    this.requestId = opts.requestId
    this.network = opts.network ?? false
  }
}

export interface ReqOptions extends AxiosRequestConfig {
  silent?: boolean
  skipAuth?: boolean
  _retried?: boolean
  _retried429?: boolean
}

type ErrorHook = (err: ApiError, opts: ReqOptions) => void
type AuthLostHook = () => void

let onError: ErrorHook = () => {}
let onAuthLost: AuthLostHook = () => {}
export function setHttpHooks(h: { onError?: ErrorHook; onAuthLost?: AuthLostHook }) {
  if (h.onError) onError = h.onError
  if (h.onAuthLost) onAuthLost = h.onAuthLost
}

const client = axios.create({ timeout: 30000 })

function serializeParams(params: Query): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v)) {
      if (v.length) sp.append(k, v.join(','))
    } else sp.append(k, String(v))
  }
  return sp.toString()
}

client.interceptors.request.use((cfg) => {
  const opts = cfg as ReqOptions
  if (!opts.skipAuth && tokenStore.access) {
    cfg.headers.set('Authorization', `Bearer ${tokenStore.access}`)
  }
  cfg.paramsSerializer = { serialize: (p) => serializeParams(p as Query) }
  return cfg
})

let refreshing: Promise<boolean> | null = null

async function doRefresh(): Promise<boolean> {
  const rt = tokenStore.refresh
  if (!rt) return false
  try {
    const res = await axios.post<Envelope<{ access_token: string; refresh_token?: string }>>(
      P.auth.refresh,
      { refresh_token: rt },
      { timeout: 15000 },
    )
    if (res.data?.code === 0 && res.data.data?.access_token) {
      tokenStore.set(res.data.data.access_token, res.data.data.refresh_token)
      return true
    }
  } catch {
    /* fall through */
  }
  return false
}

export function refreshToken(): Promise<boolean> {
  if (!refreshing) {
    refreshing = doRefresh().finally(() => {
      refreshing = null
    })
  }
  return refreshing
}

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err
  const ax = err as AxiosError<Envelope>
  if (ax?.response) {
    const body = ax.response.data
    if (body && typeof body === 'object' && 'code' in body) {
      return new ApiError({
        code: body.code,
        status: ax.response.status,
        message: body.message || ax.message,
        errors: body.errors,
        data: body.data,
        requestId: body.request_id,
      })
    }
    return new ApiError({ code: ax.response.status * 100, status: ax.response.status, message: ax.message })
  }
  return new ApiError({ code: 0, status: 0, message: ax?.message || 'Network Error', network: true })
}

async function send<T>(cfg: ReqOptions): Promise<T> {
  try {
    const res: AxiosResponse<Envelope<T> | T> = await client.request(cfg)
    if (cfg.responseType === 'blob' || cfg.responseType === 'text') return res.data as T
    if (res.status === 204 || res.data === '' || res.data == null) return undefined as T
    const body = res.data as Envelope<T>
    if (typeof body === 'object' && body !== null && 'code' in body) {
      if (body.code !== 0) {
        throw new ApiError({
          code: body.code,
          status: res.status,
          message: body.message,
          errors: body.errors,
          data: body.data,
          requestId: body.request_id,
        })
      }
      return body.data
    }
    return res.data as T
  } catch (raw) {
    const err = toApiError(raw)
    // 429：只读请求按 Retry-After 等待后重试一次（上限 5 秒），写操作不自动重试
    if (err.status === 429 && !cfg._retried429 && (cfg.method ?? 'GET').toUpperCase() === 'GET') {
      const ra = Number((raw as AxiosError)?.response?.headers?.['retry-after'])
      const waitMs = Math.min(Number.isFinite(ra) && ra > 0 ? ra * 1000 : 1500, 5000)
      await new Promise((r) => setTimeout(r, waitMs))
      return send<T>({ ...cfg, _retried429: true })
    }
    if (err.status === 401 && err.code === 40103 && !cfg._retried && !cfg.skipAuth) {
      const ok = await refreshToken()
      if (ok) return send<T>({ ...cfg, _retried: true })
      onAuthLost()
      throw err
    }
    if (err.status === 401 && !cfg.skipAuth) {
      onAuthLost()
    }
    if (!cfg.silent) onError(err, cfg)
    throw err
  }
}

export const http = {
  get<T>(url: string, params?: Query, opts: ReqOptions = {}) {
    return send<T>({ ...opts, method: 'GET', url, params })
  },
  post<T>(url: string, data?: unknown, opts: ReqOptions = {}) {
    return send<T>({ ...opts, method: 'POST', url, data })
  },
  put<T>(url: string, data?: unknown, opts: ReqOptions = {}) {
    return send<T>({ ...opts, method: 'PUT', url, data })
  },
  patch<T>(url: string, data?: unknown, opts: ReqOptions = {}) {
    return send<T>({ ...opts, method: 'PATCH', url, data })
  },
  delete<T>(url: string, opts: ReqOptions = {}) {
    return send<T>({ ...opts, method: 'DELETE', url })
  },
  async download(url: string, params: Query | undefined, filename: string) {
    const blob = await send<Blob>({ method: 'GET', url, params, responseType: 'blob' })
    const href = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = href
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(href), 1000)
  },
}

export function fieldErrors(err: unknown): Record<string, string> {
  const out: Record<string, string> = {}
  if (!(err instanceof ApiError)) return out
  for (const e of err.errors) out[e.field] = e.reason
  const d = err.data as { field?: string; reason?: string } | null
  if (d && typeof d === 'object' && typeof d.field === 'string' && d.reason) out[d.field] = d.reason
  return out
}
