import { test, expect, type Page } from '@playwright/test'
import { seed, login, apiToken, apiGet } from './helpers'
import { boxes, moved, gate, STATS_RE } from './stats-helpers'

// "Mark as read" on the Stats Overview's Reading now (seed `stats`: Almost Done at
// 99.5 %, Twin Format Novel and Panel Comic in progress, two novels finished in
// the last 30 days). The card goes, the book joins Finished and the finished
// count, nothing else on the page moves, and all of it holds after a reload.

const now = (page: Page) => page.locator('section[data-box="now"]')
const finished = (page: Page) => page.locator('section[data-box="finished"]')

async function expectMarked(page: Page) {
  await expect(now(page)).not.toContainText('Almost Done')
  await expect(now(page)).toContainText('Twin Format Novel')
  await expect(now(page)).toContainText('Panel Comic')
  await expect(page.getByRole('button', { name: 'Mark as read' })).toHaveCount(0)
  await expect(finished(page)).toContainText('3 books in the last 30 days')
  await expect(finished(page).getByRole('link', { name: 'Almost Done' })).toHaveCount(1)
  await expect(page.locator('[data-box="hero-fig-2"] .sr-only').first()).toHaveText('3')
}

for (const vp of [{ name: 'desktop', width: 1280, height: 800 }, { name: 'phone', width: 390, height: 844 }]) {
  test(`Mark as read on the Overview, ${vp.name}: the book moves to Finished, nothing else moves, and it holds`, async ({ page, request }) => {
    seed('stats')
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await login(page)
    await page.goto('/stats')
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)

    await expect(now(page).locator('[data-box^="now-card-"]')).toHaveCount(3)
    const card = now(page).locator('[data-box^="now-card-"]').filter({ hasText: 'Almost Done' })
    await expect(card).toContainText('100%')
    // Only the book as good as finished offers it.
    await expect(page.getByRole('button', { name: 'Mark as read' })).toHaveCount(1)
    await expect(finished(page)).toContainText('2 books in the last 30 days')
    await expect(finished(page).getByRole('link', { name: 'Almost Done' })).toHaveCount(0)
    await expect(page.locator('[data-box="hero-fig-2"] .sr-only').first()).toHaveText('2')
    const before = await boxes(page)

    const put = page.waitForResponse((r) => /\/api\/books\/\d+\/status$/.test(r.url()) && r.request().method() === 'PUT')
    const refetch = page.waitForResponse((r) => /\/api\/stats\?/.test(r.url()))
    await card.getByRole('button', { name: 'Mark as read' }).click()
    const res = await put
    expect(res.ok()).toBeTruthy()
    const id = Number(/\/api\/books\/(\d+)\/status/.exec(res.url())![1])
    await expect(page.getByText('Marked as read: Almost Done')).toBeVisible()
    await refetch
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
    await expectMarked(page)
    // The cards left keep their places' row; every section below Reading now stays put.
    const after = await boxes(page)
    expect(moved(before, after, (k) => !k.startsWith('now-card-'))).toEqual([])

    // Persisted: the server has it read, and the page says so after a reload.
    const token = await apiToken(request)
    expect((await apiGet<{ title: string }>(request, token, `/api/books/${id}`)).body.title).toBe('Almost Done')
    expect((await apiGet<{ status: string }>(request, token, `/api/books/${id}/status`)).body.status).toBe('read')
    await page.reload()
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
    await expectMarked(page)
    await expect(now(page).locator('[data-box^="now-card-"]')).toHaveCount(2)

    // The next visit's skeleton reserves the two cards left: nothing moves when the answer lands.
    const stats = await gate(page, STATS_RE)
    await page.reload()
    await expect(now(page).locator('[data-box^="now-card-"]')).toHaveCount(2)
    await expect(page.locator('[aria-busy="true"]').first()).toBeVisible()
    const skeleton = await boxes(page)
    stats.release()
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
    expect(moved(skeleton, await boxes(page))).toEqual([])
  })
}
