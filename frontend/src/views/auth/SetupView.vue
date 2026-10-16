<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, type FormInstance } from 'element-plus'
import { authApi } from '@/api/auth'
import { useAuth } from '@/stores/auth'
import { describeError } from '@/composables/useErrors'
import { usernameRules, passwordRules, confirmRules } from '@/utils/rules'

const { t } = useI18n()
const router = useRouter()
const auth = useAuth()
const form = reactive({ username: '', password: '', confirm: '' })
const formRef = ref<FormInstance>()
const loading = ref(false)
const error = ref('')
const rules = { username: usernameRules(), password: passwordRules(), confirm: confirmRules(() => form.password) }

async function submit() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  loading.value = true
  error.value = ''
  try {
    await authApi.setup({ username: form.username.trim(), password: form.password })
    auth.initialized = true
    ElMessage.success(t('auth.setupDone'))
    router.replace('/login')
  } catch (e) {
    error.value = describeError(e)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <h2 class="auth-title">{{ t('auth.setupTitle') }}</h2>
  <el-form ref="formRef" :model="form" :rules="rules" label-position="top" status-icon @submit.prevent="submit">
    <el-form-item :label="t('auth.username')" prop="username">
      <el-input v-model="form.username" autocomplete="username" />
    </el-form-item>
    <el-form-item :label="t('auth.password')" prop="password">
      <el-input v-model="form.password" type="password" show-password autocomplete="new-password" />
    </el-form-item>
    <el-form-item :label="t('auth.confirmPassword')" prop="confirm">
      <el-input v-model="form.confirm" type="password" show-password autocomplete="new-password" />
    </el-form-item>
    <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
    <el-button type="primary" native-type="submit" :loading="loading" class="full">{{ t('auth.setupCreate') }}</el-button>
  </el-form>
</template>

<style scoped>
.auth-title { margin: 0 0 16px; font-size: 18px; }
.full { width: 100%; }
.auth-error { color: var(--el-color-danger); font-size: 13px; margin: 0 0 12px; }
</style>
