import { test, expect, type Locator, type Page } from '@playwright/test'
import { seed, login, apiToken, apiGet } from './helpers'
import { LONG_SERIES, goalSet, goalsAnswer } from './goals-fixtures'
import { homePick } from '../src/lib/goalsModel'
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
  for (const chip of ['Steady', 'Keep it up', 'Stretch']) await expect(dialog(page).getByRole('radio', { name: new RegExp(chip) })).toContainText(/on \d+ of 90 days/)
  await dialog(page).getByRole('radio', { name: /Keep it up/ }).click()
  await expect(dialog(page)).toContainText(/you'd finish about [\d.]+ books a month/)
  // Books goals come from reading time too.
  await dialog(page).getByRole('radio', { name: /^Books a month/ }).click()
  await expect(dialog(page).getByRole('radio', { name: /Keep it up/ })).toContainText(/on \d+ of 90 days/)
  // Weekly goals count weeks.
  await dialog(page).getByRole('radio', { name: /^Minutes a week/ }).click()
  await expect(dialog(page).getByRole('radio', { name: /Keep it up/ })).toContainText(/in \d+ of 12 weeks/)
  // No chip text is cut, at this width and a phone's: the note wraps and the chip grows.
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    for (const kind of ['Books a month', 'Minutes a day', 'Pages a week']) {
      await dialog(page).getByRole('radio', { name: new RegExp('^' + kind) }).click()
      const group = dialog(page).getByRole('radiogroup', { name: 'Suggested targets' })
      // Up to three, each number once (this reader's pages a week come out the same three times).
      await expect(group.getByRole('radio').first()).toBeVisible()
      const values = await group.getByRole('radio').evaluateAll((els) => els.map((e) => e.querySelector('span')?.textContent))
      expect(new Set(values).size, `${kind}: ${values}`).toBe(values.length)
      // (Text only: the chosen chip's gliding indicator is an aria-hidden span.)
      const cut = await group.evaluate((g) => [...g.querySelectorAll('[role="radio"] span:not([aria-hidden])')]
        .filter((el) => el.scrollWidth > el.clientWidth + 0.5 || getComputedStyle(el).textOverflow === 'ellipsis' ||
          el.getBoundingClientRect().right > el.closest('[role="radio"]')!.getBoundingClientRect().right + 0.5)
        .map((el) => el.textContent))
      expect(cut, `${kind} at ${width}`).toEqual([])
    }
  }
  await page.setViewportSize({ width: 1280, height: 720 })
  await dialog(page).getByRole('button', { name: 'Cancel' }).click()

  seed('stats')
  await page.reload()
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  await newGoal(page)
  await dialog(page).getByRole('radio', { name: /^Minutes a day/ }).click()
  await expect(dialog(page).getByRole('radiogroup', { name: 'Suggested targets' })).toHaveCount(0)
  for (const v of ['15', '30', '60']) await expect(dialog(page).getByRole('radio', { name: v, exact: true })).toBeVisible()
})

