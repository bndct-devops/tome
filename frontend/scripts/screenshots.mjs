// Regenerate the screenshots in docs/screenshots/ by driving a real browser
// against a running Tome (frontend + backend).
//
// Usage:
//   ./dev.sh                       # in another terminal
//   TOME_SCREENSHOT_PASS=... node scripts/screenshots.mjs
//
// Environment:
//   TOME_SCREENSHOT_BASE   Frontend URL    (default http://localhost:5173)
//   TOME_SCREENSHOT_API    Backend URL     (default http://localhost:8080)
//   TOME_SCREENSHOT_USER   Admin username  (default benedict)
//   TOME_SCREENSHOT_PASS   Admin password  (REQUIRED)
//   TOME_SCREENSHOT_ONLY   Comma-separated shot names to capture (default: all)
//
// Each shot lives in SHOTS below: name → path / viewport / optional waitFor.
// New routes? Add a row. PNGs land in docs/screenshots/.
import { chromium, devices } from 'playwright'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'

// --showcase flag flips defaults to the showcase stack on :5174/:8090
const SHOWCASE = process.argv.includes('--showcase')
const BASE = process.env.TOME_SCREENSHOT_BASE ?? (SHOWCASE ? 'http://localhost:5174' : 'http://localhost:5173')
const API = process.env.TOME_SCREENSHOT_API ?? (SHOWCASE ? 'http://localhost:8090' : 'http://localhost:8080')
const USER = process.env.TOME_SCREENSHOT_USER ?? 'benedict'
const PASS = process.env.TOME_SCREENSHOT_PASS ?? (SHOWCASE ? 'showcase' : undefined)
const TOKEN = process.env.TOME_SCREENSHOT_TOKEN  // Skip /login; use this token directly.
const ONLY = process.env.TOME_SCREENSHOT_ONLY?.split(',').map(s => s.trim())
const THEME = process.env.TOME_SCREENSHOT_THEME  // 'light' | 'dark' | 'amber' — overrides per-shot
// Always write to the repo's docs/screenshots/, regardless of cwd.
const __dir = path.dirname(new URL(import.meta.url).pathname)
const OUT = path.resolve(__dir, '../../docs/screenshots')

if (!PASS && !TOKEN) {
  console.error('Set TOME_SCREENSHOT_PASS (admin password) OR TOME_SCREENSHOT_TOKEN (existing JWT/API token).')
  process.exit(1)
}

const DESKTOP = { width: 1600, height: 1000, deviceScaleFactor: 2 }
const MOBILE = devices['iPhone 13']  // 390×844, scale 3, mobile UA, touch

// Populated at startup via the API — book IDs shift across re-seeds so we
// can't hardcode them. See `resolveBookIds()`.
const bookIds = {}

/** @type {Array<{name: string, path: string, viewport?: any, mobile?: boolean, waitFor?: string, settle?: number}>} */
const SHOTS = [
  // Desktop
  { name: 'home', path: '/', viewport: DESKTOP, waitFor: 'h2, h3, [class*="streak"]' },
  { name: 'dashboard', path: '/?view=large', viewport: DESKTOP, waitFor: 'img[loading="lazy"], [class*="grid"]' },
  { name: 'series', path: '/?tab=series', viewport: DESKTOP, settle: 800 },
  { name: 'book-detail', path: '/books/1', viewport: DESKTOP, settle: 800 },
  { name: 'series-detail', path: '/?tab=series&series_detail=Berserk', viewport: DESKTOP, settle: 1200, prefs: { tome_sidebar: 'closed' } },
  { name: 'stats', path: '/stats', viewport: DESKTOP, settle: 1200 },
  // Cropped view of the stats top — readable at half-width on the landing page.
  // Width is the full DESKTOP, height crops just the metrics-row + currently-reading.
  { name: 'stats-overview', path: '/stats', viewport: DESKTOP, settle: 1200, clip: { x: 0, y: 0, width: 1600, height: 720 } },

  // Mobile (PWA)
  { name: 'mobile-home', path: '/', mobile: true, waitFor: 'h2, h3, [class*="streak"]' },
  { name: 'mobile-stats', path: '/stats', mobile: true, settle: 1200 },
  { name: 'mobile-series', path: '/?tab=series', mobile: true, settle: 800 },
  {
    name: 'mobile-reader',
    // Resolved at runtime from the showcase DB (Frankenstein's id can shift across re-seeds).
    path: () => `/reader/${bookIds.frankenstein ?? 1}`,
    mobile: true,
    settle: 2500,  // foliate-view needs time to render the EPUB inside its iframe
    after: async (page) => {
      // First wait for the EPUB to actually be loaded by foliate-view —
      // its inner iframe appears once content is ready.
      await page.waitForSelector('foliate-view', { timeout: 8000 }).catch(() => {})
      await page.waitForTimeout(2000)
      // Turn a few pages so the screenshot shows real prose, not the cover.
      const viewport = page.viewportSize() || { width: 390, height: 844 }
      const x = viewport.width * 0.85
      const y = viewport.height * 0.5
      for (let i = 0; i < 10; i++) {
        await page.touchscreen.tap(x, y)
        await page.waitForTimeout(350)
      }
    },
    // After the screenshot is captured, reset Frankenstein to unread so the
    // showcase stays consistent across re-runs (the reader's auto-track would
    // otherwise leave it marked as `reading` with stale progress).
    cleanup: async (token, api) => {
      const id = bookIds.frankenstein
      if (!id) return
      await fetch(`${api}/api/books/${id}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ status: 'unread' }),
      }).catch(() => {})
    },
  },
  // Sidebar drawer needs interaction — open the hamburger after load
  {
    name: 'mobile-sidebar',
    path: '/',
    mobile: true,
    after: async (page) => {
      await page.locator('button[aria-label*="navigation" i], button:has-text("☰")').first().click().catch(() => {})
      await page.waitForTimeout(400)
    },
  },
]

async function login() {
  const r = await fetch(`${API}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: USER, password: PASS }),
  })
  if (!r.ok) throw new Error(`Login failed (${r.status}): ${await r.text()}`)
  const j = await r.json()
  return j.access_token
}

