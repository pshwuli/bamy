<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Cpu } from '@element-plus/icons-vue'
import { strategiesApi, itemsOf } from '@/api/extensions'
import type { SchemaField, Strategy } from '@/api/types'
import { handleError } from '@/composables/useErrors'
import { pretty } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import CapabilityBadges from '@/components/CapabilityBadges.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'

type Row = Strategy & { load_error?: string | null }
type ListResp = { items?: Row[]; failed?: { id: string; reason: string }[] }

const { t } = useI18n()
const router = useRouter()

const all = shallowRef<Row[]>([])
const failedExtra = shallowRef<{ id: string; reason: string }[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const filters = ref({ keyword: '', tag: '', capability: '', load: '' })
const applied = ref({ ...filters.value })

const CAPS = ['close_position', 'close_positions', 'close_all', 'cancel_order', 'list_positions', 'pause', 'resume']

function loadState(s: Row): string {
  return s.load_status ?? (s.loaded ? 'loaded' : 'failed')
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const d = (await strategiesApi.list()) as unknown as ListResp
    all.value = itemsOf<Row>(d as { items: Row[] })
    failedExtra.value = d?.failed ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

const tagOptions = computed(() => [...new Set(all.value.flatMap((s) => s.tags ?? []))].sort())

const filtered = computed(() => {
  const f = applied.value
  const kw = f.keyword.trim().toLowerCase()
  return all.value.filter((s) => {
    if (kw && !`${s.id} ${s.name ?? ''} ${s.author ?? ''}`.toLowerCase().includes(kw)) return false
    if (f.tag && !(s.tags ?? []).includes(f.tag)) return false
    if (f.capability && !s.capabilities?.[f.capability]) return false
    if (f.load && loadState(s) !== f.load) return false
    return true
  })
})
const okCards = computed(() => filtered.value.filter((s) => loadState(s) !== 'failed'))
const failedList = computed(() => {
  const map = new Map<string, { id: string; name: string; reason: string }>()
  for (const s of all.value.filter((x) => loadState(x) === 'failed')) {
    map.set(s.id, { id: s.id, name: s.id, reason: s.load_error ?? s.error ?? '-' })
  }
  for (const f of failedExtra.value) {
    const cur = map.get(f.id)
    map.set(f.id, { id: f.id, name: f.id, reason: f.reason || cur?.reason || '-' })
  }
  return [...map.values()]
})

function search() {
  applied.value = { ...filters.value }
}
function reset() {
  filters.value = { keyword: '', tag: '', capability: '', load: '' }
  search()
}

const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref<Row | null>(null)
async function openDetail(s: Row) {
  detail.value = s
  detailOpen.value = true
  detailLoading.value = true
  try {
    detail.value = (await strategiesApi.get(s.id)) as Row
  } catch (e) {
    handleError(e)
  } finally {
    detailLoading.value = false
  }
}
function range(row: unknown) {
  const f = row as SchemaField
  if (f.min === undefined && f.max === undefined) return '-'
  return `${f.min ?? '-∞'} ~ ${f.max ?? '∞'}${f.suffix ? ` ${f.suffix}` : ''}`
}
function defaultText(row: unknown) {
  const f = row as SchemaField
  if (f.default === undefined || f.default === null || f.default === '') return '-'
  return typeof f.default === 'object' ? pretty(f.default) : String(f.default)
}

function createInstance(s: Row) {
  router.push({ path: '/instances/new', query: { strategy: s.id } })
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader :title="t('strategy.title')" :subtitle="t('strategy.readonlyNote')" />

    <FilterBar :loading="loading" @search="search" @reset="reset">
      <el-input
        v-model="filters.keyword"
        :placeholder="t('strategy.searchPh')"
        :aria-label="t('strategy.searchPh')"
        clearable
        @keyup.enter="search"
      />
      <el-select v-model="filters.tag" :placeholder="t('strategy.tags')" :aria-label="t('strategy.tags')" clearable @change="search">
        <el-option v-for="tg in tagOptions" :key="tg" :label="tg" :value="tg" />
      </el-select>
      <el-select v-model="filters.capability" :placeholder="t('strategy.capability')" :aria-label="t('strategy.capability')" clearable @change="search">
        <el-option v-for="c in CAPS" :key="c" :label="t(`capability.${c}`)" :value="c" />
      </el-select>
      <el-select v-model="filters.load" :placeholder="t('strategy.loadStatus')" :aria-label="t('strategy.loadStatus')" clearable @change="search">
        <el-option v-for="s in ['loaded', 'failed', 'outdated']" :key="s" :label="t(`status.load.${s}`)" :value="s" />
      </el-select>
    </FilterBar>

    <ErrorState v-if="error" :error="error" @retry="load" />
    <template v-else>
      <div v-loading="loading" class="cards">
        <article v-for="s in okCards" :key="s.id" class="card scard">
          <header class="sc-head">
            <div class="sc-icon" aria-hidden="true">
              <img v-if="s.icon && /^(https?:|\/|data:)/.test(s.icon)" :src="s.icon" alt="" />
              <span v-else-if="s.icon">{{ s.icon }}</span>
              <el-icon v-else :size="22"><Cpu /></el-icon>
            </div>
            <div class="sc-title">
              <h2>{{ s.name || s.id }}</h2>
              <div class="muted small">
                v{{ s.version || '-' }}<template v-if="s.author"> · {{ s.author }}</template>
              </div>
            </div>
            <StatusBadge kind="load" :value="loadState(s)" />
          </header>
          <p class="sc-desc" :title="s.description">{{ s.description || t('extv.noDesc') }}</p>
          <div v-if="s.tags?.length" class="sc-tags">
            <el-tag v-for="tg in s.tags" :key="tg" size="small" effect="plain" disable-transitions>{{ tg }}</el-tag>
          </div>
          <div class="sc-row muted small">{{ t('strategy.paramsCount') }}：{{ s.params_count ?? s.params_schema?.length ?? 0 }}</div>
          <CapabilityBadges :caps="s.capabilities" />
          <footer class="sc-actions">
            <el-button size="small" @click="openDetail(s)">{{ t('strategy.viewDetail') }}</el-button>
            <el-button size="small" type="primary" :disabled="!s.loaded" @click="createInstance(s)">{{ t('strategy.createInstance') }}</el-button>
          </footer>
        </article>
      </div>
      <EmptyState v-if="!loading && !okCards.length" :title="all.length ? t('common.noMatch') : t('common.empty')" />

      <section v-if="failedList.length" class="card failed-area" aria-labelledby="failed-title">
        <h2 id="failed-title" class="card-title danger-text">{{ t('strategy.failedArea') }}</h2>
        <div class="table-wrap">
          <el-table :data="failedList" class="resp-table" size="small">
            <el-table-column :label="t('common.name')" prop="name" min-width="140" />
            <el-table-column :label="t('strategy.failedReason')" min-width="240">
              <template #default="{ row }"><code class="err">{{ row.reason }}</code></template>
            </el-table-column>
            <el-table-column :label="t('strategy.loadStatus')" width="110">
              <template #default><StatusBadge kind="load" value="failed" /></template>
            </el-table-column>
            <el-table-column :label="t('strategy.suggestion')" min-width="200">
              <template #default>{{ t('strategy.suggestionText') }}</template>
            </el-table-column>
          </el-table>
        </div>
      </section>
    </template>

    <el-dialog v-model="detailOpen" :title="detail?.name || detail?.id || ''" width="760px" append-to-body>
      <div v-if="detail" v-loading="detailLoading">
        <el-descriptions :column="2" border size="small" :title="t('extv.metaInfo')">
          <el-descriptions-item label="ID"><code>{{ detail.id }}</code></el-descriptions-item>
          <el-descriptions-item :label="t('strategy.version')">{{ detail.version || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('strategy.author')">{{ detail.author || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('strategy.loadStatus')"><StatusBadge kind="load" :value="loadState(detail)" /></el-descriptions-item>
          <el-descriptions-item :label="t('strategy.tickInterval')">{{ detail.runtime?.tick_interval ?? '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('strategy.symbols')">{{ detail.runtime?.symbols?.join(', ') || t('strategy.allSymbols') }}</el-descriptions-item>
          <el-descriptions-item :label="t('extv.capabilities')" :span="2">
            <CapabilityBadges v-if="Object.values(detail.capabilities ?? {}).some(Boolean)" :caps="detail.capabilities" />
            <span v-else class="muted">{{ t('extv.noCaps') }}</span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('common.detail')" :span="2">{{ detail.description || '-' }}</el-descriptions-item>
        </el-descriptions>
        <h3 class="sub-title">{{ t('strategy.params') }}</h3>
        <div class="table-wrap">
          <el-table :data="detail.params_schema ?? []" size="small" class="resp-table" :empty-text="t('extv.noParams')">
            <el-table-column :label="t('extv.paramKey')" min-width="120"><template #default="{ row }"><code>{{ row.key }}</code></template></el-table-column>
            <el-table-column :label="t('extv.paramLabel')" prop="label" min-width="110" />
            <el-table-column :label="t('extv.paramType')" prop="type" width="90" />
            <el-table-column :label="t('extv.paramDefault')" min-width="90"><template #default="{ row }">{{ defaultText(row) }}</template></el-table-column>
            <el-table-column :label="t('extv.paramRequired')" width="70"><template #default="{ row }">{{ row.required ? t('common.yes') : t('common.no') }}</template></el-table-column>
            <el-table-column :label="t('extv.paramRange')" min-width="110"><template #default="{ row }">{{ range(row) }}</template></el-table-column>
            <el-table-column :label="t('extv.paramHelp')" prop="help" min-width="160" />
          </el-table>
        </div>
      </div>
      <template #footer>
        <el-button @click="detailOpen = false">{{ t('common.close') }}</el-button>
        <el-button v-if="detail?.loaded" type="primary" @click="detail && createInstance(detail)">{{ t('strategy.createInstance') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.cards { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); min-height: 80px; }
.cards .card + .card { margin-top: 0; }
.scard { display: flex; flex-direction: column; gap: 8px; }
.sc-head { display: flex; align-items: center; gap: 10px; }
.sc-icon { width: 40px; height: 40px; border-radius: 8px; background: var(--el-fill-color-light); display: flex; align-items: center; justify-content: center; flex: none; font-size: 20px; overflow: hidden; }
.sc-icon img { width: 100%; height: 100%; object-fit: cover; }
.sc-title { flex: 1; min-width: 0; }
.sc-title h2 { font-size: 15px; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.small { font-size: 12px; }
.sc-desc { margin: 0; font-size: 13px; color: var(--el-text-color-regular); display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 2.6em; }
.sc-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.sc-actions { margin-top: auto; display: flex; gap: 8px; justify-content: flex-end; }
.failed-area { margin-top: 16px; border-color: var(--el-color-danger-light-5); }
.table-wrap { overflow-x: auto; }
.err { white-space: pre-wrap; word-break: break-all; }
.sub-title { font-size: 14px; margin: 16px 0 8px; }
</style>
