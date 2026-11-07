// Hardcover page shots.
//
// MOCK: the showcase copy has no valid Hardcover token, so every scene here answers
// /hardcover/status with a linked account ("benedict", see settings.mjs) and rewrites the
// real /hardcover/books list (all "Not synced yet" while unlinked) into a synced state:
// most books matched with a slug, one not matched, one excluded, one not synced yet, so
// every state badge shows in the first two rows.
import { mockHardcoverLinked } from './settings.mjs'

const slugify = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
const NOT_MATCHED = new Set(["Frieren: Beyond Journey's End, Vol. 2"])
const EXCLUDED = new Set(['Second Story Man'])
const PENDING = new Set(['Berserk, Vol. 6'])

async function mockHardcoverBooks(page) {
  await page.route('**/api/hardcover/books', async (route) => {
    const res = await route.fetch()
    const books = await res.json()
    const out = books.map(b => {
      if (NOT_MATCHED.has(b.title)) return { ...b, state: 'unmatched', slug: null, method: null }
      if (EXCLUDED.has(b.title)) return { ...b, state: 'excluded', slug: null, method: null }
      if (PENDING.has(b.title)) return b
      return { ...b, state: 'matched', slug: slugify(b.title), method: b.isbn ? 'isbn' : 'title', synced_at: '2026-11-07T18:20:00' }
    })
    await route.fulfill({ json: out })
  })
}

const linked = async ({ page }) => { await mockHardcoverLinked(page); await mockHardcoverBooks(page) }

export const scenes = [
  {
    name: 'hardcover', route: '/hardcover',
    before: async (ctx) => {
      await ctx.page.addInitScript(() => localStorage.setItem('tome_hardcover_group', 'false'))
      await linked(ctx)
    },
  },
  {
    name: 'hardcover-grouped', route: '/hardcover',
    before: async (ctx) => {
      await ctx.page.addInitScript(() => localStorage.setItem('tome_hardcover_group', 'true'))
      await linked(ctx)
    },
  },
  {
    // MOCK: no book carries edition pages on the copy (no Hardcover link), so GET /books/1
    // (Project Hail Mary, real progress 42%) gets hardcover_pages 476 and a slug added.
    name: 'hardcover-book-pages', route: '/books/1',
    before: async ({ page }) => {
      await page.route(/\/api\/books\/1(\?.*)?$/, async (route) => {
        if (route.request().method() !== 'GET') return route.continue()
        const res = await route.fetch()
        const b = await res.json()
        await route.fulfill({ json: { ...b, hardcover_pages: 476, hardcover_slug: 'project-hail-mary' } })
      })
    },
    // From the title down to the page count line.
    clip: async ({ page }) => {
      const h1 = await page.locator('main h1').first().boundingBox()
      const pages = await page.locator('main').getByText(/^p\. \d+ of \d+$/).first().locator('..').boundingBox()
      const x = h1.x + 20
      const y = h1.y + 16
      return { x, y, width: Math.max(pages.x + pages.width, h1.x + h1.width) - x, height: pages.y + pages.height - y - 32 }
    },
  },
]
