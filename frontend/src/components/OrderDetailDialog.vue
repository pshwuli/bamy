<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ordersApi, type Fill } from '@/api/orders'
import { itemsOf } from '@/api/extensions'
import type { Order } from '@/api/types'
import { fmtNum, fmtTime, fmtSymbol } from '@/utils/format'
import { handleError } from '@/composables/useErrors'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ orderId: number | null }>()
const open = defineModel<boolean>({ required: true })
const { t, te } = useI18n()
const order = ref<Order | null>(null)
const fills = ref<Fill[]>([])
const loading = ref(false)

watch(
  () => [open.value, props.orderId],
  async () => {
    if (!open.value || !props.orderId) return
    loading.value = true
    try {
      const [o, f] = await Promise.all([ordersApi.get(props.orderId), ordersApi.fills(props.orderId).catch(() => [])])
      order.value = o
      fills.value = itemsOf(f as Fill[] | { items: Fill[] })
    } catch (e) {
      handleError(e)
    } finally {
      loading.value = false
    }
  },
)
const src = (s?: string) => (s && te(`source.${s}`) ? t(`source.${s}`) : s || '-')
</script>

<template>
  <el-dialog v-model="open" :title="t('order.detail')" width="720px" append-to-body>
    <div v-loading="loading">
      <template v-if="order">
        <el-alert
          v-if="order.plugin_rejection"
          type="warning"
          :closable="false"
          show-icon
          :title="t('order.pluginRejected')"
          class="od-alert"
        >
          <div>{{ t('error.pluginDenied', { plugin: order.plugin_rejection.plugin_name || order.plugin_rejection.plugin_id }) }}</div>
          <div v-if="order.plugin_rejection.reason">{{ t('error.pluginReason', { reason: order.plugin_rejection.reason }) }}</div>
          <div v-if="order.plugin_rejection.checkpoint">{{ t('error.pluginCheckpoint', { checkpoint: order.plugin_rejection.checkpoint }) }}</div>
          <div v-if="order.plugin_rejection.effective_scope">{{ t('error.pluginScope', { scope: t(`scope.${order.plugin_rejection.effective_scope}`) }) }}</div>
        </el-alert>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item :label="t('order.id')">{{ order.client_order_id }}</el-descriptions-item>
          <el-descriptions-item :label="t('order.status')"><StatusBadge kind="order" :value="order.status" /></el-descriptions-item>
          <el-descriptions-item :label="t('order.instance')">
            <router-link :to="`/instances/${order.instance_id}`" @click="open = false">{{ order.instance_name || `#${order.instance_id}` }}</router-link>
          </el-descriptions-item>
          <el-descriptions-item :label="t('order.symbol')">{{ fmtSymbol(order.symbol) }}</el-descriptions-item>
          <el-descriptions-item :label="t('order.side')">{{ t(`side.${order.side}`) }}</el-descriptions-item>
          <el-descriptions-item :label="t('order.type')">{{ order.type }}</el-descriptions-item>
          <el-descriptions-item :label="t('order.amount')">{{ fmtNum(order.amount, 8) }}</el-descriptions-item>
          <el-descriptions-item :label="t('order.price')">{{ fmtNum(order.price, 8) }}</el-descriptions-item>
          <el-descriptions-item :label="t('order.filledAmount')">{{ fmtNum(order.filled_amount, 8) }}</el-descriptions-item>
          <el-descriptions-item :label="t('order.filledPrice')">{{ fmtNum(order.filled_price, 8) }}</el-descriptions-item>
          <el-descriptions-item :label="t('order.source')">{{ src(order.source) }}</el-descriptions-item>
          <el-descriptions-item :label="t('order.time')">{{ fmtTime(order.created_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="order.reject_reason" :label="t('order.rejectReason')" :span="2">{{ order.reject_reason }}</el-descriptions-item>
        </el-descriptions>
        <h4>{{ t('order.fills') }}</h4>
        <el-table :data="fills" size="small" stripe>
          <el-table-column :label="t('order.time')" min-width="160"><template #default="{ row }">{{ fmtTime(row.created_at || row.timestamp) }}</template></el-table-column>
          <el-table-column :label="t('order.price')"><template #default="{ row }">{{ fmtNum(row.price, 8) }}</template></el-table-column>
          <el-table-column :label="t('order.amount')"><template #default="{ row }">{{ fmtNum(row.amount, 8) }}</template></el-table-column>
          <el-table-column :label="t('order.fee')"><template #default="{ row }">{{ fmtNum(row.fee, 8) }} {{ row.fee_asset || '' }}</template></el-table-column>
        </el-table>
      </template>
    </div>
  </el-dialog>
</template>

<style scoped>
.od-alert { margin-bottom: 12px; }
h4 { margin: 16px 0 8px; font-size: 13px; }
</style>
