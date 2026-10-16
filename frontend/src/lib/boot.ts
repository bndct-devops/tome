/** Startup: data requests in parallel with the app's code, and the splash held
 * until the page has its data.
 *
 * Each visit records the GET requests a page made while it started, keyed by
 * its address. On the next visit to that address an inline script in
 * index.html starts the same requests before the bundle has loaded, and
 * `api.ts` takes the responses from here instead of asking again. The splash
 * stays up until every early response has been used, so the page appears
 * with its data instead of going through spinner and skeleton. `HOLD_CAP_MS`
 * after the first render it goes regardless: a slow server falls back to the
 * skeleton, never a frozen logo; so does a page that stops using the early
 * responses for `QUIET_MS` (the record was stale; the next visit fixes it). A
 * first visit has nothing recorded and drops the splash on the first render,
 * as before. Recording runs `RECORD_MS` past the first render, so it also
 * catches what a page asks for once the sign-in check has answered. */

declare global {
  interface Window { __tomeBoot?: Record<string, Promise<Response>> }
}

const RECORD_KEY = 'tome_boot_requests'
const MAX_PATHS = 32 // keep in step with the inline script in index.html
const MAX_PAGES = 20
const HOLD_CAP_MS = 1000
const QUIET_MS = 400
const RECORD_MS = 2000

const bootKey = location.pathname + location.search
const early: Record<string, Promise<Response>> = window.__tomeBoot ?? {}
delete window.__tomeBoot
let unused = Object.keys(early).length
let pending = 0
let released = false
let recordUntil = Infinity
let quietTimer: ReturnType<typeof setTimeout> | undefined
const recorded: string[] = []
const readyCallbacks: (() => void)[] = []

/** The early response for `path`, at most once; otherwise `doFetch()`. Records
 * the request while the page is still starting. */
export function bootFetch(path: string, method: string | undefined, doFetch: () => Promise<Response>): Promise<Response> {
  if (method && method !== 'GET') return doFetch()
  if (performance.now() < recordUntil && recorded.length < MAX_PATHS && !recorded.includes(path)) recorded.push(path)
  const taken = early[path]
  if (!taken) return doFetch()
  delete early[path]
  unused--
  pending++
  clearTimeout(quietTimer)
  // Give React a frame to render what the caller does with the data.
  const settle = () => requestAnimationFrame(() => requestAnimationFrame(() => { pending--; check() }))
  taken.then(settle, settle)
  return taken
}

function check() {
  if (released || pending > 0) return
  if (unused <= 0) release()
  else if (recordUntil !== Infinity) {
    clearTimeout(quietTimer)
    quietTimer = setTimeout(release, QUIET_MS)
  }
}

function release() {
  if (released) return
  released = true
  document.getElementById('tome-splash')?.remove()
  for (const k of Object.keys(early)) delete early[k]
  readyCallbacks.splice(0).forEach(cb => cb())
}

function saveRecord() {
  try {
    const all: Record<string, string[]> = JSON.parse(localStorage.getItem(RECORD_KEY) || '{}')
    delete all[bootKey]
    if (recorded.length) all[bootKey] = recorded
    const keys = Object.keys(all)
    for (const k of keys.slice(0, Math.max(0, keys.length - MAX_PAGES))) delete all[k]
    localStorage.setItem(RECORD_KEY, JSON.stringify(all))
  } catch { /* storage unavailable: no early requests next time */ }
}

/** Runs `cb` once the page has started (splash gone), for work that must not
 * compete with it, like preloading images. */
export function whenStarted(cb: () => void) {
  if (released) cb()
  else readyCallbacks.push(cb)
}

/** Call once the first render is on screen. The splash sits outside React's
 * root, so it stays until released here or when the data is in. */
export function afterFirstRender() {
  if (recordUntil !== Infinity) return // StrictMode runs effects twice in dev
  recordUntil = performance.now() + RECORD_MS
  setTimeout(saveRecord, RECORD_MS)
  if (unused <= 0 && pending <= 0) {
    requestAnimationFrame(release)
    return
  }
  setTimeout(release, HOLD_CAP_MS)
  check()
}
