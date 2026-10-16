<script setup lang="ts">
import { computed, reactive, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { accountsApi, type AccountBalance, type AccountHealth } from '@/api/accounts'
import { instancesApi } from '@/api/instances'
import { userApi } from '@/api/user'
import { ApiError, fieldErrors } from '@/api/http'
import type { Account, Instance, Order, Position, Query } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { confirm } from '@/composables/useConfirm'
import { fmtMoney, fmtNum, fmtPct, fmtTime, pnlClass } from '@/utils/format'
import { req } from '@/utils/rules'
import PageHeader from '@/components/PageHeader.vue'
import Pager from '@/components/Pager.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import StatCard from '@/components/StatCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import OrderTable from '@/components/OrderTable.vue'
import AuditTable from '@/components/AuditTable.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const KEY_RE = /^[A-Za-z0-9]{16,128}$/

const id = computed(() => Number(route.params.id))
const tab = ref<string>(typeof route.query.tab === 'string' ? route.query.tab : 'overview')
watch(tab, (v) => router.replace({ query: { ...route.query, tab: v } }))

function statusValue(s: string | null | undefined) {
  return s === 'error' ? 'abnormal' : s ?? ''
}
function envLabel(env?: string) {
  return env === 'mainnet' || env === 'testnet' ? t(`env.${env}`) : env || '-'
}

// ---------- 账号 ----------
const acc = shallowRef<Account | null>(null)
const accLoading = ref(false)
const accError = ref<unknown>(null)
const notFound = computed(() => accError.value instanceof ApiError && (accError.value.status === 404 || accError.value.status === 403))

async function loadAccount() {
  accLoading.value = true
  accError.value = null
  try {
    acc.value = await accountsApi.get(id.value)
  } catch (e) {
    accError.value = e
  } finally {
    accLoading.value = false
  }
}

const ipList = computed(() => {
  const v = (acc.value as (Account & { ip_whitelist?: string[] }) | null)?.ip_whitelist
  return Array.isArray(v) && v.length ? v.join(', ') : '-'
})

// ---------- 验证 ----------
type VerifyResult = { ok?: boolean; status?: string; message?: string; reason?: string; code?: number; can_trade?: boolean; can_withdraw?: boolean }
const verifying = ref(false)
const verifyRes = shallowRef<VerifyResult | null>(null)
const verifyAt = ref<string | null>(null)

async function doVerify() {
  verifying.value = true
  try {
    verifyRes.value = (await accountsApi.verify(id.value)) as VerifyResult
    verifyAt.value = new Date().toISOString()
    if (verifyRes.value?.ok) ElMessage.success(t('account.verifyOk'))
    else ElMessage.warning(t('account.verifyFailed'))
    await loadAccount()
    if (healthLoaded.value) void loadHealth()
  } catch {
    /* 全局已提示 */
  } finally {
    verifying.value = false
  }
}

// ---------- 编辑 ----------
const nameRules = () => [req(), { min: 4, max: 32, message: t('account.nameRule'), trigger: ['blur', 'change'] }]
const keyRules = () => [req(), { pattern: KEY_RE, message: t('account.keyFormat'), trigger: ['blur', 'change'] }]

const editOpen = ref(false)
const editSaving = ref(false)
const editRef = ref<FormInstance>()
const editForm = reactive({ name: '', readonly: false, remark: '' })
const editErrs = ref<Record<string, string>>({})
const editRules = computed<FormRules>(() => ({ name: nameRules() }))

function openEdit() {
  if (!acc.value) return
  Object.assign(editForm, { name: acc.value.name, readonly: !!acc.value.readonly, remark: acc.value.remark ?? '' })
  editErrs.value = {}
  editOpen.value = true
}

async function submitEdit() {
  if (!(await editRef.value?.validate().catch(() => false))) return
  editSaving.value = true
  editErrs.value = {}
  try {
    const r = await accountsApi.update(id.value, { ...editForm })
    if (r && acc.value) acc.value = { ...acc.value, ...r, instance_count: acc.value.instance_count }
    ElMessage.success(t('common.success'))
    editOpen.value = false
  } catch (e) {
    editErrs.value = fieldErrors(e)
  } finally {
    editSaving.value = false
  }
}

// ---------- 更新密钥 ----------
const credOpen = ref(false)
const credSaving = ref(false)
const credRef = ref<FormInstance>()
const credForm = reactive({ api_key: '', api_secret: '' })
const credRules = computed<FormRules>(() => ({ api_key: keyRules(), api_secret: keyRules() }))

function openCred() {
  Object.assign(credForm, { api_key: '', api_secret: '' })
  credOpen.value = true
}

async function submitCred() {
  if (!(await credRef.value?.validate().catch(() => false))) return
  credSaving.value = true
  try {
    await accountsApi.credentials(id.value, { ...credForm })
    ElMessage.success(t('common.success'))
    credOpen.value = false
    await loadAccount()
  } catch {
    /* 全局已提示 */
  } finally {
    credSaving.value = false
  }
}

// ---------- 删除 ----------
async function doDelete() {
  if (!acc.value) return
  if (!(await confirm(t('account.deleteConfirm', { name: acc.value.name }), t('common.delete'), true))) return
  try {
    await accountsApi.remove(id.value)
    ElMessage.success(t('account.deleted'))
    router.replace('/accounts')
  } catch {
    /* 全局已提示 */
  }
}
// ---------- 余额 ----------
const bal = shallowRef<AccountBalance | null>(null)
const balLoading = ref(false)
const balError = ref<unknown>(null)
const balLoaded = ref(false)
type AssetRow = { asset: string; free: number | null; locked: number | null; total: number | null }
const balRows = computed<AssetRow[]>(() => {
  const d = bal.value as (AccountBalance & { details?: { asset: string; free?: number; used?: number; total?: number }[] }) | null
  if (!d) return []
  if (Array.isArray(d.details) && d.details.length) {
    return d.details.map((b) => ({ asset: b.asset, free: b.free ?? null, locked: b.used ?? null, total: b.total ?? null }))
  }
  return Object.entries(d.assets ?? {}).map(([asset, v]) =>
    typeof v === 'number' ? { asset, free: null, locked: null, total: v } : { asset, free: v.free ?? null, locked: v.locked ?? null, total: v.total ?? null },
  )
})

async function loadBalance() {
  balLoading.value = true
  balError.value = null
  try {
    bal.value = await accountsApi.balance(id.value)
  } catch (e) {
    balError.value = e
  } finally {
    balLoading.value = false
    balLoaded.value = true
  }
}

// ---------- 持仓 ----------
type PosRow = Position & { instance_id?: number; instance_name?: string; unrealized_pnl_pct?: number }
const positions = shallowRef<PosRow[]>([])
const posLoading = ref(false)
const posError = ref<unknown>(null)
const posLoaded = ref(false)

async function loadPositions() {
  posLoading.value = true
  posError.value = null
  try {
    const d = (await accountsApi.positions(id.value)) as unknown
    if (Array.isArray(d)) positions.value = d as PosRow[]
    else {
      const o = (d ?? {}) as { items?: PosRow[]; positions?: PosRow[] }
      positions.value = o.items ?? o.positions ?? []
    }
  } catch (e) {
    posError.value = e
  } finally {
    posLoading.value = false
    posLoaded.value = true
  }
}

function posRate(p: PosRow): number | null {
  if (p.pnl_rate != null) return p.pnl_rate
  if (p.unrealized_pnl_pct != null) return p.unrealized_pnl_pct / 100
  return null
}

// ---------- 实例 ----------
const inst = useTable<Instance>((q) => instancesApi.list({ ...q, account_id: id.value }), {}, { immediate: false })
const instLoaded = ref(false)

// ---------- 订单 ----------
const ord = useTable<Order>((q) => accountsApi.orders(id.value, q), { status: '' }, { immediate: false })
const ordLoaded = ref(false)
const orderStatuses = ['pending', 'submitted', 'partial', 'filled', 'canceled', 'rejected', 'failed', 'unknown']

// ---------- 健康 ----------
type HealthData = AccountHealth & { status?: string; recent_checks: (AccountHealth['recent_checks'][number] & { error_code?: string | null; error_message?: string | null })[] }
const health = shallowRef<HealthData | null>(null)
const healthLoading = ref(false)
const healthError = ref<unknown>(null)
const healthLoaded = ref(false)

async function loadHealth() {
  healthLoading.value = true
  healthError.value = null
  try {
    health.value = (await accountsApi.health(id.value)) as HealthData
  } catch (e) {
    healthError.value = e
  } finally {
    healthLoading.value = false
    healthLoaded.value = true
  }
}

const checks = computed(() =>
  (health.value?.recent_checks ?? []).map((c) => ({
    at: c.checked_at ?? c.at ?? null,
    ok: c.success ?? c.ok ?? false,
    latency: c.latency_ms ?? null,
    error: c.error_message ?? c.error ?? (c.error_code ? String(c.error_code) : ''),
  })),
)

// ---------- 审计 ----------
const auditFixed = computed<Query>(() => ({ target_type: 'account', target_id: id.value }))
const auditFetcher = (q: Query) => userApi.auditLogs(q)

// ---------- Tab 懒加载 ----------
function ensureTab(name: string) {
  if (name === 'balance' && !balLoaded.value) void loadBalance()
  else if (name === 'positions' && !posLoaded.value) void loadPositions()
  else if (name === 'instances' && !instLoaded.value) {
    instLoaded.value = true
    void inst.load()
  } else if (name === 'orders' && !ordLoaded.value) {
    ordLoaded.value = true
    void ord.load()
  } else if (name === 'health' && !healthLoaded.value) void loadHealth()
}
watch(tab, ensureTab)

watch(
  id,
  () => {
    acc.value = null
    balLoaded.value = posLoaded.value = instLoaded.value = ordLoaded.value = healthLoaded.value = false
    verifyRes.value = null
    void loadAccount()
    ensureTab(tab.value)
  },
  { immediate: true },
)

</script>

<template>
  <div class="account-detail">
    <ErrorState v-if="accError && !acc && !notFound" :error="accError" @retry="loadAccount" />
    <EmptyState v-else-if="notFound" :title="t('account.notFound')" :action-text="t('common.back')" @action="router.push('/accounts')" />
    <template v-else>
      <PageHeader :title="acc?.name ?? '...'" back>
        <template #sub>
          <template v-if="acc">
            <StatusBadge kind="account" :value="statusValue(acc.status)" />
            <el-tag size="small" effect="plain" :type="acc.env === 'testnet' ? 'warning' : 'info'">{{ envLabel(acc.env) }}</el-tag>
            <el-tag v-if="acc.readonly" size="small" type="info">{{ t('common.readonly') }}</el-tag>
            <code class="mono">{{ acc.api_key_masked }}</code>
          </template>
        </template>
        <el-button :disabled="!acc" @click="openEdit">{{ t('common.edit') }}</el-button>
        <el-button :disabled="!acc" :loading="verifying" @click="doVerify">{{ t('account.verify') }}</el-button>
        <el-button :disabled="!acc" @click="openCred">{{ t('account.credentials') }}</el-button>
        <el-button type="danger" plain :disabled="!acc" @click="doDelete">{{ t('common.delete') }}</el-button>
      </PageHeader>

      <el-tabs v-model="tab" class="detail-tabs">
        <!-- 概览 -->
        <el-tab-pane :label="t('account.tabOverview')" name="overview">
          <div v-loading="accLoading && !acc">
            <el-descriptions v-if="acc" :title="t('account.info')" :column="2" border class="resp-desc">
              <el-descriptions-item :label="t('account.name')">{{ acc.name }}</el-descriptions-item>
              <el-descriptions-item :label="t('account.status')"><StatusBadge kind="account" :value="statusValue(acc.status)" /></el-descriptions-item>
              <el-descriptions-item :label="t('account.apiKey')"><code class="mono">{{ acc.api_key_masked || '-' }}</code></el-descriptions-item>
              <el-descriptions-item :label="t('account.exchange')">{{ acc.exchange || 'binance' }}</el-descriptions-item>
              <el-descriptions-item :label="t('account.env')">{{ envLabel(acc.env) }}</el-descriptions-item>
              <el-descriptions-item :label="t('account.market')">{{ acc.market_type === 'spot' ? t('market.spot') : acc.market_type }}</el-descriptions-item>
              <el-descriptions-item :label="t('account.readonly')">{{ acc.readonly ? t('common.yes') : t('common.no') }}</el-descriptions-item>
              <el-descriptions-item :label="t('account.instanceCount')">{{ acc.instance_count ?? 0 }}</el-descriptions-item>
              <el-descriptions-item :label="t('account.lastActive')">{{ fmtTime(acc.last_active_at) }}</el-descriptions-item>
              <el-descriptions-item :label="t('account.lastVerified')">{{ fmtTime(acc.last_verified_at) }}</el-descriptions-item>
              <el-descriptions-item :label="t('common.createdAt')">{{ fmtTime(acc.created_at) }}</el-descriptions-item>
              <el-descriptions-item :label="t('account.ipWhitelist')">{{ ipList }}</el-descriptions-item>
              <el-descriptions-item :label="t('account.remark')" :span="2">{{ acc.remark || '-' }}</el-descriptions-item>
            </el-descriptions>
            <el-card v-if="verifyRes" shadow="never" class="verify-card">
              <template #header>{{ t('account.verifyResultTitle') }} · {{ fmtTime(verifyAt) }}</template>
              <el-alert
                :title="verifyRes.ok ? t('account.verifyOk') : `${t('account.verifyFailed')}${verifyRes.reason || verifyRes.message ? '：' + (verifyRes.reason || verifyRes.message) : ''}`"
                :type="verifyRes.ok ? 'success' : 'error'"
                :closable="false"
                show-icon
              />
              <div v-if="verifyRes.can_trade !== undefined" class="perm-row">
                <span>{{ t('account.canTrade') }}：{{ verifyRes.can_trade ? t('common.yes') : t('common.no') }}</span>
                <span>{{ t('account.canWithdraw') }}：{{ verifyRes.can_withdraw ? t('common.yes') : t('common.no') }}</span>
              </div>
              <el-alert v-if="verifyRes.can_withdraw" :title="t('account.withdrawWarn')" type="warning" :closable="false" show-icon />
            </el-card>
          </div>
        </el-tab-pane>

        <!-- 余额 -->
        <el-tab-pane :label="t('account.tabBalance')" name="balance" lazy>
          <ErrorState v-if="balError" :error="balError" @retry="loadBalance" />
          <div v-else v-loading="balLoading">
            <div class="tab-toolbar">
              <StatCard :label="t('account.totalUsdt')" :value="fmtMoney(bal?.total_usdt)" :loading="balLoading && !bal" class="total-card" />
              <el-button :loading="balLoading" @click="loadBalance">{{ t('common.refresh') }}</el-button>
            </div>
            <div class="table-wrap">
              <el-table :data="balRows" class="resp-table" size="small">
                <el-table-column :label="t('account.asset')" prop="asset" min-width="100" />
                <el-table-column :label="t('account.free')" align="right" min-width="130">
                  <template #default="{ row }">{{ fmtNum(row.free, 8) }}</template>
                </el-table-column>
                <el-table-column :label="t('account.locked')" align="right" min-width="130">
                  <template #default="{ row }">{{ fmtNum(row.locked, 8) }}</template>
                </el-table-column>
                <el-table-column :label="t('account.amount')" align="right" min-width="130">
                  <template #default="{ row }">{{ fmtNum(row.total, 8) }}</template>
                </el-table-column>
                <template #empty><EmptyState v-if="balLoaded && !balLoading" :title="t('account.noBalance')" /><span v-else /></template>
              </el-table>
            </div>
          </div>
        </el-tab-pane>

        <!-- 持仓 -->
        <el-tab-pane :label="t('account.tabPositions')" name="positions" lazy>
          <ErrorState v-if="posError" :error="posError" @retry="loadPositions" />
          <div v-else v-loading="posLoading">
            <div class="tab-toolbar end">
              <el-button :loading="posLoading" @click="loadPositions">{{ t('common.refresh') }}</el-button>
            </div>
            <div class="table-wrap">
              <el-table :data="positions" class="resp-table" size="small">
                <el-table-column :label="t('account.instanceName')" min-width="130">
                  <template #default="{ row }">
                    <router-link v-if="row.instance_id" :to="`/instances/${row.instance_id}`" class="link">{{ row.instance_name || row.instance_id }}</router-link>
                    <span v-else>-</span>
                  </template>
                </el-table-column>
                <el-table-column :label="t('instance.symbol')" prop="symbol" min-width="110" />
                <el-table-column :label="t('instance.side')" prop="side" width="80" />
                <el-table-column :label="t('instance.amount')" align="right" min-width="110">
                  <template #default="{ row }">{{ fmtNum(row.amount, 8) }}</template>
                </el-table-column>
                <el-table-column :label="t('instance.entryPrice')" align="right" min-width="110">
                  <template #default="{ row }">{{ fmtNum(row.entry_price, 8) }}</template>
                </el-table-column>
                <el-table-column :label="t('instance.markPrice')" align="right" min-width="110">
                  <template #default="{ row }">{{ fmtNum(row.mark_price, 8) }}</template>
                </el-table-column>
                <el-table-column :label="t('instance.pnl')" align="right" min-width="110">
                  <template #default="{ row }"><span :class="pnlClass(row.unrealized_pnl)">{{ fmtMoney(row.unrealized_pnl) }}</span></template>
                </el-table-column>
                <el-table-column :label="t('instance.pnlRate')" align="right" min-width="100">
                  <template #default="{ row }"><span :class="pnlClass(posRate(row as PosRow))">{{ fmtPct(posRate(row as PosRow)) }}</span></template>
                </el-table-column>
                <template #empty><EmptyState v-if="posLoaded && !posLoading" :title="t('account.noPositions')" /><span v-else /></template>
              </el-table>
            </div>
          </div>
        </el-tab-pane>

        <!-- 实例 -->
        <el-tab-pane :label="t('account.tabInstances')" name="instances" lazy>
          <ErrorState v-if="inst.error.value && !inst.items.value.length" :error="inst.error.value" @retry="inst.load" />
          <template v-else>
            <div v-loading="inst.loading.value" class="table-wrap">
              <el-table :data="inst.items.value" class="resp-table" size="small">
                <el-table-column :label="t('instance.name')" min-width="140">
                  <template #default="{ row }"><router-link :to="`/instances/${row.id}`" class="link">{{ row.name }}</router-link></template>
                </el-table-column>
                <el-table-column :label="t('instance.strategy')" min-width="130">
                  <template #default="{ row }">{{ row.strategy_name || row.strategy_id }}</template>
                </el-table-column>
                <el-table-column :label="t('instance.symbol')" prop="symbol" min-width="110" />
                <el-table-column :label="t('instance.mode')" width="90">
                  <template #default="{ row }">{{ row.paper ? t('common.paper') : t('common.live') }}</template>
                </el-table-column>
                <el-table-column :label="t('common.status')" width="100">
                  <template #default="{ row }"><StatusBadge kind="instance" :value="row.status" /></template>
                </el-table-column>
                <el-table-column :label="t('instance.equity')" align="right" min-width="110">
                  <template #default="{ row }">{{ fmtMoney(row.equity) }}</template>
                </el-table-column>
                <el-table-column :label="t('common.createdAt')" min-width="160">
                  <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
                </el-table-column>
                <template #empty>
                  <EmptyState v-if="inst.loadedOnce.value && !inst.loading.value" :title="t('account.noInstances')" /><span v-else />
                </template>
              </el-table>
            </div>
            <Pager v-if="inst.total.value > 0" v-model:page="inst.page.value" v-model:size="inst.pageSize.value" :total="inst.total.value" />
          </template>
        </el-tab-pane>

        <!-- 订单 -->
        <el-tab-pane :label="t('account.tabOrders')" name="orders" lazy>
          <div class="tab-toolbar">
            <el-select
              v-model="ord.filters.status"
              :placeholder="t('order.status')"
              :aria-label="t('order.status')"
              clearable
              class="status-select"
              @change="ord.search"
            >
              <el-option v-for="s in orderStatuses" :key="s" :value="s" :label="t(`status.order.${s}`)" />
            </el-select>
            <el-button :loading="ord.loading.value" @click="ord.load">{{ t('common.refresh') }}</el-button>
          </div>
          <ErrorState v-if="ord.error.value && !ord.items.value.length" :error="ord.error.value" @retry="ord.load" />
          <template v-else>
            <OrderTable :orders="ord.items.value" :loading="ord.loading.value" :cancellable="false" />
            <Pager v-if="ord.total.value > 0" v-model:page="ord.page.value" v-model:size="ord.pageSize.value" :total="ord.total.value" />
          </template>
        </el-tab-pane>

        <!-- 健康 -->
        <el-tab-pane :label="t('account.tabHealth')" name="health" lazy>
          <ErrorState v-if="healthError" :error="healthError" @retry="loadHealth" />
          <div v-else v-loading="healthLoading">
            <div class="stat-grid">
              <StatCard
                :label="t('account.onlineRate')"
                :value="health?.online_rate != null ? fmtPct(health.online_rate, 2, false) : '-'"
                :tone="health?.online_rate != null && health.online_rate < 0.95 ? 'warn' : ''"
                :loading="healthLoading && !health"
              />
              <StatCard
                :label="t('account.avgLatency')"
                :value="health?.avg_latency_ms != null ? `${health.avg_latency_ms} ms` : '-'"
                :loading="healthLoading && !health"
              />
              <StatCard :label="t('account.lastCheck')" :value="fmtTime(health?.last_check_at)" :loading="healthLoading && !health" />
            </div>
            <div class="tab-toolbar end">
              <el-button :loading="healthLoading" @click="loadHealth">{{ t('common.refresh') }}</el-button>
            </div>
            <div class="table-wrap">
              <el-table :data="checks" class="resp-table" size="small" max-height="480">
                <el-table-column :label="t('account.checkAt')" min-width="170">
                  <template #default="{ row }">{{ fmtTime(row.at) }}</template>
                </el-table-column>
                <el-table-column :label="t('account.checkResult')" width="90">
                  <template #default="{ row }">
                    <el-tag size="small" :type="row.ok ? 'success' : 'danger'">{{ row.ok ? t('account.checkOk') : t('account.checkFail') }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column :label="t('account.latency')" align="right" width="100">
                  <template #default="{ row }">{{ row.latency != null ? `${row.latency} ms` : '-' }}</template>
                </el-table-column>
                <el-table-column :label="t('account.error')" min-width="220" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.error || '-' }}</template>
                </el-table-column>
                <template #empty><EmptyState v-if="healthLoaded && !healthLoading" :title="t('account.noChecks')" /><span v-else /></template>
              </el-table>
            </div>
          </div>
        </el-tab-pane>

        <!-- 审计 -->
        <el-tab-pane :label="t('account.tabAudit')" name="audit" lazy>
          <AuditTable :fetcher="auditFetcher" :fixed="auditFixed" />
        </el-tab-pane>
      </el-tabs>
    </template>

    <el-dialog v-model="editOpen" :title="t('account.editTitle')" width="480px" class="resp-dialog" destroy-on-close>
      <el-form ref="editRef" :model="editForm" :rules="editRules" label-position="top" @submit.prevent="submitEdit">
        <el-form-item :label="t('account.name')" prop="name" :error="editErrs.name">
          <el-input v-model="editForm.name" maxlength="32" />
        </el-form-item>
        <el-form-item :label="t('account.readonly')" :error="editErrs.readonly">
          <el-switch v-model="editForm.readonly" :aria-label="t('account.readonly')" />
          <span class="hint">{{ t('account.readonlyHint') }}</span>
        </el-form-item>
        <el-form-item :label="t('account.remark')" :error="editErrs.remark">
          <el-input v-model="editForm.remark" type="textarea" :rows="2" maxlength="200" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="editSaving" @click="submitEdit">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="credOpen" :title="t('account.credTitle')" width="480px" class="resp-dialog" destroy-on-close>
      <p class="hint block">{{ t('account.credHint') }}</p>
      <el-form ref="credRef" :model="credForm" :rules="credRules" label-position="top" @submit.prevent="submitCred">
        <el-form-item :label="t('account.apiKey')" prop="api_key">
          <el-input v-model="credForm.api_key" autocomplete="off" />
        </el-form-item>
        <el-form-item :label="t('account.apiSecret')" prop="api_secret">
          <el-input v-model="credForm.api_secret" type="password" show-password autocomplete="new-password" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="credOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="credSaving" @click="submitCred">{{ t('common.submit') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.mono { font-family: var(--el-font-family-mono, ui-monospace, monospace); font-size: 12px; }
.link { color: var(--el-color-primary); text-decoration: none; }
.link:hover { text-decoration: underline; }
.hint { color: var(--el-text-color-secondary); font-size: 12px; margin-left: 8px; }
.hint.block { display: block; margin: 0 0 8px; }
.table-wrap { overflow-x: auto; }
.tab-toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.tab-toolbar.end { justify-content: flex-end; }
.total-card { flex: 1 1 220px; max-width: 320px; }
.status-select { width: 180px; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 12px; }
.verify-card { margin-top: 16px; }
.perm-row { display: flex; gap: 16px; flex-wrap: wrap; margin: 10px 0; font-size: 13px; }
:deep(.resp-dialog) { max-width: calc(100vw - 24px); }
@media (max-width: 767px) {
  .resp-desc :deep(.el-descriptions__body .el-descriptions__table) { table-layout: fixed; }
  .status-select { width: 100%; }
  .total-card { max-width: none; }
}
</style>
