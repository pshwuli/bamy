<script setup lang="ts">
// 策略的"适合谁"与回测摘要。数据来自 strategy.yaml 的 suitability / backtest 段，原样展示，不做加工。
// compact：卡片里只显示资金门槛、风险档位和一行回测；完整：再加逐币回测表与注意事项。
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Strategy } from '@/api/types'

const props = withDefaults(defineProps<{ strategy: Strategy; compact?: boolean; capital?: number | null }>(), { compact: false, capital: null })
const { t } = useI18n()

const suit = computed(() => props.strategy.suitability ?? null)
const bt = computed(() => props.strategy.backtest ?? null)
const minUsdt = computed(() => suit.value?.capital?.min_usdt ?? null)
const sweet = computed(() => suit.value?.capital?.sweet_spot_usdt ?? null)
const risk = computed(() => suit.value?.risk ?? null)
const fmtU = (v: number) => (v >= 1_000_000 ? `${v / 1_000_000} M` : v >= 1000 ? `${v / 1000} k` : `${v}`)

// 给定本金时的提示：低于门槛、在甜区、高于甜区
const capitalHint = computed(() => {
  const c = props.capital
  if (c == null || c <= 0 || minUsdt.value == null) return null
  if (c < minUsdt.value) return { tone: 'down', text: t('fit.belowMin', { min: minUsdt.value }) }
  if (sweet.value && c > sweet.value[1]) return { tone: 'warn', text: t('fit.aboveSweet', { max: fmtU(sweet.value[1]) }) }
  return { tone: 'up', text: t('fit.ok') }
})
const RISK_TONE: Record<string, string> = { 低: 'up', 中: 'warn', 高: 'down', 极高: 'down' }
</script>

<template>
  <div class="fit" :class="{ compact }">
    <div v-if="suit" class="fit-tags">
      <span v-if="risk" class="pill" :class="`t-${RISK_TONE[risk.level] ?? 'warn'}`">{{ t('fit.risk') }} · {{ risk.level }}</span>
      <span v-if="minUsdt != null" class="pill">{{ t('fit.min') }} <b class="num">{{ minUsdt }}</b> U</span>
      <span v-if="sweet" class="pill">{{ t('fit.sweet') }} <b class="num">{{ fmtU(sweet[0]) }}–{{ fmtU(sweet[1]) }}</b> U</span>
    </div>
    <p v-if="capitalHint" class="fit-hint" :class="`t-${capitalHint.tone}`" role="status">{{ capitalHint.text }}</p>
    <p v-if="bt?.summary" class="fit-sum">{{ bt.summary }}</p>
    <template v-if="!compact">
      <dl v-if="suit" class="fit-dl">
        <div v-if="risk?.note"><dt>{{ t('fit.riskNote') }}</dt><dd>{{ risk.note }}</dd></div>
        <div v-if="suit.market"><dt>{{ t('fit.market') }}</dt><dd>{{ suit.market }}</dd></div>
        <div v-if="suit.symbols"><dt>{{ t('fit.symbols') }}</dt><dd>{{ suit.symbols }}</dd></div>
        <div v-if="suit.capital?.note"><dt>{{ t('fit.capital') }}</dt><dd>{{ suit.capital.note }}</dd></div>
      </dl>
      <div v-if="bt?.results?.length" class="fit-table-wrap">
        <table class="fit-table">
          <caption>{{ t('fit.btTitle') }}<span v-if="bt.period"> · {{ bt.period }}</span></caption>
          <thead>
            <tr>
              <th scope="col">{{ t('fit.symbol') }}</th>
              <th v-if="bt.results.some((r) => r.win_rate)" scope="col">{{ t('fit.win') }}</th>
              <th v-if="bt.results.some((r) => r.profit_factor)" scope="col">{{ t('fit.pf') }}</th>
              <th v-if="bt.results.some((r) => r.cagr)" scope="col">{{ t('fit.cagr') }}</th>
              <th v-if="bt.results.some((r) => r.max_drawdown)" scope="col">{{ t('fit.mdd') }}</th>
              <th v-if="bt.results.some((r) => r.buy_hold_drawdown)" scope="col">{{ t('fit.bhMdd') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in bt.results" :key="r.symbol">
              <th scope="row">{{ r.symbol }}</th>
              <td v-if="bt.results.some((x) => x.win_rate)" class="num">{{ r.win_rate ?? '—' }}</td>
              <td v-if="bt.results.some((x) => x.profit_factor)" class="num">{{ r.profit_factor ?? '—' }}</td>
              <td v-if="bt.results.some((x) => x.cagr)" class="num">{{ r.cagr ?? '—' }}</td>
              <td v-if="bt.results.some((x) => x.max_drawdown)" class="num t-down">{{ r.max_drawdown ?? '—' }}</td>
              <td v-if="bt.results.some((x) => x.buy_hold_drawdown)" class="num muted">{{ r.buy_hold_drawdown ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="bt?.cost" class="fit-foot">{{ t('fit.cost') }}：{{ bt.cost }}</p>
      <p v-if="bt?.caveat" class="fit-caveat">{{ bt.caveat }}</p>
    </template>
  </div>
</template>

<style scoped>
.fit { display: grid; gap: 10px; }
.fit-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.pill { display: inline-flex; align-items: center; gap: 4px; height: 26px; padding: 0 10px; border-radius: 13px; background: var(--surface-2);
  border: 1px solid var(--line); font-size: 12px; color: var(--ink-2); white-space: nowrap; }
.pill b { color: var(--ink); font-weight: 500; }
.pill.t-up { color: var(--up); border-color: color-mix(in srgb, var(--up) 35%, var(--line)); }
.pill.t-warn { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 35%, var(--line)); }
.pill.t-down { color: var(--down); border-color: color-mix(in srgb, var(--down) 35%, var(--line)); }
.fit-hint { margin: 0; font-size: 13px; }
.t-up { color: var(--up); } .t-warn { color: var(--warn); } .t-down { color: var(--down); }
.fit-sum { margin: 0; font-size: 13px; line-height: 1.6; color: var(--ink-2); }
.compact .fit-sum { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.fit-dl { display: grid; gap: 10px; margin: 4px 0 0; }
.fit-dl dt { font-size: 12px; color: var(--ink-3); }
.fit-dl dd { margin: 2px 0 0; font-size: 14px; line-height: 1.6; }
.fit-table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; }
.fit-table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 420px; }
.fit-table caption { text-align: left; padding: 10px 12px; font-size: 12px; color: var(--ink-3); caption-side: top; }
.fit-table th, .fit-table td { padding: 8px 12px; text-align: right; border-top: 1px solid var(--line); white-space: nowrap; }
.fit-table th:first-child { text-align: left; font-weight: 500; }
.fit-table thead th { font-size: 12px; font-weight: 500; color: var(--ink-3); background: var(--surface-2); }
.fit-foot, .fit-caveat { margin: 0; font-size: 12px; color: var(--ink-3); line-height: 1.6; }
.fit-caveat { color: var(--warn); }
</style>
