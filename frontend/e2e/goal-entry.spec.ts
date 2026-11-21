import { test, expect, type Page } from '@playwright/test'
import { seed, login, apiToken, apiGet, GUEST } from './helpers'
import { goalsAnswer } from './goals-fixtures'
import { boxes, moved, forgetStats, gate, GOALS_RE, GOALS_LIST_RE } from './stats-helpers'

// The ways into a goal outside Stats: Home (a first goal, and one more beside the
// row), and "Finish by…" in a series' and a book's header actions, which opens the
// small dialog for that title. Seed `goal-entry`: the `stats` reader without goals,
// plus "Long Road" (three volumes, the first in progress) and "Read Saga" (all read).

type Goal = { id: number; kind: string; target: number; target_series?: string | null; target_book_id?: number | null; due_date?: string }
const dialog = (page: Page) => page.getByRole('dialog').first()
const isGoalPost = (r: { url(): string; request(): { method(): string } }) => /\/api\/goals(\?|$)/.test(r.url()) && r.request().method() === 'POST'
const homeCards = (page: Page) => page.locator('[data-box="home-goals"] [role="listitem"]')

async function apiGoals(page: Page): Promise<Goal[]> {
  const token = await apiToken(page.request)
  return (await apiGet<{ goals: Goal[] }>(page.request, token, '/api/goals')).body.goals
}

async function bookId(page: Page, title: string): Promise<number> {
  const token = await apiToken(page.request)
  const rows = (await apiGet<{ id: number; title: string }[]>(page.request, token, '/api/books?limit=500')).body
  return rows.find((b) => b.title === title)!.id
}

async function settled(page: Page) {
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
}

async function goalsTab(page: Page) {
  await page.evaluate(() => localStorage.setItem('tome_stats_view', JSON.stringify({ tab: 'goals', days: 30, board: null })))
  await page.goto('/stats')
  await expect(page.locator('[data-box="goals-now"]')).toHaveAttribute('aria-busy', 'false')
  return page.locator('[data-box="goals-now"] [role="listitem"]')
}

/** The small dialog: the title, the day, what it takes; no kinds, no search. */
async function expectSmallDialog(page: Page, title: string) {
  await expect(dialog(page)).toContainText('Finish by a date')
  await expect(dialog(page).getByRole('heading')).toHaveText(title)
  await expect(dialog(page).getByRole('radiogroup', { name: 'Goal' })).toHaveCount(0)
  await expect(dialog(page).getByRole('radio')).toHaveCount(0)
  await expect(dialog(page).getByRole('button', { name: 'Book or series' })).toHaveCount(0)
  await expect(page.getByRole('combobox')).toHaveCount(0)
  await expect(dialog(page).getByRole('button', { name: 'End of the year' })).toBeVisible()
  await expect(dialog(page)).toContainText(/a day for the \d+ days to/)
}

test('Home without goals: a first goal from the line under the figures, on Home; it shows in the row and on Stats, after a reload too', async ({ page }) => {
  seed('goal-entry')
  await login(page)
  await settled(page)
  await expect(page.locator('[data-box="home-goals"]')).toHaveCount(0)
  const start = page.getByRole('button', { name: 'Set a reading goal' })
  await expect(start).toBeVisible()
  // Keyboard: Enter opens it, Escape closes it, focus is back on the line.
  await start.focus()
  await page.keyboard.press('Enter')
  await expect(dialog(page)).toContainText('New goal')
  await expect(page).toHaveURL(/\/$/)
  await expect(dialog(page).locator(':focus')).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(start).toBeFocused()

  await start.click()
  await dialog(page).getByRole('radio', { name: /^Books a month/ }).click()
  await dialog(page).locator('#goal-target').fill('3')
  const created = page.waitForResponse(isGoalPost)
  await dialog(page).getByRole('button', { name: 'Set goal' }).click()
  expect((await created).status()).toBe(201)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(homeCards(page)).toHaveCount(1)
  await expect(homeCards(page)).toContainText('Books in')
  await expect(start).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'New goal' })).toBeVisible()
  await page.reload()
  await settled(page)
  await expect(homeCards(page)).toHaveCount(1)
  expect((await apiGoals(page)).map((g) => [g.kind, g.target])).toEqual([['books_per_month', 3]])
  await expect(await goalsTab(page)).toHaveCount(1)
})

