import { defineStore } from 'pinia'
import { authApi } from '@/api/auth'
import type { Role, User } from '@/api/types'
import { tokenStore } from '@/utils/token'

const USER_KEY = 'bamy.user'
const rank: Record<Role, number> = { user: 1, admin: 2, super_admin: 3 }

function cachedUser(): User | null {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch {
    return null
  }
}

export const useAuth = defineStore('auth', {
  state: () => ({
    user: cachedUser() as User | null,
    initialized: null as boolean | null,
    loaded: false,
  }),
  getters: {
    loggedIn: (s) => !!s.user && !!tokenStore.access,
    role: (s): Role => s.user?.role ?? 'user',
    isAdmin(): boolean {
      return rank[this.role] >= rank.admin && this.loggedIn
    },
    isSuper(): boolean {
      return this.role === 'super_admin' && this.loggedIn
    },
  },
  actions: {
    hasRole(r: Role) {
      return this.loggedIn && rank[this.role] >= rank[r]
    },
    setUser(u: User | null) {
      this.user = u
      if (u) localStorage.setItem(USER_KEY, JSON.stringify(u))
      else localStorage.removeItem(USER_KEY)
    },
    async checkSetup(): Promise<boolean> {
      if (this.initialized === true) return true
      try {
        const r = await authApi.setupStatus()
        this.initialized = !!r?.initialized
      } catch {
        this.initialized = true
      }
      return this.initialized
    },
    async login(username: string, password: string, two_factor_code?: string) {
      const r = await authApi.login({ username, password, two_factor_code: two_factor_code || undefined })
      tokenStore.set(r.access_token, r.refresh_token)
      this.setUser(r.user)
      this.loaded = false
      await this.fetchMe()
    },
    async fetchMe() {
      if (!tokenStore.access) return
      try {
        const me = await authApi.me()
        this.setUser({ ...this.user, ...me })
      } catch {
        /* handled by http hooks */
      } finally {
        this.loaded = true
      }
    },
    async logout(all = false) {
      try {
        if (tokenStore.access) await authApi.logout(all, tokenStore.refresh)
      } catch {
        /* ignore */
      }
      this.clear()
    },
    clear() {
      tokenStore.clear()
      this.setUser(null)
      this.loaded = false
    },
  },
})
