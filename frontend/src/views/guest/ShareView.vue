<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Refresh } from '@element-plus/icons-vue'
import { ApiError } from '@/api/http'
import { P } from '@/api/paths'
import { viewApi, type ViewData } from '@/api/view'
import type { LogEntry, Order, Position, Row } from '@/api/types'
import { describeError } from '@/composables/useErrors'
import { RealtimeSocket, wsUrl } from '@/utils/socket'
import { fmtMoney, fmtNum, fmtPct, fmtTime, pnlClass } from '@/utils/format'
import StatCard from '@/components/StatCard.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import LineChart from '@/components/LineChart.vue'
import ParamsView from '@/components/ParamsView.vue'
import LogList from '@/components/LogList.vue'

// 后端曲线点实际为 { t, v }；api/view.ts 声明为 { t|timestamp, equity }，两者都兼容
type CurvePoint = { t?: string; timestamp?: string; v?: number; equity?: number }
type Data = Omit<ViewData, 'equity_curve'> & { equity_curve?: CurvePoint[]; readonly?: boolean }
type Pos = Position & { unrealized_pnl_pct?: number }

const { t, te } = useI18n()
const route = useRoute()
const token = computed(() => String(route.params.token ?? ''))

const data = shallowRef<Data | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)
const lastAt = ref<number | null>(null)

// 后端 resolve_share：40102 无效、40103 过期、40305 撤销（或实例不可访问）
const errorKind = computed<'invalid' | 'expired' | 'revoked' | 'other' | null>(() => {
  const e = error.value
  if (!e) return null
  if (e instanceof ApiError) {
    if (e.code === 40102 || e.status === 404) return 'invalid'
    if (e.code === 40103) return 'expired'
    if (e.code === 40305) return 'revoked'
  }
  return 'other'
})
const errorTitle = computed(() => {
  const k = errorKind.value
  if (k === 'invalid') return t('view.invalid')
  if (k === 'expired') return t('view.expired')
  if (k === 'revoked') return t('view.revoked')
  return t('misc.shareLoadFailed')
})

async function load() {
  if (!token.value) {
    error.value = new ApiError({ code: 40102, status: 401, message: '' })
    return
  }
  loading.value = true
  try {
    data.value = (await viewApi.get(token.value)) as unknown as Data
    error.value = null
    lastAt.value = Date.now()
  } catch (e) {
    error.value = e
    if (errorKind.value !== 'other') stopLive()
  } finally {
    loading.value = false
  }
}

const curve = computed(() => data.value?.equity_curve ?? [])
const timeline = computed(() => curve.value.map((p) => p.t ?? p.timestamp ?? ''))
const series = computed(() => [{ name: t('view.equity'), values: curve.value.map((p) => p.v ?? p.equity ?? null) }])
const positions = computed(() => (data.value?.positions ?? null) as Pos[] | null)
const orders = computed(() => (data.value?.orders ?? null) as Order[] | null)
const logs = computed(() => (data.value?.logs ?? null) as LogEntry[] | null)
const hasEquity = computed(() => data.value?.equity !== undefined && data.value?.equity !== null)
const pctOf = (p: Row) => (p.pnl_rate ?? (p.unrealized_pnl_pct != null ? p.unrealized_pnl_pct / 100 : null))
const src = (s?: string) => (s && te(`source.${s}`) ? t(`source.${s}`) : s || '-')

