import { reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError } from '@/api/http'
import type { PluginRejection } from '@/api/types'
import { t, te } from '@/i18n'

export const PLUGIN_CODES = [42290, 42291, 42292] as const
export type PluginCode = (typeof PLUGIN_CODES)[number]

export const pluginBlock = reactive({
  visible: false,
  code: 42290 as PluginCode,
  info: null as PluginRejection | null,
  message: '',
  requestId: '' as string | undefined,
})

export function isPluginError(err: unknown): err is ApiError {
  return err instanceof ApiError && (PLUGIN_CODES as readonly number[]).includes(err.code)
}

export function showPluginBlock(err: ApiError) {
  pluginBlock.code = err.code as PluginCode
  pluginBlock.info = (err.data as PluginRejection) ?? null
  pluginBlock.message = err.message
  pluginBlock.requestId = err.requestId
  pluginBlock.visible = true
}

export function describeError(err: unknown): string {
  if (!(err instanceof ApiError)) return err instanceof Error ? err.message : String(err)
  if (err.network) return t('error.network')
  const key = `codes.${err.code}`
  const base = err.message || (te(key) ? t(key) : '')
  if (base) return base
  if (err.status === 401) return t('error.unauthorized')
  if (err.status === 403) return t('error.forbidden')
  if (err.status === 404) return t('error.notFound')
  if (err.status === 429) return t('error.rateLimited')
  if (err.status === 503) return t('error.unavailable')
  if (err.status >= 500) return t('error.server')
  return t('common.unknown')
}

export function handleError(err: unknown) {
  if (isPluginError(err)) {
    showPluginBlock(err)
    return
  }
  if (err instanceof ApiError && err.status === 401) return
  const msg = describeError(err)
  const rid = err instanceof ApiError && err.requestId && err.status >= 500 ? `（${t('error.requestId', { id: err.requestId })}）` : ''
  ElMessage({ type: 'error', message: msg + rid, grouping: true, duration: 4000 })
}

export async function attempt<T>(fn: () => Promise<T>, successMsg?: string | false): Promise<T | undefined> {
  try {
    const r = await fn()
    if (successMsg !== false) ElMessage.success(successMsg || t('common.success'))
    return r
  } catch (e) {
    handleError(e)
    return undefined
  }
}
