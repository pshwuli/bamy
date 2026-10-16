<script setup lang="ts">
import { useBreakpoint } from '@/composables/useBreakpoint'

defineProps<{ total: number }>()
const page = defineModel<number>('page', { required: true })
const size = defineModel<number>('size', { required: true })
const { isMobile } = useBreakpoint()
</script>

<template>
  <div class="pager">
    <el-pagination
      v-model:current-page="page"
      v-model:page-size="size"
      :total="total"
      :page-sizes="[10, 20, 50, 100]"
      :layout="isMobile ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper'"
      :pager-count="isMobile ? 5 : 7"
      background
      size="small"
    />
  </div>
</template>

<style scoped>
.pager { display: flex; justify-content: flex-end; margin-top: 12px; overflow-x: auto; }
</style>
