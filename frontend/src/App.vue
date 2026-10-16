<script setup lang="ts">
import { computed } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import en from 'element-plus/es/locale/lang/en'
import { usePrefs } from '@/stores/prefs'
import PluginBlockDialog from '@/components/PluginBlockDialog.vue'

const prefs = usePrefs()
const locale = computed(() => (prefs.locale === 'en-US' ? en : zhCn))
</script>

<template>
  <el-config-provider :locale="locale">
    <router-view />
    <PluginBlockDialog />
  </el-config-provider>
</template>