test('Home: one row of the goals that need attention; a card opens it on the Goals tab, See all the tab', async ({ page }) => {
  seed('goals')
  await login(page)
  await page.goto('/')
  const home = page.locator('[data-box="home-goals"]')
  // Six goals, four shown in homePick's order. The weekly goals' status depends on the weekday (on Monday to
  // Wednesday reading days is on pace and outranks Comics, which is ahead; from Friday it is reached and Manga
  // takes the last place), so the cards are compared with the rule over the answer the page gets, not fixed names.
  await expect(home.getByRole('listitem')).toHaveCount(4)
  const names = await home.getByRole('listitem').evaluateAll((els) => els.map((e) => e.querySelector('[class*="text-muted-foreground"]')?.textContent))
  const answer = await page.evaluate(async () => {
    const zone = encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone)
    const r = await fetch(`/api/goals?tz_offset=${new Date().getTimezoneOffset()}&tz=${zone}`,
      { headers: { Authorization: `Bearer ${localStorage.getItem('tome_token')}` } })
    return (await r.json()) as { today: string; goals: { id: number; name: string; status: string; period_end: string }[] }
  })
  expect(names, `Home cards on ${answer.today}`).toEqual(homePick(answer.goals).map((g) => g.name))
  expect(names).toContain(`Books in ${year} · Light Novels`)
  expect(names).toContain('Minutes a week')
  const all = home.getByRole('link', { name: 'See all 6 goals' })
  await expect(all).toHaveAttribute('href', '/stats?tab=goals')
  await home.getByRole('listitem').filter({ hasText: 'Minutes a week' }).getByRole('button').click()
  await expect(page).toHaveURL(/\/stats/)
  await expect(page.getByRole('tab', { name: 'Goals' })).toHaveAttribute('aria-selected', 'true')
  await expect(card(page, 'Minutes a week').getByRole('button')).toHaveAttribute('aria-pressed', 'true')
  // The link wins over the view Stats remembers.
  await page.evaluate(() => localStorage.setItem('tome_stats_view', JSON.stringify({ tab: 'overview', days: 30, board: null })))
  await page.goto('/')
  await home.getByRole('link', { name: 'See all 6 goals' }).click()
  await expect(page).toHaveURL(/\/stats/)
  await expect(page.getByRole('tab', { name: 'Goals' })).toHaveAttribute('aria-selected', 'true')
  await expect(cards(page)).toHaveCount(6)
})

/** Answers GET /api/goals with a made-up set (goals-fixtures.ts) around the server's own today. */
/** The server's "today" the last routed goals answer was built around. */
let routedToday = ''

async function routeGoals(page: Page, n: 1 | 3 | 4 | 6 | 8) {
  await page.unroute(GOALS_LIST_RE)
  await page.route(GOALS_LIST_RE, async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    // E2E_GOALS_TODAY=YYYY-MM-DD builds the answer around another day, to try a weekday or a month end.
    const today = process.env.E2E_GOALS_TODAY || ((await (await route.fetch()).json()).today as string)
    routedToday = today
    await route.fulfill({ json: goalsAnswer(n, today) })
  })
}

/**
 * What is wrong with a goal grid's hairlines: every rule between rows runs the grid's full
 * width and sits flush between the rows; every card of a row is as tall as the row, so its
 * column rule (left border) has one length; a column rule is drawn left of every card but
 * a row's first.
 */
async function ruleFaults(list: Locator) {
  return list.evaluate((grid) => {
    const out: string[] = []
    const g = grid.getBoundingClientRect()
    const near = (a: number, b: number) => Math.abs(a - b) <= 0.5
    const cells = [...grid.querySelectorAll(':scope > [role="listitem"]')].map((el) => ({ r: el.getBoundingClientRect(), bl: parseFloat(getComputedStyle(el).borderLeftWidth) }))
    const rows: { top: number; bottom: number; cells: typeof cells }[] = []
    for (const c of cells) {
      const row = rows.find((r) => near(r.top, c.r.top))
      if (row) row.cells.push(c)
      else rows.push({ top: c.r.top, bottom: c.r.bottom, cells: [c] })
    }
    rows.forEach((row, i) => {
      row.cells.forEach((c, j) => {
        if (!near(c.r.height, row.cells[0].r.height)) out.push(`row ${i} card ${j}: ${c.r.height} high, the row's first ${row.cells[0].r.height}`)
        if ((j > 0) !== (c.bl >= 1)) out.push(`row ${i} card ${j}: column rule ${c.bl}`)
      })
    })
    const rules = [...grid.querySelectorAll(':scope > [data-rule="row"]')].map((el) => ({ r: el.getBoundingClientRect(), bt: parseFloat(getComputedStyle(el).borderTopWidth) }))
    if (rules.length !== Math.max(0, rows.length - 1)) out.push(`${rules.length} row rules for ${rows.length} rows`)
    rules.forEach((rule, i) => {
      if (!(rule.bt >= 1)) out.push(`rule ${i}: no line`)
      if (!near(rule.r.left, g.left) || !near(rule.r.right, g.right)) out.push(`rule ${i}: ${rule.r.left}-${rule.r.right}, the grid ${g.left}-${g.right}`)
      const above = rows[i], below = rows[i + 1]
      if (above && !near(rule.r.top, above.top + above.cells[0].r.height)) out.push(`rule ${i}: top ${rule.r.top}, the row above ends ${above.top + above.cells[0].r.height}`)
      if (below && !near(rule.r.bottom, below.top)) out.push(`rule ${i}: bottom ${rule.r.bottom}, the row below starts ${below.top}`)
    })
    return out
  })
}

