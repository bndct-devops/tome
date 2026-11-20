import type { Page } from '@playwright/test'

// Shared by the Stats specs: where every [data-box] sits, what moved between two
// reads, and gates that hold the Stats requests until a test lets them through.

export type Box = { x: number; y: number }

/** Every [data-box] position in page coordinates, read once no finite animation is running. */
export async function boxes(page: Page): Promise<Record<string, Box>> {
  return page.evaluate(async () => {
    await Promise.all(document.getAnimations()
      .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
      .map((a) => a.finished.catch(() => undefined)))
    await new Promise((r) => requestAnimationFrame(() => r(null)))
    const out: Record<string, { x: number; y: number }> = {}
    const sc = document.querySelector('main')?.scrollTop ?? 0
    for (const el of document.querySelectorAll('[data-box]')) {
      const r = el.getBoundingClientRect()
      out[el.getAttribute('data-box')!] = { x: Math.round(r.x), y: Math.round(r.y + window.scrollY + sc) }
    }
    return out
  })
}

/** Boxes present in both reads whose position changed by more than a pixel. */
export function moved(before: Record<string, Box>, after: Record<string, Box>, only?: (k: string) => boolean) {
  return Object.keys(before)
    .filter((k) => (!only || only(k)) && after[k] && (Math.abs(after[k].x - before[k].x) > 1 || Math.abs(after[k].y - before[k].y) > 1))
    .map((k) => `${k}: ${before[k].x},${before[k].y} -> ${after[k].x},${after[k].y}`)
}

/** Forget what this browser remembered about the Stats pages: the next visit is a first visit. */
export async function forgetStats(page: Page) {
  await page.evaluate(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith('tome_stats_')) localStorage.removeItem(k)
  })
}

/** Holds the requests matching `re` from now on until release() (each later request too, until then). */
export async function gate(page: Page, re: RegExp) {
  let release: () => void = () => {}
  const held = new Promise<void>((r) => { release = r })
  await page.route(re, async (route) => { await held; await route.continue() })
  return { release }
}

export const STATS_RE = /\/api\/stats(?!\/dashboard)/
export const GOALS_RE = /\/api\/goals/
