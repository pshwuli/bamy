<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { adminApi, type HealthInfo, type Overview } from '@/api/admin'
import { adminExtApi, itemsOf } from '@/api/extensions'
import { P } from '@/api/paths'
import type { Binding, LogEntry, Row } from '@/api/types'
import { useLive } from '@/composables/useLive'
import { fmtDuration, fmtMoney, fmtNum, fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import StatCard from '@/components/StatCard.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ErrorState from '@/components/ErrorState.vue'

// 后端 overview 实际返回比 api/admin.ts 的 Overview 多/不同的字段，这里做宽松兼容
interface OverviewX extends Omit<Overview, 'system' | 'plugin_alerts'> {
  engine?: { running_instances?: number; live_bindings?: number; uptime_seconds?: number; ready?: boolean; subscriptions?: string[] }
  extensions?: { strategies?: number; plugins?: number; failed?: Record<string, string> | unknown[] }
  system?: Record<string, unknown>
  recent_circuit_breakers?: Row[]
  recent_reconcile_diffs?: Row[]
  active_users_top10?: { user_id: number; username: string | null; orders?: number }[]
  plugin_alerts?: { auto_disabled_bindings?: number; failed_plugins?: string[] } | { plugin_id: string; kind: string; message: string; at?: string }[]
}

const { t } = useI18n()
const router = useRouter()

const loading = ref(true)
const error = ref<unknown>(null)
const ov = ref<OverviewX | null>(null)
const health = ref<HealthInfo | null>(null)
const healthErr = ref(false)
const logs = shallowRef<LogEntry[]>([])
const badBindings = shallowRef<Binding[]>([])

async function loadHealth() {
  try {
    health.value = await adminApi.health()
    healthErr.value = false
  } catch {
    // /health 不健康时返回 503 且不是信封格式，直接读原始响应拿 checks
    try {
      const r = await fetch(P.health)
      health.value = (await r.json()) as HealthInfo
      healthErr.value = false
    } catch {
      health.value = null
      healthErr.value = true
    }
  }
}

async function load() {
  error.value = null
  const [o, , errLogs, warnLogs, binds, invalid] = await Promise.allSettled([
    adminApi.overview(),
    loadHealth(),
    adminApi.systemLogs({ page: 1, page_size: 10, level: 'error' }),
    adminApi.systemLogs({ page: 1, page_size: 10, level: 'warn' }),
    adminExtApi.allBindings({ page: 1, page_size: 20, status: 'disabled' }),
    adminExtApi.allBindings({ page: 1, page_size: 20, status: 'invalid' }),
  ])
  if (o.status === 'fulfilled') ov.value = o.value as unknown as OverviewX
  else if (!ov.value) error.value = o.reason
  const merged: LogEntry[] = []
  if (errLogs.status === 'fulfilled') merged.push(...itemsOf(errLogs.value))
  if (warnLogs.status === 'fulfilled') merged.push(...itemsOf(warnLogs.value))
  const ts = (l: LogEntry) => new Date(l.timestamp || l.created_at || 0).getTime()
  logs.value = merged.sort((a, b) => ts(b) - ts(a)).slice(0, 10)
  const bad: Binding[] = []
  if (binds.status === 'fulfilled') bad.push(...itemsOf(binds.value))
  if (invalid.status === 'fulfilled') bad.push(...itemsOf(invalid.value))
  badBindings.value = bad.filter((b) => b.status !== 'enabled')
  loading.value = false
}

const sys = computed(() => ov.value?.system ?? {})
const sysRows = computed(() => {
  const s = sys.value
  const rows: { label: string; value: string }[] = []
  if (s.cpu_percent != null) rows.push({ label: t('admin.cpu'), value: `${fmtNum(s.cpu_percent as number, 1)}%` })
  if (Array.isArray(s.load_avg)) rows.push({ label: t('adm1.loadAvg'), value: (s.load_avg as number[]).join(' / ') })
  if (s.memory_percent != null) rows.push({ label: t('admin.memory'), value: `${fmtNum(s.memory_percent as number, 1)}%` })
  if (s.memory_total_mb != null) {
    const total = Number(s.memory_total_mb)
    const avail = Number(s.memory_available_mb ?? 0)
    const pct = total ? ((total - avail) / total) * 100 : 0
    rows.push({ label: t('admin.memory'), value: `${fmtNum(total - avail, 0)} / ${fmtNum(total, 0)} MB (${fmtNum(pct, 1)}%)` })
  }
  if (s.disk_free_gb != null) rows.push({ label: t('adm1.diskFree'), value: `${fmtNum(s.disk_free_gb as number)} GB` })
  if (s.db_size_mb != null) rows.push({ label: t('admin.database'), value: `${fmtNum(s.db_size_mb as number)} MB` })
  const dbCheck = health.value?.checks?.database
  if (dbCheck) rows.push({ label: t('admin.database'), value: dbCheck })
  return rows
})

const checks = computed(() => Object.entries(health.value?.checks ?? {}).map(([k, v]) => ({ key: k, value: String(v), ok: String(v) === 'ok' })))
const engineInfo = computed(() => ov.value?.engine)
const extInfo = computed(() => ov.value?.extensions)
const extFailedCount = computed(() => {
  const f = extInfo.value?.failed
  if (!f) return 0
  return Array.isArray(f) ? f.length : Object.keys(f).length
})

const pluginAlertItems = computed(() => {
  const pa = ov.value?.plugin_alerts
  const out: { key: string; text: string; label: string; to?: string; tone: 'warning' | 'danger' }[] = []
  if (Array.isArray(pa)) {
    for (const a of pa) out.push({ key: `${a.plugin_id}-${a.kind}`, text: `${a.plugin_id}: ${a.message}`, label: a.kind, tone: 'warning' })
  } else if (pa) {
    for (const p of pa.failed_plugins ?? []) out.push({ key: `f-${p}`, text: `${t('adm1.failedPlugins')}: ${p}`, label: t('status.load.failed'), to: '/admin/plugins', tone: 'danger' })
  }
  for (const b of badBindings.value) {
    out.push({
      key: `b-${b.id}`,
      text: `#${b.id} ${b.plugin_name || b.plugin_id} · ${t(`scope.${b.scope}`)}${b.scope_name ? ` ${b.scope_name}` : ''}${b.owner_username ? ` · ${b.owner_username}` : ''}`,
      label: t(`status.binding.${b.status === 'invalid' ? 'invalid' : b.enabled ? 'auto_disabled' : 'disabled'}`),
      to: '/admin/plugins',
      tone: b.status === 'invalid' ? 'danger' : 'warning',
    })
  }
  return out
})
const autoDisabled = computed(() => {
  const pa = ov.value?.plugin_alerts
  return pa && !Array.isArray(pa) ? pa.auto_disabled_bindings ?? 0 : badBindings.value.length
})

const topUsers = computed(() => ov.value?.active_users_top10 ?? ov.value?.active_users_top ?? [])
const breakers = computed(() => ov.value?.recent_circuit_breakers ?? [])
const diffs = computed(() => ov.value?.recent_reconcile_diffs ?? [])
const levelLabel = (l: string) => t(`breaker.level${l.charAt(0).toUpperCase()}${l.slice(1)}`)

useLive({
  events: ['instance.status', 'instance.error', 'system.alert', 'reconcile.diff', 'reconcile.completed', 'account.status'],
  onEvent: () => void load(),
  poll: load,
})

onMounted(load)
</script>

<template>
  <div>
    <PageHeader :title="t('nav.adminDashboard')">
      <el-button :loading="loading" @click="load">{{ t('common.refresh') }}</el-button>
    </PageHeader>

    <ErrorState v-if="error && !ov" :error="error" @retry="load" />
    <template v-else>
      <div class="grid-cards">
        <StatCard :label="t('admin.totalUsers')" :value="ov?.total_users ?? '-'" to="/admin/users" :loading="loading" />
        <StatCard :label="t('admin.totalAccounts')" :value="ov?.total_accounts ?? '-'" to="/admin/accounts" :loading="loading" />
        <StatCard :label="t('admin.totalInstances')" :value="ov?.total_instances ?? '-'" to="/admin/instances" :loading="loading" />
        <StatCard :label="t('admin.runningInstances')" :value="ov?.running_instances ?? '-'" to="/admin/instances" :loading="loading" />
        <StatCard :label="t('admin.paperLive')" :value="`${ov?.paper_instances ?? '-'} / ${ov?.live_instances ?? '-'}`" :loading="loading" />
        <StatCard :label="t('admin.todayOrders')" :value="ov?.today_orders ?? '-'" :loading="loading" />
        <StatCard :label="t('admin.todayVolume')" :value="fmtMoney(ov?.today_volume)" :loading="loading" />
        <StatCard
          :label="t('admin.activeAlerts')"
          :value="ov?.active_alerts ?? '-'"
          :tone="ov?.active_alerts ? 'warn' : ''"
          to="/admin/circuit-breakers"
          :loading="loading"
        />
      </div>
      <div class="grid-2">
        <section class="card" :aria-busy="loading">
          <h2 class="card-title">
            {{ t('admin.engine') }}
            <el-tag v-if="health" :type="health.status === 'healthy' ? 'success' : 'danger'" size="small">{{ health.status }}</el-tag>
          </h2>
          <el-skeleton v-if="loading" :rows="4" animated />
          <template v-else>
            <p v-if="healthErr" class="danger-text">{{ t('adm1.healthUnreachable') }}</p>
            <el-table v-else :data="checks" size="small" class="resp-table">
              <el-table-column prop="key" :label="t('adm1.checkItem')" min-width="110" />
              <el-table-column :label="t('adm1.checkResult')" min-width="140">
                <template #default="{ row }">
                  <el-tag :type="row.ok ? 'success' : 'warning'" size="small">{{ row.value }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
            <el-descriptions :column="2" size="small" border class="mt">
              <el-descriptions-item v-if="health" :label="t('admin.version')">{{ health.version }}</el-descriptions-item>
              <el-descriptions-item :label="t('admin.uptime')">{{ fmtDuration(engineInfo?.uptime_seconds ?? health?.uptime) }}</el-descriptions-item>
              <el-descriptions-item :label="t('admin.runningInstances')">{{ engineInfo?.running_instances ?? '-' }}</el-descriptions-item>
              <el-descriptions-item :label="t('adm1.autoDisabledBindings')">{{ autoDisabled }}</el-descriptions-item>
              <el-descriptions-item :label="t('adm1.strategiesLoaded')">{{ extInfo?.strategies ?? '-' }}</el-descriptions-item>
              <el-descriptions-item :label="t('adm1.pluginsLoaded')">{{ extInfo?.plugins ?? '-' }}</el-descriptions-item>
              <el-descriptions-item :label="t('adm1.extFailed')">
                <span :class="{ 'danger-text': extFailedCount > 0 }">{{ extFailedCount }}</span>
              </el-descriptions-item>
            </el-descriptions>
          </template>
        </section>
        <section class="card" :aria-busy="loading">
          <h2 class="card-title">{{ t('admin.resources') }}</h2>
          <el-skeleton v-if="loading" :rows="4" animated />
          <el-descriptions v-else-if="sysRows.length" :column="1" size="small" border>
            <el-descriptions-item v-for="(r, i) in sysRows" :key="i" :label="r.label">{{ r.value }}</el-descriptions-item>
          </el-descriptions>
          <el-empty v-else :description="t('common.empty')" :image-size="48" />
        </section>
      </div>

      <div class="grid-2">
        <section class="card">
          <h2 class="card-title">{{ t('admin.recentBreakers') }}<router-link to="/admin/circuit-breakers">{{ t('dash.viewAll') }}</router-link></h2>
          <el-table :data="breakers" size="small" class="resp-table" v-loading="loading">
            <el-table-column :label="t('breaker.level')" width="80"><template #default="{ row }">{{ levelLabel(row.level) }}</template></el-table-column>
            <el-table-column :label="t('breaker.target')" min-width="100" show-overflow-tooltip>
              <template #default="{ row }">{{ row.target_name || row.target_id || '-' }}</template>
            </el-table-column>
            <el-table-column prop="reason" :label="t('breaker.reason')" min-width="140" show-overflow-tooltip />
            <el-table-column :label="t('common.status')" width="90"><template #default="{ row }"><StatusBadge kind="breaker" :value="row.status" /></template></el-table-column>
            <el-table-column :label="t('breaker.triggeredAt')" min-width="150"><template #default="{ row }">{{ fmtTime(row.triggered_at) }}</template></el-table-column>
            <template #empty><el-empty :description="t('common.empty')" :image-size="48" /></template>
          </el-table>
        </section>
        <section class="card">
          <h2 class="card-title">{{ t('admin.recentDiffs') }}<router-link to="/admin/reconcile">{{ t('dash.viewAll') }}</router-link></h2>
          <el-table :data="diffs" size="small" class="resp-table" v-loading="loading">
            <el-table-column :label="t('account.name')" min-width="100" show-overflow-tooltip>
              <template #default="{ row }">{{ row.account_name || (row.account_id ? `#${row.account_id}` : '-') }}</template>
            </el-table-column>
            <el-table-column :label="t('reconcile.type')" min-width="90"><template #default="{ row }">{{ row.diff_type || row.type }}</template></el-table-column>
            <el-table-column :label="t('reconcile.resolved')" width="90">
              <template #default="{ row }"><el-tag :type="row.resolved ? 'info' : 'warning'" size="small">{{ row.resolved ? t('common.yes') : t('common.no') }}</el-tag></template>
            </el-table-column>
            <el-table-column :label="t('common.time')" min-width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
            <template #empty><el-empty :description="t('common.empty')" :image-size="48" /></template>
          </el-table>
        </section>
      </div>
      <div class="grid-2">
        <section class="card">
          <h2 class="card-title">{{ t('admin.recentAlerts') }}<router-link to="/admin/logs">{{ t('dash.viewAll') }}</router-link></h2>
          <el-table :data="logs" size="small" class="resp-table" v-loading="loading">
            <el-table-column :label="t('syslog.level')" width="80">
              <template #default="{ row }"><el-tag :type="row.level === 'error' ? 'danger' : 'warning'" size="small">{{ row.level }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="module" :label="t('syslog.module')" width="100" show-overflow-tooltip />
            <el-table-column prop="message" :label="t('instance.message')" min-width="200" show-overflow-tooltip />
            <el-table-column :label="t('common.time')" min-width="150"><template #default="{ row }">{{ fmtTime(row.timestamp || row.created_at) }}</template></el-table-column>
            <template #empty><el-empty :description="t('dash.noAlerts')" :image-size="48" /></template>
          </el-table>
        </section>
        <section class="card">
          <h2 class="card-title">{{ t('admin.topUsers') }}<router-link to="/admin/users">{{ t('dash.viewAll') }}</router-link></h2>
          <el-table
            :data="topUsers"
            size="small"
            class="resp-table clickable"
            v-loading="loading"
            @row-click="(r: Row) => router.push(`/admin/users/${r.user_id}`)"
          >
            <el-table-column type="index" width="50" label="#" />
            <el-table-column :label="t('adm1.username')" min-width="120">
              <template #default="{ row }">
                <router-link :to="`/admin/users/${row.user_id}`" @click.stop>{{ row.username || `#${row.user_id}` }}</router-link>
              </template>
            </el-table-column>
            <el-table-column :label="t('adm1.orders7d')" align="right" min-width="90">
              <template #default="{ row }">{{ row.orders ?? row.score ?? '-' }}</template>
            </el-table-column>
            <template #empty><el-empty :description="t('common.empty')" :image-size="48" /></template>
          </el-table>
        </section>
      </div>

      <section class="card" :class="{ 'danger-card': pluginAlertItems.some((a) => a.tone === 'danger') }">
        <h2 class="card-title">{{ t('admin.pluginAlerts') }}<router-link to="/admin/plugins">{{ t('dash.viewAll') }}</router-link></h2>
        <el-skeleton v-if="loading" :rows="2" animated />
        <ul v-else-if="pluginAlertItems.length" class="alert-list">
          <li v-for="a in pluginAlertItems" :key="a.key">
            <el-tag :type="a.tone" size="small">{{ a.label }}</el-tag>
            <router-link v-if="a.to" :to="a.to">{{ a.text }}</router-link>
            <span v-else>{{ a.text }}</span>
          </li>
        </ul>
        <p v-else class="muted">{{ t('adm1.noPluginAlerts') }}</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.mt { margin-top: 12px; }
.alert-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.alert-list li { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.clickable :deep(tr) { cursor: pointer; }
</style>