test('Home with goals: "New goal" and "See all" on one line under the row; a new goal joins the row in place', async ({ page }) => {
  seed('goals')
  await page.setViewportSize({ width: 1440, height: 900 })
  await login(page)
  await settled(page)
  const more = page.locator('[data-box="home-goals-more"]')
  const add = more.getByRole('button', { name: 'New goal' })
  const all = more.getByRole('link', { name: 'See all 6 goals' })
  await expect(add).toBeVisible()
  await expect(all).toBeVisible()
  const [a, b] = [await add.boundingBox(), await all.boundingBox()]
  expect(Math.abs(a!.y - b!.y)).toBeLessThanOrEqual(1)
  expect(b!.x).toBeGreaterThan(a!.x)
  // Recently added's own See all is on its title's line, not right under the goals' links.
  const recentAll = page.locator('[data-box="home-recent"]').getByRole('button', { name: 'See all' })
  expect(Math.abs((await recentAll.boundingBox())!.x - a!.x)).toBeGreaterThan(200)

  const before = await boxes(page)
  await add.click()
  await expect(dialog(page)).toContainText('New goal')
  await dialog(page).getByRole('radio', { name: /^Pages a day/ }).click()
  await dialog(page).locator('#goal-target').fill('25')
  const created = page.waitForResponse(isGoalPost)
  await dialog(page).getByRole('button', { name: 'Set goal' }).click()
  expect((await created).status()).toBe(201)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  // Seven goals now: the row keeps four, the one just set among them, and nothing else moved.
  await expect(more.getByRole('link', { name: 'See all 7 goals' })).toBeVisible()
  await expect(homeCards(page)).toHaveCount(4)
  await expect(homeCards(page).filter({ hasText: 'Pages a day' })).toHaveCount(1)
  expect(moved(before, await boxes(page), (k) => !k.startsWith('goal-'))).toEqual([])
  await page.reload()
  await settled(page)
  expect((await apiGoals(page)).some((g) => g.kind === 'pages_per_day' && g.target === 25)).toBe(true)
  await expect(await goalsTab(page)).toHaveCount(7)
})

