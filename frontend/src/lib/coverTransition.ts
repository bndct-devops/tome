import '@/styles/view-transitions.css'

/**
 * The cover you click grows into the page's hero cover, and Back sends it to
 * its place again. Built on the View Transitions API: the clicked cover and
 * the hero share one `view-transition-name` for the length of the transition,
 * the rest of the page cross-fades. Without the API, or under reduced motion,
 * navigation happens as before.
 *
 * Markers:
 *   book    covers `data-cover-id={book.id}`, the book page hero `data-hero-cover={id}`
 *   series  covers `data-series-cover={name}`, the series page hero `data-series-hero={name}`
 *
 * Call sites do not use this module directly: `BookLink` / `useBookNav` and
 * `useSeriesNav` in components/motion wrap it.
 *
 * Back: every navigation that starts here remembers the history entry it
 * left and what it opened. Popping back to that entry while the hero is on
 * screen holds the router's popstate, captures the page, replays the event
 * inside the transition and grows the hero back into the cover it came from
 * (a book page's hero, when a series was opened from it), whatever page that
 * is (a book page, a series, the library, Stats ...).
 *
 * Also hosts the root cross-fade between pages (`crossFade`, used by
 * components/motion/TransitionRouter): one view transition at a time.
 */

const NAME = 'book-cover'
// How long the old picture may stay frozen while the new page renders.
const WAIT_MS = 400
// Going back: the longest wait for the page's first commit (it is usually
// rendered from memory at once).
const BACK_WAIT_MS = 200
// Page to page: the longest the old picture stays frozen.
const ROUTE_CAP_MS = 250

/** What a card already knows, handed to the book page through router state so
 *  its hero paints at once instead of a loader. */
export interface BookPreview {
  id: number
  title: string
  author?: string | null
  series?: string | null
  series_index?: number | null
  /** The thumbnail the card showed: the hero starts with it (already cached). */
  cover?: string | null
  /** The cover's stored colour, held by the hero until an image paints. */
  coverColor?: string | null
}

export function previewState(preview: BookPreview) {
  return { bookPreview: preview }
}

/** The preview from router state when it belongs to this book. */
export function readPreview(state: unknown, id: number): BookPreview | null {
  const p = (state as { bookPreview?: BookPreview } | null)?.bookPreview
  return p && p.id === id ? p : null
}

/** What a series card knows, handed to the series page so its hero paints
 *  while the series loads. */
export interface SeriesPreview {
  name: string
  /** The cover the card showed. */
  cover?: string | null
}

export function seriesPreviewState(preview: SeriesPreview) {
  return { seriesPreview: preview }
}

export function readSeriesPreview(state: unknown, name: string): SeriesPreview | null {
  const p = (state as { seriesPreview?: SeriesPreview } | null)?.seriesPreview
  return p && p.name === name ? p : null
}

type Target = { kind: 'book'; id: number } | { kind: 'series'; name: string }

const sel = (attr: string, value: string | number) => `[${attr}="${CSS.escape(String(value))}"]`
const coverSel = (t: Target) => t.kind === 'book' ? sel('data-cover-id', t.id) : sel('data-series-cover', t.name)
const heroSel = (t: Target) => t.kind === 'book' ? sel('data-hero-cover', t.id) : sel('data-series-hero', t.name)

let passThrough = false

// ---------------------------------------------------------------------------
// The one way the app starts a view transition. While the update callback is
// pending the browser shows a frozen picture of the old page, so every update
// here settles within a hard cap whatever happens inside it (a promise that
// never resolves, a render error, a slow or failed chunk). One transition at a
// time: a new one asked for while another is on screen skips the old one
// (the page jumps to its new state at once) and applies its own update plainly.

type Mark = 'route' | 'cover' | 'theme'
const MARK_ATTR: Record<Mark, string> = { route: 'routeTransition', cover: 'coverTransition', theme: 'themeTransition' }

