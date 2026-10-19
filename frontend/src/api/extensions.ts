import { http } from './http'
import { P } from './paths'
import type { Binding, BindingHealth, Id, Page, Plugin, PluginHealth, Query, Scope, Strategy } from './types'

export interface ReloadResult {
  strategies: { loaded: number; failed: number; errors: { id: string; reason: string }[] }
  plugins: { loaded: number; failed: number; invalidated_bindings?: number; errors: { id: string; reason: string }[] }
}

export interface BindingInput {
  plugin_id: string
  scope: Exclude<Scope, 'global'>
  scope_id: number
  settings?: Record<string, unknown>
  priority?: number
  enabled?: boolean
}

export interface BindingPatch {
  settings?: Record<string, unknown>
  priority?: number
  enabled?: boolean
}

export const strategiesApi = {
  list: (q: Query = {}) => http.get<Page<Strategy> | { items: Strategy[] }>(P.strategies.list, q),
  get: (id: Id) => http.get<Strategy>(P.strategies.one(id)),
}

export const pluginsApi = {
  list: (q: Query = {}) => http.get<Page<Plugin> | { items: Plugin[] }>(P.plugins.list, q),
  get: (id: Id) => http.get<Plugin>(P.plugins.one(id)),
}

export type ActionResult = Record<string, unknown> & { status?: string; qr_url?: string; interval?: number; settings_updated?: boolean }

export const bindingsApi = {
  list: (q: Query) => http.get<Page<Binding>>(P.bindings.list, q),
  create: (body: BindingInput) => http.post<Binding>(P.bindings.list, body, { silent: true }),
  update: (id: Id, body: BindingPatch) => http.patch<Binding>(P.bindings.one(id), body, { silent: true }),
  remove: (id: Id) => http.delete<void>(P.bindings.one(id)),
  health: (id: Id) => http.get<BindingHealth>(P.bindings.health(id)),
  runAction: (id: Id, action: string, payload: Record<string, unknown> = {}) =>
    http.post<ActionResult>(P.bindings.action(id, action), { payload }, { silent: true }),
}

export const adminExtApi = {
  reload: () => http.post<ReloadResult>(P.admin.pluginsReload),
  uninstall: (id: Id) => http.delete<void>(P.admin.plugin(id)),
  // 撤销卸载：插件重新出现在市场，之前因卸载失效的绑定恢复为可用
  reinstall: (id: Id) => http.post<unknown>(`${P.admin.plugin(id)}/install`),
  pluginHealth: (id: Id) => http.get<PluginHealth>(P.admin.pluginHealth(id)),
  networkHosts: () => http.get<{ items: { host: string; plugins: string[]; bindings: number }[] }>(P.admin.networkHosts),
  globalBindings: (q: Query) => http.get<Page<Binding>>(P.admin.globalBindings, q),
  createGlobal: (body: { plugin_id: string; settings?: Record<string, unknown>; priority?: number; enabled?: boolean }) =>
    http.post<Binding>(P.admin.globalBindings, body, { silent: true }),
  allBindings: (q: Query) => http.get<Page<Binding>>(P.admin.allBindings, q),
  updateBinding: (id: Id, body: BindingPatch) => http.patch<Binding>(P.admin.binding(id), body, { silent: true }),
  removeBinding: (id: Id) => http.delete<void>(P.admin.binding(id)),
  runBindingAction: (id: Id, action: string, payload: Record<string, unknown> = {}) =>
    http.post<ActionResult>(P.admin.bindingAction(id, action), { payload }, { silent: true }),
}

export function itemsOf<T>(d: Page<T> | { items: T[] } | T[] | null | undefined): T[] {
  if (!d) return []
  if (Array.isArray(d)) return d
  return d.items ?? []
}