for (const n of [1, 4] as const) {
  test(`Home with ${n} goal${n > 1 ? 's' : ''}: "New goal" is there, "See all" is not`, async ({ page }) => {
    seed('goal-entry')
    await login(page)
    await page.route(GOALS_LIST_RE, async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      const today = (await (await route.fetch()).json()).today as string
      await route.fulfill({ json: goalsAnswer(n, today) })
    })
    await page.goto('/')
    await settled(page)
    await expect(homeCards(page)).toHaveCount(n)
    await expect(page.locator('[data-box="home-goals-more"]').getByRole('button', { name: 'New goal' })).toBeVisible()
    await expect(page.getByRole('link', { name: /^See all \d+ goals/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Set a reading goal' })).toHaveCount(0)
  })
}

for (const vp of [{ name: 'desktop', width: 1280, height: 800 }, { name: 'phone', width: 390, height: 844 }]) {
  test(`Home without goals, ${vp.name}: the line comes with the page and nothing moves after, first visit and second`, async ({ page }) => {
    seed('goal-entry')
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await login(page)
    await page.waitForLoadState('networkidle')
    for (const visit of ['first', 'second'] as const) {
      if (visit === 'first') await forgetStats(page)
      const goals = await gate(page, GOALS_RE)
      if (visit === 'first') await page.goto('/')
      else await page.reload()
      await expect(page.locator('[aria-busy="true"]').first()).toBeVisible()
      await expect(page.locator('[data-box="home-goal-start"]')).toHaveCount(0)
      goals.release()
      await expect(page.locator('[data-box="home-goal-start"]')).toBeVisible()
      const first = await boxes(page)
      expect(Object.keys(first)).toEqual(expect.arrayContaining(['home-goal-start', 'home-recent']))
      await page.waitForLoadState('networkidle')
      expect(moved(first, await boxes(page))).toEqual([])
      await page.unroute(GOALS_RE)
    }
  })
}

test('Series page: "Finish by…" opens the small dialog for that series; the goal is set, shows on Home and Stats, and the pill then opens it', async ({ page }) => {
  seed('goal-entry')
  await login(page)
  await page.goto('/?tab=series&series_detail=Long%20Road')
  const pill = page.getByRole('button', { name: 'Finish by…' })
  await expect(pill).toBeVisible()
  await pill.click()
  await expectSmallDialog(page, 'Long Road')
  await dialog(page).getByRole('button', { name: 'End of the year' }).click()
  const created = page.waitForResponse(isGoalPost)
  await dialog(page).getByRole('button', { name: 'Set goal' }).click()
  expect((await created).status()).toBe(201)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText('Goal set. It shows on Home and in Stats.')).toBeVisible()
  const year = new Date().getFullYear()
  const goals = await apiGoals(page)
  expect(goals.map((g) => [g.kind, g.target_series, g.due_date])).toEqual([['finish_by', 'Long Road', `${year}-12-31`]])
  // The pill now names the goal's day and opens it to edit.
  const set = page.getByRole('button', { name: 'Finish by Dec 31' })
  await expect(set).toBeVisible()
  await page.reload()
  await expect(set).toBeVisible()
  await set.click()
  await expect(dialog(page)).toContainText('Edit goal')
  await expect(dialog(page)).toContainText('Long Road')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(set).toBeFocused()
  await page.goto('/')
  await settled(page)
  await expect(homeCards(page).filter({ hasText: 'Long Road' })).toHaveCount(1)
  await expect((await goalsTab(page)).filter({ hasText: 'Long Road' })).toHaveCount(1)
})

test('Book page: "Finish by…" sets a goal for that book; a read book or series offers none', async ({ page }) => {
  seed('goal-entry')
  await login(page)
  const id = await bookId(page, 'Long Road 2')
  await page.goto(`/books/${id}`)
  const pill = page.getByRole('button', { name: 'Finish by…' })
  await expect(pill).toBeVisible()
  await pill.click()
  await expectSmallDialog(page, 'Long Road 2')
  const created = page.waitForResponse(isGoalPost)
  await dialog(page).getByRole('button', { name: 'Set goal' }).click()
  expect((await created).status()).toBe(201)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /^Finish by \w+ \d+$/ })).toBeVisible()
  const goals = await apiGoals(page)
  expect(goals.map((g) => [g.kind, g.target_book_id])).toEqual([['finish_by', id]])
  await page.reload()
  await expect(page.getByRole('button', { name: /^Finish by \w+ \d+$/ })).toBeVisible()
  await page.goto('/')
  await settled(page)
  await expect(homeCards(page).filter({ hasText: 'Long Road 2' })).toHaveCount(1)

  // Read already: nothing to finish.
  await page.goto(`/books/${await bookId(page, 'Finished Novel 1')}`)
  await expect(page.getByRole('heading', { name: 'Finished Novel 1' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible()
  await page.waitForLoadState('networkidle')
  await expect(page.getByRole('button', { name: /^Finish by/ })).toHaveCount(0)
  await page.goto('/?tab=series&series_detail=Read%20Saga')
  await expect(page.getByRole('heading', { name: 'Read Saga' })).toBeVisible()
  await page.waitForLoadState('networkidle')
  await expect(page.getByRole('button', { name: /^Finish by/ })).toHaveCount(0)
})

test('A guest has goals too: the Home line and "Finish by…" on a book and a series', async ({ page }) => {
  seed('goal-entry')
  await login(page, GUEST)
  await settled(page)
  await expect(page.getByRole('button', { name: 'Set a reading goal' })).toBeVisible()
  await page.goto('/?tab=series&series_detail=Long%20Road')
  await expect(page.getByRole('button', { name: 'Finish by…' })).toBeVisible()
  await page.goto(`/books/${await bookId(page, 'Long Road 3')}`)
  const pill = page.getByRole('button', { name: 'Finish by…' })
  await expect(pill).toBeVisible()
  await pill.click()
  await expectSmallDialog(page, 'Long Road 3')
  const created = page.waitForResponse(isGoalPost)
  await dialog(page).getByRole('button', { name: 'Set goal' }).click()
  expect((await created).status()).toBe(201)
})

for (const vp of [{ name: 'desktop', width: 1280, height: 800 }, { name: 'phone', width: 390, height: 844 }]) {
  test(`Series page, ${vp.name}: the pill takes its place unseen until the goals are in; Share and Manage never move`, async ({ page }) => {
    seed('goal-entry')
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await login(page)
    const token = await apiToken(page.request)
    const year = new Date().getFullYear()
    expect((await page.request.post('/api/goals', { headers: { Authorization: `Bearer ${token}` }, data: { kind: 'finish_by', series: 'Long Road', due_date: `${year}-12-31` } })).status()).toBe(201)
    const goals = await gate(page, GOALS_LIST_RE)
    await page.goto('/?tab=series&series_detail=Long%20Road')
    const share = page.getByRole('button', { name: 'Share', exact: true })
    const manage = page.getByRole('button', { name: 'Manage', exact: true })
    await expect(manage).toBeVisible()
    await expect(page.getByRole('button', { name: /^Finish by/ })).toHaveCount(0)
    const before = [await share.boundingBox(), await manage.boundingBox()]
    goals.release()
    await expect(page.getByRole('button', { name: 'Finish by Dec 31' })).toBeVisible()
    expect([await share.boundingBox(), await manage.boundingBox()]).toEqual(before)
    await page.unroute(GOALS_LIST_RE)
  })
}
