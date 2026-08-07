import fs from 'node:fs'
import path from 'node:path'
import { test, expect } from '@playwright/test'
import { seed, login, apiToken, apiGet, LIBRARY_DIR } from './helpers'

// The issue #165 journey: files deleted from disk outside of Tome left ghost
// records behind. The user finds them marked in Duplicates, cleans them up
// via Library Health, and never loses a real file.
test.describe('library cleanup (issue #165)', () => {
  test.beforeEach(async ({ page }) => {
    seed('orphans')
    await login(page)
  })

  test('dead copies are marked MISSING in the duplicates tab', async ({ page }) => {
    await page.goto('/admin')
    await page.click('button:has-text("Duplicates")')
    await expect(page.getByText('Duplicate Detection')).toBeVisible()
    // Each scan ghost carries the badge in its exact-match group AND in the
    // overlapping same-series-volume group (2 ghosts × 2 groups).
    await expect(page.getByText('MISSING')).toHaveCount(4)
  })

  test('library health lists orphans and removes them without touching real files', async ({ page, request }) => {
    await page.goto('/admin')
    await page.click('button:has-text("Library")')
    await page.click('button:has-text("Scan Library")')

    // Three dead entries: two whole-book ghosts + AoT's stale extra file.
    await expect(page.getByText('Orphaned entries')).toBeVisible()
    await expect(page.getByText('book entry will be removed')).toHaveCount(3)
    await expect(page.getByText('file entry only')).toHaveCount(1)

    // Two-step confirm, then the summary.
    await page.click('button:has-text("Remove Dead Entries")')
    await page.click('button:has-text("Confirm — remove 4 entries")')
    await expect(page.getByText('Dead entries removed')).toBeVisible()
    await expect(page.getByText('3 book entries removed · 1 file entry removed')).toBeVisible()
    await expect(page.getByText('Orphaned entries')).not.toBeVisible()

    // End state via API: ghosts gone, healthy books intact.
    const token = await apiToken(request)
    const { body: books } = await apiGet<{ id: number; title: string }[]>(
      request, token, '/api/books?limit=100')
    const titles = books.map(b => b.title).sort()
    expect(titles).toEqual([
      'Attack on Titan Vol. 1',
      'One Piece Vol. 1',
      'One Piece Vol. 2',
    ])

    // AoT kept exactly its one real file; real files still on disk.
    const aot = books.find(b => b.title === 'Attack on Titan Vol. 1')!
    const { body: detail } = await apiGet<{ files: unknown[] }>(request, token, `/api/books/${aot.id}`)
    expect(detail.files).toHaveLength(1)
    expect(fs.existsSync(path.join(LIBRARY_DIR, 'One Piece', 'One Piece Vol. 1.cbz'))).toBe(true)
    expect(fs.existsSync(path.join(LIBRARY_DIR, 'Attack on Titan', 'Attack on Titan Vol. 1.cbz'))).toBe(true)
  })

  test('remove dead entries refuses when the files are actually alive', async ({ page }) => {
    // A second scan right after cleanup must find nothing to remove — and a
    // library whose files all exist shows no orphan section at all.
    await page.goto('/admin')
    await page.click('button:has-text("Library")')
    await page.click('button:has-text("Scan Library")')
    await page.click('button:has-text("Remove Dead Entries")')
    await page.click('button:has-text("Confirm — remove 4 entries")')
    await expect(page.getByText('Dead entries removed')).toBeVisible()

    await page.click('button:has-text("Scan Library")')
    await expect(page.getByText('Orphaned entries')).not.toBeVisible()
  })
})
