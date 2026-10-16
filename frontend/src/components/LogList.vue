<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { LogEntry } from '@/api/types'
import { fmtTime } from '@/utils/format'

const props = withDefaults(defineProps<{ logs: LogEntry[]; height?: string; showModule?: boolean; autoScroll?: boolean }>(), {
  height: '480px',
  showModule: false,
  autoScroll: true,
})
const { t } = useI18n()
const box = ref<HTMLDivElement>()

watch(
  () => props.logs.length,
  async () => {
    if (!props.autoScroll) return
    await nextTick()
    if (box.value) box.value.scrollTop = box.value.scrollHeight
  },
)

const levelTone: Record<string, string> = { debug: 'info', info: 'primary', warn: 'warning', warning: 'warning', error: 'danger', critical: 'danger' }
</script>

<template>
  <div ref="box" class="log-list" :style="{ height }" role="log" aria-live="polite">
    <div v-if="!logs.length" class="ll-empty">{{ t('common.empty') }}</div>
    <div v-for="(l, i) in logs" :key="l.id ?? i" class="ll-row">
      <span class="ll-time">{{ fmtTime(l.timestamp || l.created_at) }}</span>
      <el-tag size="small" :type="(levelTone[l.level?.toLowerCase()] as 'info') || 'info'" effect="plain" class="ll-level" disable-transitions>{{ l.level }}</el-tag>
      <span v-if="showModule && l.module" class="ll-module">[{{ l.module }}]</span>
      <span class="ll-msg">{{ l.message }}</span>
    </div>
  </div>
</template>

<style scoped>
.log-list { overflow: auto; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; background: var(--el-fill-color-lighter); border: 1px solid var(--el-border-color-lighter); border-radius: 6px; padding: 8px; }
.ll-row { display: flex; gap: 8px; align-items: baseline; padding: 2px 0; border-bottom: 1px dashed var(--el-border-color-lighter); }
.ll-time { color: var(--el-text-color-secondary); white-space: nowrap; }
.ll-level { min-width: 52px; justify-content: center; }
.ll-module { color: var(--el-text-color-secondary); }
.ll-msg { white-space: pre-wrap; word-break: break-all; }
.ll-empty { color: var(--el-text-color-secondary); text-align: center; padding: 24px; }
</style>