for (const vp of [{ name: 'desktop', width: 1440, height: 900, cols: 4 }, { name: 'phone', width: 390, height: 844, cols: 2 }]) {
  test(`Goal grids, ${vp.name}: Home caps and orders, and every grid's rules are whole for 1 to 8 goals`, async ({ page }) => {
    seed('goals')
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await login(page)
    for (const n of [1, 3, 4, 6, 8] as const) {
      await routeGoals(page, n)
      await page.goto('/')
      const home = page.locator('[data-box="home-goals"]')
      await expect(home.getByRole('listitem')).toHaveCount(Math.min(4, n))
      expect(await ruleFaults(home.getByRole('list')), `Home, ${n} goals`).toEqual([])
      // Desktop: one row; the phone: two rows of two.
      const tops = new Set(await home.getByRole('listitem').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top))))
      expect(tops.size, `Home rows, ${n} goals`).toBe(Math.ceil(Math.min(4, n) / vp.cols))
      await expect(home.getByRole('link', { name: /^See all/ })).toHaveCount(n > 4 ? 1 : 0)
      await page.evaluate(() => localStorage.setItem('tome_stats_view', JSON.stringify({ tab: 'goals', days: 30, board: null })))
      await page.goto('/stats')
      await expect(now(page)).toHaveAttribute('aria-busy', 'false')
      await expect(cards(page)).toHaveCount(n)
      expect(await ruleFaults(now(page).getByRole('list')), `Goals tab, ${n} goals`).toEqual([])
    }
    // Eight goals, every kind: Home shows the four that homePick picks, in its order (the behind first, by
    // id, then the not yet reached by the nearest end). Which goals are behind depends on the day of the
    // week and of the month the suite runs on, so the cards are compared with the rule, not with fixed names.
    await routeGoals(page, 8)
    await page.goto('/')
    const home = page.locator('[data-box="home-goals"]')
    await expect(home.getByRole('listitem')).toHaveCount(4)
    const labels = await home.getByRole('listitem').evaluateAll((els) => els.map((e) => e.querySelector('[class*="text-muted-foreground"]')?.textContent))
    // A finish-by card's small line is "Finish by <date>"; every other card's is the goal's name.
    const picked = homePick(goalSet(8, routedToday) as { id: number; status: string; period_end: string; kind: string; name: string }[])
    picked.forEach((g, i) => expect(labels[i], `Home card ${i + 1} on ${routedToday}`).toContain(g.kind === 'finish_by' ? 'Finish by' : g.name))
    await expect(home.getByRole('link', { name: 'See all 8 goals' })).toHaveAttribute('href', '/stats?tab=goals')
    // A long series title takes two lines before it is cut. The cards of its row start their status
    // lines level, and each card's sub line follows its own status with no gap (the wrapped title's
    // card simply ends a line lower). On Home and on the Goals tab.
    await routeGoals(page, 6)
    for (const where of ['home', 'tab'] as const) {
      if (where === 'home') await page.goto('/')
      else {
        await page.evaluate(() => localStorage.setItem('tome_stats_view', JSON.stringify({ tab: 'goals', days: 30, board: null })))
        await page.goto('/stats')
        await expect(now(page)).toHaveAttribute('aria-busy', 'false')
      }
      const items = where === 'home' ? home.getByRole('listitem') : cards(page)
      const finishCard = items.filter({ hasText: LONG_SERIES })
      await expect(finishCard).toBeVisible()
      const cut = await finishCard.locator('.line-clamp-2').evaluate((el) => el.scrollHeight > el.clientHeight + 1)
      expect(cut, `${where}: the series title is whole on two lines`).toBe(false)
      const lines = await items.evaluateAll((els) => els.map((e) => {
        const status = e.querySelector('[data-line="status"]')!, sub = e.querySelector('[data-line="sub"]')!
        const st = status.getBoundingClientRect(), sb = sub.getBoundingClientRect()
        const lh = parseFloat(getComputedStyle(status).lineHeight)
        return { top: e.getBoundingClientRect().top, status: st.top, statusLines: Math.round(st.height / lh), gap: sb.top - st.bottom }
      }))
      // The normal spacing: the gap under a one-line status on a card without the long title.
      const normal = lines.find((l) => l.statusLines === 1)!.gap
      expect(normal, `${where}: the normal gap`).toBeLessThan(6)
      expect(lines.some((l) => l.statusLines === 2), `${where}: one status on two lines`).toBe(true)
      const rows = new Map<number, typeof lines>()
      for (const l of lines) rows.set(Math.round(l.top), [...(rows.get(Math.round(l.top)) ?? []), l])
      for (const r of rows.values()) for (const l of r) {
        expect(Math.abs(l.status - r[0].status), `${where}: status tops level`).toBeLessThanOrEqual(0.5)
        expect(Math.abs(l.gap - normal), `${where}: sub line directly under its own status`).toBeLessThanOrEqual(0.5)
      }
    }
  })
}

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
  // The setting sits under Language and region (the language sets dates and numbers; the week start goes with them).
  await expect(page.getByRole('heading', { level: 1, name: 'Language and region' })).toBeVisible()
  const put = page.waitForResponse((r) => /\/api\/auth\/me(\?|$)/.test(r.url()) && r.request().method() === 'PUT')
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
      await expect(page.locator('[data-box="home-goals"] [role="listitem"]')).toHaveCount(4)
      const first = await boxes(page)
      expect(Object.keys(first)).toEqual(expect.arrayContaining(['home-recent', 'home-goals-all']))
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

