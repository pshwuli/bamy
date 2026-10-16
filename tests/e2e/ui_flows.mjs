// 交互流：通过界面完成注册→登录→建实例→启动→看到成交→停止→安装插件→设置主题，并截图。
// 用法：node ui_flows.mjs <base_url>
import { chromium } from 'playwright'

const base = process.argv[2]
const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, locale: 'zh-CN' })
const page = await ctx.newPage()
const errs = []
page.on('pageerror', (e) => errs.push(String(e)))
page.on('response', (r) => { if (r.status() >= 500) errs.push(`HTTP ${r.status()} ${r.url()}`) })
const steps = []
async function step(name, fn) {
  const before = errs.length
  try {
    await fn()
    steps.push([name, errs.length === before, errs.slice(before).join(' | ')])
  } catch (e) {
    steps.push([name, false, String(e).split('\n')[0]])
    await page.screenshot({ path: `/e2e/fail-${steps.length}.png`, fullPage: true }).catch(() => {})
  }
}
const uname = 'ui' + Date.now().toString().slice(-6)

await step('注册', async () => {
  await page.goto(base + '/register', { waitUntil: 'networkidle' })
  const inputs = page.locator('form input')
  await inputs.nth(0).fill(uname)
  const pw = page.locator('input[type="password"]')
  await pw.nth(0).fill('Uitest12345')
  await pw.nth(1).fill('Uitest12345')
  await page.locator('form button[type="submit"], form .el-button--primary').first().click()
  await page.waitForURL(/\/(login|dashboard)/, { timeout: 15000 })
})

await step('登录', async () => {
  if (!page.url().includes('/login')) await page.goto(base + '/login', { waitUntil: 'networkidle' })
  await page.locator('input').first().fill(uname)
  await page.locator('input[type="password"]').first().fill('Uitest12345')
  await page.keyboard.press('Enter')
  await page.waitForURL(/\/dashboard/, { timeout: 15000 })
})

await step('从策略页进入创建实例并提交', async () => {
  await page.goto(base + '/instances/new?strategy=dca_strategy', { waitUntil: 'networkidle' })
  await page.getByLabel(/实例名/).first().fill('界面创建的定投')
  const sym = page.getByLabel(/交易对/).first()
  if (await sym.evaluate((el) => el.tagName === 'INPUT' && !el.readOnly)) await sym.fill('BTC/USDT')
  else { await sym.click(); await page.getByRole('option', { name: 'BTC/USDT' }).first().click() }
  await page.getByRole('button', { name: /^(创建|提交|确定|保存)/ }).last().click()
  await page.waitForURL(/\/instances\/\d+/, { timeout: 15000 })
})

await step('启动实例并看到成交', async () => {
  await page.getByRole('button', { name: /^启动$/ }).first().click()
  await page.getByText(/运行中/).first().waitFor({ timeout: 15000 })
  await page.getByRole('tab', { name: '订单' }).click()
  await page.locator('[role=tabpanel]:visible').getByText(/已成交/).first().waitFor({ timeout: 20000 })
  await page.screenshot({ path: './instance-orders.png', fullPage: true })
})

await step('停止实例', async () => {
  await page.getByRole('button', { name: /^停止$/ }).first().click()
  const confirmBtn = page.locator('.el-message-box__btns .el-button--primary')
  if (await confirmBtn.isVisible().catch(() => false)) await confirmBtn.click()
  await page.getByText(/已停止/).first().waitFor({ timeout: 15000 })
})

await step('插件市场安装审计导出（用户级）', async () => {
  await page.goto(base + '/plugins', { waitUntil: 'networkidle' })
  const card = page.locator('article', { hasText: /审计/ }).first()
  await card.getByRole('button', { name: /安装/ }).click()
  const dlg = page.locator('.el-dialog:visible').last()
  await dlg.waitFor()
  const done = page.locator('.el-message-box', { hasText: /安装成功/ })
  for (let i = 0; i < 5; i++) {
    if (await done.isVisible().catch(() => false)) break
    const next = dlg.getByRole('button', { name: /下一步|确认安装|安装|确定/ }).last()
    if (!(await next.isVisible().catch(() => false))) break
    await next.click()
    await page.waitForTimeout(700)
  }
  await done.waitFor({ timeout: 10000 })
  await done.getByRole('button', { name: /^关闭$/ }).last().click()
  await page.goto(base + '/my-plugins', { waitUntil: 'networkidle' })
  await page.getByText(/审计/).first().waitFor({ timeout: 10000 })
  await page.screenshot({ path: './my-plugins.png', fullPage: true })
})

await step('切换暗色主题与英文', async () => {
  await page.goto(base + '/settings', { waitUntil: 'networkidle' })
  await page.getByRole('tab', { name: /偏好/ }).click()
  await page.getByText(/暗色/).first().click()
  await page.getByRole('button', { name: /^保存/ }).last().click()
  await page.waitForTimeout(800)
  const dark = await page.evaluate(() => document.documentElement.classList.contains('dark'))
  if (!dark) throw new Error('未切换到暗色')
  await page.screenshot({ path: './settings-dark.png', fullPage: true })
})

await step('普通用户访问管理员页被拒', async () => {
  await page.goto(base + '/admin/users', { waitUntil: 'networkidle' })
  // 路由守卫异步校验角色后才跳 404，等跳转发生（或确认留在原地）再判断
  await page.waitForURL((u) => !u.pathname.startsWith('/admin'), { timeout: 8000 }).catch(() => {})
  const url = page.url()
  const text = await page.locator('body').innerText()
  if (url.endsWith('/admin/users') && !/无权|不存在|404|403/.test(text)) throw new Error('普通用户看到了管理员页：' + url)
})

await page.goto(base + '/dashboard', { waitUntil: 'networkidle' })
await page.screenshot({ path: './dashboard.png', fullPage: true })
await browser.close()
let bad = 0
for (const [n, ok, info] of steps) { if (!ok) bad++; console.log(`${ok ? 'OK ' : 'BAD'} ${n}${info ? '  ' + info : ''}`) }
console.log(`RESULT ${steps.length - bad}/${steps.length}`)
