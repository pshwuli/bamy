<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { FormInstance, FormRules } from 'element-plus'
import { adminApi, type CircuitBreaker } from '@/api/admin'
import type { Row } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { confirm, promptText } from '@/composables/useConfirm'
import { attempt } from '@/composables/useErrors'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useLive } from '@/composables/useLive'
import { useAuth } from '@/stores/auth'
import { fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import Pager from '@/components/Pager.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ErrorState from '@/components/ErrorState.vue'
import EmptyState from '@/components/EmptyState.vue'

const { t } = useI18n()
const auth = useAuth()
const { isMobile } = useBreakpoint()

const tbl = useTable<CircuitBreaker>(adminApi.circuitBreakers, { level: '', active: '' as boolean | '' })
useLive({ events: ['risk.circuit_break', 'risk.circuit_recover'], onEvent: () => void tbl.load() })

const levelLabel = (l: string) => ({ account: t('breaker.levelAccount'), user: t('breaker.levelUser'), global: t('breaker.levelGlobal') })[l] ?? l
const statusOf = (r: Row): string => r.status ?? (r.recovered_at ? 'recovered' : r.active === false ? 'recovered' : 'active')
const targetOf = (r: Row) => (r.level === 'global' ? '-' : r.target_name ? `${r.target_name} (#${r.target_id})` : r.target_id != null ? `#${r.target_id}` : '-')
const canRecover = (r: Row) => statusOf(r) === 'active' && (r.level !== 'global' || auth.isSuper)

const recovering = ref<number | null>(null)
async function recover(row: Row) {
  const reason = await promptText(t('breaker.recoverConfirm'), `${t('breaker.recover')} · ${levelLabel(row.level)} ${targetOf(row)}`)
  if (reason === null) return
  recovering.value = row.id
  const r = await attempt(() => adminApi.recoverBreaker(row.id, reason.trim()), t('breaker.recovered'))
  recovering.value = null
  if (r !== undefined) void tbl.load()
}

// 手动触发
const formRef = ref<FormInstance>()
const dlg = reactive({ open: false, loading: false })
const form = reactive({ level: 'account' as 'account' | 'user' | 'global', target_id: undefined as number | undefined, reason: '' })
const rules: FormRules = {
  target_id: [{ validator: (_r, v, cb) => (form.level === 'global' || (v && v > 0) ? cb() : cb(new Error(t('auth.required')))), trigger: 'blur' }],
  reason: [{ required: true, whitespace: true, message: t('auth.required'), trigger: 'blur' }],
}
function openTrigger() {
  Object.assign(form, { level: 'account', target_id: undefined, reason: '' })
  dlg.open = true
}
async function submit() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  if (!(await confirm(t('breaker.triggerConfirm'), t('breaker.trigger'), true))) return
  dlg.loading = true
  const r = await attempt(
    () => adminApi.triggerBreaker({ level: form.level, target_id: form.level === 'global' ? null : form.target_id ?? null, reason: form.reason.trim() }),
    t('breaker.triggered'),
  )
  dlg.loading = false
  if (r !== undefined) {
    dlg.open = false
    void tbl.search()
  }
}
</script>

