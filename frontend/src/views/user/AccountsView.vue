<script setup lang="ts">
import { computed, reactive, ref, shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Plus, Upload } from '@element-plus/icons-vue'
import { accountsApi, type AccountBalance, type AccountInput } from '@/api/accounts'
import { fieldErrors } from '@/api/http'
import type { Account } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { confirm } from '@/composables/useConfirm'
import { fmtMoney, fmtNum, fmtTime } from '@/utils/format'
import { req } from '@/utils/rules'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import Pager from '@/components/Pager.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'

const { t } = useI18n()
const KEY_RE = /^[A-Za-z0-9]{16,128}$/
const BATCH_MAX = 50

const { items, loading, loadedOnce, error, page, pageSize, total, filters, load, search, resetFilters } = useTable<Account>(
  (q) => accountsApi.list(q),
  { keyword: '', status: '', env: '', market_type: '' },
)

const statusOptions = computed(() => [
  { value: 'online', label: t('status.account.online') },
  { value: 'offline', label: t('status.account.offline') },
  { value: 'error', label: t('status.account.abnormal') },
  { value: 'invalid', label: t('status.account.invalid') },
])

function statusValue(s: string) {
  return s === 'error' ? 'abnormal' : s
}

function envLabel(env: string) {
  return env === 'mainnet' || env === 'testnet' ? t(`env.${env}`) : env
}
function marketLabel(m: string) {
  return m === 'spot' ? t('market.spot') : m === 'futures' ? t('mkt.futures') : m
}

const keyRules = () => [req(), { pattern: KEY_RE, message: t('account.keyFormat'), trigger: ['blur', 'change'] }]
const nameRules = () => [req(), { min: 4, max: 32, message: t('account.nameRule'), trigger: ['blur', 'change'] }]

// ---------- 新增 ----------
const createOpen = ref(false)
const createSaving = ref(false)
const createRef = ref<FormInstance>()
const createForm = reactive<AccountInput>({ name: '', api_key: '', api_secret: '', env: 'mainnet', market_type: 'spot', readonly: false, remark: '' })
const createErrs = ref<Record<string, string>>({})
const createRules = computed<FormRules>(() => ({ name: nameRules(), api_key: keyRules(), api_secret: keyRules(), env: [req()] }))

function openCreate() {
  Object.assign(createForm, { name: '', api_key: '', api_secret: '', env: 'mainnet', market_type: 'spot', readonly: false, remark: '' })
  createErrs.value = {}
  createOpen.value = true
}

async function submitCreate() {
  if (!(await createRef.value?.validate().catch(() => false))) return
  createSaving.value = true
  createErrs.value = {}
  try {
    const r = (await accountsApi.create({ ...createForm, remark: createForm.remark || undefined })) as Account & { can_withdraw?: boolean }
    createOpen.value = false
    ElMessage.success(t('common.success'))
    if (r?.can_withdraw) ElMessage({ type: 'warning', message: t('account.withdrawWarn'), duration: 6000 })
    await load()
  } catch (e) {
    createErrs.value = fieldErrors(e)
  } finally {
    createSaving.value = false
  }
}

// ---------- 批量导入 ----------
type BatchResult = { success: number; failed: number; errors: { index: number; name?: string; code?: number; reason: string }[] }
const batchOpen = ref(false)
const batchText = ref('')
const batchSaving = ref(false)
const batchResult = shallowRef<BatchResult | null>(null)
const batchParseErr = ref('')

function toBool(v: unknown): boolean {
  if (typeof v === 'boolean') return v
  const s = String(v ?? '').trim().toLowerCase()
  return s === 'true' || s === '1' || s === 'yes' || s === 'y'
}

