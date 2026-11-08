// Landing page and docs index shots.
export const scenes = [
  { name: 'home', route: '/' },
  { name: 'dashboard', route: '/?tab=books' },
  { name: 'series', route: '/?tab=series' },
  { name: 'mobile-home', route: '/', viewport: 'phone' },
  { name: 'mobile-stats', route: '/stats', viewport: 'phone', settle: 2000 },
  { name: 'mobile-series', route: '/?tab=series', viewport: 'phone' },
  { name: 'mobile-sidebar', route: '/', viewport: 'phone', setup: async ({ page }) => { await page.locator('header button').first().click(); await page.waitForTimeout(700) } },
]
