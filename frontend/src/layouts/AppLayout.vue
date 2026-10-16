<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Expand, Fold, Moon, Sunny, Monitor, ArrowDown } from '@element-plus/icons-vue'
import { MENU } from '@/router/menu'
import { useAuth } from '@/stores/auth'
import { usePrefs, type ThemeMode } from '@/stores/prefs'
import { useRealtime } from '@/stores/realtime'
import { useBreakpoint } from '@/composables/useBreakpoint'
import type { Locale } from '@/i18n'
import NotificationCenter from './NotificationCenter.vue'

const auth = useAuth()
const prefs = usePrefs()
const rt = useRealtime()
const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const { isMobile, isTablet } = useBreakpoint()

const drawer = ref(false)
const groups = computed(() => MENU.filter((g) => auth.hasRole(g.role)))
const collapsed = computed(() => (isTablet.value ? true : prefs.sidebarCollapsed))
const active = computed(() => {
  const all = groups.value.flatMap((g) => g.items.map((i) => i.path))
  let best = ''
  for (const p of all) if ((route.path === p || route.path.startsWith(`${p}/`)) && p.length > best.length) best = p
  if (route.path.startsWith('/admin/plugins') || route.path.startsWith('/admin/strategies')) return route.path.split('/').slice(0, 3).join('/')
  return best || route.path
})

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

watch(() => route.fullPath, () => (drawer.value = false))

function onMenu(path: string) {
  router.push(path)
}

async function onUserCmd(cmd: string) {
  if (cmd === 'settings') router.push('/settings')
  if (cmd === 'logout') {
    rt.stop()
    await auth.logout()
    router.replace('/login')
  }
}

function toggleSide() {
  if (isMobile.value) drawer.value = !drawer.value
  else prefs.toggleSidebar()
}
const version = __APP_VERSION__
</script>

