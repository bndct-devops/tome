import fs from 'node:fs'
import path from 'node:path'
import { test, expect, type Page } from '@playwright/test'
import { seed, login, apiToken, apiGet, LIBRARY_DIR } from './helpers'

async function openAllBooks(page: Page) {
  await page.click('text=All Books')
  await expect(page.getByText(/of \d+ books|^\d+ books/).first()).toBeVisible()
}

test.describe('dashboard selection and bulk delete', () => {
  test('select all covers the whole filtered set, not just loaded pages', async ({ page }) => {
    seed('many', 134)
    await login(page)
    await openAllBooks(page)

    // Only the first page (60) is loaded…
    await expect(page.getByText('60 of 134 books')).toBeVisible()
    // …but Select selects everything that matches the filter.
    await page.click('button:has-text("Select")')
    await expect(page.getByText('134 selected')).toBeVisible()
  })

  test('bulk delete removes records and files from disk', async ({ page, request }) => {
    seed('many', 10)
    await login(page)
    await openAllBooks(page)

    await page.click('button:has-text("Select")')
    await expect(page.getByText('10 selected')).toBeVisible()
    // Narrow to two books, the way a user would: deselect all, pick two cards.
    await page.click('button:has-text("Deselect all")')
    await page.getByText('Seeded Book 001').click()
    await page.getByText('Seeded Book 002').click()
    await expect(page.getByText('2 selected')).toBeVisible()

    await page.click('button:has-text("Delete")')
    await expect(page.getByText('This permanently removes the selected books')).toBeVisible()
    await page.locator('button', { hasText: /^Delete 2 books$/ }).last().click()
    await expect(page.getByText('Deleted 2 books')).toBeVisible()

    const token = await apiToken(request)
    const { body: books } = await apiGet<{ title: string }[]>(request, token, '/api/books?limit=100')
    expect(books).toHaveLength(8)
    expect(books.map(b => b.title)).not.toContain('Seeded Book 001')
    expect(fs.existsSync(path.join(LIBRARY_DIR, 'Seeded', 'Seeded Book 001.epub'))).toBe(false)
    expect(fs.existsSync(path.join(LIBRARY_DIR, 'Seeded', 'Seeded Book 003.epub'))).toBe(true)
  })

  test('select-all delete beyond the 500 cap chunks into multiple requests', async ({ page, request }) => {
    test.setTimeout(240_000)
    seed('many', 520)
    await login(page)
    await openAllBooks(page)

    await page.click('button:has-text("Select")')
    await expect(page.getByText('520 selected')).toBeVisible()

    const bulkDeleteRequests: number[] = []
    page.on('request', req => {
      if (req.url().includes('/api/books/bulk-delete') && req.method() === 'POST') {
        bulkDeleteRequests.push((req.postDataJSON() as { book_ids: number[] }).book_ids.length)
      }
    })

    await page.click('button:has-text("Delete")')
    // The confirm list only holds loaded books; the rest is summarised.
    await expect(page.getByText(/…and \d+ more not shown/)).toBeVisible()
    await page.locator('button', { hasText: /^Delete 520 books$/ }).last().click()
    await expect(page.getByText('Deleted 520 books')).toBeVisible({ timeout: 210_000 })

    expect(bulkDeleteRequests).toEqual([500, 20])
    const token = await apiToken(request)
    const { body: books } = await apiGet<{ title: string }[]>(request, token, '/api/books?limit=10')
    expect(books).toHaveLength(0)
  })

  test('toggling group series never duplicates books in the grid', async ({ page }) => {
    // Data whose grouped and flat orderings differ (see seed.py scenario_race)
    // — a prerequisite for the leak to surface as visible duplicates.
    seed('race')
    await login(page)
    await openAllBooks(page)

    // The reported race (issue #165 follow-up): a next-page request of the
    // grouped view is still in flight when the user ungroups. The ungroup's
    // reset resolves fast; the stale grouped page resolves later and — before
    // the fix — got appended onto the flat list. The flat view then loads the
    // same books again further down: duplicates in the viewport.
    let slowPages = true
    let pageRequests = 0
    await page.route('**/api/books?*', async route => {
      const url = new URL(route.request().url())
      if (Number(url.searchParams.get('skip') ?? '0') > 0) {
        pageRequests++
        if (slowPages) await new Promise(r => setTimeout(r, 1500))
      }
      await route.continue()
    })

    // Group, then scroll the grid (wheel fires at the pointer, so hover it
    // first) until the grouped view requests its next page…
    await page.click('button:has-text("Group series")')
    await page.waitForTimeout(600)
    await page.getByText('Middle Solo 001').first().hover()
    await page.mouse.wheel(0, 20000)
    await expect.poll(() => pageRequests, { timeout: 5000 }).toBeGreaterThan(0)
    // …and ungroup while that request is still held in flight.
    await page.click('button:has-text("Group series")')
    await page.waitForTimeout(2500) // stale grouped page lands (or is discarded)

    // Load the whole flat view; every book must appear exactly once.
    slowPages = false
    await page.getByText('Alpha Saga Vol 01').first().hover()
    for (let i = 0; i < 10; i++) {
      await page.mouse.wheel(0, 20000)
      await page.waitForTimeout(400)
    }
    const titles = (await page.locator('main p').allTextContents())
      .map(t => t.trim())
      .filter(t => /^(Alpha Saga Vol|Middle Solo|Zeta Saga Vol)/.test(t))
    const dupes = titles.filter((t, i) => titles.indexOf(t) !== i)
    expect([...new Set(dupes)]).toEqual([])
    expect(titles.length).toBe(160)
  })
})
