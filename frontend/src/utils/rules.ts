import type { FormItemRule } from 'element-plus'
import { t } from '@/i18n'

export const USERNAME_RE = /^[A-Za-z0-9_]{3,32}$/
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function req(): FormItemRule {
  return { required: true, message: t('auth.required'), trigger: 'blur' }
}

export function usernameRules(): FormItemRule[] {
  return [req(), { pattern: USERNAME_RE, message: t('auth.usernameRule'), trigger: ['blur', 'change'] }]
}

export function passwordRules(): FormItemRule[] {
  return [
    req(),
    {
      validator: (_r, v: string, cb) => (v && v.length >= 8 && /[A-Za-z]/.test(v) && /\d/.test(v) ? cb() : cb(new Error(t('auth.pwdRule')))),
      trigger: ['blur', 'change'],
    },
  ]
}

export function confirmRules(get: () => string): FormItemRule[] {
  return [req(), { validator: (_r, v: string, cb) => (v === get() ? cb() : cb(new Error(t('auth.pwdMismatch')))), trigger: ['blur', 'change'] }]
}

export function emailRules(required = false): FormItemRule[] {
  const r: FormItemRule[] = [{ pattern: EMAIL_RE, message: t('auth.emailRule'), trigger: ['blur', 'change'] }]
  return required ? [req(), ...r] : r
}
