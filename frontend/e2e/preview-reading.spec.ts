import { test, expect } from '@playwright/test'
import { seed, login, apiToken, apiGet } from './helpers'

// Preview (#289): the reader opened with ?preview=1 must not write anything
// back. Every write the web reader makes is PUT /books/{id}/status, so the
// test counts those and checks the stored status afterwards.
test.describe('preview reading', () => {
  test('a normal open marks the book reading, a preview leaves it untouched', async ({ page, request }) => {
    seed('many', 2)
    await login(page)
    const token = await apiToken(request)
    const { body: books } = await apiGet<{ id: number; title: string }[]>(request, token, '/api/books?limit=10')
    const [normal, preview] = books

    const puts: string[] = []
    await page.route(/\/api\/books\/\d+\/status$/, route => {
      if (route.request().method() === 'PUT') puts.push(route.request().url())
      return route.continue()
    })

    // Control: the normal reader writes "reading" as soon as it opens.
    await page.goto(`/reader/${normal.id}`)
    await expect.poll(() => puts.length, { timeout: 10_000 }).toBeGreaterThan(0)
    const after = await apiGet<{ status: string }>(request, token, `/api/books/${normal.id}/status`)
    expect(after.body.status).toBe('reading')

    // Preview: same reader, nothing written.
    puts.length = 0
    await page.goto(`/reader/${preview.id}?preview=1`)
    await page.waitForTimeout(4000)
    expect(puts).toHaveLength(0)
    const kept = await apiGet<{ status: string; progress_pct: number | null }>(request, token, `/api/books/${preview.id}/status`)
    expect(kept.body.status).toBe('unread')
    expect(kept.body.progress_pct).toBeNull()
  })

  test('the Read button menu opens the preview', async ({ page, request }) => {
    seed('many', 1)
    await login(page)
    const token = await apiToken(request)
    const { body: books } = await apiGet<{ id: number }[]>(request, token, '/api/books?limit=1')

    await page.goto(`/books/${books[0].id}`)
    await expect(page.getByRole('button', { name: 'Read', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'More ways to read' }).click()
    await expect(page.getByText('Does not update progress or stats')).toBeVisible()
    await page.getByText('Preview', { exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/reader/${books[0].id}\\?preview=1$`))
  })
})
