<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { instancesApi } from '@/api/instances'
import { ordersApi } from '@/api/orders'
import { accountsApi, type AccountHealth } from '@/api/accounts'
import { bindingsApi } from '@/api/extensions'
import type { Account, Binding, Instance, Order, Row } from '@/api/types'
import { useLive } from '@/composables/useLive'
import { useNotifications } from '@/stores/notifications'
import { fmtMoney, fmtPct, fmtTime, pnlClass, fmtSymbol } from '@/utils/format'
import StatusBadge from '@/components/StatusBadge.vue'

const { t } = useI18n()
const router = useRouter()
const notes = useNotifications()

const loading = ref(true)
const accountTotal = ref(0)
const instTotal = ref(0)
const runningTotal = ref(0)
const paperTotal = ref(0)
const todayOrders = ref(0)
const todayPnl = ref<number | null>(null)
const instances = shallowRef<Instance[]>([])
const orders = shallowRef<Order[]>([])
const accounts = shallowRef<Account[]>([])
const health = ref<Record<number, AccountHealth | null>>({})
const badBindings = shallowRef<Binding[]>([])

function startOfToday() {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d.toISOString()
}

async function load() {
  const today = startOfToday()
  const results = await Promise.allSettled([
    accountsApi.list({ page: 1, page_size: 5 }),
    instancesApi.list({ page: 1, page_size: 5 }),
    instancesApi.list({ page: 1, page_size: 1, status: 'running' }),
    instancesApi.list({ page: 1, page_size: 100, paper: true }),
    ordersApi.list({ page: 1, page_size: 5 }),
    ordersApi.list({ page: 1, page_size: 1, start_time: today }),
    bindingsApi.list({ page: 1, page_size: 100 }),
  ])
  const [acc, inst, run, paper, ord, ordToday, binds] = results
  if (acc.status === 'fulfilled') {
    accounts.value = acc.value.items
    accountTotal.value = acc.value.pagination?.total ?? acc.value.items.length
  }
  if (inst.status === 'fulfilled') {
    instances.value = inst.value.items
    instTotal.value = inst.value.pagination?.total ?? inst.value.items.length
  }
  if (run.status === 'fulfilled') runningTotal.value = run.value.pagination?.total ?? run.value.items.length
  if (paper.status === 'fulfilled') {
    paperTotal.value = paper.value.pagination?.total ?? paper.value.items.length
    // 后端返回 initial_equity（模拟盘初始资金）；旧字段名 initial_balance 兼容
    const base = (i: Row) => i.initial_equity ?? i.initial_balance ?? i.paper_balance
    const withBase = paper.value.items.filter((i) => i.equity != null && base(i) != null)
    todayPnl.value = withBase.length ? withBase.reduce((s, i) => s + (Number(i.equity) - Number(base(i))), 0) : null
    paperEquity.value = withBase.length ? withBase.reduce((s, i) => s + Number(i.equity), 0) : null
    paperBase.value = withBase.length ? withBase.reduce((s, i) => s + Number(base(i)), 0) : null
  }
  if (ord.status === 'fulfilled') orders.value = ord.value.items
  if (ordToday.status === 'fulfilled') todayOrders.value = ordToday.value.pagination?.total ?? ordToday.value.items.length
  if (binds.status === 'fulfilled') badBindings.value = binds.value.items.filter((b) => b.status !== 'enabled' && (b.auto_disabled || b.status === 'invalid'))
  loading.value = false
  void loadHealth()
}

async function loadHealth() {
  const out: Record<number, AccountHealth | null> = {}
  await Promise.all(
    accounts.value.map(async (a) => {
      try {
        out[a.id] = await accountsApi.health(a.id, true)
      } catch {
        out[a.id] = null
      }
    }),
  )
  health.value = out
}

const paperEquity = ref<number | null>(null)
const paperBase = ref<number | null>(null)
const pnlPct = computed(() => (todayPnl.value != null && paperBase.value ? (todayPnl.value / paperBase.value) * 100 : null))
const statusRank: Record<string, number> = { error: 0, running: 1, paused: 2, starting: 3, stopping: 4, created: 5, stopped: 6 }
const sortedInstances = computed(() => [...instances.value].sort((a, b) => (statusRank[a.status] ?? 9) - (statusRank[b.status] ?? 9)))

const alerts = computed(() => notes.items.filter((n) => n.category === 'alert' || n.category === 'system').slice(0, 5))

useLive({
  events: ['instance.status', 'instance.error', 'order.created', 'order.filled', 'order.failed', 'account.status'],
  onEvent: (m) => {
    const d = m.data as Partial<Instance> & { instance_id?: number }
    if (m.event === 'instance.status' && d.instance_id) {
      instances.value = instances.value.map((i) => (i.id === d.instance_id ? { ...i, status: (d.status as Instance['status']) ?? i.status } : i))
    } else void load()
  },
  poll: load,
})

