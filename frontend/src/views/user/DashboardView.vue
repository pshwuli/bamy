<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { instancesApi } from '@/api/instances'
import { ordersApi } from '@/api/orders'
import { accountsApi, type AccountHealth } from '@/api/accounts'
import { bindingsApi } from '@/api/extensions'
import type { Account, Binding, Instance, Order } from '@/api/types'
import { useLive } from '@/composables/useLive'
import { useNotifications } from '@/stores/notifications'
import { fmtMoney, fmtPct, fmtTime, pnlClass } from '@/utils/format'
import StatCard from '@/components/StatCard.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import PageHeader from '@/components/PageHeader.vue'

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
    const withBase = paper.value.items.filter((i) => i.equity != null && i.initial_balance != null)
    todayPnl.value = withBase.length ? withBase.reduce((s, i) => s + (Number(i.equity) - Number(i.initial_balance)), 0) : null
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
</script>

<template>
  <div>
    <PageHeader :title="t('nav.dashboard')" />
    <div class="grid-cards">
      <StatCard :label="t('dash.accounts')" :value="accountTotal" to="/accounts" :loading="loading" />
      <StatCard :label="t('dash.instances')" :value="`${runningTotal} / ${instTotal}`" :sub="t('dash.running')" to="/instances" :loading="loading" />
      <StatCard :label="t('dash.paper')" :value="paperTotal" to="/paper" :loading="loading" />
      <StatCard :label="t('dash.todayOrders')" :value="todayOrders" to="/orders" :loading="loading" />
      <StatCard
        :label="t('dash.todayPnl')"
        :value="todayPnl === null ? '-' : fmtMoney(todayPnl)"
        :tone="todayPnl === null ? '' : todayPnl >= 0 ? 'up' : 'down'"
        to="/paper"
        :loading="loading"
      />
      <StatCard :label="t('dash.alerts')" :value="alerts.length" :tone="alerts.length ? 'warn' : ''" :loading="loading" />
    </div>

    <div class="grid-2">
      <section class="card">
        <h2 class="card-title">{{ t('dash.instanceStatus') }}<router-link to="/instances">{{ t('dash.viewAll') }}</router-link></h2>
        <el-skeleton v-if="loading" :rows="4" animated />
        <el-table v-else :data="instances" size="small" @row-click="(r: Instance) => router.push(`/instances/${r.id}`)" class="clickable">
          <el-table-column prop="name" :label="t('instance.name')" min-width="120" show-overflow-tooltip />
          <el-table-column prop="symbol" :label="t('instance.symbol')" min-width="90" />
          <el-table-column :label="t('common.status')" width="100"><template #default="{ row }"><StatusBadge kind="instance" :value="row.status" /></template></el-table-column>
          <el-table-column :label="t('instance.equity')" align="right" min-width="100"><template #default="{ row }">{{ fmtMoney(row.equity) }}</template></el-table-column>
          <template #empty>
            <el-empty :description="t('common.empty')" :image-size="48"><el-button type="primary" size="small" @click="router.push('/instances/new')">{{ t('instance.create') }}</el-button></el-empty>
          </template>
        </el-table>
      </section>
      <section class="card">
        <h2 class="card-title">{{ t('dash.recentOrders') }}<router-link to="/orders">{{ t('dash.viewAll') }}</router-link></h2>
        <el-skeleton v-if="loading" :rows="4" animated />
        <el-table v-else :data="orders" size="small">
          <el-table-column prop="symbol" :label="t('order.symbol')" min-width="90" />
          <el-table-column :label="t('order.side')" width="70"><template #default="{ row }"><span :class="row.side === 'buy' ? 'pnl-up' : 'pnl-down'">{{ t(`side.${row.side}`) }}</span></template></el-table-column>
          <el-table-column :label="t('order.status')" width="100"><template #default="{ row }"><StatusBadge kind="order" :value="row.status" /></template></el-table-column>
          <el-table-column :label="t('order.time')" min-width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        </el-table>
      </section>
    </div>

    <div class="grid-2">
      <section class="card">
        <h2 class="card-title">{{ t('dash.accountHealth') }}<router-link to="/accounts">{{ t('dash.viewAll') }}</router-link></h2>
        <el-table :data="accounts" size="small" @row-click="(r: Account) => router.push(`/accounts/${r.id}`)" class="clickable">
          <el-table-column prop="name" :label="t('account.name')" min-width="110" />
          <el-table-column :label="t('common.status')" width="90"><template #default="{ row }"><StatusBadge kind="account" :value="row.status" /></template></el-table-column>
          <el-table-column :label="t('dash.onlineRate')" align="right"><template #default="{ row }">{{ health[row.id] ? fmtPct(health[row.id]!.online_rate, 1, false) : '-' }}</template></el-table-column>
          <el-table-column :label="t('dash.latency')" align="right"><template #default="{ row }">{{ health[row.id]?.avg_latency_ms != null ? `${Math.round(health[row.id]!.avg_latency_ms!)} ms` : '-' }}</template></el-table-column>
        </el-table>
      </section>
      <section class="card">
        <h2 class="card-title">{{ t('dash.recentAlerts') }}</h2>
        <el-empty v-if="!alerts.length" :description="t('dash.noAlerts')" :image-size="48" />
        <ul v-else class="alert-list">
          <li v-for="a in alerts" :key="a.id">
            <router-link v-if="a.link" :to="a.link">{{ a.title }}</router-link><span v-else>{{ a.title }}</span>
            <span v-if="a.body" class="muted"> · {{ a.body }}</span>
            <div class="muted small">{{ fmtTime(a.ts) }}</div>
          </li>
        </ul>
        <template v-if="badBindings.length">
          <h3 class="sub-title">{{ t('dash.pluginIssues') }}</h3>
          <ul class="alert-list">
            <li v-for="b in badBindings" :key="b.id">
              <router-link to="/my-plugins">{{ b.plugin_name || b.plugin_id }}</router-link>
              <StatusBadge kind="binding" :value="b.status" :auto-disabled="b.auto_disabled" />
              <span class="muted small">{{ b.auto_disabled ? t('binding.autoDisabledHint') : t('binding.invalidHint') }}</span>
            </li>
          </ul>
        </template>
      </section>
    </div>
    <p v-if="todayPnl !== null" class="muted small" :class="pnlClass(todayPnl)" />
  </div>
</template>

<style scoped>
.clickable :deep(tr) { cursor: pointer; }
.alert-list { list-style: none; padding: 0; margin: 0; }
.alert-list li { padding: 6px 0; border-bottom: 1px solid var(--el-border-color-lighter); display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.small { font-size: 12px; }
.sub-title { font-size: 13px; margin: 16px 0 6px; }
</style>
