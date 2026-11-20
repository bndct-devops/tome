import { test, expect, type Page } from '@playwright/test'
import { seed, login, apiToken, apiGet } from './helpers'
import { boxes, moved, forgetStats, gate, STATS_RE, GOALS_RE, GOALS_LIST_RE, HISTORY_RE } from './stats-helpers'

// Reading goals against the real backend. Seed `goals`: year goals scoped to Manga
// (reached), Comics (ahead of pace) and Light Novels (behind pace), a weekly minutes
// goal, reading days a week, and finish "Done Saga" (both volumes read: completed).
// Seed `stats` is a reader without goals and with little history (the dialog offers
// presets); `goals-history` reads most days of the last three months (suggestions).

type Goal = { id: number; kind: string; target: number; status: string; period_start: string; due_date?: string }
const year = new Date().getFullYear()
const now = (page: Page) => page.locator('[data-box="goals-now"]')
const cards = (page: Page) => now(page).getByRole('listitem')
const card = (page: Page, name: string | RegExp) => cards(page).filter({ hasText: name })
const detail = (page: Page) => page.locator('[data-box="goal-detail"]')
const dialog = (page: Page) => page.getByRole('dialog').first()
const isGoalWrite = (method: string) => (r: { url(): string; request(): { method(): string } }) => /\/api\/goals/.test(r.url()) && r.request().method() === method
const goalsFromApi = async (request: Parameters<typeof apiToken>[0]) => {
  const token = await apiToken(request)
  return (await apiGet<{ goals: Goal[]; week_start: string }>(request, token, '/api/goals')).body
}

