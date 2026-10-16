<script setup lang="ts">
import { Moon, Sunny, Monitor, ArrowLeft } from '@element-plus/icons-vue'
import { usePrefs } from '@/stores/prefs'
import { computed } from 'vue'
import BrandMark from '@/components/BrandMark.vue'

const prefs = usePrefs()
const themeIcon = computed(() => (prefs.theme === 'dark' ? Moon : prefs.theme === 'light' ? Sunny : Monitor))
</script>

<template>
  <div class="auth-wrap">
    <header class="auth-top">
      <router-link to="/" class="auth-back" :aria-label="$t('common.back')"><el-icon><ArrowLeft /></el-icon><BrandMark :size="26" wordmark /></router-link>
      <div class="auth-tools">
        <el-button text :icon="themeIcon" :aria-label="$t('header.theme')" @click="prefs.cycleTheme()" />
        <el-button text @click="prefs.setLocale(prefs.locale === 'zh-CN' ? 'en-US' : 'zh-CN')">{{ prefs.locale === 'zh-CN' ? 'EN' : '中' }}</el-button>
      </div>
    </header>
    <main class="auth-card">
      <router-view />
    </main>
    <p class="auth-note">{{ $t('landing.foot_self') }}</p>
  </div>
</template>

<style scoped>
.auth-wrap { min-height: 100vh; min-height: 100dvh; display: flex; flex-direction: column; align-items: center; background: var(--bg);
  padding: max(12px, env(safe-area-inset-top)) 16px calc(24px + env(safe-area-inset-bottom)); }
.auth-top { width: 100%; max-width: 1200px; display: flex; justify-content: space-between; align-items: center; }
.auth-back { display: inline-flex; align-items: center; gap: 8px; color: var(--ink-2); text-decoration: none; padding: 6px 4px; }
.auth-tools { display: flex; gap: 2px; }
.auth-card { width: 100%; max-width: 420px; margin: auto 0; padding: clamp(28px, 6vw, 40px) clamp(20px, 5vw, 36px);
  background: var(--surface); border: 1px solid var(--line); border-radius: 18px; box-shadow: var(--shadow); }
.auth-card :deep(h2), .auth-card :deep(h3) { font-size: 26px; letter-spacing: -0.03em; margin: 0 0 22px; }
.auth-card :deep(.el-form-item__label) { font-weight: 500; }
.auth-card :deep(.el-button--primary) { height: 46px; font-size: 16px; border-radius: 12px; }
.auth-note { max-width: 420px; text-align: center; font-size: 12px; color: var(--ink-3); margin: 18px 0 0; line-height: 1.6; }
@media (max-width: 560px) {
  .auth-card { margin: 24px 0 auto; border: 0; box-shadow: none; background: transparent; padding: 8px 4px; }
}
</style>
