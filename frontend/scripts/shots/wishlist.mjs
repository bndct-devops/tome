// Wishlist shots (member page, add dialog, Following strip, Admin > Wishlist).
import { openAdminSection } from './admin.mjs'
import { cropBox } from './settings.mjs'

const cover = (id) => `/api/books/${id}/cover?w=160`

// Showcase series as Hardcover-style series hits (used by the series search mocks below).
const SERIES_HITS = [
  { source: 'hardcover', source_id: '35135', name: 'Dungeon Crawler Carl', author: 'Matt Dinniman', total: 7, slug: 'dungeon-crawler-carl', cover_url: null },
  { source: 'hardcover', source_id: '41270', name: 'Cradle', author: 'Will Wight', total: 12, slug: 'cradle', cover_url: null },
]

// MOCK: release detection is off on the copy (the follows endpoint 403s), so
// /wishlist/follows answers with the copy's two real whole-series wishes (kind "follow"):
// Berserk (10 of 43 in the library) and Dungeon Crawler Carl (none in the library). The
// runner's tome_follows_disabled flag is removed so the strip renders.
const FOLLOWS = [
  { id: 1, name: 'Berserk', author: 'Kentaro Miura', cover_url: cover(14), source_id: '128731', latest_known_index: 43, latest_known_title: null, latest_release_date: '2022-09-28', owned_max_index: 10 },
  { id: 2, name: 'Dungeon Crawler Carl', author: 'Matt Dinniman', cover_url: null, source_id: '277861', latest_known_index: 7, latest_known_title: null, latest_release_date: '2024-02-13', owned_max_index: null },
]
async function mockFollows(page) {
  await page.addInitScript(() => sessionStorage.removeItem('tome_follows_disabled'))
  await page.route('**/api/wishlist/follows', r => r.fulfill({ json: FOLLOWS }))
}

export const scenes = [
  {
    // MOCK: the copy holds a wish, so GET /wishlist answers [] for the empty state.
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
      await page.locator('[role="dialog"] input').first().fill('Carl')
      await page.waitForTimeout(1200)
    },
    clip: async ({ page }) => page.locator('[role="dialog"]'),
  },
  {
    // Real data: the single-book wish (The Hitchhiker's Guide to the Galaxy). The two
    // whole-series wishes are follows, shown in the Following strip (mocked, see FOLLOWS).
    name: 'wishlist', route: '/wishlist',
    // The Hitchhiker's wish exists for the admin fulfil shot (its book is in the library); the member
    // list is served without it here (pass-through mock, count and rows agree).
    before: async ({ page }) => {
      await mockFollows(page)
      await page.route(u => /\/api\/wishlist(\?|$)/.test(u.pathname + u.search), async route => {
        if (route.request().method() !== 'GET') return route.continue()
        const res = await route.fetch(); let body = await res.json()
        const drop = w => /hitchhiker/i.test(w?.title ?? '')
        if (Array.isArray(body)) body = body.filter(w => !drop(w))
        else if (body && Array.isArray(body.items)) body = { ...body, items: body.items.filter(w => !drop(w)), total: Math.max(0, (body.total ?? body.items.length) - 1) }
        await route.fulfill({ response: res, json: body })
      })
    },
  },
  {
    // MOCK: see FOLLOWS; /wishlist/search-series returns a Frieren hit for the typed search.
    name: 'wishlist-following', route: '/wishlist',
    before: async ({ page }) => {
      await mockFollows(page)
      await page.route('**/api/wishlist/search-series?*', r => r.fulfill({ json: [
        { source: 'hardcover', source_id: '310112', name: "Frieren: Beyond Journey's End", author: 'Kanehito Yamada', total: 13, slug: 'frieren-beyond-journeys-end', cover_url: cover(18) },
      ] }))
    },
    setup: async ({ page }) => {
      const input = page.getByPlaceholder(/Follow a series/)
      await input.waitFor()
      await page.evaluate(() => {
        const m = document.querySelector('main'); const el = document.querySelector('input[placeholder^="Follow a series"]').closest('section')
        m.scrollTo(0, m.scrollTop + el.getBoundingClientRect().top - m.getBoundingClientRect().top - 80)
      })
      await input.fill('Frieren')
      await page.waitForTimeout(1200)
      // The result dropdown floats over the list; push the list down by the dropdown's
      // height so the first followed series stays fully visible under it.
      await page.evaluate(() => {
        const wrap = document.querySelector('input[placeholder^="Follow a series"]').parentElement
        const drop = wrap.querySelector('div.absolute')
        const list = wrap.nextElementSibling
        if (drop && list) list.style.marginTop = `${drop.getBoundingClientRect().height + 20}px`
        // The wish row above is out of frame, but its cover shadow reaches into the top
        // padding: drop shadows outside this section.
        const sec = wrap.closest('section')
        for (const el of document.querySelectorAll('main *')) if (!sec.contains(el) && getComputedStyle(el).boxShadow !== 'none') el.style.boxShadow = 'none'
      })
      await page.waitForTimeout(200)
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
      // end 10px under the picker's Fulfill button: the row's own bottom padding, clear of
      // the divider and the next row's cover shadow
      const btn = await page.locator('main .divide-y > div').filter({ hasText: "The Hitchhiker's Guide to the Galaxy" }).first().getByRole('button', { name: 'Fulfill', exact: true }).last().boundingBox()
      return cropBox(b, b.y + 14, btn.y + btn.height, 24, 10)
    },
  },
]