onMounted(load)
// 净值没有实时推送：页面可见时每 30 秒刷新一次，钱和状态会跟着动
const refresher = setInterval(() => { if (!document.hidden) void load() }, 30000)
onBeforeUnmount(() => clearInterval(refresher))
</script>

<template>
  <div class="dash">
    <!-- 第一屏：账户全貌。手机上一眼看完：资金、盈亏、在跑几个、有没有告警 -->
    <section class="summary" :class="{ loading }">
      <div class="sum-main">
        <span class="sum-label">{{ t('dashx.paperEquity') }}</span>
        <span class="sum-value num">{{ paperEquity == null ? '—' : fmtMoney(paperEquity) }}<small>USDT</small></span>
        <span v-if="todayPnl != null" class="sum-pnl num" :class="pnlClass(todayPnl)">
          {{ todayPnl >= 0 ? '+' : '' }}{{ fmtMoney(todayPnl) }}
          <template v-if="pnlPct != null">（{{ pnlPct >= 0 ? '+' : '' }}{{ pnlPct.toFixed(2) }}%）</template>
          <span class="muted">{{ t('dashx.sinceStart') }}</span>
        </span>
        <span v-else class="muted sum-pnl">{{ t('dashx.noPaper') }}</span>
      </div>
      <dl class="sum-figs">
        <router-link to="/instances" class="fig"><dt>{{ t('dash.running') }}</dt><dd class="num"><b>{{ runningTotal }}</b><span>/{{ instTotal }}</span></dd></router-link>
        <router-link to="/orders" class="fig"><dt>{{ t('dash.todayOrders') }}</dt><dd class="num"><b>{{ todayOrders }}</b></dd></router-link>
        <router-link to="/accounts" class="fig"><dt>{{ t('dash.accounts') }}</dt><dd class="num"><b>{{ accountTotal }}</b></dd></router-link>
        <div class="fig" :class="{ warn: alerts.length }"><dt>{{ t('dash.alerts') }}</dt><dd class="num"><b>{{ alerts.length }}</b></dd></div>
      </dl>
    </section>

    <div class="cols">
      <section class="panel">
        <header class="panel-head">
          <h2>{{ t('dash.instanceStatus') }}</h2>
          <router-link to="/instances">{{ t('dash.viewAll') }}</router-link>
        </header>
        <el-skeleton v-if="loading" :rows="4" animated class="pad" />
        <div v-else-if="!instances.length" class="empty">
          <p>{{ t('dashx.emptyInstances') }}</p>
          <el-button type="primary" @click="router.push('/instances/new')">{{ t('instance.create') }}</el-button>
        </div>
        <ul v-else class="rows">
          <li v-for="i in sortedInstances" :key="i.id">
            <router-link :to="`/instances/${i.id}`" class="row">
              <span class="row-main">
                <span class="row-title">{{ i.name }}</span>
                <span class="row-sub">{{ fmtSymbol(i.symbol) }} · {{ i.strategy_name || i.strategy_id }}<template v-if="i.paper"> · {{ t('dashx.paper') }}</template></span>
              </span>
              <span class="row-side">
                <span class="num row-num">{{ fmtMoney(i.equity) }}</span>
                <StatusBadge kind="instance" :value="i.status" />
              </span>
            </router-link>
          </li>
        </ul>
      </section>

      <section class="panel">
        <header class="panel-head">
          <h2>{{ t('dash.recentOrders') }}</h2>
          <router-link to="/orders">{{ t('dash.viewAll') }}</router-link>
        </header>
        <el-skeleton v-if="loading" :rows="4" animated class="pad" />
        <p v-else-if="!orders.length" class="empty muted">{{ t('common.empty') }}</p>
        <ul v-else class="rows">
          <li v-for="o in orders" :key="o.id" class="row static">
            <span class="row-main">
              <span class="row-title"><span :class="o.side === 'buy' ? 'pnl-up' : 'pnl-down'">{{ t(`side.${o.side}`) }}</span> {{ fmtSymbol(o.symbol) }}</span>
              <span class="row-sub num">{{ fmtTime(o.created_at) }}</span>
            </span>
            <span class="row-side">
              <span class="num row-num">{{ o.filled_amount || o.amount }}<template v-if="o.filled_price"> @ {{ fmtMoney(o.filled_price) }}</template></span>
              <StatusBadge kind="order" :value="o.status" />
            </span>
          </li>
        </ul>
      </section>

      <section class="panel">
        <header class="panel-head"><h2>{{ t('dash.recentAlerts') }}</h2></header>
        <p v-if="!alerts.length && !badBindings.length" class="empty muted">{{ t('dash.noAlerts') }}</p>
        <ul v-else class="rows">
          <li v-for="a in alerts" :key="a.id" class="row static">
            <span class="row-main">
              <span class="row-title"><router-link v-if="a.link" :to="a.link">{{ a.title }}</router-link><template v-else>{{ a.title }}</template></span>
              <span v-if="a.body" class="row-sub">{{ a.body }}</span>
            </span>
            <span class="row-side num muted">{{ fmtTime(a.ts) }}</span>
          </li>
          <li v-for="b in badBindings" :key="`b${b.id}`" class="row static">
            <span class="row-main">
              <span class="row-title"><router-link to="/my-plugins">{{ b.plugin_name || b.plugin_id }}</router-link></span>
              <span class="row-sub">{{ b.auto_disabled ? t('binding.autoDisabledHint') : t('binding.invalidHint') }}</span>
            </span>
            <span class="row-side"><StatusBadge kind="binding" :value="b.status" :auto-disabled="b.auto_disabled" /></span>
          </li>
        </ul>
      </section>

      <section class="panel">
        <header class="panel-head">
          <h2>{{ t('dash.accountHealth') }}</h2>
          <router-link to="/accounts">{{ t('dash.viewAll') }}</router-link>
        </header>
        <div v-if="!accounts.length" class="empty">
          <p class="muted">{{ t('dashx.emptyAccounts') }}</p>
          <el-button @click="router.push('/accounts')">{{ t('account.add') }}</el-button>
        </div>
        <ul v-else class="rows">
          <li v-for="a in accounts" :key="a.id">
            <router-link :to="`/accounts/${a.id}`" class="row">
              <span class="row-main">
                <span class="row-title">{{ a.name }}</span>
                <span class="row-sub num">
                  {{ t('dash.onlineRate') }} {{ health[a.id] ? fmtPct(health[a.id]!.online_rate, 1, false) : '—' }}
                  · {{ health[a.id]?.avg_latency_ms != null ? `${Math.round(health[a.id]!.avg_latency_ms!)} ms` : '—' }}
                </span>
              </span>
              <span class="row-side"><StatusBadge kind="account" :value="a.status" /></span>
            </router-link>
          </li>
        </ul>
      </section>
    </div>
  </div>
