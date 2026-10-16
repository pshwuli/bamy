<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AuditLog, Page, Query, Row } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { fmtTime } from '@/utils/format'
import FilterBar from './FilterBar.vue'
import Pager from './Pager.vue'
import JsonView from './JsonView.vue'
import ErrorState from './ErrorState.vue'

const props = withDefaults(
  defineProps<{
    fetcher: (q: Query) => Promise<Page<AuditLog>>
    detailFetcher?: (id: number) => Promise<AuditLog>
    showActor?: boolean
    showFilters?: boolean
    fixed?: Query
  }>(),
  { showActor: false, showFilters: true, fixed: () => ({}) },
)
const { t, te } = useI18n()

const range = ref<[string, string] | null>(null)
const tbl = useTable<AuditLog>(props.fetcher, { action: '', source: '', target_type: '', target_id: '', actor_id: '', keyword: '' }, {
  transform: (q) => ({ ...q, ...props.fixed, start_time: range.value?.[0], end_time: range.value?.[1] }),
})

const detail = ref<AuditLog | null>(null)
const open = ref(false)
async function show(row: Row) {
  detail.value = row
  open.value = true
  if (props.detailFetcher) {
    try {
      detail.value = await props.detailFetcher(row.id)
    } catch {
      /* keep row */
    }
  }
}
const sources = ['strategy', 'manual', 'system', 'admin', 'plugin']
const srcLabel = (s?: string) => (s && te(`source.${s}`) ? t(`source.${s}`) : s || '-')
function reset() {
  range.value = null
  tbl.resetFilters()
}
defineExpose({ reload: tbl.load })
</script>

<template>
  <div>
    <FilterBar v-if="showFilters" :loading="tbl.loading.value" @search="tbl.search" @reset="reset">
      <el-input v-model="tbl.filters.keyword" :placeholder="t('common.search')" clearable @keyup.enter="tbl.search" />
      <el-input v-model="tbl.filters.action" :placeholder="t('audit.action')" clearable />
      <el-select v-model="tbl.filters.source" :placeholder="t('audit.source')" clearable>
        <el-option v-for="s in sources" :key="s" :value="s" :label="srcLabel(s)" />
      </el-select>
      <el-input v-model="tbl.filters.target_type" :placeholder="t('audit.targetType')" clearable />
      <el-input v-if="showActor" v-model="tbl.filters.target_id" :placeholder="t('audit.targetId')" clearable />
      <el-input v-if="showActor" v-model="tbl.filters.actor_id" :placeholder="t('audit.actorId')" clearable />
      <el-date-picker v-model="range" type="datetimerange" value-format="YYYY-MM-DDTHH:mm:ssZ" :start-placeholder="t('common.time')" :end-placeholder="t('common.time')" />
    </FilterBar>
    <ErrorState v-if="tbl.error.value && !tbl.items.value.length" :error="tbl.error.value" @retry="tbl.load" />
    <template v-else>
      <el-skeleton v-if="!tbl.loadedOnce.value" :rows="6" animated />
      <el-table v-else v-loading="tbl.loading.value" :data="tbl.items.value" stripe size="small" class="resp-table">
        <el-table-column :label="t('audit.time')" min-width="160">
          <template #default="{ row }">{{ fmtTime(row.timestamp || row.created_at) }}</template>
        </el-table-column>
        <el-table-column v-if="showActor" :label="t('audit.actor')" min-width="110">
          <template #default="{ row }">{{ row.actor_username || row.actor_id || '-' }}</template>
        </el-table-column>
        <el-table-column prop="action" :label="t('audit.action')" min-width="150" />
        <el-table-column :label="t('audit.target')" min-width="140">
          <template #default="{ row }">{{ row.target_type || '-' }}<span v-if="row.target_id"> #{{ row.target_id }}</span></template>
        </el-table-column>
        <el-table-column :label="t('audit.result')" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.result === 'failure' || row.result === 'failed' ? 'danger' : 'success'" disable-transitions>
              {{ row.result === 'failure' || row.result === 'failed' ? t('audit.failure') : t('audit.success') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('audit.source')" width="100">
          <template #default="{ row }">
            {{ srcLabel(row.source) }}
            <el-tag v-if="row.source === 'plugin' && row.effective_scope" size="small" type="info" effect="plain">{{ t(`scope.${row.effective_scope}`) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('audit.detail')" width="80" fixed="right">
          <template #default="{ row }"><el-button link type="primary" @click="show(row)">{{ t('common.view') }}</el-button></template>
        </el-table-column>
        <template #empty><el-empty :description="t('common.noMatch')" :image-size="60" /></template>
      </el-table>
      <Pager v-model:page="tbl.page.value" v-model:size="tbl.pageSize.value" :total="tbl.total.value" />
    </template>

    <el-dialog v-model="open" :title="t('audit.full')" width="720px" append-to-body>
      <template v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item :label="t('audit.time')">{{ fmtTime(detail.timestamp || detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item :label="t('audit.actor')">{{ detail.actor_username || detail.actor_id || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('audit.action')">{{ detail.action }}</el-descriptions-item>
          <el-descriptions-item :label="t('audit.target')">{{ detail.target_type }} #{{ detail.target_id ?? '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('audit.result')">{{ detail.result || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('audit.source')">{{ srcLabel(detail.source) }}</el-descriptions-item>
          <el-descriptions-item :label="t('audit.ip')">{{ detail.ip || '-' }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.effective_scope" :label="t('audit.effectiveScope')">{{ t(`scope.${detail.effective_scope}`) }}</el-descriptions-item>
          <el-descriptions-item :label="t('audit.ua')" :span="2">{{ detail.user_agent || '-' }}</el-descriptions-item>
        </el-descriptions>
        <el-row :gutter="12" class="diff">
          <el-col :xs="24" :md="12"><h4>{{ t('audit.oldValue') }}</h4><JsonView :value="detail.old_value" /></el-col>
          <el-col :xs="24" :md="12"><h4>{{ t('audit.newValue') }}</h4><JsonView :value="detail.new_value" /></el-col>
        </el-row>
        <template v-if="detail.detail"><h4>{{ t('audit.detail') }}</h4><JsonView :value="detail.detail" /></template>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.diff { margin-top: 12px; }
h4 { margin: 12px 0 6px; font-size: 13px; }
</style>
