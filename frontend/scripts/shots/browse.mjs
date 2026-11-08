// Series, book page, highlights and calendar shots for the docs.
const BERSERK_1 = 5, PHM = 1, FRANKENSTEIN = 37

const pause = (page, ms = 700) => page.waitForTimeout(ms)
const dialog = (page) => page.locator('[role="dialog"]').last()
const scrollMain = (page, y) => page.evaluate(y => document.querySelector('main').scrollTo(0, y), y)

// Scroll `main` so `locator` sits `top` px below the viewport top.
async function scrollTo(page, locator, top = 120) {
  await locator.first().evaluate((el, top) => {
    const main = document.querySelector('main')
    main.scrollTo(0, main.scrollTop + el.getBoundingClientRect().top - top)
  }, top)
  await pause(page, 400)
}

// Union of several boxes (for crops that span an element plus its popover).
const union = (...boxes) => {
  const b = boxes.filter(Boolean)
  const x = Math.min(...b.map(r => r.x)), y = Math.min(...b.map(r => r.y))
  return { x, y, width: Math.max(...b.map(r => r.x + r.width)) - x, height: Math.max(...b.map(r => r.y + r.height)) - y }
}

// The book page's status/action block: reading-status pills down to the rating
// row, widened to include `extra` (a popover or picker inside it).
async function actionBlock(page, extra) {
  const top = await page.getByRole('button', { name: 'Unread', exact: true }).first().boundingBox()
  const rating = await page.getByText(/You rated this|Rate this book/).first().boundingBox()
  const row = await page.getByRole('button', { name: 'More send options' }).first().locator('xpath=ancestor::div[contains(@class,"flex-wrap")][1]').boundingBox()
  return union(top, rating, row, await extra.boundingBox())
}

// The section element whose heading carries `text`.
const sectionOf = (page, text) => page.locator('section', { has: page.getByText(text, { exact: false }) }).first()

// Small placeholder files named after showcase titles; nothing is uploaded.
const file = (name, kb, mimeType) => ({ name, mimeType, buffer: Buffer.alloc(kb * 1024, 1) })