// 实时：/ws/view/{token} 推送 instance.status / order.filled / position.changed，收到后节流刷新
let socket: RealtimeSocket | null = null
const liveOpen = ref(false)
let pending: ReturnType<typeof setTimeout> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
function scheduleReload() {
  if (pending) return
  pending = setTimeout(() => {
    pending = null
    void load()
  }, 1500)
}
function startLive() {
  socket = new RealtimeSocket(() => wsUrl(P.ws.view(token.value)))
  socket.onState((s) => {
    liveOpen.value = s === 'open'
    if (s === 'polling' && !pollTimer) pollTimer = setInterval(() => void load(), 30000)
    if (s === 'open' && pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  })
  socket.on(() => scheduleReload())
  socket.connect()
}
function stopLive() {
  socket?.close()
  socket = null
  liveOpen.value = false
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

onMounted(async () => {
  document.title = `${t('misc.shareTitle')} · BAMY`
  await load()
  if (!error.value || errorKind.value === 'other') startLive()
})
onBeforeUnmount(() => {
  stopLive()
  if (pending) clearTimeout(pending)
})
</script>

<template>
  <div class="share-page">
    <header class="sv-top">
      <span class="sv-brand">BAMY</span>
      <div class="sv-top-actions">
        <el-tag v-if="liveOpen" size="small" type="success" effect="plain" disable-transitions>{{ t('misc.live') }}</el-tag>
        <span v-if="lastAt" class="sv-muted">{{ fmtTime(lastAt) }}</span>
        <el-button :icon="Refresh" :loading="loading" size="small" @click="load">{{ t('common.refresh') }}</el-button>
      </div>
    </header>

    <main class="sv-main">
      <el-result v-if="error && !data" :icon="errorKind === 'other' ? 'error' : 'warning'" :title="errorTitle" :sub-title="errorKind === 'other' ? describeError(error) : ''">
        <template v-if="errorKind === 'other'" #extra>
          <el-button type="primary" @click="load">{{ t('common.retry') }}</el-button>
        </template>
      </el-result>

      <el-skeleton v-else-if="!data" :rows="8" animated />

      <template v-else>
        <el-alert v-if="error" type="warning" :closable="false" show-icon :title="errorTitle" class="sv-gap" />

        <section class="sv-head">
          <h1 class="sv-title">{{ data.instance_name }}</h1>
          <div class="sv-meta">
            <span>{{ t('instance.strategy') }}：{{ data.strategy_name || '-' }}</span>
            <span>{{ t('instance.symbol') }}：{{ data.symbol }}</span>
            <el-tag size="small" :type="data.paper ? 'info' : 'warning'" effect="plain" disable-transitions>{{ data.paper ? t('common.paper') : t('common.live') }}</el-tag>
            <StatusBadge v-if="data.status" kind="instance" :value="data.status" />
            <el-tag size="small" type="info" disable-transitions>{{ t('view.readonly') }}</el-tag>
          </div>
        </section>

        <template v-if="hasEquity">
          <div class="grid-cards">
            <StatCard :label="t('view.equity')" :value="fmtMoney(data.equity)" />
            <StatCard
              :label="t('view.returnRate')"
              :value="fmtPct(data.return_rate)"
              :tone="data.return_rate == null ? '' : data.return_rate >= 0 ? 'up' : 'down'"
            />
          </div>
          <section class="card">
            <h2 class="card-title">{{ t('view.curve') }}</h2>
            <LineChart v-if="timeline.length" :timeline="timeline" :series="series" area :aria-label="t('view.curve')" />
            <el-empty v-else :description="t('common.empty')" :image-size="60" />
          </section>
        </template>
        <el-alert v-else type="info" :closable="false" :title="t('misc.shareNoEquity')" class="sv-gap" />

        <section v-if="positions" class="card">
          <h2 class="card-title">{{ t('view.positions') }}</h2>
          <el-table :data="positions" size="small" stripe class="resp-table">
            <el-table-column prop="symbol" :label="t('instance.symbol')" min-width="100" />
            <el-table-column :label="t('instance.side')" width="70"><template #default="{ row }">{{ te(`side.${row.side}`) ? t(`side.${row.side}`) : row.side }}</template></el-table-column>
            <el-table-column :label="t('instance.amount')" min-width="100" align="right"><template #default="{ row }">{{ fmtNum(row.amount, 8) }}</template></el-table-column>
            <el-table-column :label="t('instance.entryPrice')" min-width="100" align="right"><template #default="{ row }">{{ fmtNum(row.entry_price, 8) }}</template></el-table-column>
            <el-table-column :label="t('instance.markPrice')" min-width="100" align="right"><template #default="{ row }">{{ fmtNum(row.mark_price, 8) }}</template></el-table-column>
            <el-table-column :label="t('instance.pnl')" min-width="100" align="right">
              <template #default="{ row }"><span :class="pnlClass(row.unrealized_pnl)">{{ fmtMoney(row.unrealized_pnl) }}</span></template>
            </el-table-column>
            <el-table-column :label="t('instance.pnlRate')" min-width="90" align="right">
              <template #default="{ row }"><span :class="pnlClass(pctOf(row))">{{ fmtPct(pctOf(row)) }}</span></template>
            </el-table-column>
            <template #empty><el-empty :description="t('common.empty')" :image-size="48" /></template>
          </el-table>
        </section>

        <section v-if="orders" class="card">
          <h2 class="card-title">{{ t('view.orders') }}</h2>
          <el-table :data="orders" size="small" stripe class="resp-table">
            <el-table-column :label="t('order.time')" min-width="160"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
            <el-table-column :label="t('order.side')" width="70">
              <template #default="{ row }"><span :class="row.side === 'buy' ? 'pnl-up' : 'pnl-down'">{{ te(`side.${row.side}`) ? t(`side.${row.side}`) : row.side }}</span></template>
            </el-table-column>
            <el-table-column prop="type" :label="t('order.type')" width="80" />
            <el-table-column :label="t('order.amount')" min-width="100" align="right"><template #default="{ row }">{{ fmtNum(row.amount, 8) }}</template></el-table-column>
            <el-table-column :label="t('order.price')" min-width="100" align="right"><template #default="{ row }">{{ fmtNum(row.price, 8) }}</template></el-table-column>
            <el-table-column :label="t('order.filledAmount')" min-width="100" align="right"><template #default="{ row }">{{ fmtNum(row.filled_amount, 8) }}</template></el-table-column>
            <el-table-column :label="t('order.filledPrice')" min-width="100" align="right"><template #default="{ row }">{{ fmtNum(row.filled_price, 8) }}</template></el-table-column>
            <el-table-column :label="t('order.status')" width="100"><template #default="{ row }"><StatusBadge kind="order" :value="row.status" /></template></el-table-column>
            <el-table-column :label="t('order.source')" width="80"><template #default="{ row }">{{ src(row.source) }}</template></el-table-column>
            <template #empty><el-empty :description="t('common.empty')" :image-size="48" /></template>
          </el-table>
        </section>

        <section v-if="data.params" class="card">
          <h2 class="card-title">{{ t('view.params') }}</h2>
          <ParamsView :params="data.params" />
        </section>

        <section v-if="logs" class="card">
          <h2 class="card-title">{{ t('view.logs') }}</h2>
          <LogList :logs="[...logs].reverse()" height="360px" show-module />
        </section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.share-page { min-height: 100vh; background: var(--el-bg-color-page); }
.sv-top { position: sticky; top: 0; z-index: 10; display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 10px 16px; background: var(--el-bg-color); border-bottom: 1px solid var(--el-border-color-lighter); }
.sv-brand { font-weight: 700; letter-spacing: 1px; }
.sv-top-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.sv-muted { color: var(--el-text-color-secondary); font-size: 12px; }
.sv-main { max-width: 1100px; margin: 0 auto; padding: 16px; }
.sv-head { margin-bottom: 16px; }
.sv-title { margin: 0 0 6px; font-size: 22px; font-weight: 600; word-break: break-all; }
.sv-meta { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; color: var(--el-text-color-secondary); font-size: 13px; }
.sv-gap { margin-bottom: 16px; }
.card { margin-bottom: 16px; }
.card + .card { margin-top: 0; }
@media (max-width: 767px) { .sv-main { padding: 12px; } .sv-muted { display: none; } }
</style>