let active: ViewTransition | null = null
// True while an update callback runs its synchronous part: navigations made
// there belong to the transition already on screen.
let applying = false

/** True while a view transition is on screen. */
export function transitionRunning(): boolean {
  return active != null
}

/** True while a transition's update applies a navigation (it brings its own picture). */
export function insideTransitionUpdate(): boolean {
  return applying || passThrough
}

/** Ends the transition on screen at once (the new state shows unanimated). */
export function skipActiveTransition(): void {
  active?.skipTransition()
}

const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms))

function callSafely<T>(update: () => T | Promise<T>): Promise<T | undefined> {
  applying = true
  try {
    return Promise.resolve(update()).catch(() => undefined)
  } catch {
    return Promise.resolve(undefined)
  } finally {
    applying = false
  }
}

/**
 * Runs `update` inside a view transition, the frozen frame held for at most
 * `capMs`. Under reduced motion, without the API, or when the transition
 * cannot start, `update` runs plainly. Returns whether a transition started.
 */
export function capturedUpdate(mark: Mark, capMs: number, update: () => unknown, onDone?: () => void): boolean {
  if (!transitionsSupported()) { void callSafely(update); onDone?.(); return false }
  if (active) { active.skipTransition(); void callSafely(update); onDone?.(); return false }
  const root = document.documentElement
  let vt: ViewTransition
  try {
    vt = document.startViewTransition(() => Promise.race([callSafely(update), sleep(capMs)]).then(() => {}))
  } catch {
    void callSafely(update); onDone?.(); return false
  }
  active = vt
  root.dataset[MARK_ATTR[mark]] = ''
  vt.ready.catch(() => {})
  vt.updateCallbackDone.catch(() => {})
  vt.finished.catch(() => {}).finally(() => {
    if (active === vt) active = null
    delete root.dataset[MARK_ATTR[mark]]
    onDone?.()
  })
  return true
}

// Commits of the router (TransitionRouter reports them): a wait for the new
// page ends at its commit instead of at a guessed time.
const commitWaiters = new Set<() => void>()

/** Called by TransitionRouter after each committed location. */
export function noteRouteCommit(): void {
  const all = [...commitWaiters]
  commitWaiters.clear()
  all.forEach(f => f())
}

/** Resolves at the router's next commit, or after `ms`. */
function nextCommit(ms: number): Promise<void> {
  return new Promise(resolve => {
    const done = () => { clearTimeout(t); commitWaiters.delete(done); resolve() }
    const t = setTimeout(done, ms)
    commitWaiters.add(done)
  })
}