<template>
  <el-container class="app-shell">
    <el-aside v-if="!isMobile" :width="collapsed ? '64px' : '220px'" class="aside">
      <router-link to="/" class="brand" :aria-label="t('app.name')">
        <span class="logo-mark" aria-hidden="true">B</span>
        <span v-if="!collapsed" class="brand-name">{{ t('app.name') }}</span>
      </router-link>
      <el-scrollbar>
        <nav :aria-label="t('nav.dashboard')">
          <el-menu :default-active="active" :collapse="collapsed" :collapse-transition="false" @select="onMenu">
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
    </el-aside>

    <el-drawer v-else v-model="drawer" direction="ltr" size="240px" :with-header="false" class="nav-drawer">
      <router-link to="/" class="brand">
        <span class="logo-mark" aria-hidden="true">B</span><span class="brand-name">{{ t('app.name') }}</span>
      </router-link>
      <el-menu :default-active="active" @select="onMenu">
        <el-menu-item-group v-for="g in groups" :key="g.label" :title="t(g.label)">
          <el-menu-item v-for="it in g.items" :key="it.path" :index="it.path">
            <el-icon><component :is="it.icon" /></el-icon><span>{{ t(it.label) }}</span>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>
    </el-drawer>

    <el-container direction="vertical" class="main-col">
      <el-header class="topbar" height="56px">
        <div class="tb-left">
          <el-button text :icon="collapsed && !isMobile ? Expand : Fold" :aria-label="t('common.expand')" @click="toggleSide" />
          <el-breadcrumb v-if="!isMobile" separator="/">
            <el-breadcrumb-item :to="{ path: '/' }">{{ t('nav.dashboard') }}</el-breadcrumb-item>
            <el-breadcrumb-item v-for="(c, i) in crumbs" :key="i" :to="c.to ? { path: c.to } : undefined">{{ t(c.label) }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="tb-right">
          <NotificationCenter />
          <el-dropdown trigger="click" @command="(c: ThemeMode) => prefs.setTheme(c)">
            <el-button text :icon="themeIcon" :aria-label="t('header.theme')" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="light" :icon="Sunny">{{ t('theme.light') }}</el-dropdown-item>
                <el-dropdown-item command="dark" :icon="Moon">{{ t('theme.dark') }}</el-dropdown-item>
                <el-dropdown-item command="auto" :icon="Monitor">{{ t('theme.auto') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-dropdown trigger="click" @command="(l: Locale) => prefs.setLocale(l)">
            <el-button text :aria-label="t('header.language')">{{ prefs.locale === 'zh-CN' ? '中' : 'EN' }}</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="zh-CN">中文</el-dropdown-item>
                <el-dropdown-item command="en-US">English</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-dropdown trigger="click" @command="onUserCmd">
            <span class="user-btn" tabindex="0" role="button">
              <el-avatar :size="28">{{ (auth.user?.username || '?').slice(0, 1).toUpperCase() }}</el-avatar>
              <span v-if="!isMobile" class="uname">{{ auth.user?.nickname || auth.user?.username }}</span>
              <el-tag v-if="auth.isAdmin && !isMobile" size="small" effect="plain">{{ auth.role }}</el-tag>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="settings">{{ t('header.settings') }}</el-dropdown-item>
                <el-dropdown-item command="logout" divided>{{ t('header.logout') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-alert v-if="rt.state === 'polling'" type="warning" :title="t('header.wsPolling')" :closable="false" show-icon class="ws-bar" />

      <el-main class="content">
        <router-view v-slot="{ Component }">
          <component :is="Component" :key="route.path" />
        </router-view>
      </el-main>

      <el-footer height="28px" class="statusbar">
        <span class="ws" :class="wsTone"><i class="ws-dot" aria-hidden="true" />{{ wsLabel }}</span>
        <span>v{{ version }}</span>
        <span>{{ nowText }}</span>
      </el-footer>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-shell { height: 100vh; }
.aside { background: var(--el-bg-color); border-right: 1px solid var(--el-border-color-lighter); display: flex; flex-direction: column; transition: width 0.2s; overflow: hidden; }
.aside :deep(.el-menu) { border-right: 0; }
.aside :deep(.el-menu-item-group__title) { padding-top: 12px; }
.brand { height: 56px; display: flex; align-items: center; gap: 10px; padding: 0 16px; text-decoration: none; color: var(--el-text-color-primary); flex-shrink: 0; }
.logo-mark { width: 32px; height: 32px; border-radius: 8px; background: var(--el-color-primary); color: #fff; display: grid; place-items: center; font-weight: 700; flex-shrink: 0; }
.brand-name { font-weight: 700; font-size: 18px; }
.main-col { min-width: 0; }
.topbar { display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--el-border-color-lighter); background: var(--el-bg-color); padding: 0 12px; gap: 8px; }
.tb-left, .tb-right { display: flex; align-items: center; gap: 4px; min-width: 0; }
.tb-right :deep(.el-badge) { display: inline-flex; }
.user-btn { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; padding: 4px 6px; border-radius: 6px; }
.user-btn:focus-visible { outline: 2px solid var(--el-color-primary); }
.uname { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.content { background: var(--bamy-page); padding: 20px; overflow: auto; }
.content > :deep(*) { max-width: 1600px; margin: 0 auto; }
.ws-bar { border-radius: 0; }
.statusbar { display: flex; align-items: center; gap: 16px; font-size: 12px; color: var(--el-text-color-secondary); border-top: 1px solid var(--el-border-color-lighter); background: var(--el-bg-color); padding: 0 12px; }
.ws { display: inline-flex; align-items: center; gap: 6px; }
.ws-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--el-color-info); display: inline-block; }
.ws.ok .ws-dot { background: var(--el-color-success); }
.ws.warn .ws-dot { background: var(--el-color-warning); }
@media (max-width: 767px) { .content { padding: 12px; } .statusbar span:last-child { margin-left: auto; } }
</style>
