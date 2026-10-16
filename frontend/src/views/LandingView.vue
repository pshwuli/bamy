<script setup lang="ts">
// 主页（未登录可见）：讲清楚 BAMY 是什么，展示真实行情与内置策略的回测摘要，引导登录或注册。
// 只用真实数据：行情来自 /api/public/overview（币安公开行情），策略数字来自 strategy.yaml 的 backtest 段。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowRight } from '@element-plus/icons-vue'
import { http } from '@/api/http'
import { useAuth } from '@/stores/auth'
import { usePrefs } from '@/stores/prefs'
import BrandMark from '@/components/BrandMark.vue'

interface Ticker { symbol: string; price: number | null }
interface BtRow { symbol: string; win_rate?: string; profit_factor?: number; cagr?: string; max_drawdown?: string }
interface PubStrategy {
  id: string; name: string; description?: string; tags?: string[]
  suitability?: { capital?: { min_usdt?: number }; market?: string } | null
  backtest?: { summary?: string; results?: BtRow[] } | null
}

const { t } = useI18n()
const auth = useAuth()
const prefs = usePrefs()
const tickers = ref<Ticker[]>([])
const prev = ref<Record<string, number>>({})
const strategies = ref<PubStrategy[]>([])
const failed = ref(false)
const allowRegister = ref(true)
let timer: ReturnType<typeof setInterval> | undefined

async function load() {
  try {
    const d = await http.get<{ tickers: Ticker[]; strategies: PubStrategy[] }>('/api/public/overview', undefined, { skipAuth: true, silent: true })
    for (const x of tickers.value) if (x.price != null) prev.value[x.symbol] = x.price
    tickers.value = d.tickers
    strategies.value = d.strategies
    failed.value = d.tickers.every((x) => x.price == null)
  } catch {
    failed.value = true
  }
}
onMounted(async () => {
  void load()
  timer = setInterval(load, 15000)
  try {
    const s = await http.get<{ allow_register?: boolean }>('/api/setup/status', undefined, { skipAuth: true, silent: true })
    allowRegister.value = s.allow_register !== false
  } catch { /* 保持默认 */ }
})
onBeforeUnmount(() => clearInterval(timer))

const loggedIn = computed(() => auth.loggedIn)
const fmtPrice = (v: number | null) => (v == null ? '—' : v >= 1000 ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : v.toLocaleString('en-US', { maximumFractionDigits: 4 }))
const tick = (x: Ticker) => {
  const p = prev.value[x.symbol]
  return x.price == null || p == null || p === x.price ? '' : x.price > p ? 'up' : 'down'
}
const headline = (s: PubStrategy) => {
  const r = s.backtest?.results ?? []
  const btc = r.find((x) => x.symbol.startsWith('BTC')) ?? r[0]
  return btc
}
const features = computed(() => [1, 2, 3, 4].map((i) => ({ t: t(`landing.s${i}_t`), p: t(`landing.s${i}_p`) })))
</script>