for (const late of ['series', 'books'] as const) {
  test(`Dialog: the finish-by picker lists nothing until series and books are in (${late} late), series first, each row says what it is`, async ({ page }) => {
    seed('goals')
    await login(page)
    // Held from the first request on (a fresh page: nothing cached yet). The seed's one series
    // (Done Saga) is all read, so it would not be offered: answered here as not read yet, so the
    // list has a series row to come first.
    let release: () => void = () => {}
    const open = new Promise<void>((r) => { release = r })
    await page.route(/\/api\/books\/series(\?|$)/, async (route) => {
      if (late === 'series') await open
      const res = await route.fetch()
      const rows = (await res.json()) as { read_count: number }[]
      await route.fulfill({ response: res, json: rows.map((r) => ({ ...r, read_count: 0 })) })
    })
    if (late === 'books') await page.route(/\/api\/books\?reading_status=reading/, async (route) => { await open; await route.continue() })
    const held = { release: () => release() }
    await openTab(page, 'Goals')
    await newGoal(page)
    await dialog(page).getByRole('radio', { name: /^Finish by a date/ }).click()
    await dialog(page).getByRole('button', { name: 'Book or series' }).click()
    const list = page.getByRole('listbox', { name: 'Book or series' })
    await expect(list).toHaveAttribute('aria-busy', 'true')
    await expect(list).toContainText('Loading series and books…')
    await expect(list.getByRole('option')).toHaveCount(0)
    held.release()
    await expect(list).toHaveAttribute('aria-busy', 'false')
    const rows = await list.getByRole('option').allTextContents()
    expect(rows.length).toBeGreaterThan(0)
    const kinds = rows.map((r) => (/series · (\d+ of \d+ read|has a goal)$/.test(r) ? 's' : /book( · has a goal)?$/.test(r) ? 'b' : '?')).join('')
    expect(kinds, rows.join(' | ')).toMatch(/^s+b+$/)
    // Rows already shown keep their place while a search's matches arrive below them.
    const search = await gate(page, /\/api\/books\?q=/)
    await page.getByPlaceholder('Search series and books').fill('Vol')
    await expect(list).toContainText('Searching books…')
    const before = await list.getByRole('option').allTextContents()
    search.release()
    await expect(list).toHaveAttribute('aria-busy', 'false')
    const after = await list.getByRole('option').allTextContents()
    expect(after.slice(0, before.length)).toEqual(before)
  })
}

