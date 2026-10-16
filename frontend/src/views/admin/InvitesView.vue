<script setup lang="ts">
import { reactive, ref, shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { adminApi, type Invite, type InviteUsage } from '@/api/admin'
import type { Role, Row } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { confirm } from '@/composables/useConfirm'
import { attempt } from '@/composables/useErrors'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useAuth } from '@/stores/auth'
import { fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import Pager from '@/components/Pager.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ErrorState from '@/components/ErrorState.vue'
import EmptyState from '@/components/EmptyState.vue'

const { t } = useI18n()
const auth = useAuth()
const { isMobile } = useBreakpoint()

const tbl = useTable<Invite>(adminApi.invites)

// 后端 active ↔ StatusBadge enabled
const statusOf = (r: Row): string => {
  const s = r.status ?? (r.enabled === false ? 'disabled' : 'active')
  return s === 'active' ? 'enabled' : s
}
const roleLabel = (r?: string) => (r === 'admin' ? t('invite.roleAdmin') : r === 'user' || !r ? t('invite.roleUser') : r)

async function copy(code: string) {
  try {
    await navigator.clipboard.writeText(code)
    ElMessage.success(t('common.copied'))
  } catch {
    ElMessage.warning(code)
  }
}

const toggling = ref<number | null>(null)
async function toggle(row: Row) {
  const enabled = row.enabled !== false && statusOf(row) !== 'disabled'
  if (enabled && !(await confirm(t('invite.disableConfirm', { code: row.code })))) return
  toggling.value = row.id
  const r = await attempt(() => (enabled ? adminApi.disableInvite(row.id) : adminApi.enableInvite(row.id)))
  toggling.value = null
  if (r !== undefined) void tbl.load()
}

// 生成
const gen = reactive({ open: false, loading: false, max_uses: 1, expires_in_days: 7 as number | undefined, role: 'user' as Role, created: null as Invite | null })
function openGen() {
  Object.assign(gen, { open: true, loading: false, max_uses: 1, expires_in_days: 7, role: 'user', created: null })
}
async function submitGen() {
  gen.loading = true
  const body = { max_uses: gen.max_uses, expires_in_days: (gen.expires_in_days ?? null) as number, role: gen.role }
  const r = await attempt(() => adminApi.createInvite(body), t('invite.created'))
  gen.loading = false
  if (r) {
    gen.created = r
    void tbl.search()
  }
}

// 使用记录
const usage = reactive({ open: false, loading: false, error: null as unknown, code: '' })
const usages = shallowRef<InviteUsage[]>([])
async function showUsages(row: Row) {
  Object.assign(usage, { open: true, loading: true, error: null, code: row.code })
  usages.value = []
  try {
    const d = await adminApi.inviteUsages(row.id)
    usages.value = Array.isArray(d) ? d : d?.items ?? []
  } catch (e) {
    usage.error = e
  } finally {
    usage.loading = false
  }
}
let lastUsageRow: Row = null
function openUsages(row: Row) {
  lastUsageRow = row
  void showUsages(row)
}
</script>

<template>
  <div>
    <PageHeader :title="t('invite.title')">
      <el-button type="primary" @click="openGen">{{ t('invite.generate') }}</el-button>
    </PageHeader>

    <section class="card">
      <ErrorState v-if="tbl.error.value && !tbl.items.value.length" :error="tbl.error.value" @retry="tbl.load" />
      <template v-else>
        <div style="overflow-x: auto">
          <el-table v-loading="tbl.loading.value" :data="tbl.items.value" class="resp-table" row-key="id">
            <el-table-column :label="t('invite.code')" min-width="150">
              <template #default="{ row }">
                <code>{{ row.code }}</code>
                <el-button link size="small" :aria-label="t('invite.copyCode')" @click="copy(row.code)">{{ t('common.copy') }}</el-button>
              </template>
            </el-table-column>
            <el-table-column :label="t('invite.role')" width="100"><template #default="{ row }">{{ roleLabel(row.role) }}</template></el-table-column>
            <el-table-column :label="`${t('invite.used')} / ${t('invite.maxUses')}`" width="120"><template #default="{ row }"><span class="num">{{ row.used_count ?? 0 }} / {{ row.max_uses }}</span></template></el-table-column>
            <el-table-column :label="t('invite.expiresAt')" min-width="150"><template #default="{ row }">{{ row.expires_at ? fmtTime(row.expires_at) : t('invite.never') }}</template></el-table-column>
            <el-table-column :label="t('invite.status')" width="100"><template #default="{ row }"><StatusBadge kind="invite" :value="statusOf(row)" /></template></el-table-column>
            <el-table-column :label="t('common.createdAt')" min-width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
            <el-table-column :label="t('common.actions')" width="170" :fixed="isMobile ? undefined : 'right'">
              <template #default="{ row }">
                <el-button size="small" link type="primary" @click="openUsages(row)">{{ t('invite.usages') }}</el-button>
                <el-button size="small" link :type="statusOf(row) === 'disabled' ? 'success' : 'danger'" :loading="toggling === row.id" @click="toggle(row)">
                  {{ statusOf(row) === 'disabled' ? t('common.enable') : t('common.disable') }}
                </el-button>
              </template>
            </el-table-column>
            <template #empty><EmptyState :action-text="t('invite.generate')" @action="openGen" /></template>
          </el-table>
        </div>
        <Pager v-model:page="tbl.page.value" v-model:size="tbl.pageSize.value" :total="tbl.total.value" />
      </template>
    </section>

    <el-dialog v-model="gen.open" :title="t('invite.generate')" :width="isMobile ? '95%' : '480px'" destroy-on-close>
      <template v-if="gen.created">
        <el-alert :title="t('invite.createdHint')" type="success" show-icon :closable="false" style="margin-bottom: 12px" />
        <div style="display: flex; gap: 8px; align-items: center">
          <el-input :model-value="gen.created.code" readonly :aria-label="t('invite.code')" />
          <el-button type="primary" @click="copy(gen.created.code)">{{ t('invite.copyCode') }}</el-button>
        </div>
      </template>
      <el-form v-else label-position="top" @submit.prevent="submitGen">
        <el-form-item :label="t('invite.maxUses')">
          <el-input-number v-model="gen.max_uses" :min="1" :max="10000" step-strictly controls-position="right" style="width: 100%" />
        </el-form-item>
        <el-form-item :label="t('invite.expiresDays')">
          <el-input-number v-model="gen.expires_in_days" :min="1" :max="365" step-strictly controls-position="right" style="width: 100%" />
          <div class="muted" style="font-size: 12px">{{ t('invite.noExpire') }}</div>
        </el-form-item>
        <el-form-item :label="t('invite.role')">
          <el-radio-group v-model="gen.role">
            <el-radio value="user">{{ roleLabel('user') }}</el-radio>
            <el-radio value="admin" :disabled="!auth.isSuper">{{ roleLabel('admin') }}</el-radio>
          </el-radio-group>
          <div v-if="!auth.isSuper" class="muted" style="font-size: 12px; width: 100%">{{ t('invite.roleAdminSuperOnly') }}</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="gen.open = false">{{ t('common.close') }}</el-button>
        <el-button v-if="!gen.created" type="primary" :loading="gen.loading" @click="submitGen">{{ t('invite.generate') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="usage.open" :title="`${t('invite.usages')} · ${usage.code}`" :width="isMobile ? '95%' : '560px'">
      <ErrorState v-if="usage.error" :error="usage.error" @retry="showUsages(lastUsageRow)" />
      <el-table v-else v-loading="usage.loading" :data="usages" size="small" class="resp-table">
        <el-table-column :label="t('invite.user')" min-width="140"><template #default="{ row }">{{ row.username }} <span class="muted">#{{ row.user_id }}</span></template></el-table-column>
        <el-table-column :label="t('invite.usedAt')" min-width="160"><template #default="{ row }">{{ fmtTime(row.used_at) }}</template></el-table-column>
        <template #empty><EmptyState /></template>
      </el-table>
    </el-dialog>
  </div>
</template>
