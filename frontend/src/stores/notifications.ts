import { defineStore } from 'pinia'
import { http } from '@/api/http'

export type NoticeCategory = 'alert' | 'order' | 'system'

export interface Notice {
  id: string
  /** 后端通知表的 id；仅实时推送、尚未落库的为空 */
  serverId?: number
  category: NoticeCategory
  event: string
  title: string
  body?: string
  link?: string
  ts: number
  read: boolean
}

interface ServerNotice { id: number; level: string; title: string; body?: string | null; source?: string | null; read: boolean; created_at: string }

const MAX = 200

function categoryOf(source: string | null | undefined, level: string): NoticeCategory {
  if (source?.startsWith('order.')) return 'order'
  if (level === 'error' || level === 'warn' || source?.startsWith('risk.') || source?.startsWith('instance.')) return 'alert'
  return 'system'
}

export const useNotifications = defineStore('notifications', {
  state: () => ({ items: [] as Notice[], loaded: false }),
  getters: {
    unread: (s) => s.items.filter((n) => !n.read).length,
  },
  actions: {
    /** 登录后拉取持久化通知（刷新页面、换设备都不会丢） */
    async load() {
      try {
        const r = await http.get<{ items: ServerNotice[] }>('/api/notifications', { page: 1, page_size: 100 }, { silent: true })
        const fromServer: Notice[] = r.items.map((n) => ({
          id: `s${n.id}`, serverId: n.id, category: categoryOf(n.source, n.level), event: n.source ?? 'system',
          title: n.title, body: n.body ?? undefined, ts: Date.parse(n.created_at) || Date.now(), read: n.read,
        }))
        // 实时推送已经到达、但服务端列表里也有的，以服务端为准（按标题+时间近似去重）
        const live = this.items.filter((x) => !x.serverId && !fromServer.some((s) => s.title === x.title && Math.abs(s.ts - x.ts) < 5000))
        this.items = [...live, ...fromServer].sort((a, b) => b.ts - a.ts).slice(0, MAX)
        this.loaded = true
      } catch {
        /* 通知是辅助信息，拉取失败不打断页面 */
      }
    },
    push(n: Omit<Notice, 'id' | 'read' | 'ts'> & { ts?: number }) {
      this.items.unshift({ ...n, id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, ts: n.ts ?? Date.now(), read: false })
      if (this.items.length > MAX) this.items.length = MAX
    },
    markRead(id: string) {
      const n = this.items.find((x) => x.id === id)
      if (!n || n.read) return
      n.read = true
      if (n.serverId) void http.post('/api/notifications/read', { ids: [n.serverId] }, { silent: true }).catch(() => undefined)
    },
    markAllRead() {
      this.items.forEach((n) => (n.read = true))
      void http.post('/api/notifications/read', {}, { silent: true }).catch(() => undefined)
    },
    clear() {
      this.items = []
      this.loaded = false
    },
  },
})
