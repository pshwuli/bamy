<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { instancesApi, shareApi, type InstanceStatusInfo, type PaperAccount, type ShareInput, type Snapshot } from '@/api/instances'
import { strategiesApi, itemsOf } from '@/api/extensions'
import { ApiError, fieldErrors } from '@/api/http'
import type { LogEntry, Order, Position, Row, SchemaField, ShareToken, Strategy } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { useLive } from '@/composables/useLive'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { MoreFilled } from '@element-plus/icons-vue'
import { confirm } from '@/composables/useConfirm'
import { attempt, handleError } from '@/composables/useErrors'
import { useAuth } from '@/stores/auth'
import { copyText, fmtDuration, fmtMoney, fmtNum, fmtPct, fmtTime, pnlClass, sinceSeconds, fmtSymbol } from '@/utils/format'
import { mergeValues } from '@/utils/schema'
import PageHeader from '@/components/PageHeader.vue'
import Pager from '@/components/Pager.vue'
import FilterBar from '@/components/FilterBar.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import StatCard from '@/components/StatCard.vue'
import ErrorState from '@/components/ErrorState.vue'
import SchemaForm from '@/components/SchemaForm.vue'
import OrderTable from '@/components/OrderTable.vue'
import AuditTable from '@/components/AuditTable.vue'
import LogList from '@/components/LogList.vue'
import LineChart from '@/components/LineChart.vue'
import ParamsView from '@/components/ParamsView.vue'
import CapabilityBadges from '@/components/CapabilityBadges.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuth()

const id = computed(() => Number(route.params.id))
const inst = ref<Row | null>(null)
const loading = ref(true)
const loadError = ref<unknown>(null)
const strategy = shallowRef<Strategy | null>(null)
const tab = ref((route.query.tab as string) || 'overview')

const caps = computed<Record<string, boolean>>(() => strategy.value?.capabilities ?? inst.value?.capabilities ?? {})
const schema = computed<SchemaField[]>(() => strategy.value?.params_schema ?? [])
const isOwner = computed(() => !inst.value?.owner_id || inst.value.owner_id === auth.user?.id)
const stopped = computed(() => ['stopped', 'error', 'created'].includes(inst.value?.status))
const active = computed(() => ['running', 'paused', 'starting', 'resuming', 'pausing'].includes(inst.value?.status))
const hasClose = computed(() => !!(caps.value.close_position || caps.value.close_all))

async function loadInstance() {
  loadError.value = null
  try {
    const d = await instancesApi.get(id.value)
    inst.value = d
    if (!strategy.value || strategy.value.id !== d.strategy_id) {
      strategy.value = await strategiesApi.get(d.strategy_id).catch(() => null)
    }
  } catch (e) {
    loadError.value = e
  } finally {
    loading.value = false
  }
}

// ---------- 概览 ----------
const paper = shallowRef<(PaperAccount & { fee_rate?: number; slippage?: number }) | null>(null)
const snapshots = shallowRef<(Snapshot & { snapshot_at?: string; drawdown?: number })[]>([])
const positions = shallowRef<Position[]>([])
const posLoading = ref(false)
const posError = ref<unknown>(null)
const recentOrders = shallowRef<Order[]>([])

const equity = computed<number | null>(() => paper.value?.equity ?? inst.value?.equity ?? null)
const returnRate = computed<number | null>(() => {
  const i = inst.value
  if (!i) return null
  if (paper.value?.initial_balance) return (paper.value.equity - paper.value.initial_balance) / paper.value.initial_balance
  if (i.return_rate != null) return Number(i.return_rate)
  if (i.return_pct != null) return Number(i.return_pct) / 100
  return null
})
const maxDrawdown = computed<number | null>(() => {
  if (!snapshots.value.length) return null
  let peak = -Infinity
  let dd = 0
  for (const s of snapshots.value) {
    if (s.drawdown != null) dd = Math.max(dd, Number(s.drawdown))
    peak = Math.max(peak, Number(s.equity))
    if (peak > 0) dd = Math.max(dd, (peak - Number(s.equity)) / peak)
  }
  return dd
})
const runSecs = computed<number | null>(() => {
  const i = inst.value
  if (!i || !['running', 'paused'].includes(i.status)) return null
  return i.running_seconds ?? sinceSeconds(i.running_since ?? i.last_started_at)
})
const curve = computed(() => ({
  timeline: snapshots.value.map((s) => s.snapshot_at ?? s.timestamp ?? s.ts ?? s.at ?? ''),
  series: [{ name: t('instance.equity'), values: snapshots.value.map((s) => Number(s.equity)) }],
}))
const pnlRate = (p: Row): number | null =>
  p.pnl_rate != null ? Number(p.pnl_rate) : p.unrealized_pnl_pct != null ? Number(p.unrealized_pnl_pct) / 100 : null

async function loadPaper() {
  if (!inst.value) return
  // 权益快照：模拟盘和实盘都有（实盘要设了本金才写）
  const [a, s] = await Promise.allSettled([
    inst.value.paper ? instancesApi.paperAccount(id.value) : Promise.reject(new Error('live')),
    instancesApi.paperSnapshots(id.value, {}),
  ])
  if (a.status === 'fulfilled') paper.value = a.value
  if (s.status === 'fulfilled') snapshots.value = itemsOf(s.value as never)
}

// 实盘本金：用来算收益率和回撤（不影响下单）；运行中也能改
const liveCapital = computed<number | null>(() => {
  const v = Number((inst.value?.paper_config as Row | null)?.live_capital)
  return Number.isFinite(v) && v > 0 ? v : null
})
const livePnl = computed<number | null>(() => (inst.value && !inst.value.paper && inst.value.pnl != null ? Number(inst.value.pnl) : null))
const capOpen = ref(false)
const capValue = ref<number | null>(null)
const capSaving = ref(false)
function openCapital() {
  capValue.value = liveCapital.value
  capOpen.value = true
}
async function saveCapital() {
  if (!capValue.value || capValue.value <= 0) return
  capSaving.value = true
  try {
    await instancesApi.update(id.value, { paper_config: { live_capital: capValue.value } })
    capOpen.value = false
    ElMessage.success(t('common.saved'))
    await loadInstance()
    await loadPaper()
  } catch (e) {
    handleError(e)
  } finally {
    capSaving.value = false
  }
}

async function loadPositions() {
  posLoading.value = true
  posError.value = null
  try {
    const d = await instancesApi.positions(id.value)
    positions.value = Array.isArray(d) ? (d as Position[]) : d?.positions ?? []
  } catch (e) {
    posError.value = e
  } finally {
    posLoading.value = false
  }
}

