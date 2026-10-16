<script setup lang="ts">
defineProps<{ label: string; value: string | number; sub?: string; to?: string; tone?: 'up' | 'down' | 'warn' | '' ; loading?: boolean }>()
</script>

<template>
  <component :is="to ? 'router-link' : 'div'" :to="to" class="stat-card" :class="{ link: !!to }">
    <div class="sc-label">{{ label }}</div>
    <el-skeleton v-if="loading" :rows="0" animated class="sc-skel" />
    <div v-else class="sc-value" :class="tone ? `tone-${tone}` : ''">{{ value }}</div>
    <div v-if="sub" class="sc-sub">{{ sub }}</div>
  </component>
</template>

<style scoped>
.stat-card { display: block; padding: 16px; border-radius: 8px; background: var(--el-bg-color); border: 1px solid var(--el-border-color-lighter); color: inherit; text-decoration: none; min-height: 92px; }
.stat-card.link:hover, .stat-card.link:focus-visible { border-color: var(--el-color-primary); outline: none; }
.sc-label { font-size: 13px; color: var(--el-text-color-secondary); }
.sc-value { font-size: 26px; font-weight: 600; margin-top: 6px; font-variant-numeric: tabular-nums; color: var(--el-text-color-primary); }
.sc-sub { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.sc-skel { margin-top: 10px; }
.tone-up { color: var(--bamy-up); }
.tone-down { color: var(--bamy-down); }
.tone-warn { color: var(--el-color-warning); }
</style>
