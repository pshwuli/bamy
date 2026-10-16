import { chromium } from 'playwright'
const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 390, height: 844 } })
const errs = []; p.on('pageerror', (e) => errs.push(String(e))); p.on('console', (m) => m.type() === 'error' && errs.push(m.text()))
await p.goto(process.argv[2] + '/', { waitUntil: 'networkidle' }); await p.waitForTimeout(1500)
console.log('URL', p.url()); console.log('TEXT', (await p.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 300)); console.log('ERR', errs.slice(0, 5))
await p.screenshot({ path: './home-now.png' }); await b.close()