async function loadRecentOrders() {
  try {
    recentOrders.value = (await instancesApi.orders(id.value, { page: 1, page_size: 5 })).items ?? []
  } catch {
    recentOrders.value = []
  }
}

// 策略状态：上次判断结果 / 下次判断时间 / 最近一行日志，区分"在等机会"和"卡住了"
const runInfo = ref<InstanceStatusInfo | null>(null)
async function loadRunInfo() {
  try { runInfo.value = await instancesApi.status(id.value) } catch { /* 静默：状态卡片只是辅助 */ }
}

function refreshAll() {
  void loadRunInfo()
  // loadPaper 依赖 inst.paper，必须等实例详情回来再取，否则首屏模拟账户与净值曲线一直为空
  void loadInstance().then(() => loadPaper())
  void loadPositions()
  void loadRecentOrders()
}

// ---------- 顶部动作 ----------
const acting = ref(false)
async function act(action: 'start' | 'stop' | 'pause' | 'resume') {
  if (!inst.value) return
  if (action === 'stop' && !(await confirm(t('instx.stopConfirm', { name: inst.value.name })))) return
  if ((action === 'pause' && !caps.value.pause) || (action === 'resume' && !caps.value.resume)) {
    ElMessage.warning(t('instx.pauseUnsupported'))
    return
  }
  acting.value = true
  const r = await attempt(() => instancesApi[action](id.value))
  acting.value = false
  if (r?.status && inst.value) inst.value = { ...inst.value, status: r.status }
  void loadInstance()
}

async function removeInstance() {
  if (!inst.value) return
  if (!stopped.value) {
    ElMessage.warning(t('instance.needStopped'))
    return
  }
  if (!(await confirm(t('instance.deleteConfirm', { name: inst.value.name }), t('common.delete'), true))) return
  const ok = await attempt(async () => {
    await instancesApi.remove(id.value)
    return true
  }, t('instx.deleted'))
  if (ok) void router.replace('/instances')
}

const resetOpen = ref(false)
const resetSaving = ref(false)
const resetForm = reactive({ keep_orders: false, keep_snapshots: false })
function openReset() {
  if (!stopped.value) {
    ElMessage.warning(t('instance.needStopped'))
    return
  }
  Object.assign(resetForm, { keep_orders: false, keep_snapshots: false })
  resetOpen.value = true
}
async function submitReset() {
  resetSaving.value = true
  const ok = await attempt(async () => {
    await instancesApi.resetPaper(id.value, { ...resetForm })
    return true
  }, t('instx.resetDone'))
  resetSaving.value = false
  if (ok) {
    resetOpen.value = false
    refreshAll()
  }
}

// ---------- 平仓 ----------
const posSelection = ref<Position[]>([])
const closing = ref(false)
async function runClose(fn: () => Promise<unknown>) {
  if (closing.value) return   // 防连点：上一笔平仓没回来之前不再发
  closing.value = true
  const res = await attempt(async () => (await fn()) ?? {})
  closing.value = false
  // 不论成功失败都刷新：以交易所/模拟盘的真实持仓为准，而不是以按钮结果为准
  void loadPositions()
  void loadPaper()
  void loadRecentOrders()
  if (res === undefined) return
  posSelection.value = []
  const failed = Number((res as Row)?.failed ?? 0)
  if (failed > 0) ElMessage.warning(t('instx.closePartial', { n: failed }))
}
async function closeAllPositions() {
  if (!positions.value.length) return
  if (!hasClose.value) {
    if (!(await confirm(t('instx.forceCloseConfirm'), t('instx.forceCloseAll'), true))) return
    // position_id 省略 = 平掉全部
    return runClose(() => instancesApi.forceClose(id.value))
  }
  if (!(await confirm(t('instance.closeAllConfirm'), t('instance.closeAll'), true))) return
  if (caps.value.close_all) return runClose(() => instancesApi.closeAll(id.value))
  const ids = positions.value.map((p) => String(p.id))
  return runClose(async () => {
    for (const pid of ids) await instancesApi.closePosition(id.value, pid, null)
  })
}
async function closeSelected() {
  const ids = posSelection.value.map((p) => String(p.id))
  if (!ids.length) return
  if (!(await confirm(t('instx.closeSelectedConfirm', { n: ids.length }), t('instance.closeSelected'), true))) return
  if (caps.value.close_positions) return runClose(() => instancesApi.closeBatch(id.value, ids))
  if (caps.value.close_position) return runClose(async () => {
    for (const pid of ids) await instancesApi.closePosition(id.value, pid, null)
  })
  return runClose(async () => {
    for (const pid of ids) await instancesApi.forceClose(id.value, pid)
  })
}

const closeOpen = ref(false)
const closeTarget = shallowRef<Position | null>(null)
const closeAmount = ref<number | null>(null)
function openClose(p: Row) {
  closeTarget.value = p
  closeAmount.value = Number(p.amount)
  closeOpen.value = true
}
async function submitClose() {
  const p = closeTarget.value
  if (!p) return
  const full = closeAmount.value == null || closeAmount.value >= Number(p.amount)
  closeOpen.value = false
  if (!hasClose.value) {
    if (!(await confirm(t('instx.forceCloseConfirm'), t('instance.forceClose'), true))) return
    return runClose(() => instancesApi.forceClose(id.value, String(p.id)))
  }
  if (!full && !caps.value.close_partial) {
    ElMessage.warning(t('instx.partialUnsupported'))
    return
  }
  return runClose(() => instancesApi.closePosition(id.value, String(p.id), full ? null : closeAmount.value))
}
// ---------- 订单 ----------
const orderRange = ref<[string, string] | null>(null)
const ord = useTable<Order>((q) => instancesApi.orders(id.value, q), { status: '', side: '' }, {
  immediate: false,
  transform: (q) => ({ ...q, start_time: orderRange.value?.[0], end_time: orderRange.value?.[1] }),
})
const ORDER_STATUSES = ['submitted', 'partial', 'filled', 'canceled', 'rejected', 'unknown', 'failed'] as const
function resetOrders() {
  orderRange.value = null
  void ord.resetFilters()
}
async function cancelOrder(o: Row) {
  if (!(await confirm(t('instx.cancelOrderConfirm', { id: o.client_order_id || o.id })))) return
  if (await attempt(async () => (await instancesApi.cancelOrder(id.value, o.id), true))) void ord.load()
}
async function cancelAllOrders() {
  if (!(await confirm(t('instance.cancelAllConfirm'), t('instance.cancelAll'), true))) return
  if (await attempt(async () => (await instancesApi.cancelAll(id.value), true))) void ord.load()
}

