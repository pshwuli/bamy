// 设计走查截图：按手机（390×844）和桌面（1440×900）两种尺寸截关键页面，保存到 ./shots/<tag>/
// 用法：node shots.mjs <base_url> <user> <pass> <tag> [instance_id]
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const [base, user, pass, tag = 'now', instId = '1'] = process.argv.slice(2)
const out = `./shots/${tag}`
mkdirSync(out, { recursive: true })
const pages = [
  ['home', '/'], ['login', '/login'],
  ['dashboard', '/dashboard'], ['instances', '/instances'], ['instance', `/instances/${instId}`],
  ['strategies', '/strategies'], ['create', '/instances/new'], ['orders', '/orders'], ['accounts', '/accounts'],
  ['admin', '/admin/dashboard'],
]
const browser = await chromium.launch()
for (const [vw, vh, dev] of [[390, 844, 'm'], [1440, 900, 'd']]) {
  const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, deviceScaleFactor: 1, locale: 'zh-CN',
    isMobile: dev === 'm', hasTouch: dev === 'm' })
  const page = await ctx.newPage()
  for (const [name, path] of pages.slice(0, 2)) {
    await page.goto(base + path, { waitUntil: 'networkidle' }); await page.waitForTimeout(800)
    await page.screenshot({ path: `${out}/${dev}-${name}.png`, fullPage: dev === 'm' })
  }
  await page.goto(base + '/login', { waitUntil: 'networkidle' })
  await page.locator('input').first().fill(user)
  await page.locator('input[type=password]').first().fill(pass)
  await page.keyboard.press('Enter')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }).catch(() => {})
  for (const [name, path] of pages.slice(2)) {
    await page.goto(base + path, { waitUntil: 'networkidle' }); await page.waitForTimeout(1200)
    // 检查横向溢出：手机上页面不应出现整页横向滚动
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    if (overflow > 2) console.log(`OVERFLOW ${dev}-${name} +${overflow}px`)
    await page.screenshot({ path: `${out}/${dev}-${name}.png`, fullPage: dev === 'm' })
  }
  await ctx.close()
}
await browser.close()
console.log('shots ->', out)
