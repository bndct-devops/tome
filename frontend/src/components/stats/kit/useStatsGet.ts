// One GET per URL for the designed Stats pages, cached for the session like
// /api/stats. A new URL (another range) keeps the previous answer on screen,
// flagged `pending`, until its own lands, so a page can dim in place instead
// of falling back to its skeleton.
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { CustomRange } from '@/components/stats/range'

const cache = new Map<string, unknown>()
// Requests on their way, by URL: a second asker (another section, a tab switched
// back and forth mid-load) shares the first request instead of starting its own.
const inflight = new Map<string, Promise<unknown>>()
// URLs whose request is a quiet prefetch, not something on screen waiting.
const background = new Set<string>()
// The Stats visit each cached answer was asked for in (beginStatsVisit): within a
// visit an answer is fresh and is not asked for again; the next visit asks once more.
let visit = 0
const answeredIn = new Map<string, number>()

/** A new visit of the Stats page: answers from earlier visits are shown, then asked for again. */
export function beginStatsVisit() {
  visit += 1
}

/** Answered during this visit and still cached (not dropped by an edit since). */
export const statsFresh = (key: string) => cache.has(key) && answeredIn.get(key) === visit

/**
 * GET `url` (under /api) once while it is in flight, and keep the answer in the
 * session cache under `key` (default: the URL itself).
 */
export function statsFetch<T>(url: string, key: string = url): Promise<T> {
  const running = inflight.get(url) as Promise<T> | undefined
  if (running) {
    // Something on screen now waits for it: no longer a quiet prefetch.
    background.delete(url)
    return running
  }
  const asked = visit
  // An answer whose request was dropped meanwhile (dropStatsCache) is not cached.
  const p: Promise<T> = api.get<T>(url).then((r) => {
    if (inflight.get(url) === p) { cache.set(key, r); answeredIn.set(key, asked) }
    return r
  })
  inflight.set(url, p)
  const clear = () => { if (inflight.get(url) === p) { inflight.delete(url); background.delete(url) } }
  p.then(clear, clear)
  return p
}

/** The session cache, also used by the Stats frame for /api/stats (keys are paths under /api). */
export const statsCacheGet = <T,>(path: string) => cache.get(path) as T | undefined
export const statsCacheSet = (path: string, value: unknown) => { cache.set(path, value) }

// ── Prefetch: one quiet request at a time, after idle, behind anything on screen ──

type Job = { url: string; key: string; signal: AbortSignal }
const queue: Job[] = []
let draining = false

type IdleWindow = Window & {
  requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number
  cancelIdleCallback?: (id: number) => void
}

const idle = (signal: AbortSignal) => new Promise<void>((resolve) => {
  const w = window as IdleWindow
  if (w.requestIdleCallback) {
    const id = w.requestIdleCallback(() => resolve(), { timeout: 2000 })
    signal.addEventListener('abort', () => { w.cancelIdleCallback?.(id); resolve() }, { once: true })
  } else {
    const id = window.setTimeout(resolve, 300)
    signal.addEventListener('abort', () => { window.clearTimeout(id); resolve() }, { once: true })
  }
})

// Settles once no request that something on screen waits for is in flight.
async function visibleSettled() {
  for (;;) {
    const waiting = [...inflight].filter(([u]) => !background.has(u)).map(([, p]) => p)
    if (waiting.length === 0) return
    await Promise.allSettled(waiting)
  }
}

async function drain() {
  if (draining) return
  draining = true
  try {
    for (let job = queue.shift(); job; job = queue.shift()) {
      if (job.signal.aborted) continue
      await idle(job.signal)
      await visibleSettled()
      if (job.signal.aborted || statsFresh(job.key) || inflight.has(job.url)) continue
      const p = statsFetch(job.url, job.key)
      background.add(job.url)
      await p.catch(() => {})
    }
  } finally {
    draining = false
  }
}

/**
 * Fetch `jobs` into the cache in the background, so later range switches come from
 * it. Aborting `signal` drops the ones not started yet.
 */
export function statsPrefetch(jobs: { url: string; key?: string }[], signal: AbortSignal) {
  for (const j of jobs) queue.push({ url: j.url, key: j.key ?? j.url, signal })
  void drain()
}

/** How long a range change waits before it shows that it is waiting (bar, dimming). */
export const PENDING_DELAY = 150

/** `on` once it has stayed on for `ms`: a short wait shows nothing. */
export function useDelayed(on: boolean, ms: number = PENDING_DELAY): boolean {
  const [late, setLate] = useState(false)
  useEffect(() => {
    if (!on) return
    const id = window.setTimeout(() => setLate(true), ms)
    return () => { window.clearTimeout(id); setLate(false) }
  }, [on, ms])
  return on && late
}

/** The query part of a ranged stats request, with the browser's offset. */
export function rangeQuery(days: number, custom: CustomRange | null): string {
  const range = custom ? `start=${custom.start}&end=${custom.end}` : `days=${days}`
  return `${range}&tz_offset=${new Date().getTimezoneOffset()}`
}

/** The Habits page's own ranged request. The Stats frame waits for it too, so a range lands in one step. */
export const habitsPath = (days: number, custom: CustomRange | null) => '/stats/habits?' + rangeQuery(days, custom)

/**
 * `path` is relative to /api (e.g. `/stats/habits?days=90&tz_offset=-60`); null fetches nothing.
 * A /stats answer already asked for during this visit is not asked for again; other paths
 * (goals) are asked for on every new path, as before.
 */
export function useStatsGet<T>(path: string | null): { data: T | null; pending: boolean; failed: boolean } {
  const [state, setState] = useState<{ path: string; data: T } | null>(() => {
    const hit = path ? (cache.get(path) as T | undefined) : undefined
    return path && hit !== undefined ? { path, data: hit } : null
  })
  const [failedPath, setFailedPath] = useState<string | null>(null)
  useEffect(() => {
    if (!path || (path.startsWith('/stats') && statsFresh(path))) return
    let current = true
    statsFetch<T>(path)
      .then((r) => { if (current) setState({ path, data: r }) })
      .catch(() => { if (current) setFailedPath(path) })
    return () => { current = false }
  }, [path])
  // A cached answer for this URL shows at once; otherwise the last one stays, pending.
  const hit = path ? (cache.get(path) as T | undefined) : undefined
  return {
    data: hit !== undefined ? hit : state?.data ?? null,
    pending: hit === undefined && !!state && state.path !== path,
    failed: !!path && failedPath === path,
  }
}

// Told of every drop, so a host showing a dropped answer asks again whoever made the edit.
const dropListeners = new Set<(prefix: string) => void>()

/** Called with the prefix of every dropStatsCache; returns the unsubscribe. */
export function onStatsDrop(fn: (prefix: string) => void): () => void {
  dropListeners.add(fn)
  return () => { dropListeners.delete(fn) }
}

/** Forget cached answers (after an edit that changes them, e.g. a deleted session). */
export function dropStatsCache(prefix: string) {
  for (const k of [...cache.keys()]) if (k.startsWith(prefix)) { cache.delete(k); answeredIn.delete(k) }
  for (const k of [...inflight.keys()]) if (k.startsWith(prefix)) { inflight.delete(k); background.delete(k) }
  for (const fn of [...dropListeners]) fn(prefix)
}
