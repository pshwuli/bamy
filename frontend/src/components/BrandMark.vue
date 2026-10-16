<script setup lang="ts">
// BAMY 标志：一根上行的 K 线折成字母 B 的骨架。只用 currentColor + --accent 两色。
withDefaults(defineProps<{ size?: number; wordmark?: boolean }>(), { size: 28, wordmark: false })
</script>

<template>
  <span class="brand-mark" :style="{ '--s': `${size}px` }">
    <svg :width="size" :height="size" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect x="0.5" y="0.5" width="31" height="31" rx="8" class="bm-bg" />
      <path d="M9 23V9h7.2a4 4 0 0 1 0 8H9m0 0h8.4a3 3 0 0 1 0 6H9" class="bm-b" />
      <path d="M22.5 13.5 25 10.5" class="bm-tick" />
    </svg>
    <span v-if="wordmark" class="bm-word">BAMY</span>
  </span>
</template>

<style scoped>
.brand-mark { display: inline-flex; align-items: center; gap: 10px; color: var(--ink); }
.bm-bg { fill: var(--surface-2); stroke: var(--line); }
.bm-b { fill: none; stroke: var(--ink); stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; }
.bm-tick { fill: none; stroke: var(--accent); stroke-width: 2.6; stroke-linecap: round; }
.bm-word { font-weight: 600; font-size: calc(var(--s) * 0.62); letter-spacing: -0.03em; }
</style>
