import { onBeforeUnmount, onMounted, watch } from 'vue'
import { useRealtime } from '@/stores/realtime'
import type { WsMessage } from '@/utils/socket'

export const POLL_MS = Number(import.meta.env.VITE_POLL_INTERVAL_MS) || 15000

export function useLive(opts: {
  topics?: () => string[]
  events?: string[]
  onEvent?: (msg: WsMessage) => void
  poll?: () => unknown
  interval?: number
}) {
  const rt = useRealtime()
  let off: (() => unknown) | null = null
  let timer: ReturnType<typeof setInterval> | null = null
  let current: string[] = []

  function bind() {
    off?.()
    off = rt.on((m) => {
      if (opts.events && !opts.events.includes(m.event)) return
      opts.onEvent?.(m)
    })
  }

  function resub() {
    const next = opts.topics?.() ?? []
    if (current.length) rt.unsubscribe(current)
    if (next.length) rt.subscribe(next)
    current = next
  }

  function syncPolling() {
    const need = !!opts.poll && rt.state !== 'open'
    if (need && !timer) timer = setInterval(() => opts.poll?.(), opts.interval ?? POLL_MS)
    if (!need && timer) {
      clearInterval(timer)
      timer = null
    }
  }

  onMounted(() => {
    bind()
    resub()
    syncPolling()
  })

  const stopState = watch(
    () => rt.state,
    (s, prev) => {
      syncPolling()
      if (s === 'open' && prev !== 'open') bind()
    },
  )
  const stopSock = watch(
    () => rt.socket,
    () => {
      bind()
      resub()
    },
  )

  onBeforeUnmount(() => {
    stopState()
    stopSock()
    off?.()
    if (current.length) rt.unsubscribe(current)
    if (timer) clearInterval(timer)
  })

  return { resubscribe: resub }
}
