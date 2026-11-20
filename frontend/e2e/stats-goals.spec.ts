import { test, expect, type Page } from '@playwright/test'
import { seed, login, apiToken, apiGet } from './helpers'
import { boxes, moved, forgetStats, gate, STATS_RE, GOALS_RE } from './stats-helpers'

// Reading goals on the Stats Overview against the real backend (seed `goals`:
// year goals scoped to Manga, reached; Comics, ahead of pace; Light Novels,
// behind pace; and a weekly minutes goal for all books). Seed `stats` is the
// same reader without goals.

type Goal = { id: number; kind: string; target: number }
const year = new Date().getFullYear()
const section = (page: Page) => page.locator('section[data-box="goals"]')
const cells = (page: Page) => page.locator('[data-box="goal-cells"] > div')
const cell = (page: Page, label: string | RegExp) => cells(page).filter({ hasText: label })
const isGoalWrite = (method: string) => (r: { url(): string; request(): { method(): string } }) => /\/api\/goals/.test(r.url()) && r.request().method() === method
const goalsFromApi = async (request: Parameters<typeof apiToken>[0]) => {
  const token = await apiToken(request)
  return (await apiGet<{ goals: Goal[] }>(request, token, '/api/goals')).body.goals
}

async function openOverview(page: Page) {
  await page.goto('/stats')
  await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
}

async function expectStates(page: Page) {
  await expect(section(page).getByRole('heading', { name: 'Reading goals' })).toBeVisible()
  await expect(cells(page)).toHaveCount(4)
  const manga = cell(page, 'Manga')
  await expect(manga).toContainText(`${year} challenge · Manga`)
  await expect(manga).toContainText('2/ 2 books')
  await expect(manga).toContainText('Goal reached')
  // Reached: the figure in the success colour, the others in the foreground colour.
  await expect(manga.locator('.font-display')).toHaveClass(/text-success/)
  const comics = cell(page, 'Comics')
  await expect(comics).toContainText(/\d+ ahead of pace/)
  await expect(comics.locator('.font-display')).not.toHaveClass(/text-success/)
  const ln = cell(page, 'Light Novels')
  await expect(ln).toContainText('1/ 40 books')
  await expect(ln).toContainText(/\d+ behind pace/)
  const minutes = cell(page, 'Weekly minutes')
  await expect(minutes).toContainText('185/ 300 min')
  await expect(minutes).toContainText('this week')
  // Without goals the start line at the end of the page is not offered.
  await expect(page.getByRole('button', { name: 'Set a reading goal' })).toHaveCount(0)
}

test('Reading goals: reached, ahead and behind each read as such, after a reload too', async ({ page }) => {
  seed('goals')
  await login(page)
  await openOverview(page)
  await expectStates(page)
  await page.reload()
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  await expectStates(page)
})

