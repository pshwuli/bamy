<script setup lang="ts">
import { computed, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { instancesApi } from '@/api/instances'
import { strategiesApi, itemsOf } from '@/api/extensions'
import { accountsApi } from '@/api/accounts'
import { http, ApiError, fieldErrors } from '@/api/http'
import { P } from '@/api/paths'
import type { Account, Instance, Row, SchemaField, Strategy } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { useLive } from '@/composables/useLive'
import { confirm } from '@/composables/useConfirm'
import { attempt, handleError } from '@/composables/useErrors'
import { fmtDuration, fmtMoney, fmtPct, fmtTime, pnlClass, sinceSeconds } from '@/utils/format'
import { mergeValues } from '@/utils/schema'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import Pager from '@/components/Pager.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import SchemaForm from '@/components/SchemaForm.vue'

const { t } = useI18n()
const router = useRouter()

const { items, loading, loadedOnce, error, page, pageSize, total, filters, load, search, resetFilters } = useTable<Instance>(
  (q) => instancesApi.list(q),
  { keyword: '', status: '', paper: '' as '' | 'true' | 'false', strategy_id: '', account_id: '' as '' | number },
)

const STATUSES = ['created', 'running', 'paused', 'stopped', 'error'] as const
const strategies = shallowRef<Strategy[]>([])
const accounts = shallowRef<Account[]>([])
void strategiesApi.list({ page_size: 100 }).then((d) => (strategies.value = itemsOf(d))).catch(() => {})
void accountsApi.list({ page: 1, page_size: 100 }).then((d) => (accounts.value = d.items ?? [])).catch(() => {})

const STOPPED = new Set(['stopped', 'error', 'created'])
const isStopped = (r: Row) => STOPPED.has(r.status)
const canStop = (r: Row) => ['running', 'paused', 'starting', 'resuming', 'pausing'].includes(r.status)
const caps = (r: Row): Record<string, boolean> => r.capabilities ?? strategies.value.find((s) => s.id === r.strategy_id)?.capabilities ?? {}
const pausable = (r: Row) => !!caps(r).pause
const resumable = (r: Row) => !!caps(r).resume

// 后端字段 return_pct（百分数）/ running_seconds；兼容前端类型 return_rate / running_since
function returnRate(r: Row): number | null {
  if (r.return_rate != null) return Number(r.return_rate)
  if (r.return_pct != null) return Number(r.return_pct) / 100
  return null
}
function runningSecs(r: Row): number | null {
  if (r.running_seconds != null) return Number(r.running_seconds)
  if (!['running', 'paused'].includes(r.status)) return null
  return sinceSeconds(r.running_since ?? r.last_started_at)
}
const lastActive = (r: Row) => r.last_active_at ?? r.updated_at ?? r.last_started_at

function patchRow(id: number, patch: Partial<Instance>) {
  items.value = items.value.map((i) => (i.id === id ? { ...i, ...patch } : i))
}

// ---------- 行内动作 ----------
const busy = ref<number | null>(null)
async function act(r: Row, action: 'start' | 'stop' | 'pause' | 'resume') {
  if (action === 'stop' && !(await confirm(t('instx.stopConfirm', { name: r.name })))) return
  if ((action === 'pause' && !pausable(r)) || (action === 'resume' && !resumable(r))) {
    ElMessage.warning(t('instx.pauseUnsupported'))
    return
  }
  busy.value = r.id
  const res = await attempt(() => instancesApi[action](r.id))
  busy.value = null
  if (res?.status) patchRow(r.id, { status: res.status })
  void load()
}

async function remove(r: Row) {
  if (!isStopped(r)) {
    ElMessage.warning(t('instance.needStopped'))
    return
  }
  if (!(await confirm(t('instance.deleteConfirm', { name: r.name }), t('common.delete'), true))) return
  await attempt(() => instancesApi.remove(r.id), t('instx.deleted'))
  void load()
}

// ---------- 批量 ----------
const selection = ref<Instance[]>([])
const batching = ref(false)
type BatchRes = { results: { instance_id: number; ok: boolean; reason?: string }[] }
async function batch(action: 'start' | 'stop') {
  if (!selection.value.length) {
    ElMessage.warning(t('instx.noSelection'))
    return
  }
  const n = selection.value.length
  const msg = action === 'start' ? t('instx.batchStartConfirm', { n }) : t('instx.batchStopConfirm', { n })
  if (!(await confirm(msg))) return
  batching.value = true
  const ids = selection.value.map((i) => i.id)
  let ok = 0
  let fail = 0
  try {
    const r = await http.post<BatchRes>(`${P.instances.list}/batch/${action}`, { instance_ids: ids }, { silent: true })
    for (const x of r?.results ?? []) x.ok ? ok++ : fail++
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 405)) {
      for (const id of ids) {
        try {
          await instancesApi[action](id)
          ok++
        } catch {
          fail++
        }
      }
    } else handleError(e)
  } finally {
    batching.value = false
  }
  if (ok || fail) ElMessage({ type: fail ? 'warning' : 'success', message: t('instance.batchDone', { ok, fail }) })
  void load()
}