test('Finish by, far behind: an hour or more a day reads in hours and minutes', async ({ page }) => {
  seed('goals')
  await login(page)
  await page.route(GOALS_LIST_RE, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    for (const g of body.goals) {
      if (g.kind !== 'finish_by') continue
      g.status = 'behind'
      g.reached = false
      g.finish = { ...g.finish, volumes_left: 2, volumes_read: Math.max(0, g.finish.volumes_total - 2), needed_seconds_per_day: 23280, recent_seconds_per_day: 4380, remaining_seconds: 69840, days_left: 3 }
    }
    await route.fulfill({ response: res, json: body })
  })
  await openTab(page, 'Goals')
  const saga = card(page, /Finish by/)
  await expect(saga).toContainText('needs about 6h 28m a day')
  await expect(saga).not.toContainText('388')
  await saga.getByRole('button').click()
  await expect(detail(page)).toHaveAttribute('aria-busy', 'false')
  await expect(detail(page)).toContainText('6h 28m')
  await expect(detail(page)).toContainText('1h 13m')
  await expect(detail(page)).not.toContainText('388')
})

test('A year goal\'s years: a year without data says so and is never "missed"', async ({ page }) => {
  seed('goals')
  await login(page)
  // The years view (two past years with data) where the last year has none: as a reader with
  // finish dates in older years only.
  await page.route(HISTORY_RE, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    if (body.kind === 'books_per_year' && body.summary) {
      body.periods = body.periods.map((p: { state: string }, i: number, all: unknown[]) => i === all.length - 1 ? { ...p, value: 0, state: 'no_data', reached: null } : { ...p, state: 'met', reached: true })
      body.summary = { ...body.summary, view: 'years', months: undefined, months_summary: undefined, current_run: 0, current_run_includes_current: false }
    }
    await route.fulfill({ response: res, json: body })
  })
  await openTab(page, 'Goals')
  await card(page, 'Comics').getByRole('button').click()
  await expect(detail(page)).toHaveAttribute('aria-busy', 'false')
  await expect(detail(page)).toContainText(`No finish dates in ${year - 1}`)
  await expect(detail(page)).not.toContainText(`${year - 1} missed`)
})

test('Burn-up legend: each mark is drawn exactly as its line in the plot', async ({ page }) => {
  // The year chart falls back to its legend once "today" leaves no room at the end (late in the year).
  test.skip(new Date().getMonth() < 9, 'the year chart only falls back to its legend late in the year')
  seed('goals')
  await login(page)
  await openTab(page, 'Goals')
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await card(page, 'Comics').getByRole('button').click()
    await expect(detail(page)).toHaveAttribute('aria-busy', 'false')
    const chart = detail(page).locator('svg[aria-label="This period so far against the pace"]')
    await expect(chart.locator('[data-legend] [data-mark]').first()).toBeAttached()
    const styles = await chart.evaluate((svg) => {
      const look = (el: Element) => { const cs = getComputedStyle(el); return [cs.stroke, cs.strokeWidth, cs.strokeDasharray, cs.strokeOpacity, cs.strokeLinecap].join(' | ') }
      return [...svg.querySelectorAll('[data-legend] [data-mark]')].map((m) => {
        const mark = m.getAttribute('data-mark')!
        const line = [...svg.querySelectorAll(`[data-mark="${mark}"]`)].find((l) => !l.closest('[data-legend]'))!
        return { mark, legend: look(m), plot: look(line) }
      })
    })
    expect(styles.length).toBeGreaterThan(0)
    for (const s of styles) expect(s.legend, `${s.mark} at ${width}`).toBe(s.plot)
  }
})