function normalize(o: Record<string, unknown>): AccountInput {
  const remark = o.remark == null ? '' : String(o.remark).trim()
  return {
    name: String(o.name ?? '').trim(),
    api_key: String(o.api_key ?? '').trim(),
    api_secret: String(o.api_secret ?? '').trim(),
    env: String(o.env ?? '').trim() || 'mainnet',
    market_type: String(o.market_type ?? '').trim() === 'futures' ? 'futures' : 'spot',
    readonly: toBool(o.readonly),
    ...(remark ? { remark } : {}),
  }
}

function splitCsvLine(line: string): string[] {
  const out: string[] = []
  let cur = ''
  let quoted = false
  for (let i = 0; i < line.length; i++) {
    const c = line[i]
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') {
        cur += '"'
        i++
      } else if (c === '"') quoted = false
      else cur += c
    } else if (c === '"') quoted = true
    else if (c === ',') {
      out.push(cur)
      cur = ''
    } else cur += c
  }
  out.push(cur)
  return out.map((s) => s.trim())
}

const CSV_COLS = ['name', 'api_key', 'api_secret', 'env', 'readonly', 'remark']

function parseBatch(text: string): AccountInput[] {
  const s = text.trim()
  if (!s) return []
  if (s.startsWith('[') || s.startsWith('{')) {
    let data: unknown
    try {
      data = JSON.parse(s)
    } catch {
      // 每行一个 JSON 对象
      data = s.split(/\r?\n/).filter((l) => l.trim()).map((l) => JSON.parse(l))
    }
    const arr = Array.isArray(data) ? data : [data]
    return arr.map((o) => normalize((o ?? {}) as Record<string, unknown>))
  }
  const lines = s.split(/\r?\n/).filter((l) => l.trim())
  let cols = CSV_COLS
  const head = splitCsvLine(lines[0]).map((c) => c.toLowerCase())
  if (head.includes('name') && head.includes('api_key')) {
    cols = head
    lines.shift()
  }
  return lines.map((l) => {
    const cells = splitCsvLine(l)
    const o: Record<string, unknown> = {}
    cols.forEach((c, i) => (o[c] = cells[i]))
    return normalize(o)
  })
}

const batchParsed = computed<AccountInput[]>(() => {
  try {
    return parseBatch(batchText.value)
  } catch {
    return []
  }
})

function openBatch() {
  batchText.value = ''
  batchResult.value = null
  batchParseErr.value = ''
  batchOpen.value = true
}

async function submitBatch() {
  batchParseErr.value = ''
  let list: AccountInput[]
  try {
    list = parseBatch(batchText.value)
  } catch (e) {
    batchParseErr.value = t('account.batchParseError', { msg: e instanceof Error ? e.message : String(e) })
    return
  }
  if (!list.length) {
    batchParseErr.value = t('account.batchEmpty')
    return
  }
  if (list.length > BATCH_MAX) {
    batchParseErr.value = t('account.batchTooMany', { n: BATCH_MAX })
    return
  }
  batchSaving.value = true
  try {
    batchResult.value = await accountsApi.batch(list)
    if (batchResult.value?.success) await load()
  } catch {
    /* 全局已提示 */
  } finally {
    batchSaving.value = false
  }
}

// ---------- 编辑 ----------
const editOpen = ref(false)
const editSaving = ref(false)
const editRef = ref<FormInstance>()
const editId = ref<number | null>(null)
const editForm = reactive({ name: '', readonly: false, remark: '' })
const editErrs = ref<Record<string, string>>({})
const editRules = computed<FormRules>(() => ({ name: nameRules() }))

function openEdit(a: Account) {
  editId.value = a.id
  Object.assign(editForm, { name: a.name, readonly: !!a.readonly, remark: a.remark ?? '' })
  editErrs.value = {}
  editOpen.value = true
}