// ---------- 编辑参数 ----------
const editOpen = ref(false)
const editSaving = ref(false)
const editTarget = shallowRef<Instance | null>(null)
const editSchema = shallowRef<SchemaField[]>([])
const editSymbols = ref<string[]>([])
const editParams = ref<Record<string, unknown>>({})
const editErrs = ref<Record<string, string>>({})
const editLoading = ref(false)
const editForm = ref<InstanceType<typeof SchemaForm>>()

async function openEdit(r: Row) {
  if (!isStopped(r)) {
    ElMessage.warning(t('instance.needStopped'))
    return
  }
  editTarget.value = r
  editErrs.value = {}
  editOpen.value = true
  editLoading.value = true
  try {
    const s = await strategiesApi.get(r.strategy_id)
    editSchema.value = s.params_schema ?? []
    editSymbols.value = s.runtime?.symbols ?? []
    editParams.value = mergeValues(editSchema.value, r.params)
  } catch {
    editOpen.value = false
  } finally {
    editLoading.value = false
  }
}

async function submitEdit() {
  if (!editTarget.value || !editForm.value?.validate()) return
  editSaving.value = true
  editErrs.value = {}
  try {
    await instancesApi.update(editTarget.value.id, { params: editForm.value.values() })
    ElMessage.success(t('common.success'))
    editOpen.value = false
    void load()
  } catch (e) {
    editErrs.value = fieldErrors(e)
    handleError(e)
  } finally {
    editSaving.value = false
  }
}

// ---------- 换策略 ----------
const csOpen = ref(false)
const csSaving = ref(false)
const csTarget = shallowRef<Instance | null>(null)
const csStrategyId = ref('')
const csStrategy = shallowRef<Strategy | null>(null)
const csParams = ref<Record<string, unknown>>({})
const csErrs = ref<Record<string, string>>({})
const csNeedClose = ref(false)
const csClose = ref(false)
const csLoading = ref(false)
const csForm = ref<InstanceType<typeof SchemaForm>>()
const csOptions = computed(() => strategies.value.filter((s) => s.loaded !== false && s.id !== csTarget.value?.strategy_id))

function openChange(r: Row) {
  if (!isStopped(r)) {
    ElMessage.warning(t('instance.needStopped'))
    return
  }
  csTarget.value = r
  csStrategyId.value = ''
  csStrategy.value = null
  csParams.value = {}
  csErrs.value = {}
  csNeedClose.value = false
  csClose.value = false
  csOpen.value = true
}

async function pickStrategy(id: string) {
  csStrategy.value = null
  if (!id) return
  csLoading.value = true
  try {
    const s = await strategiesApi.get(id)
    csStrategy.value = s
    csParams.value = mergeValues(s.params_schema ?? [], {})
  } finally {
    csLoading.value = false
  }
}

async function submitChange() {
  if (!csTarget.value || !csStrategy.value) return
  if (csForm.value && !csForm.value.validate()) return
  csSaving.value = true
  csErrs.value = {}
  try {
    const params = csForm.value ? csForm.value.values() : {}
    const body = { strategy_id: csStrategy.value.id, params, close_positions: csClose.value }
    await instancesApi.changeStrategy(csTarget.value.id, body as { strategy_id: string; params: Record<string, unknown> })
    ElMessage.success(t('common.success'))
    csOpen.value = false
    void load()
  } catch (e) {
    if (e instanceof ApiError && e.code === 40904) {
      csNeedClose.value = true
      ElMessage.warning(t('instx.needClose'))
    } else {
      csErrs.value = fieldErrors(e)
      handleError(e)
    }
  } finally {
    csSaving.value = false
  }
}

// ---------- 实时 ----------
useLive({
  events: ['instance.status'],
  onEvent: (m) => {
    const d = m.data as { instance_id?: number; status?: Instance['status']; last_error?: string | null }
    if (!d.instance_id || !d.status) return
    if (!items.value.some((i) => i.id === d.instance_id)) return
    patchRow(d.instance_id, { status: d.status, last_error: d.last_error ?? null })
  },
  poll: load,
})

