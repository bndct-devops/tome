import { test, expect, type Page, type Response } from '@playwright/test'
import { seed, login, apiToken, apiGet } from './helpers'

// Settings › Appearance › Show Boards: a reader who never customised a Stats
// board reaches the old drag-and-drop grid as a Boards tab, every default board
// listed. Off again, the tab goes and Stats lands on the Overview.

type Blob = { data: { showBoards?: boolean; tabs?: { id: string; label: string }[] } | null }

const statsTabs = (page: Page) => page.getByRole('tablist', { name: 'Stats pages' }).getByRole('tab')
const boardsSwitch = (page: Page) => page.getByRole('switch', { name: 'Show Boards' })
const isDashboardPut = (r: { url(): string; method(): string }) => r.url().includes('/api/stats/dashboard') && r.method() === 'PUT'
const isDashboardPutResponse = (r: Response) => isDashboardPut(r.request())

async function flip(page: Page, to: boolean) {
  await page.goto('/settings/appearance')
  const sw = boardsSwitch(page)
  await expect(sw).toHaveAttribute('aria-checked', String(!to))
  const put = page.waitForResponse(isDashboardPutResponse)
  await sw.click()
  expect((await put).ok()).toBeTruthy()
  await expect(sw).toHaveAttribute('aria-checked', String(to))
}

async function renameBoard(page: Page, from: string, to: string) {
  const put = page.waitForResponse(isDashboardPutResponse)
  await page.getByRole('button', { name: `Rename ${from}` }).click()
  const input = page.locator('input:focus')
  await input.fill(to)
  await input.press('Enter')
  expect((await put).ok()).toBeTruthy()
}

test('Show Boards opens the default boards to a reader who never customised them', async ({ page, request }) => {
  seed('reset')
  const token = await apiToken(request)
  await login(page)

  // Plain page loads never write the saved boards.
  const puts: string[] = []
  page.on('request', (r) => { if (isDashboardPut(r)) puts.push(r.url()) })

  // Nothing customised, switch off: no Boards tab.
  await page.goto('/stats')
  await expect(statsTabs(page)).toHaveText(['Overview', 'Habits', 'Library', 'Taste', 'Timeline', 'Sessions'])
  await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
  expect(puts).toEqual([])

  // On in Settings: only the flag is written.
  await flip(page, true)
  expect((await apiGet<Blob>(request, token, '/api/stats/dashboard')).body.data).toEqual({ showBoards: true })
  puts.length = 0

  // Back on Stats the tab is there before the server has answered (remembered in this browser).
  let release: () => void = () => {}
  const held = new Promise<void>((r) => { release = r })
  await page.route('**/api/stats/dashboard', async (route) => { await held; await route.continue() })
  await page.goto('/stats')
  await expect(page.getByRole('tab', { name: 'Boards' })).toBeVisible()
  release()

  // Every default board, editable.
  await page.getByRole('tab', { name: 'Boards' }).click()
  const main = page.locator('main')
  for (const name of ['Overview', 'Habits', 'Library', 'Taste', 'Timeline']) {
    await expect(main.getByRole('button', { name, exact: true })).toBeVisible()
  }
  expect(puts).toEqual([])
  await main.getByRole('button', { name: 'Edit', exact: true }).click()
  await renameBoard(page, 'Overview', 'My overview')
  let saved = (await apiGet<Blob>(request, token, '/api/stats/dashboard')).body.data
  expect(saved?.showBoards).toBe(true)
  expect(saved?.tabs?.map((x) => x.label)).toEqual(['My overview', 'Habits', 'Library', 'Taste', 'Timeline'])
  // Back to the default name, so nothing is the reader's own any more.
  await renameBoard(page, 'My overview', 'Overview')
  saved = (await apiGet<Blob>(request, token, '/api/stats/dashboard')).body.data
  expect(saved?.tabs?.map((x) => x.label)).toEqual(['Overview', 'Habits', 'Library', 'Taste', 'Timeline'])
  await main.getByRole('button', { name: 'Done', exact: true }).click()

  // Off again: the flag goes, the boards stay as saved.
  await flip(page, false)
  saved = (await apiGet<Blob>(request, token, '/api/stats/dashboard')).body.data
  expect(saved?.showBoards).toBeUndefined()
  expect(saved?.tabs).toHaveLength(5)

  // Stats remembered the Boards tab; it is gone and the Overview is shown, without an error.
  puts.length = 0
  await page.goto('/stats')
  await expect(statsTabs(page)).toHaveText(['Overview', 'Habits', 'Library', 'Taste', 'Timeline', 'Sessions'])
  await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('Couldn’t load stats.')).toHaveCount(0)
  // After the server's answer too.
  await page.waitForLoadState('networkidle')
  await expect(page.getByRole('tab', { name: 'Boards' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
  expect(puts).toEqual([])
})
