<script setup lang="ts">
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
</script>

<template>
  <div class="nf">
    <el-result icon="warning" title="404" :sub-title="t('error.pageNotFoundDesc')">
      <template #extra>
        <router-link to="/"><el-button type="primary">{{ t('error.backHome') }}</el-button></router-link>
      </template>
    </el-result>
  </div>
</template>

<style scoped>
.nf { min-height: 100vh; display: grid; place-items: center; background: var(--bamy-page); }
</style>
