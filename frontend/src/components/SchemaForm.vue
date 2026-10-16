<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { QuestionFilled } from '@element-plus/icons-vue'
import type { SchemaField } from '@/api/types'
import { DEFAULT_GROUP, isSecret, isVisible, validateField, outputValues, type ValidateMsgs } from '@/utils/schema'

const props = withDefaults(
  defineProps<{
    schema: SchemaField[]
    disabled?: boolean
    keepSecret?: boolean
    symbols?: string[]
    externalErrors?: Record<string, string>
    errorPrefix?: string
    labelWidth?: string
  }>(),
  { disabled: false, keepSecret: false, symbols: () => [], externalErrors: () => ({}), errorPrefix: '', labelWidth: '140px' },
)
const model = defineModel<Record<string, unknown>>({ required: true })
const { t } = useI18n()

const errors = reactive<Record<string, string>>({})
const touched = reactive<Record<string, boolean>>({})

const msgs = computed<ValidateMsgs>(() => ({
  required: t('schema.required'),
  min: (n) => t('schema.min', { n }),
  max: (n) => t('schema.max', { n }),
  int: t('schema.int'),
  number: t('schema.number'),
  pattern: t('schema.pattern'),
  maxLength: (n) => t('schema.maxLength', { n }),
  option: t('schema.option'),
}))

const groups = computed(() => {
  const map = new Map<string, SchemaField[]>()
  for (const f of props.schema) {
    const g = f.group || DEFAULT_GROUP
    if (!map.has(g)) map.set(g, [])
    map.get(g)!.push(f)
  }
  return [...map.entries()].map(([name, fields]) => ({ name, fields }))
})
const openGroups = ref<string[]>([])
watch(groups, (g) => (openGroups.value = g.map((x) => x.name)), { immediate: true })

function visible(f: SchemaField) {
  return isVisible(f, model.value, props.schema)
}

function check(f: SchemaField) {
  if (!visible(f)) {
    delete errors[f.key]
    return true
  }
  const e = validateField(f, model.value[f.key], msgs.value, props.keepSecret)
  if (e) errors[f.key] = e
  else delete errors[f.key]
  return !e
}

watch(
  () => ({ ...model.value }),
  () => {
    for (const f of props.schema) if (touched[f.key]) check(f)
  },
  { deep: true },
)

function touch(f: SchemaField) {
  touched[f.key] = true
  check(f)
}

function errorOf(f: SchemaField) {
  return errors[f.key] || props.externalErrors[`${props.errorPrefix}${f.key}`] || props.externalErrors[f.key] || ''
}

function validate(): boolean {
  let ok = true
  for (const f of props.schema) {
    touched[f.key] = true
    if (!check(f)) ok = false
  }
  return ok
}

function values() {
  return outputValues(props.schema, model.value, props.keepSecret)
}

function setVal(key: string, v: unknown) {
  model.value = { ...model.value, [key]: v }
}

const symbolOptions = computed(() => props.symbols.map((s) => ({ value: s, label: s })))
const durationUnits = [
  { v: 1, k: 'schema.sec' },
  { v: 60, k: 'schema.min_' },
  { v: 3600, k: 'schema.hour' },
]
const durationUnit = reactive<Record<string, number>>({})
function durationDisplay(f: SchemaField) {
  const v = model.value[f.key]
  if (v === null || v === undefined || v === '') return null
  const u = durationUnit[f.key] ?? 1
  return Number(v) / u
}
function setDuration(f: SchemaField, n: number | null | undefined) {
  const u = durationUnit[f.key] ?? 1
  setVal(f.key, n === null || n === undefined ? null : Math.round(n * u))
}

function placeholderOf(f: SchemaField) {
  if (props.keepSecret && isSecret(f)) return t('schema.secretKeep')
  return f.placeholder || ''
}

defineExpose({ validate, values })
</script>

