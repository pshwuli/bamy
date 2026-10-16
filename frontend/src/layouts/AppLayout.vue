<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Expand, Fold, Moon, Sunny, Monitor, ArrowDown, Odometer, Cpu, List, Collection, Grid, Plus } from '@element-plus/icons-vue'
import { MENU } from '@/router/menu'
import { useAuth } from '@/stores/auth'
import { usePrefs, type ThemeMode } from '@/stores/prefs'
import { useRealtime } from '@/stores/realtime'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { Locale } from '@/i18n'
import NotificationCenter from './NotificationCenter.vue'
import BrandMark from '@/components/BrandMark.vue'

const auth = useAuth()
const prefs = usePrefs()
const rt = useRealtime()
const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const { isMobile, isTablet } = useBreakpoint()

const sheet = ref(false)
const groups = computed(() => MENU.filter((g) => auth.hasRole(g.role)))
const collapsed = computed(() => (isTablet.value ? true : prefs.sidebarCollapsed))
const active = computed(() => {
  const all = groups.value.flatMap((g) => g.items.map((i) => i.path))
  let best = ''
  for (const p of all) if ((route.path === p || route.path.startsWith(`${p}/`)) && p.length > best.length) best = p
  if (route.path.startsWith('/admin/plugins') || route.path.startsWith('/admin/strategies')) return route.path.split('/').slice(0, 3).join('/')
  return best || route.path
})

// 手机底部导航：四个高频入口 + "更多"（打开全部页面）
const TABS = [
  { path: '/dashboard', label: 'tab.home', icon: Odometer },
  { path: '/instances', label: 'tab.instances', icon: Cpu },
  { path: '/orders', label: 'tab.orders', icon: List },
  { path: '/strategies', label: 'tab.strategies', icon: Collection },
]
const tabActive = computed(() => TABS.find((x) => route.path === x.path || route.path.startsWith(`${x.path}/`))?.path ?? '')

const crumbs = computed(() => {
  const list: { label: string; to?: string }[] = []
  for (const r of route.matched) {
    const bc = r.meta.breadcrumb as { label: string; to?: string }[] | undefined
    if (bc) list.push(...bc)
    if (r.meta.title) list.push({ label: r.meta.title as string })
  }
  const dyn = route.meta.dynamicTitle as string | undefined
  if (dyn) list[list.length - 1] = { label: dyn }
  return list
})
const pageTitle = computed(() => {
  const last = crumbs.value[crumbs.value.length - 1]
  return last ? t(last.label) : t('app.name')
})

const themeIcon = computed(() => (prefs.theme === 'dark' ? Moon : prefs.theme === 'light' ? Sunny : Monitor))

const now = ref(new Date())
let clock: ReturnType<typeof setInterval>
onMounted(() => (clock = setInterval(() => (now.value = new Date()), 1000)))
onBeforeUnmount(() => clearInterval(clock))
const nowText = computed(() =>
  now.value.toLocaleTimeString('zh-CN', { timeZone: prefs.timezone === 'local' ? undefined : prefs.timezone, hour12: prefs.timeFormat === '12h' }),
)

const wsLabel = computed(() => (rt.state === 'open' ? t('header.wsConnected') : rt.state === 'polling' ? t('header.wsPolling') : t('header.wsConnecting')))
const wsTone = computed(() => (rt.state === 'open' ? 'ok' : rt.state === 'polling' ? 'warn' : 'pending'))

watch(() => route.fullPath, () => (sheet.value = false))

function go(path: string) {
  router.push(path)
}

async function onUserCmd(cmd: string) {
  if (cmd === 'settings') router.push('/settings')
  if (cmd === 'logout') {
    rt.stop()
    await auth.logout()
    router.replace('/')
  }
}
const version = __APP_VERSION__
</script>