</template>

<style scoped>
.summary { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 24px; align-items: end; padding: 22px 24px; margin-bottom: 14px;
  background: var(--surface); border: 1px solid var(--line); border-radius: 16px; }
.sum-main { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.sum-label { font-size: 13px; color: var(--ink-3); }
.sum-value { font-size: clamp(32px, 4vw, 48px); font-weight: 500; letter-spacing: -0.03em; line-height: 1.05; }
.sum-value small { font-size: 0.36em; color: var(--ink-3); margin-left: 8px; letter-spacing: 0; font-family: var(--font-ui); }
.sum-pnl { font-size: 15px; display: flex; gap: 8px; flex-wrap: wrap; align-items: baseline; }
.sum-pnl .muted { font-size: 12px; font-family: var(--font-ui); }
.sum-figs { display: grid; grid-template-columns: repeat(4, auto); gap: 6px; margin: 0; }
.fig { display: flex; flex-direction: column; gap: 4px; padding: 10px 16px; border-radius: 10px; background: var(--surface-2); color: inherit; text-decoration: none !important; min-width: 88px; }
a.fig:hover { background: color-mix(in srgb, var(--surface-2) 70%, var(--line)); }
.fig dt { font-size: 12px; color: var(--ink-3); }
.fig dd { margin: 0; font-size: 22px; }
.fig dd span { color: var(--ink-3); font-size: 15px; }
.fig.warn dd { color: var(--warn); }

.cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; min-width: 0; }
.panel-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 18px; border-bottom: 1px solid var(--line); }
.panel-head h2 { margin: 0; font-size: 15px; letter-spacing: -0.01em; }
.panel-head a { font-size: 13px; }
.pad { padding: 16px 18px; }
.empty { padding: 28px 18px; text-align: center; }
.empty p { margin: 0 0 14px; }
.rows { list-style: none; margin: 0; padding: 0; }
.rows > li + li { border-top: 1px solid var(--line); }
.row { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 18px; color: inherit; text-decoration: none !important; min-height: 60px; }
a.row:hover { background: var(--surface-2); }
a.row:active { background: var(--surface-2); }
.row-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.row-title { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row-sub { font-size: 12px; color: var(--ink-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row-side { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; flex-shrink: 0; font-size: 12px; }
.row-num { font-size: 14px; color: var(--ink); }

@media (max-width: 1023px) { .cols { grid-template-columns: 1fr; } }
@media (max-width: 767px) {
  .summary { grid-template-columns: 1fr; gap: 16px; padding: 18px 16px; border-radius: 14px; }
  .sum-figs { grid-template-columns: repeat(4, 1fr); }
  .fig { min-width: 0; padding: 10px; }
  .fig dd { font-size: 20px; }
  .row { padding: 12px 14px; }
  .panel-head { padding: 12px 14px; }
}
</style>
