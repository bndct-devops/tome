// Stats page shots (docs/stats.astro + landing).
//
// The Stats dashboard persists boards, the active board and the range on the server
// (GET/PUT /api/stats/dashboard), and clicking a tab or range pill writes back. To keep
// scenes deterministic and leave the shared showcase state alone, every scene mocks that
// one endpoint: GET returns the default boards with the board + range this scene wants,
// PUT is swallowed. All stats data itself is real.
//
// The word-count tiles (Words read, Reading speed, Book length, Re-reads) are gallery-only,
// on no default board, so the mock adds a "Words" board holding them.

const DEFAULT_TABS = ['overview', 'habits', 'library', 'taste', 'timeline']

const WORDS_TAB = {
  id: 'words',
  label: 'Words',
  tiles: ['words-read', 'true-wpm', 'book-length', 'rereads'].map((id) => ({
    id, defId: id, config: { chartType: 'bar', days: 0, ...(id === 'book-length' ? {} : { autoFit: true }) },
  })),
  layout: [
    { i: 'words-read', x: 0, y: 0, w: 6, h: 2, minW: 3, minH: 1 },
    { i: 'true-wpm', x: 6, y: 0, w: 6, h: 3, minW: 3, minH: 2 },
    { i: 'book-length', x: 0, y: 3, w: 6, h: 3, minW: 3, minH: 2 },
    { i: 'rereads', x: 6, y: 3, w: 4, h: 3, minW: 3, minH: 2 },
  ],
}

// Mock /api/stats/dashboard: default boards (from the server copy, defaults only) + Words.
// `grow` = { tileId: rows }: a taller tile (a user resize) so a list tile shows every row.
const board = (activeTabId, days, grow = {}) => async ({ page, api }) => {
  const words = activeTabId === 'words'
  const res = await api('/stats/dashboard')
  const tabs = (res?.data?.tabs ?? []).filter((t) => DEFAULT_TABS.includes(t.id))
    .map((t) => ({ ...t, layout: t.layout.map((l) => (grow[l.i] ? { ...l, h: grow[l.i] } : l)) }))
  const data = tabs.length === DEFAULT_TABS.length
    ? { tabs: words ? [...tabs, WORDS_TAB] : tabs, activeTabId, pad: 'lot', days, savedAt: Date.now() + 1e9 }
    : null // server copy unusable: let the page build its defaults (Words board then missing)
  await page.route('**/api/stats/dashboard', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: { data } }) : route.fulfill({ json: { data: null } }))
}

const tile = (page, title) => page.locator('.react-grid-item', { has: page.getByRole('heading', { name: title, exact: true }) }).first()

// Scroll <main> so the tile sits near the top of the viewport (clips are viewport-bound).
const scrollTo = (title) => async ({ page }) => {
  const t = tile(page, title)
  await t.waitFor()
  await t.evaluate((el) => {
    const main = document.querySelector('main')
    const top = el.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop
    main.scrollTo(0, Math.max(0, top - 80))
  })
  await page.waitForTimeout(700)
}

// Tiles sit 16px apart (grid gap). A pad of 7 keeps the crop inside the gap on every side,
// so no neighbour's hairline or edge leaks in.
const PAD = 7

// Clip to the tile's content: a tile taller than its content (a short list) ends at its
// last painted descendant instead of a block of empty space.
const contentClip = (title) => async ({ page }) => {
  const t = tile(page, title)
  const b = await t.boundingBox()
  // bottom of the last text or image (layout wrappers stretch to the tile's height)
  const bottom = await t.evaluate((el) => {
    const rects = [...el.querySelectorAll('img, svg')].map((n) => n.getBoundingClientRect())
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
    for (let n = w.nextNode(); n; n = w.nextNode()) if (n.nodeValue.trim()) { const r = document.createRange(); r.selectNodeContents(n); rects.push(r.getBoundingClientRect()) }
    return Math.max(...rects.filter((r) => r.width > 0 && r.height > 0).map((r) => r.bottom))
  })
  return { ...b, height: Math.min(b.height, bottom - b.y + 8) }
}

