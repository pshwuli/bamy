import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import type { Role } from '@/api/types'
import { useAuth } from '@/stores/auth'
import { tokenStore } from '@/utils/token'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    public?: boolean
    guestOnly?: boolean
    landing?: boolean
    role?: Role
    breadcrumb?: { label: string; to?: string }[]
    dynamicTitle?: string
  }
}

const v = {
  login: () => import('@/views/auth/LoginView.vue'),
  register: () => import('@/views/auth/RegisterView.vue'),
  forgot: () => import('@/views/auth/ForgotPasswordView.vue'),
  reset: () => import('@/views/auth/ResetPasswordView.vue'),
  setup: () => import('@/views/auth/SetupView.vue'),
}

const userRoutes: RouteRecordRaw[] = [
  { path: 'dashboard', component: () => import('@/views/user/DashboardView.vue'), meta: { title: 'nav.dashboard' } },
  { path: 'accounts', component: () => import('@/views/user/AccountsView.vue'), meta: { title: 'nav.accounts' } },
  {
    path: 'accounts/:id',
    component: () => import('@/views/user/AccountDetailView.vue'),
    meta: { title: 'common.detail', breadcrumb: [{ label: 'nav.accounts', to: '/accounts' }] },
  },
  { path: 'strategies', component: () => import('@/views/user/StrategiesView.vue'), meta: { title: 'nav.strategies' } },
  { path: 'plugins', component: () => import('@/views/user/PluginMarketView.vue'), meta: { title: 'nav.plugins' } },
  { path: 'my-plugins', component: () => import('@/views/user/MyPluginsView.vue'), meta: { title: 'nav.myPlugins' } },
  { path: 'instances', component: () => import('@/views/user/InstancesView.vue'), meta: { title: 'nav.instances' } },
  {
    path: 'instances/new',
    component: () => import('@/views/user/InstanceCreateView.vue'),
    meta: { title: 'instance.create', breadcrumb: [{ label: 'nav.instances', to: '/instances' }] },
  },
  {
    path: 'instances/:id',
    component: () => import('@/views/user/InstanceDetailView.vue'),
    meta: { title: 'common.detail', breadcrumb: [{ label: 'nav.instances', to: '/instances' }] },
  },
  { path: 'orders', component: () => import('@/views/user/OrdersView.vue'), meta: { title: 'nav.orders' } },
  { path: 'paper', component: () => import('@/views/user/PaperView.vue'), meta: { title: 'nav.paper' } },
  { path: 'audit', component: () => import('@/views/user/AuditView.vue'), meta: { title: 'nav.audit' } },
  { path: 'settings', component: () => import('@/views/user/SettingsView.vue'), meta: { title: 'nav.settings' } },
]

const adminRoutes: RouteRecordRaw[] = [
  { path: 'admin', redirect: '/admin/dashboard' },
  { path: 'admin/dashboard', component: () => import('@/views/admin/AdminDashboardView.vue'), meta: { title: 'nav.adminDashboard', role: 'admin' } },
  { path: 'admin/users', component: () => import('@/views/admin/UsersView.vue'), meta: { title: 'nav.adminUsers', role: 'admin' } },
  {
    path: 'admin/users/:id',
    component: () => import('@/views/admin/UserDetailView.vue'),
    meta: { title: 'admin.userDetail', role: 'admin', breadcrumb: [{ label: 'nav.adminUsers', to: '/admin/users' }] },
  },
  { path: 'admin/accounts', component: () => import('@/views/admin/AdminAccountsView.vue'), meta: { title: 'nav.adminAccounts', role: 'admin' } },
  { path: 'admin/instances', component: () => import('@/views/admin/AdminInstancesView.vue'), meta: { title: 'nav.adminInstances', role: 'admin' } },
  { path: 'admin/strategies', component: () => import('@/views/admin/ExtensionsView.vue'), meta: { title: 'nav.adminStrategies', role: 'admin' } },
  { path: 'admin/plugins', component: () => import('@/views/admin/ExtensionsView.vue'), meta: { title: 'nav.adminPlugins', role: 'admin' } },
  { path: 'admin/reconcile', component: () => import('@/views/admin/ReconcileView.vue'), meta: { title: 'nav.adminReconcile', role: 'admin' } },
  { path: 'admin/circuit-breakers', component: () => import('@/views/admin/CircuitBreakersView.vue'), meta: { title: 'nav.adminBreakers', role: 'admin' } },
  { path: 'admin/logs', component: () => import('@/views/admin/SystemLogsView.vue'), meta: { title: 'nav.adminLogs', role: 'admin' } },
  { path: 'admin/audit', component: () => import('@/views/admin/AdminAuditView.vue'), meta: { title: 'nav.adminAudit', role: 'admin' } },
  { path: 'admin/invites', component: () => import('@/views/admin/InvitesView.vue'), meta: { title: 'nav.adminInvites', role: 'admin' } },
  { path: 'super/configs', component: () => import('@/views/admin/ConfigsView.vue'), meta: { title: 'nav.superConfigs', role: 'super_admin' } },
  { path: 'super/backup', component: () => import('@/views/admin/BackupView.vue'), meta: { title: 'nav.superBackup', role: 'super_admin' } },
]

const routes: RouteRecordRaw[] = [
  // 主页：未登录看到产品介绍；已登录也能访问（导航里有"进入后台"）
  { path: '/', component: () => import('@/views/LandingView.vue'), meta: { public: true, landing: true } },
  {
    path: '/',
    component: () => import('@/layouts/AuthLayout.vue'),
    children: [
      { path: 'login', component: v.login, meta: { public: true, guestOnly: true } },
      { path: 'register', component: v.register, meta: { public: true, guestOnly: true } },
      { path: 'forgot-password', component: v.forgot, meta: { public: true, guestOnly: true } },
      { path: 'reset-password', component: v.reset, meta: { public: true } },
      { path: 'setup', component: v.setup, meta: { public: true } },
    ],
  },
  { path: '/view/:token', component: () => import('@/views/guest/ShareView.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('@/layouts/AppLayout.vue'),
    children: [...userRoutes, ...adminRoutes],
  },
  { path: '/404', component: () => import('@/views/error/NotFoundView.vue'), meta: { public: true } },
  { path: '/:pathMatch(.*)*', component: () => import('@/views/error/NotFoundView.vue'), meta: { public: true } },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const auth = useAuth()
  if (to.path.startsWith('/view/')) return true

  const initialized = await auth.checkSetup()
  if (!initialized && to.path !== '/setup') return '/setup'
  // 已登录用户直接打开根路径时进总览；从后台主动点品牌/首页链接（带 ?home=1）仍可看主页
  if (to.meta.landing && tokenStore.access && auth.user && to.query.home !== '1') return '/dashboard'
  if (initialized && to.path === '/setup') return '/login'

  if (to.meta.public) {
    if (to.meta.guestOnly && tokenStore.access && auth.user) return '/dashboard'
    return true
  }

  if (!tokenStore.access || !auth.user) {
    return { path: '/login', query: to.fullPath !== '/dashboard' ? { redirect: to.fullPath } : {} }
  }
  if (!auth.loaded) await auth.fetchMe()
  if (!auth.user) return { path: '/login', query: { redirect: to.fullPath } }

  const need = to.matched.reduce<Role | undefined>((acc, r) => (r.meta.role as Role | undefined) ?? acc, undefined)
  if (need && !auth.hasRole(need)) return { path: '/404', query: { from: to.fullPath } }
  return true
})
