<script setup lang="ts">
// 筛选栏。桌面：一行排开。手机：第一个控件（通常是搜索框）常驻，其余折叠在"筛选"按钮后面，
// 按钮上显示当前生效的筛选数，避免一进页面就是一屏下拉框。
import { Comment, computed, ref, useSlots, type VNode } from 'vue'
import { useI18n } from 'vue-i18n'
import { Filter, Search, RefreshLeft } from '@element-plus/icons-vue'
import { useBreakpoint } from '@/composables/useBreakpoint'

const props = defineProps<{ loading?: boolean; active?: number }>()
const emit = defineEmits<{ search: []; reset: [] }>()
const { t } = useI18n()
const { isMobile } = useBreakpoint()
const slots = useSlots()
const open = ref(false)

// 把默认插槽拆成"第一个"和"其余"，手机上只常驻第一个
const nodes = computed<VNode[]>(() => (slots.default?.() ?? []).flatMap((n) => (Array.isArray(n.children) && typeof n.type === 'symbol' ? (n.children as VNode[]) : [n])).filter((n) => n.type !== Comment))
const first = computed(() => nodes.value.slice(0, 1))
const rest = computed(() => nodes.value.slice(1))
const badge = computed(() => props.active ?? 0)
function doSearch() { emit('search'); if (isMobile.value) open.value = false }
</script>

<template>
  <form class="filter-bar" :class="{ mobile: isMobile }" role="search" @submit.prevent="doSearch">
    <template v-if="!isMobile">
      <div class="fb-fields"><slot /></div>
      <div class="fb-actions">
        <el-button type="primary" native-type="submit" :icon="Search" :loading="loading">{{ t('common.search') }}</el-button>
        <el-button :icon="RefreshLeft" @click="emit('reset')">{{ t('common.clearFilters') }}</el-button>
        <slot name="extra" />
      </div>
    </template>
    <template v-else>
      <div class="fb-row">
        <div class="fb-first"><component :is="n" v-for="(n, i) in first" :key="i" /></div>
        <el-button v-if="rest.length" class="fb-toggle" :class="{ on: open || badge }" :icon="Filter" :aria-expanded="open" @click="open = !open">
          {{ t('fbx.filter') }}<span v-if="badge" class="fb-badge num">{{ badge }}</span>
        </el-button>
        <el-button v-else type="primary" native-type="submit" :icon="Search" :loading="loading" :aria-label="t('common.search')" />
      </div>
      <div v-show="open" class="fb-more">
        <component :is="n" v-for="(n, i) in rest" :key="i" />
        <div class="fb-actions">
          <el-button @click="emit('reset'); open = false">{{ t('common.clearFilters') }}</el-button>
          <el-button type="primary" native-type="submit" :loading="loading">{{ t('fbx.apply') }}</el-button>
        </div>
        <div v-if="$slots.extra" class="fb-extra"><slot name="extra" /></div>
      </div>
    </template>
  </form>
</template>

<style scoped>
.filter-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
.fb-fields { display: flex; gap: 8px; flex-wrap: wrap; flex: 1 1 auto; }
.fb-fields > :deep(*) { width: 180px; }
.fb-fields > :deep(.el-input:first-child) { width: 240px; }
.fb-fields > :deep(.el-date-editor) { width: 320px; }
.fb-actions { display: flex; gap: 8px; }
.filter-bar.mobile { flex-direction: column; align-items: stretch; gap: 8px; }
.fb-row { display: flex; gap: 8px; }
.fb-first { flex: 1; min-width: 0; }
.fb-first > :deep(*) { width: 100% !important; }
.fb-toggle { flex-shrink: 0; }
.fb-toggle.on { border-color: var(--accent); color: var(--accent); }
.fb-badge { margin-left: 6px; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; background: var(--accent); color: var(--accent-ink); font-size: 11px; display: inline-grid; place-items: center; }
.fb-more { display: grid; gap: 8px; padding: 12px; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; }
.fb-more > :deep(*) { width: 100% !important; }
.fb-more .fb-actions { display: grid; grid-template-columns: 1fr 1fr; margin-top: 4px; }
.fb-more .fb-actions :deep(.el-button) { margin: 0; }
.fb-extra { display: flex; gap: 8px; flex-wrap: wrap; }
</style>
