import type { SchemaField } from '@/api/types'

export const DEFAULT_GROUP = '__default__'

export function isSecret(f: SchemaField) {
  return f.type === 'secret' || f.secret === true
}

export function emptyValue(f: SchemaField): unknown {
  switch (f.type) {
    case 'bool':
      return false
    case 'multiselect':
      return []
    case 'int':
    case 'float':
    case 'percent':
    case 'duration':
      return null
    default:
      return ''
  }
}

export function defaultsOf(schema: SchemaField[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const f of schema) {
    out[f.key] = f.default !== undefined ? clone(f.default) : emptyValue(f)
  }
  return out
}

function clone<T>(v: T): T {
  return v && typeof v === 'object' ? JSON.parse(JSON.stringify(v)) : v
}

export function mergeValues(schema: SchemaField[], current: Record<string, unknown> | null | undefined) {
  const base = defaultsOf(schema)
  if (current) for (const [k, v] of Object.entries(current)) base[k] = clone(v)
  return base
}

function looseEq(a: unknown, b: unknown): boolean {
  if (Array.isArray(b)) return b.some((x) => looseEq(a, x))
  if (typeof a === 'boolean' || typeof b === 'boolean') return String(a) === String(b)
  return a === b || String(a) === String(b)
}

export function isVisible(f: SchemaField, values: Record<string, unknown>, schema: SchemaField[]): boolean {
  if (!f.depends_on) return true
  const parent = schema.find((x) => x.key === f.depends_on!.key)
  if (parent && !isVisible(parent, values, schema)) return false
  return looseEq(values[f.depends_on.key], f.depends_on.value)
}

export function isEmpty(v: unknown): boolean {
  return v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0)
}

export interface ValidateMsgs {
  required: string
  min: (n: number) => string
  max: (n: number) => string
  int: string
  number: string
  pattern: string
  maxLength: (n: number) => string
  option: string
}

export function validateField(f: SchemaField, v: unknown, msgs: ValidateMsgs, keepSecret = false): string | null {
  if (isEmpty(v)) {
    if (f.required && !(keepSecret && isSecret(f))) return msgs.required
    return null
  }
  switch (f.type) {
    case 'int':
    case 'float':
    case 'percent':
    case 'duration': {
      const n = Number(v)
      if (!Number.isFinite(n)) return msgs.number
      if (f.type === 'int' && !Number.isInteger(n)) return msgs.int
      const min = f.min ?? (f.type === 'percent' ? 0 : undefined)
      const max = f.max ?? (f.type === 'percent' ? 100 : undefined)
      if (min !== undefined && n < min) return msgs.min(min)
      if (max !== undefined && n > max) return msgs.max(max)
      break
    }
    case 'string':
    case 'text':
    case 'secret':
    case 'symbol': {
      const s = String(v)
      if (f.max_length && s.length > f.max_length) return msgs.maxLength(f.max_length)
      if (f.pattern) {
        try {
          if (!new RegExp(f.pattern).test(s)) return msgs.pattern
        } catch {
          /* invalid regex in schema: skip */
        }
      }
      if (f.type === 'symbol' && !/^[A-Z0-9]{2,15}\/[A-Z0-9]{2,15}$/.test(s)) return msgs.pattern
      break
    }
    case 'select':
      if (f.options?.length && !f.options.some((o) => looseEq(v, o.value))) return msgs.option
      break
    case 'multiselect':
      if (f.options?.length && Array.isArray(v) && !v.every((x) => f.options!.some((o) => looseEq(x, o.value)))) return msgs.option
      break
  }
  return null
}

export function outputValues(schema: SchemaField[], values: Record<string, unknown>, keepSecret = false) {
  const out: Record<string, unknown> = {}
  for (const f of schema) {
    if (!isVisible(f, values, schema)) continue
    const v = values[f.key]
    if (keepSecret && isSecret(f) && (isEmpty(v) || /^\*+$/.test(String(v)))) continue
    if (isEmpty(v) && !f.required && f.type !== 'bool') continue
    if (['int', 'float', 'percent', 'duration'].includes(f.type) && !isEmpty(v)) out[f.key] = Number(v)
    else out[f.key] = v
  }
  return out
}
