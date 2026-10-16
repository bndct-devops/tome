import { whenStarted } from './boot'

/** Loads images the page will show further down into the browser cache, a few
 * at a time, at low priority and only when the browser is idle, so a cover is
 * usually there before it is scrolled to. Starts once the page has started.
 * The returned function drops whatever has not started yet (call it when the
 * page goes away). */

const CONCURRENCY = 4
const queue: string[] = []
const requested = new Set<string>()
let active = 0

const idle: (cb: () => void) => void =
  typeof window.requestIdleCallback === 'function'
    ? cb => window.requestIdleCallback(cb, { timeout: 2000 })
    : cb => setTimeout(cb, 200)

function pump() {
  while (active < CONCURRENCY && queue.length) {
    const url = queue.shift()!
    requested.add(url)
    const img = new Image()
    img.decoding = 'async'
    img.fetchPriority = 'low'
    active++
    img.onload = img.onerror = () => { active--; idle(pump) }
    img.src = url
  }
}

export function preloadImages(urls: string[]): () => void {
  let cancelled = false
  whenStarted(() => {
    if (cancelled) return
    for (const u of urls) if (!requested.has(u) && !queue.includes(u)) queue.push(u)
    idle(pump)
  })
  return () => {
    cancelled = true
    for (const u of urls) {
      const i = queue.indexOf(u)
      if (i >= 0) queue.splice(i, 1)
    }
  }
}
