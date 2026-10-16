<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { adminApi, type Backup } from '@/api/admin'
import type { Row } from '@/api/types'
import { useTable } from '@/composables/useTable'
import { confirm, confirmTyped } from '@/composables/useConfirm'
import { attempt } from '@/composables/useErrors'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useAuth } from '@/stores/auth'
import { fmtBytes, fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import ErrorState from '@/components/ErrorState.vue'
import EmptyState from '@/components/EmptyState.vue'

const { t } = useI18n()
const auth = useAuth()
const { isMobile } = useBreakpoint()

const tbl = useTable<Backup>(adminApi.backups, {}, { pageSize: 200 })

const nameOf = (r: Row): string => r.filename || `#${r.id}`
const sizeOf = (r: Row): number | null => r.size_bytes ?? r.size ?? null
const statusTone = (s?: string) => (s === 'failed' || s === 'error' ? 'danger' : s === 'running' || s === 'pending' ? 'warning' : 'success')

const creating = ref(false)
async function create() {
  if (!(await confirm(t('backup.createConfirm'), t('backup.create')))) return
  creating.value = true
  const r = await attempt(() => adminApi.createBackup(), t('backup.created'))
  creating.value = false
  if (r !== undefined) void tbl.search()
}

const downloading = ref<number | string | null>(null)
async function download(row: Row) {
  downloading.value = row.id
  await attempt(() => adminApi.downloadBackup(row), false)
  downloading.value = null
}

const restoring = ref<number | string | null>(null)
async function restore(row: Row) {
  const ok = await confirmTyped(`${t('backup.restoreConfirm')}\n${nameOf(row)}`, 'RESTORE', t('backup.restoreTitle', { name: nameOf(row) }))
  if (!ok) return
  restoring.value = row.id
  const r = await attempt(() => adminApi.restoreBackup(row.id), t('backup.restoreStarted'))
  restoring.value = null
  if (r !== undefined) {
    await ElMessageBox.alert(t('backup.restoreDone'), t('backup.restore'), { type: 'warning', confirmButtonText: t('common.confirm') }).catch(() => undefined)
  }
}
</script>

<template>
  <div>
    <PageHeader :title="t('backup.title')">
      <el-button v-if="auth.isSuper" type="primary" :loading="creating" @click="create">{{ t('backup.create') }}</el-button>
      <el-button :loading="tbl.loading.value" @click="tbl.load">{{ t('common.refresh') }}</el-button>
    </PageHeader>

    <el-alert :title="t('backup.warn')" type="warning" show-icon :closable="false" style="margin-bottom: 12px" />

    <section class="card">
      <h2 class="card-title">{{ t('backup.history') }}</h2>
      <ErrorState v-if="tbl.error.value && !tbl.items.value.length" :error="tbl.error.value" @retry="tbl.load" />
      <template v-else>
        <div style="overflow-x: auto">
          <el-table v-loading="tbl.loading.value" :data="tbl.items.value" class="resp-table" row-key="id">
            <el-table-column :label="t('backup.file')" min-width="220" show-overflow-tooltip><template #default="{ row }"><code>{{ nameOf(row) }}</code></template></el-table-column>
            <el-table-column :label="t('backup.size')" width="110" align="right"><template #default="{ row }"><span class="num">{{ fmtBytes(sizeOf(row)) }}</span></template></el-table-column>
            <el-table-column :label="t('backup.status')" width="100">
              <template #default="{ row }"><el-tag :type="statusTone(row.status)" size="small">{{ row.status || '-' }}</el-tag></template>
            </el-table-column>
            <el-table-column :label="t('backup.createdAt')" min-width="160"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
            <el-table-column v-if="auth.isSuper" :label="t('common.actions')" width="150" :fixed="isMobile ? undefined : 'right'">
              <template #default="{ row }">
                <el-button size="small" link type="primary" :loading="downloading === row.id" @click="download(row)">{{ t('backup.download') }}</el-button>
                <el-button v-if="row.restorable !== false" size="small" link type="danger" :loading="restoring === row.id" @click="restore(row)">{{ t('backup.restore') }}</el-button>
                <el-tooltip v-else :content="t('backupx.pgRestoreHint')" placement="top"><span class="muted small">{{ t('backupx.scriptOnly') }}</span></el-tooltip>
              </template>
            </el-table-column>
            <template #empty><EmptyState :action-text="auth.isSuper ? t('backup.create') : undefined" @action="create" /></template>
          </el-table>
        </div>
      </template>
    </section>
  </div>
</template>