async function resolveBookIds(token) {
  // Look up book IDs by title. Keeps the script working across re-seeds.
  const wanted = { frankenstein: 'Frankenstein' }
  try {
    const r = await fetch(`${API}/api/books?q=Frankenstein&per_page=5`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    const data = await r.json()
    const list = Array.isArray(data) ? data : (data.books ?? [])
    for (const [key, title] of Object.entries(wanted)) {
      const hit = list.find(b => b.title === title)
      if (hit) bookIds[key] = hit.id
    }
  } catch (e) {
    console.warn('  ! Could not resolve book IDs:', e.message)
  }
}

async function captureShot(browser, token, shot) {
  const context = await browser.newContext(shot.mobile ? MOBILE : { viewport: shot.viewport })
  const theme = THEME ?? shot.theme ?? 'light'
  const prefs = { ...(shot.prefs ?? {}) }
  // Reader has its own theme (light/sepia/dark) stored separately. For shots
  // that render the reader, mirror the app theme — amber → sepia (amber isn't
  // a valid reader theme).
  const pathStr = typeof shot.path === 'function' ? shot.path() : shot.path
  if (shot.syncReaderTheme || pathStr.startsWith('/reader/')) {
    prefs.reader_theme = theme === 'amber' ? 'sepia' : theme
  }
  await context.addInitScript(({ t, theme, prefs }) => {
    localStorage.setItem('tome_token', t)
    localStorage.setItem('tome_theme', theme)
    for (const [k, v] of Object.entries(prefs)) localStorage.setItem(k, v)
  }, { t: token, theme, prefs })
  const page = await context.newPage()
  await page.goto(`${BASE}${pathStr}`, { waitUntil: 'domcontentloaded' })
  if (shot.waitFor) {
    await page.waitForSelector(shot.waitFor, { timeout: 10000 }).catch(() => {})
  }
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {})
  if (shot.after) await shot.after(page)
  if (shot.settle) await page.waitForTimeout(shot.settle)
  const file = path.join(OUT, `${shot.name}.png`)
  await page.screenshot({ path: file, fullPage: false, ...(shot.clip ? { clip: shot.clip } : {}) })
  await context.close()
  return file
}

async function main() {
  await mkdir(OUT, { recursive: true })

  const shots = ONLY ? SHOTS.filter(s => ONLY.includes(s.name)) : SHOTS
  if (ONLY && shots.length !== ONLY.length) {
    const missing = ONLY.filter(n => !shots.find(s => s.name === n))
    console.warn(`Unknown shot name(s): ${missing.join(', ')}`)
  }

  let token = TOKEN
  if (!token) {
    console.log(`Logging in as ${USER} against ${API}...`)
    token = await login()
  } else {
    console.log('Using TOME_SCREENSHOT_TOKEN (skipping /login)')
  }

  await resolveBookIds(token)
  if (Object.keys(bookIds).length) {
    console.log(`Resolved book IDs:`, bookIds)
  }

  console.log(`Capturing ${shots.length} shot(s) → ${OUT}`)
  const browser = await chromium.launch()
  try {
    for (const shot of shots) {
      const t0 = Date.now()
      const file = await captureShot(browser, token, shot)
      console.log(`  ✓ ${shot.name.padEnd(20)} ${path.relative(process.cwd(), file)} (${Date.now() - t0}ms)`)
      if (shot.cleanup) await shot.cleanup(token, API)
    }
  } finally {
    await browser.close()
  }
}

main().catch(e => {
  console.error('Screenshot run failed:', e.message)
  process.exit(1)
})
