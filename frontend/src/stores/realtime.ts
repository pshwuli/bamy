import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { ElNotification } from 'element-plus'
import { P } from '@/api/paths'
import { RealtimeSocket, wsUrl, type WsMessage, type WsState } from '@/utils/socket'
import { tokenStore } from '@/utils/token'
import { useNotifications, type NoticeCategory } from './notifications'
import { t } from '@/i18n'

const KEY_EVENTS: Record<string, NoticeCategory> = {
  'instance.error': 'alert',
  'risk.alert': 'alert',
  'circuit_breaker.triggered': 'alert',
  'system.alert': 'system',
  'order.filled': 'order',
  'order.failed': 'order',
  'account.status': 'system',
  'plugin.auto_disabled': 'alert',
  'binding.auto_disabled': 'alert',
}
const POPUP = new Set(['instance.error', 'risk.alert', 'circuit_breaker.triggered', 'system.alert', 'order.failed', 'plugin.auto_disabled', 'binding.auto_disabled'])

function describe(msg: WsMessage): { title: string; body?: string; link?: string } {
  const d = (msg.data ?? {}) as Record<string, unknown>
  const name = (d.instance_name || d.name || d.plugin_name || d.plugin_id || '') as string
  const text = (d.message || d.reason || d.error || '') as string
  let link: string | undefined
  if (d.instance_id) link = `/instances/${d.instance_id}`
  else if (d.account_id && msg.event.startsWith('account')) link = `/accounts/${d.account_id}`
  else if (msg.event.includes('auto_disabled')) link = '/my-plugins'
  return { title: `${msg.event}${name ? ` · ${name}` : ''}`, body: text || undefined, link }
}

export const useRealtime = defineStore('realtime', {
  state: () => ({
    socket: null as RealtimeSocket | null,
    state: 'idle' as WsState,
    channel: '' as string,
  }),
  getters: {
    live: (s) => s.state === 'open',
    polling: (s) => s.state === 'polling',
  },
  actions: {
    start(admin: boolean) {
      const channel = admin ? P.ws.admin : P.ws.user
      if (this.socket && this.channel === channel) return
      this.stop()
      this.channel = channel
      const sock = markRaw(new RealtimeSocket(() => (tokenStore.access ? wsUrl(channel, tokenStore.access) : null)))
      sock.onState((s) => (this.state = s))
      sock.on((m) => this.handle(m))
      this.socket = sock
      sock.connect()
    },
    stop() {
      this.socket?.close()
      this.socket = null
      this.state = 'idle'
    },
    handle(msg: WsMessage) {
      const cat = KEY_EVENTS[msg.event]
      if (!cat) return
      const { title, body, link } = describe(msg)
      useNotifications().push({ category: cat, event: msg.event, title, body, link, ts: msg.ts ?? msg.timestamp })
      if (POPUP.has(msg.event)) {
        ElNotification({ title: t('header.notifications'), message: body ? `${title}: ${body}` : title, type: cat === 'alert' ? 'warning' : 'info', duration: 6000 })
      }
    },
    subscribe(topics: string[]) {
      this.socket?.subscribe(topics)
    },
    unsubscribe(topics: string[]) {
      this.socket?.unsubscribe(topics)
    },
    on(h: (m: WsMessage) => void) {
      return this.socket?.on(h) ?? (() => false)
    },
  },
})
