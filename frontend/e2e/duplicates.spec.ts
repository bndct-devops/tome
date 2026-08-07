import { test, expect, type Page } from '@playwright/test'
import { seed, login, apiToken, apiGet } from './helpers'

// Queued-decision resolution in the Duplicates tab: pick keeper + action per
// group, one Apply All, one refresh.
test.describe('duplicates apply-all', () => {
  test.beforeEach(async ({ page }) => {
    seed('duplicates')
    await login(page)
    await page.goto('/admin')
    await page.click('button:has-text("Duplicates")')
    await expect(page.getByText('Duplicate Detection')).toBeVisible()
  })

  function group(page: Page, containsText: string) {
    return page.locator('div.rounded-xl', { hasText: containsText }).first()
  }

  test('mixed decisions apply in one pass and survive one refresh', async ({ page, request }) => {
    // Merge the healthy Naruto hash pair (keep the default = first book).
    await group(page, 'Naruto Vol. 1 (copy)').locator('button:has-text("Merge")').click()

    // Delete the One Piece scan ghost, keeping the real Vol. 1.
    const op = group(page, 'one_piece_v01[scan]')
    await op.locator('label', { hasText: 'One Piece Vol. 1' }).first().locator('input[type=radio]').check()
    await op.locator('button:has-text("Delete")').first().click()

    // Dismiss the Bleach ISBN pair — it's an omnibus, not a duplicate.
    await group(page, 'Bleach Vol. 01 Omnibus').locator('button:has-text("Dismiss")').click()

    // The plan bar spells out what will happen.
    await expect(page.getByText('merge 1 group · delete 1 book (removes files) · dismiss 1 group')).toBeVisible()

    await page.click('button:has-text("Apply All (3)")')
    await page.click('button:has-text("Confirm — apply 3 decisions")')
    await expect(page.getByText('Applied 3 groups')).toBeVisible()

    // End state: keeper holds both Naruto files, ghost gone, Bleach dismissed
    // (both books alive but no group shown), overlap groups resolved with them.
    await expect(page.getByText('No duplicates found. Your library looks clean.')).toBeVisible()

    const token = await apiToken(request)
    const { body: books } = await apiGet<{ id: number; title: string }[]>(
      request, token, '/api/books?limit=100')
    const titles = books.map(b => b.title).sort()
    expect(titles).toEqual([
      'Bleach Vol. 01 Omnibus',
      'Bleach Vol. 1',
      'Naruto Vol. 1',
      'One Piece Vol. 1',
    ])
    const keeper = books.find(b => b.title === 'Naruto Vol. 1')!
    const { body: detail } = await apiGet<{ files: unknown[] }>(request, token, `/api/books/${keeper.id}`)
    expect(detail.files).toHaveLength(2)

    // Dismissal persists across a manual refresh too.
    await page.click('button:has-text("Refresh")')
    await expect(page.getByText('No duplicates found. Your library looks clean.')).toBeVisible()
  })

  test('a decision that fails is reported, the rest still apply', async ({ page }) => {
    // The Naruto pair appears twice: as an exact-match group and as a
    // same-series-volume group. Queue "delete others, keep Vol. 1" on the
    // exact-match group and "merge, keep the copy" on the series group: the
    // delete runs first (render order), so the merge's keeper is gone by the
    // time it executes — it must fail loudly, not silently.
    const exact = page
      .locator('div.rounded-xl', { has: page.getByText('Exact Match'), hasText: 'Naruto Vol. 1 (copy)' })
      .first()
    await exact.locator('label', { hasText: /^Naruto Vol\. 1(?! \(copy\))/ }).first().locator('input[type=radio]').check()
    await exact.locator('button:has-text("Delete")').first().click()

    const seriesGroup = page
      .locator('div.rounded-xl', { has: page.getByText('Same Series Volume'), hasText: 'Naruto Vol. 1 (copy)' })
      .first()
    await seriesGroup.locator('label', { hasText: 'Naruto Vol. 1 (copy)' }).first().locator('input[type=radio]').check()
    await seriesGroup.locator('button:has-text("Merge")').click()

    await page.click('button:has-text("Apply All (2)")')
    await page.click('button:has-text("Confirm — apply 2 decisions")')

    await expect(page.getByText('Applied 1 group, 1 failed')).toBeVisible()
    await expect(page.getByText('Naruto Vol. 1 (copy):')).toBeVisible()
  })

  test('decisions can be toggled off and cleared', async ({ page }) => {
    const naruto = group(page, 'Naruto Vol. 1 (copy)')
    await naruto.locator('button:has-text("Merge")').click()
    await expect(page.getByText('Apply All (1)')).toBeVisible()

    // Clicking the queued action again unqueues it.
    await naruto.locator('button:has-text("Merge")').click()
    await expect(page.getByText('Apply All (1)')).not.toBeVisible()

    // Clear drops every queued decision at once.
    await naruto.locator('button:has-text("Merge")').click()
    await group(page, 'Bleach Vol. 01 Omnibus').locator('button:has-text("Dismiss")').click()
    await expect(page.getByText('Apply All (2)')).toBeVisible()
    await page.click('button:has-text("Clear")')
    await expect(page.getByText('Apply All (2)')).not.toBeVisible()
  })
})