<template>
  <div class="landing">
    <header class="lp-nav">
      <router-link to="/" class="lp-brand" aria-label="BAMY"><BrandMark :size="30" wordmark /></router-link>
      <nav class="lp-links">
        <button type="button" class="lp-lang" @click="prefs.setLocale(prefs.locale === 'zh-CN' ? 'en-US' : 'zh-CN')">
          {{ prefs.locale === 'zh-CN' ? 'EN' : '中' }}
        </button>
        <template v-if="loggedIn">
          <router-link to="/dashboard" class="btn btn-solid">{{ t('landing.nav_open') }}</router-link>
        </template>
        <template v-else>
          <router-link to="/login" class="btn btn-ghost">{{ t('landing.nav_login') }}</router-link>
          <router-link v-if="allowRegister" to="/register" class="btn btn-solid">{{ t('landing.nav_register') }}</router-link>
        </template>
      </nav>
    </header>

    <main>
      <section class="hero">
        <div class="hero-copy">
          <p class="kicker"><i class="pulse" aria-hidden="true" />{{ t('landing.kicker') }}</p>
          <h1 class="hero-title">{{ t('landing.title1') }}<br /><em>{{ t('landing.title2') }}</em></h1>
          <p class="hero-lead">{{ t('landing.lead') }}</p>
          <div class="hero-cta">
            <router-link v-if="loggedIn" to="/dashboard" class="btn btn-solid btn-lg">{{ t('landing.cta_open') }}<el-icon><ArrowRight /></el-icon></router-link>
            <template v-else>
              <router-link :to="allowRegister ? '/register' : '/login'" class="btn btn-solid btn-lg">{{ t('landing.cta_start') }}<el-icon><ArrowRight /></el-icon></router-link>
              <router-link to="/login" class="btn btn-ghost btn-lg">{{ t('landing.cta_login') }}</router-link>
            </template>
          </div>
        </div>

        <!-- 产品自己的界面片段：实时行情板 -->
        <div class="board" data-ui aria-live="polite">
          <div class="board-head">
            <span>{{ t('landing.live_title') }}</span>
            <span class="muted">{{ failed ? t('landing.live_fail') : t('landing.live_note') }}</span>
          </div>
          <ul class="board-rows">
            <li v-for="x in tickers" :key="x.symbol" class="board-row">
              <span class="sym">{{ x.symbol.replace('/USDT', '') }}<small>/USDT</small></span>
              <span class="px num" :class="tick(x)">{{ fmtPrice(x.price) }}</span>
            </li>
            <li v-if="!tickers.length" v-for="i in 4" :key="i" class="board-row skel"><span /><span /></li>
          </ul>
        </div>
      </section>

      <section class="feats">
        <article v-for="(f, i) in features" :key="i" class="feat">
          <span class="feat-n num">0{{ i + 1 }}</span>
          <h2>{{ f.t }}</h2>
          <p>{{ f.p }}</p>
        </article>
      </section>

      <section v-if="strategies.length" class="strats">
        <div class="strats-head">
          <h2>{{ t('landing.strat_title') }}</h2>
          <router-link :to="loggedIn ? '/strategies' : '/login?redirect=/strategies'" class="more">{{ t('landing.strat_more') }} →</router-link>
        </div>
        <div class="strat-list">
          <article v-for="s in strategies" :key="s.id" class="strat">
            <h3>{{ s.name }}</h3>
            <p class="strat-desc">{{ s.description }}</p>
            <dl v-if="headline(s)" class="strat-figs">
              <div v-if="headline(s)!.win_rate"><dt>{{ t('landing.fig_win') }}</dt><dd class="num">{{ headline(s)!.win_rate }}</dd></div>
              <div v-if="headline(s)!.cagr"><dt>{{ t('landing.fig_cagr') }}</dt><dd class="num">{{ headline(s)!.cagr }}</dd></div>
              <div v-if="headline(s)!.max_drawdown"><dt>{{ t('landing.fig_mdd') }}</dt><dd class="num down">{{ headline(s)!.max_drawdown }}</dd></div>
            </dl>
            <p v-if="s.suitability?.market" class="strat-fit"><span>{{ t('landing.fit') }}</span>{{ s.suitability.market }}</p>
          </article>
        </div>
      </section>
    </main>

    <footer class="lp-foot">
      <BrandMark :size="22" wordmark />
      <p>{{ t('landing.foot_self') }}</p>
      <p class="muted">{{ t('landing.foot_risk') }}</p>
    </footer>
  </div>
</template>

<style scoped>
.landing { min-height: 100vh; background: var(--bg); color: var(--ink); --pad: clamp(20px, 5vw, 64px); }
.lp-nav { display: flex; justify-content: space-between; align-items: center; padding: 18px var(--pad); padding-top: max(18px, env(safe-area-inset-top)); }
.lp-brand { text-decoration: none; }
.lp-links { display: flex; align-items: center; gap: 8px; }
.lp-lang { background: none; border: 0; color: var(--ink-2); font: inherit; font-size: 14px; padding: 8px 10px; cursor: pointer; border-radius: 8px; }
.lp-lang:hover { background: var(--surface-2); }

.btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; height: 40px; padding: 0 18px; border-radius: 10px;
  font-weight: 600; font-size: 15px; text-decoration: none !important; white-space: nowrap; transition: background 0.15s, border-color 0.15s, transform 0.1s; }