export function transitionsSupported(): boolean {
  return typeof document !== 'undefined'
    && typeof document.startViewTransition === 'function'
    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function onScreen(el: Element): boolean {
  const r = el.getBoundingClientRect()
  return r.width > 0 && r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth
}

/** The on-screen element matching `selector`, the one nearest `near` when several are. */
function visible(selector: string, near?: Element | null): HTMLElement | null {
  const all = Array.from(document.querySelectorAll<HTMLElement>(selector)).filter(onScreen)
  if (all.length <= 1 || !near) return all[0] ?? null
  const n = near.getBoundingClientRect()
  const dist = (el: HTMLElement) => {
    const r = el.getBoundingClientRect()
    return Math.hypot(r.left + r.width / 2 - n.left - n.width / 2, r.top + r.height / 2 - n.top - n.height / 2)
  }
  return all.reduce((a, b) => (dist(b) < dist(a) ? b : a))
}

/** The on-screen cover of a book, nearest `near` (the clicked link) when given. */
export function findBookCover(id: number, near?: Element | null): HTMLElement | null {
  return typeof document === 'undefined' ? null : visible(coverSel({ kind: 'book', id }), near)
}

/** The on-screen cover of a series, nearest `near` when given. */
export function findSeriesCover(name: string, near?: Element | null): HTMLElement | null {
  return typeof document === 'undefined' ? null : visible(coverSel({ kind: 'series', name }), near)
}

/** Resolves with an on-screen element matching `selector`, or null after `ms`.
 *  Polls as well as observing: a restored scroll brings an element on screen
 *  without a DOM change. */
function waitFor(selector: string, ms: number): Promise<HTMLElement | null> {
  const now = visible(selector)
  if (now) return Promise.resolve(now)
  return new Promise(resolve => {
    const check = () => { const el = visible(selector); if (el) done(el) }
    const done = (el: HTMLElement | null) => { obs.disconnect(); clearInterval(poll); clearTimeout(timer); resolve(el) }
    const obs = new MutationObserver(check)
    obs.observe(document.body, { childList: true, subtree: true })
    const poll = setInterval(check, 16)
    const timer = setTimeout(() => done(null), ms)
  })
}

/** Waits (briefly) for the images inside `el` to be ready to paint, so the new
 *  picture is not captured with an empty cover. */
async function decoded(el: Element) {
  const imgs = Array.from(el.querySelectorAll('img'))
  await Promise.race([
    Promise.all(imgs.map(i => i.decode().catch(() => {}))),
    new Promise(r => setTimeout(r, 120)),
  ])
}

// A hover tilt (lib/tilt.ts) on the cover or a wrapper: settled at once
// before the capture, so the cover leaves flat and the flight carries no
// perspective into its landing.
function flatten(from: HTMLElement) {
  for (let el: HTMLElement | null = from, i = 0; el && i < 4; el = el.parentElement, i++) {
    if (!el.style.transform) continue
    const transition = el.style.transition
    el.style.transition = 'none'
    el.style.transform = ''
    void el.offsetWidth
    el.style.transition = transition
  }
}

function run(from: HTMLElement, update: () => Promise<HTMLElement | null>, cut = false) {
  // Names are set only while the transition is still waiting for them: an
  // update cut off by the cap must not name an element later.
  let open = true
  let to: HTMLElement | null = null
  const root = document.documentElement
  flatten(from)
  from.style.viewTransitionName = NAME
  if (cut) root.dataset.coverCut = ''
  const started = capturedUpdate('cover', WAIT_MS, async () => {
    const until = performance.now() + WAIT_MS - 10
    from.style.viewTransitionName = ''
    const el = await update()
    if (open && el && performance.now() < until) { to = el; el.style.viewTransitionName = NAME }
  }, () => {
    open = false
    delete root.dataset.coverCut
    from.style.viewTransitionName = ''
    if (to) to.style.viewTransitionName = ''
  })
  if (!started) from.style.viewTransitionName = ''
}

// History entry a cover navigation left -> what it opened, and where the
// hero lands going back: the element it grew from, else the next selector.
const origins = new Map<string, { target: Target; back: string[]; cut: boolean }>()

const MARKERS = ['data-cover-id', 'data-series-cover', 'data-hero-cover', 'data-series-hero']

/** The selector that finds `el` again on its page: the target's card marker
 *  when it carries it, else its own marker. */
function markerOf(el: HTMLElement, target: Target): string {
  const card = coverSel(target)
  if (el.matches(card)) return card
  const attr = MARKERS.find(a => el.getAttribute(a))
  return attr ? sel(attr, el.getAttribute(attr)!) : card
}

/** The book page's hero, as a place a cover may land on going back. */
export const bookHeroSelector = (id: number) => sel('data-hero-cover', id)

// The router's entry key; the entry the app was loaded on has only its index.
function historyKey(state: unknown): string | null {
  const s = state as { key?: unknown; idx?: unknown } | null
  if (typeof s?.key === 'string') return s.key
  return typeof s?.idx === 'number' ? `idx:${s.idx}` : null
}

/** The history entry on screen now. A replace deferred into a transition
 *  compares it with the one it started on: when another navigation came in
 *  between, the replace is dropped rather than overwrite that entry. */
export function historyEntry(): string {
  return `${historyKey(window.history.state)}|${window.location.href}`
}

interface OpenOptions {
  /** Where the hero lands going back, the first on screen (default: the cover again). */
  back?: string[]
  /** Navigate once this settles (the old page stays frozen until then, within the cap). */
  ready?: Promise<void>
  /** Between a book page and its series: the page changes at once instead of
   *  cross-fading (the two heroes and titles sit in the same places, a
   *  cross-fade double-exposes them); only the cover moves. */
  cut?: boolean
}

function open(target: Target, cover: HTMLElement | null, go: () => void, opts: OpenOptions = {}) {
  const here = historyKey(window.history.state)
  if (here) {
    if (cover) origins.set(here, { target, back: [...new Set([...(opts.back ?? [markerOf(cover, target)]), coverSel(target)])], cut: !!opts.cut })
    else origins.delete(here)
  }
  if (!cover || active || !transitionsSupported() || !onScreen(cover)) {
    skipActiveTransition()
    if (opts.ready) void opts.ready.then(go)
    else go()
    return
  }
  run(cover, async () => {
    if (opts.ready) await opts.ready
    // Applied inside this transition (the router must not start its own).
    applying = true
    try { go() } finally { applying = false }
    // The hero paints in the first commit (it holds the card's preview).
    const hero = await waitFor(heroSel(target), WAIT_MS - 130)
    if (hero) await decoded(hero)
    return hero
  }, opts.cut)
}

/**
 * Navigates to a book page with the cover transition. `cover` is the clicked
 * cover's box (null: no cover to grow, navigate plainly); `go` performs the
 * navigation.
 */
export function openBookWithCover(cover: HTMLElement | null, bookId: number, go: () => void) {
  open({ kind: 'book', id: bookId }, cover, go)
}

/** Navigates to a series page, growing `cover` into its hero (see OpenOptions). */
export function openSeriesWithCover(cover: HTMLElement | null, name: string, go: () => void, opts?: OpenOptions) {
  open({ kind: 'series', name }, cover, go, opts)
}

/** True for a plain left click (no modifier) that a link handler may take over. */
export function isPlainClick(e: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; defaultPrevented: boolean }) {
  return !e.defaultPrevented && e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey
}

