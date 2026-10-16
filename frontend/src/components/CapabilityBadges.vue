<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ caps?: Record<string, boolean> | null }>()
const { t, te } = useI18n()
const list = computed(() =>
  Object.entries(props.caps ?? {})
    .filter(([, v]) => v === true)
    .map(([k]) => ({ k, label: te(`capability.${k}`) ? t(`capability.${k}`) : k })),
)
</script>

<template>
  <span class="caps">
    <el-tag v-for="c in list" :key="c.k" size="small" type="info" effect="plain" disable-transitions>{{ c.label }}</el-tag>
  </span>
</template>

<style scoped>
.caps { display: inline-flex; flex-wrap: wrap; gap: 4px; }
</style>
