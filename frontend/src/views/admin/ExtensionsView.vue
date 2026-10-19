<script setup lang="ts">
import { computed, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { adminExtApi, pluginsApi, strategiesApi, itemsOf, type ReloadResult } from '@/api/extensions'
import { adminApi, type AdminUser } from '@/api/admin'
import { ApiError, fieldErrors } from '@/api/http'
import type { Binding, Instance, Page, Plugin, PluginHealth, Query, Scope, Strategy } from '@/api/types'
import { attempt, handleError } from '@/composables/useErrors'
import { confirm, confirmTyped } from '@/composables/useConfirm'
import { useTable } from '@/composables/useTable'
import { useAuth } from '@/stores/auth'
import { mergeValues } from '@/utils/schema'
import { fmtNum, fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import Pager from '@/components/Pager.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import SchemaForm from '@/components/SchemaForm.vue'
import PluginActionButton from '@/components/PluginActionButton.vue'
import PluginMeta from '@/components/PluginMeta.vue'
import CapabilityBadges from '@/components/CapabilityBadges.vue'
import BindingHealthDialog from '@/components/BindingHealthDialog.vue'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = Record<PropertyKey, any>
type SRow = Strategy & { load_error?: string | null; usage?: { version_distribution?: Record<string, number>; user_count?: number; instance_count?: number } }
type PRow = Plugin & {
  load_error?: string | null
  has_closed_fail_mode?: boolean
  hooks_summary?: { events?: string[]; checkpoints?: string[] }
}
type BRow = Binding & {
  target_name?: string | null
  last_error?: string | null
  health?: { event_calls?: number; checkpoint_calls?: number; error_count?: number } | null
}
type Dist = { plugin_id: string; global: number; user: number; account: number; covered_accounts?: number; covered_instances?: number }

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuth()

// ================================================================ Tabs 与路由
const tab = ref<'strategies' | 'plugins'>(route.path.endsWith('/plugins') ? 'plugins' : 'strategies')
watch(
  () => route.path,
  (p) => {
    if (p.endsWith('/plugins')) tab.value = 'plugins'
    else if (p.endsWith('/strategies')) tab.value = 'strategies'
  },
)
function onTab(name: string | number) {
  const path = name === 'plugins' ? '/admin/plugins' : '/admin/strategies'
  if (route.path !== path) router.replace({ path })
}
const sub = ref<'list' | 'bindings' | 'health' | 'hosts'>('list')

// ================================================================ 公共
const asP = (r: Any) => r as PRow
const loadState = (x: Any): string =>
  x.load_status === 'uninstalled' ? 'failed' : x.load_status ?? (x.loaded ? 'loaded' : 'failed')
function bindingState(b: Any): 'enabled' | 'disabled' | 'invalid' | 'auto_disabled' {
  if (b.status === 'invalid') return 'invalid'
  if (b.auto_disabled || (b.enabled && b.status === 'disabled')) return 'auto_disabled'
  return b.enabled && b.status === 'enabled' ? 'enabled' : 'disabled'
}
const bCalls = (b: Any) => (b.health?.event_calls ?? 0) + (b.health?.checkpoint_calls ?? 0)
const bErrs = (b: Any) => b.health?.error_count ?? 0

// ================================================================ 重新扫描
const reloading = ref(false)
const reloadResult = ref<ReloadResult | null>(null)
const reloadOpen = ref(false)
async function reload() {
  if (!(await confirm(t('ext.reloadConfirm'), t('ext.reload')))) return
  reloading.value = true
  try {
    reloadResult.value = await adminExtApi.reload()
    reloadOpen.value = true
    await Promise.all([loadStrategies(), loadPlugins()])
    if (sub.value === 'bindings') void bt.load()
  } catch (e) {
    handleError(e)
  } finally {
    reloading.value = false
  }
}
const reloadErrors = computed(() => {
  const r = reloadResult.value
  if (!r) return []
  return [
    ...(r.strategies?.errors ?? []).map((e) => ({ kind: t('ext.tabStrategies'), ...e })),
    ...(r.plugins?.errors ?? []).map((e) => ({ kind: t('ext.tabPlugins'), ...e })),
  ]
})

// ================================================================ 策略管理
const strategies = shallowRef<SRow[]>([])
const strategyFailed = shallowRef<{ id: string; reason: string }[]>([])
const sLoading = ref(false)
const sError = ref<unknown>(null)
const sKeyword = ref('')
async function loadStrategies() {
  sLoading.value = true
  sError.value = null
  try {
    const d = (await strategiesApi.list()) as unknown as { items?: SRow[]; failed?: { id: string; reason: string }[] }
    strategies.value = d?.items ?? []
    strategyFailed.value = d?.failed ?? []
  } catch (e) {
    sError.value = e
  } finally {
    sLoading.value = false
  }
}
const sFiltered = computed(() => {
  const kw = sKeyword.value.trim().toLowerCase()
  return strategies.value.filter((s) => !kw || `${s.id} ${s.name} ${s.author ?? ''} ${(s.tags ?? []).join(' ')}`.toLowerCase().includes(kw))
})
const sFailedList = computed(() => {
  const m = new Map<string, string>()
  for (const s of strategies.value) if (loadState(s) === 'failed') m.set(s.id, s.load_error ?? s.error ?? '-')
  for (const f of strategyFailed.value) m.set(f.id, f.reason || m.get(f.id) || '-')
  return [...m.entries()].map(([id, reason]) => ({ id, reason }))
})
const sTotalInstances = computed(() => strategies.value.reduce((n, s) => n + (s.instance_count ?? 0), 0))

const sd = reactive({ open: false, loading: false, item: null as SRow | null, instances: [] as Instance[], instTotal: 0 })
async function openStrategy(s: Any) {
  sd.open = true
  sd.loading = true
  sd.item = s as SRow
  sd.instances = []
  sd.instTotal = 0
  const [detail, insts] = await Promise.allSettled([
    strategiesApi.get(s.id),
    adminApi.instances({ strategy_id: s.id, page: 1, page_size: 50 }),
  ])
  if (detail.status === 'fulfilled') sd.item = { ...(s as SRow), ...(detail.value as SRow) }
  else handleError(detail.reason)
  if (insts.status === 'fulfilled') {
    sd.instances = itemsOf<Instance>(insts.value)
    sd.instTotal = insts.value.pagination?.total ?? sd.instances.length
  }
  sd.loading = false
}
const sdUserCount = computed(() => sd.item?.usage?.user_count ?? new Set(sd.instances.map((i) => i.owner_id)).size)
const sdVersions = computed(() => {
  const v = sd.item?.usage?.version_distribution
  if (v) return Object.entries(v).map(([version, n]) => ({ version: version || '-', n }))
  const m = new Map<string, number>()
  for (const i of sd.instances) m.set(i.strategy_version || '-', (m.get(i.strategy_version || '-') ?? 0) + 1)
  return [...m.entries()].map(([version, n]) => ({ version, n }))
})
function viewInstances(id: string) {
  router.push({ path: '/admin/instances', query: { strategy_id: id } })
}
// ================================================================ 插件管理
const plugins = shallowRef<PRow[]>([])
const globalMap = shallowRef<Record<string, BRow>>({})
const pLoading = ref(false)
const pError = ref<unknown>(null)
const pKeyword = ref('')
const busy = ref<Record<string | number, boolean>>({})
async function loadPlugins() {
  pLoading.value = true
  pError.value = null
  try {
    const [ps, gs] = await Promise.all([
      pluginsApi.list().then((d) => itemsOf<PRow>(d)),
      adminExtApi.globalBindings({ page: 1, page_size: 200 }).then((d) => itemsOf<BRow>(d)).catch((e) => (handleError(e), [] as BRow[])),
    ])
    plugins.value = ps
    globalMap.value = Object.fromEntries(gs.map((g) => [g.plugin_id, g]))
  } catch (e) {
    pError.value = e
  } finally {
    pLoading.value = false
  }
}
const pFiltered = computed(() => {
  const kw = pKeyword.value.trim().toLowerCase()
  return plugins.value.filter((p) => !kw || `${p.id} ${p.name} ${(p.tags ?? []).join(' ')}`.toLowerCase().includes(kw))
})
const pFailed = computed(() => plugins.value.filter((p) => loadState(p) === 'failed'))
const pluginName = (id: string) => plugins.value.find((p) => p.id === id)?.name || id
const hasClosed = (p: Any) => !!p.has_closed_fail_mode || (p.hooks ?? []).some((h: Any) => h.type === 'checkpoint' && h.fail_mode === 'closed')

// 详情（含 hooks、settings_schema、network）
const detailCache = new Map<string, PRow>()
async function pluginDetail(id: string): Promise<PRow | null> {
  if (detailCache.has(id)) return detailCache.get(id)!
  try {
    const d = (await pluginsApi.get(id)) as PRow
    detailCache.set(id, d)
    return d
  } catch (e) {
    handleError(e)
    return null
  }
}
const pd = reactive({ open: false, loading: false, item: null as PRow | null })
async function openPlugin(p: Any) {
  pd.open = true
  pd.loading = true
  pd.item = p as PRow
  const d = await pluginDetail(p.id)
  if (d) pd.item = { ...(p as PRow), ...d }
  pd.loading = false
}

// 全局绑定启停
async function toggleGlobal(p: Any, on: boolean) {
  const g = globalMap.value[p.id]
  if (!g) return
  busy.value = { ...busy.value, [p.id]: true }
  const r = await attempt(() => adminExtApi.updateBinding(g.id, { enabled: on }))
  busy.value = { ...busy.value, [p.id]: false }
  if (r) globalMap.value = { ...globalMap.value, [p.id]: { ...g, ...(r as BRow) } }
}

// 新建 / 编辑全局绑定
const ge = reactive({
  open: false, loading: false, saving: false,
  plugin: null as PRow | null, binding: null as BRow | null,
  values: {} as Record<string, unknown>, priority: 100, enabled: true, extErrors: {} as Record<string, string>,
})
const geForm = ref<InstanceType<typeof SchemaForm> | null>(null)
async function openGlobal(p: Any) {
  const b = globalMap.value[p.id] ?? null
  Object.assign(ge, { open: true, loading: true, saving: false, plugin: p as PRow, binding: b, values: {}, priority: b?.priority ?? p.priority ?? 100, enabled: b?.enabled ?? true, extErrors: {} })
  const d = await pluginDetail(p.id)
  if (!d) {
    ge.open = false
    return
  }
  ge.plugin = { ...(p as PRow), ...d }
  ge.values = mergeValues(d.settings_schema ?? [], b?.settings ?? {})
  ge.loading = false
}
async function onGlobalActionDone() {
  await loadPlugins()
  const b = ge.plugin ? globalMap.value[ge.plugin.id] : null
  if (b && ge.plugin) {
    ge.binding = b
    ge.values = mergeValues(ge.plugin.settings_schema ?? [], b.settings ?? {})
  }
}
async function saveGlobal() {
  if (!ge.plugin || !geForm.value || !geForm.value.validate()) return
  if (!Number.isInteger(ge.priority)) return ElMessage.warning(t('extv.priorityInvalid'))
  ge.saving = true
  try {
    const settings = geForm.value.values()
    if (ge.binding) await adminExtApi.updateBinding(ge.binding.id, { settings, priority: ge.priority })
    else await adminExtApi.createGlobal({ plugin_id: ge.plugin.id, settings, priority: ge.priority, enabled: ge.enabled })
    ElMessage.success(t('common.success'))
    ge.open = false
    void loadPlugins()
  } catch (e) {
    if (e instanceof ApiError && e.code === 40913) ElMessage.warning(t('codes.40913'))
    else {
      ge.extErrors = fieldErrors(e)
      handleError(e)
    }
  } finally {
    ge.saving = false
  }
}

async function reinstall(p: Any) {
  const r = await attempt(() => adminExtApi.reinstall(p.id) as Promise<unknown>, t('extx.reinstallDone'))
  if (r !== undefined) void loadPlugins()
}
async function uninstall(p: Any) {
  if (!auth.isSuper) return
  if (!(await confirmTyped(t('ext.uninstallConfirm', { name: p.name || p.id }), p.id, t('ext.uninstall')))) return
  const r = await attempt(() => adminExtApi.uninstall(p.id) as Promise<unknown>, false)
  if (r !== undefined) {
    const n = (r as { invalidated_bindings?: number } | undefined)?.invalidated_bindings ?? 0
    ElMessage.success(t('extv.uninstallDone', { n }))
    detailCache.delete(p.id)
    void loadPlugins()
  }
}

function reviewHosts(p: Any) {
  sub.value = 'hosts'
  hostFocus.value = p.id
}

// ================================================================ 5.10 绑定总览
const users = shallowRef<AdminUser[]>([])
const distribution = shallowRef<Dist[]>([])
const bKeyword = ref('')
const bt = useTable<BRow>(
  async (q: Query) => {
    const d = (await adminExtApi.allBindings(q)) as Page<BRow> & { distribution?: Dist[] }
    distribution.value = d?.distribution ?? []
    return d
  },
  { owner_id: '', plugin_id: '', scope: '', status: '' },
  {
    immediate: false,
    transform: (q) => {
      // 后端 status 列与开关 enabled 分离：手动停用只改 enabled。自动停用 = status disabled 且 enabled；
      // 已启用/已停用无法由 status 单独表达，交给前端按 bindingState 再筛
      if (q.status === 'auto_disabled') return { ...q, status: 'disabled' }
      if (q.status === 'enabled' || q.status === 'manual_disabled') return { ...q, status: undefined }
      return q
    },
  },
)
const bRows = computed(() => {
  const kw = bKeyword.value.trim().toLowerCase()
  return bt.items.value.filter((b) => {
    const st = bindingState(b)
    if (bt.filters.status === 'auto_disabled' && st !== 'auto_disabled') return false
    if (bt.filters.status === 'manual_disabled' && st !== 'disabled') return false
    if (bt.filters.status === 'enabled' && st !== 'enabled') return false
    if (!kw) return true
    return `${b.plugin_id} ${b.plugin_name ?? ''} ${b.owner_username ?? ''} ${b.target_name ?? ''}`.toLowerCase().includes(kw)
  })
})
function bTarget(b: Any) {
  if (b.scope === 'global') return '-'
  return b.target_name || `#${b.scope_id}`
}
async function toggleBinding(b: Any, on: boolean) {
  busy.value = { ...busy.value, [`b${b.id}`]: true }
  const r = await attempt(() => adminExtApi.updateBinding(b.id, { enabled: on }), on && bindingState(b) === 'auto_disabled' ? t('binding.recoverDone') : undefined)
  busy.value = { ...busy.value, [`b${b.id}`]: false }
  if (r) {
    bt.items.value = bt.items.value.map((x) => (x.id === b.id ? { ...x, ...(r as BRow) } : x))
    if (b.scope === 'global') void loadPlugins()
  }
}
async function removeBinding(b: Any) {
  if (!(await confirm(t('extv.deleteBindingConfirm'), t('common.delete'), true))) return
  const ok = await attempt(() => adminExtApi.removeBinding(b.id).then(() => true))
  if (ok) {
    void bt.load()
    if (b.scope === 'global') void loadPlugins()
  }
}

// 编辑任意绑定的设置 / 优先级（管理员）
const be = reactive({ open: false, loading: false, saving: false, row: null as BRow | null, plugin: null as PRow | null, values: {} as Record<string, unknown>, priority: 100, onlyPriority: false, extErrors: {} as Record<string, string> })
const beForm = ref<InstanceType<typeof SchemaForm> | null>(null)
async function openBindingEdit(b: Any, onlyPriority = false) {
  Object.assign(be, { open: true, loading: !onlyPriority, saving: false, row: b as BRow, plugin: null, values: {}, priority: b.priority, onlyPriority, extErrors: {} })
  if (onlyPriority) return
  const d = await pluginDetail(b.plugin_id)
  if (!d) {
    be.open = false
    return
  }
  be.plugin = d
  be.values = mergeValues(d.settings_schema ?? [], b.settings ?? {})
  be.loading = false
}
async function saveBindingEdit() {
  if (!be.row) return
  if (!Number.isInteger(be.priority)) return ElMessage.warning(t('extv.priorityInvalid'))
  if (!be.onlyPriority && (!beForm.value || !beForm.value.validate())) return
  be.saving = true
  try {
    const body = be.onlyPriority ? { priority: be.priority } : { settings: beForm.value!.values(), priority: be.priority }
    const r = (await adminExtApi.updateBinding(be.row.id, body)) as BRow
    bt.items.value = bt.items.value.map((x) => (x.id === be.row!.id ? { ...x, ...r } : x))
    ElMessage.success(t('common.success'))
    be.open = false
  } catch (e) {
    be.extErrors = fieldErrors(e)
    handleError(e)
  } finally {
    be.saving = false
  }
}
function viewPluginBindings(id: string) {
  sub.value = 'bindings'
  bt.filters.plugin_id = id
  void bt.search()
}

// ================================================================ 5.11 插件健康度
const hPlugin = ref('')
const hLoading = ref(false)
const hData = ref<PluginHealth | null>(null)
const hError = ref<unknown>(null)
async function loadHealth() {
  if (!hPlugin.value) return
  hLoading.value = true
  hError.value = null
  try {
    hData.value = await adminExtApi.pluginHealth(hPlugin.value)
  } catch (e) {
    hError.value = e
  } finally {
    hLoading.value = false
  }
}
watch(hPlugin, () => void loadHealth())
function viewHealth(id: string) {
  sub.value = 'health'
  hPlugin.value = id
}
const healthOpen = ref(false)
const healthId = ref<number | null>(null)
function openBindingHealth(id: number) {
  healthId.value = id
  healthOpen.value = true
}
async function recoverBinding(id: number) {
  if (!(await confirm(t('binding.recover') + '？', t('binding.recover')))) return
  const r = await attempt(() => adminExtApi.updateBinding(id, { enabled: true }), t('binding.recoverDone'))
  if (r) {
    healthOpen.value = false
    void loadHealth()
    if (sub.value === 'bindings') void bt.load()
  }
}

// ================================================================ 5.12 出网 hosts
type HostItem = { host: string; plugins: string[]; bindings: number }
const hosts = shallowRef<HostItem[]>([])
const noNetPlugins = shallowRef<string[]>([])
const hostsLoading = ref(false)
const hostsError = ref<unknown>(null)
const hostFocus = ref('')
async function loadHosts() {
  hostsLoading.value = true
  hostsError.value = null
  try {
    const d = (await adminExtApi.networkHosts()) as { items?: HostItem[]; no_network_plugins?: string[] }
    hosts.value = d?.items ?? []
    noNetPlugins.value = d?.no_network_plugins ?? []
  } catch (e) {
    hostsError.value = e
  } finally {
    hostsLoading.value = false
  }
}
const hostsShown = computed(() => (hostFocus.value ? hosts.value.filter((h) => h.plugins.includes(hostFocus.value)) : hosts.value))

// ================================================================ 5.13 重点关注
const focusRows = shallowRef<{ plugin: PRow; checkpoint: string; timeout?: number; health?: PluginHealth | null }[]>([])
const focusLoading = ref(false)
async function loadFocus() {
  const closed = plugins.value.filter((p) => hasClosed(p) && loadState(p) !== 'failed')
  focusLoading.value = true
  const out: typeof focusRows.value = []
  await Promise.all(
    closed.map(async (p) => {
      const [d, h] = await Promise.all([pluginDetail(p.id), adminExtApi.pluginHealth(p.id).catch(() => null)])
      for (const hk of (d?.hooks ?? []).filter((x) => x.type === 'checkpoint' && x.fail_mode === 'closed')) {
        out.push({ plugin: { ...p, ...(d ?? {}) }, checkpoint: hk.checkpoint ?? '-', timeout: hk.timeout_ms, health: h })
      }
    }),
  )
  focusRows.value = out.sort((a, b) => a.plugin.id.localeCompare(b.plugin.id))
  focusLoading.value = false
}
watch(plugins, () => void loadFocus())
const scopeCount = (p: Any, s: Scope) => (p.bindings_by_scope?.[s] ?? distribution.value.find((d) => d.plugin_id === p.id)?.[s] ?? (s === 'global' && globalMap.value[p.id] ? 1 : 0)) as number

// ================================================================ 子 Tab 懒加载
const loadedSubs = new Set<string>()
watch(
  [tab, sub],
  ([tb, s]) => {
    if (tb !== 'plugins' || loadedSubs.has(s)) return
    loadedSubs.add(s)
    if (s === 'bindings') {
      void bt.load()
      if (!users.value.length) adminApi.users({ page: 1, page_size: 200 }).then((d) => (users.value = itemsOf<AdminUser>(d))).catch(() => undefined)
    } else if (s === 'hosts') void loadHosts()
    else if (s === 'health' && !hPlugin.value && plugins.value.length) hPlugin.value = plugins.value[0].id
  },
  { immediate: true },
)

onMounted(() => {
  void loadStrategies()
  void loadPlugins()
  // 绑定分布需要用到 distribution，预取一页
  if (!loadedSubs.has('bindings')) adminExtApi.allBindings({ page: 1, page_size: 1 }).then((d) => (distribution.value = (d as Any).distribution ?? [])).catch(() => undefined)
})
</script>

<template>
  <div>
    <PageHeader :title="t('ext.title')">
      <el-button type="primary" :icon="Refresh" :loading="reloading" @click="reload">{{ t('ext.reload') }}</el-button>
    </PageHeader>

    <el-tabs v-model="tab" @tab-change="onTab">
      <!-- ================= 策略管理 ================= -->
      <el-tab-pane :label="t('ext.tabStrategies')" name="strategies">
        <ErrorState v-if="sError" :error="sError" @retry="loadStrategies" />
        <template v-else>
          <div class="grid-cards">
            <div class="card stat"><div class="muted small">{{ t('ext.tabStrategies') }}</div><div class="stat-v num">{{ strategies.length }}</div></div>
            <div class="card stat"><div class="muted small">{{ t('status.load.failed') }}</div><div class="stat-v num" :class="{ 'danger-text': sFailedList.length }">{{ sFailedList.length }}</div></div>
            <div class="card stat"><div class="muted small">{{ t('strategy.usage') }}</div><div class="stat-v num">{{ sTotalInstances }}</div></div>
          </div>
          <div class="card">
            <div class="toolbar">
              <el-input v-model="sKeyword" :placeholder="t('strategy.searchPh')" :aria-label="t('strategy.searchPh')" clearable class="kw" />
              <el-button :loading="sLoading" @click="loadStrategies">{{ t('common.refresh') }}</el-button>
            </div>
            <div class="table-wrap">
              <el-table v-loading="sLoading" :data="sFiltered" class="resp-table" row-key="id" :empty-text="t('common.empty')">
                <el-table-column :label="t('common.name')" min-width="160">
                  <template #default="{ row }">
                    <el-link type="primary" @click="openStrategy(row)">{{ row.name || row.id }}</el-link>
                    <div class="muted small"><code>{{ row.id }}</code></div>
                  </template>
                </el-table-column>
                <el-table-column :label="t('strategy.version')" prop="version" width="90" />
                <el-table-column :label="t('strategy.author')" min-width="100"><template #default="{ row }">{{ row.author || '-' }}</template></el-table-column>
                <el-table-column :label="t('strategy.tags')" min-width="140">
                  <template #default="{ row }">
                    <span class="tags"><el-tag v-for="tg in row.tags ?? []" :key="tg" size="small" effect="plain" disable-transitions>{{ tg }}</el-tag></span>
                  </template>
                </el-table-column>
                <el-table-column :label="t('strategy.capability')" min-width="180"><template #default="{ row }"><CapabilityBadges :caps="row.capabilities" /></template></el-table-column>
                <el-table-column :label="t('strategy.loadStatus')" width="110"><template #default="{ row }"><StatusBadge kind="load" :value="loadState(row)" /></template></el-table-column>
                <el-table-column :label="t('strategy.usage')" width="110" align="right">
                  <template #default="{ row }">
                    <el-link v-if="row.instance_count" type="primary" @click="viewInstances(row.id)">{{ row.instance_count }}</el-link>
                    <span v-else class="num">{{ row.instance_count ?? 0 }}</span>
                  </template>
                </el-table-column>
                <el-table-column :label="t('common.actions')" min-width="200">
                  <template #default="{ row }">
                    <el-button link type="primary" :disabled="!row.loaded" @click="openStrategy(row)">{{ t('strategy.viewDetail') }}</el-button>
                    <el-button link type="primary" @click="viewInstances(row.id)">{{ t('strategy.viewInstances') }}</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            <p class="muted small">{{ t('strategy.readonlyNote') }}</p>
          </div>

          <section v-if="sFailedList.length" class="card failed-area" aria-labelledby="sfail-title">
            <h2 id="sfail-title" class="card-title danger-text">{{ t('strategy.failedArea') }}</h2>
            <div class="table-wrap">
              <el-table :data="sFailedList" size="small" class="resp-table">
                <el-table-column :label="t('common.name')" prop="id" min-width="140" />
                <el-table-column :label="t('strategy.failedReason')" min-width="260"><template #default="{ row }"><code class="err">{{ row.reason }}</code></template></el-table-column>
                <el-table-column :label="t('strategy.suggestion')" min-width="200"><template #default>{{ t('strategy.suggestionText') }}</template></el-table-column>
              </el-table>
            </div>
          </section>
        </template>
      </el-tab-pane>
      <!-- ================= 插件管理 ================= -->
      <el-tab-pane :label="t('ext.tabPlugins')" name="plugins">
        <ErrorState v-if="pError" :error="pError" @retry="loadPlugins" />
        <template v-else>
          <el-radio-group v-model="sub" class="subtabs" :aria-label="t('ext.tabPlugins')">
            <el-radio-button value="list">{{ t('ext.pluginList') }}</el-radio-button>
            <el-radio-button value="bindings">{{ t('ext.allBindings') }}</el-radio-button>
            <el-radio-button value="health">{{ t('ext.health') }}</el-radio-button>
            <el-radio-button value="hosts">{{ t('ext.networkReview') }}</el-radio-button>
          </el-radio-group>

          <!-- 插件列表 + 重点关注 -->
          <template v-if="sub === 'list'">
            <section class="card focus" aria-labelledby="focus-title">
              <h2 id="focus-title" class="card-title danger-text">{{ t('ext.focus') }}</h2>
              <p class="muted small">{{ t('ext.focusDesc') }}</p>
              <div class="table-wrap">
                <el-table v-loading="focusLoading" :data="focusRows" size="small" class="resp-table" :empty-text="t('ext.noClosed')">
                  <el-table-column :label="t('binding.plugin')" min-width="150">
                    <template #default="{ row }"><el-link type="primary" @click="openPlugin(row.plugin)">{{ row.plugin.name || row.plugin.id }}</el-link></template>
                  </el-table-column>
                  <el-table-column :label="t('ext.checkpoint')" min-width="140"><template #default="{ row }"><code>{{ row.checkpoint }}</code></template></el-table-column>
                  <el-table-column :label="t('ext.timeout')" width="100" align="right"><template #default="{ row }">{{ row.timeout ?? '-' }}</template></el-table-column>
                  <el-table-column :label="t('plugin.dataScope')" width="120"><template #default="{ row }"><PluginMeta :plugin="row.plugin" part="dataScope" /></template></el-table-column>
                  <el-table-column :label="t('ext.bindingDist')" min-width="170">
                    <template #default="{ row }">{{ t('scope.global') }} {{ scopeCount(row.plugin, 'global') }} / {{ t('scope.user') }} {{ scopeCount(row.plugin, 'user') }} / {{ t('scope.account') }} {{ scopeCount(row.plugin, 'account') }}</template>
                  </el-table-column>
                  <el-table-column :label="t('binding.health')" min-width="140">
                    <template #default="{ row }">
                      <span v-if="row.health" class="num" :class="{ 'danger-text': row.health.error_count }">{{ t('binding.healthSummary', { calls: fmtNum(row.health.event_calls + row.health.checkpoint_calls, 0), errors: fmtNum(row.health.error_count + row.health.timeout_count, 0) }) }}</span>
                      <span v-else>-</span>
                    </template>
                  </el-table-column>
                  <el-table-column :label="t('common.actions')" min-width="220">
                    <template #default="{ row }">
                      <el-button link type="primary" @click="viewPluginBindings(row.plugin.id)">{{ t('ext.viewBindings') }}</el-button>
                      <el-button v-if="globalMap[row.plugin.id]?.enabled" link type="warning" @click="toggleGlobal(row.plugin, false)">{{ t('ext.disableBinding') }}</el-button>
                      <el-button link type="primary" @click="viewHealth(row.plugin.id)">{{ t('binding.viewHealth') }}</el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </section>

            <div class="card">
              <div class="toolbar">
                <el-input v-model="pKeyword" :placeholder="t('common.search')" :aria-label="t('common.search')" clearable class="kw" />
                <el-button :loading="pLoading" @click="loadPlugins">{{ t('common.refresh') }}</el-button>
              </div>
              <div class="table-wrap">
                <el-table v-loading="pLoading" :data="pFiltered" class="resp-table" row-key="id" :empty-text="t('common.empty')">
                  <el-table-column :label="t('common.name')" min-width="160">
                    <template #default="{ row }">
                      <el-link type="primary" @click="openPlugin(row)">{{ row.name || row.id }}</el-link>
                      <div class="muted small"><code>{{ row.id }}</code></div>
                    </template>
                  </el-table-column>
                  <el-table-column :label="t('strategy.version')" prop="version" width="80" />
                  <el-table-column :label="t('plugin.scopes')" min-width="140"><template #default="{ row }"><PluginMeta :plugin="asP(row)" part="scopes" /></template></el-table-column>
                  <el-table-column :label="t('plugin.dataScope')" width="120"><template #default="{ row }"><PluginMeta :plugin="asP(row)" part="dataScope" /></template></el-table-column>
                  <el-table-column :label="t('plugin.hosts')" min-width="140"><template #default="{ row }"><PluginMeta :plugin="asP(row)" part="hosts" /></template></el-table-column>
                  <el-table-column :label="t('plugin.hooks')" min-width="160">
                    <template #default="{ row }">
                      <span class="small">{{ t('plugin.hooksSummary', { e: row.hooks_summary?.events?.length ?? 0, c: row.hooks_summary?.checkpoints?.length ?? 0 }) }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column :label="t('plugin.failMode')" width="100">
                    <template #default="{ row }">
                      <el-tag size="small" :type="hasClosed(row) ? 'danger' : 'info'" :effect="hasClosed(row) ? 'dark' : 'plain'" disable-transitions>{{ hasClosed(row) ? 'closed' : 'open' }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column :label="t('ext.bindingCount')" width="90" align="right"><template #default="{ row }">{{ row.binding_count ?? 0 }}</template></el-table-column>
                  <el-table-column :label="t('strategy.loadStatus')" width="110">
                    <template #default="{ row }">
                      <StatusBadge v-if="row.load_status !== 'uninstalled'" kind="load" :value="loadState(row)" />
                      <el-tag v-else size="small" type="info" disable-transitions>{{ t('extv.uninstalled') }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column :label="t('ext.globalBindings')" width="130">
                    <template #default="{ row }">
                      <el-switch
                        v-if="globalMap[row.id]"
                        :model-value="bindingState(globalMap[row.id]) === 'enabled'"
                        :loading="busy[row.id]"
                        :disabled="bindingState(globalMap[row.id]) === 'invalid'"
                        :aria-label="`${t('ext.globalToggle')} ${row.name || row.id}`"
                        @change="(v) => toggleGlobal(row, !!v)"
                      />
                      <StatusBadge v-if="globalMap[row.id] && bindingState(globalMap[row.id]) !== 'enabled'" kind="binding" :value="bindingState(globalMap[row.id])" class="ml" />
                      <span v-if="!globalMap[row.id]" class="muted small">{{ t('extv.noGlobal') }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column :label="t('common.actions')" min-width="280" fixed="right">
                    <template #default="{ row }">
                      <div class="ops">
                        <el-button link type="primary" @click="openPlugin(row)">{{ t('common.detail') }}</el-button>
                        <el-button v-if="row.scopes.includes('global') && row.loaded" link type="primary" @click="openGlobal(row)">{{ globalMap[row.id] ? t('extv.globalEdit') : t('ext.globalCreate') }}</el-button>
                        <el-button v-if="(row.network_hosts ?? []).length" link type="primary" @click="reviewHosts(row)">{{ t('ext.reviewHosts') }}</el-button>
                        <el-button link type="primary" @click="viewHealth(row.id)">{{ t('ext.health') }}</el-button>
                        <el-button v-if="auth.isSuper && row.load_status !== 'uninstalled'" link type="danger" @click="uninstall(row)">{{ t('ext.uninstall') }}</el-button>
                        <el-button v-if="auth.isSuper && row.load_status === 'uninstalled'" link type="primary" @click="reinstall(row)">{{ t('extx.reinstall') }}</el-button>
                      </div>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </div>

            <section v-if="pFailed.length" class="card failed-area" aria-labelledby="pfail-title">
              <h2 id="pfail-title" class="card-title danger-text">{{ t('extv.loadFailedList') }}</h2>
              <div class="table-wrap">
                <el-table :data="pFailed" size="small" class="resp-table">
                  <el-table-column :label="t('common.name')" prop="id" min-width="140" />
                  <el-table-column :label="t('strategy.failedReason')" min-width="260"><template #default="{ row }"><code class="err">{{ row.load_error || row.error || (row.load_status === 'uninstalled' ? t('extv.uninstalled') : '-') }}</code></template></el-table-column>
                  <el-table-column :label="t('strategy.suggestion')" min-width="200"><template #default>{{ t('strategy.suggestionText') }}</template></el-table-column>
                </el-table>
              </div>
            </section>
          </template>
          <!-- 5.10 绑定总览 -->
          <template v-else-if="sub === 'bindings'">
            <div class="card">
              <FilterBar :loading="bt.loading.value" @search="bt.search" @reset="bt.resetFilters">
                <el-input v-model="bKeyword" :placeholder="t('extv.keywordPh')" :aria-label="t('extv.keywordPh')" clearable />
                <el-select v-model="bt.filters.owner_id" :placeholder="t('extv.ownerFilter')" :aria-label="t('extv.ownerFilter')" clearable filterable @change="bt.search">
                  <el-option v-for="u in users" :key="u.id" :label="u.username" :value="u.id" />
                </el-select>
                <el-select v-model="bt.filters.plugin_id" :placeholder="t('binding.plugin')" :aria-label="t('binding.plugin')" clearable filterable @change="bt.search">
                  <el-option v-for="p in plugins" :key="p.id" :label="p.name || p.id" :value="p.id" />
                </el-select>
                <el-select v-model="bt.filters.scope" :placeholder="t('binding.scope')" :aria-label="t('binding.scope')" clearable @change="bt.search">
                  <el-option v-for="s in ['global', 'user', 'account']" :key="s" :label="t(`scope.${s}`)" :value="s" />
                </el-select>
                <el-select v-model="bt.filters.status" :placeholder="t('binding.status')" :aria-label="t('binding.status')" clearable @change="bt.search">
                  <el-option :label="t('status.binding.enabled')" value="enabled" />
                  <el-option :label="t('status.binding.disabled')" value="manual_disabled" />
                  <el-option :label="t('status.binding.auto_disabled')" value="auto_disabled" />
                  <el-option :label="t('status.binding.invalid')" value="invalid" />
                </el-select>
              </FilterBar>
              <ErrorState v-if="bt.error.value" :error="bt.error.value" @retry="bt.load" />
              <template v-else>
                <div class="table-wrap">
                  <el-table v-loading="bt.loading.value" :data="bRows" class="resp-table" row-key="id" :empty-text="t('common.empty')">
                    <el-table-column :label="t('binding.owner')" min-width="110">
                      <template #default="{ row }">{{ row.scope === 'global' ? t('ext.adminGlobal') : row.owner_username || `#${row.owner_id}` }}</template>
                    </el-table-column>
                    <el-table-column :label="t('binding.plugin')" min-width="150">
                      <template #default="{ row }">{{ row.plugin_name || pluginName(row.plugin_id) }}</template>
                    </el-table-column>
                    <el-table-column :label="t('binding.scope')" width="90"><template #default="{ row }">{{ t(`scope.${row.scope}`) }}</template></el-table-column>
                    <el-table-column :label="t('binding.target')" min-width="120"><template #default="{ row }">{{ bTarget(row) }}</template></el-table-column>
                    <el-table-column :label="t('binding.status')" width="120">
                      <template #default="{ row }">
                        <StatusBadge kind="binding" :value="bindingState(row) === 'auto_disabled' ? 'disabled' : bindingState(row)" :auto-disabled="bindingState(row) === 'auto_disabled'" />
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('binding.priority')" prop="priority" width="80" align="right" />
                    <el-table-column :label="t('binding.health')" min-width="130">
                      <template #default="{ row }">
                        <span class="num" :class="{ 'danger-text': bErrs(row) }">{{ t('binding.healthSummary', { calls: fmtNum(bCalls(row), 0), errors: fmtNum(bErrs(row), 0) }) }}</span>
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('common.actions')" min-width="340" fixed="right">
                      <template #default="{ row }">
                        <div class="ops">
                          <el-button v-if="bindingState(row) === 'auto_disabled'" size="small" type="warning" :loading="busy[`b${row.id}`]" @click="recoverBinding(row.id)">{{ t('binding.recover') }}</el-button>
                          <el-switch
                            v-else
                            :model-value="bindingState(row) === 'enabled'"
                            :loading="busy[`b${row.id}`]"
                            :disabled="bindingState(row) === 'invalid'"
                            :aria-label="`${t('common.enable')}/${t('common.disable')} #${row.id}`"
                            @change="(v) => toggleBinding(row, !!v)"
                          />
                          <el-button link type="primary" :disabled="bindingState(row) === 'invalid'" @click="openBindingEdit(row)">{{ t('binding.editSettings') }}</el-button>
                          <el-button link type="primary" @click="openBindingEdit(row, true)">{{ t('binding.editPriority') }}</el-button>
                          <el-button link type="primary" @click="openBindingHealth(row.id)">{{ t('binding.health') }}</el-button>
                          <el-button link type="danger" @click="removeBinding(row)">{{ t('common.delete') }}</el-button>
                        </div>
                      </template>
                    </el-table-column>
                  </el-table>
                </div>
                <Pager v-model:page="bt.page.value" v-model:size="bt.pageSize.value" :total="bt.total.value" />
              </template>
            </div>

            <section class="card" aria-labelledby="dist-title">
              <h2 id="dist-title" class="card-title">{{ t('ext.scopeDist') }}</h2>
              <div class="table-wrap">
                <el-table :data="distribution" size="small" class="resp-table" :empty-text="t('common.empty')">
                  <el-table-column :label="t('binding.plugin')" min-width="150"><template #default="{ row }">{{ pluginName(row.plugin_id) }}</template></el-table-column>
                  <el-table-column :label="t('ext.globalCount')" prop="global" align="right" min-width="100" />
                  <el-table-column :label="t('ext.userCount')" prop="user" align="right" min-width="100" />
                  <el-table-column :label="t('ext.accountCount')" prop="account" align="right" min-width="100" />
                  <el-table-column :label="t('ext.coverAccounts')" align="right" min-width="100"><template #default="{ row }">{{ row.covered_accounts ?? '-' }}</template></el-table-column>
                  <el-table-column :label="t('ext.coverInstances')" align="right" min-width="100"><template #default="{ row }">{{ row.covered_instances ?? '-' }}</template></el-table-column>
                </el-table>
              </div>
            </section>
          </template>

          <!-- 5.11 插件健康度 -->
          <template v-else-if="sub === 'health'">
            <div class="card">
              <div class="toolbar">
                <el-select v-model="hPlugin" :placeholder="t('ext.pickPlugin')" :aria-label="t('ext.pickPlugin')" filterable class="kw">
                  <el-option v-for="p in plugins" :key="p.id" :label="p.name || p.id" :value="p.id" />
                </el-select>
                <el-button :disabled="!hPlugin" :loading="hLoading" @click="loadHealth">{{ t('common.refresh') }}</el-button>
                <el-button link type="primary" @click="router.push({ path: '/admin/logs', query: { keyword: hPlugin || undefined } })">{{ t('extv.viewAlerts') }}</el-button>
              </div>
              <EmptyState v-if="!hPlugin" :title="t('extv.pickPluginHint')" />
              <ErrorState v-else-if="hError" :error="hError" @retry="loadHealth" />
              <div v-else v-loading="hLoading">
                <template v-if="hData">
                  <div class="grid-cards">
                    <div class="card stat"><div class="muted small">{{ t('binding.eventCalls') }}</div><div class="stat-v num">{{ fmtNum(hData.event_calls, 0) }}</div></div>
                    <div class="card stat"><div class="muted small">{{ t('binding.checkpointCalls') }}</div><div class="stat-v num">{{ fmtNum(hData.checkpoint_calls, 0) }}</div></div>
                    <div class="card stat"><div class="muted small">{{ t('binding.errorCount') }}</div><div class="stat-v num" :class="{ 'danger-text': hData.error_count + hData.timeout_count }">{{ fmtNum(hData.error_count + hData.timeout_count, 0) }}</div><div class="muted small">{{ t('binding.timeoutCount') }} {{ fmtNum(hData.timeout_count, 0) }}</div></div>
                    <div class="card stat"><div class="muted small">{{ t('binding.avgLatency') }}</div><div class="stat-v num">{{ hData.avg_latency_ms == null ? '-' : `${fmtNum(hData.avg_latency_ms, 1)} ms` }}</div></div>
                  </div>
                  <p class="small">
                    {{ t('extv.bindingsStat', { total: hData.bindings.total, enabled: hData.bindings.enabled, disabled: hData.bindings.disabled, invalid: hData.bindings.invalid, auto: hData.bindings.auto_disabled }) }}
                    <el-link v-if="hData.bindings.total" type="primary" class="ml" @click="viewPluginBindings(hData.plugin_id)">{{ t('ext.viewBindings') }}</el-link>
                  </p>
                  <el-descriptions :column="1" border size="small">
                    <el-descriptions-item :label="t('binding.lastError')">
                      <template v-if="hData.last_error">
                        <code class="err">{{ hData.last_error.message }}</code>
                        <div class="muted small">
                          {{ fmtTime(hData.last_error.at) }}
                          <el-link v-if="hData.last_error.binding_id" type="primary" class="ml" @click="openBindingHealth(hData.last_error.binding_id)">{{ t('extv.bindingId', { id: hData.last_error.binding_id }) }}</el-link>
                        </div>
                      </template>
                      <span v-else>-</span>
                    </el-descriptions-item>
                  </el-descriptions>
                  <h3 class="sub-title">{{ t('ext.worstBindings') }}（{{ t('ext.byBinding') }}）</h3>
                  <div class="table-wrap">
                    <el-table :data="hData.worst_bindings ?? []" size="small" class="resp-table" :empty-text="t('common.empty')">
                      <el-table-column :label="t('extv.bindingId', { id: '' })" width="110"><template #default="{ row }">#{{ row.binding_id }}</template></el-table-column>
                      <el-table-column :label="t('binding.scope')" width="100"><template #default="{ row }">{{ t(`scope.${row.scope}`) }}</template></el-table-column>
                      <el-table-column :label="t('binding.target')" width="110"><template #default="{ row }">{{ row.scope_id ?? '-' }}</template></el-table-column>
                      <el-table-column :label="t('binding.errorCount')" prop="error_count" align="right" width="100" />
                      <el-table-column :label="t('common.actions')" min-width="200">
                        <template #default="{ row }">
                          <el-button link type="primary" @click="openBindingHealth(row.binding_id)">{{ t('binding.viewHealth') }}</el-button>
                        </template>
                      </el-table-column>
                    </el-table>
                  </div>
                </template>
              </div>
            </div>
          </template>
          <!-- 5.12 出网 hosts -->
          <template v-else>
            <div class="card">
              <el-alert type="info" :closable="false" show-icon :title="t('extv.strategyNet')" class="mb" />
              <div class="toolbar">
                <el-select v-model="hostFocus" :placeholder="t('extv.allPlugins')" :aria-label="t('binding.plugin')" clearable filterable class="kw">
                  <el-option v-for="p in plugins" :key="p.id" :label="p.name || p.id" :value="p.id" />
                </el-select>
                <el-button :loading="hostsLoading" @click="loadHosts">{{ t('common.refresh') }}</el-button>
              </div>
              <ErrorState v-if="hostsError" :error="hostsError" @retry="loadHosts" />
              <div v-else class="table-wrap">
                <el-table v-loading="hostsLoading" :data="hostsShown" class="resp-table" :empty-text="t('ext.noHosts')">
                  <el-table-column :label="t('ext.host')" min-width="200"><template #default="{ row }"><code>{{ row.host }}</code></template></el-table-column>
                  <el-table-column :label="t('ext.usedBy')" min-width="200">
                    <template #default="{ row }">
                      <span class="tags">
                        <el-tag v-for="pid in row.plugins" :key="pid" size="small" effect="plain" class="clickable" disable-transitions @click="viewPluginBindings(pid)">{{ pluginName(pid) }}</el-tag>
                      </span>
                    </template>
                  </el-table-column>
                  <el-table-column :label="t('ext.bindingDist')" width="120" align="right"><template #default="{ row }">{{ t('extv.bindingCountShort', { n: row.bindings }) }}</template></el-table-column>
                </el-table>
              </div>
              <template v-if="noNetPlugins.length && !hostFocus">
                <h3 class="sub-title">{{ t('extv.noNetworkPlugins') }}</h3>
                <span class="tags">
                  <el-tag v-for="pid in noNetPlugins" :key="pid" size="small" type="info" effect="plain" disable-transitions>{{ pluginName(pid) }} · {{ t('plugin.noNetwork') }}</el-tag>
                </span>
              </template>
            </div>
          </template>
        </template>
      </el-tab-pane>
    </el-tabs>

    <!-- 重新扫描结果 -->
    <el-dialog v-model="reloadOpen" :title="t('extv.reloadDone')" width="600px" append-to-body>
      <template v-if="reloadResult">
        <el-alert
          type="success"
          :closable="false"
          show-icon
          :title="t('ext.reloadResult', { sl: reloadResult.strategies?.loaded ?? 0, sf: reloadResult.strategies?.failed ?? 0, pl: reloadResult.plugins?.loaded ?? 0, pf: reloadResult.plugins?.failed ?? 0, ib: reloadResult.plugins?.invalidated_bindings ?? 0 })"
        />
        <template v-if="reloadErrors.length">
          <h3 class="sub-title danger-text">{{ t('extv.reloadErrors') }}</h3>
          <el-table :data="reloadErrors" size="small" class="resp-table">
            <el-table-column prop="kind" width="100" />
            <el-table-column label="ID" prop="id" min-width="120" />
            <el-table-column :label="t('strategy.failedReason')" min-width="220"><template #default="{ row }"><code class="err">{{ row.reason }}</code></template></el-table-column>
          </el-table>
        </template>
      </template>
      <template #footer><el-button type="primary" @click="reloadOpen = false">{{ t('common.close') }}</el-button></template>
    </el-dialog>

    <!-- 策略详情 + 使用统计 -->
    <el-dialog v-model="sd.open" :title="sd.item ? `${sd.item.name || sd.item.id}` : ''" width="820px" append-to-body>
      <div v-if="sd.item" v-loading="sd.loading">
        <el-descriptions :column="2" border size="small" :title="t('extv.metaInfo')">
          <el-descriptions-item label="ID"><code>{{ sd.item.id }}</code></el-descriptions-item>
          <el-descriptions-item :label="t('strategy.version')">{{ sd.item.version || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('strategy.author')">{{ sd.item.author || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('strategy.loadStatus')"><StatusBadge kind="load" :value="loadState(sd.item)" /></el-descriptions-item>
          <el-descriptions-item :label="t('extv.capabilities')" :span="2"><CapabilityBadges :caps="sd.item.capabilities" /></el-descriptions-item>
          <el-descriptions-item :label="t('common.detail')" :span="2">{{ sd.item.description || '-' }}</el-descriptions-item>
        </el-descriptions>
        <h3 class="sub-title">{{ t('strategy.params') }}</h3>
        <div class="table-wrap">
          <el-table :data="sd.item.params_schema ?? []" size="small" class="resp-table" :empty-text="t('extv.noParams')">
            <el-table-column :label="t('extv.paramKey')" min-width="110"><template #default="{ row }"><code>{{ row.key }}</code></template></el-table-column>
            <el-table-column :label="t('extv.paramLabel')" prop="label" min-width="110" />
            <el-table-column :label="t('extv.paramType')" prop="type" width="80" />
            <el-table-column :label="t('extv.paramRequired')" width="70"><template #default="{ row }">{{ row.required ? t('common.yes') : t('common.no') }}</template></el-table-column>
            <el-table-column :label="t('extv.paramHelp')" prop="help" min-width="160" />
          </el-table>
        </div>
        <h3 class="sub-title">{{ t('strategy.usageStats') }}</h3>
        <p class="small">
          {{ t('strategy.usage') }}：{{ sd.item.usage?.instance_count ?? sd.instTotal }} · {{ t('strategy.usersCount') }}：{{ sdUserCount }}
          <template v-if="sdVersions.length"> · {{ t('strategy.versionDist') }}：<span v-for="v in sdVersions" :key="v.version" class="ml">{{ t('extv.versionCount', { v: v.version, n: v.n }) }}</span></template>
        </p>
        <div class="table-wrap">
          <el-table :data="sd.instances" size="small" class="resp-table" :empty-text="t('common.empty')">
            <el-table-column :label="t('extv.instanceName')" prop="name" min-width="140" />
            <el-table-column :label="t('extv.owner')" min-width="100"><template #default="{ row }">{{ row.owner_username || `#${row.owner_id}` }}</template></el-table-column>
            <el-table-column :label="t('extv.account')" min-width="110"><template #default="{ row }">{{ row.paper ? t('common.paper') : row.account_name || '-' }}</template></el-table-column>
            <el-table-column :label="t('strategy.version')" prop="strategy_version" width="90" />
            <el-table-column :label="t('extv.runStatus')" width="110"><template #default="{ row }"><StatusBadge kind="instance" :value="row.status" /></template></el-table-column>
          </el-table>
        </div>
      </div>
      <template #footer>
        <el-button @click="sd.open = false">{{ t('common.close') }}</el-button>
        <el-button v-if="sd.item" type="primary" @click="viewInstances(sd.item.id)">{{ t('strategy.viewInstances') }}</el-button>
      </template>
    </el-dialog>
    <!-- 插件详情 -->
    <el-dialog v-model="pd.open" :title="pd.item ? `${t('plugin.detail')} · ${pd.item.name || pd.item.id}` : ''" width="760px" append-to-body>
      <div v-if="pd.item" v-loading="pd.loading">
        <el-alert v-if="pd.item.data_scope === 'all'" type="error" :closable="false" show-icon :title="t('plugin.allDataWarn')" class="mb" />
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="ID"><code>{{ pd.item.id }}</code></el-descriptions-item>
          <el-descriptions-item :label="t('strategy.version')">{{ pd.item.version || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('strategy.author')">{{ pd.item.author || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('strategy.loadStatus')"><StatusBadge kind="load" :value="loadState(pd.item)" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.scopes')"><PluginMeta :plugin="pd.item" part="scopes" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.dataScope')"><PluginMeta :plugin="pd.item" part="dataScope" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.priority')">{{ pd.item.priority ?? '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('plugin.failMode')"><PluginMeta :plugin="pd.item" part="failMode" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.hosts')" :span="2"><PluginMeta :plugin="pd.item" part="hosts" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.hooks')" :span="2"><PluginMeta :plugin="pd.item" part="hooks" /></el-descriptions-item>
          <el-descriptions-item :label="t('common.detail')" :span="2">{{ pd.item.description || '-' }}</el-descriptions-item>
        </el-descriptions>
        <h3 class="sub-title">{{ t('plugin.settingsSchema') }}</h3>
        <div class="table-wrap">
          <el-table :data="pd.item.settings_schema ?? []" size="small" class="resp-table" :empty-text="t('extv.noSettings')">
            <el-table-column :label="t('extv.paramKey')" min-width="110"><template #default="{ row }"><code>{{ row.key }}</code></template></el-table-column>
            <el-table-column :label="t('extv.paramLabel')" prop="label" min-width="110" />
            <el-table-column :label="t('extv.paramType')" prop="type" width="80" />
            <el-table-column :label="t('extv.paramRequired')" width="70"><template #default="{ row }">{{ row.required ? t('common.yes') : t('common.no') }}</template></el-table-column>
            <el-table-column :label="t('extv.paramHelp')" prop="help" min-width="160" />
          </el-table>
        </div>
      </div>
      <template #footer><el-button @click="pd.open = false">{{ t('common.close') }}</el-button></template>
    </el-dialog>

    <!-- 新建/编辑全局绑定 -->
    <el-dialog v-model="ge.open" :title="ge.binding ? t('extv.globalEdit') : t('ext.globalCreate')" width="640px" append-to-body :close-on-click-modal="false">
      <div v-loading="ge.loading">
        <template v-if="ge.plugin">
          <el-alert v-if="ge.plugin.data_scope === 'all'" type="error" :closable="false" show-icon :title="t('plugin.allDataWarn')" class="mb" />
          <p class="small">{{ ge.plugin.name || ge.plugin.id }} · {{ t('scope.global') }}</p>
          <div v-if="ge.binding && ge.plugin.actions?.length" class="action-row">
            <PluginActionButton v-for="a in ge.plugin.actions" :key="a.id" :binding-id="ge.binding.id" :action="a" admin @done="onGlobalActionDone" />
          </div>
          <SchemaForm ref="geForm" v-model="ge.values" :schema="ge.plugin.settings_schema ?? []" :keep-secret="!!ge.binding" :external-errors="ge.extErrors" />
          <el-form label-width="140px" @submit.prevent>
            <el-form-item :label="t('binding.priority')">
              <el-input-number v-model="ge.priority" :min="0" :max="100000" :step="1" step-strictly :aria-label="t('binding.priority')" />
              <span class="muted small ml">{{ t('plugin.priorityHint') }}</span>
            </el-form-item>
            <el-form-item v-if="!ge.binding" :label="t('common.enable')">
              <el-switch v-model="ge.enabled" :aria-label="t('common.enable')" />
            </el-form-item>
          </el-form>
        </template>
      </div>
      <template #footer>
        <el-button @click="ge.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="ge.saving" :disabled="!ge.plugin || ge.loading" @click="saveGlobal">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 编辑任意绑定 -->
    <el-dialog v-model="be.open" :title="be.onlyPriority ? t('extv.priorityTitle') : t('binding.editSettings')" :width="be.onlyPriority ? '380px' : '640px'" append-to-body :close-on-click-modal="false">
      <div v-loading="be.loading">
        <SchemaForm v-if="!be.onlyPriority && be.plugin" ref="beForm" v-model="be.values" :schema="be.plugin.settings_schema ?? []" keep-secret :external-errors="be.extErrors" />
        <el-form :label-position="be.onlyPriority ? 'top' : 'right'" label-width="140px" @submit.prevent>
          <el-form-item :label="t('binding.priority')">
            <el-input-number v-model="be.priority" :min="0" :max="100000" :step="1" step-strictly :aria-label="t('binding.priority')" />
            <span class="muted small ml">{{ t('plugin.priorityHint') }}</span>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="be.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="be.saving" :disabled="be.loading" @click="saveBindingEdit">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <BindingHealthDialog v-model="healthOpen" :binding-id="healthId">
      <template #default="{ data }">
        <div v-if="data?.auto_disabled" class="recover">
          <el-button type="warning" @click="recoverBinding(data.binding_id)">{{ t('binding.recover') }}</el-button>
        </div>
      </template>
    </BindingHealthDialog>
  </div>
</template>

<style scoped>
.action-row {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.small { font-size: 12px; }
.stat { padding: 12px 16px; }
.stat-v { font-size: 22px; font-weight: 600; margin-top: 4px; }
.grid-cards .card + .card { margin-top: 0; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; align-items: center; }
.kw { width: 240px; }
.table-wrap { overflow-x: auto; }
.tags { display: inline-flex; flex-wrap: wrap; gap: 4px; }
.ops { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; }
.ops :deep(.el-button + .el-button) { margin-left: 0; }
.failed-area { border-color: var(--el-color-danger-light-5); }
.focus { border-color: var(--el-color-danger-light-5); }
.subtabs { margin-bottom: 12px; flex-wrap: wrap; }
.err { white-space: pre-wrap; word-break: break-all; }
.sub-title { font-size: 14px; margin: 16px 0 8px; }
.mb { margin-bottom: 12px; }
.ml { margin-left: 8px; }
.clickable { cursor: pointer; }
.recover { margin-top: 12px; text-align: right; }
@media (max-width: 767px) { .kw { width: 100%; } }
</style>
