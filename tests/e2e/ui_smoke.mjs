// 真实浏览器冒烟：登录后逐页打开，收集控制台错误、未捕获异常、5xx 与页面是否渲染出内容。
// 用法：node ui_smoke.mjs <base_url> <admin_user> <admin_pass> <instance_id> <account_id> <share_token>
import { chromium } from 'playwright'

const [base, user, pass, instId, accId, shareToken] = process.argv.slice(2)
const pages = [
  '/dashboard', '/accounts', `/accounts/${accId}`, '/strategies', '/plugins', '/my-plugins', '/instances',
  '/instances/new', `/instances/${instId}`, '/orders', '/paper', '/audit', '/settings',
  '/admin/dashboard', '/admin/users', '/admin/users/1', '/admin/accounts', '/admin/instances', '/admin/strategies',
  '/admin/plugins', '/admin/reconcile', '/admin/circuit-breakers', '/admin/logs', '/admin/audit', '/admin/invites',
  '/super/configs', '/super/backup',
]

const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, locale: 'zh-CN' })
const page = await ctx.newPage()
let errors = []
let throttled = 0
page.on('console', (m) => { if (m.type() !== 'error') return; if (/status of 429/.test(m.text())) { throttled++; return } errors.push(`console: ${m.text().slice(0, 300)}`) })
page.on('pageerror', (e) => errors.push(`pageerror: ${String(e).slice(0, 300)}`))
page.on('response', (r) => { if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`) })

const results = []
async function visit(path, mobile = false) {
  errors = []
  if (mobile) await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(base + path, { waitUntil: 'networkidle', timeout: 30000 }).catch((e) => errors.push(`goto: ${e}`))
  await page.waitForTimeout(2500)  // 模拟真实用户节奏
  const text = (await page.locator('body').innerText().catch(() => '')).trim()
  const stub = /^TODO$/m.test(text)
  const finalUrl = page.url().replace(base, '')
  const ok = errors.length === 0 && text.length > 20 && !stub && !finalUrl.startsWith('/login')
  results.push({ path: path + (mobile ? ' [mobile]' : ''), ok, final: finalUrl, chars: text.length, errors: errors.slice(0, 5) })
  if (mobile) await page.setViewportSize({ width: 1366, height: 900 })
}

// 登录页
await page.goto(base + '/login', { waitUntil: 'networkidle' })
await page.locator('input').first().fill(user)
await page.locator('input[type="password"]').first().fill(pass)
await page.keyboard.press('Enter')
await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }).catch(() => {})
results.push({ path: 'login', ok: !page.url().includes('/login'), final: page.url().replace(base, ''), errors: errors.slice(0, 5) })

for (const p of pages) await visit(p)
for (const p of ['/dashboard', `/instances/${instId}`, '/admin/plugins']) await visit(p, true)
// 带筛选参数跳转到系统日志：关键字应回填到输入框
await visit('/admin/logs?keyword=telegram_notify')
{
  const v = await page.getByRole('textbox', { name: /关键词|关键字|keyword/i }).first().inputValue().catch(() => '')
  results[results.length - 1].ok &&= v === 'telegram_notify'
  if (v !== 'telegram_notify') results[results.length - 1].errors.push(`keyword 未回填：${v}`)
}
// 详情页各 Tab
await visit(`/instances/${instId}`)
const tabs = page.getByRole('tab')
const n = await tabs.count()
for (let i = 0; i < n; i++) {
  errors = []
  await tabs.nth(i).click().catch((e) => errors.push(String(e)))
  await page.waitForTimeout(600)
  results.push({ path: `/instances/${instId} tab#${i} ${await tabs.nth(i).innerText()}`, ok: errors.length === 0, errors: errors.slice(0, 5) })
}
// 访客分享页（登出态）
await ctx.clearCookies()
await page.evaluate(() => localStorage.clear())
await visit(`/view/${shareToken}`)
await visit('/view/not-a-real-token')
results[results.length - 1].ok = results[results.length - 1].errors.filter((e) => !e.startsWith('console')).length === 0

await browser.close()
let bad = 0
for (const r of results) {
  if (!r.ok) bad++
  console.log(`${r.ok ? 'OK ' : 'BAD'} ${r.path}${r.final && r.final !== r.path ? ` -> ${r.final}` : ''}${r.errors?.length ? '\n    ' + r.errors.join('\n    ') : ''}`)
}
console.log(`RESULT ${results.length - bad}/${results.length} ok（限流后自动重试 ${throttled} 次）`)
process.exit(bad ? 1 : 0)
