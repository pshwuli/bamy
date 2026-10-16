<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { adminApi, type AdminUser } from '@/api/admin'
import { adminExtApi } from '@/api/extensions'
import type { Account, Binding, Instance, Query } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { confirm } from '@/composables/useConfirm'
import { attempt } from '@/composables/useErrors'
import { useAuth } from '@/stores/auth'
import { fmtTime, fmtSymbol } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import Pager from '@/components/Pager.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ErrorState from '@/components/ErrorState.vue'
import AuditTable from '@/components/AuditTable.vue'

const { t } = useI18n()
const route = useRoute()
const auth = useAuth()

const id = computed(() => Number(route.params.id))
const user = ref<(AdminUser & { order_count?: number; last_login_ip?: string | null }) | null>(null)
const loading = ref(true)
const error = ref<unknown>(null)
const tab = ref('accounts')

async function loadUser() {
  loading.value = true
  error.value = null
  try {
    user.value = await adminApi.user(id.value)
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

const withOwner = (q: Query): Query => ({ ...q, owner_id: id.value })
const accTbl = useTable<Account>(adminApi.accounts, {}, { transform: withOwner, immediate: false })
const instTbl = useTable<Instance>(adminApi.instances, {}, { transform: withOwner, immediate: false })
const bindTbl = useTable<Binding>(adminExtApi.allBindings, {}, { transform: withOwner, immediate: false })
const loaded = new Set<string>()

function loadTab(name: string) {
  if (loaded.has(name)) return
  loaded.add(name)
  if (name === 'accounts') void accTbl.load()
  else if (name === 'instances' || name === 'orders') void instTbl.load()
  else if (name === 'bindings') void bindTbl.load()
}

watch(
  id,
  () => {
    loaded.clear()
    void loadUser()
    loadTab(tab.value)
  },
  { immediate: true },
)
watch(tab, loadTab)

const roleLabel = (r?: string) => (r === 'super_admin' ? t('adm1.roleSuper') : r === 'admin' ? t('adm1.roleAdmin') : t('adm1.roleUser'))
const canManage = computed(() => !!user.value && user.value.id !== auth.user?.id && (auth.isSuper || user.value.role !== 'super_admin'))

async function toggle() {
  const u = user.value
  if (!u) return
  const disabling = u.status !== 'disabled'
  const msg = disabling ? t('admin.disableUserConfirm', { name: u.username }) : t('adm1.enableUserConfirm', { name: u.username })
  if (!(await confirm(msg, undefined, disabling))) return
  await attempt(() => (disabling ? adminApi.disableUser(u.id) : adminApi.enableUser(u.id)))
  void loadUser()
}

const auditFixed = computed<Query>(() => ({ actor_id: id.value }))
</script>

<template>
  <div>
    <PageHeader :title="user ? `${t('admin.userDetail')} · ${user.username}` : t('admin.userDetail')" back>
      <el-button v-if="user && canManage" :type="user.status === 'disabled' ? 'success' : 'danger'" plain @click="toggle">
        {{ user.status === 'disabled' ? t('admin.enableUser') : t('admin.disableUser') }}
      </el-button>
    </PageHeader>

    <ErrorState v-if="error" :error="error" @retry="loadUser" />
    <template v-else>
      <section class="card" :aria-busy="loading">
        <h2 class="card-title">{{ t('admin.basicInfo') }}</h2>
        <el-skeleton v-if="loading && !user" :rows="4" animated />
        <el-descriptions v-else-if="user" :column="3" border size="small" class="desc">
          <el-descriptions-item :label="t('admin.userId')">{{ user.id }}</el-descriptions-item>
          <el-descriptions-item :label="t('adm1.username')">{{ user.username }}</el-descriptions-item>
          <el-descriptions-item :label="t('adm1.nickname')">{{ user.nickname || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('adm1.email')">{{ user.email || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('admin.role')">{{ roleLabel(user.role) }}</el-descriptions-item>
          <el-descriptions-item :label="t('common.status')"><StatusBadge kind="user" :value="user.status" /></el-descriptions-item>
          <el-descriptions-item :label="t('adm1.twoFactor')">{{ user.two_factor_enabled ? t('common.yes') : t('common.no') }}</el-descriptions-item>
          <el-descriptions-item :label="t('admin.registeredAt')">{{ fmtTime(user.created_at) }}</el-descriptions-item>
          <el-descriptions-item :label="t('admin.lastLogin')">{{ fmtTime(user.last_login_at) }}</el-descriptions-item>
          <el-descriptions-item :label="t('adm1.lastLoginIp')">{{ user.last_login_ip || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('admin.accountCount')">{{ user.account_count ?? 0 }}</el-descriptions-item>
          <el-descriptions-item :label="t('admin.instanceCount')">{{ user.instance_count ?? 0 }}</el-descriptions-item>
          <el-descriptions-item :label="t('admin.bindingCount')">{{ user.binding_count ?? 0 }}</el-descriptions-item>
          <el-descriptions-item :label="t('adm1.orderCount')">{{ user.order_count ?? '-' }}</el-descriptions-item>
        </el-descriptions>
      </section>
      <section v-if="user" class="card">
        <el-tabs v-model="tab">
          <el-tab-pane :label="t('adm1.tabAccounts')" name="accounts">
            <ErrorState v-if="accTbl.error.value" :error="accTbl.error.value" @retry="accTbl.load" />
            <template v-else>
              <div class="table-scroll">
                <el-table :data="accTbl.items.value" v-loading="accTbl.loading.value" size="small" class="resp-table">
                  <el-table-column prop="name" :label="t('account.name')" min-width="120" show-overflow-tooltip />
                  <el-table-column prop="api_key_masked" :label="t('account.apiKey')" min-width="130" />
                  <el-table-column :label="t('account.env')" width="90"><template #default="{ row }">{{ t(`env.${row.env}`) }}</template></el-table-column>
                  <el-table-column :label="t('account.readonly')" width="70">
                    <template #default="{ row }"><el-tag v-if="row.readonly" size="small" type="info">{{ t('common.readonly') }}</el-tag></template>
                  </el-table-column>
                  <el-table-column :label="t('common.status')" width="90"><template #default="{ row }"><StatusBadge kind="account" :value="row.status" /></template></el-table-column>
                  <el-table-column :label="t('account.lastActive')" min-width="150"><template #default="{ row }">{{ fmtTime(row.last_active_at) }}</template></el-table-column>
                  <template #empty><el-empty :description="t('common.empty')" :image-size="48" /></template>
                </el-table>
              </div>
              <Pager v-model:page="accTbl.page.value" v-model:size="accTbl.pageSize.value" :total="accTbl.total.value" />
            </template>
          </el-tab-pane>

          <el-tab-pane :label="t('adm1.tabInstances')" name="instances">
            <ErrorState v-if="instTbl.error.value" :error="instTbl.error.value" @retry="instTbl.load" />
            <template v-else>
              <div class="table-scroll">
                <el-table :data="instTbl.items.value" v-loading="instTbl.loading.value" size="small" class="resp-table">
                  <el-table-column :label="t('instance.name')" min-width="130">
                    <template #default="{ row }"><router-link :to="`/instances/${row.id}`">{{ row.name }}</router-link></template>
                  </el-table-column>
                  <el-table-column :label="t('instance.strategy')" min-width="110" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.strategy_name || row.strategy_id }}</template>
                  </el-table-column>
                  <el-table-column :label="t('instance.symbol')" min-width="90"><template #default="{ row }">{{ fmtSymbol(row.symbol) }}</template></el-table-column>
                  <el-table-column :label="t('instance.account')" min-width="100" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.paper ? t('common.paper') : row.account_name || '-' }}</template>
                  </el-table-column>
                  <el-table-column :label="t('common.status')" width="90"><template #default="{ row }"><StatusBadge kind="instance" :value="row.status" /></template></el-table-column>
                  <el-table-column :label="t('common.createdAt')" min-width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
                  <template #empty><el-empty :description="t('common.empty')" :image-size="48" /></template>
                </el-table>
              </div>
              <Pager v-model:page="instTbl.page.value" v-model:size="instTbl.pageSize.value" :total="instTbl.total.value" />
            </template>
          </el-tab-pane>

          <el-tab-pane :label="t('adm1.tabOrders')" name="orders">
            <el-alert type="info" :closable="false" show-icon :title="t('adm1.ordersNote')" class="mb" />
            <ul v-if="instTbl.items.value.length" class="link-list">
              <li v-for="i in instTbl.items.value" :key="i.id">
                <router-link :to="{ path: `/instances/${i.id}`, query: { tab: 'orders' } }">{{ i.name }} · {{ fmtSymbol(i.symbol) }}</router-link>
                <StatusBadge kind="instance" :value="i.status" />
              </li>
            </ul>
            <el-empty v-else-if="!instTbl.loading.value" :description="t('common.empty')" :image-size="48" />
          </el-tab-pane>

          <el-tab-pane :label="t('adm1.tabBindings')" name="bindings">
            <ErrorState v-if="bindTbl.error.value" :error="bindTbl.error.value" @retry="bindTbl.load" />
            <template v-else>
              <div class="table-scroll">
                <el-table :data="bindTbl.items.value" v-loading="bindTbl.loading.value" size="small" class="resp-table">
                  <el-table-column prop="id" label="ID" width="70" />
                  <el-table-column :label="t('adm1.plugin')" min-width="130" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.plugin_name || row.plugin_id }}</template>
                  </el-table-column>
                  <el-table-column :label="t('adm1.scope')" min-width="120">
                    <template #default="{ row }">{{ t(`scope.${row.scope}`) }}{{ row.scope_name ? ` · ${row.scope_name}` : '' }}</template>
                  </el-table-column>
                  <el-table-column prop="priority" :label="t('adm1.priority')" width="80" align="right" />
                  <el-table-column :label="t('common.status')" width="110">
                    <template #default="{ row }"><StatusBadge kind="binding" :value="row.status" :auto-disabled="row.auto_disabled" /></template>
                  </el-table-column>
                  <el-table-column :label="t('common.createdAt')" min-width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
                  <template #empty><el-empty :description="t('common.empty')" :image-size="48" /></template>
                </el-table>
              </div>
              <Pager v-model:page="bindTbl.page.value" v-model:size="bindTbl.pageSize.value" :total="bindTbl.total.value" />
            </template>
          </el-tab-pane>

          <el-tab-pane :label="t('adm1.tabAudit')" name="audit" lazy>
            <AuditTable :fetcher="adminApi.auditLogs" :detail-fetcher="(lid: number) => adminApi.auditLog(lid)" :fixed="auditFixed" />
          </el-tab-pane>
        </el-tabs>
      </section>
    </template>
  </div>
</template>

<style scoped>
.desc :deep(.el-descriptions__table) { table-layout: fixed; }
.table-scroll { overflow-x: auto; }
.mb { margin-bottom: 12px; }
.link-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.link-list li { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
@media (max-width: 767px) {
  .desc :deep(.el-descriptions__body .el-descriptions__table) { table-layout: auto; }
}
</style>