test('Reading goals: a new goal, its edit and its delete persist, and an edit moves nothing', async ({ page, request }) => {
  seed('goals')
  await login(page)
  await openOverview(page)
  await expect(cells(page)).toHaveCount(4)

  // New goal from the section heading.
  await section(page).getByRole('button', { name: 'New goal' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Set a reading goal')
  await dialog.getByRole('radio', { name: 'Pages per week' }).click()
  await dialog.getByRole('radio', { name: '150', exact: true }).click()
  const created = page.waitForResponse(isGoalWrite('POST'))
  await dialog.getByRole('button', { name: 'Set goal' }).click()
  expect((await created).status()).toBe(201)
  await expect(dialog).toHaveCount(0)
  await expect(cells(page)).toHaveCount(5)
  await expect(cell(page, 'Weekly pages')).toContainText('/ 150 pages')
  await page.reload()
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  await expect(cells(page)).toHaveCount(5)
  await expect(cell(page, 'Weekly pages')).toContainText('/ 150 pages')

  // Edit its target: the figure changes in place, nothing on the page moves.
  const before = await boxes(page)
  const pages = cell(page, 'Weekly pages')
  await pages.hover()
  await pages.getByRole('button', { name: 'Edit goal' }).click()
  await expect(dialog).toContainText('Edit goal')
  await dialog.locator('#goal-target').fill('500')
  const updated = page.waitForResponse(isGoalWrite('PUT'))
  await dialog.getByRole('button', { name: 'Save' }).click()
  expect((await updated).ok()).toBeTruthy()
  await expect(dialog).toHaveCount(0)
  await expect(cell(page, 'Weekly pages')).toContainText('/ 500 pages')
  expect(moved(before, await boxes(page))).toEqual([])
  await page.reload()
  await expect(cell(page, 'Weekly pages')).toContainText('/ 500 pages')
  expect((await goalsFromApi(request)).find((g) => g.kind === 'pages_per_week')?.target).toBe(500)

  // Delete it: gone, after a reload too, the other four untouched.
  await cell(page, 'Weekly pages').hover()
  const deleted = page.waitForResponse(isGoalWrite('DELETE'))
  await cell(page, 'Weekly pages').getByRole('button', { name: 'Delete this goal' }).click()
  expect((await deleted).status()).toBe(204)
  await expect(cells(page)).toHaveCount(4)
  await expect(cell(page, 'Weekly pages')).toHaveCount(0)
  await page.reload()
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  await expectStates(page)
  expect((await goalsFromApi(request)).map((g) => g.kind).sort()).toEqual(['books_per_year', 'books_per_year', 'books_per_year', 'minutes_per_week'])
})

test('Reading goals: a reader without goals starts one from the end of the page, and deleting it brings the start back', async ({ page, request }) => {
  seed('stats')
  await login(page)
  await openOverview(page)
  await expect(section(page)).toHaveCount(0)
  const start = page.getByRole('button', { name: 'Set a reading goal' })
  await expect(start).toBeVisible()

  await start.click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('radio', { name: 'Books this month' }).click()
  await dialog.locator('#goal-target').fill('3')
  const created = page.waitForResponse(isGoalWrite('POST'))
  await dialog.getByRole('button', { name: 'Set goal' }).click()
  expect((await created).status()).toBe(201)
  await expect(cells(page)).toHaveCount(1)
  // Two novels finished in the last two weeks; whether they fall in this month depends on the date.
  await expect(cell(page, 'Monthly goal')).toContainText(/\d\/ 3 books/)
  await expect(start).toHaveCount(0)

  await page.reload()
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  await expect(cells(page)).toHaveCount(1)
  await expect(start).toHaveCount(0)

  await cell(page, 'Monthly goal').hover()
  const deleted = page.waitForResponse(isGoalWrite('DELETE'))
  await cell(page, 'Monthly goal').getByRole('button', { name: 'Delete this goal' }).click()
  expect((await deleted).status()).toBe(204)
  await expect(section(page)).toHaveCount(0)
  await expect(start).toBeVisible()
  await page.reload()
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  await expect(section(page)).toHaveCount(0)
  await expect(start).toBeVisible()
  expect(await goalsFromApi(request)).toEqual([])
})

for (const vp of [{ name: 'desktop', width: 1280, height: 800 }, { name: 'phone', width: 390, height: 844 }]) {
  test(`Reading goals, ${vp.name}: nothing drawn moves when goals and stats land, first visit and second`, async ({ page }) => {
    seed('goals')
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await login(page)

    for (const visit of ['first', 'second'] as const) {
      if (visit === 'first') await forgetStats(page)
      const goals = await gate(page, GOALS_RE)
      const stats = await gate(page, STATS_RE)
      if (visit === 'first') await page.goto('/stats')
      else await page.reload()
      await expect(page.locator('[aria-busy="true"]').first()).toBeVisible()
      if (visit === 'first') {
        // How many goals there are is not known yet: a quiet placeholder below the hero, no goal cells.
        await expect(page.locator('[data-pending]')).toBeVisible()
        await expect(page.locator('[data-box^="goal-"]')).toHaveCount(0)
      } else {
        // The four remembered from the first visit are reserved.
        await expect(page.locator('[data-box="goal-cells"] > div')).toHaveCount(4)
      }
      const skeleton = await boxes(page)
      expect(Object.keys(skeleton).length).toBeGreaterThan(0)

      // Goals first (their request is the quick one), then the stats.
      goals.release()
      await expect(section(page)).toHaveAttribute('aria-busy', 'false')
      const withGoals = await boxes(page)
      expect(moved(skeleton, withGoals)).toEqual([])
      stats.release()
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
      await expect(page.locator('[data-pending]')).toHaveCount(0)
      const loaded = await boxes(page)
      expect(moved(withGoals, loaded)).toEqual([])
      expect(Object.keys(withGoals).filter((k) => !loaded[k])).toEqual([])
      await expect(cells(page)).toHaveCount(4)
      await page.unroute(GOALS_RE)
      await page.unroute(STATS_RE)
    }
  })
}

test('Reading goals: stats landing before the goals moves nothing on a first visit either', async ({ page }) => {
  seed('goals')
  await login(page)
  await forgetStats(page)
  const goals = await gate(page, GOALS_RE)
  const stats = await gate(page, STATS_RE)
  await page.goto('/stats')
  await expect(page.locator('[data-pending]')).toBeVisible()
  const skeleton = await boxes(page)
  const answered = page.waitForResponse((r) => /\/api\/stats\?/.test(r.url()))
  stats.release()
  await answered
  // The hero fills in; what depends on the goals still waits.
  await expect(page.locator('[data-box="hero"]')).not.toHaveAttribute('aria-busy', 'true')
  await expect(page.locator('[data-pending]')).toBeVisible()
  const withStats = await boxes(page)
  expect(moved(skeleton, withStats)).toEqual([])
  goals.release()
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  await expect(page.locator('[data-pending]')).toHaveCount(0)
  const loaded = await boxes(page)
  expect(moved(withStats, loaded)).toEqual([])
  expect(Object.keys(withStats).filter((k) => !loaded[k])).toEqual([])
})
