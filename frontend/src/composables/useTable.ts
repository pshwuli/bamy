import { reactive, ref, shallowRef, watch } from 'vue'
import type { Filters, Page, Pagination, Query } from '@/api/types'
import { usePrefs } from '@/stores/prefs'

export function useTable<T, F extends Filters = Filters>(
  fetcher: (q: Query) => Promise<Page<T> | T[] | { items: T[] }>,
  initialFilters: F = {} as F,
  opts: { immediate?: boolean; pageSize?: number; transform?: (q: Query) => Query } = {},
) {
  const prefs = usePrefs()
  const items = shallowRef<T[]>([])
  const loading = ref(false)
  const loadedOnce = ref(false)
  const error = ref<unknown>(null)
  const page = ref(1)
  const pageSize = ref(opts.pageSize ?? prefs.pageSize ?? 20)
  const total = ref(0)
  const pagination = ref<Pagination | null>(null)
  const filters = reactive({ ...initialFilters }) as F

  function query(): Query {
    const q: Query = { page: page.value, page_size: pageSize.value }
    for (const [k, v] of Object.entries(filters)) {
      if (v === '' || v === null || v === undefined) continue
      if (Array.isArray(v) && v.length === 0) continue
      q[k] = v as Query[string]
    }
    return opts.transform ? opts.transform(q) : q
  }

  async function load() {
    loading.value = true
    error.value = null
    try {
      const d = await fetcher(query())
      if (Array.isArray(d)) {
        items.value = d
        total.value = d.length
        pagination.value = null
      } else {
        items.value = d?.items ?? []
        const pg = (d as Page<T>).pagination
        pagination.value = pg ?? null
        total.value = pg?.total ?? items.value.length
      }
    } catch (e) {
      error.value = e
    } finally {
      loading.value = false
      loadedOnce.value = true
    }
  }

  function search() {
    page.value = 1
    return load()
  }

  function resetFilters() {
    for (const k of Object.keys(filters)) (filters as Record<string, unknown>)[k] = (initialFilters as Record<string, unknown>)[k]
    return search()
  }

  watch([page, pageSize], () => load())
  if (opts.immediate !== false) void load()

  return { items, loading, loadedOnce, error, page, pageSize, total, pagination, filters, load, search, resetFilters, query }
}
