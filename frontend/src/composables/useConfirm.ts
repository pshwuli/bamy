import { ElMessageBox } from 'element-plus'
import { t } from '@/i18n'

export async function confirm(message: string, title?: string, danger = false): Promise<boolean> {
  try {
    await ElMessageBox.confirm(message, title || t('common.confirm'), {
      type: danger ? 'error' : 'warning',
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      confirmButtonClass: danger ? 'el-button--danger' : undefined,
    })
    return true
  } catch {
    return false
  }
}

export async function confirmTyped(message: string, word: string, title?: string): Promise<boolean> {
  try {
    await ElMessageBox.prompt(`${message}\n${t('common.dangerConfirm', { word })}`, title || t('common.confirm'), {
      type: 'error',
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      confirmButtonClass: 'el-button--danger',
      inputValidator: (v: string) => v === word || t('common.dangerConfirm', { word }),
    })
    return true
  } catch {
    return false
  }
}

export async function promptText(message: string, title?: string, required = true): Promise<string | null> {
  try {
    const r = await ElMessageBox.prompt(message, title || t('common.confirm'), {
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      inputValidator: (v: string) => !required || !!v?.trim() || t('auth.required'),
    })
    return (r as { value: string }).value
  } catch {
    return null
  }
}
