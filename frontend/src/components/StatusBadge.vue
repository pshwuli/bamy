<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

type Kind = 'instance' | 'order' | 'account' | 'user' | 'binding' | 'load' | 'breaker' | 'invite'
type Tone = 'success' | 'warning' | 'danger' | 'info' | 'primary'

const props = defineProps<{ kind: Kind; value: string | null | undefined; autoDisabled?: boolean }>()
const { t, te } = useI18n()

const TONES: Record<Kind, Record<string, Tone>> = {
  instance: {
    created: 'info', starting: 'primary', running: 'success', pausing: 'warning', paused: 'warning',
    resuming: 'primary', stopping: 'warning', stopped: 'info', error: 'danger', deleted: 'info',
  },
  order: {
    pending: 'info', submitting: 'primary', submitted: 'primary', partial: 'warning', filled: 'success',
    canceling: 'warning', canceled: 'info', rejected: 'danger', unknown: 'warning', failed: 'danger',
  },
  account: { active: 'success', online: 'success', offline: 'info', abnormal: 'warning', invalid: 'danger', disabled: 'info', paused: 'warning', deleted: 'info' },
  user: { pending: 'info', active: 'success', locked: 'warning', disabled: 'danger' },
  binding: { enabled: 'success', disabled: 'warning', invalid: 'danger', auto_disabled: 'warning' },
  load: { loaded: 'success', failed: 'danger', outdated: 'warning' },
  breaker: { active: 'danger', recovered: 'info' },
  invite: { enabled: 'success', disabled: 'info', expired: 'warning', exhausted: 'info' },
}

const TRANSIENT = new Set(['starting', 'pausing', 'resuming', 'stopping', 'submitting', 'canceling'])

const effective = computed(() => (props.kind === 'binding' && props.autoDisabled ? 'auto_disabled' : props.value ?? ''))
const tone = computed<Tone>(() => TONES[props.kind][effective.value] ?? 'info')
const label = computed(() => {
  const key = `status.${props.kind}.${effective.value}`
  return te(key) ? t(key) : effective.value || '-'
})
const elType = computed(() => (tone.value === 'primary' ? 'primary' : tone.value))
</script>

<template>
  <el-tag :type="elType" size="small" effect="light" disable-transitions class="status-badge" :class="{ transient: TRANSIENT.has(effective) }">
    <span class="dot" aria-hidden="true" />
    {{ label }}
  </el-tag>
</template>

<style scoped>
.status-badge { gap: 4px; }
.dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: currentColor; margin-right: 4px; vertical-align: middle; }
.transient .dot { animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: 0.25; } }
@media (prefers-reduced-motion: reduce) { .transient .dot { animation: none; } }
</style>