// The finish-by picker's card against the New goal dialog under it: off screen, or a line of the
// dialog cut by the card's top or bottom edge (each line is wholly covered or wholly clear). The
// unseen copy that measures the count kinds (data-ruler) is skipped: nothing of it can peek out.
const pickerFaults = (card: Locator) => card.evaluate((c) => {
  const r = c.getBoundingClientRect()
  const out: string[] = []
  if (r.top < 0 || r.bottom > window.innerHeight) out.push(`card off screen ${r.top}-${r.bottom}`)
  const dlg = document.querySelector('[role="dialog"][aria-label="New goal"]')!
  for (const el of dlg.querySelectorAll('p, span, button, label')) {
    if (el.children.length && el.tagName !== 'BUTTON') continue
    if (el.closest('[data-ruler]')) continue
    const b = el.getBoundingClientRect()
    if (!b.height || !b.width) continue
    const overlapX = b.left < r.right && b.right > r.left
    const cut = overlapX && ((b.top < r.bottom && b.bottom > r.bottom) || (b.top < r.top && b.bottom > r.top))
    if (cut) out.push(`${el.tagName} "${(el.textContent || '').trim().slice(0, 30)}" ${Math.round(b.top)}-${Math.round(b.bottom)} vs card ${Math.round(r.top)}-${Math.round(r.bottom)}`)
  }
  return { h: Math.round(r.height), out }
})

/** Resolves once nothing in the New goal dialog moves (its opening, a height step). */
const dialogSettled = (page: Page) => expect.poll(() => page.evaluate(() => {
  const d = document.querySelector('[role="dialog"][aria-label="New goal"]')
  return d ? d.getAnimations({ subtree: true }).filter((a) => a.playState === 'running' && !/color|opacity|shadow/.test((a as CSSTransition).transitionProperty ?? '')).length : -1
})).toBe(0)

test('Finish-by picker while loading: the card keeps the size it will have, nothing peeks out under it', async ({ page }) => {
  seed('goals')
  await login(page)
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
    // A fresh page each time (nothing cached), with the series answer held.
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    let release: () => void = () => {}
    const open = new Promise<void>((r) => { release = r })
    await page.route(/\/api\/books\/series(\?|$)/, async (route) => { await open; await route.continue() })
    await openTab(page, 'Goals')
    await newGoal(page)
    await dialog(page).getByRole('radio', { name: /^Finish by a date/ }).click()
    await dialog(page).getByRole('button', { name: 'Book or series' }).click()
    const list = page.getByRole('listbox', { name: 'Book or series' })
    const card = page.getByRole('dialog', { name: 'Book or series' })
    // The card shows once the dialog has settled from its one height step.
    await expect(card).toBeVisible()
    await expect(list).toHaveAttribute('aria-busy', 'true')
    const loading = await pickerFaults(card)
    expect(loading.out, `loading at ${width}`).toEqual([])
    release()
    await expect(list).toHaveAttribute('aria-busy', 'false')
    const loaded = await pickerFaults(card)
    expect(loaded.out, `loaded at ${width}`).toEqual([])
    expect(loaded.h).toBe(loading.h)
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
  }
})

