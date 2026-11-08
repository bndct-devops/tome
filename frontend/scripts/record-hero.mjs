// Record the hero video for the website (website/public/hero-{light,dark,amber}.webm)
// and the README (docs/screenshots/hero.gif) against the showcase instance.
//
// Usage:
//   scripts/run-showcase.sh                  # backend :8090 + frontend :5174
//   cd frontend && node scripts/record-hero.mjs [theme...]
//
// Env: BASE (default http://localhost:5174), API (default http://localhost:8090), OUT.
// The first ten seconds warm the page's caches inside the recording and are cut
// off with ffmpeg afterwards; without ffmpeg the raw file is kept as hero-<theme>.webm.
import { chromium } from 'playwright'
import { readdir, rename, rm, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
const __dir = path.dirname(new URL(import.meta.url).pathname)
const BASE = process.env.BASE ?? 'http://localhost:5174'
const API = process.env.API ?? 'http://localhost:8090'
const OUT = process.env.OUT ?? path.resolve(__dir, '../../website/public')
const VIEWPORT = { width: 1400, height: 900 }
const THEMES = process.argv.slice(2).length ? process.argv.slice(2) : ['light', 'dark', 'amber']

const r = await fetch(`${API}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'benedict', password: 'showcase' }) })
const { access_token: token } = await r.json()

async function scrollMain(page, distance, duration) {
  await page.evaluate(({ distance, duration }) => new Promise(resolve => {
    const el = document.querySelector('main') ?? document.scrollingElement
    const start = el.scrollTop, t0 = performance.now()
    const step = now => {
      const p = Math.min((now - t0) / duration, 1)
      const e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2
      el.scrollTop = start + distance * e
      p < 1 ? requestAnimationFrame(step) : resolve()
    }
    requestAnimationFrame(step)
  }), { distance, duration })
}
const pause = (page, ms) => page.waitForTimeout(ms)
async function moveTo(page, locator) {
  const box = await locator.boundingBox(); if (!box) return
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 18 })
}
async function clickSoft(page, locator, settle = 1200) {
  if (!(await locator.count())) { console.log('  missing:', locator.toString()); return false }
  await moveTo(page, locator.first()); await pause(page, 250); await locator.first().click(); await pause(page, settle); return true
}

async function record(theme) {
  const tmp = path.join(OUT, `_tmp_${theme}`); await mkdir(tmp, { recursive: true })
  const browser = await chromium.launch()
  const init = ([t, th]) => { localStorage.setItem('tome_token', t); localStorage.setItem('tome_theme', th); localStorage.setItem('tome_sidebar', 'open'); localStorage.setItem('tome_seen_version', '1.0.0'); sessionStorage.setItem('tome_follows_disabled', '1') }
  // warm the caches in a context that is not recorded
  const warm = await browser.newContext({ viewport: VIEWPORT }); await warm.addInitScript(init, [token, theme])
  const wp = await warm.newPage(); for (const u of ['/', '/?tab=books', '/?tab=series', '/stats', '/calendar']) { await wp.goto(`${BASE}${u}`); await wp.waitForLoadState('networkidle').catch(() => {}) }
  await warm.close()

  const ctx = await browser.newContext({ viewport: VIEWPORT, recordVideo: { dir: tmp, size: VIEWPORT } })
  await ctx.addInitScript(init, [token, theme])
  const page = await ctx.newPage()
  const t0 = Date.now()
  // warm the in-memory caches inside the recorded page; this stretch is cut off afterwards
  for (const u of ['/stats', '/calendar', '/?tab=series', '/?tab=books']) { await page.goto(`${BASE}${u}`); await page.waitForLoadState('networkidle').catch(() => {}); await pause(page, 900) }
  await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle').catch(() => {}); await pause(page, 900)
  const cutAt = (Date.now() - t0) / 1000
  await pause(page, 1400)

  // 1. Home: hero, then the rows and the numbers
  await scrollMain(page, 520, 1400); await pause(page, 900)
  await scrollMain(page, -520, 1000); await pause(page, 500)

  // 2. A cover in Recently added opens the Quick look; Open page goes to the book
  const h = page.getByRole('heading', { name: /recently added/i }).first()
  const block = h.locator('xpath=ancestor::*[self::section or self::div][2]')
  let cover = block.locator('button:has(img)').nth(1)
  if (!(await cover.count())) cover = block.locator('img').nth(1)
  await cover.scrollIntoViewIfNeeded().catch(() => {}); await pause(page, 600)
  if (await clickSoft(page, cover, 1600)) {
    const open = page.getByRole('button', { name: /open page/i }).or(page.getByRole('link', { name: /open page/i }))
    if (!(await clickSoft(page, open, 1800))) { await page.keyboard.press('Escape'); await pause(page, 600) }
  }
  // 3. Book page: reading stats
  if (page.url().includes('/books/')) { await scrollMain(page, 560, 1500); await pause(page, 1100) }

  // 4. Series grid, then Berserk
  await clickSoft(page, page.getByRole('link', { name: 'Series', exact: true }).or(page.locator('aside').getByText('Series', { exact: true })), 1500)
  await clickSoft(page, page.getByText('Berserk', { exact: true }).first(), 1800)
  await scrollMain(page, 520, 1500); await pause(page, 1000)

  // 5. Stats: the board, then Habits
  await clickSoft(page, page.getByRole('link', { name: 'Stats', exact: true }).or(page.locator('aside').getByText('Stats', { exact: true })), 1800)
  await scrollMain(page, 480, 1400); await pause(page, 800)
  await clickSoft(page, page.getByRole('button', { name: 'Habits', exact: true }), 1800)

  // 6. Calendar
  await clickSoft(page, page.getByRole('link', { name: 'Calendar', exact: true }).or(page.locator('aside').getByText('Calendar', { exact: true })), 2200)

  await page.close(); await ctx.close(); await browser.close()
  const f = (await readdir(tmp)).find(x => x.endsWith('.webm'))
  const raw = path.join(OUT, `raw-${theme}.webm`), out = path.join(OUT, `hero-${theme}.webm`)
  if (f) await rename(path.join(tmp, f), raw)
  await rm(tmp, { recursive: true, force: true })
  try {
    execFileSync('ffmpeg', ['-v', 'error', '-y', '-ss', String(cutAt), '-i', raw, '-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', '32', '-row-mt', '1', '-an', out])
    await rm(raw)
    if (theme === 'light') {
      const gif = path.resolve(__dir, '../../docs/screenshots/hero.gif')
      execFileSync('ffmpeg', ['-v', 'error', '-y', '-i', out, '-vf', 'fps=10,scale=760:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=192:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle', gif])
      console.log('  gif', gif)
    }
  } catch (e) { console.log('  ffmpeg not available or failed, keeping the raw recording as', out); await rename(raw, out).catch(() => {}) }
  console.log('recorded', theme, 'trimmed', cutAt.toFixed(1), 's of warm-up')
}
for (const t of THEMES) await record(t)
