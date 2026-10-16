<script setup lang="ts">
import { computed, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { CopyDocument, Plus } from '@element-plus/icons-vue'
import { authApi } from '@/api/auth'
import { userApi, type ApiToken, type NotificationSettings, type Preferences, type SessionInfo } from '@/api/user'
import { itemsOf } from '@/api/extensions'
import type { Row, User } from '@/api/types'
import { useAuth } from '@/stores/auth'
import { usePrefs, type ThemeMode } from '@/stores/prefs'
import type { Locale } from '@/i18n'
import { handleError } from '@/composables/useErrors'
import { confirm } from '@/composables/useConfirm'
import { confirmRules, emailRules, passwordRules, req } from '@/utils/rules'
import { copyText, fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import ErrorState from '@/components/ErrorState.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuth()
const prefs = usePrefs()

const TABS = ['profile', 'security', 'notify', 'prefs'] as const
type Tab = (typeof TABS)[number]
const tab = ref<Tab>(TABS.includes(route.query.tab as Tab) ? (route.query.tab as Tab) : 'profile')
watch(tab, (v) => router.replace({ query: { ...route.query, tab: v } }))

async function copy(text: string) {
  if (await copyText(text)) ElMessage.success(t('common.copied'))
}

// ---------------- 个人资料 ----------------
const profileForm = ref<FormInstance>()
const profile = reactive({ username: '', nickname: '', email: '' })
const profileLoading = ref(true)
const profileError = ref<unknown>(null)
const savingProfile = ref(false)
const twoFactorOn = ref(false)
const profileRules: FormRules = { email: emailRules(), nickname: [{ max: 64, message: t('schema.maxLength', { n: 64 }), trigger: 'blur' }] }

function fillProfile(u: User) {
  profile.username = u.username
  profile.nickname = u.nickname ?? ''
  profile.email = u.email ?? ''
  twoFactorOn.value = !!u.two_factor_enabled
}

async function loadProfile() {
  profileLoading.value = true
  profileError.value = null
  try {
    const u = await userApi.profile()
    fillProfile(u)
    auth.setUser({ ...auth.user, ...u })
  } catch (e) {
    profileError.value = e
  } finally {
    profileLoading.value = false
  }
}

async function saveProfile() {
  if (!(await profileForm.value?.validate().catch(() => false))) return
  savingProfile.value = true
  try {
    const u = await userApi.updateProfile({ nickname: profile.nickname.trim(), email: profile.email.trim() })
    ElMessage.success(t('misc.saved'))
    fillProfile(u)
    auth.setUser({ ...auth.user, ...u })
  } catch {
    /* 全局钩子已提示 */
  } finally {
    savingProfile.value = false
  }
}

// ---------------- 修改密码 ----------------
const pwdForm = ref<FormInstance>()
const pwd = reactive({ old_password: '', new_password: '', confirm: '' })
const pwdRules: FormRules = { old_password: [req()], new_password: passwordRules(), confirm: confirmRules(() => pwd.new_password) }
const savingPwd = ref(false)
async function changePwd() {
  if (!(await pwdForm.value?.validate().catch(() => false))) return
  savingPwd.value = true
  if (await run(() => authApi.changePassword({ old_password: pwd.old_password, new_password: pwd.new_password }), t('misc.pwdChanged'))) {
    pwdForm.value?.resetFields()
  }
  savingPwd.value = false
}

// 非 silent 请求的错误由全局钩子提示；这里只返回是否成功
async function run(fn: () => Promise<unknown>, okMsg?: string): Promise<boolean> {
  try {
    await fn()
    if (okMsg) ElMessage.success(okMsg)
    return true
  } catch {
    return false
  }
}
// ---------------- 2FA ----------------
// 后端 /2fa/enable 返回 otpauth_uri（api/auth.ts 类型写的是 otpauth_url），两者都兼容
const tfaSetup = ref<{ secret: string; uri: string } | null>(null)
const tfaCode = ref('')
const tfaBusy = ref(false)
const recoveryCodes = ref<string[]>([])
const disableOpen = ref(false)
const regenOpen = ref(false)
const actionCode = ref('')

async function startTfa() {
  tfaBusy.value = true
  try {
    const r = (await authApi.tfaEnable()) as { secret?: string; otpauth_uri?: string; otpauth_url?: string }
    tfaSetup.value = { secret: r.secret ?? '', uri: r.otpauth_uri ?? r.otpauth_url ?? '' }
    tfaCode.value = ''
  } catch {
    /* 全局钩子已提示 */
  } finally {
    tfaBusy.value = false
  }
}

async function verifyTfa() {
  if (!/^\d{6}$/.test(tfaCode.value.trim())) return void ElMessage.warning(t('auth.twoFactorHint'))
  tfaBusy.value = true
  try {
    const r = await authApi.tfaVerify(tfaCode.value.trim())
    recoveryCodes.value = r?.recovery_codes ?? []
    tfaSetup.value = null
    twoFactorOn.value = true
    if (auth.user) auth.setUser({ ...auth.user, two_factor_enabled: true })
    ElMessage.success(t('common.success'))
  } catch {
    /* 全局钩子已提示 */
  } finally {
    tfaBusy.value = false
  }
}

function openCodeDialog(kind: 'disable' | 'regen') {
  actionCode.value = ''
  if (kind === 'disable') disableOpen.value = true
  else regenOpen.value = true
}

async function disableTfa() {
  if (!actionCode.value.trim()) return
  tfaBusy.value = true
  if (await run(() => authApi.tfaDisable(actionCode.value.trim()), t('common.success'))) {
    twoFactorOn.value = false
    recoveryCodes.value = []
    disableOpen.value = false
    if (auth.user) auth.setUser({ ...auth.user, two_factor_enabled: false })
  }
  tfaBusy.value = false
}

async function regenCodes() {
  if (!actionCode.value.trim()) return
  tfaBusy.value = true
  try {
    const r = await authApi.tfaRecovery(actionCode.value.trim())
    recoveryCodes.value = r?.recovery_codes ?? []
    regenOpen.value = false
  } catch {
    /* 全局钩子已提示 */
  } finally {
    tfaBusy.value = false
  }
}

// ---------------- 会话 ----------------
type Session = SessionInfo & { expires_at?: string | null }
const sessions = shallowRef<Session[]>([])
const sessionsLoading = ref(false)
const sessionsError = ref<unknown>(null)
async function loadSessions() {
  sessionsLoading.value = true
  sessionsError.value = null
  try {
    sessions.value = itemsOf(await userApi.sessions())
  } catch (e) {
    sessionsError.value = e
  } finally {
    sessionsLoading.value = false
  }
}
async function revokeSession(s: Row) {
  if (!(await confirm(t('misc.sessionRevokeConfirm'), t('settings.revoke'), true))) return
  if (await run(() => userApi.revokeSession(s.id), t('common.success'))) void loadSessions()
}
async function logoutAll() {
  if (!(await confirm(t('settings.logoutAll'), t('settings.logoutAll'), true))) return
  await auth.logout(true)
  void router.replace('/login')
}

// ---------------- API Token ----------------
type Token = ApiToken & { revoked_at?: string | null; use_count?: number }
const tokens = shallowRef<Token[]>([])
const tokensLoading = ref(false)
const tokensError = ref<unknown>(null)
const tokenOpen = ref(false)
const tokenForm = ref<FormInstance>()
const tokenInput = reactive({ name: '', scope: 'read', expires_at: null as string | null })
const tokenRules: FormRules = { name: [req(), { max: 64, message: t('schema.maxLength', { n: 64 }), trigger: 'blur' }], scope: [req()] }
const creatingToken = ref(false)
const newToken = ref<string | null>(null)
const scopes = computed(() => [
  { value: 'read', label: t('settings.scopeRead') },
  { value: 'write', label: t('settings.scopeTrade') },
  ...(auth.isAdmin ? [{ value: 'admin', label: t('misc.scopeAdmin') }] : []),
])
const scopeLabel = (s: string) => scopes.value.find((x) => x.value === s)?.label ?? (s === 'admin' ? t('misc.scopeAdmin') : s)

async function loadTokens() {
  tokensLoading.value = true
  tokensError.value = null
  try {
    tokens.value = itemsOf(await userApi.apiTokens()) as Token[]
  } catch (e) {
    tokensError.value = e
  } finally {
    tokensLoading.value = false
  }
}
function openToken() {
  tokenInput.name = ''
  tokenInput.scope = 'read'
  tokenInput.expires_at = null
  newToken.value = null
  tokenOpen.value = true
}
async function createToken() {
  if (!(await tokenForm.value?.validate().catch(() => false))) return
  creatingToken.value = true
  try {
    const r = await userApi.createApiToken({ name: tokenInput.name.trim(), scope: tokenInput.scope, expires_at: tokenInput.expires_at })
    newToken.value = r?.token ?? null
    ElMessage.success(t('misc.tokenCreated'))
    void loadTokens()
  } catch {
    /* 全局钩子已提示 */
  } finally {
    creatingToken.value = false
  }
}
async function revokeToken(tk: Row) {
  if (!(await confirm(t('settings.tokenRevokeConfirm', { name: tk.name }), t('settings.revoke'), true))) return
  if (await run(() => userApi.revokeApiToken(tk.id), t('common.success'))) void loadTokens()
}
const disablePast = (d: Date) => d.getTime() < Date.now() - 86400_000

// ---------------- 通知 ----------------
// 与后端 engine/runtime.py NOTIFY_EVENTS 一致；后端会拒绝列表之外的事件
const EVENT_TYPES = [
  'instance.error', 'instance.auto_stopped', 'order.filled', 'order.failed', 'order.rejected',
  'guard.triggered', 'guard.missing', 'risk.breached', 'risk.circuit_break', 'risk.circuit_recover',
  'account.invalid', 'account.recovered', 'reconcile.diff', 'system.alert',
]
const notifyForm = ref<FormInstance>()
const notify = reactive<NotificationSettings>({ email_enabled: false, webhook_url: '', event_types: [] })
const notifyLoading = ref(false)
const notifyError = ref<unknown>(null)
const savingNotify = ref(false)
const notifyRules: FormRules = {
  webhook_url: [{ validator: (_r, v: string, cb) => (!v || /^https?:\/\/\S+$/.test(v) ? cb() : cb(new Error(t('misc.webhookRule')))), trigger: 'blur' }],
}
const eventOptions = computed(() => [...new Set([...EVENT_TYPES, ...notify.event_types])])

async function loadNotify() {
  notifyLoading.value = true
  notifyError.value = null
  try {
    const n = await userApi.notificationSettings()
    notify.email_enabled = !!n?.email_enabled
    notify.webhook_url = n?.webhook_url ?? ''
    notify.event_types = n?.event_types ?? []
  } catch (e) {
    notifyError.value = e
  } finally {
    notifyLoading.value = false
  }
}
async function saveNotify() {
  if (!(await notifyForm.value?.validate().catch(() => false))) return
  savingNotify.value = true
  await run(() => userApi.updateNotificationSettings({ email_enabled: notify.email_enabled, webhook_url: notify.webhook_url?.trim() ?? '', event_types: notify.event_types }), t('misc.saved'))
  savingNotify.value = false
}

// ---------------- 偏好 ----------------
const pref = reactive<Preferences>({
  theme: prefs.theme,
  language: prefs.locale,
  time_format: prefs.timeFormat,
  timezone: prefs.timezone,
  page_size: prefs.pageSize,
})
const savingPref = ref(false)
const timezones = computed(() => {
  const base = ['Asia/Shanghai', 'UTC', 'local']
  let all: string[] = []
  try {
    all = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.('timeZone') ?? []
  } catch {
    all = []
  }
  return [...new Set([...base, pref.timezone, ...all])]
})
const tzLabel = (z: string) => (z === 'Asia/Shanghai' ? `${t('settings.tzBeijing')} (Asia/Shanghai)` : z === 'UTC' ? t('settings.tzUtc') : z === 'local' ? t('settings.tzLocal') : z)

async function loadPrefs() {
  try {
    const p = await userApi.preferences()
    if (p) {
      prefs.applyRemote(p)
      Object.assign(pref, { theme: prefs.theme, language: prefs.locale, time_format: prefs.timeFormat, timezone: prefs.timezone, page_size: prefs.pageSize })
    }
  } catch {
    /* 用本地值 */
  }
}
async function savePrefs() {
  savingPref.value = true
  prefs.setTheme(pref.theme as ThemeMode, false)
  prefs.setLocale(pref.language as Locale, false)
  prefs.timeFormat = pref.time_format
  prefs.timezone = pref.timezone
  prefs.pageSize = pref.page_size
  prefs.persist()
  try {
    await userApi.updatePreferences({ ...pref })
    ElMessage.success(t('misc.saved'))
  } catch (e) {
    handleError(e)
  } finally {
    savingPref.value = false
  }
}

const loadedTabs = new Set<Tab>()
function loadTab(v: Tab) {
  if (loadedTabs.has(v)) return
  loadedTabs.add(v)
  if (v === 'security') {
    void loadSessions()
    void loadTokens()
  } else if (v === 'notify') void loadNotify()
  else if (v === 'prefs') void loadPrefs()
}
watch(tab, loadTab)
onMounted(() => {
  void loadProfile()
  loadTab(tab.value)
})
</script>

<template>
  <div>
    <PageHeader :title="t('settings.title')" />
    <section class="card">
      <el-tabs v-model="tab">
        <!-- 个人资料 -->
        <el-tab-pane :label="t('settings.tabProfile')" name="profile">
          <ErrorState v-if="profileError" :error="profileError" @retry="loadProfile" />
          <el-skeleton v-else-if="profileLoading" :rows="3" animated />
          <el-form v-else ref="profileForm" :model="profile" :rules="profileRules" label-position="top" class="narrow" @submit.prevent="saveProfile">
            <el-form-item :label="t('auth.username')" prop="username">
              <el-input v-model="profile.username" disabled />
            </el-form-item>
            <el-form-item :label="t('settings.nickname')" prop="nickname">
              <el-input v-model="profile.nickname" maxlength="64" autocomplete="nickname" />
            </el-form-item>
            <el-form-item :label="t('auth.email')" prop="email">
              <el-input v-model="profile.email" type="email" autocomplete="email" />
            </el-form-item>
            <el-button type="primary" native-type="submit" :loading="savingProfile">{{ t('settings.saveProfile') }}</el-button>
          </el-form>
        </el-tab-pane>

        <!-- 安全 -->
        <el-tab-pane :label="t('settings.tabSecurity')" name="security">
          <h2 class="sec-title">{{ t('settings.changePassword') }}</h2>
          <el-form ref="pwdForm" :model="pwd" :rules="pwdRules" label-position="top" class="narrow" @submit.prevent="changePwd">
            <el-form-item :label="t('settings.oldPassword')" prop="old_password">
              <el-input v-model="pwd.old_password" type="password" show-password autocomplete="current-password" />
            </el-form-item>
            <el-form-item :label="t('auth.newPassword')" prop="new_password">
              <el-input v-model="pwd.new_password" type="password" show-password autocomplete="new-password" />
            </el-form-item>
            <el-form-item :label="t('auth.confirmPassword')" prop="confirm">
              <el-input v-model="pwd.confirm" type="password" show-password autocomplete="new-password" />
            </el-form-item>
            <el-button type="primary" native-type="submit" :loading="savingPwd">{{ t('settings.changePassword') }}</el-button>
          </el-form>

          <el-divider />
          <h2 class="sec-title">
            {{ t('settings.tfa') }}
            <el-tag :type="twoFactorOn ? 'success' : 'info'" size="small" disable-transitions>{{ twoFactorOn ? t('settings.tfaOn') : t('settings.tfaOff') }}</el-tag>
          </h2>
          <div v-if="!twoFactorOn && !tfaSetup">
            <el-button type="primary" :loading="tfaBusy" @click="startTfa">{{ t('settings.tfaEnable') }}</el-button>
          </div>
          <div v-else-if="tfaSetup" class="narrow">
            <p class="hint">{{ t('settings.tfaScan') }}</p>
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item :label="t('settings.tfaSecret')">
                <code class="mono">{{ tfaSetup.secret }}</code>
                <el-button link :icon="CopyDocument" :aria-label="t('common.copy')" @click="copy(tfaSetup.secret)" />
              </el-descriptions-item>
              <el-descriptions-item v-if="tfaSetup.uri" :label="t('misc.tfaOtpauth')">
                <a :href="tfaSetup.uri" class="mono break">{{ tfaSetup.uri }}</a>
                <el-button link :icon="CopyDocument" :aria-label="t('common.copy')" @click="copy(tfaSetup.uri)" />
              </el-descriptions-item>
            </el-descriptions>
            <el-form label-position="top" class="mt" @submit.prevent="verifyTfa">
              <el-form-item :label="t('settings.tfaCode')">
                <el-input v-model="tfaCode" inputmode="numeric" maxlength="6" autocomplete="one-time-code" />
              </el-form-item>
              <el-button type="primary" native-type="submit" :loading="tfaBusy">{{ t('misc.tfaVerify') }}</el-button>
              <el-button @click="tfaSetup = null">{{ t('common.cancel') }}</el-button>
            </el-form>
          </div>
          <div v-else class="btn-row">
            <el-button @click="openCodeDialog('regen')">{{ t('settings.tfaRecovery') }}</el-button>
            <el-button type="danger" plain @click="openCodeDialog('disable')">{{ t('settings.tfaDisable') }}</el-button>
          </div>
          <el-alert v-if="recoveryCodes.length" type="warning" :closable="false" show-icon :title="t('settings.tfaRecoveryHint')" class="mt narrow">
            <div class="codes">
              <code v-for="c in recoveryCodes" :key="c" class="mono">{{ c }}</code>
            </div>
            <el-button size="small" :icon="CopyDocument" @click="copy(recoveryCodes.join('\n'))">{{ t('common.copy') }}</el-button>
          </el-alert>

          <el-divider />
          <h2 class="sec-title">
            {{ t('settings.sessions') }}
            <el-button size="small" type="danger" plain @click="logoutAll">{{ t('settings.logoutAll') }}</el-button>
          </h2>
          <ErrorState v-if="sessionsError" :error="sessionsError" @retry="loadSessions" />
          <el-table v-else v-loading="sessionsLoading" :data="sessions" size="small" stripe class="resp-table">
            <el-table-column :label="t('settings.device')" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                {{ row.device || row.user_agent || '-' }}
                <el-tag v-if="row.current" size="small" type="success" class="ml">{{ t('settings.current') }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="ip" :label="t('settings.ip')" min-width="120" />
            <el-table-column :label="t('common.createdAt')" min-width="160"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
            <el-table-column :label="t('misc.expires')" min-width="160"><template #default="{ row }">{{ fmtTime(row.last_active_at || row.expires_at) }}</template></el-table-column>
            <el-table-column :label="t('common.actions')" width="90" fixed="right">
              <template #default="{ row }"><el-button link type="danger" @click="revokeSession(row)">{{ t('settings.revoke') }}</el-button></template>
            </el-table-column>
            <template #empty><el-empty :description="t('common.empty')" :image-size="48" /></template>
          </el-table>

          <el-divider />
          <h2 class="sec-title">
            {{ t('settings.apiTokens') }}
            <el-button size="small" type="primary" :icon="Plus" @click="openToken">{{ t('settings.tokenCreate') }}</el-button>
          </h2>
          <ErrorState v-if="tokensError" :error="tokensError" @retry="loadTokens" />
          <el-table v-else v-loading="tokensLoading" :data="tokens" size="small" stripe class="resp-table">
            <el-table-column prop="name" :label="t('settings.tokenName')" min-width="140" show-overflow-tooltip />
            <el-table-column :label="t('misc.tokenPrefix')" min-width="130"><template #default="{ row }"><code class="mono">{{ row.token_prefix || '-' }}…</code></template></el-table-column>
            <el-table-column :label="t('settings.tokenScope')" width="90"><template #default="{ row }">{{ scopeLabel(row.scope) }}</template></el-table-column>
            <el-table-column :label="t('misc.lastUsed')" min-width="160"><template #default="{ row }">{{ fmtTime(row.last_used_at) }}</template></el-table-column>
            <el-table-column :label="t('settings.tokenExpires')" min-width="160">
              <template #default="{ row }">{{ row.expires_at ? fmtTime(row.expires_at) : t('settings.never') }}</template>
            </el-table-column>
            <el-table-column :label="t('common.status')" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.revoked_at" size="small" type="info" disable-transitions>{{ t('misc.revokedAt') }}</el-tag>
                <el-tag v-else size="small" type="success" disable-transitions>{{ t('instance.shareActive') }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('common.actions')" width="90" fixed="right">
              <template #default="{ row }"><el-button v-if="!row.revoked_at" link type="danger" @click="revokeToken(row)">{{ t('settings.revoke') }}</el-button></template>
            </el-table-column>
            <template #empty><el-empty :description="t('common.empty')" :image-size="48" /></template>
          </el-table>
        </el-tab-pane>

        <!-- 通知 -->
        <el-tab-pane :label="t('settings.tabNotify')" name="notify">
          <ErrorState v-if="notifyError" :error="notifyError" @retry="loadNotify" />
          <el-form v-else ref="notifyForm" v-loading="notifyLoading" :model="notify" :rules="notifyRules" label-position="top" class="narrow" @submit.prevent="saveNotify">
            <!-- 事件邮件尚未实现（SMTP 只用于找回密码），不展示开关，避免"打开了却收不到" -->
            <p class="muted small">{{ t('notifyx.how') }}</p>
            <el-form-item :label="t('settings.webhook')" prop="webhook_url">
              <el-input v-model="notify.webhook_url" placeholder="https://" type="url" clearable />
              <div class="muted small">{{ t('notifyx.webhookHint') }}</div>
            </el-form-item>
            <el-form-item :label="t('settings.eventTypes')" prop="event_types">
              <el-checkbox-group v-model="notify.event_types" class="events">
                <el-checkbox v-for="e in eventOptions" :key="e" :value="e">{{ e }}</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-button type="primary" native-type="submit" :loading="savingNotify">{{ t('common.save') }}</el-button>
          </el-form>
        </el-tab-pane>

        <!-- 偏好 -->
        <el-tab-pane :label="t('settings.tabPrefs')" name="prefs">
          <el-form :model="pref" label-position="top" class="narrow" @submit.prevent="savePrefs">
            <el-form-item :label="t('settings.theme')">
              <el-radio-group v-model="pref.theme">
                <el-radio-button value="light">{{ t('theme.light') }}</el-radio-button>
                <el-radio-button value="dark">{{ t('theme.dark') }}</el-radio-button>
                <el-radio-button value="auto">{{ t('theme.auto') }}</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item :label="t('settings.language')">
              <el-radio-group v-model="pref.language">
                <el-radio-button value="zh-CN">{{ t('misc.langZh') }}</el-radio-button>
                <el-radio-button value="en-US">{{ t('misc.langEn') }}</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item :label="t('settings.timeFormat')">
              <el-radio-group v-model="pref.time_format">
                <el-radio-button value="24h">{{ t('misc.time24') }}</el-radio-button>
                <el-radio-button value="12h">{{ t('misc.time12') }}</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item :label="t('settings.timezone')">
              <el-select v-model="pref.timezone" filterable :aria-label="t('settings.timezone')">
                <el-option v-for="z in timezones" :key="z" :value="z" :label="tzLabel(z)" />
              </el-select>
            </el-form-item>
            <el-form-item :label="t('settings.pageSize')">
              <el-select v-model="pref.page_size" :aria-label="t('settings.pageSize')">
                <el-option v-for="n in [10, 20, 50, 100]" :key="n" :value="n" :label="String(n)" />
              </el-select>
            </el-form-item>
            <el-button type="primary" native-type="submit" :loading="savingPref">{{ t('common.save') }}</el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </section>

    <!-- 关闭 2FA / 重新生成恢复码 -->
    <el-dialog v-model="disableOpen" :title="t('settings.tfaDisable')" width="420px" append-to-body>
      <p class="hint">{{ t('misc.tfaDisableHint') }}</p>
      <el-form label-position="top" @submit.prevent="disableTfa">
        <el-form-item :label="t('settings.tfaCode')">
          <el-input v-model="actionCode" inputmode="numeric" maxlength="16" autocomplete="one-time-code" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="disableOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="tfaBusy" :disabled="!actionCode.trim()" @click="disableTfa">{{ t('settings.tfaDisable') }}</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="regenOpen" :title="t('settings.tfaRecovery')" width="420px" append-to-body>
      <p class="hint">{{ t('misc.tfaRecoveryCodeHint') }}</p>
      <el-form label-position="top" @submit.prevent="regenCodes">
        <el-form-item :label="t('settings.tfaCode')">
          <el-input v-model="actionCode" inputmode="numeric" maxlength="16" autocomplete="one-time-code" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="regenOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="tfaBusy" :disabled="!actionCode.trim()" @click="regenCodes">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>

    <!-- 创建 Token -->
    <el-dialog v-model="tokenOpen" :title="t('settings.tokenCreate')" width="480px" append-to-body :close-on-click-modal="!newToken">
      <template v-if="newToken">
        <el-alert type="warning" :closable="false" show-icon :title="t('settings.tokenOnce')" />
        <div class="token-box">
          <code class="mono break">{{ newToken }}</code>
          <el-button :icon="CopyDocument" @click="copy(newToken)">{{ t('common.copy') }}</el-button>
        </div>
      </template>
      <el-form v-else ref="tokenForm" :model="tokenInput" :rules="tokenRules" label-position="top" @submit.prevent="createToken">
        <el-form-item :label="t('settings.tokenName')" prop="name">
          <el-input v-model="tokenInput.name" maxlength="64" />
        </el-form-item>
        <el-form-item :label="t('settings.tokenScope')" prop="scope">
          <el-radio-group v-model="tokenInput.scope">
            <el-radio-button v-for="s in scopes" :key="s.value" :value="s.value">{{ s.label }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="t('settings.tokenExpires')" prop="expires_at">
          <el-date-picker v-model="tokenInput.expires_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" :disabled-date="disablePast" :placeholder="t('settings.never')" clearable />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="tokenOpen = false">{{ newToken ? t('common.close') : t('common.cancel') }}</el-button>
        <el-button v-if="!newToken" type="primary" :loading="creatingToken" @click="createToken">{{ t('common.create') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.narrow { max-width: 520px; }
.sec-title { font-size: 15px; font-weight: 600; margin: 0 0 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.hint { color: var(--el-text-color-secondary); font-size: 13px; margin: 0 0 8px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.break { word-break: break-all; }
.mt { margin-top: 12px; }
.ml { margin-left: 6px; }
.btn-row { display: flex; gap: 8px; flex-wrap: wrap; }
.codes { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 4px 12px; margin: 8px 0; }
.events { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
.token-box { display: flex; gap: 8px; align-items: center; margin-top: 12px; padding: 8px; background: var(--el-fill-color-lighter); border-radius: 6px; }
.token-box code { flex: 1; }
</style>
