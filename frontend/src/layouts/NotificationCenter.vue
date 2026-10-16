<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Bell } from '@element-plus/icons-vue'
import { useNotifications, type Notice, type NoticeCategory } from '@/stores/notifications'
import { fmtTime } from '@/utils/format'

const store = useNotifications()
const router = useRouter()
const { t } = useI18n()
const tab = ref<'all' | NoticeCategory>('all')
const list = computed(() => (tab.value === 'all' ? store.items : store.items.filter((n) => n.category === tab.value)).slice(0, 50))

function open(n: Notice) {
  store.markRead(n.id)
  if (n.link) router.push(n.link)
}
</script>

<template>
  <el-popover placement="bottom-end" :width="360" trigger="click">
    <template #reference>
      <el-badge :value="store.unread" :hidden="!store.unread" :max="99">
        <el-button text :icon="Bell" :aria-label="t('header.notifications')" />
      </el-badge>
    </template>
    <div class="nc-head">
      <strong>{{ t('header.notifications') }}</strong>
      <el-button link type="primary" :disabled="!store.unread" @click="store.markAllRead()">{{ t('header.markAllRead') }}</el-button>
    </div>
    <el-segmented
      v-model="tab"
      size="small"
      :options="[
        { value: 'all', label: t('common.all') },
        { value: 'alert', label: t('header.catAlert') },
        { value: 'order', label: t('header.catOrder') },
        { value: 'system', label: t('header.catSystem') },
      ]"
      block
    />
    <el-scrollbar max-height="360px">
      <div v-if="!list.length" class="nc-empty">{{ t('header.noNotifications') }}</div>
      <button v-for="n in list" :key="n.id" class="nc-item" :class="{ unread: !n.read }" type="button" @click="open(n)">
        <div class="nc-title">{{ n.title }}</div>
        <div v-if="n.body" class="nc-body">{{ n.body }}</div>
        <div class="nc-time">{{ fmtTime(n.ts) }}</div>
      </button>
    </el-scrollbar>
  </el-popover>
</template>

<style scoped>
.nc-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.nc-empty { text-align: center; color: var(--el-text-color-secondary); padding: 24px 0; }
.nc-item { display: block; width: 100%; text-align: left; border: 0; background: none; padding: 8px 6px; border-bottom: 1px solid var(--el-border-color-lighter); cursor: pointer; color: inherit; font: inherit; }
.nc-item:hover, .nc-item:focus-visible { background: var(--el-fill-color-light); outline: none; }
.nc-item.unread .nc-title::before { content: ''; display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--el-color-primary); margin-right: 6px; vertical-align: middle; }
.nc-title { font-size: 13px; font-weight: 500; }
.nc-body { font-size: 12px; color: var(--el-text-color-regular); margin-top: 2px; }
.nc-time { font-size: 11px; color: var(--el-text-color-secondary); margin-top: 2px; }
:deep(.el-segmented) { margin-bottom: 6px; }
</style>
