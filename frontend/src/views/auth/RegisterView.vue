<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, type FormInstance } from 'element-plus'
import { authApi } from '@/api/auth'
import { fieldErrors } from '@/api/http'
import { describeError } from '@/composables/useErrors'
import { usernameRules, passwordRules, confirmRules, emailRules } from '@/utils/rules'

const { t } = useI18n()
const router = useRouter()
const form = reactive({ username: '', email: '', password: '', confirm: '', invite_code: '' })
const formRef = ref<FormInstance>()
const loading = ref(false)
const error = ref('')
const fe = ref<Record<string, string>>({})

const rules = {
  username: usernameRules(),
  email: emailRules(false),
  password: passwordRules(),
  confirm: confirmRules(() => form.password),
}

async function submit() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  loading.value = true
  error.value = ''
  fe.value = {}
  try {
    await authApi.register({
      username: form.username.trim(),
      password: form.password,
      email: form.email.trim() || undefined,
      invite_code: form.invite_code.trim() || undefined,
    })
    ElMessage.success(t('auth.registerDone'))
    router.replace('/login')
  } catch (e) {
    fe.value = fieldErrors(e)
    error.value = describeError(e)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <h2 class="auth-title">{{ t('auth.register') }}</h2>
  <el-form ref="formRef" :model="form" :rules="rules" label-position="top" status-icon @submit.prevent="submit">
    <el-form-item :label="t('auth.username')" prop="username" :error="fe.username">
      <el-input v-model="form.username" autocomplete="username" />
    </el-form-item>
    <el-form-item :label="t('auth.email')" prop="email" :error="fe.email">
      <el-input v-model="form.email" type="email" autocomplete="email" />
    </el-form-item>
    <el-form-item :label="t('auth.password')" prop="password" :error="fe.password">
      <el-input v-model="form.password" type="password" show-password autocomplete="new-password" />
    </el-form-item>
    <el-form-item :label="t('auth.confirmPassword')" prop="confirm">
      <el-input v-model="form.confirm" type="password" show-password autocomplete="new-password" />
    </el-form-item>
    <el-form-item :label="t('auth.inviteCode')" prop="invite_code" :error="fe.invite_code">
      <el-input v-model="form.invite_code" autocomplete="off" />
    </el-form-item>
    <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
    <el-button type="primary" native-type="submit" :loading="loading" class="full">{{ t('auth.register') }}</el-button>
  </el-form>
  <div class="auth-links"><router-link to="/login">{{ t('auth.toLogin') }}</router-link></div>
</template>

<style scoped>
.auth-title { margin: 0 0 16px; font-size: 18px; }
.full { width: 100%; }
.auth-error { color: var(--el-color-danger); font-size: 13px; margin: 0 0 12px; }
.auth-links { margin-top: 16px; font-size: 13px; text-align: center; }
</style>
