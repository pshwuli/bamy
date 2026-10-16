<script setup lang="ts">
import { onMounted, ref, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { adminApi, type AdminUser } from '@/api/admin'
import { itemsOf, strategiesApi } from '@/api/extensions'
import type { Instance, InstanceStatus, Row, Strategy } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { useLive } from '@/composables/useLive'
import { confirm } from '@/composables/useConfirm'
import { attempt } from '@/composables/useErrors'
import { fmtMoney, fmtTime, fmtSymbol } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import Pager from '@/components/Pager.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ErrorState from '@/components/ErrorState.vue'

type InstanceX = Instance & { last_started_at?: string | null }

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const qOwner = Number(route.query.owner_id) || ''
const qStrategy = typeof route.query.strategy_id === 'string' ? route.query.strategy_id : ''
const tbl = useTable<InstanceX>(adminApi.instances, {
  owner_id: qOwner as number | '',
  strategy_id: qStrategy,
  status: '',
  paper: '' as boolean | '',
})

const STATUSES: InstanceStatus[] = ['created', 'running', 'paused', 'stopped', 'error']
const STOPPABLE = new Set(['running', 'paused', 'starting', 'pausing', 'resuming', 'error'])

const userOpts = shallowRef<AdminUser[]>([])
const userLoading = ref(false)
const strategies = shallowRef<Strategy[]>([])
async function searchUsers(kw: string) {
  userLoading.value = true
  try {
    userOpts.value = (await adminApi.users({ page: 1, page_size: 20, keyword: kw }))?.items ?? []
  } catch {
    userOpts.value = []
  } finally {
    userLoading.value = false
  }
}
onMounted(async () => {
  void strategiesApi.list({ page: 1, page_size: 200 }).then((r) => (strategies.value = itemsOf(r))).catch(() => undefined)
  await searchUsers('')
  if (qOwner && !userOpts.value.some((u) => u.id === qOwner)) {
    try {
      userOpts.value = [await adminApi.user(qOwner), ...userOpts.value]
    } catch {
      /* ignore */
    }
  }
})

function syncQuery() {
  const f = tbl.filters
  void router.replace({
    query: {
      ...route.query,
      owner_id: f.owner_id ? String(f.owner_id) : undefined,
      strategy_id: f.strategy_id || undefined,
    },
  })
}
function onFilter() {
  syncQuery()
  void tbl.search()
}
function onReset() {
  tbl.filters.owner_id = ''
  tbl.filters.strategy_id = ''
  void router.replace({ query: { ...route.query, owner_id: undefined, strategy_id: undefined } })
  void tbl.resetFilters()
}

async function forceStop(i: Row) {
  if (!(await confirm(t('instance.forceStopConfirm', { name: i.name }), t('instance.forceStop'), true))) return
  await attempt(() => adminApi.forceStop(i.id))
  void tbl.load()
}

function runtime(i: Row) {
  const since = i.running_since || (i.status === 'running' ? i.last_started_at : null)
  if (!since) return '-'
  const s = Math.max(0, (Date.now() - new Date(since).getTime()) / 1000)
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`
}

useLive({
  events: ['instance.status', 'instance.error'],
  onEvent: (m) => {
    const d = m.data as { instance_id?: number; status?: InstanceStatus }
    if (!d.instance_id) return
    tbl.items.value = tbl.items.value.map((i) => (i.id === d.instance_id ? { ...i, status: d.status ?? i.status } : i))
  },
  poll: tbl.load,
})
</script>

<template>
  <div>
    <PageHeader :title="t('instance.adminTitle')" />

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
        style="width: 180px"
        @change="onFilter"
      >
        <el-option v-for="u in userOpts" :key="u.id" :label="u.username" :value="u.id" />
      </el-select>
      <el-select v-model="tbl.filters.strategy_id" filterable clearable :placeholder="t('adm1.byStrategy')" :aria-label="t('adm1.byStrategy')" style="width: 180px" @change="onFilter">
        <el-option v-if="qStrategy && !strategies.some((s) => s.id === qStrategy)" :label="qStrategy" :value="qStrategy" />
        <el-option v-for="s in strategies" :key="s.id" :label="s.name || s.id" :value="s.id" />
      </el-select>
      <el-select v-model="tbl.filters.status" clearable :placeholder="t('adm1.allStatus')" :aria-label="t('common.status')" style="width: 130px" @change="onFilter">
        <el-option v-for="s in STATUSES" :key="s" :label="t(`status.instance.${s}`)" :value="s" />
      </el-select>
      <el-select v-model="tbl.filters.paper" clearable :placeholder="t('adm1.allModes')" :aria-label="t('instance.mode')" style="width: 120px" @change="onFilter">
        <el-option :label="t('common.paper')" :value="true" />
        <el-option :label="t('common.live')" :value="false" />
      </el-select>
    </FilterBar>

    <section class="card">
      <ErrorState v-if="tbl.error.value" :error="tbl.error.value" @retry="tbl.load" />
      <template v-else>
        <div class="table-scroll">
          <el-table :data="tbl.items.value" v-loading="tbl.loading.value" size="small" class="resp-table">
            <el-table-column :label="t('instance.name')" min-width="140">
              <template #default="{ row }"><router-link :to="`/instances/${row.id}`">{{ row.name }}</router-link></template>
            </el-table-column>
            <el-table-column :label="t('instance.owner')" min-width="110">
              <template #default="{ row }">
                <router-link v-if="row.owner_id" :to="`/admin/users/${row.owner_id}`">{{ row.owner_username || `#${row.owner_id}` }}</router-link>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('instance.strategy')" min-width="120" show-overflow-tooltip>
              <template #default="{ row }">{{ row.strategy_name || row.strategy_id }}</template>
            </el-table-column>
            <el-table-column :label="t('instance.symbol')" min-width="90"><template #default="{ row }">{{ fmtSymbol(row.symbol) }}</template></el-table-column>
            <el-table-column :label="t('instance.account')" min-width="110" show-overflow-tooltip>
              <template #default="{ row }">{{ row.paper ? t('common.paper') : row.account_name || '-' }}</template>
            </el-table-column>
            <el-table-column :label="t('instance.mode')" width="80">
              <template #default="{ row }"><el-tag size="small" :type="row.paper ? 'info' : 'warning'">{{ row.paper ? t('common.paper') : t('common.live') }}</el-tag></template>
            </el-table-column>
            <el-table-column :label="t('common.status')" width="90"><template #default="{ row }"><StatusBadge kind="instance" :value="row.status" /></template></el-table-column>
            <el-table-column :label="t('instance.equity')" min-width="100" align="right"><template #default="{ row }">{{ fmtMoney(row.equity) }}</template></el-table-column>
            <el-table-column :label="t('instance.runtime')" width="90"><template #default="{ row }">{{ runtime(row) }}</template></el-table-column>
            <el-table-column :label="t('adm1.lastStarted')" min-width="150">
              <template #default="{ row }">{{ fmtTime(row.last_started_at || row.last_active_at) }}</template>
            </el-table-column>
            <el-table-column :label="t('common.actions')" width="150" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="router.push(`/instances/${row.id}`)">{{ t('common.view') }}</el-button>
                <el-button v-if="STOPPABLE.has(row.status)" link type="danger" size="small" @click="forceStop(row)">{{ t('instance.forceStop') }}</el-button>
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
