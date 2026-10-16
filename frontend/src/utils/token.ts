const ACCESS = 'bamy.access_token'
const REFRESH = 'bamy.refresh_token'

export const tokenStore = {
  get access(): string | null {
    return localStorage.getItem(ACCESS)
  },
  get refresh(): string | null {
    return localStorage.getItem(REFRESH)
  },
  set(access: string, refresh?: string) {
    localStorage.setItem(ACCESS, access)
    if (refresh) localStorage.setItem(REFRESH, refresh)
  },
  clear() {
    localStorage.removeItem(ACCESS)
    localStorage.removeItem(REFRESH)
  },
}
