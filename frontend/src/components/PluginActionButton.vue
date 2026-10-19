<script setup lang="ts">
// 插件手动动作按钮（如扫码绑定）：start 拿二维码 → 按插件给的间隔轮询 → 完成后通知父组件刷新。
// 凭据由服务端直接写入绑定设置，浏览器只看到状态，看不到 AppSecret。
import { onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import QRCode from 'qrcode'
import { adminExtApi, bindingsApi, type ActionResult } from '@/api/extensions'
import { handleError } from '@/composables/useErrors'

const props = defineProps<{
  bindingId: number
  action: { id: string; label: string }
  admin?: boolean
}>()
const emit = defineEmits<{ done: [] }>()
const { t } = useI18n()

type Phase = 'starting' | 'waiting' | 'done' | 'expired' | 'failed'
const open = ref(false)
const phase = ref<Phase>('starting')
const qrImg = ref('')
const qrUrl = ref('')
const failure = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let session = 0 // 每次开始/关闭自增，丢弃过期会话的异步回包

function call(payload: Record<string, unknown>): Promise<ActionResult> {
  const run = props.admin ? adminExtApi.runBindingAction : bindingsApi.runAction
  return run(props.bindingId, props.action.id, payload) as Promise<ActionResult>
}

function stop() {
  session += 1
  if (timer) clearTimeout(timer)
  timer = undefined
}

function fail(e: unknown) {
  phase.value = 'failed'
  failure.value = e instanceof Error ? e.message : t('pluginAction.failed')
}

async function start() {
  stop()
  const mine = session
  open.value = true
  phase.value = 'starting'
  qrImg.value = qrUrl.value = failure.value = ''
  try {
    const r = await call({ step: 'start' })
    if (mine !== session) return
    if (!r.qr_url) throw new Error(t('pluginAction.noQr'))
    qrUrl.value = r.qr_url
    qrImg.value = await QRCode.toDataURL(r.qr_url, { margin: 1, width: 220 })
    if (mine !== session) return
    phase.value = 'waiting'
    schedule(mine, r.interval)
  } catch (e) {
    if (mine === session) fail(e)
  }
}

function schedule(mine: number, seconds?: number) {
  const ms = Math.min(Math.max(Number(seconds) || 2, 1), 10) * 1000
  timer = setTimeout(() => void poll(mine), ms)
}

async function poll(mine: number) {
  try {
    const r = await call({ step: 'poll' })
    if (mine !== session) return
    if (r.status === 'done') {
      phase.value = 'done'
      stop()
      emit('done')
    } else if (r.status === 'expired') {
      phase.value = 'expired'
      stop()
    } else {
      schedule(mine, r.interval)
    }
  } catch (e) {
    if (mine !== session) return
    stop()
    fail(e)
    handleError(e)
  }
}

function close() {
  stop()
  open.value = false
}

onBeforeUnmount(stop)
</script>

<template>
  <el-button type="primary" plain @click="start">{{ action.label }}</el-button>
  <el-dialog v-model="open" :title="action.label" width="340px" append-to-body :close-on-click-modal="false" @close="stop">
    <div class="qr-body" role="status" aria-live="polite">
      <el-skeleton v-if="phase === 'starting'" :rows="4" animated />
      <template v-else-if="phase === 'waiting'">
        <img class="qr-img" :src="qrImg" :alt="t('pluginAction.qrAlt')" width="220" height="220" />
        <p class="muted small">{{ t('pluginAction.scanHint') }}</p>
        <el-link :href="qrUrl" target="_blank" rel="noopener noreferrer" type="primary">{{ t('pluginAction.openLink') }}</el-link>
      </template>
      <el-result v-else-if="phase === 'done'" icon="success" :title="t('pluginAction.done')" :sub-title="t('pluginAction.doneHint')" />
      <el-result v-else-if="phase === 'expired'" icon="warning" :title="t('pluginAction.expired')" />
      <el-result v-else icon="error" :title="t('pluginAction.failed')" :sub-title="failure" />
    </div>
    <template #footer>
      <el-button v-if="phase === 'expired' || phase === 'failed'" type="primary" @click="start">{{ t('pluginAction.retry') }}</el-button>
      <el-button @click="close">{{ phase === 'done' ? t('common.close') : t('common.cancel') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.qr-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  min-height: 260px;
  justify-content: center;
}
.qr-img {
  border-radius: 8px;
  background: #fff;
}
</style>
