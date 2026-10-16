<script setup lang="ts">
import { onMounted, ref, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { adminApi, type AdminUser } from '@/api/admin'
import type { Account, Row } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { confirm } from '@/composables/useConfirm'
import { attempt } from '@/composables/useErrors'
import { fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import Pager from '@/components/Pager.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ErrorState from '@/components/ErrorState.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const qOwner = Number(route.query.owner_id) || ''
const tbl = useTable<Account>(adminApi.accounts, { owner_id: qOwner as number | '', status: '' })
const STATUSES = ['online', 'offline', 'abnormal', 'invalid', 'disabled']

// 按用户筛选：远程搜索用户名
const userOpts = shallowRef<AdminUser[]>([])
const userLoading = ref(false)
async function searchUsers(kw: string) {
  userLoading.value = true
  try {
    const r = await adminApi.users({ page: 1, page_size: 20, keyword: kw })
    userOpts.value = r?.items ?? []
  } catch {
    userOpts.value = []
  } finally {
    userLoading.value = false
  }
}
onMounted(async () => {
  await searchUsers('')
  if (qOwner && !userOpts.value.some((u) => u.id === qOwner)) {
    try {
      userOpts.value = [await adminApi.user(qOwner), ...userOpts.value]
    } catch {
      /* ignore */
    }
  }
})

function onFilter() {
  void router.replace({ query: { ...route.query, owner_id: tbl.filters.owner_id ? String(tbl.filters.owner_id) : undefined } })
  void tbl.search()
}
function onReset() {
  tbl.filters.owner_id = ''
  void router.replace({ query: { ...route.query, owner_id: undefined } })
  void tbl.resetFilters()
}

async function toggle(a: Row) {
  const disabling = a.status !== 'disabled'
  const msg = disabling ? t('account.forceDisableConfirm', { name: a.name }) : t('adm1.enableAccountConfirm', { name: a.name })
  if (!(await confirm(msg, disabling ? t('account.forceDisable') : t('account.enableBack'), disabling))) return
  await attempt(() => (disabling ? adminApi.disableAccount(a.id) : adminApi.enableAccount(a.id)))
  void tbl.load()
}
</script>

<template>
  <div>
    <PageHeader :title="t('account.adminTitle')" />

    <FilterBar :loading="tbl.loading.value" @search="onFilter" @reset="onReset">
      <el-select
        v-model="tbl.filters.owner_id"
        filterable
        remote
        clearable
        :remote-method="searchUsers"
        :loading="userLoading"
        :placeholder="t('adm1.byUser')"
        :aria-label="t('adm1.byUser')"
        style="width: 200px"
        @change="onFilter"
      >
        <el-option v-for="u in userOpts" :key="u.id" :label="u.username" :value="u.id" />
      </el-select>
      <el-select v-model="tbl.filters.status" clearable :placeholder="t('adm1.allStatus')" :aria-label="t('common.status')" style="width: 140px" @change="onFilter">
        <el-option v-for="s in STATUSES" :key="s" :label="t(`status.account.${s}`)" :value="s" />
      </el-select>
    </FilterBar>

    <section class="card">
      <ErrorState v-if="tbl.error.value" :error="tbl.error.value" @retry="tbl.load" />
      <template v-else>
        <div class="table-scroll">
          <el-table :data="tbl.items.value" v-loading="tbl.loading.value" size="small" class="resp-table">
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="name" :label="t('account.name')" min-width="130" show-overflow-tooltip />
            <el-table-column :label="t('account.owner')" min-width="110">
              <template #default="{ row }">
                <router-link v-if="row.owner_id" :to="`/admin/users/${row.owner_id}`">{{ row.owner_username || `#${row.owner_id}` }}</router-link>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column prop="api_key_masked" :label="t('account.apiKey')" min-width="130" />
            <el-table-column :label="t('account.env')" width="90"><template #default="{ row }">{{ t(`env.${row.env}`) }}</template></el-table-column>
            <el-table-column :label="t('account.market')" width="80"><template #default="{ row }">{{ t(`market.${row.market_type}`) }}</template></el-table-column>
            <el-table-column :label="t('account.readonly')" width="70">
              <template #default="{ row }"><el-tag v-if="row.readonly" size="small" type="info">{{ t('common.readonly') }}</el-tag></template>
            </el-table-column>
            <el-table-column :label="t('common.status')" width="90"><template #default="{ row }"><StatusBadge kind="account" :value="row.status" /></template></el-table-column>
            <el-table-column :label="t('account.lastActive')" min-width="150"><template #default="{ row }">{{ fmtTime(row.last_active_at) }}</template></el-table-column>
            <el-table-column :label="t('common.actions')" width="130" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.status !== 'disabled'" link type="danger" size="small" @click="toggle(row)">{{ t('account.forceDisable') }}</el-button>
                <el-button v-else link type="success" size="small" @click="toggle(row)">{{ t('account.enableBack') }}</el-button>
              </template>
            </el-table-column>
            <template #empty><el-empty :description="t('common.noMatch')" :image-size="64" /></template>
          </el-table>
        </div>
        <Pager v-model:page="tbl.page.value" v-model:size="tbl.pageSize.value" :total="tbl.total.value" />
      </template>
    </section>
  </div>
</template>

<style scoped>
.table-scroll { overflow-x: auto; }
</style>
