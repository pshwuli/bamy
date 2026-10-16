<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { FormInstance } from 'element-plus'
import { authApi } from '@/api/auth'
import { describeError } from '@/composables/useErrors'
import { emailRules } from '@/utils/rules'

const { t } = useI18n()
const form = reactive({ email: '' })
const formRef = ref<FormInstance>()
const loading = ref(false)
const sent = ref(false)
const error = ref('')

async function submit() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  loading.value = true
  error.value = ''
  try {
    await authApi.forgotPassword(form.email.trim())
    sent.value = true
  } catch (e) {
    error.value = describeError(e)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <h2 class="auth-title">{{ t('auth.forgot') }}</h2>
  <el-result v-if="sent" icon="success" :title="t('auth.resetSent')" />
  <el-form v-else ref="formRef" :model="form" :rules="{ email: emailRules(true) }" label-position="top" @submit.prevent="submit">
    <el-form-item :label="t('auth.email')" prop="email">
      <el-input v-model="form.email" type="email" autocomplete="email" />
    </el-form-item>
    <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
    <el-button type="primary" native-type="submit" :loading="loading" class="full">{{ t('auth.sendReset') }}</el-button>
  </el-form>
  <div class="auth-links"><router-link to="/login">{{ t('auth.backToLogin') }}</router-link></div>
</template>

<style scoped>
.auth-title { margin: 0 0 16px; font-size: 18px; }
.full { width: 100%; }
.auth-error { color: var(--el-color-danger); font-size: 13px; margin: 0 0 12px; }
.auth-links { margin-top: 16px; font-size: 13px; text-align: center; }
</style>
