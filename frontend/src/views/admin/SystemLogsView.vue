<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { adminApi } from '@/api/admin'
import type { LogEntry } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { useLive } from '@/composables/useLive'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { WsMessage } from '@/utils/socket'
import { fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import Pager from '@/components/Pager.vue'
import LogList from '@/components/LogList.vue'
import ErrorState from '@/components/ErrorState.vue'
import EmptyState from '@/components/EmptyState.vue'

const { t } = useI18n()
const { isMobile } = useBreakpoint()

const LEVELS = ['debug', 'info', 'warn', 'error', 'critical']
const levelTone: Record<string, 'info' | 'primary' | 'warning' | 'danger'> = { debug: 'info', info: 'primary', warn: 'warning', warning: 'warning', error: 'danger', critical: 'danger' }

const range = ref<[string, string] | null>(null)
const tbl = useTable<LogEntry>(
  adminApi.systemLogs,
  { level: '', module: '', instance_id: undefined as number | undefined, account_id: undefined as number | undefined, keyword: '' },
  { transform: (q) => ({ ...q, start_time: range.value?.[0], end_time: range.value?.[1] }) },
)
function reset() {
  range.value = null
  void tbl.resetFilters()
}

const mode = ref<'table' | 'stream'>('table')
const live = ref(false)
const streamLogs = computed(() => [...tbl.items.value].reverse())

function matches(e: LogEntry) {
  const f = tbl.filters
  if (range.value) return false
  if (f.level && e.level !== f.level) return false
  if (f.module && e.module !== f.module) return false
  if (f.instance_id && e.instance_id !== f.instance_id) return false
  if (f.account_id && e.account_id !== f.account_id) return false
  if (f.keyword && !e.message?.includes(f.keyword)) return false
  return true
}

let seq = 0
function onEvent(m: WsMessage) {
  if (!live.value || tbl.page.value !== 1) return
  const d = (m.data ?? {}) as Record<string, unknown>
  const lvl = String(d.level ?? 'info')
  const entry: LogEntry = {
    id: `live-${Date.now()}-${seq++}`,
    level: lvl === 'warning' ? 'warn' : lvl,
    module: (d.module as string | undefined) ?? 'strategy',
    message: String(d.message ?? ''),
    instance_id: (d.instance_id as number | undefined) ?? null,
    account_id: (d.account_id as number | undefined) ?? null,
    timestamp: new Date(typeof d.ts === 'number' ? d.ts : m.ts ?? Date.now()).toISOString(),
  }
  if (!matches(entry)) return
  tbl.items.value = [entry, ...tbl.items.value].slice(0, tbl.pageSize.value)
  tbl.total.value += 1
}

const { resubscribe } = useLive({
  topics: () => (live.value ? [tbl.filters.instance_id ? `instance.${tbl.filters.instance_id}.logs` : 'instance.*'] : []),
  events: ['instance.log'],
  onEvent,
  poll: () => {
    if (live.value && !tbl.loading.value) void tbl.load()
  },
})
watch(live, (on) => {
  resubscribe()
  if (on) void tbl.search()
})
function search() {
  if (live.value) resubscribe()
  void tbl.search()
}
</script>

<template>
  <div>
    <PageHeader :title="t('syslog.title')">
      <el-tooltip :content="t('syslog.liveHint')">
        <el-switch v-model="live" :active-text="t('syslog.live')" :aria-label="t('syslog.live')" />
      </el-tooltip>
      <el-radio-group v-model="mode" size="small" :aria-label="t('syslog.title')">
        <el-radio-button value="table">{{ t('syslog.table') }}</el-radio-button>
        <el-radio-button value="stream">{{ t('syslog.stream') }}</el-radio-button>
      </el-radio-group>
      <el-button :loading="tbl.loading.value" @click="tbl.load">{{ t('common.refresh') }}</el-button>
    </PageHeader>

    <FilterBar :loading="tbl.loading.value" @search="search" @reset="reset">
      <el-select v-model="tbl.filters.level" clearable :placeholder="t('syslog.level')" :aria-label="t('syslog.level')" style="width: 110px">
        <el-option v-for="l in LEVELS" :key="l" :label="l" :value="l" />
      </el-select>
      <el-input v-model="tbl.filters.module" clearable :placeholder="t('syslog.module')" :aria-label="t('syslog.module')" style="width: 130px" @keyup.enter="search" />
      <el-input-number v-model="tbl.filters.instance_id" :min="1" controls-position="right" :placeholder="t('syslog.instanceId')" :aria-label="t('syslog.instanceId')" style="width: 130px" />
      <el-input-number v-model="tbl.filters.account_id" :min="1" controls-position="right" :placeholder="t('syslog.accountId')" :aria-label="t('syslog.accountId')" style="width: 130px" />
      <el-input v-model="tbl.filters.keyword" clearable :placeholder="t('syslog.keyword')" :aria-label="t('syslog.keyword')" style="width: 180px" @keyup.enter="search" />
      <el-date-picker
        v-model="range"
        type="datetimerange"
        value-format="YYYY-MM-DDTHH:mm:ss"
        :start-placeholder="t('syslog.timeRange')"
        :end-placeholder="t('syslog.timeRange')"
        :aria-label="t('syslog.timeRange')"
        style="max-width: 360px"
      />
    </FilterBar>

    <section class="card">
      <ErrorState v-if="tbl.error.value && !tbl.items.value.length" :error="tbl.error.value" @retry="tbl.load" />
      <template v-else>
        <div v-if="mode === 'stream'" v-loading="tbl.loading.value && !tbl.loadedOnce.value">
          <EmptyState v-if="tbl.loadedOnce.value && !tbl.items.value.length" />
          <LogList v-else :logs="streamLogs" show-module :height="isMobile ? '60vh' : '560px'" />
        </div>
        <div v-else style="overflow-x: auto">
          <el-table v-loading="tbl.loading.value" :data="tbl.items.value" class="resp-table" row-key="id" size="small">
            <el-table-column :label="t('syslog.time')" min-width="160"><template #default="{ row }">{{ fmtTime(row.timestamp ?? row.created_at) }}</template></el-table-column>
            <el-table-column :label="t('syslog.level')" width="90">
              <template #default="{ row }"><el-tag :type="levelTone[row.level] ?? 'info'" size="small">{{ row.level }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="module" :label="t('syslog.module')" width="110" show-overflow-tooltip />
            <el-table-column :label="t('syslog.instance')" width="90">
              <template #default="{ row }"><router-link v-if="row.instance_id" :to="`/instances/${row.instance_id}`">#{{ row.instance_id }}</router-link><span v-else class="muted">-</span></template>
            </el-table-column>
            <el-table-column :label="t('syslog.account')" width="90">
              <template #default="{ row }">{{ row.account_id ? `#${row.account_id}` : '-' }}</template>
            </el-table-column>
            <el-table-column :label="t('syslog.message')" min-width="320">
              <template #default="{ row }"><span style="white-space: pre-wrap; word-break: break-word">{{ row.message }}</span></template>
            </el-table-column>
            <template #empty><EmptyState /></template>
          </el-table>
        </div>
        <Pager v-model:page="tbl.page.value" v-model:size="tbl.pageSize.value" :total="tbl.total.value" />
      </template>
    </section>
  </div>
</template>
