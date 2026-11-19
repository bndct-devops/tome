import { test, expect, type Page } from '@playwright/test'
import { seed, login } from './helpers'

// A browser's first visit to Stats (nothing remembered in localStorage): while the
// answers are on their way the page draws a skeleton, and when they land nothing
// that was drawn moves or goes. Every stats request is held until the skeleton has been
// measured, then let through.

type Box = { x: number; y: number }

const boxes = (page: Page) => page.evaluate(() => {
  const out: Record<string, { x: number; y: number }> = {}
  const sc = document.querySelector('main')?.scrollTop ?? 0
  for (const el of document.querySelectorAll('[data-box]')) {
    const r = el.getBoundingClientRect()
    out[el.getAttribute('data-box')!] = { x: Math.round(r.x), y: Math.round(r.y + window.scrollY + sc) }
  }
  return out
})

function moved(before: Record<string, Box>, after: Record<string, Box>) {
  return Object.keys(before)
    .filter((k) => after[k] && (Math.abs(after[k].x - before[k].x) > 1 || Math.abs(after[k].y - before[k].y) > 1))
    .map((k) => `${k}: ${before[k].x},${before[k].y} -> ${after[k].x},${after[k].y}`)
}

for (const tab of ['Library', 'Sessions']) {
  test(`Stats ${tab}: nothing drawn moves when the data lands on a first visit`, async ({ page }) => {
    seed('many', 40)
    await login(page)
    await page.evaluate(() => {
      for (const k of Object.keys(localStorage)) if (k.startsWith('tome_stats_')) localStorage.removeItem(k)
    })

    let release: () => void = () => {}
    const held = new Promise<void>((r) => { release = r })
    await page.route(/\/api\/(stats(?!\/dashboard)|goals)/, async (route) => { await held; await route.continue() })

    await page.goto('/stats')
    await page.getByRole('tablist', { name: 'Stats pages' }).getByRole('tab', { name: tab }).click()
    await expect(page.locator('[aria-busy="true"]').first()).toBeVisible()
    await page.waitForTimeout(300)
    const skeleton = await boxes(page)
    expect(Object.keys(skeleton).length).toBeGreaterThan(0)

    release()
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
    await page.waitForTimeout(300)
    const loaded = await boxes(page)
    expect(moved(skeleton, loaded)).toEqual([])
    // Nor does any of it go, except session rows a reader without sessions does not have.
    expect(Object.keys(skeleton).filter((k) => !loaded[k] && !/^s(row|day)-/.test(k))).toEqual([])
  })
}
