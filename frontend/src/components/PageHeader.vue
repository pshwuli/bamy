<script setup lang="ts">
import { useRouter } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'

defineProps<{ title: string; subtitle?: string; back?: boolean }>()
const router = useRouter()
function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/')
}
</script>

<template>
  <div class="page-header">
    <div class="ph-left">
      <el-button v-if="back" :icon="ArrowLeft" circle size="small" :aria-label="$t('common.back')" @click="goBack" />
      <div>
        <h1 class="ph-title"><slot name="title">{{ title }}</slot></h1>
        <div v-if="subtitle || $slots.sub" class="ph-sub"><slot name="sub">{{ subtitle }}</slot></div>
      </div>
    </div>
    <div class="ph-actions"><slot /></div>
  </div>
</template>

<style scoped>
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.ph-left { display: flex; gap: 12px; align-items: center; min-width: 0; }
.ph-title { margin: 0; font-size: 20px; font-weight: 600; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ph-sub { color: var(--el-text-color-secondary); font-size: 13px; margin-top: 4px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.ph-actions { display: flex; gap: 8px; flex-wrap: wrap; }
@media (max-width: 767px) { .ph-actions { width: 100%; } .ph-actions :deep(.el-button) { flex: 1 1 auto; margin-left: 0; } }
</style>
