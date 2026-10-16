<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { adminApi } from '@/api/admin'
import PageHeader from '@/components/PageHeader.vue'
import AuditTable from '@/components/AuditTable.vue'

const { t } = useI18n()
</script>

<template>
  <div>
    <PageHeader :title="t('audit.adminTitle')" />
    <section class="card">
      <AuditTable :fetcher="adminApi.auditLogs" :detail-fetcher="adminApi.auditLog" show-actor />
    </section>
  </div>
</template>
