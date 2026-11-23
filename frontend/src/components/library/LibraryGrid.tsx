/**
 * The Library's cards, grid or list, and how they move.
 *
 * Only the cards around the screen take part in a change: a list change
 * (sort, filter, search, grouping), the grid/list switch and the cover-size
 * slider measure and animate the cards near the screen and let the rest snap
 * where nobody sees them. The caller calls `prepare()` on the handle right
 * before the state change, while the old cards are still in place.
 *
 * The cards render in blocks of 60 (the page size), each block its own
 * AnimatePresence: a new list that replaces hundreds of loaded cards removes
 * the blocks past the first at once instead of fading cards nobody sees.
 * Blocks below the screen follow a view switch or selection mode a moment
 * after the visible ones, so the visible part moves without a long frame.
 *
 * A list change: the old cards stay (dimmed by the caller) until the new list
 * is in. Cards in both lists glide to their new place; the ones that leave
 * fade out over the ones that arrive (`data-motion-pop-id` is on top, see
 * library.css), so the grid cross-changes instead of blinking. Grouping by
 * series: the stack takes the first volume's card, which glides to the
 * stack's place, and the other volumes on screen fly into it. Ungrouping: the
 * volumes come out of the stack they were in.
 */
import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useInsertionEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react'
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
// A remount (back to the Library) shows the blocks around the place it
// returns to first, the rest one block per step after.
const MOUNT_SETTLE_MS = 380

export type PrepareKind = 'view' | 'list' | 'size' | 'select'

