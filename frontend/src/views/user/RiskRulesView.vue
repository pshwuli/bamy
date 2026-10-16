<script setup lang="ts">
// 风控规则：每笔下单前由后端风控检查（先于插件拦截）。用户管自己的规则；管理员还能建全局规则（对所有人生效）。
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Plus } from '@element-plus/icons-vue'
import { http } from '@/api/http'
import { accountsApi } from '@/api/accounts'
import type { Account } from '@/api/types'
import { useAuth } from '@/stores/auth'
import { attempt } from '@/composables/useErrors'
import { confirm } from '@/composables/useConfirm'
import { fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'

interface Shape { field: string; min?: number; max?: number; list?: boolean }
interface Rule {
  id: number; rule_type: string; rule_value: Record<string, unknown>
  account_id: number | null; owner_id: number | null; enabled: boolean; created_at?: string
}

const { t } = useI18n()
const auth = useAuth()
const items = ref<Rule[]>([])
const shapes = ref<Record<string, Shape>>({})
const accounts = ref<Account[]>([])
const loading = ref(true)
const error = ref<unknown>(null)
async function load() {
  loading.value = true
  error.value = null
  try {
    const r = await http.get<{ items: Rule[]; shapes: Record<string, Shape> }>('/api/risk-rules', undefined, { silent: true })
    items.value = r.items
    shapes.value = r.shapes ?? {}
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}
onMounted(async () => {
  void load()
  try {
    const r = await accountsApi.list({ page: 1, page_size: 100 })
    accounts.value = r.items
  } catch {
    /* 没有账号时只能建"全部账号"范围的规则 */
  }
})

const typeOptions = computed(() => Object.keys(shapes.value))
const accName = (id: number | null) => (id == null ? t('risk.scopeAll') : accounts.value.find((a) => a.id === id)?.name ?? `#${id}`)
const isRatio = (type: string) => type === 'max_drawdown' || type === 'max_daily_loss'
const isSeconds = (type: string) => type.endsWith('_cooldown')

function fmtValue(r: Rule): string {
  const sh = shapes.value[r.rule_type]
  const v = r.rule_value?.[sh?.field ?? 'value']
  if (Array.isArray(v)) return v.join('  ')
  const n = Number(v)
  if (isRatio(r.rule_type)) return `${(n * 100).toFixed(0)}%`
  if (isSeconds(r.rule_type)) return n >= 3600 ? `${(n / 3600).toFixed(1)} h` : `${Math.round(n / 60)} min`
  if (r.rule_type.endsWith('_value')) return `${n.toLocaleString()} USDT`
  return String(n)
}

// ---- 新增 / 编辑
const open = ref(false)
const saving = ref(false)
const editing = ref<Rule | null>(null)
const form = reactive({ rule_type: 'max_order_value', num: 1000 as number | null, symbols: '', account_id: null as number | null, global_rule: false })
const curShape = computed(() => shapes.value[form.rule_type])

function openNew() {
  editing.value = null
  Object.assign(form, { rule_type: typeOptions.value[0] ?? 'max_order_value', num: null, symbols: '', account_id: null, global_rule: false })
  open.value = true
}
function openEdit(r: Rule) {
  editing.value = r
  const sh = shapes.value[r.rule_type]
  const v = r.rule_value?.[sh?.field ?? 'value']
  Object.assign(form, {
    rule_type: r.rule_type, account_id: r.account_id, global_rule: r.owner_id == null,
    symbols: Array.isArray(v) ? v.join(' ') : '',
    num: Array.isArray(v) ? null : isRatio(r.rule_type) ? Number(v) * 100 : Number(v),
  })
  open.value = true
}
function buildValue(): Record<string, unknown> {
  const sh = curShape.value
  if (sh?.list) return { symbols: form.symbols.split(/[\s,，]+/).map((s) => s.trim().toUpperCase()).filter(Boolean) }
  const n = Number(form.num)
  return { [sh?.field ?? 'value']: isRatio(form.rule_type) ? n / 100 : n }
}
async function save() {
  if (saving.value) return
  saving.value = true
  const body = buildValue()
  const ok = await attempt(() =>
    editing.value
      ? http.patch(`/api/risk-rules/${editing.value.id}`, { rule_value: body })
      : http.post('/api/risk-rules', { rule_type: form.rule_type, rule_value: body, account_id: form.account_id, global_rule: form.global_rule }),
  t('common.saved'))
  saving.value = false
  if (ok !== undefined) {
    open.value = false
    void load()
  }
}
async function toggle(r: Rule) {
  const ok = await attempt(() => http.patch(`/api/risk-rules/${r.id}`, { enabled: !r.enabled }))
  if (ok !== undefined) r.enabled = !r.enabled
}
async function remove(r: Rule) {
  if (!(await confirm(t('risk.deleteConfirm'), t('common.delete'), true))) return
  const ok = await attempt(() => http.delete(`/api/risk-rules/${r.id}`), t('common.deleted'))
  if (ok !== undefined) items.value = items.value.filter((x) => x.id !== r.id)
}
const canEdit = (r: Rule) => r.owner_id != null || auth.isAdmin
</script>
<template>
  <div>
    <PageHeader :title="t('nav.riskRules')">
      <el-button type="primary" :icon="Plus" @click="openNew">{{ t('risk.add') }}</el-button>
    </PageHeader>
    <p class="note">{{ t('risk.note') }}</p>

    <ErrorState v-if="error" :error="error" @retry="load" />
    <div v-else v-loading="loading">
      <EmptyState v-if="!loading && !items.length" :description="t('risk.empty')">
        <el-button type="primary" :icon="Plus" @click="openNew">{{ t('risk.add') }}</el-button>
      </EmptyState>
      <ul v-else class="rules">
        <li v-for="r in items" :key="r.id" class="rule" :class="{ off: !r.enabled }">
          <div class="r-main">
            <div class="r-title">
              {{ t(`risk.t_${r.rule_type}`) }}
              <el-tag v-if="r.owner_id == null" size="small" type="warning" disable-transitions>{{ t('risk.global') }}</el-tag>
            </div>
            <div class="r-value num">{{ fmtValue(r) }}</div>
            <div class="r-meta">{{ accName(r.account_id) }}<template v-if="r.created_at"> · {{ fmtTime(r.created_at, false) }}</template></div>
          </div>
          <div v-if="canEdit(r)" class="r-actions">
            <el-switch :model-value="r.enabled" :aria-label="t('common.enabled')" @change="toggle(r)" />
            <el-button link type="primary" @click="openEdit(r)">{{ t('common.edit') }}</el-button>
            <el-button link type="danger" @click="remove(r)">{{ t('common.delete') }}</el-button>
          </div>
        </li>
      </ul>
    </div>

    <el-dialog v-model="open" :title="editing ? t('common.edit') : t('risk.add')" width="460px" append-to-body>
      <el-form label-position="top" @submit.prevent="save">
        <el-form-item :label="t('risk.type')">
          <el-select v-model="form.rule_type" :disabled="!!editing" style="width: 100%">
            <el-option v-for="k in typeOptions" :key="k" :value="k" :label="t(`risk.t_${k}`)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('risk.value')" required>
          <el-input v-if="curShape?.list" v-model="form.symbols" placeholder="BTC/USDT ETH/USDT" />
          <el-input-number v-else v-model="form.num" :min="isRatio(form.rule_type) ? 1 : (curShape?.min ?? 1)"
            :max="isRatio(form.rule_type) ? 100 : (curShape?.max ?? undefined)" controls-position="right" style="width: 100%" />
          <div class="hint">
            <template v-if="curShape?.list">{{ t('risk.symbolsHint') }}</template>
            <template v-else-if="isRatio(form.rule_type)">%</template>
            <template v-else-if="isSeconds(form.rule_type)">{{ t('common.seconds') }}</template>
            <template v-else-if="form.rule_type.endsWith('_value')">USDT</template>
          </div>
        </el-form-item>
        <el-form-item v-if="!editing" :label="t('risk.scope')">
          <el-select v-model="form.account_id" clearable :placeholder="t('risk.scopeAll')" style="width: 100%">
            <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="a.name" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!editing && auth.isAdmin">
          <el-checkbox v-model="form.global_rule">{{ t('risk.globalRule') }}</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.note { color: var(--ink-3); font-size: 13px; margin: 0 0 14px; max-width: 70ch; line-height: 1.6; }
.rules { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.rule { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 14px 16px;
  background: var(--surface); border: 1px solid var(--line); border-radius: 12px; }
.rule.off { opacity: 0.6; }
.r-main { min-width: 0; }
.r-title { font-weight: 600; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.r-value { font-size: 18px; margin-top: 4px; }
.r-meta { font-size: 12px; color: var(--ink-3); margin-top: 4px; }
.r-actions { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
.hint { font-size: 12px; color: var(--ink-3); margin-top: 4px; }
</style>
