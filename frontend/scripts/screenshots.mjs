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
  // Cropped view of the stats Overview tab — autoCrop trims to the centered content.
  // Used on the landing page.
  { name: 'stats-overview', path: '/stats', viewport: { width: 1600, height: 1400, deviceScaleFactor: 2 }, settle: 1500, autoCrop: true },

  // Per-card element-bounded stats shots for /docs/stats. Each card gets its
  // own tight crop so the docs can interleave shot + description per H3.
  // Overview tab
  { name: 'stats-totals',             path: '/stats', viewport: { width: 1400, height: 1200, deviceScaleFactor: 2 }, settle: 1500, element: 'div[class*="lg:grid-cols-6"]' },
  { name: 'stats-currently-reading',  path: '/stats', viewport: { width: 1400, height: 1200, deviceScaleFactor: 2 }, settle: 1500, element: 'div.rounded-xl:has(h3:text-is("Currently Reading"))' },
  { name: 'stats-time-per-day',       path: '/stats', viewport: { width: 1400, height: 1200, deviceScaleFactor: 2 }, settle: 1500, element: 'div.rounded-xl:has(h3:text-is("Reading Time per Day"))' },
  { name: 'stats-top-books',          path: '/stats', viewport: { width: 1400, height: 1200, deviceScaleFactor: 2 }, settle: 1500, element: 'div.rounded-xl:has(h3:text-is("Top Books by Reading Time"))' },
  { name: 'stats-activity-grid',      path: '/stats', viewport: { width: 1400, height: 1200, deviceScaleFactor: 2 }, settle: 1500, element: 'div.rounded-xl:has(h3:has-text("Reading Activity"))' },

  // Habits tab — click the Habits pill first
  { name: 'stats-heatmap',           path: '/stats', viewport: { width: 1400, height: 1400, deviceScaleFactor: 2 }, settle: 1500, after: async (p) => { await p.locator('button:has-text("habits")').first().click().catch(() => {}); await p.waitForTimeout(700) }, element: 'div.rounded-xl:has(h3:has-text("Reading Intensity"))' },
  { name: 'stats-session-timeline',  path: '/stats', viewport: { width: 1400, height: 1400, deviceScaleFactor: 2 }, settle: 1500, after: async (p) => { await p.locator('button:has-text("habits")').first().click().catch(() => {}); await p.waitForTimeout(700) }, element: 'div.rounded-xl:has(h3:text-is("Session Timeline"))' },
  { name: 'stats-pace',              path: '/stats', viewport: { width: 1400, height: 1400, deviceScaleFactor: 2 }, settle: 1500, after: async (p) => { await p.locator('button:has-text("habits")').first().click().catch(() => {}); await p.waitForTimeout(700) }, element: 'div.rounded-xl:has(h3:text-is("Reading Pace"))' },
  { name: 'stats-monthly-comparison', path: '/stats', viewport: { width: 1400, height: 1400, deviceScaleFactor: 2 }, settle: 1500, after: async (p) => { await p.locator('button:has-text("habits")').first().click().catch(() => {}); await p.waitForTimeout(700) }, element: 'div.rounded-xl:has(h3:has-text("Last 12 Months"))' },

  // Library tab — click the Library pill first
  { name: 'stats-series-completion', path: '/stats', viewport: { width: 1400, height: 1400, deviceScaleFactor: 2 }, settle: 1500, after: async (p) => { await p.locator('button:has-text("library")').first().click().catch(() => {}); await p.waitForTimeout(700) }, element: 'div:has(> h2:has-text("Series Completion"))' },
  { name: 'stats-author-affinity',   path: '/stats', viewport: { width: 1400, height: 1400, deviceScaleFactor: 2 }, settle: 1500, after: async (p) => { await p.locator('button:has-text("library")').first().click().catch(() => {}); await p.waitForTimeout(700) }, element: 'div.rounded-xl:has(h3:text-is("Top Authors by Reading Time"))' },
  { name: 'stats-completion-by-type',path: '/stats', viewport: { width: 1400, height: 1400, deviceScaleFactor: 2 }, settle: 1500, after: async (p) => { await p.locator('button:has-text("library")').first().click().catch(() => {}); await p.waitForTimeout(700) }, element: 'div.rounded-xl:has(h3:has-text("Finish Rate per Book Category"))' },
  { name: 'stats-category-breakdown',path: '/stats', viewport: { width: 1400, height: 1400, deviceScaleFactor: 2 }, settle: 1500, after: async (p) => { await p.locator('button:has-text("library")').first().click().catch(() => {}); await p.waitForTimeout(700) }, element: 'div.rounded-xl:has(h3:text-is("Category Breakdown"))' },
  { name: 'stats-library-growth',    path: '/stats', viewport: { width: 1400, height: 1400, deviceScaleFactor: 2 }, settle: 1500, after: async (p) => { await p.locator('button:has-text("library")').first().click().catch(() => {}); await p.waitForTimeout(700) }, element: 'div.rounded-xl:has(h3:has-text("Cumulative Books Added"))' },

  // Docs — admin + feature surfaces (tier 2 sweep). All auto-cropped so the
  // shot ends where the content ends instead of bleeding into the min-h-screen tail.
  {
    name: 'stats-habits',
    path: '/stats',
    viewport: { width: 1600, height: 2400, deviceScaleFactor: 2 },
    settle: 1500,
    autoCrop: true,
    after: async (page) => {
      await page.locator('button:has-text("habits")').first().click().catch(() => {})
      await page.waitForTimeout(800)
    },
  },
  {
    name: 'stats-library',
    path: '/stats',
    viewport: { width: 1600, height: 2400, deviceScaleFactor: 2 },
    settle: 1500,
    autoCrop: true,
    after: async (page) => {
      await page.locator('button:has-text("library")').first().click().catch(() => {})
      await page.waitForTimeout(800)
    },
  },
  { name: 'users-list',  path: '/users',    viewport: { width: 1600, height: 2000, deviceScaleFactor: 2 }, settle: 1000, autoCrop: true },
  { name: 'admin-page',  path: '/admin',    viewport: { width: 1600, height: 2000, deviceScaleFactor: 2 }, settle: 1000, autoCrop: true },
  { name: 'settings',    path: '/settings', viewport: { width: 1600, height: 2400, deviceScaleFactor: 2 }, settle: 1000, autoCrop: true },
  { name: 'bindery',     path: '/bindery',  viewport: { width: 1600, height: 2000, deviceScaleFactor: 2 }, settle: 1000, autoCrop: true },

  // Section/modal tight crops — capture exactly one block of UI via element selector.
  {
    name: 'settings-opds',
    path: '/settings',
    viewport: { width: 1600, height: 2000, deviceScaleFactor: 2 },
    settle: 1000,
    element: 'div.p-6.space-y-4:has(p:has-text("OPDS Catalog"))',
  },
  {
    name: 'settings-api-tokens',
    path: '/settings',
    viewport: { width: 1600, height: 2400, deviceScaleFactor: 2 },
    settle: 1000,
    element: 'div.bg-card.rounded-xl:has(button:has-text("New Token"))',
  },
  {
    name: 'settings-quick-connect',
    path: '/settings',
    viewport: { width: 1600, height: 2000, deviceScaleFactor: 2 },
    settle: 1000,
    element: 'div.p-5:has(p:text-is("Quick Connect"))',
  },
  {
    name: 'settings-themes',
    path: '/settings',
    viewport: { width: 1600, height: 2000, deviceScaleFactor: 2 },
    settle: 1000,
    after: async (page) => {
      await page.locator('span:text-is("Appearance")').first().scrollIntoViewIfNeeded().catch(() => {})
      await page.waitForTimeout(300)
      // Add padding around the section element by expanding its box via a wrapper
      await page.evaluate(() => {
        const section = document.querySelector('section:has(span)')
        const sections = [...document.querySelectorAll('section')]
        const target = sections.find(s => s.querySelector('span')?.textContent?.trim() === 'Appearance')
        if (target) target.style.padding = '48px 32px'
      })
    },
    element: 'section:has(span:text-is("Appearance"))',
  },
  {
    name: 'book-detail-edit',
    path: '/books/1',
    viewport: { width: 1600, height: 1400, deviceScaleFactor: 2 },
    settle: 1000,
    after: async (page) => {
      await page.locator('button:has-text("Edit")').first().click().catch(() => {})
      await page.waitForTimeout(600)
    },
    autoCrop: true,
  },
  {
    name: 'upload-modal',
    path: '/',
    viewport: { width: 1600, height: 1200, deviceScaleFactor: 2 },
    settle: 1000,
    after: async (page) => {
      await page.locator('button:has-text("Upload")').first().click().catch(() => {})
      await page.waitForTimeout(500)
      await maskModalBackdrop(page)
    },
    element: 'div.max-w-lg:has(h2:has-text("Upload"))',
  },
  {
    name: 'sidebar-libraries',
    path: '/?tab=books',
    viewport: { width: 1600, height: 1200, deviceScaleFactor: 2 },
    settle: 1000,
    prefs: { tome_sidebar: 'open' },
    element: 'aside',
  },
  {
    name: 'users-create-modal',
    path: '/users',
    viewport: { width: 1600, height: 1200, deviceScaleFactor: 2 },
    settle: 800,
    after: async (page) => {
      await page.locator('button:has-text("New User")').first().click().catch(() => {})
      await page.waitForTimeout(500)
      await maskModalBackdrop(page)
    },
    element: 'div.max-w-md:has(h2:has-text("New User"))',
  },
  {
    name: 'series-arcs-modal',
    path: '/?tab=series&series_detail=Berserk',
    viewport: { width: 1600, height: 1400, deviceScaleFactor: 2 },
    settle: 1500,
    after: async (page) => {
      await page.locator('button[title="Manage series"]').first().click().catch(() => {})
      await page.waitForTimeout(600)
      await page.locator('div.max-w-3xl button:has-text("Arcs")').first().click().catch(() => {})
      await page.waitForTimeout(500)
      await maskModalBackdrop(page)
    },
    element: 'div.max-w-3xl:has(h2:has-text("Manage Series"))',
  },
  {
    name: 'reader-epub',
    path: () => `/reader/${bookIds.frankenstein ?? 1}`,
    viewport: { width: 1400, height: 1000, deviceScaleFactor: 2 },
    settle: 2500,
    syncReaderTheme: true,
    after: async (page) => {
      await page.waitForSelector('foliate-view', { timeout: 8000 }).catch(() => {})
      await page.waitForTimeout(2000)
      // Click forward a few pages so the shot has real prose, not the cover.
      const vp = page.viewportSize() || { width: 1400, height: 1000 }
      for (let i = 0; i < 6; i++) {
        await page.mouse.click(vp.width * 0.85, vp.height * 0.5)
        await page.waitForTimeout(300)
      }
    },
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

// When capturing a modal as a tight element shot, paint the dark fixed-inset
// backdrop with the exact docs page background so the modal's rounded corner
// cutouts blend invisibly into the docs site instead of revealing the showcase
// page content (or dark fill) underneath.
async function maskModalBackdrop(page) {
  await page.evaluate(() => {
    // Match website/src/styles/global.css --bg values (docs site bg per theme).
    const themeBg = {
      light: 'oklch(0.99 0 0)',
      dark:  'oklch(0.13 0 0)',
      amber: 'oklch(0.97 0.025 80)',
    }
    const html = document.documentElement
    const theme = html.classList.contains('theme-amber') ? 'amber'
                : html.classList.contains('dark') ? 'dark'
                : 'light'
    const bg = themeBg[theme]
    document.querySelectorAll('.fixed.inset-0').forEach(el => {
      el.style.background = bg
      el.style.backdropFilter = 'none'
      el.style.webkitBackdropFilter = 'none'
    })
    document.querySelectorAll('.absolute.inset-0').forEach(el => {
      if (el.className.includes('bg-black')) {
        el.style.background = bg
        el.style.backdropFilter = 'none'
        el.style.webkitBackdropFilter = 'none'
      }
    })
  })
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
  // Element-bounded screenshot: crops exactly to the target element's bounding
  // box, ideal for capturing a single section or modal without manual clip math.
  if (shot.element) {
    const locator = page.locator(shot.element).first()
    await locator.waitFor({ timeout: 6000 }).catch(() => {})
    await locator.screenshot({ path: file })
    await context.close()
    return file
  }
  let clip = shot.clip
  if (!clip && shot.autoCrop) {
    // Tight bounding-box crop. Walks the DOM, skipping elements that span
    // ≥95% of the viewport (full-width wrappers like .min-h-screen) and
    // anything fixed/sticky/hidden. The remaining centered/inner elements
    // give us the actual content bounds — both horizontal and vertical.
    const pad = shot.autoCropPad ?? 24
    const measured = await page.evaluate((pad) => {
      let minLeft = Infinity, maxRight = 0, maxBottom = 0
      const walk = (el) => {
        const style = window.getComputedStyle(el)
        if (style.position === 'fixed' || style.position === 'sticky') return
        if (style.display === 'none' || style.visibility === 'hidden') return
        const rect = el.getBoundingClientRect()
        const isWideContainer = rect.width >= window.innerWidth * 0.95
        if (!isWideContainer && rect.width > 0 && rect.height > 0) {
          if (rect.left < minLeft) minLeft = rect.left
          if (rect.right > maxRight) maxRight = rect.right
          if (rect.bottom > maxBottom) maxBottom = rect.bottom
        }
        for (const child of el.children) walk(child)
      }
      walk(document.body)
      if (!Number.isFinite(minLeft) || maxBottom === 0) return null
      // Symmetric crop: mirror around viewport center using the larger reach.
      // Avoids the docs page looking like the content is glued to one side.
      const center = window.innerWidth / 2
      const halfWidth = Math.max(center - minLeft, maxRight - center)
      const left = Math.max(0, Math.floor(center - halfWidth - pad))
      const right = Math.min(window.innerWidth, Math.ceil(center + halfWidth + pad))
      return {
        x: left,
        y: 0,
        width: right - left,
        height: Math.ceil(maxBottom + pad),
      }
    }, pad)
    if (measured) clip = measured
  }
  await page.screenshot({ path: file, fullPage: false, ...(clip ? { clip } : {}) })
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