const tileScene = (name, activeTabId, days, title, extra = {}, grow) => ({
  name, route: '/stats', settle: 1800,
  before: board(activeTabId, days, grow),
  setup: scrollTo(title),
  clip: async ({ page }) => tile(page, title),
  pad: PAD,
  ...extra,
})

export const scenes = [
  // Landing: Overview board from the header down to the activity grid (ends just below it).
  { name: 'stats-overview', route: '/stats', settle: 2000, before: board('overview', 30), fullPage: true, maxHeight: 1225 },

  // Edit, then Add tile: the widget gallery.
  {
    name: 'stats-add-tile', route: '/stats', settle: 1800, before: board('overview', 30),
    setup: async ({ page }) => {
      await page.getByRole('button', { name: 'Edit', exact: true }).last().click()
      await page.waitForTimeout(400)
      await page.getByRole('button', { name: 'Add tile' }).click()
      await page.waitForTimeout(1200)
    },
    clip: async ({ page }) => page.locator('[role="dialog"]').last(),
  },

  // The six KPI tiles of the Overview's first row, as one strip.
  {
    name: 'stats-totals', route: '/stats', settle: 1800, before: board('overview', 30), pad: PAD,
    clip: async ({ page }) => {
      const boxes = []
      for (const t of ['Reading time', 'Sessions', 'Finished', 'Streak', 'Pages turned', 'Completion']) boxes.push(await tile(page, t).boundingBox())
      const x = Math.min(...boxes.map((b) => b.x)), y = Math.min(...boxes.map((b) => b.y))
      return { x, y, width: Math.max(...boxes.map((b) => b.x + b.width)) - x, height: Math.max(...boxes.map((b) => b.y + b.height)) - y }
    },
  },

  // Overview tiles
  tileScene('stats-currently-reading', 'overview', 30, 'Currently reading'),
  tileScene('stats-time-per-day', 'overview', 30, 'Reading time per day'),
  tileScene('stats-top-books', 'overview', 365, 'Top books by reading time'),
  tileScene('stats-activity-grid', 'overview', 30, 'Reading activity, last 365 days'),

  // Habits tiles
  tileScene('stats-heatmap', 'habits', 0, 'Reading intensity by hour and day'),
  tileScene('stats-session-timeline', 'habits', 7, 'Session timeline', {
    // the taller tile leaves blank space under the footer line: crop at the footer
    clip: async ({ page }) => {
      const b = await tile(page, 'Session timeline').boundingBox()
      const f = await tile(page, 'Session timeline').getByText(/active days? in this window/).boundingBox()
      return { ...b, height: f.y + f.height - b.y }
    },
  }, { 'session-timeline': 4 }),
  tileScene('stats-pace', 'habits', 90, 'Reading pace'),
  tileScene('stats-monthly-comparison', 'habits', 0, 'Reading hours and books finished, last 12 months'),

  // Library tiles
  tileScene('stats-series-completion', 'library', 0, 'Series completion'),
  tileScene('stats-author-affinity', 'library', 0, 'Top authors by reading time'),
  tileScene('stats-completion-by-type', 'library', 0, 'Finish rate per book category'),
  tileScene('stats-category-breakdown', 'library', 0, 'Category breakdown'),
  tileScene('stats-library-growth', 'library', 0, 'Cumulative books added, last 24 months'),

  // Word-count tiles (gallery-only; on the mocked Words board)
  tileScene('stats-words-read', 'words', 0, 'Words read'),
  tileScene('stats-reading-speed', 'words', 0, 'Reading speed'),
  tileScene('stats-book-length', 'words', 0, 'Book length'),
  tileScene('stats-rereads', 'words', 0, 'Re-reads', { clip: contentClip('Re-reads') }),
]
