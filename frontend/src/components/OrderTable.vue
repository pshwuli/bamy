<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Order, OrderStatus, Row } from '@/api/types'
import { fmtNum, fmtTime, fmtSymbol } from '@/utils/format'
import StatusBadge from './StatusBadge.vue'
import OrderDetailDialog from './OrderDetailDialog.vue'

withDefaults(defineProps<{ orders: Order[]; loading?: boolean; showInstance?: boolean; compact?: boolean; cancellable?: boolean }>(), {
  loading: false,
  showInstance: true,
  compact: false,
  cancellable: true,
})
const emit = defineEmits<{ cancel: [order: Row] }>()
const { t, te } = useI18n()

const CANCELLABLE: OrderStatus[] = ['pending', 'submitted', 'partial']
const detailId = ref<number | null>(null)
const open = ref(false)
function show(o: Row) {
  detailId.value = o.id
  open.value = true
}
const src = (s?: string) => (s && te(`source.${s}`) ? t(`source.${s}`) : s || '-')
</script>

<template>
  <el-table v-loading="loading" :data="orders" stripe size="small" class="resp-table">
    <el-table-column :label="t('order.id')" min-width="170" show-overflow-tooltip>
      <template #default="{ row }"><el-button link type="primary" @click="show(row)">{{ row.client_order_id }}</el-button></template>
    </el-table-column>
    <el-table-column v-if="showInstance" :label="t('order.instance')" min-width="120" show-overflow-tooltip>
      <template #default="{ row }"><router-link :to="`/instances/${row.instance_id}`">{{ row.instance_name || `#${row.instance_id}` }}</router-link></template>
    </el-table-column>
    <el-table-column :label="t('order.symbol')" min-width="100"><template #default="{ row }">{{ fmtSymbol(row.symbol) }}</template></el-table-column>
    <el-table-column :label="t('order.side')" width="70">
      <template #default="{ row }"><span :class="row.side === 'buy' ? 'pnl-up' : 'pnl-down'">{{ t(`side.${row.side}`) }}</span></template>
    </el-table-column>
    <el-table-column v-if="!compact" prop="type" :label="t('order.type')" width="80" />
    <el-table-column :label="t('order.amount')" min-width="100" align="right"><template #default="{ row }">{{ fmtNum(row.amount, 8) }}</template></el-table-column>
    <el-table-column v-if="!compact" :label="t('order.price')" min-width="100" align="right"><template #default="{ row }">{{ fmtNum(row.price, 8) }}</template></el-table-column>
    <el-table-column v-if="!compact" :label="t('order.filledAmount')" min-width="100" align="right"><template #default="{ row }">{{ fmtNum(row.filled_amount, 8) }}</template></el-table-column>
    <el-table-column v-if="!compact" :label="t('order.filledPrice')" min-width="100" align="right"><template #default="{ row }">{{ fmtNum(row.filled_price, 8) }}</template></el-table-column>
    <el-table-column :label="t('order.status')" width="100"><template #default="{ row }"><StatusBadge kind="order" :value="row.status" /></template></el-table-column>
    <el-table-column v-if="!compact" :label="t('order.source')" width="80"><template #default="{ row }">{{ src(row.source) }}</template></el-table-column>
    <el-table-column :label="t('order.time')" min-width="160"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
    <el-table-column v-if="!compact" :label="t('common.actions')" width="120" fixed="right">
      <template #default="{ row }">
        <el-button link type="primary" @click="show(row)">{{ t('common.detail') }}</el-button>
        <el-button v-if="cancellable && CANCELLABLE.includes(row.status)" link type="danger" @click="emit('cancel', row)">{{ t('order.cancel') }}</el-button>
      </template>
    </el-table-column>
    <template #empty><el-empty :description="t('common.empty')" :image-size="60" /></template>
  </el-table>
  <OrderDetailDialog v-model="open" :order-id="detailId" />
</template>
