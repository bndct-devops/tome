// Stats page shots (docs/stats.astro + landing).
//
// The page's tab and range are this browser's view state (localStorage
// `tome_stats_view`), so each scene seeds them before navigation; nothing is
// written to the server by looking around. All stats data is real.
//
// The Boards tab only exists when a saved board differs from the shipped
// defaults. The boards scene therefore mocks GET /api/stats/dashboard with one
// board of its own (a default Overview under another name) and swallows PUTs,
// leaving the showcase account's saved boards alone.

const view = (tab, days = 90) => async ({ page }) => {
  await page.addInitScript(([tab, days]) => {
    try { localStorage.setItem('tome_stats_view', JSON.stringify({ tab, days, board: null })) } catch { /* ignore */ }
  }, [tab, days])
}

const page = (name, tab, days, extra = {}) => ({
  name, route: '/stats', settle: 2000, before: view(tab, days), fullPage: true, maxHeight: 2400, ...extra,
})

// One custom board for the Boards scene: the shipped Overview board, renamed.
const OVERVIEW = ['stat-time', 'stat-sessions', 'stat-finished', 'stat-streak', 'stat-pages', 'stat-completion']
const MY_BOARD = {
  id: 'tab--shots',
  label: 'My board',
  tiles: [...OVERVIEW, 'currently-reading', 'daily', 'top-books', 'activity-365'].map((id) => ({
    id, defId: id, config: { chartType: 'bar', days: 0, ...(id === 'currently-reading' || id === 'top-books' ? { autoFit: true } : {}) },
  })),
  layout: [
    ...OVERVIEW.map((i, k) => ({ i, x: k * 2, y: 0, w: 2, h: 1, minW: 2, minH: 1 })),
    { i: 'currently-reading', x: 0, y: 1, w: 12, h: 2, minW: 3, minH: 1 },
    { i: 'daily', x: 0, y: 3, w: 6, h: 3, minW: 3, minH: 2 },
    { i: 'top-books', x: 6, y: 3, w: 6, h: 3, minW: 3, minH: 1 },
    { i: 'activity-365', x: 0, y: 6, w: 12, h: 2, minW: 4, minH: 2 },
  ],
}

export const scenes = [
  // Landing and docs: the Overview from the header through the "Where the time went" /
  // "When you read" row, cut 44 px under it so no part of "The year" heading shows
  // (on the showcase seed that row ends at 1503 and The year starts at 1555).
  page('stats-overview', 'overview', 90, { maxHeight: 1555 }),
  page('stats-habits', 'habits', 90),
  page('stats-library', 'library', 0),
  page('stats-taste', 'taste', 90),
  page('stats-sessions', 'sessions', 90, { maxHeight: 1400 }),
  {
    name: 'stats-boards', route: '/stats', settle: 2000, fullPage: true, maxHeight: 1400,
    before: async (ctx) => {
      await view('boards', 30)(ctx)
      const data = { tabs: [MY_BOARD], activeTabId: MY_BOARD.id, pad: 'lot', days: 30, savedAt: Date.now() + 1e9 }
      // Mocked (see the header): a saved board of its own, without touching the account's.
      await ctx.page.route('**/api/stats/dashboard', (route) =>
        route.request().method() === 'GET' ? route.fulfill({ json: { data } }) : route.fulfill({ json: { ok: true } }))
    },
  },
]
