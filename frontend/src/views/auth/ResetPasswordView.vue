<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, type FormInstance } from 'element-plus'
import { authApi } from '@/api/auth'
import { describeError } from '@/composables/useErrors'
import { passwordRules, confirmRules } from '@/utils/rules'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const token = computed(() => (typeof route.query.token === 'string' ? route.query.token : ''))
const form = reactive({ password: '', confirm: '' })
const formRef = ref<FormInstance>()
const loading = ref(false)
const error = ref('')
const rules = { password: passwordRules(), confirm: confirmRules(() => form.password) }

async function submit() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  loading.value = true
  error.value = ''
  try {
    await authApi.resetPassword({ token: token.value, new_password: form.password })
    ElMessage.success(t('auth.resetDone'))
    router.replace('/login')
  } catch (e) {
    error.value = describeError(e)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <h2 class="auth-title">{{ t('auth.resetPassword') }}</h2>
  <el-alert v-if="!token" type="error" :title="t('auth.invalidResetToken')" :closable="false" show-icon />
  <el-form v-else ref="formRef" :model="form" :rules="rules" label-position="top" status-icon @submit.prevent="submit">
    <el-form-item :label="t('auth.newPassword')" prop="password">
      <el-input v-model="form.password" type="password" show-password autocomplete="new-password" />
    </el-form-item>
    <el-form-item :label="t('auth.confirmPassword')" prop="confirm">
      <el-input v-model="form.confirm" type="password" show-password autocomplete="new-password" />
    </el-form-item>
    <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
    <el-button type="primary" native-type="submit" :loading="loading" class="full">{{ t('auth.resetPassword') }}</el-button>
  </el-form>
  <div class="auth-links"><router-link to="/login">{{ t('auth.backToLogin') }}</router-link></div>
</template>

<style scoped>
.auth-title { margin: 0 0 16px; font-size: 18px; }
.full { width: 100%; }
.auth-error { color: var(--el-color-danger); font-size: 13px; margin: 0 0 12px; }
.auth-links { margin-top: 16px; font-size: 13px; text-align: center; }
</style>
