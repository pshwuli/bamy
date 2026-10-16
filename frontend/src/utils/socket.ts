export interface WsMessage<T = unknown> {
  event: string
  topic?: string
  data: T
  ts?: number
  timestamp?: number
}

export type WsState = 'idle' | 'connecting' | 'open' | 'polling'
type Handler = (msg: WsMessage) => void

const MAX_FAILS_BEFORE_POLLING = 3
const MAX_BACKOFF = 30000

export class RealtimeSocket {
  private ws: WebSocket | null = null
  private url: () => string | null
  private topics = new Map<string, number>()
  private handlers = new Set<Handler>()
  private stateHandlers = new Set<(s: WsState) => void>()
  private fails = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private closedByUser = false
  state: WsState = 'idle'

  constructor(url: () => string | null) {
    this.url = url
  }

  private setState(s: WsState) {
    this.state = s
    this.stateHandlers.forEach((h) => h(s))
  }

  onState(h: (s: WsState) => void) {
    this.stateHandlers.add(h)
    return () => this.stateHandlers.delete(h)
  }

  on(h: Handler) {
    this.handlers.add(h)
    return () => this.handlers.delete(h)
  }

  connect() {
    this.closedByUser = false
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return
    const u = this.url()
    if (!u || typeof WebSocket === 'undefined') {
      this.setState('polling')
      return
    }
    if (this.state !== 'polling') this.setState('connecting')
    let ws: WebSocket
    try {
      ws = new WebSocket(u)
    } catch {
      this.scheduleReconnect()
      return
    }
    this.ws = ws
    ws.onopen = () => {
      this.fails = 0
      this.setState('open')
      this.sendSubscribe([...this.topics.keys()])
    }
    ws.onmessage = (ev) => {
      let msg: WsMessage
      try {
        msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '')
      } catch {
        return
      }
      if (!msg || typeof msg.event !== 'string') return
      this.handlers.forEach((h) => h(msg))
    }
    ws.onclose = () => {
      this.ws = null
      if (!this.closedByUser) this.scheduleReconnect()
    }
    ws.onerror = () => {
      try {
        ws.close()
      } catch {
        /* ignore */
      }
    }
  }

  private scheduleReconnect() {
    this.fails++
    if (this.fails >= MAX_FAILS_BEFORE_POLLING) this.setState('polling')
    else this.setState('connecting')
    const delay = Math.min(MAX_BACKOFF, 1000 * 2 ** Math.min(this.fails, 5)) + Math.random() * 500
    if (this.timer) clearTimeout(this.timer)
    this.timer = setTimeout(() => this.connect(), delay)
  }

  private sendSubscribe(topics: string[], action: 'subscribe' | 'unsubscribe' = 'subscribe') {
    if (!topics.length || !this.ws || this.ws.readyState !== WebSocket.OPEN) return
    this.ws.send(JSON.stringify({ action, topics }))
  }

  subscribe(topics: string[]) {
    const fresh: string[] = []
    for (const t of topics) {
      const n = this.topics.get(t) ?? 0
      if (n === 0) fresh.push(t)
      this.topics.set(t, n + 1)
    }
    this.sendSubscribe(fresh)
  }

  unsubscribe(topics: string[]) {
    const gone: string[] = []
    for (const t of topics) {
      const n = (this.topics.get(t) ?? 0) - 1
      if (n <= 0) {
        this.topics.delete(t)
        gone.push(t)
      } else this.topics.set(t, n)
    }
    this.sendSubscribe(gone, 'unsubscribe')
  }

  close() {
    this.closedByUser = true
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    this.fails = 0
    try {
      this.ws?.close()
    } catch {
      /* ignore */
    }
    this.ws = null
    this.setState('idle')
  }
}

export function wsUrl(path: string, token?: string | null): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const q = token ? `?token=${encodeURIComponent(token)}` : ''
  return `${proto}//${location.host}${path}${q}`
}