test('Finish-by picker opened during the height step: the title holds, the card sits on its trigger once the dialog settles', async ({ page }) => {
  seed('goals')
  await login(page)
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
    await openTab(page, 'Goals')
    // The one height step (the panel's move and the area's height) slowed from 200 ms to 1.2 s, so
    // the picker opens well inside it.
    await page.addStyleTag({ content: '[role="dialog"][aria-modal="true"][style*="transition: top"], [role="dialog"][aria-modal="true"] [style*="transition: height"] { transition-duration: 1200ms !important }' })
    await newGoal(page)
    await dialogSettled(page)
    // Every frame from here: the dialog's title, the trigger, and the card whenever it is shown.
    await page.evaluate(() => {
      const w = window as unknown as { __frames: { title: number; bottom: number; trigger: number | null; card: number | null }[]; __stop: boolean }
      w.__frames = []
      w.__stop = false
      const tick = () => {
        const d = document.querySelector('[role="dialog"][aria-label="New goal"]')
        if (!d || w.__stop) return
        const t = d.querySelector('button[aria-label="Book or series"]')
        const c = document.querySelector<HTMLElement>('[role="dialog"][aria-label="Book or series"]')
        const shown = c && getComputedStyle(c).visibility !== 'hidden'
        w.__frames.push({ title: d.querySelector('h2')!.getBoundingClientRect().top, bottom: d.getBoundingClientRect().bottom, trigger: t ? t.getBoundingClientRect().bottom : null, card: shown ? c.getBoundingClientRect().top : null })
        requestAnimationFrame(tick)
      }
      requestAnimationFrame(tick)
    })
    await dialog(page).getByRole('radio', { name: /^Finish by a date/ }).click()
    // At once, mid-step (force: no wait for the trigger to stand still).
    await dialog(page).getByRole('button', { name: 'Book or series' }).click({ force: true })
    // (Located by attribute: a role query skips the card while it is hidden.)
    await expect(page.locator('[role="dialog"][aria-label="Book or series"]')).toBeAttached()
    const card = page.getByRole('dialog', { name: 'Book or series' })
    const midStep = await page.evaluate(() => document.querySelector('[role="dialog"][aria-label="New goal"]')!.getAnimations({ subtree: true }).some((a) => (a as CSSTransition).transitionProperty === 'height'))
    expect(midStep, `the picker opened inside the step at ${width}`).toBe(true)
    await dialogSettled(page)
    await expect(card).toBeVisible()
    const frames = await page.evaluate(() => { const w = window as unknown as { __frames: { title: number; bottom: number; trigger: number | null; card: number | null }[]; __stop: boolean }; w.__stop = true; return w.__frames })
    // Whenever the card shows, it sits 6 px under the trigger (it never shows where the trigger was).
    const off = frames.filter((f) => f.card != null && f.trigger != null && Math.abs(f.card - (f.trigger + 6)) > 1)
    expect(off.map((f) => `card ${Math.round(f.card!)} trigger ${Math.round(f.trigger!)}`), `card on its trigger at ${width}`).toEqual([])
    expect(frames.some((f) => f.card != null), `card shown at ${width}`).toBe(true)
    // The dialog grows at the bottom: its title holds (where the bottom would leave the screen the
    // dialog moves up instead, as far as it has to), and its bottom stays on screen.
    const titles = frames.map((f) => f.title)
    const vh = width === 390 ? 844 : 900
    if (width === 1440) expect(Math.max(...titles) - Math.min(...titles), `title holds at ${width}`).toBeLessThanOrEqual(1)
    else expect(titles.every((t, i) => i === 0 || t <= titles[i - 1] + 1), `title only moves up at ${width}`).toBe(true)
    expect(Math.max(...frames.map((f) => f.bottom)), `bottom on screen at ${width}`).toBeLessThanOrEqual(vh - 16 + 1)
    expect((await pickerFaults(card)).out, `settled at ${width}`).toEqual([])
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
  }
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
