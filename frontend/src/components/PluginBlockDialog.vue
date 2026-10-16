<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { pluginBlock } from '@/composables/useErrors'
import { useAuth } from '@/stores/auth'
import { adminExtApi } from '@/api/extensions'
import { attempt } from '@/composables/useErrors'
import BindingHealthDialog from './BindingHealthDialog.vue'

const { t } = useI18n()
const router = useRouter()
const auth = useAuth()

const info = computed(() => pluginBlock.info)
const name = computed(() => info.value?.plugin_name || info.value?.plugin_id || '?')
const title = computed(() => {
  if (pluginBlock.code === 42291) return t('error.pluginTimeout', { plugin: name.value })
  if (pluginBlock.code === 42292) return t('error.pluginError', { plugin: name.value })
  return t('error.pluginDenied', { plugin: name.value })
})
const isGlobal = computed(() => info.value?.effective_scope === 'global')
const healthOpen = ref(false)

function close() {
  pluginBlock.visible = false
}
function goBindings() {
  close()
  router.push(auth.isAdmin ? { path: '/admin/plugins', query: { tab: 'bindings', plugin: info.value?.plugin_id } } : '/my-plugins')
}
async function disableBinding() {
  if (!info.value?.binding_id) return
  const ok = await attempt(() => adminExtApi.updateBinding(info.value!.binding_id!, { enabled: false }))
  if (ok !== undefined) close()
}
</script>

<template>
  <el-dialog v-model="pluginBlock.visible" :title="title" width="520px" append-to-body>
    <el-alert :type="pluginBlock.code === 42290 ? 'warning' : 'error'" :closable="false" show-icon :title="title">
      <template v-if="pluginBlock.code !== 42290">{{ t('error.pluginNotSystem') }}</template>
    </el-alert>
    <el-descriptions :column="1" border size="small" class="pb-desc">
      <el-descriptions-item v-if="info?.reason" :label="t('order.rejectReason')">{{ info.reason }}</el-descriptions-item>
      <el-descriptions-item v-else-if="pluginBlock.code === 42292 && pluginBlock.message" :label="t('order.rejectReason')">
        {{ pluginBlock.message }}
      </el-descriptions-item>
      <el-descriptions-item v-if="info?.checkpoint" :label="t('ext.checkpoint')">{{ info.checkpoint }}</el-descriptions-item>
      <el-descriptions-item v-if="info?.effective_scope" :label="t('audit.effectiveScope')">{{ t(`scope.${info.effective_scope}`) }}</el-descriptions-item>
      <el-descriptions-item v-if="info?.binding_id" label="binding_id">{{ info.binding_id }}</el-descriptions-item>
      <el-descriptions-item v-if="pluginBlock.requestId" label="request_id">{{ pluginBlock.requestId }}</el-descriptions-item>
    </el-descriptions>
    <p class="pb-hint">{{ isGlobal && !auth.isAdmin ? t('error.pluginGlobalHint') : t('error.pluginAdjustHint') }}</p>
    <template #footer>
      <el-button
        v-if="pluginBlock.code !== 42290 && info?.binding_id && (auth.isAdmin || !isGlobal)"
        @click="healthOpen = true"
      >{{ t('error.pluginViewHealth') }}</el-button>
      <el-button v-if="pluginBlock.code === 42292 && auth.isAdmin && info?.binding_id" type="danger" plain @click="disableBinding">
        {{ t('error.pluginDisableBinding') }}
      </el-button>
      <el-button v-if="!isGlobal || auth.isAdmin" @click="goBindings">{{ t('error.pluginGoBindings') }}</el-button>
      <el-button type="primary" @click="close">{{ t('common.close') }}</el-button>
    </template>
    <BindingHealthDialog v-if="info?.binding_id" v-model="healthOpen" :binding-id="info.binding_id" />
  </el-dialog>
</template>

<style scoped>
.pb-desc { margin-top: 12px; }
.pb-hint { color: var(--el-text-color-secondary); font-size: 13px; margin: 12px 0 0; }
</style>
