<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { adminApi, type AdminUser } from '@/api/admin'
import type { Role, Row } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { confirm, confirmTyped } from '@/composables/useConfirm'
import { attempt } from '@/composables/useErrors'
import { useAuth } from '@/stores/auth'
import { copyText, fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import Pager from '@/components/Pager.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ErrorState from '@/components/ErrorState.vue'

const { t } = useI18n()
const router = useRouter()
const auth = useAuth()

const tbl = useTable<AdminUser>(adminApi.users, { keyword: '', role: '', status: '' })

const ROLES: Role[] = ['user', 'admin', 'super_admin']
const STATUSES = ['active', 'disabled', 'locked', 'pending']
const roleLabel = (r?: string) => (r === 'super_admin' ? t('adm1.roleSuper') : r === 'admin' ? t('adm1.roleAdmin') : t('adm1.roleUser'))
const roleTag = (r?: string) => (r === 'super_admin' ? 'danger' : r === 'admin' ? 'warning' : 'info')
const isSelf = (u: Row) => u.id === auth.user?.id
// 普通管理员不能操作超级管理员（后端 40302）
const canManage = (u: Row) => !isSelf(u) && (auth.isSuper || u.role !== 'super_admin')

async function toggle(u: Row) {
  const disabling = u.status !== 'disabled'
  const msg = disabling ? t('admin.disableUserConfirm', { name: u.username }) : t('adm1.enableUserConfirm', { name: u.username })
  if (!(await confirm(msg, undefined, disabling))) return
  await attempt(() => (disabling ? adminApi.disableUser(u.id) : adminApi.enableUser(u.id)))
  void tbl.load()
}

const pwdOpen = ref(false)
const pwdUser = ref('')
const tempPwd = ref('')
async function resetPwd(u: Row) {
  if (!(await confirm(t('admin.resetPwdConfirm', { name: u.username }), t('admin.resetPwd'), true))) return
  const r = await attempt(() => adminApi.resetPassword(u.id), false)
  if (!r?.temp_password) return
  pwdUser.value = u.username
  tempPwd.value = r.temp_password
  pwdOpen.value = true
}
async function copyPwd() {
  if (await copyText(tempPwd.value)) ElMessage.success(t('common.copied'))
}
function closePwd() {
  tempPwd.value = ''
  pwdUser.value = ''
}

const roleOpen = ref(false)
const roleTarget = ref<AdminUser | null>(null)
const newRole = ref<Role>('user')
const roleSaving = ref(false)
function openRole(u: Row) {
  roleTarget.value = u
  newRole.value = u.role
  roleOpen.value = true
}
async function saveRole() {
  const u = roleTarget.value
  if (!u || newRole.value === u.role) {
    roleOpen.value = false
    return
  }
  roleSaving.value = true
  const ok = await attempt(() => adminApi.setRole(u.id, newRole.value).then(() => true), t('adm1.roleChanged'))
  roleSaving.value = false
  if (ok) {
    roleOpen.value = false
    void tbl.load()
  }
}

async function hardDelete(u: Row) {
  if (!(await confirmTyped(t('admin.hardDeleteConfirm', { name: u.username }), u.username, t('admin.hardDelete')))) return
  const ok = await attempt(() => adminApi.deleteUser(u.id, u.username).then(() => true), t('adm1.deleted'))
  if (ok) void tbl.load()
}

function onCommand(cmd: string, u: Row) {
  if (cmd === 'role') openRole(u)
  else if (cmd === 'delete') void hardDelete(u)
  else if (cmd === 'reset') void resetPwd(u)
}
</script>

<template>
  <div>
    <PageHeader :title="t('nav.adminUsers')" />

    <FilterBar :loading="tbl.loading.value" @search="tbl.search" @reset="tbl.resetFilters">
      <el-input
        v-model="tbl.filters.keyword"
        :placeholder="t('adm1.userSearch')"
        :aria-label="t('adm1.userSearch')"
        clearable
        style="width: 200px"
        @keyup.enter="tbl.search"
      />
      <el-select v-model="tbl.filters.role" :placeholder="t('adm1.allRoles')" :aria-label="t('admin.role')" clearable style="width: 150px" @change="tbl.search">
        <el-option v-for="r in ROLES" :key="r" :label="roleLabel(r)" :value="r" />
      </el-select>
      <el-select v-model="tbl.filters.status" :placeholder="t('adm1.allStatus')" :aria-label="t('common.status')" clearable style="width: 140px" @change="tbl.search">
        <el-option v-for="s in STATUSES" :key="s" :label="t(`status.user.${s}`)" :value="s" />
      </el-select>
    </FilterBar>

    <section class="card">
      <ErrorState v-if="tbl.error.value" :error="tbl.error.value" @retry="tbl.load" />
      <template v-else>
        <div class="table-scroll">
          <el-table :data="tbl.items.value" v-loading="tbl.loading.value" class="resp-table" size="small">
            <el-table-column prop="id" :label="t('admin.userId')" width="80" />
            <el-table-column :label="t('adm1.username')" min-width="130">
              <template #default="{ row }">
                <router-link :to="`/admin/users/${row.id}`">{{ row.username }}</router-link>
                <el-tag v-if="isSelf(row)" size="small" class="ml">{{ t('adm1.self') }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('adm1.nickname')" min-width="110" show-overflow-tooltip>
              <template #default="{ row }">{{ row.nickname || '-' }}</template>
            </el-table-column>
            <el-table-column :label="t('admin.role')" width="120">
              <template #default="{ row }"><el-tag :type="roleTag(row.role)" size="small">{{ roleLabel(row.role) }}</el-tag></template>
            </el-table-column>
            <el-table-column :label="t('common.status')" width="100">
              <template #default="{ row }"><StatusBadge kind="user" :value="row.status" /></template>
            </el-table-column>
            <el-table-column :label="t('admin.accountCount')" width="80" align="right">
              <template #default="{ row }">
                <router-link :to="{ path: '/admin/accounts', query: { owner_id: row.id } }">{{ row.account_count ?? 0 }}</router-link>
              </template>
            </el-table-column>
            <el-table-column :label="t('admin.instanceCount')" width="80" align="right">
              <template #default="{ row }">
                <router-link :to="{ path: '/admin/instances', query: { owner_id: row.id } }">{{ row.instance_count ?? 0 }}</router-link>
              </template>
            </el-table-column>
            <el-table-column :label="t('admin.bindingCount')" width="100" align="right">
              <template #default="{ row }">{{ row.binding_count ?? 0 }}</template>
            </el-table-column>
            <el-table-column :label="t('admin.registeredAt')" min-width="150">
              <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column :label="t('admin.lastLogin')" min-width="150">
              <template #default="{ row }">{{ fmtTime(row.last_login_at) }}</template>
            </el-table-column>
            <el-table-column :label="t('common.actions')" width="230" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="router.push(`/admin/users/${row.id}`)">{{ t('common.view') }}</el-button>
                <el-tooltip :disabled="canManage(row)" :content="t('adm1.selfHint')">
                  <span>
                    <el-button
                      link
                      size="small"
                      :type="row.status === 'disabled' ? 'success' : 'danger'"
                      :disabled="!canManage(row)"
                      @click="toggle(row)"
                    >{{ row.status === 'disabled' ? t('admin.enableUser') : t('admin.disableUser') }}</el-button>
                  </span>
                </el-tooltip>
                <el-dropdown trigger="click" :disabled="!canManage(row)" @command="(c: string) => onCommand(c, row)">
                  <el-button link size="small" :disabled="!canManage(row)" :aria-label="t('common.more')">{{ t('common.more') }}</el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="reset">{{ t('admin.resetPwd') }}</el-dropdown-item>
                      <el-dropdown-item v-if="auth.isSuper" command="role">{{ t('admin.changeRole') }}</el-dropdown-item>
                      <el-dropdown-item v-if="auth.isSuper" command="delete" divided class="danger-text">{{ t('admin.hardDelete') }}</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </template>
            </el-table-column>
            <template #empty><el-empty :description="t('common.noMatch')" :image-size="64" /></template>
          </el-table>
        </div>
        <Pager v-model:page="tbl.page.value" v-model:size="tbl.pageSize.value" :total="tbl.total.value" />
      </template>
    </section>
    <el-dialog v-model="pwdOpen" :title="t('admin.tempPassword')" width="440px" :close-on-click-modal="false" @closed="closePwd">
      <p>{{ pwdUser }}</p>
      <el-input :model-value="tempPwd" readonly :aria-label="t('admin.tempPassword')" class="mono">
        <template #append><el-button @click="copyPwd">{{ t('common.copy') }}</el-button></template>
      </el-input>
      <el-alert type="warning" :closable="false" show-icon class="mt" :title="t('adm1.tempPwdHint')" />
      <template #footer><el-button type="primary" @click="pwdOpen = false">{{ t('common.close') }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="roleOpen" :title="t('admin.changeRole')" width="400px">
      <el-form label-position="top" @submit.prevent="saveRole">
        <el-form-item :label="t('adm1.username')">{{ roleTarget?.username }}</el-form-item>
        <el-form-item :label="t('adm1.newRole')" for="role-select">
          <el-radio-group id="role-select" v-model="newRole">
            <el-radio v-for="r in ROLES" :key="r" :value="r">{{ roleLabel(r) }}</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="roleOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="roleSaving" @click="saveRole">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.table-scroll { overflow-x: auto; }
.ml { margin-left: 6px; }
.mt { margin-top: 12px; }
.mono :deep(input) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
</style>
