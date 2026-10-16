<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Download, Plus } from '@element-plus/icons-vue'
import { instancesApi } from '@/api/instances'
import { paperApi } from '@/api/orders'
import type { Instance, Query, Row } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { fmtDuration, fmtMoney, fmtNum, fmtPct, pnlClass, fmtSymbol } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import Pager from '@/components/Pager.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ErrorState from '@/components/ErrorState.vue'
import EmptyState from '@/components/EmptyState.vue'
import LineChart, { type LineSeries } from '@/components/LineChart.vue'

// 后端 /api/paper/compare 与 /compare/metrics 的实际响应（比 api/types 里的类型多几个字段）
interface CompareSeries { instance_id: number; name: string; paper?: boolean; account_id?: number | null; values: (number | null)[] }
interface CompareResp { timeline: string[]; series: CompareSeries[]; granularity?: string; account_plugin_notice?: boolean }
interface MetricRow {
  instance_id: number
  name?: string
  strategy_id?: string
  symbol?: string
  initial_balance?: number | null
  equity?: number | null
  total_return?: number | null
  max_drawdown?: number | null
  sharpe?: number | null
  win_rate?: number | null
  profit_factor?: number | null
  order_count?: number | null
  running_hours?: number | null
}

const MAX = 10
const { t } = useI18n()
const router = useRouter()

// ---------- 区域1：我的模拟盘 ----------
const tbl = useTable<Instance>(instancesApi.list, {}, { transform: (q) => ({ ...q, paper: true }) })

// ---------- 区域2：多盘对比 ----------
const candidates = shallowRef<Instance[]>([])
const selected = ref<number[]>([])
const mode = ref<'absolute' | 'normalized'>('absolute')
const showDrawdown = ref(false)
const preset = ref<'24h' | '7d' | '30d' | 'custom' | 'all'>('7d')
const custom = ref<[string, string] | null>(null)
const granularity = ref<'' | 'minute' | 'hour' | 'day'>('')

