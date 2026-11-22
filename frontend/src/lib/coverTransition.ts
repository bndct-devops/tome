import '@/styles/view-transitions.css'

/**
 * The cover you click grows into the book page's hero cover, and Back sends
 * it to its place in the list. Built on the View Transitions API: the clicked
 * cover and the hero cover share one `view-transition-name` for the length of
 * the transition, the rest of the page cross-fades. Without the API, or under
 * reduced motion, navigation happens as before.
 *
 * Covers mark themselves with `data-cover-id`, the hero with `data-hero-cover`.
 */

const NAME = 'book-cover'
// How long the old picture may stay frozen while the new page renders.
const WAIT_MS = 400

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
}

export function previewState(preview: BookPreview) {
  return { bookPreview: preview }
}

/** The preview from router state when it belongs to this book. */
export function readPreview(state: unknown, id: number): BookPreview | null {
  const p = (state as { bookPreview?: BookPreview } | null)?.bookPreview
  return p && p.id === id ? p : null
}

let running = false
let passThrough = false

function supported(): boolean {
  return typeof document !== 'undefined'
    && typeof document.startViewTransition === 'function'
    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function onScreen(el: Element): boolean {
  const r = el.getBoundingClientRect()
  return r.width > 0 && r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth
}

/** Resolves with the first element matching `selector`, or null after `ms`. */
function waitFor(selector: string, ms: number): Promise<Element | null> {
  const now = document.querySelector(selector)
  if (now) return Promise.resolve(now)
  return new Promise(resolve => {
    const done = (el: Element | null) => { obs.disconnect(); clearTimeout(timer); resolve(el) }
    const obs = new MutationObserver(() => { const el = document.querySelector(selector); if (el) done(el) })
    obs.observe(document.body, { childList: true, subtree: true })
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

function run(from: HTMLElement, update: () => Promise<HTMLElement | null>) {
  running = true
  const root = document.documentElement
  root.dataset.coverTransition = ''
  from.style.viewTransitionName = NAME
  let to: HTMLElement | null = null
  const vt = document.startViewTransition(async () => {
    from.style.viewTransitionName = ''
    to = await update()
    if (to) to.style.viewTransitionName = NAME
  })
  vt.finished.catch(() => {}).finally(() => {
    from.style.viewTransitionName = ''
    if (to) to.style.viewTransitionName = ''
    delete root.dataset.coverTransition
    running = false
  })
}

/**
 * Navigates to a book page with the cover transition. `cover` is the clicked
 * cover's box; `go` performs the navigation.
 */
export function openBookWithCover(cover: HTMLElement | null, bookId: number, go: () => void) {
  if (!cover || running || !supported() || !onScreen(cover)) { go(); return }
  run(cover, async () => {
    go()
    const hero = await waitFor(`[data-hero-cover="${bookId}"]`, WAIT_MS) as HTMLElement | null
    if (hero) await decoded(hero)
    return hero
  })
}

/** True for a plain left click (no modifier) that a link handler may take over. */
export function isPlainClick(e: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; defaultPrevented: boolean }) {
  return !e.defaultPrevented && e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey
}

// Back / forward from a book page to the library: the router's popstate
// listener is held back (this capture listener on window runs first), the
// old page is captured, then the event is replayed inside the transition.
if (typeof window !== 'undefined') {
  window.addEventListener('popstate', e => {
    if (passThrough || running || !supported()) return
    const hero = document.querySelector<HTMLElement>('[data-hero-cover]')
    if (!hero || !onScreen(hero) || window.location.pathname !== '/') return
    const id = hero.dataset.heroCover
    e.stopImmediatePropagation()
    const state = e.state
    run(hero, async () => {
      passThrough = true
      try { window.dispatchEvent(new PopStateEvent('popstate', { state })) } finally { passThrough = false }
      const landed = await waitFor('[data-dashboard]', WAIT_MS)
      if (!landed) return null
      // The library restores its scroll in the same commit the cards arrive in.
      const card = Array.from(document.querySelectorAll<HTMLElement>(`[data-cover-id="${id}"]`)).find(onScreen) ?? null
      return card
    })
  }, true)
}
