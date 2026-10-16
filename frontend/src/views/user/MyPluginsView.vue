<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { bindingsApi, pluginsApi, itemsOf } from '@/api/extensions'
import { ApiError, fieldErrors } from '@/api/http'
import type { Binding, Plugin } from '@/api/types'
import { attempt, handleError } from '@/composables/useErrors'
import { confirm } from '@/composables/useConfirm'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { mergeValues } from '@/utils/schema'
import { fmtNum } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import SchemaForm from '@/components/SchemaForm.vue'
import PluginMeta from '@/components/PluginMeta.vue'
import BindingHealthDialog from '@/components/BindingHealthDialog.vue'

type Row = Binding & {
  target_name?: string | null
  last_error?: string | null
  overrides_global?: boolean
  health?: { event_calls?: number; checkpoint_calls?: number; error_count?: number } | null
}
// el-table 插槽 row 类型为 DefaultRow，这里放宽入参
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type RowArg = Row | Record<PropertyKey, any>
type GlobalRow = { plugin_id: string; status: Binding['status']; enabled: boolean; readonly?: boolean }
type ListResp = { items?: Row[]; global_bindings?: GlobalRow[] }

const { t } = useI18n()
const router = useRouter()
const { isMobile } = useBreakpoint()

const rows = shallowRef<Row[]>([])
const globals = shallowRef<GlobalRow[]>([])
const plugins = shallowRef<Record<string, Plugin>>({})
const loading = ref(false)
const loadedOnce = ref(false)
const error = ref<unknown>(null)
const busy = ref<Record<number, boolean>>({})
const filters = ref({ scope: '', state: '' })
const applied = ref({ ...filters.value })

// 后端：status 列为 enabled/disabled/invalid，用户手动停用只改 enabled；enabled && status=disabled 即自动停用
function stateOf(b: RowArg): 'enabled' | 'disabled' | 'invalid' | 'auto_disabled' {
  if (b.status === 'invalid') return 'invalid'
  if (b.auto_disabled || (b.enabled && b.status === 'disabled')) return 'auto_disabled'
  return b.enabled && b.status === 'enabled' ? 'enabled' : 'disabled'
}
const calls = (b: RowArg) => (b.health?.event_calls ?? 0) + (b.health?.checkpoint_calls ?? 0) || b.calls || 0
const errs = (b: RowArg) => b.health?.error_count ?? b.errors ?? 0
const pluginName = (id: string, fallback?: string) => plugins.value[id]?.name || fallback || id

async function load() {
  loading.value = true
  error.value = null
  try {
    const [d, ps] = await Promise.all([
      bindingsApi.list({ page: 1, page_size: 100 }) as unknown as Promise<ListResp>,
      pluginsApi.list().then((x) => itemsOf<Plugin>(x)).catch(() => [] as Plugin[]),
    ])
    const all = (d?.items ?? []) as Row[]
    rows.value = all.filter((b) => b.scope !== 'global')
    // 兼容：若 items 中混入 global 项，或后端单独返回 global_bindings，统一放到只读区
    const fromItems: GlobalRow[] = all.filter((b) => b.scope === 'global').map((b) => ({ plugin_id: b.plugin_id, status: b.status, enabled: b.enabled }))
    globals.value = [...fromItems, ...(d?.global_bindings ?? [])]
    plugins.value = Object.fromEntries(ps.map((p) => [p.id, p]))
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
    loadedOnce.value = true
  }
}

const filtered = computed(() =>
  rows.value.filter((b) => {
    if (applied.value.scope && b.scope !== applied.value.scope) return false
    if (applied.value.state && stateOf(b) !== applied.value.state) return false
    return true
  }),
)
const globalIds = computed(() => new Set(globals.value.map((g) => g.plugin_id)))
const isOverriding = (b: RowArg) => !!(b.overridden || b.overrides_global || globalIds.value.has(b.plugin_id))

function search() {
  applied.value = { ...filters.value }
}
function reset() {
  filters.value = { scope: '', state: '' }
  search()
}

function patchRow(id: number, patch: Partial<Row>) {
  rows.value = rows.value.map((r) => (r.id === id ? { ...r, ...patch } : r))
}

