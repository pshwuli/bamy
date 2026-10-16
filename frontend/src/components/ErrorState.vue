<script setup lang="ts">
import { computed } from 'vue'
import { describeError } from '@/composables/useErrors'

const props = defineProps<{ error: unknown }>()
const emit = defineEmits<{ retry: [] }>()
const msg = computed(() => describeError(props.error))
</script>

<template>
  <el-result icon="error" :title="msg">
    <template #extra>
      <el-button type="primary" @click="emit('retry')">{{ $t('common.retry') }}</el-button>
    </template>
  </el-result>
</template>