async function openTab(page: Page, tab: 'Overview' | 'Goals') {
  await page.goto('/stats')
  await page.getByRole('tablist', { name: 'Stats pages' }).getByRole('tab', { name: tab }).click()
  await expect(page.getByRole('tab', { name: tab })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
}

async function newGoal(page: Page) {
  await page.getByRole('button', { name: 'New goal' }).first().click()
  await expect(dialog(page)).toContainText('New goal')
}

async function createCount(page: Page, kind: string, target: number) {
  await newGoal(page)
  await dialog(page).getByRole('radio', { name: new RegExp('^' + kind) }).click()
  await dialog(page).locator('#goal-target').fill(String(target))
  const created = page.waitForResponse(isGoalWrite('POST'))
  await dialog(page).getByRole('button', { name: 'Set goal' }).click()
  expect((await created).status()).toBe(201)
  await expect(page.getByRole('dialog')).toHaveCount(0)
}

test('Goals tab: reached, ahead, behind and a completed finish-by read as such, after a reload too', async ({ page }) => {
  seed('goals')
  await login(page)
  await openTab(page, 'Goals')
  for (let pass = 0; pass < 2; pass++) {
    await expect(cards(page)).toHaveCount(6)
    const manga = card(page, 'Manga')
    await expect(manga).toContainText(`Books in ${year} · Manga`)
    await expect(manga).toContainText('Reached')
    await expect(manga.locator('.font-display')).toHaveClass(/text-success/)
    await expect(card(page, 'Comics')).toContainText(/\d+ ahead of pace/)
    await expect(card(page, 'Comics').getByText(/ahead$/)).toHaveClass(/text-success/)
    const ln = card(page, 'Light Novels')
    await expect(ln).toContainText(/\d+ behind pace/)
    await expect(ln.getByText(/behind$/)).toHaveClass(/text-warning/)
    await expect(card(page, 'Minutes a week')).toContainText('/ 300 min')
    await expect(card(page, 'Reading days a week')).toContainText('/ 3 days')
    // Finish by: labelled by its date, the series on the strong line, reached when every volume is read.
    const saga = card(page, 'Done Saga')
    await expect(saga).toContainText(/Finish by \w+ \d+/)
    await expect(saga).toContainText('Reached')
    await expect(saga).toContainText('2/ 2 volumes')
    await page.reload()
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  }
  // The Overview keeps one row of four and the way to the tab.
  await openTab(page, 'Overview')
  const row = page.locator('[data-box="goal-cells"] > div')
  await expect(row).toHaveCount(4)
  await page.getByRole('button', { name: 'All 6 goals' }).click()
  await expect(page.getByRole('tab', { name: 'Goals' })).toHaveAttribute('aria-selected', 'true')
  await expect(cards(page)).toHaveCount(6)
})

test('Goals tab: every kind has its detail', async ({ page }) => {
  seed('goals')
  await login(page)
  await openTab(page, 'Goals')
  const expectations: [string | RegExp, string[]][] = [
    // A year goal without two past years of data: this year's months, not empty years.
    ['Manga', ['This year', 'This year by month', 'Months at pace', 'Best month']],
    ['Minutes a week', ['This week', 'Last 12 weeks', 'Usual week']],
    ['Reading days a week', ['This week', 'Last 12 weeks', 'reached', 'still to come', 'Best run']],
    ['Done Saga', ['Volumes', 'What it takes', 'Time left']],
  ]
  for (const [name, texts] of expectations) {
    await card(page, name).getByRole('button').click()
    await expect(card(page, name).getByRole('button')).toHaveAttribute('aria-pressed', 'true')
    await expect(detail(page)).toHaveAttribute('aria-busy', 'false')
    for (const tx of texts) await expect(detail(page)).toContainText(tx)
  }
  // The selection is remembered in this browser.
  await page.reload()
  await expect(card(page, 'Done Saga').getByRole('button')).toHaveAttribute('aria-pressed', 'true')
})

test('Dialog: every kind created, one edited, one deleted and undone; the end state is on the server', async ({ page, request }) => {
  seed('stats')
  await login(page)
  await openTab(page, 'Goals')
  await expect(page.getByRole('heading', { name: 'Set your first reading goal' })).toBeVisible()
  const counts: [string, number][] = [
    ['Books a year', 24], ['Books a month', 2], ['Minutes a day', 20], ['Minutes a week', 150],
    ['Pages a day', 30], ['Pages a week', 400], ['Reading days a week', 4],
  ]
  for (const [kind, target] of counts) await createCount(page, kind, target)
  // Finish by a date: a book, by the end of the year.
  await newGoal(page)
  await dialog(page).getByRole('radio', { name: /^Finish by a date/ }).click()
  await dialog(page).getByRole('button', { name: 'Book or series' }).click()
  await page.getByPlaceholder('Search series and books').fill('Twin Format')
  await page.getByRole('option', { name: /Twin Format Novel/ }).click()
  await dialog(page).getByRole('button', { name: 'End of the year' }).click()
  await expect(dialog(page)).toContainText(/min a day|no estimate|Left to read/)
  const created = page.waitForResponse(isGoalWrite('POST'))
  await dialog(page).getByRole('button', { name: 'Set goal' }).click()
  expect((await created).status()).toBe(201)
  await expect(cards(page)).toHaveCount(8)
  // A kind already set is not picked twice (all books, and no book type left free for it).
  await newGoal(page)
  await expect(dialog(page).getByRole('radio', { name: /^Reading days a week/ })).toHaveAttribute('aria-disabled', 'true')
  await expect(dialog(page).getByRole('radio', { name: /^Books a year/ })).toContainText('You have this one')
  await dialog(page).getByRole('button', { name: 'Cancel' }).click()

  // Edit: Pages a week to 500.
  await card(page, 'Pages a week').getByRole('button').click()
  await detail(page).getByRole('button', { name: 'Edit goal' }).click()
  await expect(dialog(page)).toContainText('Edit goal')
  await dialog(page).locator('#goal-target').fill('8')
  await dialog(page).locator('#goal-target').fill('abc')
  await expect(dialog(page).getByText('Enter a whole number of at least 1.')).toBeVisible()
  await expect(dialog(page).getByRole('button', { name: 'Save' })).toBeDisabled()
  await dialog(page).locator('#goal-target').fill('500')
  const updated = page.waitForResponse(isGoalWrite('PUT'))
  await dialog(page).getByRole('button', { name: 'Save' }).click()
  expect((await updated).ok()).toBeTruthy()
  await expect(card(page, 'Pages a week')).toContainText('/ 500 pages')

  // Delete with Undo: gone at once, back after Undo.
  await card(page, 'Minutes a day').getByRole('button').click()
  const deleted = page.waitForResponse(isGoalWrite('DELETE'))
  await detail(page).getByRole('button', { name: 'Delete this goal' }).click()
  expect((await deleted).status()).toBe(204)
  await expect(cards(page)).toHaveCount(7)
  const restored = page.waitForResponse((r) => /\/restore/.test(r.url()))
  await page.getByRole('button', { name: 'Undo' }).click()
  expect((await restored).status()).toBe(200)
  await expect(cards(page)).toHaveCount(8)

  await page.reload()
  await expect(cards(page)).toHaveCount(8)
  const api = await goalsFromApi(request)
  expect(api.goals.map((g) => g.kind).sort()).toEqual(['books_per_month', 'books_per_year', 'days_per_week', 'finish_by', 'minutes_per_day', 'minutes_per_week', 'pages_per_day', 'pages_per_week'])
  expect(api.goals.find((g) => g.kind === 'pages_per_week')?.target).toBe(500)
  expect(api.goals.find((g) => g.kind === 'finish_by')?.due_date).toBe(`${year}-12-31`)
})

test('Dialog: suggested targets from the reader\'s own days, presets without history', async ({ page }) => {
  seed('goals-history')
  await login(page)
  await openTab(page, 'Goals')
  await newGoal(page)
  await dialog(page).getByRole('radio', { name: /^Minutes a day/ }).click()
  await expect(dialog(page)).toContainText(/You've read \d+ min a day on average over the last 90 days/)
  for (const chip of ['Steady', 'Keep it up', 'Stretch']) await expect(dialog(page).getByRole('radio', { name: new RegExp(chip) })).toContainText(/reached on \d+ of 90 days/)
  await dialog(page).getByRole('radio', { name: /Keep it up/ }).click()
  await expect(dialog(page)).toContainText(/you'd finish about [\d.]+ books a month/)
  // Books goals come from reading time too.
  await dialog(page).getByRole('radio', { name: /^Books a month/ }).click()
  await expect(dialog(page).getByRole('radio', { name: /Keep it up/ })).toContainText(/your pace on \d+ of 90 days/)
  await dialog(page).getByRole('button', { name: 'Cancel' }).click()

  seed('stats')
  await page.reload()
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  await newGoal(page)
  await dialog(page).getByRole('radio', { name: /^Minutes a day/ }).click()
  await expect(dialog(page)).toContainText('A few to start with')
  for (const v of ['15', '30', '60']) await expect(dialog(page).getByRole('radio', { name: v, exact: true })).toBeVisible()
})

test('Home: every goal as a card; a card opens it on the Goals tab', async ({ page }) => {
  seed('goals')
  await login(page)
  await page.goto('/')
  const home = page.locator('[data-box="home-goals"]')
  await expect(home.getByRole('listitem')).toHaveCount(6)
  await expect(home).toContainText('Light Novels')
  await home.getByRole('listitem').filter({ hasText: 'Reading days a week' }).getByRole('button').click()
  await expect(page).toHaveURL(/\/stats/)
  await expect(page.getByRole('tab', { name: 'Goals' })).toHaveAttribute('aria-selected', 'true')
  await expect(card(page, 'Reading days a week').getByRole('button')).toHaveAttribute('aria-pressed', 'true')
})

test('Week start Sunday: the strip starts on Sunday and a weekly goal\'s week with it', async ({ page, request }) => {
  seed('goals')
  await login(page)
  await openTab(page, 'Goals')
  const letters = async () => {
    await expect(card(page, 'Reading days a week').locator('.k-dot')).toHaveCount(7)
    return card(page, 'Reading days a week').locator('.k-dot > span:last-child').allTextContents()
  }
  expect((await letters())[0]).toBe('M')
  await page.goto('/settings/language')
  const put = page.waitForResponse((r) => /\/api\/auth\/me$/.test(r.url()) && r.request().method() === 'PUT')
  await page.getByRole('radiogroup', { name: 'Week starts on' }).getByRole('radio', { name: 'Sunday' }).click()
  expect((await put).ok()).toBeTruthy()
  await expect(page.getByRole('radio', { name: 'Sunday' })).toHaveAttribute('aria-checked', 'true')
  await openTab(page, 'Goals')
  expect((await letters())[0]).toBe('S')
  const api = await goalsFromApi(request)
  expect(api.week_start).toBe('sunday')
  const weekly = api.goals.find((g) => g.kind === 'minutes_per_week')!
  expect(new Date(weekly.period_start + 'T12:00:00Z').getUTCDay()).toBe(0)
  await card(page, 'Minutes a week').getByRole('button').click()
  await expect(detail(page)).toHaveAttribute('aria-busy', 'false')
  await expect(detail(page).locator('svg[aria-label="This week by day"] text').first()).toHaveText('S')
})

// ── Nothing drawn moves ──────────────────────────────────────────────────────

const VIEWPORTS = [{ name: 'desktop', width: 1280, height: 800 }, { name: 'phone', width: 390, height: 844 }]

for (const vp of VIEWPORTS) {
  test(`Goals tab, ${vp.name}: nothing drawn moves when the goals and the detail land, first visit and second`, async ({ page }) => {
    seed('goals')
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await login(page)
    // Home (where login lands) remembers the goal count when its goals answer: let it land before forgetting.
    await page.waitForLoadState('networkidle')
    for (const visit of ['first', 'second'] as const) {
      if (visit === 'first') await forgetStats(page)
      const list = await gate(page, GOALS_LIST_RE)
      const hist = await gate(page, HISTORY_RE)
      await page.evaluate(() => localStorage.setItem('tome_stats_view', JSON.stringify({ tab: 'goals', days: 30, board: null })))
      if (visit === 'first') await page.goto('/stats')
      else await page.reload()
      if (visit === 'first') {
        await expect(page.locator('[data-pending]')).toBeVisible()
        await expect(page.locator('[data-box^="goal-"]')).toHaveCount(0)
      } else {
        // The cards and the selected goal's detail are reserved at their remembered heights.
        await expect(page.locator('[data-box="goals-now"]')).toHaveAttribute('aria-busy', 'true')
        await expect(page.locator('[data-box="goal-detail"]')).toBeVisible()
      }
      const skeleton = await boxes(page)
      list.release()
      await expect(page.locator('[data-box="goals-now"]')).toHaveAttribute('aria-busy', 'false')
      const withCards = await boxes(page)
      expect(moved(skeleton, withCards)).toEqual([])
      hist.release()
      await expect(page.locator('[data-box="goal-detail"]')).toHaveAttribute('aria-busy', 'false')
      const loaded = await boxes(page)
      expect(moved(withCards, loaded)).toEqual([])
      expect(Object.keys(withCards).filter((k) => !loaded[k])).toEqual([])
      if (visit === 'second') expect(Object.keys(skeleton).filter((k) => !loaded[k])).toEqual([])
      await page.unroute(GOALS_LIST_RE)
      await page.unroute(HISTORY_RE)
    }
  })

  test(`Overview, ${vp.name}: nothing drawn moves when goals and stats land, first visit and second`, async ({ page }) => {
    seed('goals')
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await login(page)
    // Home (where login lands) remembers the goal count when its goals answer: let it land before forgetting.
    await page.waitForLoadState('networkidle')
    for (const visit of ['first', 'second'] as const) {
      if (visit === 'first') await forgetStats(page)
      await page.evaluate(() => localStorage.setItem('tome_stats_view', JSON.stringify({ tab: 'overview', days: 30, board: null })))
      const goals = await gate(page, GOALS_RE)
      const stats = await gate(page, STATS_RE)
      if (visit === 'first') await page.goto('/stats')
      else await page.reload()
      await expect(page.locator('[aria-busy="true"]').first()).toBeVisible()
      if (visit === 'first') {
        await expect(page.locator('[data-pending]')).toBeVisible()
        await expect(page.locator('[data-box^="goal-"]')).toHaveCount(0)
      } else {
        await expect(page.locator('[data-box="goal-cells"] > div')).toHaveCount(4)
      }
      const skeleton = await boxes(page)
      goals.release()
      await expect(page.locator('section[data-box="goals"]')).toHaveAttribute('aria-busy', 'false')
      const withGoals = await boxes(page)
      expect(moved(skeleton, withGoals)).toEqual([])
      stats.release()
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
      await expect(page.locator('[data-pending]')).toHaveCount(0)
      const loaded = await boxes(page)
      expect(moved(withGoals, loaded)).toEqual([])
      expect(Object.keys(withGoals).filter((k) => !loaded[k])).toEqual([])
      await page.unroute(GOALS_RE)
      await page.unroute(STATS_RE)
    }
  })

  test(`Home, ${vp.name}: the goal cards come with the page and nothing moves after, first visit and second`, async ({ page }) => {
    seed('goals')
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await login(page)
    // Home (where login lands) remembers the goal count when its goals answer: let it land before forgetting.
    await page.waitForLoadState('networkidle')
    for (const visit of ['first', 'second'] as const) {
      if (visit === 'first') await forgetStats(page)
      const goals = await gate(page, GOALS_RE)
      if (visit === 'first') await page.goto('/')
      else await page.reload()
      // Home waits for its requests (the goals among them) and draws in one paint.
      await expect(page.locator('[aria-busy="true"]').first()).toBeVisible()
      await expect(page.locator('[data-box="home-goals"]')).toHaveCount(0)
      goals.release()
      await expect(page.locator('[data-box="home-goals"] [role="listitem"]')).toHaveCount(6)
      const first = await boxes(page)
      expect(Object.keys(first)).toContain('home-recent')
      await page.waitForLoadState('networkidle')
      const later = await boxes(page)
      expect(moved(first, later)).toEqual([])
      await page.unroute(GOALS_RE)
    }
  })
}

test('Overview: a reader without goals starts one from the end of the page', async ({ page, request }) => {
  seed('stats')
  await login(page)
  await openTab(page, 'Overview')
  await expect(page.locator('section[data-box="goals"]')).toHaveCount(0)
  const start = page.getByRole('button', { name: 'Set a reading goal' })
  await expect(start).toBeVisible()
  await start.click()
  await dialog(page).getByRole('radio', { name: /^Books a month/ }).click()
  await dialog(page).locator('#goal-target').fill('3')
  const created = page.waitForResponse(isGoalWrite('POST'))
  await dialog(page).getByRole('button', { name: 'Set goal' }).click()
  expect((await created).status()).toBe(201)
  await expect(page.locator('[data-box="goal-cells"] > div')).toHaveCount(1)
  await expect(start).toHaveCount(0)
  expect((await goalsFromApi(request)).goals.map((g) => g.kind)).toEqual(['books_per_month'])
})

test('Dialog: Escape closes an open picker first, the dialog only on the next Escape', async ({ page }) => {
  seed('goals')
  await login(page)
  await openTab(page, 'Goals')
  await newGoal(page)
  await dialog(page).getByRole('radio', { name: /^Finish by a date/ }).click()
  await dialog(page).getByRole('button', { name: 'Book or series' }).click()
  await expect(page.getByRole('listbox', { name: 'Book or series' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('listbox', { name: 'Book or series' })).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: /New goal/ })).toBeVisible()
  await dialog(page).getByRole('button', { name: 'Pick a day' }).click()
  await expect(page.getByRole('grid')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('grid')).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: /New goal/ })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
})
