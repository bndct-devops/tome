/**
 * The hooks and paths behind BookLink / SeriesLink (see BookLink.tsx for how
 * to use them).
 *
 *   const openBook = useBookNav();     openBook(preview, cover?, { replace? })
 *   const openSeries = useSeriesNav(); openSeries(name, cover?, { from? })
 */
import { useCallback, type RefObject } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/lib/api'
import {
  bookHeroSelector, findBookCover, findSeriesCover, openBookWithCover, openSeriesWithCover, previewState,
  seriesPreviewState, type BookPreview,
} from '@/lib/coverTransition'

export type CoverSource = HTMLElement | RefObject<HTMLElement | null> | null | undefined

function resolve(cover: CoverSource, find: () => HTMLElement | null): HTMLElement | null {
  if (cover === undefined) return find()
  if (cover === null) return null
  return cover instanceof HTMLElement ? cover : cover.current
}

/** The picture a cover element shows. */
export function coverThumb(cover: HTMLElement | null): string | null {
  const img = cover?.querySelector('img')
  return img?.currentSrc || img?.getAttribute('src') || null
}

function withThumb(book: BookPreview, cover: HTMLElement | null): BookPreview {
  if (book.cover !== undefined || !cover) return book
  return { ...book, cover: coverThumb(cover) }
}

export const bookPath = (id: number) => `/books/${id}`
export const seriesPath = (name: string) => `/?tab=series&series_detail=${encodeURIComponent(name)}`

// ---------------------------------------------------------------------------
// Book page answers, asked for ahead: when the pointer rests on a link to a
// book (or presses it, or a tap/Enter opens it) its page's requests start
// then, not after the navigation, and the page mounts with whatever is in.

export type BookSeedKey = 'book' | 'status' | 'adjacent' | 'kosync' | 'annotations' | 'stats' | 'estimate' | 'series'
export interface BookSeed {
  /** Answers already in. */
  values: Partial<Record<BookSeedKey, unknown>>
  /** Every request, answered or not. */
  pending: Partial<Record<BookSeedKey, Promise<unknown>>>
  /** Resolves once every request has answered or failed. */
  all: Promise<void>
  at: number
}

const tzOffset = () => new Date().getTimezoneOffset()
export function bookRequests(id: number | string): Record<Exclude<BookSeedKey, 'series'>, string> {
  return {
    book: `/books/${id}`,
    status: `/books/${id}/status`,
    adjacent: `/books/${id}/adjacent`,
    kosync: `/books/${id}/kosync-progress`,
    annotations: `/books/${id}/annotations?include_clippings=1&tz_offset=${tzOffset()}`,
    stats: `/books/${id}/reading-stats?tz_offset=${tzOffset()}`,
    estimate: `/books/${id}/estimate?tz_offset=${tzOffset()}`,
  }
}
export const seriesDetailUrl = (name: string) => `/books/series-detail?name=${encodeURIComponent(name)}`
/** The hero's full-size cover for a book answer. */
export const heroCoverSrc = (b: { id: number; updated_at: string }) => `/api/books/${b.id}/cover?v=${encodeURIComponent(b.updated_at)}`

/** Starts every request of a book page; the full cover is fetched as soon as the book is in. */
export function fetchBookSeed(id: number | string): BookSeed {
  const pending: BookSeed['pending'] = {}
  for (const [k, url] of Object.entries(bookRequests(id)) as [BookSeedKey, string][]) pending[k] = api.get(url)
  const book = pending.book as Promise<{ id: number; series?: string | null; cover_path?: string | null; updated_at: string }>
  pending.series = book.then(b =>
    b.series ? api.get<{ books?: unknown[] }>(seriesDetailUrl(b.series)).then(d => d.books ?? []) : [])
  book.then(b => { if (b.cover_path) new Image().src = heroCoverSrc(b) }, () => {})
  const values: BookSeed['values'] = {}
  const all = Promise.all((Object.entries(pending) as [BookSeedKey, Promise<unknown>][])
    .map(([k, p]) => p.then(v => { values[k] = v }, () => {}))).then(() => {})
  return { values, pending, all, at: Date.now() }
}

// Asked-ahead seeds by book id. Short-lived: an answer older than this is
// asked again (the book may have changed since).
const SEED_LIFETIME_MS = 5000
const SEED_MAX = 4
const seeds = new Map<number, BookSeed>()

function freshSeed(id: number): BookSeed | null {
  const s = seeds.get(id)
  if (s && Date.now() - s.at < SEED_LIFETIME_MS) return s
  if (s) seeds.delete(id)
  return null
}

