<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaField } from '@/api/types'
import { isSecret } from '@/utils/schema'

const props = defineProps<{ params: Record<string, unknown> | null | undefined; schema?: SchemaField[] }>()

const rows = computed(() => {
  const p = props.params ?? {}
  const schema = props.schema ?? []
  const keys = new Set([...schema.map((f) => f.key), ...Object.keys(p)])
  return [...keys].map((k) => {
    const f = schema.find((x) => x.key === k)
    let v = p[k]
    if (f && isSecret(f)) v = v ? '******' : ''
    if (f?.options && v !== undefined) {
      const opts = f.options
      const label = (x: unknown) => opts.find((o) => String(o.value) === String(x))?.label ?? String(x)
      v = Array.isArray(v) ? v.map(label).join(', ') : label(v)
    }
    const display = v === undefined || v === null || v === '' ? '-' : typeof v === 'object' ? JSON.stringify(v) : String(v)
    return { key: k, label: f?.label || k, value: display + (f?.suffix && display !== '-' ? ` ${f.suffix}` : '') }
  })
})
</script>

<template>
  <el-descriptions :column="2" border size="small" class="params-view">
    <el-descriptions-item v-for="r in rows" :key="r.key" :label="r.label">{{ r.value }}</el-descriptions-item>
  </el-descriptions>
  <el-empty v-if="!rows.length" :image-size="48" />
</template>