async function toggle(b: RowArg, on: boolean) {
  const st = stateOf(b)
  if (on && st === 'auto_disabled') return ElMessage.warning(t('binding.autoDisabledHint'))
  if (on && st === 'invalid') return ElMessage.warning(t('binding.invalidHint'))
  busy.value = { ...busy.value, [b.id]: true }
  try {
    const r = (await bindingsApi.update(b.id, { enabled: on })) as Row
    patchRow(b.id, r ?? { enabled: on })
    ElMessage.success(t('common.success'))
  } catch (e) {
    if (e instanceof ApiError && e.code === 40914) ElMessage.warning(t('binding.autoDisabledHint'))
    else handleError(e)
  } finally {
    busy.value = { ...busy.value, [b.id]: false }
  }
}

// ---------- 编辑设置
const edit = ref({ open: false, loading: false, saving: false, row: null as Row | null, plugin: null as Plugin | null, values: {} as Record<string, unknown>, extErrors: {} as Record<string, string> })
const formRef = ref<InstanceType<typeof SchemaForm> | null>(null)
async function openEdit(b: RowArg) {
  edit.value = { open: true, loading: true, saving: false, row: b as Row, plugin: null, values: {}, extErrors: {} }
  try {
    const p = await pluginsApi.get(b.plugin_id)
    edit.value.plugin = p
    edit.value.values = mergeValues(p.settings_schema ?? [], b.settings ?? {})
  } catch (e) {
    handleError(e)
    edit.value.open = false
  } finally {
    edit.value.loading = false
  }
}
async function saveEdit() {
  const e = edit.value
  if (!e.row || !formRef.value || !formRef.value.validate()) return
  e.saving = true
  try {
    const r = (await bindingsApi.update(e.row.id, { settings: formRef.value.values() })) as Row
    if (r) patchRow(e.row.id, r)
    ElMessage.success(t('common.success'))
    e.open = false
  } catch (err) {
    e.extErrors = fieldErrors(err)
    handleError(err)
  } finally {
    e.saving = false
  }
}

// ---------- 改优先级
const prio = ref({ open: false, saving: false, row: null as Row | null, value: 100 })
function openPrio(b: RowArg) {
  prio.value = { open: true, saving: false, row: b as Row, value: b.priority }
}
async function savePrio() {
  const p = prio.value
  if (!p.row) return
  if (!Number.isInteger(p.value)) return ElMessage.warning(t('extv.priorityInvalid'))
  p.saving = true
  const r = await attempt(() => bindingsApi.update(p.row!.id, { priority: p.value }))
  p.saving = false
  if (r !== undefined) {
    patchRow(p.row.id, { ...(r as Row), priority: p.value })
    p.open = false
  }
}

async function remove(b: RowArg) {
  if (!(await confirm(t('binding.deleteConfirm', { name: pluginName(b.plugin_id, b.plugin_name) }), t('common.delete'), true))) return
  const ok = await attempt(() => bindingsApi.remove(b.id).then(() => true))
  if (ok) rows.value = rows.value.filter((r) => r.id !== b.id)
}

// ---------- 健康度 / 插件详情
const healthOpen = ref(false)
const healthId = ref<number | null>(null)
function openHealth(b: RowArg) {
  healthId.value = b.id
  healthOpen.value = true
}
const pd = ref({ open: false, loading: false, plugin: null as Plugin | null })
async function openPlugin(id: string) {
  pd.value = { open: true, loading: true, plugin: plugins.value[id] ?? null }
  try {
    pd.value.plugin = await pluginsApi.get(id)
  } catch (e) {
    handleError(e)
  } finally {
    pd.value.loading = false
  }
}

function rowClass({ row }: { row: Row }) {
  const s = stateOf(row)
  return s === 'auto_disabled' || s === 'invalid' ? 'row-danger' : ''
}