const data = shallowRef<CompareResp | null>(null)
const metrics = shallowRef<MetricRow[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const exporting = ref(false)

function rangeQuery(): Query {
  if (preset.value === 'custom') return { start_time: custom.value?.[0], end_time: custom.value?.[1] }
  if (preset.value === 'all') return {}
  const h = preset.value === '24h' ? 24 : preset.value === '7d' ? 168 : 720
  return { start_time: new Date(Date.now() - h * 3600_000).toISOString() }
}

function compareQuery(): Query {
  return { instance_ids: selected.value.join(','), ...rangeQuery(), granularity: granularity.value || undefined }
}

async function loadCompare() {
  if (!selected.value.length) {
    data.value = null
    metrics.value = []
    error.value = null
    return
  }
  loading.value = true
  error.value = null
  try {
    const q = compareQuery()
    const [c, m] = await Promise.all([
      paperApi.compare(q) as unknown as Promise<CompareResp>,
      paperApi.metrics({ instance_ids: q.instance_ids }) as unknown as Promise<{ metrics: MetricRow[] }>,
    ])
    data.value = c
    metrics.value = m?.metrics ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

watch(selected, (v) => {
  if (v.length > MAX) {
    selected.value = v.slice(0, MAX)
    ElMessage.warning(t('paper.maxSelect'))
    return
  }
  void loadCompare()
})
watch([preset, custom, granularity], () => {
  if (preset.value === 'custom' && !custom.value) return
  void loadCompare()
})

function normalize(values: (number | null)[]): (number | null)[] {
  const base = values.find((v) => v !== null && v !== undefined && v !== 0)
  if (!base) return values
  return values.map((v) => (v === null || v === undefined ? null : v / base))
}

function drawdown(values: (number | null)[]): (number | null)[] {
  let peak = -Infinity
  return values.map((v) => {
    if (v === null || v === undefined) return null
    peak = Math.max(peak, v)
    return peak > 0 ? (v - peak) / peak : 0
  })
}

const slotOf = (id: number) => Math.max(0, selected.value.indexOf(id))
const timeline = computed(() => data.value?.timeline ?? [])
const equitySeries = computed<LineSeries[]>(() =>
  (data.value?.series ?? []).map((s) => ({ name: s.name, slot: slotOf(s.instance_id), values: mode.value === 'normalized' ? normalize(s.values) : s.values })),
)
const ddSeries = computed<LineSeries[]>(() => (data.value?.series ?? []).map((s) => ({ name: s.name, slot: slotOf(s.instance_id), values: drawdown(s.values) })))
const valueFmt = computed(() => (mode.value === 'normalized' ? (v: number) => v.toFixed(4) : (v: number) => fmtNum(v, 2)))
const pctFmt = (v: number) => fmtPct(v, 2, false)
const hasPoints = computed(() => timeline.value.length > 0)

const byId = computed(() => new Map(candidates.value.map((i) => [i.id, i])))
const mixedNotice = computed(() => {
  if (data.value?.account_plugin_notice) return true
  const picked = selected.value.map((id) => byId.value.get(id)).filter(Boolean) as Instance[]
  return picked.some((i) => !i.paper) && picked.some((i) => i.paper)
})

const rows = computed(() =>
  metrics.value.map((m) => {
    const inst = byId.value.get(m.instance_id)
    return {
      ...m,
      name: m.name ?? inst?.name ?? `#${m.instance_id}`,
      strategy: inst?.strategy_name || m.strategy_id || inst?.strategy_id || '-',
      symbol: m.symbol ?? inst?.symbol ?? '-',
      paper: inst?.paper ?? true,
    }
  }),
)

async function exportCompare() {
  if (!selected.value.length) return
  exporting.value = true
  try {
    await paperApi.export({ ...compareQuery(), normalize: mode.value === 'normalized', granularity: undefined })
  } catch {
    /* 全局钩子已提示 */
  } finally {
    exporting.value = false
  }
}


const initialOf = (r: Row): number | null => r.initial_balance ?? r.initial_equity ?? null
const returnOf = (r: Row): number | null => {
  if (r.return_rate !== undefined && r.return_rate !== null) return r.return_rate
  if (r.return_pct !== undefined && r.return_pct !== null) return r.return_pct / 100
  const init = initialOf(r)
  return r.equity != null && init ? (Number(r.equity) - init) / init : null
}

function addToCompare(id: number) {
  if (selected.value.includes(id)) return
  if (selected.value.length >= MAX) return void ElMessage.warning(t('paper.maxSelect'))
  selected.value = [...selected.value, id]
}

onMounted(async () => {
  try {
    const r = await instancesApi.list({ page: 1, page_size: 200 })
    candidates.value = (r.items ?? []).filter((i) => i.status !== 'deleted')
  } catch {
    /* 全局钩子已提示 */
  }
})
</script>

<template>
  <div>
    <PageHeader :title="t('paper.title')">
      <el-button type="primary" :icon="Plus" @click="router.push({ path: '/instances/new', query: { paper: '1' } })">{{ t('misc.createPaper') }}</el-button>
    </PageHeader>

    <section class="card">
      <h2 class="card-title">{{ t('paper.list') }}</h2>
      <ErrorState v-if="tbl.error.value && !tbl.items.value.length" :error="tbl.error.value" @retry="tbl.load" />
      <el-skeleton v-else-if="!tbl.loadedOnce.value" :rows="4" animated />
      <EmptyState
        v-else-if="!tbl.items.value.length"
        :title="t('misc.paperEmpty')"
        :action-text="t('misc.createPaper')"
        @action="router.push({ path: '/instances/new', query: { paper: '1' } })"
      />
      <template v-else>
        <el-table v-loading="tbl.loading.value" :data="tbl.items.value" stripe size="small" class="resp-table">
          <el-table-column :label="t('instance.name')" min-width="140" show-overflow-tooltip>
            <template #default="{ row }"><router-link :to="`/instances/${row.id}`">{{ row.name }}</router-link></template>
          </el-table-column>
          <el-table-column :label="t('instance.strategy')" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.strategy_name || row.strategy_id }}</template>
          </el-table-column>
          <el-table-column :label="t('instance.symbol')" min-width="100"><template #default="{ row }">{{ fmtSymbol(row.symbol) }}</template></el-table-column>
          <el-table-column :label="t('common.status')" width="100">
            <template #default="{ row }"><StatusBadge kind="instance" :value="row.status" /></template>
          </el-table-column>
          <el-table-column :label="t('paper.initial')" min-width="110" align="right">
            <template #default="{ row }">{{ fmtMoney(initialOf(row)) }}</template>
          </el-table-column>
          <el-table-column :label="t('instance.equity')" min-width="110" align="right">
            <template #default="{ row }">{{ fmtMoney(row.equity) }}</template>
          </el-table-column>
          <el-table-column :label="t('instance.returnRate')" min-width="100" align="right">
            <template #default="{ row }"><span :class="pnlClass(returnOf(row))">{{ fmtPct(returnOf(row)) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('instance.runtime')" min-width="90">
            <template #default="{ row }">{{ fmtDuration(row.running_seconds) }}</template>
          </el-table-column>
          <el-table-column :label="t('common.actions')" width="150" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="router.push(`/instances/${row.id}`)">{{ t('common.detail') }}</el-button>
              <el-button link type="primary" :disabled="selected.includes(row.id)" @click="addToCompare(row.id)">{{ t('paper.compare') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
        <Pager v-model:page="tbl.page.value" v-model:size="tbl.pageSize.value" :total="tbl.total.value" />
      </template>
    </section>

    <section class="card">
      <h2 class="card-title">
        {{ t('paper.compare') }}
        <el-button :icon="Download" size="small" :disabled="!selected.length" :loading="exporting" @click="exportCompare">{{ t('paper.exportCompare') }}</el-button>
      </h2>

      <div class="cmp-controls">
        <el-select
          v-model="selected"
          multiple
          filterable
          collapse-tags
          collapse-tags-tooltip
          :multiple-limit="MAX"
          :placeholder="t('paper.select')"
          :aria-label="t('paper.select')"
          class="cmp-select"
        >
          <el-option v-for="i in candidates" :key="i.id" :value="i.id" :label="`${i.name} · ${fmtSymbol(i.symbol)} · ${i.paper ? t('common.paper') : t('common.live')}`" />
        </el-select>
        <el-radio-group v-model="mode" size="small" :aria-label="t('paper.metrics')">
          <el-radio-button value="absolute">{{ t('paper.absolute') }}</el-radio-button>
          <el-radio-button value="normalized">{{ t('paper.normalized') }}</el-radio-button>
        </el-radio-group>
        <el-select v-model="preset" size="small" class="cmp-small" :aria-label="t('paper.range')">
          <el-option value="24h" :label="t('paper.last24h')" />
          <el-option value="7d" :label="t('paper.last7d')" />
          <el-option value="30d" :label="t('paper.last30d')" />
          <el-option value="all" :label="t('common.all')" />
          <el-option value="custom" :label="t('paper.range')" />
        </el-select>
        <el-date-picker
          v-if="preset === 'custom'"
          v-model="custom"
          type="datetimerange"
          size="small"
          value-format="YYYY-MM-DDTHH:mm:ssZ"
          :start-placeholder="t('common.time')"
          :end-placeholder="t('common.time')"
          :aria-label="t('paper.range')"
        />
        <el-select v-model="granularity" size="small" class="cmp-small" :placeholder="t('paper.granularity')" :aria-label="t('paper.granularity')" clearable>
          <el-option value="minute" :label="t('paper.minute')" />
          <el-option value="hour" :label="t('paper.hour')" />
          <el-option value="day" :label="t('paper.day')" />
        </el-select>
        <el-checkbox v-model="showDrawdown">{{ t('paper.showDrawdown') }}</el-checkbox>
      </div>

      <el-alert v-if="mixedNotice" type="warning" :closable="false" show-icon :title="t('paper.noAccountPluginNote')" class="cmp-note" />

      <EmptyState v-if="!selected.length" :title="t('paper.noSelection')" />
      <ErrorState v-else-if="error" :error="error" @retry="loadCompare" />
      <div v-else v-loading="loading">
        <template v-if="hasPoints">
          <LineChart :timeline="timeline" :series="equitySeries" :value-format="valueFmt" :baseline="mode === 'normalized' ? 1 : null" :aria-label="t('misc.chartAria')" />
          <template v-if="showDrawdown">
            <h3 class="sub-title">{{ t('paper.drawdown') }}</h3>
            <LineChart :timeline="timeline" :series="ddSeries" :value-format="pctFmt" height="220px" :baseline="0" :aria-label="t('misc.ddAria')" />
          </template>
        </template>
        <el-empty v-else-if="!loading" :description="t('common.empty')" :image-size="60" />

        <h3 class="sub-title">{{ t('paper.metrics') }}</h3>
        <el-table :data="rows" stripe size="small" class="resp-table">
          <el-table-column :label="t('order.instance')" min-width="140" fixed="left" show-overflow-tooltip>
            <template #default="{ row }">
              <router-link :to="`/instances/${row.instance_id}`">{{ row.name }}</router-link>
              <el-tag v-if="!row.paper" size="small" type="warning" effect="plain" class="ml">{{ t('common.live') }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="strategy" :label="t('instance.strategy')" min-width="110" show-overflow-tooltip />
          <el-table-column :label="t('instance.symbol')" min-width="100"><template #default="{ row }">{{ fmtSymbol(row.symbol) }}</template></el-table-column>
          <el-table-column :label="t('paper.initial')" min-width="100" align="right"><template #default="{ row }">{{ fmtMoney(row.initial_balance) }}</template></el-table-column>
          <el-table-column :label="t('paper.current')" min-width="100" align="right"><template #default="{ row }">{{ fmtMoney(row.equity) }}</template></el-table-column>
          <el-table-column :label="t('paper.totalReturn')" min-width="90" align="right">
            <template #default="{ row }"><span :class="pnlClass(row.total_return)">{{ fmtPct(row.total_return) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('paper.maxDrawdown')" min-width="90" align="right"><template #default="{ row }">{{ fmtPct(row.max_drawdown, 2, false) }}</template></el-table-column>
          <el-table-column :label="t('paper.sharpe')" min-width="70" align="right"><template #default="{ row }">{{ fmtNum(row.sharpe, 2) }}</template></el-table-column>
          <el-table-column :label="t('paper.winRate')" min-width="80" align="right"><template #default="{ row }">{{ fmtPct(row.win_rate, 1, false) }}</template></el-table-column>
          <el-table-column :label="t('paper.profitFactor')" min-width="80" align="right"><template #default="{ row }">{{ fmtNum(row.profit_factor, 2) }}</template></el-table-column>
          <el-table-column :label="t('paper.orderCount')" min-width="80" align="right"><template #default="{ row }">{{ row.order_count ?? '-' }}</template></el-table-column>
          <el-table-column :label="t('paper.runtime')" min-width="90">
            <template #default="{ row }">{{ row.running_hours == null ? '-' : fmtDuration(row.running_hours * 3600) }}</template>
          </el-table-column>
          <template #empty><el-empty :description="t('common.empty')" :image-size="48" /></template>
        </el-table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.cmp-controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.cmp-select { width: 420px; max-width: 100%; }
.cmp-small { width: 130px; }
.cmp-note { margin-bottom: 12px; }
.sub-title { font-size: 14px; font-weight: 600; margin: 16px 0 8px; }
.ml { margin-left: 6px; }
@media (max-width: 767px) { .cmp-select, .cmp-small { width: 100%; } }
</style>
