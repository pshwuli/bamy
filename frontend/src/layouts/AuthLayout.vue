<script setup lang="ts">
import { Moon, Sunny, Monitor } from '@element-plus/icons-vue'
import { usePrefs } from '@/stores/prefs'
import { computed } from 'vue'

const prefs = usePrefs()
const themeIcon = computed(() => (prefs.theme === 'dark' ? Moon : prefs.theme === 'light' ? Sunny : Monitor))
</script>

<template>
  <div class="auth-wrap">
    <div class="auth-tools">
      <el-button text :icon="themeIcon" :aria-label="$t('header.theme')" @click="prefs.cycleTheme()" />
      <el-button text @click="prefs.setLocale(prefs.locale === 'zh-CN' ? 'en-US' : 'zh-CN')">{{ prefs.locale === 'zh-CN' ? 'EN' : '中' }}</el-button>
    </div>
    <main class="auth-card">
      <div class="auth-logo">
        <span class="logo-mark" aria-hidden="true">B</span>
        <div>
          <div class="logo-name">{{ $t('app.name') }}</div>
          <div class="logo-slogan">{{ $t('app.slogan') }}</div>
        </div>
      </div>
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.auth-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px 16px; background: var(--bamy-page); position: relative; }
.auth-tools { position: absolute; top: 12px; right: 12px; display: flex; gap: 4px; }
.auth-card { width: 100%; max-width: 400px; background: var(--el-bg-color); border: 1px solid var(--el-border-color-lighter); border-radius: 12px; padding: 32px 28px; box-shadow: var(--el-box-shadow-light); }
.auth-logo { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
.logo-mark { width: 40px; height: 40px; border-radius: 10px; background: var(--el-color-primary); color: #fff; display: grid; place-items: center; font-weight: 700; font-size: 20px; }
.logo-name { font-size: 20px; font-weight: 700; }
.logo-slogan { font-size: 12px; color: var(--el-text-color-secondary); }
</style>
