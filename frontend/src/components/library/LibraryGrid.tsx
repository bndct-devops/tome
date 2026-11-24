/**
 * The Library's cards, grid or list, and how they move.
 *
 * Only the cards around the screen take part in a change: a list change
 * (sort, filter, search, grouping), the grid/list switch and the cover-size
 * slider measure and animate the cards near the screen and let the rest snap
 * where nobody sees them. The caller calls `prepare()` on the handle right
 * before the state change, while the old cards are still in place.
 *
 * The cards render in blocks of 30, each block its own AnimatePresence: a
 * new list that replaces hundreds of loaded cards removes the blocks past the
 * first at once instead of fading cards nobody sees. Only the blocks on screen
 * follow a view switch or selection mode at once; the others keep their props
 * (so they do not render again), sit as empty boxes under one style rule, and
 * catch up a block at a time after, the first card on screen held in place.
 * Coming back to a long list renders the blocks around where it was left;
 * the ones above are boxes of the same size until they fill in.
 *
 * A list change: the old cards stay (dimmed by the caller) until the new list
 * is in. Cards in both lists glide to their new place; the ones that leave
 * fade out over the ones that arrive (`data-motion-pop-id` is on top, see
 * library.css), so the grid cross-changes instead of blinking.
 *
 * Grouping by series (and back) is its own move, one commit and one motion
 * (see GroupRun): the new list renders at once, the cards on screen are read
 * once before and once after, and only those animate (transform and opacity,
 * by hand). A stack is its first volume's card: that card's cover glides to
 * the stack's place and is its top cover from the first frame. The other
 * volumes on screen leave as bare covers that slide under it and fade; their
 * cards are gone at once. Ungrouping is the mirror: the volumes come out from
 * under the first one as bare covers and get their text when they land.
 */
