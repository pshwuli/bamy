<script setup lang="ts">
import { onMounted, ref, shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { Download } from '@element-plus/icons-vue'
import { ordersApi } from '@/api/orders'
import { instancesApi } from '@/api/instances'
import { accountsApi } from '@/api/accounts'
import type { Account, Instance, Order, OrderStatus, Query, Row } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { confirm } from '@/composables/useConfirm'
import { attempt } from '@/composables/useErrors'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import OrderTable from '@/components/OrderTable.vue'
import Pager from '@/components/Pager.vue'
import ErrorState from '@/components/ErrorState.vue'

const { t, te } = useI18n()

const STATUSES: OrderStatus[] = ['pending', 'submitting', 'submitted', 'partial', 'filled', 'canceling', 'canceled', 'rejected', 'unknown', 'failed']
// 后端保护单来源为 guard；插件不能下单，不出现在来源里
const SOURCES = ['strategy', 'manual', 'guard', 'system']
const srcLabel = (s: string) => (te(`source.${s}`) ? t(`source.${s}`) : s)

const range = ref<[string, string] | null>(null)
const instances = shallowRef<Instance[]>([])
const accounts = shallowRef<Account[]>([])
const exporting = ref(false)

const tbl = useTable<Order>(ordersApi.list, { instance_id: '', account_id: '', symbol: '', status: '', source: '' }, {
  transform: (q) => ({ ...q, symbol: typeof q.symbol === 'string' ? q.symbol.trim().toUpperCase() : q.symbol, start_time: range.value?.[0], end_time: range.value?.[1] }),
})

function reset() {
  range.value = null
  tbl.resetFilters()
}

async function exportCsv() {
  const q: Query = { ...tbl.query() }
  delete q.page
  delete q.page_size
  exporting.value = true
  try {
    await ordersApi.export(q)
  } catch {
    /* 非 silent 请求，错误已由全局钩子提示 */
  } finally {
    exporting.value = false
  }
}

async function cancel(o: Row) {
  const order = o as Order
  if (!(await confirm(t('order.cancelConfirm', { id: order.client_order_id }), t('order.cancel'), true))) return
  await attempt(() => instancesApi.cancelOrder(order.instance_id, order.id), t('misc.cancelDone'))
  void tbl.load()
}

onMounted(async () => {
  const [i, a] = await Promise.allSettled([instancesApi.list({ page: 1, page_size: 200 }), accountsApi.list({ page: 1, page_size: 200 })])
  if (i.status === 'fulfilled') instances.value = i.value.items ?? []
  if (a.status === 'fulfilled') accounts.value = a.value.items ?? []
})
</script>

<template>
  <div>
    <PageHeader :title="t('order.title')" :subtitle="t('misc.orderSourceNote')">
      <el-button :icon="Download" :loading="exporting" @click="exportCsv">{{ t('order.exportCsv') }}</el-button>
    </PageHeader>

    <section class="card">
      <FilterBar :loading="tbl.loading.value" @search="tbl.search" @reset="reset">
        <el-select v-model="tbl.filters.instance_id" :placeholder="t('order.instance')" :aria-label="t('order.instance')" clearable filterable>
          <el-option v-for="i in instances" :key="i.id" :value="i.id" :label="`${i.name}${i.paper ? ` (${t('common.paper')})` : ''}`" />
        </el-select>
        <el-select v-model="tbl.filters.account_id" :placeholder="t('order.account')" :aria-label="t('order.account')" clearable filterable>
          <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="a.name" />
        </el-select>
        <el-input v-model="tbl.filters.symbol" :placeholder="t('misc.symbolPh')" :aria-label="t('order.symbol')" clearable @keyup.enter="tbl.search" />
        <el-select v-model="tbl.filters.status" :placeholder="t('order.status')" :aria-label="t('order.status')" clearable>
          <el-option v-for="s in STATUSES" :key="s" :value="s" :label="t(`status.order.${s}`)" />
        </el-select>
        <el-select v-model="tbl.filters.source" :placeholder="t('order.source')" :aria-label="t('order.source')" clearable>
          <el-option v-for="s in SOURCES" :key="s" :value="s" :label="srcLabel(s)" />
        </el-select>
        <el-date-picker
          v-model="range"
          type="datetimerange"
          value-format="YYYY-MM-DDTHH:mm:ssZ"
          :start-placeholder="t('common.time')"
          :end-placeholder="t('common.time')"
          :aria-label="t('common.time')"
        />
      </FilterBar>

      <ErrorState v-if="tbl.error.value && !tbl.items.value.length" :error="tbl.error.value" @retry="tbl.load" />
      <template v-else>
        <el-skeleton v-if="!tbl.loadedOnce.value" :rows="6" animated />
        <template v-else>
          <OrderTable :orders="tbl.items.value" :loading="tbl.loading.value" @cancel="cancel" />
          <Pager v-model:page="tbl.page.value" v-model:size="tbl.pageSize.value" :total="tbl.total.value" />
        </template>
      </template>
    </section>
  </div>
</template>