<template>
  <el-form :label-width="labelWidth" label-position="right" :disabled="disabled" class="schema-form" @submit.prevent>
    <el-empty v-if="!schema.length" :description="t('schema.noFields')" :image-size="48" />
    <el-collapse v-else v-model="openGroups">
      <el-collapse-item v-for="g in groups" :key="g.name" :name="g.name" :title="g.name === DEFAULT_GROUP ? t('schema.general') : g.name">
        <template v-for="f in g.fields" :key="f.key">
          <el-form-item v-if="visible(f)" :required="f.required" :error="errorOf(f)" :for="`sf-${f.key}`">
            <template #label>
              <span class="sf-label">
                {{ f.label || f.key }}
                <el-tooltip v-if="f.help" :content="f.help" placement="top">
                  <el-icon class="sf-help" :aria-label="f.help"><QuestionFilled /></el-icon>
                </el-tooltip>
              </span>
            </template>

            <el-input-number
              v-if="f.type === 'int' || f.type === 'float'"
              :id="`sf-${f.key}`"
              :model-value="(model[f.key] as number | null) ?? undefined"
              :min="f.min"
              :max="f.max"
              :step="f.step ?? (f.type === 'int' ? 1 : 0.01)"
              :precision="f.type === 'int' ? 0 : undefined"
              :placeholder="placeholderOf(f)"
              controls-position="right"
              class="sf-num"
              @update:model-value="(v: number | undefined) => setVal(f.key, v ?? null)"
              @blur="touch(f)"
            />

            <el-input-number
              v-else-if="f.type === 'percent'"
              :id="`sf-${f.key}`"
              :model-value="(model[f.key] as number | null) ?? undefined"
              :min="f.min ?? 0"
              :max="f.max ?? 100"
              :step="f.step ?? 0.1"
              controls-position="right"
              class="sf-num"
              @update:model-value="(v: number | undefined) => setVal(f.key, v ?? null)"
              @blur="touch(f)"
            />

            <div v-else-if="f.type === 'duration'" class="sf-duration">
              <el-input-number
                :id="`sf-${f.key}`"
                :model-value="durationDisplay(f) ?? undefined"
                :min="0"
                controls-position="right"
                class="sf-num"
                @update:model-value="(v: number | undefined) => setDuration(f, v)"
                @blur="touch(f)"
              />
              <el-select v-model="durationUnit[f.key]" class="sf-unit" :placeholder="t('schema.sec')" :aria-label="t('schema.unit')">
                <el-option v-for="u in durationUnits" :key="u.v" :value="u.v" :label="t(u.k)" />
              </el-select>
            </div>

            <el-switch
              v-else-if="f.type === 'bool'"
              :id="`sf-${f.key}`"
              :model-value="!!model[f.key]"
              @update:model-value="(v: string | number | boolean) => { setVal(f.key, !!v); touch(f) }"
            />

            <el-select
              v-else-if="f.type === 'select'"
              :id="`sf-${f.key}`"
              :model-value="model[f.key] as string"
              :placeholder="placeholderOf(f)"
              :clearable="!f.required"
              class="sf-wide"
              @update:model-value="(v: unknown) => { setVal(f.key, v); touch(f) }"
            >
              <el-option v-for="o in f.options || []" :key="String(o.value)" :value="o.value" :label="o.label" />
            </el-select>

            <el-select
              v-else-if="f.type === 'multiselect'"
              :id="`sf-${f.key}`"
              :model-value="(model[f.key] as unknown[]) || []"
              multiple
              collapse-tags
              collapse-tags-tooltip
              :placeholder="placeholderOf(f)"
              class="sf-wide"
              @update:model-value="(v: unknown[]) => { setVal(f.key, v); touch(f) }"
            >
              <el-option v-for="o in f.options || []" :key="String(o.value)" :value="o.value" :label="o.label" />
            </el-select>

            <el-select
              v-else-if="f.type === 'symbol'"
              :id="`sf-${f.key}`"
              :model-value="model[f.key] as string"
              filterable
              allow-create
              default-first-option
              :placeholder="placeholderOf(f) || 'BTC/USDT'"
              class="sf-wide"
              @update:model-value="(v: string) => { setVal(f.key, (v || '').toUpperCase()); touch(f) }"
            >
              <el-option v-for="o in symbolOptions" :key="o.value" :value="o.value" :label="o.label" />
            </el-select>

            <el-date-picker
              v-else-if="f.type === 'date' || f.type === 'datetime'"
              :id="`sf-${f.key}`"
              :model-value="model[f.key] as string"
              :type="f.type"
              :value-format="f.type === 'date' ? 'YYYY-MM-DD' : 'YYYY-MM-DDTHH:mm:ssZ'"
              :placeholder="placeholderOf(f)"
              @update:model-value="(v: unknown) => { setVal(f.key, v); touch(f) }"
            />

            <el-time-picker
              v-else-if="f.type === 'time'"
              :id="`sf-${f.key}`"
              :model-value="model[f.key] as string"
              value-format="HH:mm:ss"
              :placeholder="placeholderOf(f)"
              @update:model-value="(v: unknown) => { setVal(f.key, v); touch(f) }"
            />

            <el-input
              v-else-if="f.type === 'text'"
              :id="`sf-${f.key}`"
              :model-value="model[f.key] as string"
              type="textarea"
              :rows="3"
              :maxlength="f.max_length"
              :show-word-limit="!!f.max_length"
              :placeholder="placeholderOf(f)"
              @update:model-value="(v: string) => setVal(f.key, v)"
              @blur="touch(f)"
            />

            <el-input
              v-else
              :id="`sf-${f.key}`"
              :model-value="model[f.key] as string"
              :type="isSecret(f) ? 'password' : 'text'"
              :show-password="isSecret(f)"
              :autocomplete="isSecret(f) ? 'new-password' : 'off'"
              :maxlength="f.max_length"
              :placeholder="placeholderOf(f)"
              class="sf-wide"
              @update:model-value="(v: string) => setVal(f.key, v)"
              @blur="touch(f)"
            >
              <template v-if="f.suffix" #append>{{ f.suffix }}</template>
            </el-input>

            <span v-if="f.suffix && f.type !== 'string' && f.type !== 'secret'" class="sf-suffix">{{ f.suffix }}</span>
          </el-form-item>
        </template>
      </el-collapse-item>
    </el-collapse>
  </el-form>
</template>

<style scoped>
.sf-label { display: inline-flex; align-items: center; gap: 4px; }
.sf-help { color: var(--el-text-color-secondary); cursor: help; }
.sf-num { width: 200px; }
.sf-wide { width: 100%; max-width: 420px; }
.sf-duration { display: flex; gap: 8px; }
.sf-unit { width: 90px; }
.sf-suffix { margin-left: 8px; color: var(--el-text-color-secondary); }
.schema-form :deep(.el-collapse-item__header) { font-weight: 600; }
@media (max-width: 767px) { .sf-num { width: 100%; } }
</style>