/** Starts a book page's requests now, unless they were started moments ago. */
export function prefetchBook(id: number): void {
  if (!Number.isFinite(id) || freshSeed(id)) return
  seeds.set(id, fetchBookSeed(id))
  while (seeds.size > SEED_MAX) seeds.delete(seeds.keys().next().value as number)
}

/** The asked-ahead requests for a book, if fresh (left in place: a page mounting twice finds them again). */
export function peekBookSeed(id: number): BookSeed | null {
  return freshSeed(id)
}

/** Forgets the asked-ahead requests for a book once its page has them. */
export function dropBookSeed(id: number): void {
  seeds.delete(id)
}

// Which links start it: an anchor to /books/{id}. A mouse resting on one for
// INTENT_MS (not one swept across, nor one scrolled under a still pointer),
// a mouse or pen press, or the click itself (a tap, Enter). A touch that may
// become a scroll starts nothing.
const INTENT_MS = 90
const BOOK_HREF = /^\/books\/(\d+)\/?$/

function bookLinkAt(target: EventTarget | null): { a: Element; id: number } | null {
  const a = target instanceof Element ? target.closest('a[href]') : null
  const match = a && BOOK_HREF.exec(a.getAttribute('href') ?? '')
  if (!a || !match) return null
  const id = Number(match[1])
  return window.location.pathname === bookPath(id) ? null : { a, id }
}

if (typeof document !== 'undefined') {
  let hovered: { a: Element; id: number } | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  const stop = () => { if (timer) { clearTimeout(timer); timer = null } }
  const arm = () => {
    stop()
    const h = hovered
    if (h) timer = setTimeout(() => { timer = null; prefetchBook(h.id) }, INTENT_MS)
  }
  document.addEventListener('pointerover', e => {
    if (e.pointerType !== 'mouse') return
    const link = bookLinkAt(e.target)
    if (link?.a === hovered?.a) return
    hovered = link
    arm()
  }, { capture: true, passive: true })
  document.addEventListener('pointerout', e => {
    if (!hovered || (e.relatedTarget instanceof Node && hovered.a.contains(e.relatedTarget))) return
    hovered = null
    stop()
  }, { capture: true, passive: true })
  // Content scrolling under a still pointer: wait for the pointer to move again.
  document.addEventListener('scroll', stop, { capture: true, passive: true })
  document.addEventListener('pointermove', e => {
    if (e.pointerType === 'mouse' && hovered && !timer && !freshSeed(hovered.id)) arm()
  }, { capture: true, passive: true })
  document.addEventListener('pointerdown', e => {
    if (e.pointerType === 'touch' || e.button !== 0) return
    const link = bookLinkAt(e.target)
    if (link) prefetchBook(link.id)
  }, { capture: true, passive: true })
  document.addEventListener('click', e => {
    const link = bookLinkAt(e.target)
    if (link && e.button === 0) prefetchBook(link.id)
  }, { capture: true })
}

/** `open(book, cover?, { replace? })`: navigates to the book page. */
export function useBookNav() {
  const navigate = useNavigate()
  return useCallback((book: BookPreview, cover?: CoverSource, opts?: { replace?: boolean; near?: Element | null }) => {
    prefetchBook(book.id)
    const el = resolve(cover, () => findBookCover(book.id, opts?.near))
    const state = previewState(withThumb(book, el))
    openBookWithCover(el, book.id, () => navigate(bookPath(book.id), { state, replace: opts?.replace }))
  }, [navigate])
}

// Series heroes, told ahead: a page that knows a series' hero cover without
// showing it (a book page, from its volumes) leaves it here once it has
// settled. The picture is fetched and decoded then, and a link to the series
// hands it to the series page, which paints it in its first frame.
const SERIES_HINT_MAX = 6
const seriesHints = new Map<string, { cover: string; img: HTMLImageElement }>()

/** Fetches and decodes `cover` as `name`'s hero, for a later open of the series. */
export function hintSeries(name: string, cover: string | null): void {
  if (!cover || typeof Image === 'undefined') return
  if (seriesHints.get(name)?.cover === cover) return
  const img = new Image()
  img.decoding = 'async'
  img.src = cover
  img.decode().catch(() => { if (seriesHints.get(name)?.img === img) seriesHints.delete(name) })
  seriesHints.delete(name)
  seriesHints.set(name, { cover, img })
  while (seriesHints.size > SERIES_HINT_MAX) seriesHints.delete(seriesHints.keys().next().value as string)
}

