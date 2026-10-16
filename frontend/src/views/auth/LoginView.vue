<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { FormInstance, FormRules } from 'element-plus'
import { useAuth } from '@/stores/auth'
import { ApiError } from '@/api/http'
import { describeError } from '@/composables/useErrors'

const { t } = useI18n()
const auth = useAuth()
const router = useRouter()
const route = useRoute()

const form = reactive({ username: '', password: '', code: '' })
const need2fa = ref(false)
const loading = ref(false)
const error = ref('')
const formRef = ref<FormInstance>()

const rules: FormRules = {
  username: [{ required: true, message: t('auth.required'), trigger: 'blur' }],
  password: [{ required: true, message: t('auth.required'), trigger: 'blur' }],
  code: [{ validator: (_r, v, cb) => (need2fa.value && !v ? cb(new Error(t('auth.required'))) : cb()), trigger: 'blur' }],
}

function lockText(err: ApiError): string {
  const d = (err.data ?? {}) as { locked_until?: string; retry_after?: number; remaining_seconds?: number }
  let secs = d.remaining_seconds ?? d.retry_after
  if (secs === undefined && d.locked_until) secs = Math.max(0, (new Date(d.locked_until).getTime() - Date.now()) / 1000)
  if (secs === undefined || !Number.isFinite(secs)) return t('auth.locked')
  const m = Math.ceil(secs / 60)
  return t('auth.lockedFor', { time: m >= 1 ? `${m} min` : `${Math.ceil(secs)} s` })
}

async function submit() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  loading.value = true
  error.value = ''
  try {
    await auth.login(form.username.trim(), form.password, need2fa.value ? form.code.trim() : undefined)
    const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/') ? route.query.redirect : '/dashboard'
    router.replace(redirect)
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.code === 40108) {
        need2fa.value = true
        error.value = t('auth.twoFactorHint')
      } else if (e.code === 40107) {
        need2fa.value = true
        error.value = describeError(e)
      } else if (e.code === 40104) error.value = t('auth.badCredentials')
      else if (e.code === 40105) error.value = lockText(e)
      else if (e.code === 40106) error.value = t('auth.disabled')
      else error.value = describeError(e)
    } else error.value = describeError(e)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <h2 class="auth-title">{{ t('auth.login') }}</h2>
  <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
    <el-form-item :label="t('auth.username')" prop="username">
      <el-input v-model="form.username" autocomplete="username" autofocus />
    </el-form-item>
    <el-form-item :label="t('auth.password')" prop="password">
      <el-input v-model="form.password" type="password" show-password autocomplete="current-password" />
    </el-form-item>
    <el-form-item v-if="need2fa" :label="t('auth.twoFactor')" prop="code">
      <el-input v-model="form.code" inputmode="numeric" autocomplete="one-time-code" maxlength="16" />
    </el-form-item>
    <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
    <el-button type="primary" native-type="submit" :loading="loading" class="full">{{ t('auth.login') }}</el-button>
  </el-form>
  <div class="auth-links">
    <router-link to="/forgot-password">{{ t('auth.forgot') }}</router-link>
    <router-link to="/register">{{ t('auth.toRegister') }}</router-link>
  </div>
</template>

<style scoped>
.auth-title { margin: 0 0 16px; font-size: 18px; }
.full { width: 100%; }
.auth-error { color: var(--el-color-danger); font-size: 13px; margin: 0 0 12px; }
.auth-links { display: flex; justify-content: space-between; margin-top: 16px; font-size: 13px; }
</style>