async function submitEdit() {
  if (editId.value == null || !(await editRef.value?.validate().catch(() => false))) return
  editSaving.value = true
  editErrs.value = {}
  try {
    await accountsApi.update(editId.value, { name: editForm.name, readonly: editForm.readonly, remark: editForm.remark })
    ElMessage.success(t('common.success'))
    editOpen.value = false
    await load()
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
const credTarget = shallowRef<Account | null>(null)
const credForm = reactive({ api_key: '', api_secret: '' })
const credRules = computed<FormRules>(() => ({ api_key: keyRules(), api_secret: keyRules() }))

function openCred(a: Account) {
  credTarget.value = a
  Object.assign(credForm, { api_key: '', api_secret: '' })
  credOpen.value = true
}

async function submitCred() {
  if (!credTarget.value || !(await credRef.value?.validate().catch(() => false))) return
  credSaving.value = true
  try {
    await accountsApi.credentials(credTarget.value.id, { ...credForm })
    ElMessage.success(t('common.success'))
    credOpen.value = false
    await load()
  } catch {
    /* 全局已提示 */
  } finally {
    credSaving.value = false
  }
}

// ---------- 验证 ----------
type VerifyResult = { ok?: boolean; status?: string; message?: string; reason?: string; code?: number; can_trade?: boolean; can_withdraw?: boolean }
const verifying = ref<number | null>(null)
const verifyOpen = ref(false)
const verifyTarget = shallowRef<Account | null>(null)
const verifyRes = shallowRef<VerifyResult | null>(null)

async function doVerify(a: Account) {
  verifying.value = a.id
  try {
    verifyRes.value = (await accountsApi.verify(a.id)) as VerifyResult
    verifyTarget.value = a
    verifyOpen.value = true
    await load()
  } catch {
    /* 全局已提示 */
  } finally {
    verifying.value = null
  }
}

// ---------- 余额 ----------
const balOpen = ref(false)
const balLoading = ref(false)
const balError = ref<unknown>(null)
const balTarget = shallowRef<Account | null>(null)
const balData = shallowRef<AccountBalance | null>(null)

type AssetRow = { asset: string; free: number | null; locked: number | null; total: number | null }
const balRows = computed<AssetRow[]>(() => {
  const d = balData.value as (AccountBalance & { details?: { asset: string; free?: number; used?: number; total?: number }[] }) | null
  if (!d) return []
  if (Array.isArray(d.details) && d.details.length) {
    return d.details.map((b) => ({ asset: b.asset, free: b.free ?? null, locked: b.used ?? null, total: b.total ?? null }))
  }
  return Object.entries(d.assets ?? {}).map(([asset, v]) =>
    typeof v === 'number'
      ? { asset, free: null, locked: null, total: v }
      : { asset, free: v.free ?? null, locked: v.locked ?? null, total: v.total ?? null },
  )
})

async function loadBalance() {
  if (!balTarget.value) return
  balLoading.value = true
  balError.value = null
  try {
    balData.value = await accountsApi.balance(balTarget.value.id)
  } catch (e) {
    balError.value = e
  } finally {
    balLoading.value = false
  }
}

function openBalance(a: Account) {
  balTarget.value = a
  balData.value = null
  balOpen.value = true
  void loadBalance()
}

// ---------- 删除 ----------
async function doDelete(a: Account) {
  if (!(await confirm(t('account.deleteConfirm', { name: a.name }), t('common.delete'), true))) return
  try {
    await accountsApi.remove(a.id)
    ElMessage.success(t('common.success'))
    if (items.value.length === 1 && page.value > 1) page.value -= 1
    else await load()
  } catch {
    /* 全局已提示 */
  }
}

</script>

<template>
  <div class="accounts-view">
    <PageHeader :title="t('account.title')">
      <el-button type="primary" :icon="Plus" @click="openCreate">{{ t('account.add') }}</el-button>
      <el-button :icon="Upload" @click="openBatch">{{ t('account.batch') }}</el-button>
    </PageHeader>

    <FilterBar :loading="loading" @search="search" @reset="resetFilters">
      <el-input
        v-model="filters.keyword"
        :placeholder="t('account.searchPh')"
        :aria-label="t('account.searchPh')"
        clearable
        @keyup.enter="search"
      />
      <el-select v-model="filters.status" :placeholder="t('account.status')" :aria-label="t('account.status')" clearable @change="search">
        <el-option v-for="o in statusOptions" :key="o.value" :value="o.value" :label="o.label" />
      </el-select>
      <el-select v-model="filters.env" :placeholder="t('account.env')" :aria-label="t('account.env')" clearable @change="search">
        <el-option value="mainnet" :label="t('env.mainnet')" />
        <el-option value="testnet" :label="t('env.testnet')" />
      </el-select>
      <el-select v-model="filters.market_type" :placeholder="t('account.market')" :aria-label="t('account.market')" clearable @change="search">
        <el-option value="spot" :label="t('market.spot')" />
        <el-option value="futures" :label="t('mkt.futures')" />
      </el-select>
    </FilterBar>

    <ErrorState v-if="error && !items.length" :error="error" @retry="load" />
    <template v-else>
      <div v-loading="loading" class="table-wrap">
        <el-table :data="items" class="resp-table" row-key="id">
          <el-table-column :label="t('account.name')" min-width="140" fixed>
            <template #default="{ row }">
              <router-link :to="`/accounts/${row.id}`" class="link">{{ row.name }}</router-link>
            </template>
          </el-table-column>
          <el-table-column :label="t('account.apiKey')" min-width="150">
            <template #default="{ row }"><code class="mono">{{ row.api_key_masked || '-' }}</code></template>
          </el-table-column>
          <el-table-column :label="t('account.env')" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.env === 'testnet' ? 'warning' : 'info'" effect="plain">{{ envLabel(row.env) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="t('account.market')" width="90">
            <template #default="{ row }">{{ marketLabel(row.market_type) }}</template>
          </el-table-column>
          <el-table-column :label="t('account.readonly')" width="80">
            <template #default="{ row }">
              <el-tag v-if="row.readonly" size="small" type="info">{{ t('common.readonly') }}</el-tag>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('account.status')" width="100">
            <template #default="{ row }"><StatusBadge kind="account" :value="statusValue(row.status)" /></template>
          </el-table-column>
          <el-table-column :label="t('account.instanceCount')" width="100" align="right">
            <template #default="{ row }">{{ row.instance_count ?? 0 }}</template>
          </el-table-column>
          <el-table-column :label="t('account.lastActive')" min-width="160">
            <template #default="{ row }">{{ fmtTime(row.last_active_at) }}</template>
          </el-table-column>
          <el-table-column :label="t('common.actions')" min-width="300">
            <template #default="{ row }">
              <div class="ops">
                <el-button link type="primary" @click="openEdit(row as Account)">{{ t('common.edit') }}</el-button>
                <el-button link type="primary" :loading="verifying === row.id" @click="doVerify(row as Account)">{{ t('account.verify') }}</el-button>
                <el-button link type="primary" @click="openCred(row as Account)">{{ t('account.credentials') }}</el-button>
                <el-button link type="primary" @click="openBalance(row as Account)">{{ t('account.viewBalance') }}</el-button>
                <el-button link type="danger" @click="doDelete(row as Account)">{{ t('common.delete') }}</el-button>
              </div>
            </template>
          </el-table-column>
          <template #empty>
            <EmptyState
              v-if="loadedOnce && !loading"
              :title="t('common.empty')"
              :action-text="t('account.add')"
              @action="openCreate"
            />
            <span v-else />
          </template>
        </el-table>
      </div>
      <Pager v-if="total > 0" v-model:page="page" v-model:size="pageSize" :total="total" />
    </template>

    <!-- 新增 -->
    <el-dialog v-model="createOpen" :title="t('account.add')" width="520px" class="resp-dialog" destroy-on-close>
      <el-form ref="createRef" :model="createForm" :rules="createRules" label-position="top" @submit.prevent="submitCreate">
        <el-form-item :label="t('account.name')" prop="name" :error="createErrs.name">
          <el-input v-model="createForm.name" maxlength="32" autocomplete="off" />
        </el-form-item>
        <el-form-item :label="t('account.apiKey')" prop="api_key" :error="createErrs.api_key">
          <el-input v-model="createForm.api_key" autocomplete="off" />
        </el-form-item>
        <el-form-item :label="t('account.apiSecret')" prop="api_secret" :error="createErrs.api_secret">
          <el-input v-model="createForm.api_secret" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-form-item :label="t('account.env')" prop="env" :error="createErrs.env">
          <el-radio-group v-model="createForm.env">
            <el-radio value="mainnet">{{ t('env.mainnet') }}</el-radio>
            <el-radio value="testnet">{{ t('env.testnet') }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="t('account.market')">
          <el-select v-model="createForm.market_type" :aria-label="t('account.market')">
            <el-option value="spot" :label="t('market.spot')" />
            <el-option value="futures" :label="t('mkt.futures')" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('account.readonly')">
          <el-switch v-model="createForm.readonly" :aria-label="t('account.readonly')" />
          <span class="hint">{{ t('account.readonlyHint') }}</span>
        </el-form-item>
        <el-form-item :label="t('account.remark')" :error="createErrs.remark">
          <el-input v-model="createForm.remark" type="textarea" :rows="2" maxlength="200" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="createSaving" @click="submitCreate">{{ t('common.submit') }}</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入 -->
    <el-dialog v-model="batchOpen" :title="t('account.batch')" width="640px" class="resp-dialog" destroy-on-close>
      <p class="hint block">{{ t('account.batchHint') }}</p>
      <p class="hint block">{{ t('account.batchJsonOrCsv') }}</p>
      <el-input
        v-model="batchText"
        type="textarea"
        :rows="8"
        :aria-label="t('account.batch')"
        placeholder='[{"name":"acc01","api_key":"...","api_secret":"...","env":"mainnet","readonly":false}]'
        class="mono"
      />
      <div class="batch-meta">
        <span v-if="batchParsed.length" class="hint">{{ t('account.batchPreview', { n: batchParsed.length }) }}</span>
        <el-alert v-if="batchParseErr" :title="batchParseErr" type="error" :closable="false" show-icon />
      </div>
      <template v-if="batchResult">
        <el-alert
          :title="t('account.batchResult', { success: batchResult.success, failed: batchResult.failed })"
          :type="batchResult.failed ? (batchResult.success ? 'warning' : 'error') : 'success'"
          :closable="false"
          show-icon
        />
        <template v-if="batchResult.errors?.length">
          <h4 class="sub-title">{{ t('account.batchErrors') }}</h4>
          <el-table :data="batchResult.errors" size="small" class="resp-table" max-height="240">
            <el-table-column :label="t('account.rowIndex')" prop="index" width="70" />
            <el-table-column :label="t('account.name')" width="140">
              <template #default="{ row }">{{ row.name || '-' }}</template>
            </el-table-column>
            <el-table-column :label="t('account.reason')" prop="reason" min-width="200" />
          </el-table>
        </template>
      </template>
      <template #footer>
        <el-button @click="batchOpen = false">{{ t('common.close') }}</el-button>
        <el-button type="primary" :loading="batchSaving" :disabled="!batchText.trim()" @click="submitBatch">{{ t('account.import') }}</el-button>
      </template>
    </el-dialog>

    <!-- 编辑 -->
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

    <!-- 更新密钥 -->
    <el-dialog v-model="credOpen" :title="`${t('account.credTitle')} · ${credTarget?.name ?? ''}`" width="480px" class="resp-dialog" destroy-on-close>
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

    <!-- 验证结果 -->
    <el-dialog v-model="verifyOpen" :title="`${t('account.verifyResultTitle')} · ${verifyTarget?.name ?? ''}`" width="440px" class="resp-dialog">
      <template v-if="verifyRes">
        <el-result
          :icon="verifyRes.ok ? 'success' : 'error'"
          :title="verifyRes.ok ? t('account.verifyOk') : t('account.verifyFailed')"
          :sub-title="verifyRes.ok ? '' : verifyRes.reason || verifyRes.message || ''"
        />
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item :label="t('account.status')">
            <StatusBadge kind="account" :value="statusValue(verifyRes.status ?? '')" />
          </el-descriptions-item>
          <el-descriptions-item v-if="verifyRes.can_trade !== undefined" :label="t('account.canTrade')">
            {{ verifyRes.can_trade ? t('common.yes') : t('common.no') }}
          </el-descriptions-item>
          <el-descriptions-item v-if="verifyRes.can_withdraw !== undefined" :label="t('account.canWithdraw')">
            {{ verifyRes.can_withdraw ? t('common.yes') : t('common.no') }}
          </el-descriptions-item>
          <el-descriptions-item v-if="verifyRes.code" label="code">{{ verifyRes.code }}</el-descriptions-item>
        </el-descriptions>
        <el-alert v-if="verifyRes.can_withdraw" :title="t('account.withdrawWarn')" type="warning" :closable="false" show-icon class="mt" />
      </template>
      <template #footer>
        <el-button type="primary" @click="verifyOpen = false">{{ t('common.close') }}</el-button>
      </template>
    </el-dialog>

    <!-- 余额 -->
    <el-dialog v-model="balOpen" :title="`${t('account.balance')} · ${balTarget?.name ?? ''}`" width="560px" class="resp-dialog">
      <ErrorState v-if="balError" :error="balError" @retry="loadBalance" />
      <div v-else v-loading="balLoading">
        <div class="bal-total">
          <span class="muted">{{ t('account.totalUsdt') }}</span>
          <strong>{{ fmtMoney(balData?.total_usdt) }}</strong>
        </div>
        <el-table :data="balRows" size="small" class="resp-table" max-height="360">
          <el-table-column :label="t('account.asset')" prop="asset" min-width="90" />
          <el-table-column :label="t('account.free')" align="right" min-width="110">
            <template #default="{ row }">{{ fmtNum(row.free, 8) }}</template>
          </el-table-column>
          <el-table-column :label="t('account.locked')" align="right" min-width="110">
            <template #default="{ row }">{{ fmtNum(row.locked, 8) }}</template>
          </el-table-column>
          <el-table-column :label="t('account.amount')" align="right" min-width="110">
            <template #default="{ row }">{{ fmtNum(row.total, 8) }}</template>
          </el-table-column>
          <template #empty><EmptyState v-if="!balLoading" :title="t('account.noBalance')" /><span v-else /></template>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="balOpen = false">{{ t('common.close') }}</el-button>
        <el-button :loading="balLoading" @click="loadBalance">{{ t('common.refresh') }}</el-button>
        <router-link v-if="balTarget" :to="`/accounts/${balTarget.id}`" class="el-button el-button--primary dlg-link">{{ t('common.detail') }}</router-link>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.table-wrap { overflow-x: auto; }
.link { color: var(--el-color-primary); text-decoration: none; }
.link:hover { text-decoration: underline; }
.mono { font-family: var(--el-font-family-mono, ui-monospace, monospace); font-size: 12px; }
.muted { color: var(--el-text-color-secondary); }
.hint { color: var(--el-text-color-secondary); font-size: 12px; margin-left: 8px; }
.hint.block { display: block; margin: 0 0 8px; }
.ops { display: flex; flex-wrap: wrap; gap: 4px 0; }
.ops :deep(.el-button + .el-button) { margin-left: 8px; }
.batch-meta { margin: 8px 0; display: flex; flex-direction: column; gap: 6px; }
.sub-title { margin: 12px 0 6px; font-size: 14px; }
.bal-total { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; font-size: 16px; }
.mt { margin-top: 12px; }
.dlg-link { margin-left: 12px; text-decoration: none; }
:deep(.resp-dialog) { max-width: calc(100vw - 24px); }
</style>