// A series page's answers, asked for ahead the same way (from a book page:
// on idle, and again on hover, press or focus of a link to the series), so
// the series page paints complete in its first frame.
export interface SeriesSeed {
  values: { detail?: unknown; arcs?: unknown; meta?: unknown; rating?: unknown; stats?: unknown }
  /** Resolves once every request has answered or failed. */
  all: Promise<void>
  at: number
}
const SERIES_SEED_MAX = 4
// A seed older than this is not used by the series page.
const SERIES_SEED_LIFETIME_MS = 15000
const seriesSeeds = new Map<string, SeriesSeed>()

/** Starts a series page's requests, unless a seed younger than `maxAgeMs` exists. */
export function prefetchSeries(name: string, maxAgeMs = SERIES_SEED_LIFETIME_MS): void {
  const s = seriesSeeds.get(name)
  if (s && Date.now() - s.at < maxAgeMs) return
  const values: SeriesSeed['values'] = {}
  const ask = (k: keyof SeriesSeed['values'], url: string) => api.get(url).then(v => { values[k] = v }, () => {})
  const all = Promise.all([
    ask('detail', seriesDetailUrl(name)),
    ask('arcs', `/series/${encodeURIComponent(name)}/arcs`),
    ask('meta', '/series/meta-map'),
    // Asked for by SeriesRating and SeriesReadingStats on their own (peekSeriesPart).
    ask('rating', `/series/${encodeURIComponent(name)}/rating`),
    ask('stats', `/series/${encodeURIComponent(name)}/reading-stats`),
  ]).then(() => {})
  seriesSeeds.delete(name)
  seriesSeeds.set(name, { values, all, at: Date.now() })
  while (seriesSeeds.size > SERIES_SEED_MAX) seriesSeeds.delete(seriesSeeds.keys().next().value as string)
}

/** The asked-ahead answers for a series when complete and fresh. */
export function peekSeriesSeed(name: string): { detail: unknown; arcs: unknown; meta: unknown } | null {
  const s = seriesSeeds.get(name)
  if (!s || Date.now() - s.at > SERIES_SEED_LIFETIME_MS) return null
  const { detail, arcs, meta } = s.values
  return detail && arcs && meta ? { detail, arcs, meta } : null
}

/** One asked-ahead answer of a series page part (`rating`, `stats`), when fresh:
 *  a part can start from it instead of holding an empty slot. */
export function peekSeriesPart(name: string, part: 'rating' | 'stats'): unknown {
  const s = seriesSeeds.get(name)
  return s && Date.now() - s.at <= SERIES_SEED_LIFETIME_MS ? s.values[part] : undefined
}

/** Resolves when the series' seed is in, or after `ms`. */
function seriesSeedSettled(name: string, ms: number): Promise<void> {
  const s = seriesSeeds.get(name)
  if (!s) return Promise.resolve()
  return Promise.race([s.all, new Promise<void>(r => setTimeout(r, ms))])
}
// The longest a series link waits for its seed before navigating (the old
// page stays frozen in the meantime).
const SERIES_SEED_WAIT_MS = 200

// Opened from a book page: its history entry keeps the book's preview, so
// going back its hero paints in the first frame (full size, it is in the
// cache) and the cover lands on it.
function keepBookPreview(book: BookPreview) {
  const h = window.history.state as { usr?: unknown } | null
  if (!h) return
  const usr = h.usr && typeof h.usr === 'object' ? h.usr : {}
  window.history.replaceState({ ...h, usr: { ...usr, ...previewState(book) } }, '')
}

/** `open(name, cover?, { from? })`: navigates to the series page. `from`: the
 *  book page it is opened from (going back, the cover lands on its hero
 *  wherever it started, the page opening at its top). */
export function useSeriesNav() {
  const navigate = useNavigate()
  return useCallback((name: string, cover?: CoverSource, opts?: { near?: Element | null; from?: BookPreview }) => {
    const el = resolve(cover, () => findSeriesCover(name, opts?.near))
    const hint = seriesHints.get(name)?.cover
    // From a book page the hint is the series hero itself (full size, decoded).
    const pic = opts?.from ? hint ?? coverThumb(el) : coverThumb(el) ?? hint
    const state = seriesPreviewState({ name, cover: pic ?? null })
    if (opts?.from) keepBookPreview(opts.from)
    // From a book page the series' answers were asked for ahead: the page
    // opens once they are in (briefly), complete in its first frame.
    openSeriesWithCover(el, name, () => navigate(seriesPath(name), { state }), opts?.from ? {
      back: [bookHeroSelector(opts.from.id)],
      ready: seriesSeedSettled(name, SERIES_SEED_WAIT_MS),
      cut: true,
    } : undefined)
  }, [navigate])
}