<template>
  <div class="shell" :class="{ mobile: isMobile }">
    <!-- 桌面 / 平板：侧边栏 -->
    <aside v-if="!isMobile" class="rail" :class="{ collapsed }">
      <router-link to="/dashboard" class="rail-brand" :aria-label="t('app.name')">
        <BrandMark :size="28" :wordmark="!collapsed" />
      </router-link>
      <el-scrollbar class="rail-scroll">
        <nav :aria-label="t('shell.menu')">
          <el-menu :default-active="active" :collapse="collapsed" :collapse-transition="false" @select="go">
            <el-menu-item-group v-for="g in groups" :key="g.label">
              <template #title><span v-if="!collapsed">{{ t(g.label) }}</span></template>
              <el-menu-item v-for="it in g.items" :key="it.path" :index="it.path">
                <el-icon><component :is="it.icon" /></el-icon>
                <template #title>{{ t(it.label) }}</template>
              </el-menu-item>
            </el-menu-item-group>
          </el-menu>
        </nav>
      </el-scrollbar>
      <div class="rail-foot">
        <span class="ws" :class="wsTone" :title="wsLabel"><i class="ws-dot" aria-hidden="true" /><span v-if="!collapsed">{{ wsLabel }}</span></span>
        <span v-if="!collapsed" class="num muted">v{{ version }}</span>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="tb-left">
          <el-button v-if="!isMobile" text :icon="collapsed ? Expand : Fold" :aria-label="t('common.expand')" @click="prefs.toggleSidebar()" />
          <router-link v-if="isMobile" to="/dashboard" class="tb-brand" :aria-label="t('app.name')"><BrandMark :size="26" /></router-link>
          <h1 v-if="isMobile" class="tb-title">{{ pageTitle }}</h1>
          <el-breadcrumb v-else separator="/">
            <el-breadcrumb-item :to="{ path: '/dashboard' }">{{ t('nav.dashboard') }}</el-breadcrumb-item>
            <el-breadcrumb-item v-for="(c, i) in crumbs" :key="i" :to="c.to ? { path: c.to } : undefined">{{ t(c.label) }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="tb-right">
          <span v-if="!isMobile" class="num muted clock">{{ nowText }}</span>
          <NotificationCenter />
          <el-dropdown v-if="!isMobile" trigger="click" @command="(c: ThemeMode) => prefs.setTheme(c)">
            <el-button text :icon="themeIcon" :aria-label="t('header.theme')" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="dark" :icon="Moon">{{ t('theme.dark') }}</el-dropdown-item>
                <el-dropdown-item command="light" :icon="Sunny">{{ t('theme.light') }}</el-dropdown-item>
                <el-dropdown-item command="auto" :icon="Monitor">{{ t('theme.auto') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-dropdown v-if="!isMobile" trigger="click" @command="(l: Locale) => prefs.setLocale(l)">
            <el-button text :aria-label="t('header.language')">{{ prefs.locale === 'zh-CN' ? '中' : 'EN' }}</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="zh-CN">中文</el-dropdown-item>
                <el-dropdown-item command="en-US">English</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-dropdown trigger="click" @command="onUserCmd">
            <button type="button" class="user-btn" :aria-label="auth.user?.username">
              <span class="avatar" aria-hidden="true">{{ (auth.user?.username || '?').slice(0, 1).toUpperCase() }}</span>
              <span v-if="!isMobile" class="uname">{{ auth.user?.nickname || auth.user?.username }}</span>
              <el-tag v-if="auth.isAdmin && !isMobile" size="small" effect="plain" round>{{ auth.role }}</el-tag>
              <el-icon v-if="!isMobile"><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="settings">{{ t('header.settings') }}</el-dropdown-item>
                <el-dropdown-item command="logout" divided>{{ t('header.logout') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <div v-if="rt.state === 'polling'" class="ws-bar" role="status">{{ t('header.wsPolling') }}</div>

      <main class="content">
        <router-view v-slot="{ Component }">
          <component :is="Component" :key="route.path" />
        </router-view>
      </main>
    </div>

    <!-- 手机：底部导航 -->
    <nav v-if="isMobile" class="tabbar" :aria-label="t('shell.menu')">
      <router-link v-for="x in TABS" :key="x.path" :to="x.path" class="tab" :class="{ on: tabActive === x.path }"
                   :aria-current="tabActive === x.path ? 'page' : undefined">
        <el-icon :size="22"><component :is="x.icon" /></el-icon>
        <span>{{ t(x.label) }}</span>
      </router-link>
      <button type="button" class="tab" :class="{ on: sheet || !tabActive }" :aria-expanded="sheet" @click="sheet = true">
        <el-icon :size="22"><Grid /></el-icon>
        <span>{{ t('tab.more') }}</span>
      </button>
    </nav>

    <!-- 手机："更多" 面板：全部页面 + 主题 / 语言 -->
    <el-drawer v-if="isMobile" v-model="sheet" direction="btt" size="78%" :with-header="false" class="more-sheet">
      <div class="sheet-grip" aria-hidden="true" />
      <router-link to="/instances/new" class="sheet-cta"><el-icon><Plus /></el-icon>{{ t('shell.newInstance') }}</router-link>
      <section v-for="g in groups" :key="g.label" class="sheet-group">
        <h2>{{ t(g.label) }}</h2>
        <div class="sheet-grid">
          <router-link v-for="it in g.items" :key="it.path" :to="it.path" class="sheet-item" :class="{ on: active === it.path }">
            <el-icon :size="20"><component :is="it.icon" /></el-icon>
            <span>{{ t(it.label) }}</span>
          </router-link>
        </div>
      </section>
      <section class="sheet-group">
        <h2>{{ t('header.theme') }} · {{ t('header.language') }}</h2>
        <div class="sheet-row">
          <el-segmented :model-value="prefs.theme" :options="[{ label: t('theme.dark'), value: 'dark' }, { label: t('theme.light'), value: 'light' }, { label: t('theme.auto'), value: 'auto' }]"
                        @change="(v: ThemeMode) => prefs.setTheme(v)" />
          <el-segmented :model-value="prefs.locale" :options="[{ label: '中文', value: 'zh-CN' }, { label: 'EN', value: 'en-US' }]"
                        @change="(v: Locale) => prefs.setLocale(v)" />
        </div>
        <div class="sheet-meta">
          <span class="ws" :class="wsTone"><i class="ws-dot" aria-hidden="true" />{{ wsLabel }}</span>
          <span class="num muted">v{{ version }} · {{ nowText }}</span>
        </div>
      </section>
    </el-drawer>
  </div>
</template>

<style scoped>
.shell { display: flex; min-height: 100vh; min-height: 100dvh; background: var(--bg); }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }

/* ---- 侧边栏 */
.rail { position: sticky; top: 0; height: 100vh; width: 232px; flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--surface); border-right: 1px solid var(--line); transition: width 0.18s cubic-bezier(0.2, 0.8, 0.2, 1); }
.rail.collapsed { width: 64px; }
.rail-brand { height: 60px; display: flex; align-items: center; padding: 0 18px; text-decoration: none; flex-shrink: 0; }
.rail-scroll { flex: 1; }
.rail :deep(.el-menu) { border-right: 0; background: transparent; --el-menu-item-height: 40px; --el-menu-sub-item-height: 40px; }
.rail :deep(.el-menu-item) { margin: 1px 8px; border-radius: var(--radius-sm); padding-left: 12px !important; color: var(--ink-2); }
.rail.collapsed :deep(.el-menu-item) { margin: 1px 6px; justify-content: center; padding: 0 !important; }
.rail :deep(.el-menu-item:hover) { background: var(--surface-2); color: var(--ink); }
.rail :deep(.el-menu-item.is-active) { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
.rail :deep(.el-menu-item-group__title) { padding: 14px 0 4px 20px !important; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-3); }
.rail-foot { border-top: 1px solid var(--line); padding: 10px 16px; display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 12px; color: var(--ink-3); }

/* ---- 顶栏 */
.topbar { position: sticky; top: 0; z-index: 20; height: 56px; display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 0 16px; background: color-mix(in srgb, var(--bg) 85%, transparent); backdrop-filter: blur(12px) saturate(1.2);
  border-bottom: 1px solid var(--line); }
.tb-left, .tb-right { display: flex; align-items: center; gap: 4px; min-width: 0; }
.tb-brand { display: inline-flex; margin-right: 6px; }
.tb-title { margin: 0; font-size: 17px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.clock { font-size: 12px; margin-right: 8px; }
.tb-right :deep(.el-badge) { display: inline-flex; }
.user-btn { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; padding: 4px 6px; border-radius: 999px; border: 0; background: transparent; color: var(--ink); font: inherit; }
.user-btn:hover { background: var(--surface-2); }
.avatar { width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center; background: var(--surface-2); border: 1px solid var(--line); font-weight: 600; font-size: 13px; }
.uname { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ws-bar { background: color-mix(in srgb, var(--warn) 14%, var(--bg)); color: var(--warn); font-size: 13px; padding: 6px 16px; border-bottom: 1px solid var(--line); }

/* ---- 内容 */
.content { flex: 1; padding: 20px 24px 32px; }
.content > :deep(*) { max-width: 1440px; margin-left: auto; margin-right: auto; }
.ws { display: inline-flex; align-items: center; gap: 6px; }
.ws-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ink-3); display: inline-block; }
.ws.ok .ws-dot { background: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.ws.warn .ws-dot { background: var(--warn); }

/* ---- 手机 */
.shell.mobile .topbar { padding: 0 8px 0 12px; padding-top: env(safe-area-inset-top); height: calc(52px + env(safe-area-inset-top)); }
.shell.mobile .content { padding: 12px 12px calc(84px + env(safe-area-inset-bottom)); }
.tabbar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 30; display: grid; grid-template-columns: repeat(5, 1fr);
  padding: 6px 4px calc(6px + env(safe-area-inset-bottom)); background: color-mix(in srgb, var(--surface) 92%, transparent);
  backdrop-filter: blur(14px) saturate(1.3); border-top: 1px solid var(--line); }
.tab { display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 6px 0; min-height: 52px; border: 0; background: none;
  color: var(--ink-3); font: inherit; font-size: 11px; font-weight: 500; text-decoration: none; border-radius: 10px; cursor: pointer; }
.tab.on { color: var(--accent); }
.tab:active { background: var(--surface-2); }
.more-sheet :deep(.el-drawer) { border-radius: 18px 18px 0 0; background: var(--surface); }
.more-sheet :deep(.el-drawer__body) { padding: 8px 16px calc(24px + env(safe-area-inset-bottom)); }
.sheet-grip { width: 40px; height: 4px; border-radius: 4px; background: var(--line); margin: 4px auto 14px; }
.sheet-cta { display: flex; align-items: center; justify-content: center; gap: 8px; height: 46px; border-radius: 12px; background: var(--accent);
  color: var(--accent-ink); font-weight: 600; text-decoration: none; margin-bottom: 8px; }
.sheet-group h2 { font-size: 12px; font-weight: 500; letter-spacing: 0.06em; color: var(--ink-3); margin: 18px 0 8px; }
.sheet-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
.sheet-item { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 12px 4px; border-radius: 12px; background: var(--surface-2);
  color: var(--ink-2); font-size: 12px; text-align: center; text-decoration: none; line-height: 1.25; }
.sheet-item.on { color: var(--accent); background: var(--accent-soft); }
.sheet-row { display: flex; gap: 8px; flex-wrap: wrap; }
.sheet-meta { display: flex; justify-content: space-between; margin-top: 14px; font-size: 12px; color: var(--ink-3); }
</style>
