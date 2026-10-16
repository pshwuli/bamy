import { usePrefs } from '@/stores/prefs'

const validTz = new Map<string, boolean>()
function isValidTz(tz: string): boolean {
  if (!validTz.has(tz)) {
    try {
      new Intl.DateTimeFormat('en-US', { timeZone: tz })
      validTz.set(tz, true)
    } catch {
      validTz.set(tz, false)
    }
  }
  return validTz.get(tz)!
}

function tzOption(): string | undefined {
  try {
    const p = usePrefs()
    if (p.timezone === 'local') return undefined
    // 存了非法时区（旧数据）时退回北京时间，避免 toLocaleString 抛 RangeError 让整页白屏
    return p.timezone && isValidTz(p.timezone) ? p.timezone : 'Asia/Shanghai'
  } catch {
    return 'Asia/Shanghai'
  }
}

function hour12(): boolean {
  try {
    return usePrefs().timeFormat === '12h'
  } catch {
    return false
  }
}

export function fmtTime(v: string | number | null | undefined, withSeconds = true): string {
  if (v === null || v === undefined || v === '') return '-'
  const d = typeof v === 'number' ? new Date(v) : new Date(v)
  if (Number.isNaN(d.getTime())) return String(v)
  return d.toLocaleString('zh-CN', {
    timeZone: tzOption(),
    hour12: hour12(),
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: withSeconds ? '2-digit' : undefined,
  })
}

export function fmtNum(v: number | string | null | undefined, digits = 2): string {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: digits })
}

export function fmtMoney(v: number | string | null | undefined, digits = 2): string {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtPct(v: number | null | undefined, digits = 2, signed = true): string {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '-'
  const n = Number(v) * 100
  const s = n.toFixed(digits)
  return `${signed && n > 0 ? '+' : ''}${s}%`
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return '-'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

export function sinceSeconds(iso: string | null | undefined): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return null
  return Math.max(0, (Date.now() - t) / 1000)
}

export function pnlClass(v: number | null | undefined): string {
  if (v === null || v === undefined) return ''
  return v > 0 ? 'pnl-up' : v < 0 ? 'pnl-down' : ''
}

export function fmtBytes(n: number | null | undefined): string {
  if (!n && n !== 0) return '-'
  const u = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let x = n
  while (x >= 1024 && i < u.length - 1) {
    x /= 1024
    i++
  }
  return `${x.toFixed(i ? 1 : 0)} ${u[i]}`
}

export function pretty(v: unknown): string {
  if (v === null || v === undefined) return '-'
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  }
}

/** 交易对显示：扫描实例显示"全市场"，合约去掉 :USDT 后缀并标"永续" */
export function fmtSymbol(sym: string | null | undefined, zh = true): string {
  if (!sym) return '-'
  if (sym === 'ALL/USDT') return zh ? '全市场 · 现货' : 'All · spot'
  if (sym === 'ALL/USDT:USDT') return zh ? '全市场 · 合约' : 'All · perp'
  if (sym.endsWith(':USDT')) return `${sym.slice(0, -5)} ${zh ? '永续' : 'perp'}`
  return sym
}
