<script setup lang="ts">
defineProps<{ title?: string; description?: string; actionText?: string }>()
const emit = defineEmits<{ action: [] }>()
</script>

<template>
  <el-empty :description="title || $t('common.empty')" :image-size="80">
    <p v-if="description" class="es-desc">{{ description }}</p>
    <el-button v-if="actionText" type="primary" @click="emit('action')">{{ actionText }}</el-button>
    <slot />
  </el-empty>
</template>

<style scoped>
.es-desc { color: var(--el-text-color-secondary); font-size: 13px; margin: 0 0 12px; max-width: 420px; }
</style>
