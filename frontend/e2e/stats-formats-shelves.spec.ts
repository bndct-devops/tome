import { test, expect, type Page } from '@playwright/test'
import { seed, login, apiToken, apiGet } from './helpers'

// Seed `stats`: "Twin Format Novel" is one book with an EPUB and a PDF file;
// a library ("Seeded Library", two unstarted novels) and a shelf ("Short reads",
// three unstarted novels by Shelf Author) for the Backlog's scope menu.

const tab = (page: Page, name: string) => page.getByRole('tablist', { name: 'Stats pages' }).getByRole('tab', { name })
// The menu closes on any scroll outside it; Playwright's click scrolls the chip into
// view first, and that scroll's event lands just after the menu opened. Scroll, let
// the event go by (two frames), then click, as a reader who scrolled down does.
async function openMenu(page: Page, chip: ReturnType<Page['locator']>) {
  await chip.scrollIntoViewIfNeeded()
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))
  await chip.click()
}
async function openTab(page: Page, name: string) {
  await page.goto('/stats')
  await tab(page, name).click()
  await expect(tab(page, name)).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
}

test('A book in two formats: its time under each format on Habits, once everywhere else', async ({ page }) => {
  seed('stats')
  await login(page)

  // Habits: the server counts a book's sessions under each of its files, by design
  // (see formatShares); EPUB 26100 s, PDF 7200 s (the twin's own time), CBZ 1200 s.
  await openTab(page, 'Habits')
  const line = page.locator('[data-box="pace-formats"]')
  await expect(line).toHaveText('By format: EPUB 0.7 pg/min, 76 %·PDF 0.7 pg/min, 21 %·CBZ 4.0 pg/min, 3 %')
  // Tips hide on a scroll, so the line is scrolled to before the pointer goes to it.
  await line.scrollIntoViewIfNeeded()
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))
  await line.locator('[data-tip-i="1"]').hover()
  const tip = page.getByRole('tooltip')
  await expect(tip).toContainText('PDF')
  await expect(tip).toContainText('0.7 pages a minute · 21 % of the time')
  await expect(tip).toContainText('80 pages in 2h 0m · 4 sessions')

  // Library: counted once among the books, listed once among the books read.
  await openTab(page, 'Library')
  // A counted figure's value is its screen-reader copy (the painted one counts up).
  await expect(page.locator('[data-box="fig-0"] .sr-only').first()).toHaveText('11')
  await expect(page.locator('[data-box="fig-0"]')).toContainText('books')
  await expect(page.locator('[data-box="fig-2"] .sr-only').first()).toHaveText('3')
  const table = page.getByRole('table', { name: 'All books by reading time' })
  await expect(table.getByRole('row').filter({ hasText: 'Twin Format Novel' })).toHaveCount(1)
  await expect(table.getByRole('row').filter({ hasText: 'Twin Format Novel' })).toContainText('2h')

  // Overview: one card in Reading now.
  await openTab(page, 'Overview')
  await expect(page.locator('section[data-box="now"] [data-box^="now-card-"]').filter({ hasText: 'Twin Format Novel' })).toHaveCount(1)

  // Home: one card on the shelf row beside the hero, and eleven books on the server.
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Almost Done' })).toBeVisible()
  const shelf = page.locator('section').filter({ has: page.getByText('Also reading', { exact: true }) })
  await expect(shelf).toHaveCount(1)
  await expect(shelf.getByRole('link')).toHaveText(['Panel Comic', 'Twin Format Novel'])
  await expect(shelf.getByRole('button', { name: 'Show Twin Format Novel here' })).toHaveCount(1)
  await expect(page.getByText('On the server').locator('..')).toHaveText('Library11booksOn the server')
})

test('Backlog scope menu: Libraries and Shelves groups, a shelf picked is remembered and its figures hold', async ({ page, request }) => {
  seed('stats')
  await login(page)
  await openTab(page, 'Library')
  const backlog = page.locator('section[data-box="backlog"]')
  const chip = backlog.getByRole('button', { name: 'Backlog of' })
  await expect(chip).toHaveText('All unread')
  await expect(backlog).toContainText('6 books')

  await openMenu(page, chip)
  const menu = page.getByRole('menu', { name: 'Backlog of' })
  await expect(menu).toBeVisible()
  await expect(menu.getByRole('menuitemradio')).toHaveText(['Want to read', 'All unread', 'Seeded Library', 'Short reads'])
  await expect(menu.getByRole('menuitemradio', { name: 'All unread' })).toHaveAttribute('aria-checked', 'true')
  await expect(menu.getByRole('group', { name: 'Libraries' })).toHaveText(/Libraries\s*Seeded Library/)
  await expect(menu.getByRole('group', { name: 'Shelves' })).toHaveText(/Shelves\s*Short reads/)

  const shelfAnswer = page.waitForResponse((r) => /\/api\/stats\/backlog\?scope=shelf%3A\d+/.test(r.url()))
  await menu.getByRole('menuitemradio', { name: 'Short reads' }).click()
  expect((await shelfAnswer).ok()).toBeTruthy()
  await expect(menu).toHaveCount(0)
  await expect(chip).toHaveText('Short reads')
  await expect(backlog).toContainText('3 books')
  await expect(backlog.locator('[data-box="backlog-figure"] .sr-only').first()).toHaveText('2')
  await expect(backlog.locator('[data-box="backlog-figure"]')).toContainText('hours')

  // Remembered in this browser: the shelf again after a reload, its figures with it.
  await page.reload()
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  await expect(chip).toHaveText('Short reads')
  await expect(backlog).toContainText('3 books')
  await openMenu(page, chip)
  await expect(menu.getByRole('menuitemradio', { name: 'Short reads' })).toHaveAttribute('aria-checked', 'true')

  // A library from the same menu.
  await menu.getByRole('menuitemradio', { name: 'Seeded Library' }).click()
  await expect(chip).toHaveText('Seeded Library')
  await expect(backlog).toContainText('2 books')

  // Back to the shelf, then the shelf goes (deleted elsewhere): the next visit falls back to All unread.
  await openMenu(page, chip)
  await menu.getByRole('menuitemradio', { name: 'Short reads' }).click()
  await expect(chip).toHaveText('Short reads')
  const token = await apiToken(request)
  const shelves = (await apiGet<{ id: number; name: string }[]>(request, token, '/api/saved-filters')).body
  const id = shelves.find((s) => s.name === 'Short reads')!.id
  const del = await request.delete(`/api/saved-filters/${id}`, { headers: { Authorization: `Bearer ${token}` } })
  expect(del.status()).toBe(204)
  await page.reload()
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  await expect(chip).toHaveText('All unread')
  await expect(backlog).toContainText('6 books')
  await openMenu(page, chip)
  await expect(menu.getByRole('group', { name: 'Shelves' })).toHaveCount(0)
  await expect(menu.getByRole('menuitemradio')).toHaveText(['Want to read', 'All unread', 'Seeded Library'])
})
