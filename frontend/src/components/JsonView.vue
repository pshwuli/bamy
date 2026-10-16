<script setup lang="ts">
import { computed } from 'vue'
import { pretty } from '@/utils/format'

const props = defineProps<{ value: unknown; maxHeight?: string }>()
const text = computed(() => pretty(props.value))
</script>

<template>
  <pre class="json-view" :style="{ maxHeight: maxHeight || '360px' }">{{ text }}</pre>
</template>

<style scoped>
.json-view { margin: 0; padding: 10px 12px; background: var(--el-fill-color-light); border-radius: 6px; font-size: 12px; line-height: 1.5; overflow: auto; white-space: pre-wrap; word-break: break-all; }
</style>