export const scenes = [
  // ── Series ────────────────────────────────────────────────────────────────
  { name: 'series-detail', route: '/?tab=series&series_detail=Berserk', settle: 1800, fullPage: true, maxHeight: 2000 },
  {
    name: 'series-arcs-modal', route: '/?tab=series&series_detail=Berserk', settle: 1800,
    setup: async ({ page }) => {
      await page.getByRole('button', { name: 'Manage', exact: true }).click(); await pause(page)
      await dialog(page).getByText('Arcs', { exact: true }).click(); await pause(page, 900)
    },
    clip: async ({ page }) => dialog(page),
  },

  // ── Book page ─────────────────────────────────────────────────────────────
  {
    name: 'upload-modal', route: '/',
    setup: async ({ page }) => {
      await page.getByRole('button', { name: 'Upload' }).first().click(); await pause(page)
      await dialog(page).locator('input[type="file"]').setInputFiles([
        file('Frankenstein.epub', 480, 'application/epub+zip'),
        file('Dune.epub', 1200, 'application/epub+zip'),
        file('Watchmen #1.cbz', 2400, 'application/zip'),
      ])
      await pause(page, 600)
      // Per-file type pickers (the first select is "Set all to").
      const selects = dialog(page).locator('select')
      await selects.nth(1).selectOption({ label: 'Books' })
      await selects.nth(2).selectOption({ label: 'Books' })
      await selects.nth(3).selectOption({ label: 'Comics' })
      await pause(page, 400)
    },
    clip: async ({ page }) => dialog(page),
  },
  {
    // 1245 tall: ends just under the Reading section's Activity chart (whole chart, no Progress row).
    name: 'book-detail-edit', route: `/books/${BERSERK_1}`, fullPage: true, maxHeight: 1245,
    setup: async ({ page }) => { await page.getByRole('button', { name: 'Edit', exact: true }).first().click(); await pause(page) },
  },
  {
    name: 'metadata-fetch', route: `/books/${PHM}`,
    // Live results from the backend (Open Library / Google Books), passed through.
    // MOCK (cosmetic): the per-source status is dropped so a rate-limited Google
    // Books does not print its warning line over the results. Only when the network
    // answers with nothing is a fallback built from the showcase book used.
    before: async ({ page }) => {
      await page.route('**/api/books/*/fetch-metadata*', async route => {
        const res = await route.fetch().catch(() => null)
        const data = res && res.ok() ? await res.json() : null
        if (data?.candidates?.length) { delete data.sources; return route.fulfill({ response: res, json: data }) }
        await route.fulfill({ json: { query_used: 'Project Hail Mary Andy Weir', sources: { open_library: 'ok' }, candidates: [
          { source: 'open_library', source_id: '/works/OL21745884W', title: 'Project Hail Mary', author: 'Andy Weir', year: 2021, publisher: 'Ballantine Books', isbn: '9780593135204', language: 'eng', page_count: 496, cover_url: null, tags: ['science fiction'], series: null, series_index: null, description: 'Ryland Grace is the sole survivor on a desperate, last-chance mission.' },
        ] } })
      })
    },
    setup: async ({ page }) => {
      await page.getByRole('button', { name: 'Fetch metadata' }).first().click()
      await page.getByText('Best match').first().waitFor({ timeout: 30000 }).catch(() => {})
      // Let the remote cover thumbnails arrive.
      await dialog(page).locator('img').evaluateAll(imgs => Promise.all(imgs.map(i => i.complete ? null : new Promise(r => { i.onload = i.onerror = r; setTimeout(r, 8000) }))))
      await pause(page, 1200)
    },
    clip: async ({ page }) => dialog(page),
  },
  {
    name: 'send-to-koreader', route: `/books/${BERSERK_1}`, pad: 24,
    setup: async ({ page }) => { await page.getByRole('button', { name: 'More send options' }).first().click(); await pause(page, 500) },
    clip: async ({ page }) => actionBlock(page, page.locator('[role="menu"]').last()),
  },
  {
    name: 'kosync-link', route: `/books/${PHM}`, pad: 24,
    // MOCK: the picker only shows when a KOSync client (stock KOReader, Readest) has
    // pushed a document no Tome book is linked to; the showcase has none. The mock
    // answers kosync-progress with one unlinked Readest document.
    before: async ({ page }) => {
      await page.route('**/api/books/*/kosync-progress', route => route.fulfill({ json: {
        linked: false, percentage: null, device: null, timestamp: null,
        unlinked_documents: [{ document: '4f1c9a0b7e2d4c6a8b3e5f7a9c1d2e4f', percentage: 0.42, device: 'Readest', timestamp: Math.floor(Date.now() / 1000) - 86400 }],
      } }))
    },
    setup: async ({ page }) => { await page.getByRole('button', { name: 'Link KOReader sync' }).click(); await pause(page, 500) },
    // effective top padding 12: the label above the status pills stays out of frame
    clip: async ({ page }) => { const b = await actionBlock(page, page.getByLabel('Synced document')); return { ...b, y: b.y + 12, height: b.height - 12 } },
  },
  {
    name: 'book-intensity', route: `/books/${BERSERK_1}`, pad: 32,
    // MOCK: no showcase book has imported KOReader page-stats, so reading-stats is
    // passed through with an intensity curve added for Berserk Vol. 1.
    before: async ({ page }) => {
      await page.route(`**/api/books/${BERSERK_1}/reading-stats*`, async route => {
        const res = await route.fetch(); const data = await res.json()
        const bins = 80
        const curve = Array.from({ length: bins }, (_, i) => {
          const x = i / bins
          const base = 40 + 25 * Math.sin(x * 9) + 18 * Math.sin(x * 23 + 1)
          const peak = 140 * Math.exp(-((x - 0.62) ** 2) / 0.002) + 90 * Math.exp(-((x - 0.18) ** 2) / 0.001)
          return Math.round(Math.max(8, base + peak))
        })
        data.intensity = { bins, curve, total_seconds: 4 * 3600 + 25 * 60, total_pages: 224, pages_read: 224, pct_read: 100, reread_bins: 2 }
        await route.fulfill({ response: res, json: data })
      })
    },
    setup: async ({ page }) => { await scrollTo(page, sectionOf(page, 'Reading intensity'), 160) },
    clip: async ({ page }) => sectionOf(page, 'Reading intensity'),
  },
  {
    name: 'koreader-highlights', route: `/books/${PHM}`, pad: 32,
    setup: async ({ page }) => { await scrollTo(page, page.getByText('Highlights and notes'), 160) },
    // start at the section title with 16px above it: the Sessions heading sits ~30px higher
    clip: async ({ page }) => { const b = await sectionOf(page, 'Highlights and notes').boundingBox(); return { ...b, y: b.y + 16, height: b.height - 16 } },
  },

  // ── Highlights + Calendar ─────────────────────────────────────────────────
  { name: 'highlights', route: '/highlights' },
  { name: 'calendar', route: '/calendar', settle: 1800 },
  { name: 'calendar-day', route: '/calendar', settle: 1800, pad: 32,
    // The day card itself, not the whole scroll lane it sits in.
    clip: async ({ page }) => page.locator('aside h2').first().locator('xpath=ancestor::div[contains(@class,"rounded")][1]') },
  {
    name: 'mobile-calendar-day', route: '/calendar', viewport: 'phone', settle: 1800,
    setup: async ({ page }) => {
      // Tap today (it always has reading in the showcase).
      await page.locator('button[aria-pressed]').filter({ has: page.locator('span.rounded-full.bg-primary.text-primary-foreground') }).first().click()
      await pause(page, 900)
    },
  },
]
