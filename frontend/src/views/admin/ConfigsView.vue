<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { adminApi, type ConfigItem } from '@/api/admin'
import type { Row } from '@/api/types'
import { confirm } from '@/composables/useConfirm'
import { attempt } from '@/composables/useErrors'
import { useAuth } from '@/stores/auth'
import { fmtTime, pretty } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import ErrorState from '@/components/ErrorState.vue'
import EmptyState from '@/components/EmptyState.vue'

type Item = ConfigItem & { updated_at?: string | null }

const { t } = useI18n()
const auth = useAuth()
const canEdit = computed(() => auth.isSuper)

const loading = ref(true)
const error = ref<unknown>(null)
const items = shallowRef<Item[]>([])
const env = ref<Record<string, unknown>>({})
const sysReadonly = ref(false)
const draft = ref<Record<string, unknown>>({})
const saving = ref(false)

async function load() {
  loading.value = true
  error.value = null
  try {
    const d = (await adminApi.configs()) as unknown
    let list: Item[] = []
    if (Array.isArray(d)) list = d as Item[]
    else if (d && typeof d === 'object') {
      const o = d as { items?: Item[]; env?: Record<string, unknown>; readonly?: boolean }
      list = Array.isArray(o.items) ? o.items : Object.entries(o).map(([key, value]) => ({ key, value }))
      env.value = o.env ?? {}
      sysReadonly.value = !!o.readonly
    }
    items.value = list
    draft.value = Object.fromEntries(list.map((i) => [i.key, i.value]))
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}
onMounted(load)

const typeOf = (i: Row): string => i.type ?? (typeof i.default === 'boolean' ? 'bool' : typeof i.default === 'number' ? (Number.isInteger(i.default) ? 'int' : 'float') : 'str')
const groups = computed(() => {
  const m = new Map<string, Item[]>()
  for (const i of items.value) {
    const g = i.group ?? i.key.split('.')[0] ?? '-'
    if (!m.has(g)) m.set(g, [])
    m.get(g)!.push(i)
  }
  return [...m.entries()].map(([name, rows]) => ({ name, rows }))
})
const changed = computed(() => {
  const out: Record<string, unknown> = {}
  for (const i of items.value) if (draft.value[i.key] !== i.value) out[i.key] = draft.value[i.key]
  return out
})
const changedCount = computed(() => Object.keys(changed.value).length)
const isChanged = (k: string) => k in changed.value
const show = (v: unknown) => (v === null || v === undefined ? '-' : typeof v === 'object' ? pretty(v) : String(v))

function discard() {
  draft.value = Object.fromEntries(items.value.map((i) => [i.key, i.value]))
}
async function save() {
  if (!changedCount.value) return
  if (!(await confirm(t('config.saveConfirm', { n: changedCount.value }), t('config.saveAll')))) return
  saving.value = true
  const r = await attempt(() => adminApi.updateConfigs(changed.value), t('config.saved'))
  saving.value = false
  if (r !== undefined) await load()
}
</script>

<template>
  <div>
    <PageHeader :title="t('config.title')">
      <template v-if="canEdit">
        <span v-if="changedCount" class="muted">{{ t('config.changed', { n: changedCount }) }}</span>
        <el-button :disabled="!changedCount" @click="discard">{{ t('config.discard') }}</el-button>
        <el-button type="primary" :disabled="!changedCount" :loading="saving" @click="save">{{ t('config.saveAll') }}</el-button>
      </template>
      <el-button :loading="loading" @click="load">{{ t('common.refresh') }}</el-button>
    </PageHeader>

    <el-alert v-if="!canEdit" :title="t('config.readonlyHint')" type="info" show-icon :closable="false" style="margin-bottom: 12px" />
    <el-alert v-if="sysReadonly" :title="t('config.readonlyMode')" type="warning" show-icon :closable="false" style="margin-bottom: 12px" />

    <ErrorState v-if="error" :error="error" @retry="load" />
    <el-skeleton v-else-if="loading && !items.length" :rows="8" animated class="card" />
    <EmptyState v-else-if="!items.length" />
    <template v-else>
      <section v-for="g in groups" :key="g.name" v-loading="loading" class="card">
        <h2 class="card-title">{{ g.name }}</h2>
        <div style="overflow-x: auto">
          <el-table :data="g.rows" class="resp-table" row-key="key" size="small">
            <el-table-column :label="t('config.key')" min-width="200">
              <template #default="{ row }">
                <code>{{ row.key }}</code>
                <el-tag v-if="isChanged(row.key)" type="warning" size="small" style="margin-left: 6px">{{ t('config.modify') }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('config.value')" min-width="200">
              <template #default="{ row }">
                <template v-if="canEdit && row.editable !== false">
                  <el-switch v-if="typeOf(row) === 'bool'" :model-value="!!draft[row.key]" @update:model-value="(v: string | number | boolean) => (draft[row.key] = !!v)" :aria-label="row.key" />
                  <el-input-number
                    v-else-if="typeOf(row) === 'int' || typeOf(row) === 'float'"
                    :model-value="(draft[row.key] as number | undefined)"
                    @update:model-value="(v: number | undefined) => (draft[row.key] = v)"
                    :step="typeOf(row) === 'int' ? 1 : 0.0001"
                    :step-strictly="typeOf(row) === 'int'"
                    :precision="typeOf(row) === 'int' ? 0 : undefined"
                    controls-position="right"
                    :aria-label="row.key"
                    style="width: 180px"
                  />
                  <el-input v-else :model-value="String(draft[row.key] ?? '')" @update:model-value="(v: string) => (draft[row.key] = v)" :aria-label="row.key" />
                </template>
                <span v-else class="num">{{ show(row.value) }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('config.default')" min-width="110"><template #default="{ row }"><span class="muted num">{{ show(row.default) }}</span></template></el-table-column>
            <el-table-column :label="t('config.desc')" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.description || '-' }}<el-tag v-if="row.hot_reload === false" size="small" type="info" style="margin-left: 6px">{{ t('config.restartNeeded') }}</el-tag></template>
            </el-table-column>
            <el-table-column :label="t('config.updatedAt')" min-width="150"><template #default="{ row }">{{ row.updated_at ? fmtTime(row.updated_at) : '-' }}</template></el-table-column>
          </el-table>
        </div>
      </section>
    </template>

    <section v-if="Object.keys(env).length" class="card">
      <h2 class="card-title">{{ t('config.env') }}</h2>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item v-for="(v, k) in env" :key="k" :label="String(k)"><span class="num">{{ show(v) }}</span></el-descriptions-item>
      </el-descriptions>
    </section>
  </div>
</template>