function onReset() {
  selection.value = []
  void resetFilters()
}
</script>

<template>
  <div>
    <PageHeader :title="t('instance.title')">
      <el-button type="primary" :icon="Plus" @click="router.push('/instances/new')">{{ t('instance.create') }}</el-button>
    </PageHeader>

    <FilterBar :loading="loading" @search="search" @reset="onReset">
      <el-input v-model="filters.keyword" :placeholder="t('instx.searchPh')" :aria-label="t('common.search')" clearable @keyup.enter="search" />
      <el-select v-model="filters.status" :placeholder="t('instx.allStatus')" :aria-label="t('common.status')" clearable>
        <el-option v-for="s in STATUSES" :key="s" :value="s" :label="t(`status.instance.${s}`)" />
      </el-select>
      <el-select v-model="filters.paper" :placeholder="t('instx.allMode')" :aria-label="t('instance.mode')" clearable>
        <el-option value="true" :label="t('common.paper')" />
        <el-option value="false" :label="t('common.live')" />
      </el-select>
      <el-select v-model="filters.strategy_id" :placeholder="t('instx.allStrategy')" :aria-label="t('instance.strategy')" clearable filterable>
        <el-option v-for="s in strategies" :key="s.id" :value="s.id" :label="s.name" />
      </el-select>
      <el-select v-model="filters.account_id" :placeholder="t('instx.allAccount')" :aria-label="t('instance.account')" clearable filterable>
        <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="a.name" />
      </el-select>
    </FilterBar>

    <div class="batch-bar">
      <span class="muted">{{ t('instx.selected', { n: selection.length }) }}</span>
      <el-button size="small" :disabled="!selection.length" :loading="batching" @click="batch('start')">{{ t('instance.batchStart') }}</el-button>
      <el-button size="small" :disabled="!selection.length" :loading="batching" @click="batch('stop')">{{ t('instance.batchStop') }}</el-button>
    </div>

    <ErrorState v-if="error && !items.length" :error="error" @retry="load" />
    <EmptyState
      v-else-if="loadedOnce && !loading && !items.length && !filters.keyword && !filters.status && !filters.paper && !filters.strategy_id && !filters.account_id"
      :title="t('instx.emptyTitle')"
      :description="t('instx.emptyDesc')"
      :action-text="t('instance.create')"
      @action="router.push('/instances/new')"
    />
    <template v-else>
      <div class="table-wrap">
        <el-table v-loading="loading" :data="items" row-key="id" stripe class="resp-table" @selection-change="(v: Instance[]) => (selection = v)">
          <el-table-column type="selection" width="42" />
          <el-table-column :label="t('instance.name')" min-width="150" show-overflow-tooltip>
            <template #default="{ row }"><router-link :to="`/instances/${row.id}`">{{ row.name }}</router-link></template>
          </el-table-column>
          <el-table-column :label="t('instance.strategy')" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.strategy_name || row.strategy_id }}</template>
          </el-table-column>
          <el-table-column prop="symbol" :label="t('instance.symbol')" min-width="100" />
          <el-table-column :label="t('instance.account')" min-width="110" show-overflow-tooltip>
            <template #default="{ row }">{{ row.paper ? t('common.paper') : row.account_name || (row.account_id ? `#${row.account_id}` : '-') }}</template>
          </el-table-column>
          <el-table-column :label="t('instance.mode')" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="row.paper ? 'info' : 'warning'" disable-transitions>{{ row.paper ? t('common.paper') : t('common.live') }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.status')" width="100">
            <template #default="{ row }">
              <el-tooltip v-if="row.last_error" :content="row.last_error" placement="top"><span><StatusBadge kind="instance" :value="row.status" /></span></el-tooltip>
              <StatusBadge v-else kind="instance" :value="row.status" />
            </template>
          </el-table-column>
          <el-table-column :label="t('instance.equity')" min-width="110" align="right">
            <template #default="{ row }">{{ fmtMoney(row.equity) }}</template>
          </el-table-column>
          <el-table-column :label="t('instance.returnRate')" min-width="90" align="right">
            <template #default="{ row }"><span :class="pnlClass(returnRate(row))">{{ fmtPct(returnRate(row)) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('instance.runtime')" min-width="90">
            <template #default="{ row }">{{ fmtDuration(runningSecs(row)) }}</template>
          </el-table-column>
          <el-table-column :label="t('instance.lastActive')" min-width="160">
            <template #default="{ row }">{{ fmtTime(lastActive(row)) }}</template>
          </el-table-column>
          <el-table-column :label="t('common.actions')" width="230" fixed="right">
            <template #default="{ row }">
              <el-button v-if="canStop(row)" link type="warning" :loading="busy === row.id" @click="act(row, 'stop')">{{ t('instance.stop') }}</el-button>
              <el-button v-else link type="primary" :loading="busy === row.id" @click="act(row, 'start')">{{ t('instance.start') }}</el-button>
              <el-button link :disabled="!isStopped(row)" :title="isStopped(row) ? '' : t('instance.needStopped')" @click="openEdit(row)">{{ t('common.edit') }}</el-button>
              <el-dropdown trigger="click">
                <el-button link :aria-label="t('common.more')">{{ t('common.more') }}</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item v-if="row.status === 'running'" :disabled="!pausable(row)" @click="act(row, 'pause')">{{ t('instance.pause') }}</el-dropdown-item>
                    <el-dropdown-item v-if="row.status === 'paused'" :disabled="!resumable(row)" @click="act(row, 'resume')">{{ t('instance.resume') }}</el-dropdown-item>
                    <el-dropdown-item :disabled="!isStopped(row)" @click="openChange(row)">{{ t('instance.changeStrategy') }}</el-dropdown-item>
                    <el-dropdown-item @click="router.push(`/instances/${row.id}`)">{{ t('common.detail') }}</el-dropdown-item>
                    <el-dropdown-item divided :disabled="!isStopped(row)" @click="remove(row)"><span class="danger-text">{{ t('common.delete') }}</span></el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </template>
          </el-table-column>
          <template #empty><el-empty :description="t('common.noMatch')" :image-size="60" /></template>
        </el-table>
      </div>
      <Pager v-model:page="page" v-model:size="pageSize" :total="total" />
    </template>

    <!-- 编辑参数 -->
    <el-dialog v-model="editOpen" :title="`${t('instance.editParams')} · ${editTarget?.name ?? ''}`" width="640px" class="resp-dialog" destroy-on-close>
      <div v-loading="editLoading">
        <el-alert :title="t('instance.needStopped')" type="info" :closable="false" show-icon class="mb" />
        <SchemaForm v-if="editSchema.length" ref="editForm" v-model="editParams" :schema="editSchema" :symbols="editSymbols" :external-errors="editErrs" error-prefix="params." />
        <el-empty v-else-if="!editLoading" :description="t('instx.noParams')" :image-size="48" />
      </div>
      <template #footer>
        <el-button @click="editOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="editSaving" :disabled="!editSchema.length" @click="submitEdit">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 换策略 -->
    <el-dialog v-model="csOpen" :title="`${t('instance.changeStrategy')} · ${csTarget?.name ?? ''}`" width="640px" class="resp-dialog" destroy-on-close>
      <el-alert :title="t('instx.changeHint')" type="info" :closable="false" show-icon class="mb" />
      <el-form label-width="120px" @submit.prevent>
        <el-form-item :label="t('instx.newStrategy')" required>
          <el-select v-model="csStrategyId" :placeholder="t('instx.selectStrategy')" filterable style="width: 100%" @change="pickStrategy">
            <el-option v-for="s in csOptions" :key="s.id" :value="s.id" :label="`${s.name} (v${s.version})`" />
          </el-select>
        </el-form-item>
      </el-form>
      <div v-loading="csLoading">
        <template v-if="csStrategy">
          <SchemaForm
            v-if="csStrategy.params_schema?.length"
            ref="csForm"
            v-model="csParams"
            :schema="csStrategy.params_schema"
            :symbols="csStrategy.runtime?.symbols ?? []"
            :external-errors="csErrs"
            error-prefix="params."
          />
          <el-empty v-else :description="t('instx.noParams')" :image-size="48" />
        </template>
      </div>
      <el-alert v-if="csNeedClose" type="warning" :closable="false" show-icon class="mt" :title="t('instx.needClose')">
        <el-checkbox v-model="csClose">{{ t('instx.closePositions') }}</el-checkbox>
      </el-alert>
      <template #footer>
        <el-button @click="csOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="csSaving" :disabled="!csStrategy || (csNeedClose && !csClose)" @click="submitChange">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.batch-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; }
.table-wrap { overflow-x: auto; }
.mb { margin-bottom: 12px; }
.mt { margin-top: 12px; }
:deep(.resp-dialog) { max-width: 96vw; }
</style>
