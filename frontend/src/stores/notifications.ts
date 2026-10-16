import { defineStore } from 'pinia'

export type NoticeCategory = 'alert' | 'order' | 'system'

export interface Notice {
  id: string
  category: NoticeCategory
  event: string
  title: string
  body?: string
  link?: string
  ts: number
  read: boolean
}

const MAX = 200

export const useNotifications = defineStore('notifications', {
  state: () => ({ items: [] as Notice[] }),
  getters: {
    unread: (s) => s.items.filter((n) => !n.read).length,
  },
  actions: {
    push(n: Omit<Notice, 'id' | 'read' | 'ts'> & { ts?: number }) {
      this.items.unshift({ ...n, id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, ts: n.ts ?? Date.now(), read: false })
      if (this.items.length > MAX) this.items.length = MAX
    },
    markRead(id: string) {
      const n = this.items.find((x) => x.id === id)
      if (n) n.read = true
    },
    markAllRead() {
      this.items.forEach((n) => (n.read = true))
    },
    clear() {
      this.items = []
    },
  },
})
