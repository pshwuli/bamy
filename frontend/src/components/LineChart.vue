<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import type { EChartsType } from 'echarts/core'
import { echarts, inkFor, DASHES } from '@/utils/chartTheme'
import { usePrefs } from '@/stores/prefs'

export interface LineSeries {
  name: string
  values: (number | null)[]
  slot?: number
}

const props = withDefaults(
  defineProps<{
    timeline: (string | number)[]
    series: LineSeries[]
    height?: string
    valueFormat?: (v: number) => string
    area?: boolean
    ariaLabel?: string
    baseline?: number | null
  }>(),
  { height: '320px', valueFormat: (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: 2 }), area: false, ariaLabel: '', baseline: null },
)

const el = ref<HTMLDivElement>()
const chart = shallowRef<EChartsType>()
const prefs = usePrefs()
const isDark = computed(() => prefs.isDark)

function fmtAxisTime(v: string | number) {
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return String(v)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function option() {
  const ink = inkFor(isDark.value)
  const multi = props.series.length > 1
  const directLabel = multi && props.series.length <= 4
  return {
    animation: false,
    backgroundColor: 'transparent',
    aria: { enabled: true, label: { description: props.ariaLabel } },
    grid: { left: 8, right: directLabel ? 96 : 16, top: multi ? 36 : 16, bottom: 48, containLabel: true },
    legend: multi
      ? { show: true, top: 0, left: 0, type: 'scroll', icon: 'roundRect', itemWidth: 12, itemHeight: 4, textStyle: { color: ink.textSecondary } }
      : { show: false },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: ink.axis } },
      backgroundColor: isDark.value ? '#262624' : '#ffffff',
      borderColor: ink.grid,
      textStyle: { color: ink.text, fontSize: 12 },
      valueFormatter: (v: unknown) => (typeof v === 'number' ? props.valueFormat(v) : '-'),
    },
    xAxis: {
      type: 'category',
      data: props.timeline,
      boundaryGap: false,
      axisLine: { lineStyle: { color: ink.axis } },
      axisTick: { show: false },
      axisLabel: { color: ink.muted, formatter: fmtAxisTime, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: ink.grid, width: 1 } },
      axisLabel: { color: ink.muted, formatter: (v: number) => props.valueFormat(v) },
    },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8, borderColor: ink.grid, textStyle: { color: ink.muted } }],
    series: props.series.map((s, i) => {
      const slot = s.slot ?? i
      const color = ink.series[slot % ink.series.length]
      const lastIdx = (() => {
        for (let k = s.values.length - 1; k >= 0; k--) if (s.values[k] !== null && s.values[k] !== undefined) return k
        return -1
      })()
      return {
        name: s.name,
        type: 'line',
        data: s.values,
        showSymbol: false,
        symbolSize: 8,
        connectNulls: true,
        lineStyle: { width: 2, color, type: DASHES[slot] ?? 'solid' },
        itemStyle: { color, borderColor: ink.surface, borderWidth: 2 },
        emphasis: { focus: 'series', lineStyle: { width: 2 } },
        areaStyle: props.area && !multi ? { color, opacity: 0.08 } : undefined,
        markLine:
          i === 0 && props.baseline !== null
            ? { silent: true, symbol: 'none', lineStyle: { color: ink.axis, type: 'dashed', width: 1 }, label: { show: false }, data: [{ yAxis: props.baseline }] }
            : undefined,
        endLabel: directLabel
          ? { show: lastIdx >= 0, formatter: () => s.name, color: ink.textSecondary, fontSize: 11, distance: 6 }
          : undefined,
      }
    }),
  }
}

function render() {
  if (!chart.value) return
  chart.value.setOption(option(), { notMerge: true })
}

let ro: ResizeObserver | null = null
onMounted(() => {
  if (!el.value) return
  chart.value = echarts.init(el.value, undefined, { renderer: 'canvas' })
  render()
  ro = new ResizeObserver(() => chart.value?.resize())
  ro.observe(el.value)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  chart.value?.dispose()
})
watch(() => [props.timeline, props.series, isDark.value, props.baseline], render, { deep: true })
</script>

<template>
  <div ref="el" class="line-chart" :style="{ height }" role="img" :aria-label="ariaLabel" />
</template>

<style scoped>
.line-chart { width: 100%; }
</style>