.btn:active { transform: translateY(1px); }
.btn-solid { background: var(--accent); color: var(--accent-ink); }
.btn-solid:hover { background: color-mix(in srgb, var(--accent) 88%, #fff); }
.btn-ghost { color: var(--ink); border: 1px solid var(--line); }
.btn-ghost:hover { border-color: var(--ink-3); background: var(--surface); }
.btn-lg { height: 50px; padding: 0 24px; font-size: 16px; border-radius: 12px; }

.hero { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr); gap: clamp(32px, 5vw, 80px); align-items: center;
  padding: clamp(32px, 7vw, 96px) var(--pad) clamp(40px, 6vw, 80px); max-width: 1360px; margin: 0 auto; }
.kicker { display: inline-flex; align-items: center; gap: 10px; margin: 0 0 22px; font-size: 13px; letter-spacing: 0.06em; color: var(--ink-2); }
.pulse { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 0 var(--accent-soft); animation: pulse 2.4s ease-out infinite; }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 45%, transparent); } 100% { box-shadow: 0 0 0 12px transparent; } }
.hero-title { font-size: clamp(44px, 7.4vw, 108px); line-height: 0.96; letter-spacing: -0.045em; font-weight: 600; margin: 0; text-wrap: balance; }
.hero-title em { font-style: normal; color: var(--accent); }
.hero-lead { font-size: clamp(17px, 1.5vw, 20px); line-height: 1.55; color: var(--ink-2); max-width: 36ch; margin: 26px 0 0; }
.hero-cta { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 34px; }

.board { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; box-shadow: var(--shadow); overflow: hidden; }
.board-head { display: flex; justify-content: space-between; gap: 12px; padding: 14px 18px; font-size: 13px; font-weight: 600; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.board-head .muted { font-weight: 400; font-size: 12px; }
.board-rows { list-style: none; margin: 0; padding: 0; }
.board-row { display: flex; justify-content: space-between; align-items: baseline; padding: 16px 18px; border-bottom: 1px solid var(--line); }
.board-row:last-child { border-bottom: 0; }
.sym { font-weight: 600; font-size: 17px; }
.sym small { font-weight: 400; color: var(--ink-3); font-size: 13px; margin-left: 2px; }
.px { font-size: clamp(20px, 2vw, 26px); font-weight: 500; transition: color 0.6s; }
.px.up { color: var(--up); }
.px.down { color: var(--down); }
.skel span { display: block; height: 18px; width: 30%; border-radius: 6px; background: var(--surface-2); }
.skel span + span { width: 40%; }

.feats { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.feat { padding: clamp(24px, 3vw, 40px) var(--pad) clamp(28px, 3vw, 44px) clamp(20px, 2.4vw, 32px); border-right: 1px solid var(--line); }
.feat:first-child { padding-left: var(--pad); }
.feat:last-child { border-right: 0; }
.feat-n { color: var(--accent); font-size: 13px; }
.feat h2 { font-size: 19px; margin: 14px 0 10px; letter-spacing: -0.02em; }
.feat p { margin: 0; color: var(--ink-2); font-size: 15px; line-height: 1.6; max-width: 34ch; }

.strats { padding: clamp(40px, 6vw, 88px) var(--pad); max-width: 1360px; margin: 0 auto; }
.strats-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 22px; flex-wrap: wrap; }
.strats-head h2 { font-size: clamp(28px, 3.4vw, 44px); margin: 0; letter-spacing: -0.035em; }
.more { font-weight: 500; }
.strat-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
.strat { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 22px; display: flex; flex-direction: column; }
.strat h3 { margin: 0 0 10px; font-size: 18px; }
.strat-desc { margin: 0; color: var(--ink-2); font-size: 14px; line-height: 1.6; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
.strat-figs { display: flex; gap: 20px; margin: 18px 0 0; padding-top: 16px; border-top: 1px solid var(--line); flex-wrap: wrap; }
.strat-figs dt { font-size: 12px; color: var(--ink-3); }
.strat-figs dd { margin: 4px 0 0; font-size: 20px; font-weight: 500; }
.strat-figs dd.down { color: var(--down); }
.strat-fit { margin: 16px 0 0; font-size: 13px; color: var(--ink-2); line-height: 1.5; }
.strat-fit span { color: var(--accent); margin-right: 8px; font-weight: 600; }

.lp-foot { padding: 32px var(--pad) calc(40px + env(safe-area-inset-bottom)); border-top: 1px solid var(--line); display: grid; gap: 10px; font-size: 13px; color: var(--ink-2); }
.lp-foot p { margin: 0; max-width: 70ch; }

@media (max-width: 960px) {
  .hero { grid-template-columns: 1fr; }
  .feats { grid-template-columns: 1fr 1fr; }
  .feat { border-bottom: 1px solid var(--line); padding-left: var(--pad); }
  .feat:nth-child(2n) { border-right: 0; }
}
@media (max-width: 560px) {
  .lp-nav .btn { padding-left: 12px; padding-right: 12px; }
  .hero { padding-top: 20px; }
  .hero-title { font-size: clamp(44px, 13vw, 64px); }
  .hero-cta .btn { flex: 1 1 100%; }
  .feats { grid-template-columns: 1fr; }
  .feat { border-right: 0; }
}
</style>
