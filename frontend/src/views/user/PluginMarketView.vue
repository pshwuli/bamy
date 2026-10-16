<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection } from '@element-plus/icons-vue'
import { pluginsApi, bindingsApi, itemsOf } from '@/api/extensions'
import { accountsApi } from '@/api/accounts'
import { ApiError, fieldErrors } from '@/api/http'
import type { Account, Plugin, PluginHook, Scope } from '@/api/types'
import { handleError } from '@/composables/useErrors'
import { useAuth } from '@/stores/auth'
import { defaultsOf } from '@/utils/schema'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import PluginMeta from '@/components/PluginMeta.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import SchemaForm from '@/components/SchemaForm.vue'

type Row = Plugin & {
  load_error?: string | null
  hooks_summary?: { events?: string[]; checkpoints?: string[] }
  has_closed_fail_mode?: boolean
}

const { t } = useI18n()
const router = useRouter()
const auth = useAuth()

const all = shallowRef<Row[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const filters = ref({ keyword: '', scope: '', network: '', load: '' })
const applied = ref({ ...filters.value })

function loadState(p: Row): string {
  const s = p.load_status as string | undefined
  if (s === 'uninstalled') return 'failed'
  return s ?? (p.loaded ? 'loaded' : 'failed')
}
function hostsOf(p: Row): string[] {
  return p.network?.hosts ?? p.network_hosts ?? []
}
// 列表接口只给 hooks_summary，详情才给 hooks；这里统一成 PluginMeta 能渲染的结构
function withHooks(p: Row): Row {
  if (p.hooks || !p.hooks_summary) return p
  const hooks: PluginHook[] = [
    ...(p.hooks_summary.events ?? []).map((e) => ({ type: 'event' as const, event: e })),
    ...(p.hooks_summary.checkpoints ?? []).map((c, _i, arr) => ({
      type: 'checkpoint' as const,
      checkpoint: c,
      // 列表只有 has_closed_fail_mode 汇总；仅一个检查点时可确定其 fail_mode
      fail_mode: p.has_closed_fail_mode && arr.length === 1 ? ('closed' as const) : undefined,
    })),
  ]
  return { ...p, hooks }
}
const userScopes = (p: Row) => p.scopes.filter((s): s is Exclude<Scope, 'global'> => s !== 'global')
const globalOnly = (p: Row) => !userScopes(p).length
const allData = (p: Row) => p.data_scope === 'all'
const failed = (p: Row) => loadState(p) === 'failed' || !p.loaded
const canInstall = (p: Row) => !failed(p) && !globalOnly(p) && (!allData(p) || auth.isAdmin) && p.installable !== false

async function load() {
  loading.value = true
  error.value = null
  try {
    all.value = itemsOf<Row>(await pluginsApi.list()).map(withHooks)
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

const filtered = computed(() => {
  const f = applied.value
  const kw = f.keyword.trim().toLowerCase()
  return all.value.filter((p) => {
    if (kw && !`${p.id} ${p.name ?? ''} ${p.author ?? ''} ${(p.tags ?? []).join(' ')}`.toLowerCase().includes(kw)) return false
    if (f.scope && !p.scopes.includes(f.scope as Scope)) return false
    if (f.network === 'out' && !hostsOf(p).length) return false
    if (f.network === 'none' && hostsOf(p).length) return false
    if (f.load && loadState(p) !== f.load) return false
    return true
  })
})
function search() {
  applied.value = { ...filters.value }
}
function reset() {
  filters.value = { keyword: '', scope: '', network: '', load: '' }
  search()
}

// ---------------- 详情
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref<Row | null>(null)
async function fetchDetail(p: Row): Promise<Row> {
  try {
    return withHooks({ ...p, ...((await pluginsApi.get(p.id)) as Row) })
  } catch (e) {
    handleError(e)
    return p
  }
}
async function openDetail(p: Row) {
  detail.value = p
  detailOpen.value = true
  detailLoading.value = true
  detail.value = await fetchDetail(p)
  detailLoading.value = false
}

// ---------------- 安装
const inst = ref({
  open: false,
  loading: false,
  submitting: false,
  step: 0,
  plugin: null as Row | null,
  scope: '' as '' | Exclude<Scope, 'global'>,
  accountId: null as number | null,
  settings: {} as Record<string, unknown>,
  priority: 100,
  extErrors: {} as Record<string, string>,
})
const accounts = shallowRef<Account[]>([])
const formRef = ref<InstanceType<typeof SchemaForm> | null>(null)
const scopeChoices = computed(() => (inst.value.plugin ? userScopes(inst.value.plugin) : []))
const STEPS = computed(() => [t('plugin.chooseScope'), t('plugin.chooseAccount'), t('plugin.fillSettings'), t('plugin.setPriority'), t('plugin.confirmInstall')])

async function openInstall(p: Row) {
  if (!canInstall(p)) return
  inst.value = { open: true, loading: true, submitting: false, step: 0, plugin: p, scope: '', accountId: null, settings: {}, priority: p.priority ?? 100, extErrors: {} }
  const [full, accs] = await Promise.all([
    fetchDetail(p),
    accountsApi.list({ page: 1, page_size: 100 }).then((d) => itemsOf<Account>(d)).catch((e) => (handleError(e), [] as Account[])),
  ])
  accounts.value = accs
  inst.value.plugin = full
  inst.value.settings = defaultsOf(full.settings_schema ?? [])
  inst.value.priority = full.priority ?? 100
  const choices = userScopes(full)
  const def = full.default_scope && full.default_scope !== 'global' ? full.default_scope : choices[0]
  inst.value.scope = choices.length === 1 ? choices[0] : def ?? ''
  inst.value.loading = false
  // 只有一个作用域时跳过选择器
  if (choices.length === 1) inst.value.step = choices[0] === 'account' ? 1 : 2
}

function stepValid(step: number): boolean {
  const s = inst.value
  if (step === 0) return !!s.scope
  if (step === 1) return s.scope !== 'account' || !!s.accountId
  if (step === 2) return formRef.value ? formRef.value.validate() : true
  if (step === 3) return Number.isInteger(s.priority)
  return true
}
function next() {
  const s = inst.value
  if (!stepValid(s.step)) {
    if (s.step === 3) ElMessage.warning(t('extv.priorityInvalid'))
    return
  }
  s.step = s.step === 0 && s.scope !== 'account' ? 2 : s.step + 1
}
function prev() {
  const s = inst.value
  let n = s.step - 1
  if (n === 1 && s.scope !== 'account') n = 0
  if (n === 0 && scopeChoices.value.length === 1) return
  s.step = Math.max(0, n)
}
const canPrev = computed(() => {
  const s = inst.value
  if (s.step === 0) return false
  if (scopeChoices.value.length === 1) return s.step > (s.scope === 'account' ? 1 : 2)
  return true
})
const accountName = computed(() => accounts.value.find((a) => a.id === inst.value.accountId)?.name ?? '')
const settingsSnapshot = ref<Record<string, unknown>>({})

async function submit() {
  const s = inst.value
  const p = s.plugin
  if (!p || !s.scope) return
  const scopeId = s.scope === 'account' ? s.accountId : auth.user?.id
  if (!scopeId) return
  s.submitting = true
  try {
    await bindingsApi.create({
      plugin_id: p.id,
      scope: s.scope,
      scope_id: scopeId,
      settings: settingsSnapshot.value,
      priority: s.priority,
      enabled: true,
    })
    s.open = false
    try {
      await ElMessageBox.confirm(t('plugin.installDone'), t('common.success'), {
        type: 'success',
        confirmButtonText: t('plugin.goMyPlugins'),
        cancelButtonText: t('common.close'),
      })
      router.push('/my-plugins')
    } catch {
      /* 留在本页 */
    }
  } catch (e) {
    if (e instanceof ApiError && e.code === 40913) {
      ElMessage.warning(t('extv.exists'))
    } else if (e instanceof ApiError && (e.code === 42227 || e.code === 42228)) {
      s.extErrors = fieldErrors(e)
      s.step = 2
      handleError(e)
    } else handleError(e)
  } finally {
    s.submitting = false
  }
}
function goConfirm() {
  if (!stepValid(3)) return ElMessage.warning(t('extv.priorityInvalid'))
  settingsSnapshot.value = formRef.value?.values() ?? {}
  inst.value.step = 4
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader :title="t('plugin.title')">
      <el-button @click="router.push('/my-plugins')">{{ t('plugin.goMyPlugins') }}</el-button>
    </PageHeader>

    <FilterBar :loading="loading" @search="search" @reset="reset">
      <el-input v-model="filters.keyword" :placeholder="t('common.search')" :aria-label="t('common.search')" clearable @keyup.enter="search" />
      <el-select v-model="filters.scope" :placeholder="t('plugin.filterScope')" :aria-label="t('plugin.filterScope')" clearable @change="search">
        <el-option v-for="s in ['global', 'user', 'account']" :key="s" :label="t(`scope.${s}`)" :value="s" />
      </el-select>
      <el-select v-model="filters.network" :placeholder="t('plugin.filterNetwork')" :aria-label="t('plugin.filterNetwork')" clearable @change="search">
        <el-option :label="t('plugin.networkOut')" value="out" />
        <el-option :label="t('plugin.networkNone')" value="none" />
      </el-select>
      <el-select v-model="filters.load" :placeholder="t('plugin.filterLoad')" :aria-label="t('plugin.filterLoad')" clearable @change="search">
        <el-option v-for="s in ['loaded', 'failed', 'outdated']" :key="s" :label="t(`status.load.${s}`)" :value="s" />
      </el-select>
    </FilterBar>

    <ErrorState v-if="error" :error="error" @retry="load" />
    <template v-else>
      <div v-loading="loading" class="cards">
        <article v-for="p in filtered" :key="p.id" class="card pcard" :class="{ 'danger-card': allData(p), 'is-failed': failed(p) }">
          <header class="pc-head">
            <div class="pc-icon" aria-hidden="true">
              <img v-if="p.icon && /^(https?:|\/|data:)/.test(p.icon)" :src="p.icon" alt="" />
              <span v-else-if="p.icon">{{ p.icon }}</span>
              <el-icon v-else :size="22"><Connection /></el-icon>
            </div>
            <div class="pc-title">
              <h2>{{ p.name || p.id }}</h2>
              <div class="muted small">v{{ p.version || '-' }}<template v-if="p.author"> · {{ p.author }}</template></div>
            </div>
            <StatusBadge kind="load" :value="loadState(p)" />
          </header>
          <p class="pc-desc" :title="p.description">{{ p.description || t('extv.noDesc') }}</p>
          <div v-if="p.tags?.length" class="pc-tags">
            <el-tag v-for="tg in p.tags" :key="tg" size="small" effect="plain" disable-transitions>{{ tg }}</el-tag>
          </div>
          <dl class="pc-meta">
            <dt>{{ t('plugin.scopes') }}</dt><dd><PluginMeta :plugin="p" part="scopes" /></dd>
            <dt>{{ t('plugin.dataScope') }}</dt><dd><PluginMeta :plugin="p" part="dataScope" /></dd>
            <dt>{{ t('plugin.hosts') }}</dt><dd><PluginMeta :plugin="p" part="hosts" /></dd>
            <dt>{{ t('plugin.hooks') }}</dt><dd><PluginMeta :plugin="p" part="hooks" /></dd>
          </dl>
          <el-alert v-if="failed(p)" type="error" :closable="false" show-icon :title="t('plugin.loadFailed')" :description="p.load_error || p.error || ''" />
          <el-alert v-else-if="allData(p) && !auth.isAdmin" type="error" :closable="false" show-icon :title="`${t('plugin.notInstallable')} · ${t('plugin.needAdmin')}`" />
          <el-alert v-else-if="globalOnly(p)" type="info" :closable="false" show-icon :title="t('plugin.globalOnly')" />
          <footer class="pc-actions">
            <el-button size="small" @click="openDetail(p)">{{ t('common.detail') }}</el-button>
            <el-button v-if="failed(p)" size="small" type="primary" disabled>{{ t('plugin.install') }}</el-button>
            <el-button v-else-if="canInstall(p)" size="small" type="primary" @click="openInstall(p)">{{ t('plugin.install') }}</el-button>
          </footer>
        </article>
      </div>
      <EmptyState v-if="!loading && !filtered.length" :title="all.length ? t('common.noMatch') : t('common.empty')" />
    </template>

    <!-- 详情 -->
    <el-dialog v-model="detailOpen" :title="detail ? `${t('plugin.detail')} · ${detail.name || detail.id}` : ''" width="760px" append-to-body>
      <div v-if="detail" v-loading="detailLoading">
        <el-alert v-if="allData(detail)" type="error" :closable="false" show-icon :title="t('plugin.allDataWarn')" class="mb" />
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="ID"><code>{{ detail.id }}</code></el-descriptions-item>
          <el-descriptions-item :label="t('strategy.version')">{{ detail.version || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('strategy.author')">{{ detail.author || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('strategy.loadStatus')"><StatusBadge kind="load" :value="loadState(detail)" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.scopes')"><PluginMeta :plugin="detail" part="scopes" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.dataScope')"><PluginMeta :plugin="detail" part="dataScope" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.priority')">{{ detail.priority ?? '-' }}（{{ t('plugin.priorityHint') }}）</el-descriptions-item>
          <el-descriptions-item :label="t('plugin.failMode')"><PluginMeta :plugin="detail" part="failMode" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.hosts')" :span="2"><PluginMeta :plugin="detail" part="hosts" /></el-descriptions-item>
          <el-descriptions-item :label="t('plugin.hooks')" :span="2"><PluginMeta :plugin="detail" part="hooks" /></el-descriptions-item>
          <el-descriptions-item :label="t('common.detail')" :span="2">{{ detail.description || '-' }}</el-descriptions-item>
        </el-descriptions>
        <h3 class="sub-title">{{ t('plugin.settingsSchema') }}</h3>
        <div class="table-wrap">
          <el-table :data="detail.settings_schema ?? []" size="small" class="resp-table" :empty-text="t('extv.noSettings')">
            <el-table-column :label="t('extv.paramKey')" min-width="120"><template #default="{ row }"><code>{{ row.key }}</code></template></el-table-column>
            <el-table-column :label="t('extv.paramLabel')" prop="label" min-width="110" />
            <el-table-column :label="t('extv.paramType')" prop="type" width="90" />
            <el-table-column :label="t('extv.paramRequired')" width="70"><template #default="{ row }">{{ row.required ? t('common.yes') : t('common.no') }}</template></el-table-column>
            <el-table-column :label="t('extv.paramHelp')" prop="help" min-width="160" />
          </el-table>
        </div>
      </div>
      <template #footer>
        <el-button @click="detailOpen = false">{{ t('common.close') }}</el-button>
        <el-button v-if="detail && canInstall(detail)" type="primary" @click="(detailOpen = false), detail && openInstall(detail)">{{ t('plugin.install') }}</el-button>
      </template>
    </el-dialog>

    <!-- 安装 -->
    <el-dialog
      v-model="inst.open"
      :title="inst.plugin ? t('extv.installTitle', { name: inst.plugin.name || inst.plugin.id }) : ''"
      width="680px"
      append-to-body
      :close-on-click-modal="false"
    >
      <div v-loading="inst.loading">
        <el-steps :active="inst.step" finish-status="success" simple class="mb" aria-hidden="true">
          <el-step v-for="s in STEPS" :key="s" :title="s" />
        </el-steps>
        <p class="sr-only" aria-live="polite">{{ t('extv.step', { n: inst.step + 1 }) }} {{ STEPS[inst.step] }}</p>

        <template v-if="inst.plugin">
          <div v-show="inst.step === 0">
            <el-alert v-if="scopeChoices.length === 1" type="info" :closable="false" :title="t('extv.scopeAuto', { scope: t(`scope.${scopeChoices[0]}`) })" />
            <el-form v-else label-position="top" @submit.prevent>
              <el-form-item :label="t('plugin.chooseScope')" required>
                <el-radio-group v-model="inst.scope">
                  <el-radio v-for="s in scopeChoices" :key="s" :value="s">{{ s === 'user' ? t('extv.userScopeSelf') : t(`scope.${s}`) }}</el-radio>
                  <el-radio v-if="inst.plugin.scopes.includes('global')" value="global" disabled>{{ t('scope.global') }}（{{ t('extv.onlyAdminScope') }}）</el-radio>
                </el-radio-group>
              </el-form-item>
            </el-form>
          </div>

          <div v-show="inst.step === 1">
            <el-form label-position="top" @submit.prevent>
              <el-form-item :label="t('plugin.chooseAccount')" required :error="inst.scope === 'account' && !accounts.length ? t('extv.noAccounts') : ''">
                <el-select v-model="inst.accountId" :placeholder="t('plugin.chooseAccount')" :aria-label="t('plugin.chooseAccount')" style="width: 100%">
                  <el-option v-for="a in accounts" :key="a.id" :label="`${a.name}（${t(`env.${a.env}`)}）`" :value="a.id" />
                </el-select>
              </el-form-item>
            </el-form>
          </div>

          <div v-show="inst.step === 2">
            <SchemaForm
              ref="formRef"
              v-model="inst.settings"
              :schema="inst.plugin.settings_schema ?? []"
              :external-errors="inst.extErrors"
            />
          </div>

          <div v-show="inst.step === 3">
            <el-form label-position="top" @submit.prevent>
              <el-form-item :label="t('plugin.setPriority')" required>
                <el-input-number v-model="inst.priority" :min="0" :max="100000" :step="1" step-strictly :aria-label="t('plugin.priority')" />
                <div class="muted small hint">{{ t('plugin.priorityHint') }}</div>
              </el-form-item>
            </el-form>
          </div>

          <div v-show="inst.step === 4">
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item :label="t('binding.plugin')">{{ inst.plugin.name || inst.plugin.id }} v{{ inst.plugin.version }}</el-descriptions-item>
              <el-descriptions-item :label="t('binding.scope')">
                {{ inst.scope ? t(`scope.${inst.scope}`) : '-' }}
                <template v-if="inst.scope === 'account'"> · {{ accountName }}</template>
                <template v-else-if="inst.scope === 'user'"> · {{ t('extv.targetSelf', { name: auth.user?.username ?? '' }) }}</template>
              </el-descriptions-item>
              <el-descriptions-item :label="t('binding.priority')">{{ inst.priority }}</el-descriptions-item>
            </el-descriptions>
            <p class="muted small">{{ t('extv.confirmSummary', { scope: inst.scope ? t(`scope.${inst.scope}`) : '-', p: inst.priority }) }}</p>
            <p v-if="!inst.plugin.allow_multiple" class="muted small">{{ t('extv.overrideTip') }}</p>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="inst.open = false">{{ t('common.cancel') }}</el-button>
        <el-button v-if="canPrev" @click="prev">{{ t('extv.prev') }}</el-button>
        <el-button v-if="inst.step < 3" type="primary" :disabled="inst.loading" @click="next">{{ t('extv.next') }}</el-button>
        <el-button v-else-if="inst.step === 3" type="primary" @click="goConfirm">{{ t('extv.next') }}</el-button>
        <el-button v-else type="primary" :loading="inst.submitting" @click="submit">{{ t('plugin.confirmInstall') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.cards { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); min-height: 80px; }
.cards .card + .card { margin-top: 0; }
.pcard { display: flex; flex-direction: column; gap: 8px; }
.pcard.is-failed { opacity: 0.8; }
.pc-head { display: flex; align-items: center; gap: 10px; }
.pc-icon { width: 40px; height: 40px; border-radius: 8px; background: var(--el-fill-color-light); display: flex; align-items: center; justify-content: center; flex: none; font-size: 20px; overflow: hidden; }
.pc-icon img { width: 100%; height: 100%; object-fit: cover; }
.pc-title { flex: 1; min-width: 0; }
.pc-title h2 { font-size: 15px; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.small { font-size: 12px; }
.pc-desc { margin: 0; font-size: 13px; color: var(--el-text-color-regular); display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 2.6em; }
.pc-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.pc-meta { display: grid; grid-template-columns: auto 1fr; gap: 4px 10px; margin: 0; font-size: 12px; }
.pc-meta dt { color: var(--el-text-color-secondary); }
.pc-meta dd { margin: 0; min-width: 0; }
.pc-actions { margin-top: auto; display: flex; gap: 8px; justify-content: flex-end; }
.table-wrap { overflow-x: auto; }
.sub-title { font-size: 14px; margin: 16px 0 8px; }
.mb { margin-bottom: 12px; }
.hint { margin-left: 8px; }
</style>