export interface LibraryGridHandle {
  /** Right before a state change that moves the cards, while the old ones are on screen. */
  prepare(kind: PrepareKind, opts?: { toGrouped?: boolean }): void
  /** Index of the first card on screen (for the way back). */
  firstVisible(): number
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
  /** The first card on screen when the list was last left: its block and the
   *  next render at once, the others after. */
  resumeAt?: number
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
interface Win { from: number; to: number; keys: Set<string>; version: number; travel: number }

/** Shared between the grid and its cards without re-rendering them. */
interface Shared {
  grid: RefObject<HTMLDivElement | null>
  /** Grouped list: series name -> the key of its stack. */
  stackOf: Map<string, string>
  grouped: boolean
  /** Ungrouping: where each series' stack was on screen. */
  spreadFrom: Map<string, DOMRect> | null
}

type Exit = { key: string; series: string | null }

function reduced() {
  // eslint-disable-next-line lingui/no-unlocalized-strings -- media query
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function makeVariants(ref: RefObject<Shared>): Variants {
  const fade = { opacity: 0, transition: { duration: 0.15 } }
  return {
    shown: { opacity: 1, x: 0, y: 0, scale: 1 },
    // A volume whose series is now a stack flies into it; any other card
    // fades where it was.
    leave: (c: Exit) => {
      const shared = ref.current
      const target = shared.grouped && c.series ? shared.stackOf.get(c.series) : undefined
      const grid = shared.grid.current
      if (!target || target === c.key || !grid || reduced()) return fade
      const el = grid.querySelector<HTMLElement>(`[data-item="${CSS.escape(c.key)}"][data-motion-pop-id]`)
      const to = grid.querySelector<HTMLElement>(`[data-item="${CSS.escape(target)}"]:not([data-motion-pop-id])`)
      if (!el || !to) return fade
      const x = to.offsetLeft - el.offsetLeft
      const y = to.offsetTop - el.offsetTop
      if (Math.abs(y) > window.innerHeight * 1.5) return fade
      return { x, y, scale: 0.86, opacity: 0, transition: { duration: GLIDE_S, ease: EASE, opacity: { duration: GLIDE_S, ease: [0.4, 0, 1, 1] } } }
    },
  }
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
  /** Below the screen, still in the old view for a moment: drawn as an empty box. */
  lagging: string | null
  shared: RefObject<Shared>
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
  dep, travelDep, switching, lagging, shared, variants,
  onToggle, onTag, onSeries, onAuthor, onQuickLook, onOpenSeries,
}: ItemProps) {
  const ref = useRef<HTMLDivElement | null>(null)
  const setRef = useCallback((node: HTMLDivElement | null) => {
    ref.current = node
    if (typeof presenceRef === 'function') presenceRef(node)
    else if (presenceRef) presenceRef.current = node
  }, [presenceRef])
  const custom = useMemo<Exit>(() => ({ key: itemKey, series: book.series ?? null }), [itemKey, book.series])
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

  // Ungrouping: a volume that was inside a stack on screen comes out of it.
  useLayoutEffect(() => {
    const spread = shared.current.spreadFrom
    const from = spread && book.series ? spread.get(book.series) : undefined
    const el = ref.current?.firstElementChild as HTMLElement | null
    if (!from || !el || reduced()) return
    const r = el.getBoundingClientRect()
    if (r.bottom < 0 || r.top > window.innerHeight || (Math.abs(r.left - from.left) < 1 && Math.abs(r.top - from.top) < 1)) return
    /* eslint-disable lingui/no-unlocalized-strings -- CSS values */
    el.animate([
      { transform: `translate(${from.left - r.left}px, ${from.top - r.top}px) scale(0.86)`, opacity: 0.5 },
      { transform: 'none', opacity: 1 },
    ], { duration: GLIDE_S * 1000, easing: EASE_CSS })
    /* eslint-enable lingui/no-unlocalized-strings */
    // Only on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
      custom={custom}
      variants={variants}
      initial={false}
      animate="shown"
      exit="leave"
      transition={{ layout: { duration: switching ? SWITCH_S : GLIDE_S, ease: EASE }, duration: 0.15 }}
      className={cn(lagging && 'library-lag')}
      style={lagging ? { containIntrinsicSize: lagging } : undefined}
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

interface ChunkProps extends Omit<Props, 'books' | 'focusedIndex' | 'refreshing' | 'resumeAt' | 'gridSize'> {
  items: Book[]
  start: number
  focused: number | null
  win: Win
  switching: boolean
  lagging: string | null
  shared: RefObject<Shared>
  variants: Variants
}

const Chunk = memo(function Chunk({ items, start, grouped, view, statuses, selected, selecting, focused, win, switching, lagging, shared, variants, ...cb }: ChunkProps) {
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
            lagging={lagging}
            shared={shared}
            variants={variants}
            {...cb}
          />
        )
      })}
    </AnimatePresence>
  )
})

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
  const { books, grouped, view, gridSize, statuses, selected, selecting, focusedIndex, refreshing, resumeAt } = props
  const gridRef = useRef<HTMLDivElement | null>(null)
  // Set by prepare() in the same batch as the caller's change.
  const [win, setWin] = useState<Win>({ from: 0, to: CHUNK * 2, keys: new Set(), version: 0, travel: 0 })
  const anchor = useRef<{ key: string; top: number } | null>(null)
  const [switchView, setSwitchView] = useState<{ to: 'grid' | 'list'; n: number } | null>(null)
  const shared = useRef<Shared>({ grid: gridRef, stackOf: new Map(), grouped, spreadFrom: null })
  // eslint-disable-next-line react-hooks/refs -- read when a card leaves, not while rendering
  const variants = useMemo(() => makeVariants(shared), [])

  // The visible index range, measured on the old cards.
  const measure = useCallback(() => {
    const grid = gridRef.current
    const main = grid?.closest('main')
    if (!grid || !main) return null
    const top = main.getBoundingClientRect().top
    const bottom = top + main.clientHeight
    const items = Array.from(grid.children) as HTMLElement[]
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

  // The first card on screen, kept up to date while scrolling, so leaving the
  // page reads it without forcing a layout.
  const lastVisible = useRef(0)
  useEffect(() => {
    const main = gridRef.current?.closest('main')
    if (!main) return
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => { raf = 0; lastVisible.current = measure()?.a ?? 0 })
    }
    onScroll()
    main.addEventListener('scroll', onScroll, { passive: true })
    return () => { cancelAnimationFrame(raf); main.removeEventListener('scroll', onScroll) }
  }, [measure])

  useImperativeHandle(handle, () => ({
    prepare(kind, opts) {
      const m = measure()
      setWin(old => ({
        from: m ? m.a - MARGIN : old.from,
        to: m ? m.b + MARGIN : old.to,
        keys: m && kind !== 'select' ? m.keys : old.keys,
        version: old.version + (kind === 'select' ? 0 : 1),
        travel: old.travel + (kind === 'view' ? 1 : 0),
      }))
      if (kind === 'select') return
      if (kind === 'view' && m) {
        const first = m.items[m.a]
        anchor.current = first?.dataset.item ? { key: first.dataset.item, top: first.getBoundingClientRect().top - m.top } : null
      }
      if (kind === 'list') {
        // Ungrouping: remember where the stacks on screen are.
        shared.current.spreadFrom = null
        if (opts?.toGrouped === false && shared.current.grouped && m) {
          const from = new Map<string, DOMRect>()
          for (const el of m.items) {
            const k = el.dataset.item
            if (!k || !m.keys.has(k)) continue
            const cover = el.querySelector<HTMLElement>('[data-series-cover]')
            const name = cover?.dataset.seriesCover
            if (name) from.set(name, el.getBoundingClientRect())
          }
          if (from.size) shared.current.spreadFrom = from
        }
      }
    },
    firstVisible() {
      return lastVisible.current
    },
  }), [measure])

  // What the exits read while the new list commits: set before any card's
  // layout effect (where the exits start) runs.
  useInsertionEffect(() => {
    shared.current.grouped = grouped
    if (!grouped) return
    const map = new Map<string, string>()
    for (const b of books) if (isStack(true, b)) map.set(b.series!, String(b.id))
    shared.current.stackOf = map
  }, [grouped, books])
  useLayoutEffect(() => { shared.current.spreadFrom = null })

  // ── Blocks that follow later ───────────────────────────────────────────
  const listView = view === 'list'
  const [shown, setShown] = useState({ view, selecting, selected })
  // Blocks past `upTo` still show the view, selection mode and selection of
  // before the change; select all / none re-renders every card otherwise.
  const [lag, setLag] = useState<{ view: ViewMode; selecting: boolean; selected: Set<number>; upTo: number; delay: number } | null>(null)
  const [firstMount] = useState(() => resumeAt != null ? Math.floor(resumeAt / CHUNK) + 1 : Infinity)
  const [mountedUpTo, setMountedUpTo] = useState(firstMount)
  if (shown.selected !== selected && shown.view === view && shown.selecting === selecting) setShown({ view, selecting, selected })
  if (shown.view !== view || shown.selecting !== selecting) {
    const viewChanged = shown.view !== view
    setShown({ view, selecting, selected })
    setLag({
      view: lag ? lag.view : shown.view,
      selecting: lag ? lag.selecting : shown.selecting,
      selected: lag ? lag.selected : shown.selected,
      upTo: Math.max(0, Math.floor(Math.max(win.to, 0) / CHUNK)),
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
  useEffect(() => {
    if (!lag) return
    if (lag.upTo >= lastBlock) { setLag(null); return }
    const id = setTimeout(() => setLag(l => l && { ...l, upTo: l.upTo + 1, delay: STEP_MS }), lag.delay)
    return () => clearTimeout(id)
  }, [lag, lastBlock])
  useEffect(() => {
    if (mountedUpTo >= lastBlock) return
    const id = setTimeout(() => setMountedUpTo(n => n + 1), mountedUpTo === firstMount ? MOUNT_SETTLE_MS : STEP_MS)
    return () => clearTimeout(id)
  }, [mountedUpTo, lastBlock, firstMount])

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

  // A lagging card is an empty box of about the size it will have.
  // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS value
  const lagSize = listView ? 'auto 69px' : `auto ${Math.round(gridSize * 1.5 + 64)}px`

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
    <div
      ref={gridRef}
      data-view-switch={switchView?.to}
      className={cn(gridClass, 'transition-opacity duration-150', refreshing && 'opacity-50')}
      style={gridStyle}
    >
      {blocks.map((items, c) => {
        if (c > mountedUpTo) return null
        const behind = !!lag && c > lag.upTo
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
            win={win}
            switching={!!switchView}
            lagging={behind && (blockView === 'list') !== listView ? lagSize : null}
            shared={shared}
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
  )
})

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
