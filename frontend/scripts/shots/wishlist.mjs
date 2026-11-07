// Wishlist shots (member page, add dialog, Following strip, Admin > Wishlist).
import { openAdminSection } from './admin.mjs'
import { cropBox } from './settings.mjs'

const cover = (id) => `/api/books/${id}/cover?w=160`

// Showcase series as Hardcover-style series hits (used by the series search mocks below).
const SERIES_HITS = [
  { source: 'hardcover', source_id: '35135', name: 'The Good Guys', author: 'Eric Ugland', total: 16, slug: 'the-good-guys', cover_url: cover(31) },
  { source: 'hardcover', source_id: '41270', name: 'The Bad Guys', author: 'Eric Ugland', total: 11, slug: 'the-bad-guys', cover_url: cover(34) },
]

export const scenes = [
  {
    // MOCK: the copy holds five wishes, so GET /wishlist answers [] for the empty state.
    name: 'wishlist-empty', route: '/wishlist',
    before: async ({ page }) => {
      await page.route(/\/api\/wishlist(\?.*)?$/, r => r.request().method() === 'GET' ? r.fulfill({ json: [] }) : r.continue())
    },
  },
  {
    // MOCK: series search needs a Hardcover token the copy does not have, so
    // /wishlist/series-search-available answers true and /wishlist/search-series returns
    // the showcase's two Eric Ugland series.
    name: 'wishlist-add-series', route: '/wishlist',
    before: async ({ page }) => {
      await page.route('**/api/wishlist/series-search-available', r => r.fulfill({ json: { available: true } }))
      await page.route('**/api/wishlist/search-series?*', r => r.fulfill({ json: SERIES_HITS }))
    },
    setup: async ({ page }) => {
      await page.getByRole('button', { name: 'Wish', exact: true }).click()
      await page.waitForTimeout(700)
      await page.locator('[role="dialog"]').getByRole('button', { name: 'Series', exact: true }).click()
      await page.locator('[role="dialog"] input').first().fill('Ugland')
      await page.waitForTimeout(1200)
    },
    clip: async ({ page }) => page.locator('[role="dialog"]'),
  },
  {
    // Real data: a whole-series wish (The Good Guys, 16 of 16) and a single-book wish.
    name: 'wishlist', route: '/wishlist',
  },
  {
    // MOCK: release detection is off on the copy (the follows endpoint 403s), so
    // /wishlist/follows returns three followed showcase series and /wishlist/search-series
    // returns a Berserk hit for the typed search. The runner's tome_follows_disabled flag is
    // removed so the strip renders.
    name: 'wishlist-following', route: '/wishlist',
    before: async ({ page }) => {
      await page.addInitScript(() => sessionStorage.removeItem('tome_follows_disabled'))
      await page.route('**/api/wishlist/follows', r => r.fulfill({ json: [
        { id: 901, name: "Frieren: Beyond Journey's End", author: 'Kanehito Yamada', cover_url: cover(18), source_id: '310112', latest_known_index: 13, latest_known_title: null, latest_release_date: '2026-10-14', owned_max_index: 3 },
        { id: 902, name: 'Vinland Saga', author: 'Makoto Yukimura', cover_url: cover(15), source_id: '199431', latest_known_index: 14, latest_known_title: null, latest_release_date: '2025-09-02', owned_max_index: 3 },
        { id: 903, name: 'The Bad Guys', author: 'Eric Ugland', cover_url: cover(34), source_id: '41270', latest_known_index: 11, latest_known_title: null, latest_release_date: '2026-06-20', owned_max_index: 11 },
      ] }))
      await page.route('**/api/wishlist/search-series?*', r => r.fulfill({ json: [
        { source: 'hardcover', source_id: '128731', name: 'Berserk', author: 'Kentaro Miura', total: 42, slug: 'berserk', cover_url: cover(5) },
      ] }))
    },
    setup: async ({ page }) => {
      const input = page.getByPlaceholder(/Follow a series/)
      await input.waitFor()
      await page.evaluate(() => {
        const m = document.querySelector('main'); const el = document.querySelector('input[placeholder^="Follow a series"]').closest('section')
        m.scrollTo(0, m.scrollTop + el.getBoundingClientRect().top - m.getBoundingClientRect().top - 80)
      })
      await input.fill('Berserk')
      await page.waitForTimeout(1200)
    },
    clip: async ({ page }) => {
      const b = await page.locator('section').filter({ has: page.getByPlaceholder(/Follow a series/) }).boundingBox()
      return cropBox(b, b.y, b.y + b.height, 32, 32)
    },
  },
  {
    name: 'wishlist-admin', route: '/admin',
    setup: openAdminSection('wishlist'),
  },
  {
    // The single-book wish (The Hitchhiker's Guide to the Galaxy) has a real suggested match.
    name: 'wishlist-admin-fulfill', route: '/admin',
    setup: async (ctx) => {
      const { page } = ctx
      await openAdminSection('wishlist')(ctx)
      const row = page.locator('main .divide-y > div').filter({ hasText: "The Hitchhiker's Guide to the Galaxy" }).first()
      await row.getByRole('button', { name: 'Fulfill' }).click()
      await page.waitForTimeout(900)
    },
    // The row with its inline picker, without the neighbouring rows.
    clip: async ({ page }) => {
      const b = await page.locator('main .divide-y > div').filter({ hasText: "The Hitchhiker's Guide to the Galaxy" }).first().boundingBox()
      return cropBox(b, b.y + 14, b.y + b.height - 16, 24, 24)
    },
  },
]