function scopeText(b: RowArg) {
  if (b.scope === 'account') return t('extv.accountScopeOf', { name: b.target_name || b.scope_name || `#${b.scope_id}` })
  return t(`scope.${b.scope}`)
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader :title="t('binding.title')">
      <el-button type="primary" @click="router.push('/plugins')">{{ t('binding.goMarket') }}</el-button>
    </PageHeader>

    <FilterBar :loading="loading" @search="search" @reset="reset">
      <el-select v-model="filters.scope" :placeholder="t('binding.scope')" :aria-label="t('binding.scope')" clearable @change="search">
        <el-option :label="t('scope.user')" value="user" />
        <el-option :label="t('scope.account')" value="account" />
      </el-select>
      <el-select v-model="filters.state" :placeholder="t('binding.status')" :aria-label="t('binding.status')" clearable @change="search">
        <el-option v-for="s in ['enabled', 'disabled', 'auto_disabled', 'invalid']" :key="s" :label="t(`status.binding.${s}`)" :value="s" />
      </el-select>
      <template #extra>
        <el-button :loading="loading" @click="load">{{ t('common.refresh') }}</el-button>
      </template>
    </FilterBar>

    <ErrorState v-if="error" :error="error" @retry="load" />
    <template v-else>
      <EmptyState
        v-if="loadedOnce && !loading && !rows.length"
        :title="t('binding.emptyTitle')"
        :description="t('binding.emptyDesc')"
        :action-text="t('binding.goMarket')"
        @action="router.push('/plugins')"
      />
      <div v-else class="card">
        <div class="table-wrap">
          <el-table
            v-loading="loading"
            :data="filtered"
            class="resp-table"
            row-key="id"
            :row-class-name="rowClass"
            :empty-text="t('common.noMatch')"
          >
            <el-table-column :label="t('binding.plugin')" min-width="180" :fixed="isMobile ? undefined : 'left'">
              <template #default="{ row }">
                <el-link type="primary" @click="openPlugin(row.plugin_id)">{{ pluginName(row.plugin_id, row.plugin_name) }}</el-link>
                <div v-if="row.plugin_version" class="muted small">v{{ row.plugin_version }}</div>
                <div v-if="isOverriding(row)" class="small override">{{ t('binding.overridden') }}</div>
                <div v-if="stateOf(row) === 'auto_disabled'" class="small danger-text" role="alert">{{ t('binding.autoDisabledHint') }}</div>
                <div v-else-if="stateOf(row) === 'invalid'" class="small danger-text">{{ row.last_error || t('binding.invalidHint') }}</div>
              </template>
            </el-table-column>
            <el-table-column :label="t('binding.scope')" min-width="150">
              <template #default="{ row }">{{ scopeText(row) }}</template>
            </el-table-column>
            <el-table-column :label="t('binding.status')" width="130">
              <template #default="{ row }">
                <StatusBadge kind="binding" :value="stateOf(row) === 'auto_disabled' ? 'disabled' : stateOf(row)" :auto-disabled="stateOf(row) === 'auto_disabled'" />
              </template>
            </el-table-column>
            <el-table-column :label="t('binding.priority')" width="90" prop="priority" />
            <el-table-column :label="t('binding.health')" min-width="140">
              <template #default="{ row }">
                <span class="num" :class="{ 'danger-text': errs(row) > 0 }">{{ t('binding.healthSummary', { calls: fmtNum(calls(row), 0), errors: fmtNum(errs(row), 0) }) }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('common.actions')" min-width="320" :fixed="isMobile ? undefined : 'right'">
              <template #default="{ row }">
                <div class="ops">
                  <el-switch
                    :model-value="stateOf(row) === 'enabled'"
                    :loading="busy[row.id]"
                    :disabled="stateOf(row) === 'auto_disabled' || stateOf(row) === 'invalid'"
                    :aria-label="`${t('common.enable')}/${t('common.disable')} ${pluginName(row.plugin_id, row.plugin_name)}`"
                    @change="(v) => toggle(row, !!v)"
                  />
                  <el-button link type="primary" :disabled="stateOf(row) === 'invalid'" @click="openEdit(row)">{{ t('binding.editSettings') }}</el-button>
                  <el-button link type="primary" @click="openPrio(row)">{{ t('binding.editPriority') }}</el-button>
                  <el-button link type="primary" @click="openHealth(row)">{{ t('binding.viewHealth') }}</el-button>
                  <el-button link type="danger" @click="remove(row)">{{ t('common.delete') }}</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <section v-if="globals.length" class="card" aria-labelledby="global-title">
        <h2 id="global-title" class="card-title">
          {{ t('binding.globalReadonly') }}
          <el-tag size="small" type="info" effect="plain">{{ t('extv.readonlyTag') }}</el-tag>
        </h2>
        <p class="muted small">{{ t('extv.globalAreaDesc') }}</p>
        <div class="table-wrap">
          <el-table :data="globals" size="small" class="resp-table">
            <el-table-column :label="t('binding.plugin')" min-width="180">
              <template #default="{ row }">
                <el-link type="primary" @click="openPlugin(row.plugin_id)">{{ pluginName(row.plugin_id) }}</el-link>
              </template>
            </el-table-column>
            <el-table-column :label="t('binding.scope')" width="120"><template #default>{{ t('scope.global') }}</template></el-table-column>
            <el-table-column :label="t('binding.status')" width="140">
              <template #default="{ row }">
                <StatusBadge
                  kind="binding"
                  :value="row.status === 'invalid' ? 'invalid' : row.enabled && row.status === 'enabled' ? 'enabled' : 'disabled'"
                  :auto-disabled="row.enabled && row.status === 'disabled'"
                />
              </template>
            </el-table-column>
            <el-table-column min-width="200">
              <template #default="{ row }">
                <span v-if="rows.some((r) => r.plugin_id === row.plugin_id)" class="small override">{{ t('binding.overridden') }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </section>
    </template>

    <el-dialog v-model="edit.open" :title="edit.row ? t('extv.settingsTitle', { name: pluginName(edit.row.plugin_id, edit.row.plugin_name) }) : ''" width="640px" append-to-body :close-on-click-modal="false">
      <div v-loading="edit.loading">
        <SchemaForm v-if="edit.plugin" ref="formRef" v-model="edit.values" :schema="edit.plugin.settings_schema ?? []" keep-secret :external-errors="edit.extErrors" />
      </div>
      <template #footer>
        <el-button @click="edit.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="edit.saving" :disabled="!edit.plugin" @click="saveEdit">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="prio.open" :title="t('extv.priorityTitle')" width="380px" append-to-body>
      <el-form label-position="top" @submit.prevent="savePrio">
        <el-form-item :label="t('binding.priority')">
          <el-input-number v-model="prio.value" :min="0" :max="100000" :step="1" step-strictly :aria-label="t('binding.priority')" />
          <div class="muted small hint">{{ t('plugin.priorityHint') }}</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="prio.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="prio.saving" @click="savePrio">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="pd.open" :title="pd.plugin ? `${t('plugin.detail')} · ${pd.plugin.name || pd.plugin.id}` : t('plugin.detail')" width="640px" append-to-body>
      <div v-loading="pd.loading">
        <el-descriptions v-if="pd.plugin" :column="1" border size="small">
          <el-descriptions-item :label="t('strategy.version')">{{ pd.plugin.version || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('plugin.scopes')"><PluginMeta :plugin="pd.plugin" part="scopes" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.dataScope')"><PluginMeta :plugin="pd.plugin" part="dataScope" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.hosts')"><PluginMeta :plugin="pd.plugin" part="hosts" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.hooks')"><PluginMeta :plugin="pd.plugin" part="hooks" /></el-descriptions-item>
          <el-descriptions-item :label="t('common.detail')">{{ pd.plugin.description || '-' }}</el-descriptions-item>
        </el-descriptions>
      </div>
      <template #footer><el-button @click="pd.open = false">{{ t('common.close') }}</el-button></template>
    </el-dialog>

    <BindingHealthDialog v-model="healthOpen" :binding-id="healthId" />
  </div>
</template>

<style scoped>
.table-wrap { overflow-x: auto; }
.small { font-size: 12px; }
.override { color: var(--el-color-warning-dark-2); }
.ops { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; }
.ops :deep(.el-button + .el-button) { margin-left: 0; }
.hint { margin-left: 8px; }
:deep(.row-danger) { --el-table-tr-bg-color: var(--el-color-danger-light-9); }
</style>
