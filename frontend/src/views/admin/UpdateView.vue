<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { http } from '@/api/http'
import { confirm } from '@/composables/useConfirm'
import { handleError } from '@/composables/useErrors'
import { fmtTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'

interface UpdateStatus {
  state: string
  message: string
  branch?: string
  current?: string
  current_subject?: string
  current_date?: string
  remote?: string
  behind?: number
  pending?: string[]
  checked_at?: string
  updated_at?: string
  installed: boolean
  stuck: boolean
  requested: boolean
  version: string
  log: string[]
}

const { t } = useI18n()
const st = ref<UpdateStatus | null>(null)
const busy = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const running = computed(() => !!st.value && (st.value.requested || ['running', 'checking'].includes(st.value.state)) && !st.value.stuck)
const tone = computed(() => {
  const s = st.value?.state
  if (s === 'failed') return 'danger'
  if (s === 'available') return 'warning'
  if (s === 'success' || s === 'latest') return 'success'
  return 'info'
})

async function load() {
  try {
    st.value = await http.get<UpdateStatus>('/api/super/update', undefined, { silent: true })
  } catch (e) {
    // 更新重启期间接口会短暂不可用：轮询时不弹错
    if (!timer) handleError(e)
  }
}

async function send(action: 'check' | 'apply') {
  if (action === 'apply' && !(await confirm(t('upd.applyConfirm'), t('upd.apply')))) return
  busy.value = true
  try {
    st.value = { ...(await http.post<UpdateStatus>('/api/super/update', { action }, { silent: true })), log: st.value?.log ?? [] }
    ElMessage.success(action === 'apply' ? t('upd.started') : t('upd.checking'))
    poll()
  } catch (e) {
    handleError(e)
  } finally {
    busy.value = false
  }
}

function poll() {
  if (timer) return
  timer = setInterval(async () => {
    await load()
    if (st.value && !running.value) {
      clearInterval(timer!)
      timer = null
    }
  }, 3000)
}

onMounted(async () => {
  await load()
  if (running.value) poll()
})
onBeforeUnmount(() => timer && clearInterval(timer))
</script>

<template>
  <div class="upd">
    <PageHeader :title="t('upd.title')">
      <el-button :loading="busy && !running" :disabled="running" @click="send('check')">{{ t('upd.check') }}</el-button>
      <el-button type="primary" :loading="running" :disabled="running || !st?.installed" @click="send('apply')">
        {{ running ? t('upd.running') : t('upd.apply') }}
      </el-button>
    </PageHeader>

    <el-alert v-if="st && !st.installed" type="warning" :closable="false" show-icon class="mb" :title="t('upd.notInstalled')">
      <code>sudo bash scripts/install-updater.sh</code>
    </el-alert>
    <el-alert v-else-if="st?.stuck" type="error" :closable="false" show-icon class="mb" :title="t('upd.stuck')" />

    <el-card v-if="st" shadow="never" class="mb">
      <div class="row">
        <span class="k">{{ t('upd.state') }}</span>
        <el-tag :type="tone" disable-transitions>{{ st.message }}</el-tag>
      </div>
      <div class="row">
        <span class="k">{{ t('upd.current') }}</span>
        <span class="num">{{ st.version }} · {{ st.current || '-' }}</span>
        <span class="sub">{{ st.current_subject }}</span>
      </div>
      <div v-if="st.branch" class="row"><span class="k">{{ t('upd.branch') }}</span><span class="num">{{ st.branch }}</span></div>
      <div class="row"><span class="k">{{ t('upd.checkedAt') }}</span><span>{{ fmtTime(st.checked_at) }}</span></div>
      <template v-if="st.pending?.length">
        <div class="k mt">{{ t('upd.pending', { n: st.behind }) }}</div>
        <ul class="commits">
          <li v-for="c in st.pending" :key="c" class="num">{{ c }}</li>
        </ul>
      </template>
      <p class="note">{{ t('upd.note') }}</p>
    </el-card>

    <el-card v-if="st?.log?.length" shadow="never">
      <template #header>{{ t('upd.log') }}</template>
      <pre class="log">{{ st.log.join('\n') }}</pre>
    </el-card>
  </div>
</template>

<style scoped>
.upd { max-width: 860px; }
.mb { margin-bottom: 16px; }
.mt { margin-top: 12px; }
.row { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; padding: 6px 0; }
.k { color: var(--el-text-color-secondary); font-size: 13px; min-width: 72px; }
.sub { color: var(--el-text-color-secondary); font-size: 13px; word-break: break-all; }
.commits { margin: 6px 0 0; padding-left: 18px; font-size: 13px; line-height: 1.7; word-break: break-all; }
.note { margin: 12px 0 0; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
.log { margin: 0; max-height: 360px; overflow: auto; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-all; }
</style>
