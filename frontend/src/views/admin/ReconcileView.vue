<script setup lang="ts">
import { onMounted, reactive, ref, shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { adminApi, type ReconcileDiff } from '@/api/admin'
import type { Account, Row } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { promptText } from '@/composables/useConfirm'
import { attempt } from '@/composables/useErrors'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import Pager from '@/components/Pager.vue'
import JsonView from '@/components/JsonView.vue'
import ErrorState from '@/components/ErrorState.vue'
import EmptyState from '@/components/EmptyState.vue'

const { t, te } = useI18n()
const { isMobile } = useBreakpoint()

const accounts = shallowRef<Account[]>([])
onMounted(async () => {
  try {
    accounts.value = (await adminApi.accounts({ page: 1, page_size: 200 })).items ?? []
  } catch {
    accounts.value = []
  }
})

const range = ref<[string, string] | null>(null)
const tbl = useTable<ReconcileDiff>(adminApi.reconcileDiffs, { account_id: '' as number | '', diff_type: '', resolved: '' as boolean | '' }, {
  transform: (q) => ({ ...q, start_time: range.value?.[0], end_time: range.value?.[1] }),
})
function reset() {
  range.value = null
  void tbl.resetFilters()
}

const typeOf = (r: Row): string => r.diff_type ?? r.type ?? ''
function typeLabel(v: string) {
  const map: Record<string, string> = { order: 'reconcile.typeLabelOrder', orders: 'reconcile.typeLabelOrder', position: 'reconcile.typeLabelPosition', positions: 'reconcile.typeLabelPosition', balance: 'reconcile.typeLabelBalance' }
  return map[v] && te(map[v]) ? t(map[v]) : v || '-'
}
const accountLabel = (r: Row) => r.account_name || (r.account_id != null ? `#${r.account_id}` : '-')

// 触发对账
const trig = reactive({ open: false, loading: false, account_id: null as number | null, type: 'all', result: undefined as unknown, done: false })
function openTrigger() {
  Object.assign(trig, { open: true, loading: false, account_id: null, type: 'all', result: undefined, done: false })
}
async function submitTrigger() {
  trig.loading = true
  const r = await attempt(() => adminApi.reconcileTrigger({ account_id: trig.account_id ?? null, type: trig.type }), t('reconcile.triggered'))
  trig.loading = false
  if (r !== undefined) {
    trig.result = r
    trig.done = true
    void tbl.load()
  }
}

const resolving = ref<number | null>(null)

async function resolve(row: Row) {
  const note = await promptText(t('reconcile.noteOptional'), t('reconcile.resolve'), false)
  if (note === null) return
  resolving.value = row.id
  const ok = await attempt(() => adminApi.reconcileResolve(row.id, note.trim() || undefined))
  resolving.value = null
  if (ok !== undefined) void tbl.load()
}
</script>

<template>
  <div>
    <PageHeader :title="t('reconcile.title')">
      <el-button type="primary" @click="openTrigger">{{ t('reconcile.trigger') }}</el-button>
    </PageHeader>

    <FilterBar :loading="tbl.loading.value" @search="tbl.search" @reset="reset">
      <el-select v-model="tbl.filters.account_id" clearable filterable :placeholder="t('reconcile.account')" :aria-label="t('reconcile.account')" style="width: 180px">
        <el-option v-for="a in accounts" :key="a.id" :label="`${a.name} (#${a.id})`" :value="a.id" />
      </el-select>
      <el-select v-model="tbl.filters.diff_type" clearable :placeholder="t('reconcile.type')" :aria-label="t('reconcile.type')" style="width: 120px">
        <el-option :label="t('reconcile.typeLabelOrder')" value="order" />
        <el-option :label="t('reconcile.typeLabelPosition')" value="position" />
        <el-option :label="t('reconcile.typeLabelBalance')" value="balance" />
      </el-select>
      <el-select v-model="tbl.filters.resolved" clearable :placeholder="t('reconcile.resolved')" :aria-label="t('reconcile.resolved')" style="width: 120px">
        <el-option :label="t('common.yes')" :value="true" />
        <el-option :label="t('common.no')" :value="false" />
      </el-select>
      <el-date-picker
        v-model="range"
        type="datetimerange"
        value-format="YYYY-MM-DDTHH:mm:ss"
        :start-placeholder="t('reconcile.timeRange')"
        :end-placeholder="t('reconcile.timeRange')"
        :aria-label="t('reconcile.timeRange')"
        style="max-width: 360px"
      />
    </FilterBar>

    <section class="card">
      <ErrorState v-if="tbl.error.value && !tbl.items.value.length" :error="tbl.error.value" @retry="tbl.load" />
      <template v-else>
        <div style="overflow-x: auto">
          <el-table v-loading="tbl.loading.value" :data="tbl.items.value" class="resp-table" row-key="id">
            <el-table-column :label="t('common.time')" min-width="160"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
            <el-table-column :label="t('reconcile.account')" min-width="130" show-overflow-tooltip><template #default="{ row }">{{ accountLabel(row) }}</template></el-table-column>
            <el-table-column :label="t('reconcile.type')" width="90"><template #default="{ row }">{{ typeLabel(typeOf(row)) }}</template></el-table-column>
            <el-table-column :label="t('reconcile.expected')" min-width="200">
              <template #default="{ row }">
                <el-collapse><el-collapse-item :title="t('common.expand')"><JsonView :value="row.expected" max-height="240px" /></el-collapse-item></el-collapse>
              </template>
            </el-table-column>
            <el-table-column :label="t('reconcile.actual')" min-width="200">
              <template #default="{ row }">
                <el-collapse><el-collapse-item :title="t('common.expand')"><JsonView :value="row.actual" max-height="240px" /></el-collapse-item></el-collapse>
              </template>
            </el-table-column>
            <el-table-column :label="t('reconcile.resolved')" width="150">
              <template #default="{ row }">
                <el-tag v-if="row.resolved" type="success" size="small">{{ t('common.yes') }}</el-tag>
                <el-tag v-else type="warning" size="small">{{ t('reconcile.unresolved') }}</el-tag>
                <div v-if="row.resolved_at" class="muted" style="font-size: 12px">{{ fmtTime(row.resolved_at) }}</div>
              </template>
            </el-table-column>
            <el-table-column :label="t('common.actions')" width="130" :fixed="isMobile ? undefined : 'right'">
              <template #default="{ row }">
                <el-button v-if="!row.resolved" size="small" type="primary" link :loading="resolving === row.id" @click="resolve(row)">{{ t('reconcile.resolve') }}</el-button>
                <span v-else class="muted">-</span>
              </template>
            </el-table-column>
            <template #empty><EmptyState /></template>
          </el-table>
        </div>
        <Pager v-model:page="tbl.page.value" v-model:size="tbl.pageSize.value" :total="tbl.total.value" />
      </template>
    </section>

    <el-dialog v-model="trig.open" :title="t('reconcile.trigger')" :width="isMobile ? '95%' : '560px'" destroy-on-close>
      <el-form label-position="top" @submit.prevent>
        <el-form-item :label="t('reconcile.account')">
          <el-select v-model="trig.account_id" clearable filterable :placeholder="t('reconcile.accountAll')" style="width: 100%">
            <el-option v-for="a in accounts" :key="a.id" :label="`${a.name} (#${a.id})`" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('reconcile.type')">
          <el-radio-group v-model="trig.type">
            <el-radio value="all">{{ t('reconcile.typeAll') }}</el-radio>
            <el-radio value="orders">{{ t('reconcile.typeOrder') }}</el-radio>
            <el-radio value="positions">{{ t('reconcile.typePosition') }}</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template v-if="trig.done">
        <h3 class="card-title">{{ t('reconcile.result') }}</h3>
        <JsonView :value="trig.result" max-height="320px" />
      </template>
      <template #footer>
        <el-button @click="trig.open = false">{{ t('common.close') }}</el-button>
        <el-button type="primary" :loading="trig.loading" @click="submitTrigger">{{ t('reconcile.trigger') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>
