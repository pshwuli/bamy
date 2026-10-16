import { defineStore } from 'pinia'
import { setLocale, type Locale } from '@/i18n'
import { userApi, type Preferences } from '@/api/user'

export type ThemeMode = 'light' | 'dark' | 'auto'
const KEY = 'bamy.prefs'

interface State {
  theme: ThemeMode
  locale: Locale
  timeFormat: '24h' | '12h'
  timezone: string
  pageSize: number
  sidebarCollapsed: boolean
  systemDark: boolean
}

function load(): Partial<State> {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '{}')
  } catch {
    return {}
  }
}

const media = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null

export const usePrefs = defineStore('prefs', {
  state: (): State => ({
    theme: 'auto',
    locale: (localStorage.getItem('bamy.locale') as Locale) || 'zh-CN',
    timeFormat: '24h',
    timezone: 'Asia/Shanghai',
    pageSize: 20,
    sidebarCollapsed: false,
    systemDark: media?.matches ?? false,
    ...load(),
  }),
  getters: {
    isDark: (s) => (s.theme === 'auto' ? s.systemDark : s.theme === 'dark'),
  },
  actions: {
    init() {
      media?.addEventListener('change', (e) => {
        this.systemDark = e.matches
        this.applyTheme()
      })
      this.applyTheme()
      setLocale(this.locale)
    },
    persist() {
      const { theme, locale, timeFormat, timezone, pageSize, sidebarCollapsed } = this
      localStorage.setItem(KEY, JSON.stringify({ theme, locale, timeFormat, timezone, pageSize, sidebarCollapsed }))
    },
    applyTheme() {
      document.documentElement.classList.toggle('dark', this.isDark)
      document.documentElement.style.colorScheme = this.isDark ? 'dark' : 'light'
    },
    setTheme(t: ThemeMode, sync = true) {
      this.theme = t
      this.applyTheme()
      this.persist()
      if (sync) void this.push({ theme: t })
    },
    cycleTheme() {
      const order: ThemeMode[] = ['light', 'dark', 'auto']
      this.setTheme(order[(order.indexOf(this.theme) + 1) % order.length])
    },
    setLocale(l: Locale, sync = true) {
      this.locale = l
      setLocale(l)
      this.persist()
      if (sync) void this.push({ language: l })
    },
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
      this.persist()
    },
    applyRemote(p: Partial<Preferences>) {
      if (p.theme) this.setTheme(p.theme, false)
      if (p.language) this.setLocale(p.language, false)
      if (p.time_format) this.timeFormat = p.time_format
      if (p.timezone) this.timezone = p.timezone
      if (p.page_size) this.pageSize = p.page_size
      this.persist()
    },
    async pull() {
      try {
        const p = await userApi.preferences()
        if (p) this.applyRemote(p)
      } catch {
        /* preferences are optional */
      }
    },
    async push(p: Partial<Preferences>) {
      try {
        await userApi.updatePreferences(p)
      } catch {
        /* keep local value */
      }
    },
  },
})
