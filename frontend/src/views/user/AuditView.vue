<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { userApi } from '@/api/user'
import PageHeader from '@/components/PageHeader.vue'
import AuditTable from '@/components/AuditTable.vue'

const { t } = useI18n()
</script>

<template>
  <div>
    <PageHeader :title="t('audit.title')" :subtitle="t('misc.auditDesc')" />
    <section class="card">
      <AuditTable :fetcher="userApi.auditLogs" />
    </section>
  </div>
</template>