/**
 * Cross-fades the whole page around `update`, which applies the navigation
 * and resolves once the new page is in. The frozen frame is held for at most
 * ROUTE_CAP_MS. Under reduced motion and without the API the update runs
 * plainly; while another transition is on screen that one is skipped.
 */
export function crossFade(update: () => unknown): void {
  capturedUpdate('route', ROUTE_CAP_MS, update)
}

/** Resolves at the router's next commit, or after the route cap. */
export function routeCommitted(): Promise<void> {
  return nextCommit(ROUTE_CAP_MS)
}

// Back / forward to the entry a cover navigation left: the router's popstate
// listener is held back (this capture listener on window runs first), the
// old page is captured, then the event is replayed inside the transition.
// The cover travels only when the card is on screen in the page's first
// committed frame (the library restores its scroll in that commit); otherwise
// the transition ends at once rather than holding the old picture.
if (typeof window !== 'undefined') {
  window.addEventListener('popstate', e => {
    if (passThrough || active || !transitionsSupported()) return
    const key = historyKey(e.state)
    const origin = key ? origins.get(key) : undefined
    if (!origin) return
    const hero = visible(heroSel(origin.target))
    if (!hero) return
    e.stopImmediatePropagation()
    const state = e.state
    run(hero, async () => {
      passThrough = true
      const committed = nextCommit(BACK_WAIT_MS)
      try { window.dispatchEvent(new PopStateEvent('popstate', { state })) } finally { passThrough = false }
      await committed
      const card = origin.back.reduce<HTMLElement | null>((found, s) => found ?? visible(s), null)
      // No card where the hero could land: show the page as it is.
      if (!card) { skipActiveTransition(); return null }
      await decoded(card)
      return card
    }, origin.cut)
  }, true)
}
