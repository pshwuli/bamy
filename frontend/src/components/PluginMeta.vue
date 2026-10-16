<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Plugin } from '@/api/types'

const props = defineProps<{ plugin: Plugin; part: 'scopes' | 'dataScope' | 'hosts' | 'hooks' | 'failMode' }>()
const { t } = useI18n()

const hosts = computed(() => props.plugin.network?.hosts ?? props.plugin.network_hosts ?? [])
const events = computed(() => (props.plugin.hooks ?? []).filter((h) => h.type === 'event'))
const checkpoints = computed(() => (props.plugin.hooks ?? []).filter((h) => h.type === 'checkpoint'))
const hasClosed = computed(() => checkpoints.value.some((h) => h.fail_mode === 'closed'))
</script>

<template>
  <span v-if="part === 'scopes'" class="pm-row">
    <el-tag v-for="s in plugin.scopes" :key="s" size="small" type="info" effect="plain" disable-transitions>{{ t(`scope.${s}`) }}</el-tag>
  </span>

  <el-tag
    v-else-if="part === 'dataScope'"
    size="small"
    :type="plugin.data_scope === 'all' ? 'danger' : 'info'"
    :effect="plugin.data_scope === 'all' ? 'dark' : 'plain'"
    disable-transitions
  >{{ t(`dataScope.${plugin.data_scope}`) }}</el-tag>

  <template v-else-if="part === 'hosts'">
    <span v-if="!hosts.length" class="pm-muted">{{ t('plugin.noNetwork') }}</span>
    <details v-else class="pm-details">
      <summary>{{ t('plugin.hostsCount', { n: hosts.length }) }}</summary>
      <ul><li v-for="h in hosts" :key="h"><code>{{ h }}</code></li></ul>
    </details>
  </template>

  <template v-else-if="part === 'hooks'">
    <span v-if="!plugin.hooks" class="pm-muted">-</span>
    <details v-else class="pm-details">
      <summary>{{ t('plugin.hooksSummary', { e: events.length, c: checkpoints.length }) }}</summary>
      <div v-if="events.length" class="pm-sub">{{ t('plugin.events') }}</div>
      <ul><li v-for="h in events" :key="h.event"><code>{{ h.event }}</code></li></ul>
      <div v-if="checkpoints.length" class="pm-sub">{{ t('plugin.checkpoints') }}</div>
      <ul>
        <li v-for="h in checkpoints" :key="h.checkpoint">
          <code>{{ h.checkpoint }}</code>
          <el-tag size="small" :type="h.fail_mode === 'closed' ? 'danger' : 'info'" effect="plain" disable-transitions>{{ h.fail_mode || 'open' }}</el-tag>
          <span v-if="h.timeout_ms" class="pm-muted">{{ h.timeout_ms }}ms</span>
        </li>
      </ul>
    </details>
  </template>

  <el-tag v-else-if="part === 'failMode'" size="small" :type="hasClosed ? 'danger' : 'info'" :effect="hasClosed ? 'dark' : 'plain'" disable-transitions>
    {{ hasClosed ? 'closed' : plugin.hooks ? 'open' : '-' }}
  </el-tag>
</template>

<style scoped>
.pm-row { display: inline-flex; flex-wrap: wrap; gap: 4px; }
.pm-muted { color: var(--el-text-color-secondary); font-size: 12px; }
.pm-details summary { cursor: pointer; font-size: 12px; color: var(--el-text-color-regular); }
.pm-details ul { margin: 4px 0; padding-left: 18px; font-size: 12px; }
.pm-details li { display: flex; gap: 6px; align-items: center; margin: 2px 0; }
.pm-sub { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
</style>
