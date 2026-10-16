import { createI18n } from 'vue-i18n'
import zhCN from './zh-CN'
import enUS from './en-US'
import zhPages from './zh-pages'
import enPages from './en-pages'
import { applyPatches } from './patch'

export type Locale = 'zh-CN' | 'en-US'
const KEY = 'bamy.locale'

function initial(): Locale {
  const saved = localStorage.getItem(KEY)
  return saved === 'en-US' ? 'en-US' : 'zh-CN'
}

export const i18n = createI18n({
  legacy: false,
  locale: initial(),
  fallbackLocale: 'zh-CN',
  missingWarn: false,
  fallbackWarn: false,
  messages: { 'zh-CN': { ...zhCN, ...zhPages }, 'en-US': { ...enUS, ...enPages } },
})

export function setLocale(l: Locale) {
  i18n.global.locale.value = l
  localStorage.setItem(KEY, l)
  document.documentElement.lang = l
}

export const t = (key: string, args?: Record<string, unknown>): string =>
  args ? i18n.global.t(key, args) : i18n.global.t(key)

export const te = (key: string): boolean => i18n.global.te(key) || i18n.global.te(key, 'zh-CN')

applyPatches((locale, msgs) => i18n.global.mergeLocaleMessage(locale, msgs as never))
