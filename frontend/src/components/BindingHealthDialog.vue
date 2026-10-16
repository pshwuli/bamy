<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { bindingsApi } from '@/api/extensions'
import type { BindingHealth } from '@/api/types'
import { fmtTime, fmtNum } from '@/utils/format'
import { handleError } from '@/composables/useErrors'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ bindingId: number | null }>()
const open = defineModel<boolean>({ required: true })
const { t } = useI18n()
const data = ref<BindingHealth | null>(null)
const loading = ref(false)

watch(
  () => [open.value, props.bindingId],
  async () => {
    if (!open.value || !props.bindingId) return
    loading.value = true
    try {
      data.value = await bindingsApi.health(props.bindingId)
    } catch (e) {
      handleError(e)
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)
</script>

<template>
  <el-dialog v-model="open" :title="t('binding.health')" width="520px" append-to-body>
    <div v-loading="loading">
      <el-descriptions v-if="data" :column="2" border size="small">
        <el-descriptions-item :label="t('binding.status')">
          <StatusBadge kind="binding" :value="data.status" :auto-disabled="data.auto_disabled" />
        </el-descriptions-item>
        <el-descriptions-item :label="t('binding.consecutive')">{{ data.consecutive_errors }}</el-descriptions-item>
        <el-descriptions-item :label="t('binding.eventCalls')">{{ fmtNum(data.event_calls, 0) }}</el-descriptions-item>
        <el-descriptions-item :label="t('binding.checkpointCalls')">{{ fmtNum(data.checkpoint_calls, 0) }}</el-descriptions-item>
        <el-descriptions-item :label="t('binding.errorCount')">{{ fmtNum(data.error_count, 0) }}</el-descriptions-item>
        <el-descriptions-item :label="t('binding.timeoutCount')">{{ fmtNum(data.timeout_count, 0) }}</el-descriptions-item>
        <el-descriptions-item :label="t('binding.avgLatency')">{{ fmtNum(data.avg_latency_ms, 1) }} ms</el-descriptions-item>
        <el-descriptions-item :label="t('binding.since')">{{ fmtTime(data.since) }}</el-descriptions-item>
        <el-descriptions-item :label="t('binding.lastError')" :span="2">
          <template v-if="data.last_error">
            <code class="err">{{ data.last_error.message }}</code>
            <div class="muted">{{ fmtTime(data.last_error.at) }}</div>
          </template>
          <span v-else>-</span>
        </el-descriptions-item>
      </el-descriptions>
      <el-alert v-if="data?.auto_disabled" type="warning" :closable="false" show-icon :title="t('binding.autoDisabledHint')" class="mt" />
      <slot :data="data" />
    </div>
  </el-dialog>
</template>

<style scoped>
.err { white-space: pre-wrap; word-break: break-all; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.mt { margin-top: 12px; }
</style>