// ---------- 日志 ----------
const logs = ref<LogEntry[]>([])
const logLevel = ref('')
const logRange = ref<[string, string] | null>(null)
const logLoading = ref(false)
const logError = ref<unknown>(null)
const autoScroll = ref(true)
const LOG_LEVELS = ['debug', 'info', 'warn', 'error'] as const
const LOG_MAX = 1000
async function loadLogs() {
  logLoading.value = true
  logError.value = null
  try {
    const d = await instancesApi.logs(id.value, {
      page: 1,
      page_size: 200,
      level: logLevel.value,
      start_time: logRange.value?.[0],
      end_time: logRange.value?.[1],
    })
    logs.value = [...(d.items ?? [])].reverse()
  } catch (e) {
    logError.value = e
  } finally {
    logLoading.value = false
  }
}
function resetLogs() {
  logLevel.value = ''
  logRange.value = null
  void loadLogs()
}

// ---------- 参数编辑 ----------
const paramsEditing = ref(false)
const paramsModel = ref<Record<string, unknown>>({})
const paramsErrs = ref<Record<string, string>>({})
const paramsSaving = ref(false)
const paramsForm = ref<InstanceType<typeof SchemaForm>>()
function startEditParams() {
  if (!stopped.value) {
    ElMessage.warning(t('instance.needStopped'))
    return
  }
  paramsModel.value = mergeValues(schema.value, inst.value?.params)
  paramsErrs.value = {}
  tab.value = 'params'
  paramsEditing.value = true
}
async function saveParams() {
  if (!paramsForm.value?.validate()) return
  paramsSaving.value = true
  paramsErrs.value = {}
  try {
    inst.value = await instancesApi.update(id.value, { params: paramsForm.value.values() })
    ElMessage.success(t('common.success'))
    paramsEditing.value = false
  } catch (e) {
    paramsErrs.value = fieldErrors(e)
    handleError(e)
  } finally {
    paramsSaving.value = false
  }
}

