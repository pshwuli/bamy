import * as echarts from 'echarts/core'
import { LineChart, BarChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  AriaComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([LineChart, BarChart, PieChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkLineComponent, AriaComponent, CanvasRenderer])

export { echarts }

export const SERIES_LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']
export const SERIES_DARK = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767']
export const MAX_SERIES = 10

export interface ChartInk {
  surface: string
  text: string
  textSecondary: string
  muted: string
  grid: string
  axis: string
  series: string[]
}

export function inkFor(dark: boolean): ChartInk {
  return dark
    ? { surface: '#1a1a19', text: '#f0efec', textSecondary: '#c3c2b7', muted: '#898781', grid: '#2c2c2a', axis: '#383835', series: SERIES_DARK }
    : { surface: '#fcfcfb', text: '#0b0b0b', textSecondary: '#52514e', muted: '#898781', grid: '#e1e0d9', axis: '#c3c2b7', series: SERIES_LIGHT }
}

export function colorForSlot(slot: number, dark: boolean): string {
  const s = inkFor(dark).series
  return s[slot % s.length]
}

export const DASHES: ('solid' | 'dashed' | 'dotted')[] = ['solid', 'solid', 'solid', 'solid', 'solid', 'solid', 'solid', 'solid', 'dashed', 'dotted']
