<script setup lang="ts">
import { Search, RefreshLeft } from '@element-plus/icons-vue'

defineProps<{ loading?: boolean }>()
const emit = defineEmits<{ search: []; reset: [] }>()
</script>

<template>
  <div class="filter-bar" role="search">
    <slot />
    <div class="fb-actions">
      <el-button type="primary" :icon="Search" :loading="loading" @click="emit('search')">{{ $t('common.search') }}</el-button>
      <el-button :icon="RefreshLeft" @click="emit('reset')">{{ $t('common.clearFilters') }}</el-button>
      <slot name="extra" />
    </div>
  </div>
</template>

<style scoped>
.filter-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.filter-bar :deep(.el-input), .filter-bar :deep(.el-select) { width: 180px; }
.filter-bar :deep(.el-date-editor.el-input__wrapper) { width: 360px; }
.fb-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.fb-actions :deep(.el-button + .el-button) { margin-left: 0; }
@media (max-width: 767px) {
  .filter-bar :deep(.el-input), .filter-bar :deep(.el-select), .filter-bar :deep(.el-date-editor.el-input__wrapper) { width: 100%; }
}
</style>