<template>
  <div>
    <PageHeader :title="t('breaker.title')" :subtitle="t('breaker.note')">
      <el-button type="danger" @click="openTrigger">{{ t('breaker.trigger') }}</el-button>
    </PageHeader>

    <el-alert :title="t('breaker.note')" type="info" show-icon :closable="false" style="margin-bottom: 12px" />

    <FilterBar :loading="tbl.loading.value" @search="tbl.search" @reset="tbl.resetFilters">
      <el-select v-model="tbl.filters.level" clearable :placeholder="t('breaker.level')" :aria-label="t('breaker.level')" style="width: 130px">
        <el-option :label="t('breaker.levelAccount')" value="account" />
        <el-option :label="t('breaker.levelUser')" value="user" />
        <el-option :label="t('breaker.levelGlobal')" value="global" />
      </el-select>
      <el-select v-model="tbl.filters.active" clearable :placeholder="t('breaker.statusFilter')" :aria-label="t('breaker.statusFilter')" style="width: 130px">
        <el-option :label="t('breaker.activeOnly')" :value="true" />
        <el-option :label="t('breaker.recoveredOnly')" :value="false" />
      </el-select>
    </FilterBar>

    <section class="card">
      <ErrorState v-if="tbl.error.value && !tbl.items.value.length" :error="tbl.error.value" @retry="tbl.load" />
      <template v-else>
        <div style="overflow-x: auto">
          <el-table v-loading="tbl.loading.value" :data="tbl.items.value" class="resp-table" row-key="id">
            <el-table-column :label="t('breaker.level')" width="90">
              <template #default="{ row }"><el-tag :type="row.level === 'global' ? 'danger' : 'warning'" size="small">{{ levelLabel(row.level) }}</el-tag></template>
            </el-table-column>
            <el-table-column :label="t('breaker.target')" min-width="140" show-overflow-tooltip><template #default="{ row }">{{ targetOf(row) }}</template></el-table-column>
            <el-table-column prop="reason" :label="t('breaker.reason')" min-width="200" show-overflow-tooltip />
            <el-table-column :label="t('breaker.triggeredAt')" min-width="160"><template #default="{ row }">{{ fmtTime(row.triggered_at ?? row.created_at) }}</template></el-table-column>
            <el-table-column :label="t('common.status')" min-width="160">
              <template #default="{ row }">
                <StatusBadge kind="breaker" :value="statusOf(row)" />
                <div v-if="row.recovered_at" class="muted" style="font-size: 12px">{{ fmtTime(row.recovered_at) }}<template v-if="row.recover_reason"> · {{ row.recover_reason }}</template></div>
              </template>
            </el-table-column>
            <el-table-column :label="t('common.actions')" width="110" :fixed="isMobile ? undefined : 'right'">
              <template #default="{ row }">
                <el-button v-if="canRecover(row)" size="small" type="primary" link :loading="recovering === row.id" @click="recover(row)">{{ t('breaker.recover') }}</el-button>
                <el-tooltip v-else-if="statusOf(row) === 'active'" :content="t('breaker.globalSuperOnly')"><span class="muted">-</span></el-tooltip>
                <span v-else class="muted">-</span>
              </template>
            </el-table-column>
            <template #empty><EmptyState /></template>
          </el-table>
        </div>
        <Pager v-model:page="tbl.page.value" v-model:size="tbl.pageSize.value" :total="tbl.total.value" />
      </template>
    </section>

    <el-dialog v-model="dlg.open" :title="t('breaker.trigger')" :width="isMobile ? '95%' : '520px'" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
        <el-form-item :label="t('breaker.level')" prop="level">
          <el-radio-group v-model="form.level">
            <el-radio value="account">{{ t('breaker.levelAccount') }}</el-radio>
            <el-radio value="user">{{ t('breaker.levelUser') }}</el-radio>
            <el-radio value="global" :disabled="!auth.isSuper">{{ t('breaker.levelGlobal') }}</el-radio>
          </el-radio-group>
          <div v-if="!auth.isSuper" class="muted" style="font-size: 12px; width: 100%">{{ t('breaker.globalSuperOnly') }}</div>
        </el-form-item>
        <el-form-item v-if="form.level !== 'global'" :label="t('breaker.targetId')" prop="target_id">
          <el-input-number v-model="form.target_id" :min="1" :step="1" step-strictly controls-position="right" style="width: 100%" />
          <div class="muted" style="font-size: 12px">{{ t('breaker.targetIdHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('breaker.reason')" prop="reason">
          <el-input v-model="form.reason" type="textarea" :rows="3" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="dlg.loading" @click="submit">{{ t('breaker.trigger') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>
