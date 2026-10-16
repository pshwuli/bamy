import { createApp, watch } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus, { ElMessage } from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import en from 'element-plus/es/locale/lang/en'
import './styles/main.css'

import App from './App.vue'
import { router } from './router'
import { i18n, t } from './i18n'
import { setHttpHooks, ApiError } from './api/http'
import { useAuth } from './stores/auth'
import { usePrefs } from './stores/prefs'
import { useRealtime } from './stores/realtime'
import { useNotifications } from './stores/notifications'
import { handleError } from './composables/useErrors'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(i18n)
app.use(ElementPlus, { locale: i18n.global.locale.value === 'en-US' ? en : zhCn })
app.use(router)

const prefs = usePrefs()
prefs.init()

let redirecting = false
setHttpHooks({
  onError: (err) => handleError(err),
  onAuthLost: () => {
    const auth = useAuth()
    useRealtime().stop()
    auth.clear()
    const cur = router.currentRoute.value
    if (redirecting || cur.meta.public) return
    redirecting = true
    ElMessage.warning(t('error.unauthorized'))
    router.replace({ path: '/login', query: { redirect: cur.fullPath } }).finally(() => (redirecting = false))
  },
})

const auth = useAuth()
watch(
  () => [auth.loggedIn, auth.isAdmin] as const,
  ([loggedIn, admin]) => {
    const rt = useRealtime()
    const notes = useNotifications()
    if (loggedIn) {
      rt.start(admin)
      void prefs.pull()
      void notes.load()
    } else {
      rt.stop()
      notes.clear()   // 换账号登录时不留上一个人的通知
    }
  },
  { immediate: true },
)

app.config.errorHandler = (err) => {
  if (err instanceof ApiError) return
  console.error(err)
}

app.mount('#app')
