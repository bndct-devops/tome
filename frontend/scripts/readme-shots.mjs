// README screenshots (docs/screenshots/*.png) against the showcase instance.
// Light theme, 1600x1000 desktop, 400px phone.
//
// Usage:
//   scripts/run-showcase.sh                  # backend :8090 + frontend :5174
//   cd frontend && node scripts/readme-shots.mjs
//
// Env: BASE (default http://localhost:5174), API (default http://localhost:8090), OUT, THEME.
import { chromium } from 'playwright'
import path from 'node:path'
const __dir = path.dirname(new URL(import.meta.url).pathname)
const BASE = process.env.BASE ?? 'http://localhost:5174', API = process.env.API ?? 'http://localhost:8090'
const OUT = process.env.OUT ?? path.resolve(__dir, '../../docs/screenshots')
const THEME = process.env.THEME ?? 'light'
const r = await fetch(`${API}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'benedict', password: 'showcase' }) })
const { access_token: token } = await r.json()
const hdr = { authorization: `Bearer ${token}` }
const books = await (await fetch(`${API}/api/books?limit=200`, { headers: hdr })).json()
const list = Array.isArray(books) ? books : (books.items ?? books.books ?? [])
const pick = (pred) => list.find(pred)
const berserk = pick(b => /berserk/i.test(b.series || '') && b.series_index === 1) ?? pick(b => b.series)
const epub = pick(b => (b.files || []).some(f => f.format === 'epub') && /frankenstein/i.test(b.title)) ?? pick(b => (b.files || []).some(f => f.format === 'epub'))
console.log('book for detail:', berserk?.title, '| epub for reader:', epub?.title)
const browser = await chromium.launch()
async function ctx(w, h) {
  const c = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 })
  await c.addInitScript(([t, th]) => { localStorage.setItem('tome_token', t); localStorage.setItem('tome_theme', th); localStorage.setItem('tome_sidebar', 'open'); localStorage.setItem('tome_seen_version', '1.0.0'); localStorage.setItem('tome_stats_hint', '1'); sessionStorage.setItem('tome_follows_disabled', '1') }, [token, THEME])
  return c.newPage()
}
async function go(p, u, settle = 1200) { await p.goto(`${BASE}${u}`); await p.waitForLoadState('networkidle').catch(() => {}); await p.waitForTimeout(settle) }
async function shot(p, name, { full = false, maxH = 2200 } = {}) {
  if (full) { const h = await p.evaluate(() => (document.querySelector('main')?.scrollHeight ?? document.body.scrollHeight) + 90); const vp = p.viewportSize(); await p.setViewportSize({ width: vp.width, height: Math.min(Math.max(h, vp.height), maxH) }); await p.waitForTimeout(400); await p.screenshot({ path: `${OUT}/${name}.png` }); await p.setViewportSize(vp) }
  else await p.screenshot({ path: `${OUT}/${name}.png` })
  console.log('shot', name)
}
let p = await ctx(1600, 1000)
await go(p, '/'); await shot(p, 'home')
await go(p, '/?tab=books'); await shot(p, 'dashboard')
await go(p, '/?tab=series'); await shot(p, 'series')
if (berserk) { await go(p, `/?tab=series&series_detail=${encodeURIComponent(berserk.series)}`); await shot(p, 'series-detail') }
if (berserk) { await go(p, `/books/${berserk.id}`); await shot(p, 'book-detail') }
await go(p, '/stats', 2000); await shot(p, 'stats')
await go(p, '/calendar', 1800); await shot(p, 'calendar')
await go(p, '/highlights'); await shot(p, 'koreader-highlights')
// quick look from Home
await go(p, '/'); {
  const h = p.getByRole('heading', { name: /recently added/i }).first()
  const block = h.locator('xpath=ancestor::*[self::section or self::div][2]')
  let c = block.locator('button:has(img)').first()
  if (!(await c.count())) c = block.locator('img').first()
  if (await c.count()) { await c.scrollIntoViewIfNeeded(); await c.click(); await p.waitForTimeout(1300); if (await p.locator('[role="dialog"], .quicklook, [data-quicklook]').count() || await p.getByRole('button', { name: /open page/i }).count()) await shot(p, 'quick-look'); else console.log('quick look did not open') }
  else console.log('no recently added cover found')
}
await p.context().close()
// phone
p = await ctx(400, 850)
await go(p, '/'); await shot(p, 'mobile-home')
await go(p, '/stats', 2000); await shot(p, 'mobile-stats')
await go(p, '/'); { const menu = p.locator('header button').first(); await menu.click(); await p.waitForTimeout(700); await shot(p, 'mobile-sidebar') }
await go(p, '/?tab=series'); await shot(p, 'mobile-series')
if (epub) { await go(p, `/reader/${epub.id}`, 3500); for (let i = 0; i < 9; i++) { await p.keyboard.press('ArrowRight'); await p.waitForTimeout(350) } await p.waitForTimeout(800); await p.mouse.click(200, 425); await p.waitForTimeout(700); await shot(p, 'mobile-reader') }
await p.context().close(); await browser.close()