// ---------- 换策略 ----------
const csOpen = ref(false)
const csSaving = ref(false)
const csList = shallowRef<Strategy[]>([])
const csStrategyId = ref('')
const csStrategy = shallowRef<Strategy | null>(null)
const csParams = ref<Record<string, unknown>>({})
const csErrs = ref<Record<string, string>>({})
const csNeedClose = ref(false)
const csClose = ref(false)
const csLoading = ref(false)
const csForm = ref<InstanceType<typeof SchemaForm>>()
async function openChange() {
  if (!stopped.value) {
    ElMessage.warning(t('instance.needStopped'))
    return
  }
  csStrategyId.value = ''
  csStrategy.value = null
  csErrs.value = {}
  csNeedClose.value = positions.value.length > 0
  csClose.value = false
  csOpen.value = true
  if (!csList.value.length) {
    csList.value = itemsOf(await strategiesApi.list({ page_size: 100 }).catch(() => null)).filter((s) => s.loaded !== false)
  }
}
async function pickStrategy(sid: string) {
  csStrategy.value = null
  if (!sid) return
  csLoading.value = true
  try {
    csStrategy.value = await strategiesApi.get(sid)
    csParams.value = mergeValues(csStrategy.value.params_schema ?? [], {})
  } finally {
    csLoading.value = false
  }
}
async function submitChange() {
  if (!csStrategy.value || (csForm.value && !csForm.value.validate())) return
  csSaving.value = true
  csErrs.value = {}
  try {
    const body = { strategy_id: csStrategy.value.id, params: csForm.value ? csForm.value.values() : {}, close_positions: csClose.value }
    inst.value = await instancesApi.changeStrategy(id.value, body as { strategy_id: string; params: Record<string, unknown> })
    strategy.value = csStrategy.value
    ElMessage.success(t('common.success'))
    csOpen.value = false
    refreshAll()
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
// ---------- 分享 ----------
const shares = shallowRef<Row[]>([])
const shareLoading = ref(false)
const shareError = ref<unknown>(null)
const shareOpen = ref(false)
const shareSaving = ref(false)
const shareNever = ref(false)
const shareForm = reactive<ShareInput>({ expires_in_days: 7, show_equity: true, show_positions: true, show_orders: true, show_params: false, show_logs: false })
const SHOW_KEYS = ['show_equity', 'show_positions', 'show_orders', 'show_params', 'show_logs'] as const
const showLabel: Record<(typeof SHOW_KEYS)[number], string> = {
  show_equity: 'instance.showEquity',
  show_positions: 'instance.showPositions',
  show_orders: 'instance.showOrders',
  show_params: 'instance.showParams',
  show_logs: 'instance.showLogs',
}
async function loadShares() {
  shareLoading.value = true
  shareError.value = null
  try {
    shares.value = itemsOf<ShareToken>(await shareApi.list({ instance_id: id.value }))
  } catch (e) {
    shareError.value = e
  } finally {
    shareLoading.value = false
  }
}
function shareStatus(s: Row): 'active' | 'revoked' | 'expired' {
  if (s.status) return s.status
  if (s.revoked || s.revoked_at) return 'revoked'
  if (s.expires_at && new Date(s.expires_at).getTime() < Date.now()) return 'expired'
  return 'active'
}
const shareTone = { active: 'success', revoked: 'info', expired: 'warning' } as const
const shareStatusLabel = { active: 'instance.shareActive', revoked: 'instance.shareRevoked', expired: 'instance.shareExpired' } as const
const shareUrl = (s: Row) => s.url || `${window.location.origin}/view/${s.token}`
function openShare() {
  Object.assign(shareForm, { expires_in_days: 7, show_equity: true, show_positions: true, show_orders: true, show_params: false, show_logs: false })
  shareNever.value = false
  shareOpen.value = true
}
async function submitShare() {
  shareSaving.value = true
  try {
    const body = { ...shareForm, expires_in_days: shareNever.value ? null : shareForm.expires_in_days }
    const s = await instancesApi.createShare(id.value, body as unknown as ShareInput)
    shareOpen.value = false
    ElMessage.success(t('instx.shareCreated'))
    if (s) await copyLink(s)
    void loadShares()
  } catch {
    /* 全局已提示 */
  } finally {
    shareSaving.value = false
  }
}
async function copyLink(s: Row) {
  if (await copyText(shareUrl(s))) ElMessage.success(t('common.copied'))
}
async function revokeShare(s: Row) {
  if (!(await confirm(t('instance.shareRevokeConfirm'), t('instance.shareRevoke'), true))) return
  if (await attempt(async () => (await shareApi.revoke(s.id), true))) void loadShares()
}

// ---------- Tab 懒加载 ----------
const loadedTabs = new Set<string>()
function ensureTab(name: string) {
  if (loadedTabs.has(name)) return
  loadedTabs.add(name)
  if (name === 'orders') void ord.load()
  else if (name === 'logs') void loadLogs()
  else if (name === 'share') void loadShares()
}
watch(tab, (v) => {
  ensureTab(v)
  void router.replace({ query: { ...route.query, tab: v } })
})

// ---------- 实时 ----------
const timers: Record<string, ReturnType<typeof setTimeout>> = {}
function debounced(key: string, fn: () => void, ms = 400) {
  clearTimeout(timers[key])
  timers[key] = setTimeout(fn, ms)
}
onBeforeUnmount(() => Object.values(timers).forEach(clearTimeout))
useLive({
  topics: () => [`instance.${id.value}.status`, `instance.${id.value}.logs`, `instance.${id.value}.orders`, `instance.${id.value}.positions`],
  onEvent: (m) => {
    const d = (m.data ?? {}) as Row
    if (Number(d.instance_id) !== id.value) return
    if (m.event === 'instance.status') {
      if (inst.value && d.status) inst.value = { ...inst.value, status: d.status, last_error: d.last_error ?? inst.value.last_error }
      if (['running', 'stopped', 'error'].includes(d.status)) void loadInstance()
    } else if (m.event === 'instance.log') {
      if (logLevel.value && d.level !== logLevel.value) return
      logs.value = [...logs.value, { level: d.level, message: d.message, timestamp: d.ts ? new Date(d.ts).toISOString() : new Date().toISOString(), instance_id: id.value }].slice(-LOG_MAX)
    } else if (m.event.startsWith('order.')) {
      // 一次下单会连发 created/submitted/filled，合并成一次刷新，避免触发接口限流
      debounced('orders', () => {
        void loadRecentOrders()
        if (loadedTabs.has('orders')) void ord.load()
      })
    } else if (m.event.startsWith('position.')) {
      debounced('positions', () => {
        void loadPositions()
        void loadPaper()
      })
    }
  },
  poll: () => {
    void loadInstance()
    void loadPositions()
    if (tab.value === 'logs') void loadLogs()
    void loadRecentOrders()
    if (loadedTabs.has('orders')) void ord.load()
  },
})

// 推送可能丢失（断线重连窗口），运行中的实例另做低频兜底刷新
const backstop = setInterval(() => {
  if (inst.value?.status !== 'running' || document.hidden) return
  void loadRecentOrders()
  void loadRunInfo()
  if (tab.value === 'orders') void ord.load()
}, 10000)
onBeforeUnmount(() => clearInterval(backstop))

watch(id, () => {
  loadedTabs.clear()
  loading.value = true
  strategy.value = null
  refreshAll()
  ensureTab(tab.value)
})

onMounted(() => {
  refreshAll()
  ensureTab(tab.value)
})

const auditFetcher = (q: Record<string, unknown>) => instancesApi.auditLogs(id.value, q as never)
const fmtSide = (s?: string) => (s ? t(`side.${s}`) : '-')
const accountLabel = computed(() => {
  const i = inst.value
  if (!i) return '-'
  return i.paper ? t('common.paper') : i.account_name || (i.account_id ? `#${i.account_id}` : '-')
})

const { isMobile } = useBreakpoint()
function onMore(cmd: string) {
  if (cmd === 'close') void closeAllPositions()
  else if (cmd === 'edit') startEditParams()
  else if (cmd === 'change') openChange()
  else if (cmd === 'delete') void removeInstance()
}
</script>

<template>
  <div>
    <ErrorState v-if="loadError && !inst" :error="loadError" @retry="refreshAll" />
    <div v-else v-loading="loading && !inst">
      <template v-if="inst">
        <PageHeader :title="inst.name" back>
          <template #sub>
            <span class="meta">
              <StatusBadge kind="instance" :value="inst.status" />
              <span>{{ t('instance.strategy') }}：{{ inst.strategy_name || inst.strategy_id }}<template v-if="inst.strategy_version"> v{{ inst.strategy_version }}</template></span>
              <span>{{ t('instance.symbol') }}：{{ fmtSymbol(inst.symbol) }}</span>
              <span>{{ t('instance.account') }}：{{ accountLabel }}</span>
              <el-tag size="small" :type="inst.paper ? 'info' : 'warning'" disable-transitions>{{ inst.paper ? t('common.paper') : t('common.live') }}</el-tag>
            </span>
          </template>
          <template v-if="isOwner && !isMobile">
            <el-button v-if="active" type="warning" :loading="acting" @click="act('stop')">{{ t('instance.stop') }}</el-button>
            <el-button v-else type="primary" :loading="acting" @click="act('start')">{{ t('instance.start') }}</el-button>
            <el-button v-if="inst.status === 'running'" :disabled="!caps.pause" :title="caps.pause ? '' : t('instx.pauseUnsupported')" @click="act('pause')">{{ t('instance.pause') }}</el-button>
            <el-button v-if="inst.status === 'paused'" :disabled="!caps.resume" :title="caps.resume ? '' : t('instx.pauseUnsupported')" @click="act('resume')">{{ t('instance.resume') }}</el-button>
            <el-button :disabled="!positions.length" :loading="closing" @click="closeAllPositions">{{ t('instance.close') }}</el-button>
            <el-button :disabled="!stopped" :title="stopped ? '' : t('instance.needStopped')" @click="startEditParams">{{ t('common.edit') }}</el-button>
            <el-button :disabled="!stopped" :title="stopped ? '' : t('instance.needStopped')" @click="openChange">{{ t('instance.changeStrategy') }}</el-button>
            <el-button type="danger" plain :disabled="!stopped" @click="removeInstance">{{ t('common.delete') }}</el-button>
          </template>
          <template v-if="isOwner && isMobile">
            <div class="m-actions">
              <el-button v-if="active" type="warning" :loading="acting" @click="act('stop')">{{ t('instance.stop') }}</el-button>
              <el-button v-else type="primary" :loading="acting" @click="act('start')">{{ t('instance.start') }}</el-button>
              <el-button v-if="inst.status === 'running' && caps.pause" @click="act('pause')">{{ t('instance.pause') }}</el-button>
              <el-button v-else-if="inst.status === 'paused' && caps.resume" @click="act('resume')">{{ t('instance.resume') }}</el-button>
              <el-button v-else :disabled="!positions.length" :loading="closing" @click="closeAllPositions">{{ t('instance.close') }}</el-button>
              <el-dropdown trigger="click" @command="onMore">
                <el-button :icon="MoreFilled" :aria-label="t('tab.more')" />
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item v-if="inst.status === 'running' && caps.pause" command="close" :disabled="!positions.length">{{ t('instance.close') }}</el-dropdown-item>
                    <el-dropdown-item command="edit" :disabled="!stopped">{{ t('common.edit') }}{{ stopped ? '' : `（${t('instance.needStopped')}）` }}</el-dropdown-item>
                    <el-dropdown-item command="change" :disabled="!stopped">{{ t('instance.changeStrategy') }}</el-dropdown-item>
                    <el-dropdown-item command="delete" :disabled="!stopped" divided><span class="danger-text">{{ t('common.delete') }}</span></el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </PageHeader>

        <el-alert v-if="!isOwner" type="info" :title="t('instx.notOwner')" :closable="false" show-icon class="mb" />
        <el-alert v-if="inst.last_error" type="error" :title="`${t('instance.lastError')}：${inst.last_error}`" :closable="false" show-icon class="mb" />
        <p v-if="!isMobile" class="note">{{ t('instance.noPluginNote') }}</p>

        <el-tabs v-model="tab">
          <!-- 概览 -->
          <el-tab-pane :label="t('instance.tabOverview')" name="overview">
            <el-alert v-if="!inst.paper && !liveCapital" type="info" :closable="false" show-icon class="mb" :title="t('livex.noCapital')">
              <el-button v-if="isOwner" size="small" type="primary" @click="openCapital">{{ t('livex.setCapital') }}</el-button>
            </el-alert>
            <div v-if="isMobile" class="m-figs">
              <div class="m-eq">
                <span>{{ !inst.paper && !liveCapital ? t('livex.pnl') : t('instance.equity') }}</span>
                <b class="num" :class="!inst.paper && !liveCapital ? pnlClass(livePnl) : ''">{{ !inst.paper && !liveCapital ? fmtMoney(livePnl) : fmtMoney(equity) }}</b>
                <button v-if="!inst.paper && liveCapital && isOwner" type="button" class="cap-link" @click="openCapital">{{ t('livex.capital') }} {{ fmtMoney(liveCapital) }} · {{ t('common.edit') }}</button>
              </div>
              <dl>
                <div><dt>{{ t('instance.returnRate') }}</dt><dd class="num" :class="pnlClass(returnRate)">{{ fmtPct(returnRate) }}</dd></div>
                <div><dt>{{ t('instance.maxDrawdown') }}</dt><dd class="num">{{ maxDrawdown == null ? '—' : fmtPct(maxDrawdown, 2, false) }}</dd></div>
                <div><dt>{{ t('instance.runtime') }}</dt><dd class="num">{{ fmtDuration(runSecs) }}</dd></div>
              </dl>
            </div>
            <div v-else class="grid-cards">
              <StatCard v-if="!inst.paper && !liveCapital" :label="t('livex.pnl')" :value="fmtMoney(livePnl)" :tone="livePnl == null ? '' : livePnl >= 0 ? 'up' : 'down'" class="big" />
              <StatCard v-else :label="t('instance.equity')" :value="fmtMoney(equity)" class="big" />
              <StatCard :label="t('instance.returnRate')" :value="fmtPct(returnRate)" :tone="returnRate == null ? '' : returnRate >= 0 ? 'up' : 'down'" />
              <StatCard :label="t('instance.maxDrawdown')" :value="maxDrawdown == null ? '-' : fmtPct(maxDrawdown, 2, false)" />
              <StatCard :label="t('instance.runtime')" :value="fmtDuration(runSecs)" />
            </div>
            <p v-if="!inst.paper && liveCapital && !isMobile" class="note">
              {{ t('livex.capital') }} {{ fmtMoney(liveCapital) }} USDT · {{ t('livex.basis') }}
              <el-button v-if="isOwner" link type="primary" @click="openCapital">{{ t('common.edit') }}</el-button>
            </p>
            <el-dialog v-model="capOpen" :title="t('livex.setCapital')" width="min(420px, 92vw)" append-to-body>
              <p class="note">{{ t('livex.capitalHelp') }}</p>
              <el-input-number v-model="capValue" :min="1" :step="100" controls-position="right" style="width: 100%" />
              <template #footer>
                <el-button @click="capOpen = false">{{ t('common.cancel') }}</el-button>
                <el-button type="primary" :loading="capSaving" :disabled="!capValue" @click="saveCapital">{{ t('common.save') }}</el-button>
              </template>
            </el-dialog>

            <div v-if="runInfo && (runInfo.strategy_status || runInfo.last_log)" class="run-info mb">
              <div class="ri-head">{{ t('instx.runInfo') }}</div>
              <p v-if="runInfo.strategy_status" class="ri-sum">{{ runInfo.strategy_status.summary }}</p>
              <p v-if="runInfo.strategy_status?.at" class="ri-meta">
                {{ t('instx.lastDecision') }} {{ fmtTime(runInfo.strategy_status.at * 1000, false) }}
                <template v-if="runInfo.strategy_status.next_at"> · {{ t('instx.nextDecision') }} {{ fmtTime(runInfo.strategy_status.next_at * 1000, false) }}</template>
              </p>
              <p v-if="runInfo.last_log" class="ri-meta">{{ t('instx.lastLog') }} {{ fmtTime(runInfo.last_log.at) }}：{{ runInfo.last_log.message }}</p>
              <p v-if="runInfo.last_tick_at" class="ri-meta">{{ t('instx.lastTick') }} {{ fmtTime(runInfo.last_tick_at) }}</p>
            </div>

            <el-card v-if="inst.paper" shadow="never" class="mb">
              <template #header>
                <div class="card-head">
                  <span>{{ t('instance.paperAccount') }}</span>
                  <el-button v-if="isOwner" size="small" type="danger" plain :disabled="!stopped" :title="stopped ? '' : t('instance.needStopped')" @click="openReset">{{ t('instance.resetPaper') }}</el-button>
                </div>
              </template>
              <el-descriptions v-if="paper" :column="isMobile ? 2 : 3" border size="small" class="desc-resp">
                <el-descriptions-item :label="t('instance.balance')">{{ fmtMoney(paper.balance) }}</el-descriptions-item>
                <el-descriptions-item :label="t('instance.equity')">{{ fmtMoney(paper.equity) }}</el-descriptions-item>
                <el-descriptions-item :label="t('instance.initial')">{{ fmtMoney(paper.initial_balance) }}</el-descriptions-item>
                <el-descriptions-item :label="t('instance.realized')"><span :class="pnlClass(paper.realized_pnl)">{{ fmtMoney(paper.realized_pnl) }}</span></el-descriptions-item>
                <el-descriptions-item :label="t('instance.unrealized')"><span :class="pnlClass(paper.unrealized_pnl)">{{ fmtMoney(paper.unrealized_pnl) }}</span></el-descriptions-item>
                <el-descriptions-item :label="t('instance.peak')">{{ fmtMoney(paper.peak_equity) }}</el-descriptions-item>
                <el-descriptions-item v-if="paper.fee_rate != null" :label="t('instx.feeRate')">{{ fmtNum(paper.fee_rate, 6) }}</el-descriptions-item>
                <el-descriptions-item v-if="paper.slippage != null" :label="t('instx.slippage')">{{ fmtNum(paper.slippage, 6) }}</el-descriptions-item>
              </el-descriptions>
              <el-empty v-else :description="t('common.empty')" :image-size="40" />
            </el-card>

            <el-card shadow="never" class="mb">
              <template #header>{{ t('instance.curve') }}</template>
              <LineChart v-if="inst.paper && snapshots.length" :timeline="curve.timeline" :series="curve.series" :value-format="(v: number) => fmtMoney(v)" area :aria-label="t('instance.curve')" height="280px" />
              <el-empty v-else :description="inst.paper ? t('instx.noCurve') : t('instx.liveCurveNote')" :image-size="48" />
            </el-card>

            <div class="grid-2">
              <el-card shadow="never">
                <template #header>
                  <div class="card-head"><span>{{ t('instance.positionsSummary') }}</span><el-button link type="primary" @click="tab = 'positions'">{{ t('instx.viewAll') }}</el-button></div>
                </template>
                <el-table :data="positions.slice(0, 5)" size="small" class="resp-table">
                  <el-table-column :label="t('instance.symbol')" min-width="100"><template #default="{ row }">{{ fmtSymbol(row.symbol) }}</template></el-table-column>
                  <el-table-column :label="t('instance.amount')" align="right"><template #default="{ row }">{{ fmtNum(row.amount, 8) }}</template></el-table-column>
                  <el-table-column :label="t('instance.pnl')" align="right"><template #default="{ row }"><span :class="pnlClass(row.unrealized_pnl)">{{ fmtMoney(row.unrealized_pnl) }}</span></template></el-table-column>
                  <template #empty><el-empty :description="t('common.empty')" :image-size="40" /></template>
                </el-table>
              </el-card>
              <el-card shadow="never">
                <template #header>
                  <div class="card-head"><span>{{ t('instance.recentOrders') }}</span><el-button link type="primary" @click="tab = 'orders'">{{ t('instx.viewAll') }}</el-button></div>
                </template>
                <OrderTable :orders="recentOrders" :show-instance="false" compact :cancellable="false" />
              </el-card>
            </div>

            <el-card shadow="never">
              <template #header>{{ t('instance.currentParams') }}</template>
              <ParamsView :params="inst.params" :schema="schema" />
            </el-card>
          </el-tab-pane>

          <!-- 持仓 -->
          <el-tab-pane :label="t('instance.tabPositions')" name="positions">
            <div class="toolbar">
              <CapabilityBadges :caps="caps" />
              <span class="spacer" />
              <template v-if="isOwner">
                <el-button size="small" :disabled="!posSelection.length" :loading="closing" @click="closeSelected">{{ t('instance.closeSelected') }}</el-button>
                <el-button size="small" type="danger" :disabled="!positions.length" :loading="closing" @click="closeAllPositions">{{ hasClose ? t('instance.closeAll') : t('instx.forceCloseAll') }}</el-button>
              </template>
              <el-button size="small" :aria-label="t('common.refresh')" @click="loadPositions">{{ t('common.refresh') }}</el-button>
            </div>
            <el-alert v-if="!hasClose" type="warning" :title="t('instx.strategyNoClose')" :closable="false" show-icon class="mb" />
            <ErrorState v-if="posError" :error="posError" @retry="loadPositions" />
            <div v-else class="table-wrap">
              <el-table v-loading="posLoading" :data="positions" row-key="id" stripe class="resp-table" @selection-change="(v: Position[]) => (posSelection = v)">
                <el-table-column v-if="isOwner" type="selection" width="42" />
                <el-table-column :label="t('instance.label')" min-width="100"><template #default="{ row }">{{ row.label || row.symbol }}</template></el-table-column>
                <el-table-column :label="t('instance.side')" width="70"><template #default="{ row }">{{ fmtSide(row.side) }}</template></el-table-column>
                <el-table-column :label="t('instance.amount')" min-width="110" align="right"><template #default="{ row }">{{ fmtNum(row.amount, 8) }}</template></el-table-column>
                <el-table-column :label="t('instance.entryPrice')" min-width="110" align="right"><template #default="{ row }">{{ fmtNum(row.entry_price, 8) }}</template></el-table-column>
                <el-table-column :label="t('instance.markPrice')" min-width="110" align="right"><template #default="{ row }">{{ fmtNum(row.mark_price, 8) }}</template></el-table-column>
                <template v-if="inst.market_type === 'futures'">
                  <el-table-column :label="t('mkt.lev')" width="64" align="right"><template #default="{ row }">{{ row.leverage ? `${row.leverage}x` : '-' }}</template></el-table-column>
                  <el-table-column :label="t('mkt.margin')" min-width="100" align="right"><template #default="{ row }">{{ row.margin == null ? '-' : fmtMoney(row.margin) }}</template></el-table-column>
                  <el-table-column :label="t('mkt.liq')" min-width="110" align="right"><template #default="{ row }"><span class="down">{{ row.liquidation_price == null ? '-' : fmtNum(row.liquidation_price, 8) }}</span></template></el-table-column>
                </template>
                <el-table-column :label="t('instance.pnl')" min-width="100" align="right"><template #default="{ row }"><span :class="pnlClass(row.unrealized_pnl)">{{ fmtMoney(row.unrealized_pnl) }}</span></template></el-table-column>
                <el-table-column :label="t('instance.pnlRate')" min-width="90" align="right"><template #default="{ row }"><span :class="pnlClass(pnlRate(row))">{{ fmtPct(pnlRate(row)) }}</span></template></el-table-column>
                <el-table-column v-if="isOwner" :label="t('common.actions')" width="110" fixed="right">
                  <template #default="{ row }"><el-button link type="danger" @click="openClose(row)">{{ hasClose ? t('instance.close') : t('instance.forceClose') }}</el-button></template>
                </el-table-column>
                <template #empty><el-empty :description="t('common.empty')" :image-size="60" /></template>
              </el-table>
            </div>
          </el-tab-pane>
          <!-- 订单 -->
          <el-tab-pane :label="t('instance.tabOrders')" name="orders">
            <FilterBar :loading="ord.loading.value" @search="ord.search" @reset="resetOrders">
              <el-select v-model="ord.filters.status" :placeholder="t('instx.allStatus')" :aria-label="t('order.status')" clearable>
                <el-option v-for="s in ORDER_STATUSES" :key="s" :value="s" :label="t(`status.order.${s}`)" />
              </el-select>
              <el-select v-model="ord.filters.side" :placeholder="t('instx.allSides')" :aria-label="t('order.side')" clearable>
                <el-option value="buy" :label="t('side.buy')" />
                <el-option value="sell" :label="t('side.sell')" />
              </el-select>
              <el-date-picker v-model="orderRange" type="datetimerange" value-format="YYYY-MM-DDTHH:mm:ss" :aria-label="t('instx.range')" :start-placeholder="t('instx.range')" :end-placeholder="t('instx.range')" />
              <template #extra>
                <el-button v-if="isOwner" type="danger" plain @click="cancelAllOrders">{{ t('instance.cancelAll') }}</el-button>
              </template>
            </FilterBar>
            <ErrorState v-if="ord.error.value && !ord.items.value.length" :error="ord.error.value" @retry="ord.load" />
            <template v-else>
              <div class="table-wrap"><OrderTable :orders="ord.items.value" :loading="ord.loading.value" :show-instance="false" :cancellable="isOwner" @cancel="cancelOrder" /></div>
              <Pager v-model:page="ord.page.value" v-model:size="ord.pageSize.value" :total="ord.total.value" />
            </template>
          </el-tab-pane>

          <!-- 日志 -->
          <el-tab-pane :label="t('instance.tabLogs')" name="logs">
            <FilterBar :loading="logLoading" @search="loadLogs" @reset="resetLogs">
              <el-select v-model="logLevel" :placeholder="t('instx.allLevels')" :aria-label="t('instance.level')" clearable>
                <el-option v-for="l in LOG_LEVELS" :key="l" :value="l" :label="l" />
              </el-select>
              <el-date-picker v-model="logRange" type="datetimerange" value-format="YYYY-MM-DDTHH:mm:ss" :aria-label="t('instx.range')" :start-placeholder="t('instx.range')" :end-placeholder="t('instx.range')" />
              <template #extra>
                <el-switch v-model="autoScroll" :active-text="t('instance.autoScroll')" />
              </template>
            </FilterBar>
            <ErrorState v-if="logError" :error="logError" @retry="loadLogs" />
            <div v-else v-loading="logLoading"><LogList :logs="logs" :auto-scroll="autoScroll" height="520px" /></div>
          </el-tab-pane>

          <!-- 参数 -->
          <el-tab-pane :label="t('instance.tabParams')" name="params">
            <template v-if="!paramsEditing">
              <div class="toolbar">
                <span class="spacer" />
                <el-button v-if="isOwner" type="primary" :disabled="!stopped" :title="stopped ? '' : t('instance.needStopped')" @click="startEditParams">{{ t('instance.editParams') }}</el-button>
              </div>
              <el-alert v-if="isOwner && !stopped" :title="t('instance.needStopped')" type="info" :closable="false" show-icon class="mb" />
              <ParamsView :params="inst.params" :schema="schema" />
            </template>
            <template v-else>
              <SchemaForm v-if="schema.length" ref="paramsForm" v-model="paramsModel" :schema="schema" :symbols="strategy?.runtime?.symbols ?? []" :external-errors="paramsErrs" error-prefix="params." />
              <el-empty v-else :description="t('instx.noParams')" :image-size="48" />
              <div class="actions">
                <el-button @click="paramsEditing = false">{{ t('common.cancel') }}</el-button>
                <el-button type="primary" :loading="paramsSaving" :disabled="!schema.length" @click="saveParams">{{ t('common.save') }}</el-button>
              </div>
            </template>
          </el-tab-pane>

          <!-- 审计 -->
          <el-tab-pane :label="t('instance.tabAudit')" name="audit" lazy>
            <AuditTable :fetcher="auditFetcher" :show-filters="false" />
          </el-tab-pane>

          <!-- 分享 -->
          <el-tab-pane v-if="isOwner" :label="t('instance.tabShare')" name="share">
            <div class="toolbar">
              <span class="spacer" />
              <el-button type="primary" @click="openShare">{{ t('instance.shareCreate') }}</el-button>
            </div>
            <ErrorState v-if="shareError" :error="shareError" @retry="loadShares" />
            <div v-else class="table-wrap">
              <el-table v-loading="shareLoading" :data="shares" stripe class="resp-table">
                <el-table-column :label="t('instance.shareUrl')" min-width="240" show-overflow-tooltip>
                  <template #default="{ row }"><code class="url">{{ shareUrl(row) }}</code></template>
                </el-table-column>
                <el-table-column :label="t('instx.shareStatus')" width="90">
                  <template #default="{ row }"><el-tag size="small" :type="shareTone[shareStatus(row)]" disable-transitions>{{ t(shareStatusLabel[shareStatus(row)]) }}</el-tag></template>
                </el-table-column>
                <el-table-column :label="t('instance.shareShow')" min-width="160">
                  <template #default="{ row }">
                    <span class="caps"><el-tag v-for="k in SHOW_KEYS.filter((x) => row[x])" :key="k" size="small" effect="plain" disable-transitions>{{ t(showLabel[k]) }}</el-tag></span>
                  </template>
                </el-table-column>
                <el-table-column :label="t('instance.shareViews')" width="90" align="right"><template #default="{ row }">{{ row.access_count ?? row.view_count ?? 0 }}</template></el-table-column>
                <el-table-column :label="t('instance.shareLastView')" min-width="150"><template #default="{ row }">{{ fmtTime(row.last_access_at ?? row.last_viewed_at) }}</template></el-table-column>
                <el-table-column :label="t('instx.shareExpiresAt')" min-width="150"><template #default="{ row }">{{ row.expires_at ? fmtTime(row.expires_at) : t('instx.neverExpire') }}</template></el-table-column>
                <el-table-column :label="t('common.createdAt')" min-width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
                <el-table-column :label="t('common.actions')" width="140" fixed="right">
                  <template #default="{ row }">
                    <el-button link type="primary" :disabled="shareStatus(row) !== 'active'" @click="copyLink(row)">{{ t('instx.shareCopy') }}</el-button>
                    <el-button link type="danger" :disabled="shareStatus(row) === 'revoked'" @click="revokeShare(row)">{{ t('instance.shareRevoke') }}</el-button>
                  </template>
                </el-table-column>
                <template #empty><el-empty :description="t('common.empty')" :image-size="60" /></template>
              </el-table>
            </div>
          </el-tab-pane>
        </el-tabs>
      </template>
    </div>

    <!-- 单个平仓 -->
    <el-dialog v-model="closeOpen" :title="t('instx.closeOne', { label: closeTarget?.label || closeTarget?.symbol || '' })" width="420px" class="resp-dialog">
      <el-form label-width="100px" @submit.prevent>
        <el-form-item :label="t('instance.closeAmount')">
          <el-input-number v-model="closeAmount" :min="0" :max="Number(closeTarget?.amount ?? 0)" :step="Number(closeTarget?.amount ?? 1) / 10" :precision="8" :disabled="!caps.close_partial" controls-position="right" />
          <div class="hint">{{ caps.close_partial ? t('instance.closeAmountHint') : t('instx.partialUnsupported') }}</div>
        </el-form-item>
      </el-form>
      <el-alert v-if="!hasClose" type="warning" :title="t('instx.strategyNoClose')" :closable="false" show-icon />
      <template #footer>
        <el-button @click="closeOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :disabled="!closeAmount" @click="submitClose">{{ hasClose ? t('instance.close') : t('instance.forceClose') }}</el-button>
      </template>
    </el-dialog>

    <!-- 重置模拟盘 -->
    <el-dialog v-model="resetOpen" :title="t('instance.resetPaper')" width="420px" class="resp-dialog">
      <el-alert type="warning" :title="t('instance.resetPaperConfirm')" :closable="false" show-icon class="mb" />
      <el-checkbox v-model="resetForm.keep_orders">{{ t('instance.keepOrders') }}</el-checkbox>
      <el-checkbox v-model="resetForm.keep_snapshots">{{ t('instance.keepSnapshots') }}</el-checkbox>
      <template #footer>
        <el-button @click="resetOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="resetSaving" @click="submitReset">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>

    <!-- 换策略 -->
    <el-dialog v-model="csOpen" :title="t('instance.changeStrategy')" width="640px" class="resp-dialog" destroy-on-close>
      <el-alert :title="t('instx.changeHint')" type="info" :closable="false" show-icon class="mb" />
      <el-form label-width="120px" @submit.prevent>
        <el-form-item :label="t('instx.newStrategy')" required>
          <el-select v-model="csStrategyId" :placeholder="t('instx.selectStrategy')" filterable style="width: 100%" @change="pickStrategy">
            <el-option v-for="s in csList.filter((x) => x.id !== inst?.strategy_id)" :key="s.id" :value="s.id" :label="`${s.name} (v${s.version})`" />
          </el-select>
        </el-form-item>
      </el-form>
      <div v-loading="csLoading">
        <template v-if="csStrategy">
          <SchemaForm v-if="csStrategy.params_schema?.length" ref="csForm" v-model="csParams" :schema="csStrategy.params_schema" :symbols="csStrategy.runtime?.symbols ?? []" :external-errors="csErrs" error-prefix="params." />
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

    <!-- 生成分享 -->
    <el-dialog v-model="shareOpen" :title="t('instance.shareCreate')" width="460px" class="resp-dialog">
      <el-form label-width="100px" @submit.prevent>
        <el-form-item :label="t('instance.shareExpires')">
          <el-input-number v-model="shareForm.expires_in_days" :min="1" :max="365" :disabled="shareNever" controls-position="right" />
          <el-checkbox v-model="shareNever" class="ml">{{ t('instx.neverExpire') }}</el-checkbox>
        </el-form-item>
        <el-form-item :label="t('instance.shareShow')">
          <div class="checks">
            <el-checkbox v-for="k in SHOW_KEYS" :key="k" v-model="shareForm[k]">{{ t(showLabel[k]) }}</el-checkbox>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="shareOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="shareSaving" @click="submitShare">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.m-actions { display: grid; grid-template-columns: 1fr 1fr auto; gap: 8px; width: 100%; }
.m-actions > :deep(.el-button) { margin: 0; height: 44px; }
.m-actions :deep(.el-dropdown .el-button) { height: 44px; width: 48px; }
.m-figs { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 16px; margin-bottom: 12px; }
.m-eq { display: flex; flex-direction: column; gap: 4px; }
.m-eq span, .m-figs dt { font-size: 12px; color: var(--ink-3); }
.m-eq b { font-size: 30px; font-weight: 500; letter-spacing: -0.02em; }
.m-figs dl { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 14px 0 0; padding-top: 12px; border-top: 1px solid var(--line); }
.m-figs dd { margin: 4px 0 0; font-size: 16px; }
.meta { display: inline-flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.note { color: var(--el-text-color-secondary); font-size: 13px; margin: 0 0 8px; }
.mb { margin-bottom: 12px; }
.mt { margin-top: 12px; }
.ml { margin-left: 12px; }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.spacer { flex: 1; }
.table-wrap { overflow-x: auto; }
.hint { color: var(--el-text-color-secondary); font-size: 12px; width: 100%; }
.actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
.url { font-size: 12px; word-break: break-all; }
.caps { display: inline-flex; flex-wrap: wrap; gap: 4px; }
.checks { display: flex; flex-wrap: wrap; gap: 4px 12px; }
.big :deep(.value), .big :deep(.sc-value) { font-size: 28px; }
:deep(.resp-dialog) { max-width: 96vw; }
@media (max-width: 767px) {
  .desc-resp :deep(.el-descriptions__body .el-descriptions__table) { table-layout: auto; }
}
.run-info { border: 1px solid var(--line, #2a2f36); border-left: 3px solid var(--accent); border-radius: 6px; padding: 10px 14px; }
.run-info .ri-head { font-size: 12px; opacity: .7; margin-bottom: 4px; }
.run-info .ri-sum { margin: 0 0 4px; font-size: 14px; line-height: 1.5; word-break: break-all; }
.run-info .ri-meta { margin: 2px 0 0; font-size: 12px; opacity: .75; word-break: break-all; }
.cap-link { align-self: flex-start; margin-top: 2px; padding: 0; border: 0; background: none; color: var(--accent); font-size: 12px; cursor: pointer; }
</style>