import { createPortal, flushSync } from 'react-dom'
import { forwardRef, memo, useCallback, useEffect, useId, useImperativeHandle, useInsertionEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { AnimatePresence, m, type Variants } from 'motion/react'
import type { Book, ReadingStatus } from '@/lib/books'
import { BookCard, type ViewMode } from '@/components/BookCard'
import { SeriesStackCard } from '@/components/SeriesStackCard'
import { EASE, EASE_CSS, seriesPath } from '@/components/motion'
import { cn } from '@/lib/utils'
import { coverSrc } from '@/lib/covers'
import './library.css'

export type StatusMap = Record<number, { status: ReadingStatus; progress_pct: number | null; rating: number | null }>

const CHUNK = 30
// Cards before and after the visible ones that still animate.
const MARGIN = 24
const GLIDE_S = 0.24
const SWITCH_S = 0.32
// After a view switch, blocks below the screen follow once the travel is over.
const SWITCH_SETTLE_MS = SWITCH_S * 1000 + 60
const SELECT_SETTLE_MS = 160
const STEP_MS = 24
// Grouping by series and back.
const GROUP_MS = 280
// A remount (back to the Library) shows the blocks around the place it
// returns to first, the rest one block per step after: blocks below appear,
// blocks above replace boxes of the same size (the screen stays put).
const MOUNT_SETTLE_MS = 380
// A list row's height, a little under the real one (so the estimate of what
// fits on screen errs on the side of more).
const LIST_ROW = 60

export type PrepareKind = 'view' | 'list' | 'size' | 'select'

/** Where the list was left: the first card on screen, its top below the top
 *  of <main>, and its height (the size of the boxes drawn for the cards above
 *  it until they render). */
export interface ResumePoint { index: number; offset: number; height: number }

export interface LibraryGridHandle {
  /** Right before a state change that moves the cards, while the old ones are on screen. */
  prepare(kind: PrepareKind, opts?: { toGrouped?: boolean }): void
  /** The first card on screen (for the way back). */
  resumePoint(): ResumePoint
}

interface Props {
  books: Book[]
  grouped: boolean
  view: ViewMode
  gridSize: number
  statuses: StatusMap
  selected: Set<number>
  selecting: boolean
  focusedIndex: number | null
  refreshing: boolean
  /** Coming back to where the list was left: the blocks around that card
   *  render at once, at the same place on screen; the others after. */
  resume?: ResumePoint
  /** A list shown again from the top: when long (STAGE_FROM), the first
   *  blocks render at once, the others after. */
  staged?: boolean
  onToggle: (id: number, shift: boolean) => void
  onTag: (tag: string) => void
  onSeries: (series: string) => void
  onAuthor: (author: string) => void
  onQuickLook: (id: number, origin: DOMRect | undefined, cover: string | null) => void
  onOpenSeries: (name: string) => void
}

const isStack = (grouped: boolean, b: Book) => grouped && !!b.series && b.status !== 'no_file'

/** The cards that take part: indices `from`..`to` of the new list and the
 *  keys that were on screen. `version` changes with every prepared change,
 *  `travel` with every view switch (cover and title hand-over). */
interface Win {
  from: number; to: number; keys: Set<string>; version: number; travel: number
  /** The cards on screen after the change (estimated for a view switch): the
   *  blocks that hold them change at once, the others follow. */
  shownFrom: number; shownTo: number
}

/** Shared between the grid and its cards without re-rendering them. */
interface Shared {
  grouped: boolean
  /** A grouping change is on: the cards that leave go at once (GroupRun draws them). */
  grouping: boolean
}

/** A card on screen when a grouping change was prepared. */
interface GroupItem {
  key: string
  series: string | null
  stack: boolean
  card: DOMRect
  cover: DOMRect
  img: HTMLImageElement | null
  radius: string
  color: string
  /** A bare cover of the previous change, still on its way. */
  ghost: boolean
}

/** A grouping change between prepare() and the commit that shows it. */
interface GroupSnap { toGrouped: boolean; items: GroupItem[] }

/** A grouping change in motion: its animations, the layer of bare covers,
 *  the cards marked for it, and the timer that ends it. */
interface GroupRun { anims: Animation[]; layer: HTMLElement | null; marked: HTMLElement[]; timer: number }

/** Reads the cards on screen (and the bare covers of a change still on its
 *  way) for a grouping change. Reads only. */
function snapGroup(grid: HTMLElement, top: number, bottom: number, bookOf: Map<string, Book>): GroupItem[] {
  const out: GroupItem[] = []
  const read = (el: HTMLElement, key: string, ghost: boolean) => {
    const card = el.getBoundingClientRect()
    if (card.bottom <= top || card.top >= bottom) return
    const box = ghost ? el : coverOf(el)
    const cover = box ? box.getBoundingClientRect() : card
    const book = bookOf.get(key)
    out.push({
      key, ghost, card, cover,
      series: ghost ? el.dataset.series || null : book?.series ?? null,
      stack: !ghost && !!el.querySelector('[data-series-cover]'),
      img: box?.querySelector('img') ?? null,
      radius: box ? getComputedStyle(box).borderRadius : '0px',
      color: box ? getComputedStyle(box).backgroundColor : '',
    })
  }
  for (const el of grid.querySelectorAll<HTMLElement>(':scope > [data-item]:not([data-motion-pop-id])')) read(el, el.dataset.item!, false)
  for (const el of grid.querySelectorAll<HTMLElement>(':scope > [data-group-ghosts] > [data-ghost]')) read(el, el.dataset.ghost!, true)
  return out
}

/** Ends a grouping change at once: its motion stops where it would end. */
function endGroupRun(run: GroupRun | null, grid: HTMLElement | null) {
  if (run) {
    clearTimeout(run.timer)
    for (const a of run.anims) a.cancel()
    run.layer?.remove()
    for (const el of run.marked) { el.removeAttribute('data-emerge'); el.removeAttribute('data-group-top') }
  }
  grid?.removeAttribute('data-grouping')
}

const OUT_OF_WIN: Win = { from: -1, to: -2, keys: new Set(), version: 0, travel: 0, shownFrom: 0, shownTo: 0 }

// WebKit (no overflow-clip-margin, the test the Hardcover page uses for the
// same engines): content-visibility: hidden boxes are laid out again on every
// forced layout, so the blocks behind a switch are sized and contained
// instead; and a cover grid of hundreds of cards takes it over 100 ms per
// layout, so its cards off screen skip theirs (data-skip-offscreen, library.css).
const SKIP_HIDDEN = typeof CSS !== 'undefined' && CSS.supports('overflow-clip-margin', '1px')

/** A list shown again (`staged`) renders in steps from this many cards on. */
export const STAGE_FROM = SKIP_HIDDEN ? 120 : CHUNK

/** About how many cards fill the window (errs on the side of more). */
function screenful(list: boolean, gridSize: number): number {
  const h = window.innerHeight
  if (list) return Math.ceil(h / LIST_ROW) + 2
  const cols = Math.max(1, Math.floor(window.innerWidth / (Math.min(gridSize, window.innerWidth * 0.42) + 12)))
  return cols * (Math.ceil(h / (gridSize * 1.5 + 40)) + 1)
}

function reduced() {
  // eslint-disable-next-line lingui/no-unlocalized-strings -- media query
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function makeVariants(ref: RefObject<Shared>): Variants {
  const fade = { opacity: 0, transition: { duration: 0.15 } }
  const gone = { opacity: 0, transition: { duration: 0 } }
  return {
    shown: { opacity: 1 },
    // A grouping change draws the cards that leave as bare covers (GroupRun):
    // the card itself goes at once. Any other card fades where it was.
    leave: () => (ref.current.grouping ? gone : fade),
  }
}

/** The cover box of a card or stack. */
function coverOf(el: HTMLElement): HTMLElement | null {
  return el.querySelector<HTMLElement>('[data-series-cover], [data-cover-id]')
}

interface ItemProps {
  /** Set by AnimatePresence (popLayout measures the leaving card through it). */
  ref?: React.Ref<HTMLDivElement>
  book: Book
  itemKey: string
  index: number
  stack: boolean
  view: ViewMode
  focused: boolean
  selected: boolean
  selecting: boolean
  status: ReadingStatus | undefined
  progress: number | null | undefined
  rating: number | null | undefined
  /** Changes when this card should measure for a glide; 'x' = sits out. */
  dep: string
  travelDep: string
  switching: boolean
  variants: Variants
  onToggle: Props['onToggle']
  onTag: Props['onTag']
  onSeries: Props['onSeries']
  onAuthor: Props['onAuthor']
  onQuickLook: Props['onQuickLook']
  onOpenSeries: Props['onOpenSeries']
}

const LibraryItem = memo(function LibraryItem({
  ref: presenceRef, book, itemKey, index, stack, view, focused, selected, selecting, status, progress, rating,
  dep, travelDep, switching, variants,
  onToggle, onTag, onSeries, onAuthor, onQuickLook, onOpenSeries,
}: ItemProps) {
  const ref = useRef<HTMLDivElement | null>(null)
  const setRef = useCallback((node: HTMLDivElement | null) => {
    ref.current = node
    if (typeof presenceRef === 'function') presenceRef(node)
    else if (presenceRef) presenceRef.current = node
  }, [presenceRef])
  const onSelect = useMemo(
    () => (selecting && !stack ? (e: React.MouseEvent) => { e.preventDefault(); onToggle(book.id, e.shiftKey) } : undefined),
    [selecting, stack, onToggle, book.id],
  )
  const cover = book.cover_path ? book : null
  const quickLook = useCallback(
    (origin?: DOMRect) => onQuickLook(book.id, origin, cover ? coverUrlOf(cover) : null),
    [onQuickLook, book.id, cover],
  )
  const openSeries = useCallback(() => { if (book.series) onOpenSeries(book.series) }, [onOpenSeries, book.series])

  return (
    <m.div
      ref={setRef}
      data-item={itemKey}
      // Position only: a box never stretches (that distorted the text); the
      // cover inside scales on its own (BookCard).
      layout="position"
      layoutDependency={dep}
      data-dep={dep}
      data-epoch={travelDep}
      data-held={dep === 'x' ? '' : undefined}
      variants={variants}
      initial={false}
      animate="shown"
      exit="leave"
      transition={{ layout: { duration: switching ? SWITCH_S : GLIDE_S, ease: EASE }, duration: 0.15 }}
      data-block={Math.floor(index / CHUNK)}
    >
      {stack ? (
        <SeriesStackCard
          book={book}
          count={book.series_count ?? 1}
          view={view}
          index={index}
          focused={focused}
          onOpen={openSeries}
          href={seriesPath(book.series!)}
          travel
          travelEpoch={travelDep}
        />
      ) : (
        <BookCard
          book={book}
          view={view}
          index={index}
          selected={selected}
          focused={focused}
          onSelect={onSelect}
          onTagClick={onTag}
          onSeriesClick={onSeries}
          onAuthorClick={onAuthor}
          onQuickLook={quickLook}
          travel
          travelEpoch={travelDep}
          readingStatus={status}
          progressPct={progress}
          rating={rating}
        />
      )}
    </m.div>
  )
})

// The URL BookCard shows, so the quick look starts from the cached picture.
const coverUrlOf = (book: Book) => coverSrc(book.id, { w: 400, v: book.updated_at })

interface ChunkProps extends Omit<Props, 'books' | 'focusedIndex' | 'refreshing' | 'resume' | 'staged' | 'gridSize'> {
  items: Book[]
  start: number
  focused: number | null
  win: Win
  switching: boolean
  variants: Variants
}

const Chunk = memo(function Chunk({ items, start, grouped, view, statuses, selected, selecting, focused, win, switching, variants, ...cb }: ChunkProps) {
  return (
    <AnimatePresence mode="popLayout" initial={false} presenceAffectsLayout={false}>
      {items.map((book, j) => {
        const i = start + j
        const key = String(book.id)
        const inWin = (i >= win.from && i <= win.to) || win.keys.has(key)
        const dep = inWin ? String(win.version) : 'x'
        const st = statuses[book.id]
        return (
          <LibraryItem
            key={key}
            itemKey={key}
            book={book}
            index={i}
            stack={isStack(grouped, book)}
            view={view}
            focused={focused === i}
            selected={selected.has(book.id)}
            selecting={selecting}
            status={st?.status}
            progress={st?.progress_pct}
            rating={st?.rating}
            dep={dep}
            travelDep={inWin ? String(win.travel) : 'x'}
            switching={switching && inWin}
            variants={variants}
            {...cb}
          />
        )
      })}
    </AnimatePresence>
  )
})

/** A block above the screen that has not rendered yet: one empty box per
 *  card, so the grid places the cards below exactly where they will be. */
const PlaceholderBlock = memo(function PlaceholderBlock({ count, height }: { count: number; height: number }) {
  return Array.from({ length: count }, (_, i) => <div key={i} aria-hidden="true" style={{ height }} />)
})

// ── Windowing (long lists) ─────────────────────────────────────────────────
// Past WINDOW_FROM cards only the blocks near the screen render; every other
// card is an empty box of that card's own height (measured when it last
// rendered at this width, else estimated), so the grid places rows, and the
// scroll height, as the cards would. WebKit lays out a grid of hundreds of
// cards in over 100 ms, on every change that touches it.
const WINDOW_FROM = 180
// Screens rendered above and below the visible part; a scroll that brings
// less than NEED_SCREENS of unrendered space near the screen renders at once.
const BEFORE_SCREENS = 1
const AFTER_SCREENS = 1.5
const NEED_SCREENS = 0.35

/** Card heights (content box) per card, view, slider value and column width. */
const heightCache = new Map<string, number>()
/** Per card, view and slider value: grid height minus the cover (which follows the column width); list height. */
const extraCache = new Map<string, number>()
/** The usual of the above per view and slider value, for cards never measured. */
const typicalExtra = new Map<string, number>()
/** The grid's width when it was last on screen: a remount (the way back) places its boxes with it at once. */
let lastGridWidth = 0

function columnWidth(list: boolean, gridSize: number, width: number): number {
  if (list) return width
  const gap = gridSize < 150 ? 12 : 20
  const min = Math.min(gridSize, window.innerWidth * 0.42)
  const cols = Math.max(1, Math.floor((width + gap) / (min + gap)))
  return (width - gap * (cols - 1)) / cols
}

interface Layout { view: ViewMode; gridSize: number; width: number }

/** Whether this card's height at this layout was measured (not estimated). */
function heightKnown(key: string, stack: boolean, l: Layout): boolean {
  const col = columnWidth(l.view === 'list', l.gridSize, l.width)
  return heightCache.has(`${key}|${stack ? 's' : 'b'}|${l.view}|${l.gridSize}|${Math.round(col)}`)
}

function cardHeight(key: string, stack: boolean, l: Layout): number {
  const list = l.view === 'list'
  const col = columnWidth(list, l.gridSize, l.width)
  const kind = `${stack ? 's' : 'b'}|${l.view}|${l.gridSize}`
  const exact = heightCache.get(`${key}|${kind}|${Math.round(col)}`)
  if (exact != null) return exact
  const extra = extraCache.get(`${key}|${kind}`) ?? typicalExtra.get(kind) ?? typicalExtra.get(`b|${l.view}|${l.gridSize}`) ?? (list ? 68 : 56)
  return list ? extra : col * 1.5 + extra
}

/** Reads the height of every card rendered in `grid` into the caches. */
function recordHeights(grid: HTMLElement, l: Layout) {
  const list = l.view === 'list'
  const col = columnWidth(list, l.gridSize, l.width)
  const counts = new Map<string, Map<number, number>>()
  for (const el of grid.querySelectorAll<HTMLElement>(':scope > [data-item]:not([data-motion-pop-id])')) {
    const card = el.firstElementChild as HTMLElement | null
    if (!card) continue
    // Fractional: a cover's height follows the column width.
    const h = card.getBoundingClientRect().height
    const stack = !!card.querySelector('[data-series-cover]')
    const kind = `${stack ? 's' : 'b'}|${l.view}|${l.gridSize}`
    const extra = list ? h : h - col * 1.5
    heightCache.set(`${el.dataset.item}|${kind}|${Math.round(col)}`, h)
    extraCache.set(`${el.dataset.item}|${kind}`, extra)
    const c = counts.get(kind) ?? new Map<number, number>()
    const r = Math.round(extra * 4) / 4
    c.set(r, (c.get(r) ?? 0) + 1)
    counts.set(kind, c)
  }
  for (const [kind, c] of counts) {
    let best = 0, n = 0
    for (const [v, k] of c) if (k > n) { best = v; n = k }
    typicalExtra.set(kind, best)
  }
}

/** A block not near the screen: one empty box per card, each of that card's height. */
const SpacerBlock = memo(function SpacerBlock({ heights, block }: { heights: number[]; block: number }) {
  return heights.map((h, i) => <div key={i} aria-hidden="true" data-spacer={block} style={{ height: h, boxSizing: 'content-box' }} />)
}, (a, b) => a.block === b.block && a.heights.length === b.heights.length && a.heights.every((h, i) => h === b.heights[i]))

/** The grid's items in order (cards and boxes), without the cards on their way out. */
function itemsOf(grid: HTMLElement): HTMLElement[] {
  return Array.from(grid.querySelectorAll<HTMLElement>(':scope > :not([data-motion-pop-id], [data-group-ghosts])'))
}

/** First and last item index between `lo` and `hi` (viewport px). */
function rangeIn(items: HTMLElement[], lo: number, hi: number): [number, number] {
  let a = 0, b = items.length
  while (a < b) { const mid = (a + b) >> 1; if (items[mid].getBoundingClientRect().bottom > lo) b = mid; else a = mid + 1 }
  const first = Math.min(a, items.length - 1)
  a = first; b = items.length
  while (a < b) { const mid = (a + b) >> 1; if (items[mid].getBoundingClientRect().top < hi) a = mid + 1; else b = mid }
  return [first, Math.max(first, a - 1)]
}

/** Slices of `books` per block, the same arrays as last time where nothing changed. */
function useBlocks(books: Book[]): Book[][] {
  const [state, setState] = useState<{ books: Book[]; blocks: Book[][] }>(() => ({ books, blocks: slice(books, []) }))
  if (state.books !== books) {
    const next = { books, blocks: slice(books, state.blocks) }
    setState(next)
    return next.blocks
  }
  return state.blocks
}

function slice(books: Book[], prev: Book[][]): Book[][] {
  const out: Book[][] = []
  for (let s = 0, c = 0; s < books.length; s += CHUNK, c++) {
    const part = books.slice(s, s + CHUNK)
    const old = prev[c]
    out.push(old && old.length === part.length && old.every((b, k) => b === part[k]) ? old : part)
  }
  return out
}

export const LibraryGrid = forwardRef<LibraryGridHandle, Props>(function LibraryGrid(props, handle) {
  const { books, grouped, view, gridSize, statuses, selected, selecting, focusedIndex, refreshing, resume, staged } = props
  const gridRef = useRef<HTMLDivElement | null>(null)
  // Set by prepare() in the same batch as the caller's change.
  const [win, setWin] = useState<Win>({ from: 0, to: CHUNK * 2, keys: new Set(), version: 0, travel: 0, shownFrom: 0, shownTo: CHUNK * 2 })
  const anchor = useRef<{ key: string; top: number } | null>(null)
  const [switchView, setSwitchView] = useState<{ to: 'grid' | 'list'; n: number } | null>(null)
  const shared = useRef<Shared>({ grouped, grouping: false })
  const booksRef = useRef(books)
  const groupSnap = useRef<GroupSnap | null>(null)
  const groupRun = useRef<GroupRun | null>(null)
  const variants = useMemo(() => makeVariants(shared), [])

  // The visible index range, measured on the old cards.
  const measure = useCallback(() => {
    const grid = gridRef.current
    const main = grid?.closest('main')
    if (!grid || !main) return null
    const top = main.getBoundingClientRect().top
    const bottom = top + main.clientHeight
    const items = (Array.from(grid.children) as HTMLElement[]).filter(el => !el.hasAttribute('data-group-ghosts'))
    let a = -1, b = -1
    const keys = new Set<string>()
    for (let i = 0; i < items.length; i++) {
      const r = items[i].getBoundingClientRect()
      if (r.bottom <= top + 1) continue
      if (r.top >= bottom) break
      if (a < 0) a = i
      b = i
      const k = items[i].dataset.item
      if (k) keys.add(k)
    }
    if (a < 0) { a = 0; b = 0 }
    return { a, b, keys, items, top }
  }, [])

  // Keeps the first real card on screen where it is across a commit that
  // changes cards above it (blocks catching up). Committed synchronously, so
  // no scroll lands between the two readings. What the scroll position could
  // not take (it rounds) carries over to the next step instead of adding up.
  const residual = useRef(0)
  const keepAnchor = useCallback((update: () => void) => {
    const grid = gridRef.current
    const main = grid?.closest('main')
    const m = main ? measure() : null
    let el: HTMLElement | null = null
    if (m) for (let i = m.a; i < m.items.length && !el; i++) if (m.items[i].dataset.item) el = m.items[i]
    const before = el ? el.getBoundingClientRect().top - residual.current : undefined
    flushSync(update)
    residual.current = 0
    if (!el || !main || before == null || !el.isConnected) return
    main.scrollTop += el.getBoundingClientRect().top - before
    residual.current = el.getBoundingClientRect().top - before
  }, [measure])

  // ── Windowing state (see WINDOW_FROM) ──
  const windowed = books.length > WINDOW_FROM
  const windowedRef = useRef(windowed)
  const [gridWidth, setGridWidth] = useState(lastGridWidth)
  const layout = useMemo<Layout>(() => ({ view, gridSize, width: gridWidth }), [view, gridSize, gridWidth])
  // The blocks that render (others are boxes), from..to.
  const [vwin, setVwin] = useState(() => {
    const at = resume?.index ?? 0
    return { from: Math.floor(at / CHUNK), to: Math.floor((at + screenful(view === 'list', gridSize)) / CHUNK) }
  })
  const vwinRef = useRef(vwin)
  // Until then (a remount), only the blocks on screen render.
  const holdUntil = useRef(0)
  // A change to the boxes keeps the item first on screen where it was.
  const pin = useRef<{ index: number; top: number; when: unknown } | null>(null)
  const stepTimer = useRef(0)
  const syncWindowRef = useRef<(flush: boolean) => void>(() => {})

  // The first card on screen, kept up to date while scrolling, so leaving the
  // page reads it without forcing a layout.
  const lastVisible = useRef<ResumePoint>({ index: 0, offset: 0, height: 0 })
  useEffect(() => {
    const main = gridRef.current?.closest('main')
    if (!main) return
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        syncWindowRef.current(true)
        const m = measure()
        const el = m?.items[m.a]
        if (!m || !el || !el.dataset.item) return
        const r = el.getBoundingClientRect()
        // The card's own height (the box may have room around it, see library.css).
        const h = (el.firstElementChild as HTMLElement | null)?.getBoundingClientRect().height ?? r.height
        lastVisible.current = { index: m.a, offset: r.top - m.top, height: h }
      })
    }
    onScroll()
    main.addEventListener('scroll', onScroll, { passive: true })
    return () => { cancelAnimationFrame(raf); main.removeEventListener('scroll', onScroll) }
  }, [measure])

  const layoutNow = useCallback((): Layout | null => {
    const grid = gridRef.current
    return grid ? { view, gridSize, width: grid.clientWidth } : null
  }, [view, gridSize])

  // Brings the rendered blocks to the ones near the screen: at once when the
  // screen nears a box, else a block per step.
  const syncWindow = useCallback((flush: boolean) => {
    const grid = gridRef.current
    const main = grid?.closest('main')
    if (!grid || !main || !windowedRef.current) return
    const items = itemsOf(grid)
    if (!items.length) return
    const top = main.getBoundingClientRect().top
    const h = main.clientHeight
    const hold = performance.now() < holdUntil.current
    const block = (i: number) => Math.floor(i / CHUNK)
    // While scrolling, space about to come on screen counts as on screen.
    const ahead = flush && !hold ? NEED_SCREENS : 0
    const need = rangeIn(items, top - h * ahead, top + h * (1 + ahead))
    const want = hold ? need : rangeIn(items, top - h * BEFORE_SCREENS, top + h * (1 + AFTER_SCREENS))
    const cur = vwinRef.current
    const target = { from: block(want[0]), to: block(want[1]) }
    const nf = block(need[0]), nt = block(need[1])
    let next: { from: number; to: number }
    if (nf < cur.from || nt > cur.to) {
      // At once: what is on screen (with what is already there next to it).
      next = cur.to < nf - 1 || cur.from > nt + 1
        ? { from: nf, to: nt }
        : { from: Math.max(target.from, Math.min(cur.from, nf)), to: Math.min(target.to, Math.max(cur.to, nt)) }
    } else if (hold || (cur.from === target.from && cur.to === target.to)) return
    else {
      // A step: drop what is past the target, add a block toward it.
      next = { from: Math.max(cur.from, target.from), to: Math.min(cur.to, target.to) }
      if (next.to < target.to) next.to++
      else if (next.from > target.from) next.from--
    }
    if (next.from !== target.from || next.to !== target.to) {
      clearTimeout(stepTimer.current)
      stepTimer.current = window.setTimeout(() => syncWindowRef.current(false), STEP_MS)
    }
    const l = layoutNow()
    if (l) recordHeights(grid, l)
    const [first] = rangeIn(items, top, top + 1)
    pin.current = { index: first, top: items[first].getBoundingClientRect().top, when: next }
    vwinRef.current = next
    if (flush) flushSync(() => setVwin(next))
    else setVwin(next)
  }, [layoutNow])
  useLayoutEffect(() => {
    syncWindowRef.current = syncWindow
    windowedRef.current = windowed
    vwinRef.current = vwin
  })
  useEffect(() => () => clearTimeout(stepTimer.current), [])

  useImperativeHandle(handle, () => ({
    prepare(kind, opts) {
      const l = windowedRef.current ? layoutNow() : null
      if (l) recordHeights(gridRef.current!, l)
      const grid = gridRef.current
      if (kind === 'list' && opts?.toGrouped != null && opts.toGrouped !== shared.current.grouped && grid) {
        // Grouping by series or back: GroupRun moves the cards, so they keep
        // their layout dependency (no layout animation of their own).
        const main = grid.closest('main')
        const bookOf = new Map(booksRef.current.map(b => [String(b.id), b]))
        const top = main ? main.getBoundingClientRect().top : 0
        const items = main ? snapGroup(grid, top, top + main.clientHeight, bookOf) : []
        endGroupRun(groupRun.current, grid)
        groupRun.current = null
        groupSnap.current = { toGrouped: opts.toGrouped, items }
        shared.current.grouping = true
        grid.setAttribute('data-grouping', '')
        const old = booksRef.current
        setWin(w => {
          const keys = new Set<string>()
          for (let i = 0; i < old.length; i++) {
            const k = String(old[i].id)
            if ((i >= w.from && i <= w.to) || w.keys.has(k)) keys.add(k)
          }
          return { ...w, from: -1, to: -2, keys }
        })
        setMount(s => (s.from > 0 ? { ...s, from: 0 } : s))
        return
      }
      const m = measure()
      // A view switch keeps the first card on screen in place: the new view
      // shows about a screenful of its own after it.
      let shown = m ? m.b - m.a + 1 : CHUNK * 2
      const main = gridRef.current?.closest('main')
      if (m && main && kind === 'view') {
        const height = main.clientHeight
        if (view !== 'list') shown = Math.ceil(height / LIST_ROW) + 2
        else {
          const gap = gridSize < 150 ? 12 : 20
          const cols = Math.max(1, Math.floor((gridRef.current!.clientWidth + gap) / (Math.min(gridSize, window.innerWidth * 0.42) + gap)))
          shown = cols * (Math.ceil(height / (gridSize * 1.5 + 40)) + 1)
        }
      }
      setWin(old => ({
        from: m ? m.a - MARGIN : old.from,
        to: m ? m.b + MARGIN : old.to,
        keys: m && kind !== 'select' ? m.keys : old.keys,
        version: old.version + (kind === 'select' ? 0 : 1),
        travel: old.travel + (kind === 'view' ? 1 : 0),
        shownFrom: m ? m.a : old.shownFrom,
        shownTo: m ? m.a + shown : old.shownTo,
      }))
      if (kind === 'view' && m && windowedRef.current) {
        // The blocks the new view shows on screen render with the switch.
        const from = Math.floor(m.a / CHUNK), to = Math.floor((m.a + shown) / CHUNK)
        setVwin(v => (v.from <= from && v.to >= to ? v : { from: Math.min(v.from, from), to: Math.max(v.to, to) }))
      }
      if (kind === 'select') return
      // A new list before the way back has filled in: everything renders.
      if (kind === 'list') setMount(s => (s.from > 0 ? { ...s, from: 0 } : s))
      if (kind === 'view' && m) {
        const first = m.items[m.a]
        anchor.current = first?.dataset.item ? { key: first.dataset.item, top: first.getBoundingClientRect().top - m.top } : null
      }
    },
    resumePoint() {
      return lastVisible.current
    },
  }), [measure, view, gridSize, layoutNow])

  useInsertionEffect(() => {
    booksRef.current = books
    shared.current.grouped = grouped
  }, [grouped, books])

  // ── Blocks that follow later ───────────────────────────────────────────
  const listView = view === 'list'
  const [shown, setShown] = useState({ view, selecting, selected })
  // Blocks outside `from`..`upTo` (above and below the screen) still show the
  // view, selection mode and selection of before the change; select all /
  // none or a view switch re-renders every card otherwise.
  const [lag, setLag] = useState<{ view: ViewMode; selecting: boolean; selected: Set<number>; from: number; upTo: number; delay: number } | null>(null)
  // Coming back: blocks `from`..`upTo` render, the ones above are boxes, the
  // ones below are not there yet.
  const [mount, setMount] = useState(() => {
    const from = resume ? Math.floor(resume.index / CHUNK) : 0
    // WebKit takes about 1 ms a card to lay out: it starts with a screenful.
    const first = SKIP_HIDDEN ? 2 : Math.max(1, Math.ceil(screenful(view === 'list', gridSize) / CHUNK))
    const stage = !!staged && books.length > STAGE_FROM
    // A windowed grid never steps the mount: should its list drop below
    // WINDOW_FROM, every block renders at once.
    if (windowed) return { from: 0, upTo: Infinity, settled: true }
    return { from, upTo: resume || stage ? from + first - 1 : Infinity, settled: !resume && !stage }
  })
  if (shown.selected !== selected && shown.view === view && shown.selecting === selecting) setShown({ view, selecting, selected })
  if (shown.view !== view || shown.selecting !== selecting) {
    const viewChanged = shown.view !== view
    setShown({ view, selecting, selected })
    setLag({
      view: lag ? lag.view : shown.view,
      selecting: lag ? lag.selecting : shown.selecting,
      selected: lag ? lag.selected : shown.selected,
      from: Math.floor(Math.max(win.shownFrom, 0) / CHUNK),
      upTo: Math.floor(Math.max(win.shownTo, 0) / CHUNK),
      delay: viewChanged ? SWITCH_SETTLE_MS : SELECT_SETTLE_MS,
    })
    if (viewChanged) setSwitchView(s => ({ to: listView ? 'list' : 'grid', n: (s?.n ?? 0) + 1 }))
  }
  useEffect(() => {
    if (!switchView) return
    const id = setTimeout(() => setSwitchView(null), SWITCH_S * 1000 + 120)
    return () => clearTimeout(id)
  }, [switchView])

  const blocks = useBlocks(books)
  const lastBlock = blocks.length - 1
  // A list that grows past WINDOW_FROM: the blocks around the screen stay.
  const [wasWindowed, setWasWindowed] = useState(windowed)
  if (wasWindowed !== windowed) {
    setWasWindowed(windowed)
    if (windowed) {
      if (mount.upTo !== Infinity || mount.from > 0) setMount({ from: 0, upTo: Infinity, settled: true })
      const at = lastVisible.current.index
      setVwin({ from: Math.max(0, Math.floor(at / CHUNK) - 1), to: Math.floor((at + screenful(listView, gridSize)) / CHUNK) + 1 })
    }
  }
  // Heights of the cards before the list turns windowed (at most two pages on).
  useLayoutEffect(() => {
    const grid = gridRef.current
    const l = layoutNow()
    if (!windowed && !lag && grid && l && books.length > WINDOW_FROM - 2 * CHUNK) recordHeights(grid, l)
    // Once per list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [books])
  // Blocks behind a view switch: in a windowed list they are boxes until the
  // switch is over, then render one per step.
  const lagLo = windowed ? Math.max(0, vwin.from) : 0
  const lagHi = windowed ? Math.min(vwin.to, lastBlock) : lastBlock
  // One block per step: below the screen first, then above it.
  useEffect(() => {
    if (!lag) return
    if (lag.upTo >= lagHi && lag.from <= lagLo) { setLag(null); return }
    const id = setTimeout(() => {
      if (lag.upTo < lagHi) setLag(l => l && { ...l, upTo: l.upTo + 1, delay: STEP_MS })
      else keepAnchor(() => setLag(l => l && { ...l, from: l.from - 1, delay: STEP_MS }))
    }, lag.delay)
    return () => clearTimeout(id)
  }, [lag, lagLo, lagHi, keepAnchor])
  useEffect(() => {
    if (windowed) return
    if (mount.upTo >= lastBlock && mount.from <= 0) return
    const id = setTimeout(() => {
      if (mount.upTo < lastBlock) setMount(s => ({ ...s, upTo: s.upTo + 1, settled: true }))
      else keepAnchor(() => setMount(s => ({ ...s, from: s.from - 1, settled: true })))
    }, mount.settled ? STEP_MS : MOUNT_SETTLE_MS)
    return () => clearTimeout(id)
  }, [mount, lastBlock, keepAnchor, windowed])

  // Coming back: the card that was first on screen goes where it was.
  useLayoutEffect(() => {
    const grid = gridRef.current
    const main = grid?.closest('main')
    const book = resume ? books[resume.index] : undefined
    if (!resume || !book || !grid || !main) return
    const el = grid.querySelector<HTMLElement>(`[data-item="${CSS.escape(String(book.id))}"]`)
    if (!el) return
    const top = main.getBoundingClientRect().top + resume.offset
    main.scrollTop += el.getBoundingClientRect().top - top
    residual.current = el.getBoundingClientRect().top - top
    // Only on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // The view switch keeps the first card on screen where it was.
  useLayoutEffect(() => {
    const a = anchor.current
    anchor.current = null
    const grid = gridRef.current
    const main = grid?.closest('main')
    if (!a || !grid || !main) return
    const el = grid.querySelector<HTMLElement>(`[data-item="${CSS.escape(a.key)}"]`)
    if (el) main.scrollTop = el.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop - a.top
  }, [view])

  // ── Windowing effects ──
  // A remount (the way back, a list shown again): the blocks on screen
  // first, the ones around them once the page has settled.
  useLayoutEffect(() => {
    holdUntil.current = performance.now() + MOUNT_SETTLE_MS
    const id = setTimeout(() => syncWindowRef.current(false), MOUNT_SETTLE_MS + 20)
    return () => clearTimeout(id)
  }, [])
  // The grid's width sets the boxes' heights (the cards' text wraps with it).
  useLayoutEffect(() => {
    const grid = gridRef.current
    if (!grid) return
    const read = () => {
      const w = grid.clientWidth
      if (grid.closest('main')) lastGridWidth = w
      setGridWidth(old => {
        if (old === w) return old
        const main = grid.closest('main')
        if (old && main && windowedRef.current) {
          const items = itemsOf(grid)
          const [first] = rangeIn(items, main.getBoundingClientRect().top, main.getBoundingClientRect().top + 1)
          if (items[first]) pin.current = { index: first, top: items[first].getBoundingClientRect().top, when: w }
        }
        return w
      })
    }
    read()
    const ro = new ResizeObserver(read)
    ro.observe(grid)
    return () => ro.disconnect()
  }, [])
  // A new list, page or width: the blocks near the screen. A view switch
  // already holds the blocks it shows (prepare); the rest follow after it.
  useLayoutEffect(() => {
    if (windowed) syncWindow(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [books, windowed, gridSize, grouped, gridWidth])
  useEffect(() => {
    if (!windowed) return
    const id = setTimeout(() => syncWindowRef.current(false), SWITCH_SETTLE_MS)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view])
  // Cards whose height at this layout is an estimate (a view or width they
  // never rendered at): once the page is idle, their blocks render one at a
  // time, out of sight, to be measured, so the boxes match them after.
  const [probe, setProbe] = useState<number | null>(null)
  const idleBusy = !windowed || !!lag || !!switchView || gridWidth === 0
  useEffect(() => {
    if (idleBusy) return
    return whenIdle(() => {
      const grid = gridRef.current
      const main = grid?.closest('main')
      if (!grid || !main) return
      if (probe != null) recordHeights(grid, layout)
      const v = vwinRef.current
      let next: number | null = null
      for (let c = 0; c < blocks.length && next == null; c++) {
        if (c >= v.from && c <= v.to) continue
        if (blocks[c].some(b => !heightKnown(String(b.id), isStack(grouped, b), layout))) next = c
      }
      if (next === probe) return
      const items = itemsOf(grid)
      const top = main.getBoundingClientRect().top
      const [first] = rangeIn(items, top, top + 1)
      if (items[first]) pin.current = { index: first, top: items[first].getBoundingClientRect().top, when: next ?? -1 }
      flushSync(() => setProbe(next))
    }, probe == null ? 600 : STEP_MS)
  }, [idleBusy, probe, blocks, layout, grouped])

  // The keyboard's focus moved to a card that is a box: scroll to it, render it.
  useLayoutEffect(() => {
    const grid = gridRef.current
    if (!windowed || focusedIndex == null || !grid) return
    const c = Math.floor(focusedIndex / CHUNK)
    if (c >= vwinRef.current.from && c <= vwinRef.current.to) return
    const el = itemsOf(grid)[focusedIndex]
    if (!el) return
    el.scrollIntoView({ block: 'nearest' })
    holdUntil.current = 0
    syncWindow(false)
    // Only when the focus moves.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedIndex])
  // Keeps the item first on screen in place across a change of boxes.
  useLayoutEffect(() => {
    const p = pin.current
    // Applies on the commit of the change that set it.
    if (!p || (p.when !== vwin && p.when !== gridWidth && p.when !== (probe ?? -1))) return
    pin.current = null
    const grid = gridRef.current
    const main = grid?.closest('main')
    if (!grid || !main) return
    const el = itemsOf(grid)[p.index]
    if (el) main.scrollTop += el.getBoundingClientRect().top - p.top
  })

  // ── Grouping by series and back (see the top of the file) ──
  // On the commit that shows the new list: keep the first card on screen (or
  // its series' stack) where it was, read every card that takes part, then
  // start the motion. All reads before any write.
  useLayoutEffect(() => {
    const snap = groupSnap.current
    if (!snap || snap.toGrouped !== grouped) return
    groupSnap.current = null
    const grid = gridRef.current
    const main = grid?.closest('main')
    const run: GroupRun = { anims: [], layer: null, marked: [], timer: 0 }
    const finish = () => {
      endGroupRun(run, grid ?? null)
      if (groupRun.current === run) groupRun.current = null
      shared.current.grouping = false
    }
    groupRun.current = run
    if (!grid || !main) { run.timer = window.setTimeout(finish, 60); return }
    const bookOf = new Map(books.map(b => [String(b.id), b]))
    // The cards that leave are still here (their exit starts after this).
    const els = Array.from(grid.querySelectorAll<HTMLElement>(':scope > [data-item]:not([data-motion-pop-id])')).filter(el => bookOf.has(el.dataset.item!))
    const byKey = new Map(els.map(el => [el.dataset.item!, el]))
    const stackKey = new Map<string, string>()
    if (grouped) for (const b of books) if (isStack(true, b)) stackKey.set(b.series!, String(b.id))
    const stackEl = (series: string | null) => (grouped && series ? byKey.get(stackKey.get(series) ?? '') : undefined)

    // The first card on screen stays where it was (grouping: its series' stack).
    const order = snap.items.filter(it => !it.ghost).sort((a, b) => a.card.top - b.card.top || a.card.left - b.card.left)
    for (const it of order) {
      const el = byKey.get(it.key) ?? stackEl(it.series)
      if (!el) continue
      const d = el.getBoundingClientRect().top - it.card.top
      if (Math.abs(d) >= 1) main.scrollTop += d
      break
    }
    if (reduced() || !snap.items.length) { run.timer = window.setTimeout(finish, 60); return }

    const top = main.getBoundingClientRect().top
    const h = main.clientHeight
    const near = (r: DOMRect) => r.bottom > top - h / 2 && r.top < top + h * 1.5
    const gridBox = grid.getBoundingClientRect()
    const snapOf = new Map(snap.items.map(it => [it.key, it]))
    // Ungrouping: the stacks that were on screen, by series.
    const stackFrom = new Map<string, GroupItem>()
    for (const it of snap.items) if (it.stack && it.series) stackFrom.set(it.series, it)

    const moves: { el: HTMLElement; from: DOMRect; to: DOMRect; card: DOMRect; emerge: boolean; top: boolean }[] = []
    for (const el of els) {
      const key = el.dataset.item!
      const old = snapOf.get(key)
      const book = bookOf.get(key)
      const from = old ? old.cover : !grouped && book?.series ? stackFrom.get(book.series)?.cover : undefined
      if (!from || !book) continue
      const card = el.getBoundingClientRect()
      if (!old && !near(card)) continue
      const box = coverOf(el)
      moves.push({
        el, from, card,
        to: box ? box.getBoundingClientRect() : card,
        emerge: !old || old.ghost,
        top: !!old && !old.ghost && old.stack !== isStack(grouped, book),
      })
    }
    const leaving = snap.items.filter(it => !byKey.has(it.key)).map(it => {
      const target = stackEl(it.series)
      const box = target ? coverOf(target) ?? target : null
      const to = box ? box.getBoundingClientRect() : null
      return { it, to: to && near(to) ? to : null }
    })

    const timing = { duration: GROUP_MS, easing: EASE_CSS }
    for (const mv of moves) {
      const dx = mv.from.left - mv.to.left
      const dy = mv.from.top - mv.to.top
      const k = mv.to.width > 0 ? mv.from.width / mv.to.width : 1
      if (!mv.emerge && Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(k - 1) < 0.01) continue
      // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS
      const origin = `${mv.to.left - mv.card.left}px ${mv.to.top - mv.card.top}px`
      if (mv.emerge) mv.el.setAttribute('data-emerge', '')
      if (mv.top) mv.el.setAttribute('data-group-top', '')
      if (mv.emerge || mv.top) run.marked.push(mv.el)
      run.anims.push(mv.el.animate([
        { transform: `translate(${dx}px, ${dy}px) scale(${k})`, transformOrigin: origin },
        { transform: 'none', transformOrigin: origin },
      ], timing))
    }
    if (leaving.length) {
      // The volumes that leave, as bare covers under the cards.
      const layer = document.createElement('div')
      layer.setAttribute('data-group-ghosts', '')
      layer.setAttribute('aria-hidden', 'true')
      const ghosts = leaving.map(({ it }) => {
        const g = document.createElement('div')
        g.setAttribute('data-ghost', it.key)
        if (it.series) g.setAttribute('data-series', it.series)
        Object.assign(g.style, {
          left: `${it.cover.left - gridBox.left}px`, top: `${it.cover.top - gridBox.top}px`,
          width: `${it.cover.width}px`, height: `${it.cover.height}px`,
          borderRadius: it.radius, backgroundColor: it.color,
        })
        if (it.img?.currentSrc || it.img?.src) {
          const img = document.createElement('img')
          img.src = it.img.currentSrc || it.img.src
          img.alt = ''
          g.append(img)
        }
        layer.append(g)
        return g
      })
      grid.append(layer)
      run.layer = layer
      leaving.forEach(({ it, to }, i) => {
        const g = ghosts[i]
        if (!to) {
          run.anims.push(g.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 150, easing: 'linear', fill: 'forwards' }))
          return
        }
        const k = it.cover.width > 0 ? to.width / it.cover.width : 1
        run.anims.push(g.animate([
          { transform: 'none' },
          { transform: `translate(${to.left - it.cover.left}px, ${to.top - it.cover.top}px) scale(${k})` },
        ], { ...timing, fill: 'forwards' }))
        // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS easing
        run.anims.push(g.animate([{ opacity: 1 }, { opacity: 0 }], { duration: GROUP_MS, easing: 'cubic-bezier(0.4, 0, 1, 1)', fill: 'forwards' }))
      })
    }
    run.timer = window.setTimeout(finish, GROUP_MS + 40)
  }, [books, grouped])
  useEffect(() => () => endGroupRun(groupRun.current, null), [])

  // Once per page load, when the page is idle: see LibraryWarmup.
  const [warm, setWarm] = useState(false)
  const endWarm = useCallback(() => setWarm(false), [])
  const hasBooks = books.length > 0
  useEffect(() => {
    if (warmedUp || !hasBooks || reduced()) return
    return whenIdle(() => { warmedUp = true; setWarm(true) }, 1200)
  }, [hasBooks])

  // A card in a block still in the other view is an empty box of about the
  // size it will have. One rule for all of them: those blocks keep their props
  // and do not render again.
  const gridId = useId()
  let lagRule = ''
  if (lag && !windowed && (lag.view === 'list') !== listView) {
    const behind: string[] = []
    for (let c = 0; c <= Math.min(lastBlock, mount.upTo); c++) if (c < lag.from || c > lag.upTo) behind.push(`[data-block="${c}"]`)
    /* eslint-disable lingui/no-unlocalized-strings -- CSS */
    const height = listView ? 69 : Math.round(gridSize * 1.5 + 64)
    const box = SKIP_HIDDEN
      ? `content-visibility: hidden; contain-intrinsic-size: auto ${height}px;`
      : 'display: none;'
    if (behind.length) lagRule = `[data-library-grid="${gridId}"] > :is(${behind.join(',')}) { ${box} }`
    /* eslint-enable lingui/no-unlocalized-strings */
  }

  const gridClass = listView
    // eslint-disable-next-line lingui/no-unlocalized-strings -- Tailwind classes
    ? 'relative flex flex-col divide-y divide-border/70'
    : cn('relative grid transition-[gap] duration-300 ease-out', gridSize < 150 ? 'gap-x-3 gap-y-5' : 'gap-x-5 gap-y-7')
  // min(size, 42vw) keeps phones at two columns regardless of the slider.
  const gridStyle = listView
    ? undefined
    // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS grid template
    : { gridTemplateColumns: `repeat(auto-fill, minmax(min(${gridSize}px, 42vw), 1fr))` }

  return (
    <>
      {warm && <LibraryWarmup sample={books} gridSize={gridSize} onDone={endWarm} />}
      {lagRule && <style>{lagRule}</style>}
      <div
        ref={gridRef}
        data-library-grid={gridId}
        data-view-switch={switchView?.to}
        className={cn(gridClass, 'transition-opacity duration-150', refreshing && 'opacity-50')}
        style={gridStyle}
      >
        {blocks.map((items, c) => {
          const behind = !!lag && (c > lag.upTo || c < lag.from)
          if (windowed) {
            if ((c < vwin.from || c > vwin.to) && c !== probe || (behind && (lag.view === 'list') !== listView)) {
              return <SpacerBlock key={c} block={c} heights={items.map(b => cardHeight(String(b.id), isStack(grouped, b), layout))} />
            }
          } else {
            if (c > mount.upTo) return null
            if (c < mount.from) return <PlaceholderBlock key={c} count={items.length} height={resume?.height ?? 0} />
          }
          // A block with no card in the window gets the same props as before,
          // so a view switch or a selection change does not render it again.
          const inWin = (c * CHUNK <= win.to && c * CHUNK + items.length - 1 >= win.from) || (win.keys.size > 0 && items.some(b => win.keys.has(String(b.id))))
          const blockView = behind ? lag.view : view
          return (
            <Chunk
              key={c}
              items={items}
              start={c * CHUNK}
              grouped={grouped}
              view={blockView}
              statuses={statuses}
              selected={behind ? lag.selected : selected}
              selecting={behind ? lag.selecting : selecting}
              focused={focusedIndex != null && focusedIndex >= c * CHUNK && focusedIndex < (c + 1) * CHUNK ? focusedIndex : null}
              win={inWin ? win : OUT_OF_WIN}
              switching={!!switchView && inWin}
              variants={variants}
              onToggle={props.onToggle}
              onTag={props.onTag}
              onSeries={props.onSeries}
              onAuthor={props.onAuthor}
              onQuickLook={props.onQuickLook}
              onOpenSeries={props.onOpenSeries}
            />
          )
        })}
      </div>
    </>
  )
})

// The first view switch or grouping after a page load used to pay for running
// the cards' and the layout animation's code for the first time (20 to 40 ms
// on top of the change itself). Once the page is idle, a hidden grid of a few
// blank cards off screen makes the same moves, so that cost is paid before the
// first click. Once per page load.
let warmedUp = false

/** Runs `fn` after `delayMs`, at the next idle moment. Returns a cancel. */
function whenIdle(fn: () => void, delayMs: number): () => void {
  let idle = 0
  const t = setTimeout(() => {
    if (typeof requestIdleCallback === 'function') idle = requestIdleCallback(fn, { timeout: 1500 })
    else fn()
  }, delayMs)
  return () => { clearTimeout(t); if (idle) cancelIdleCallback(idle) }
}

const NOOP = () => {}
const NO_STATUS: StatusMap = {}
const NO_SELECTION = new Set<number>()
// Each step waits for the previous one's motion to end.
const WARM_STEP_MS = SWITCH_S * 1000 + 120
// grid -> grouped -> ungrouped -> list -> grid
const WARM_PLAN: { kind: PrepareKind; grouped?: boolean }[] = [
  { kind: 'list', grouped: true }, { kind: 'list', grouped: false }, { kind: 'view' }, { kind: 'view' },
]

function LibraryWarmup({ sample, gridSize, onDone }: { sample: Book[]; gridSize: number; onDone: () => void }) {
  const ref = useRef<LibraryGridHandle | null>(null)
  const [step, setStep] = useState(0)
  // Blank cards: no title, author, cover or file (nothing to read, match or load).
  const [lists] = useState(() => {
    const blank = (b: Book, i: number): Book => ({
      ...b, id: -1 - i, title: '', subtitle: null, author: null, year: null,
      series: i < 4 ? '⁠' : null, series_index: i < 4 ? i + 1 : null,
      status: 'active', cover_path: null, files: [], tags: [], book_type_id: null,
      series_count: null, stack_cover_ids: null,
    })
    const flat = sample.slice(0, CHUNK).map(blank)
    return { flat, grouped: [{ ...flat[0], series_count: 4 }, ...flat.slice(4)] }
  })
  const grouped = step === 1
  const list = step === 3
  useEffect(() => {
    if (step >= WARM_PLAN.length) return whenIdle(onDone, WARM_STEP_MS)
    const next = WARM_PLAN[step]
    return whenIdle(() => {
      ref.current?.prepare(next.kind, next.grouped != null ? { toGrouped: next.grouped } : undefined)
      setStep(s => s + 1)
    }, WARM_STEP_MS)
  }, [step, onDone])
  return createPortal(
    <div
      aria-hidden="true"
      inert
      style={{ position: 'fixed', top: 0, left: -10000, width: 1000, visibility: 'hidden', pointerEvents: 'none', contain: 'layout paint' }}
    >
      <LibraryGrid
        ref={ref}
        books={grouped ? lists.grouped : lists.flat}
        grouped={grouped}
        view={list ? 'list' : gridSize < 150 ? 'small' : 'large'}
        gridSize={gridSize}
        statuses={NO_STATUS}
        selected={NO_SELECTION}
        selecting={false}
        focusedIndex={null}
        refreshing={false}
        onToggle={NOOP}
        onTag={NOOP}
        onSeries={NOOP}
        onAuthor={NOOP}
        onQuickLook={NOOP}
        onOpenSeries={NOOP}
      />
    </div>,
    document.body,
  )
}

/** The grid before its first list: the cards' shapes, so the cards land in place. */
export function LibrarySkeleton({ list, gridSize, count = 18 }: { list: boolean; gridSize: number; count?: number }) {
  if (list) {
    return (
      <div aria-busy="true" className="flex flex-col divide-y divide-border/70 animate-pulse">
        {Array.from({ length: 10 }, (_, i) => (
          <div key={i} className="flex items-center gap-3 px-3 py-2.5">
            <div className="w-9 h-12 rounded bg-muted shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="h-3.5 w-1/3 rounded bg-muted" />
              <div className="mt-2 h-3 w-1/5 rounded bg-muted" />
            </div>
          </div>
        ))}
      </div>
    )
  }
  return (
    <div
      aria-busy="true"
      className={cn('grid animate-pulse', gridSize < 150 ? 'gap-x-3 gap-y-5' : 'gap-x-5 gap-y-7')}
      style={{ gridTemplateColumns: `repeat(auto-fill, minmax(min(${gridSize}px, 42vw), 1fr))` }}
    >
      {Array.from({ length: count }, (_, i) => (
        <div key={i}>
          <div className="aspect-[2/3] rounded-[8px] bg-muted" />
          <div className="mt-2.5 h-3.5 w-4/5 rounded bg-muted" />
          <div className="mt-2 h-3 w-1/2 rounded bg-muted" />
        </div>
      ))}
    </div>
  )
}
