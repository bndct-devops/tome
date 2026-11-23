import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type Ref } from 'react'
import { Link } from 'react-router-dom'
import { AnimatePresence, m } from 'motion/react'
import {
  BookOpen, ExternalLink, Layers, Loader2, RefreshCw, Search, AlertTriangle, ChevronLeft,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Input, PageBody, PageHeader, Pill, PillGroup } from '@/components/page'
import { BookLink, CoverLift, EASE, LIST, RunningNumber, Unfold, listItem } from '@/components/motion'
import { ModalShell } from '@/components/ModalShell'
import { CoverImage } from '@/components/CoverImage'
import { SeriesStackCard } from '@/components/SeriesStackCard'
import type { Book } from '@/lib/books'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import { cn } from '@/lib/utils'
import { Trans, useLingui } from '@lingui/react/macro'
import { t, plural, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'
import { coverSrc } from '@/lib/covers'

interface HardcoverStatus {
  linked: boolean
  username?: string | null
  token_status?: string | null
  sync_enabled?: boolean
  last_synced_at?: string | null
  sync_running?: boolean
}

interface HcBook {
  book_id: number
  title: string
  series: string | null
  series_index: number | null
  author: string | null
  isbn: string | null
  cover_path: string | null
  state: 'matched' | 'unmatched' | 'excluded' | 'pending'
  slug: string | null
  method: string | null
  pages: number | null
  status: string
  rating: number | null
  progress_pct: number | null
  synced_at: string | null
  error: string | null
}

interface Candidate {
  hardcover_book_id: number
  title: string
  authors: string[]
  slug: string | null
  users_count: number
  cover_url: string | null
  series: string | null
}

type Filter = 'all' | 'matched' | 'unmatched' | 'excluded' | 'errors'

const FILTERS: { key: Filter; label: MessageDescriptor }[] = [
  { key: 'all', label: msg`All` },
  { key: 'matched', label: msg`Synced` },
  { key: 'unmatched', label: msg`Not matched` },
  { key: 'excluded', label: msg`Excluded` },
  { key: 'errors', label: msg`Errors` },
]

function displayTitle(b: HcBook): string {
  // Series-titled volumes ("Black Summoner") need the volume to be readable  - 
  // but a title that already carries its volume ("Berserk, Vol. 1") must not
  // get a second "· Vol. 1" appended (UX sweep finding).
  if (b.series && b.series_index != null) {
    const idx = String(b.series_index)
    const alreadyThere = new RegExp(`vol(?:ume)?\\.?\\s*0*${idx.replace('.', '\\.')}\\b`, 'i').test(b.title)
    if (alreadyThere) return b.title
    const base = b.title === b.series ? b.series : b.title
    // Non-breaking spaces keep "· Vol. N" together; the title part wraps.
    return t`${base} · Vol. ${idx}`
  }
  return b.title
}

const STATE_LABEL: Record<HcBook['state'], MessageDescriptor> = {
  matched: msg`Synced`,
  unmatched: msg`Not matched`,
  excluded: msg`Excluded`,
  pending: msg`Not synced yet`,
}

const GROUP_KEY = 'tome_hardcover_group'

// SeriesStackCard consumes the dashboard's Book shape - adapt an HcBook group.
function toBookShape(rep: HcBook, volumes: HcBook[]): Book {
  return {
    id: rep.book_id,
    title: rep.title,
    subtitle: null,
    author: rep.author,
    series: rep.series,
    series_index: rep.series_index,
    year: null,
    language: null,
    word_count: null,
    status: 'active',
    content_type: 'volume',
    cover_path: rep.cover_path,
    added_at: '',
    files: [],
    tags: [],
    library_ids: [],
    book_type_id: null,
    stack_cover_ids: volumes.slice(1, 3).filter(v => v.cover_path).map(v => v.book_id),
  }
}

type Cell =
  | { kind: 'book'; b: HcBook }
  | { kind: 'stack'; volumes: HcBook[] }

// A stack and its first volume share a key, so opening a series turns the
// stack into that volume in place and the others appear beside it.
const cellKey = (c: Cell) => `b-${c.kind === 'stack' ? c.volumes[0].book_id : c.b.book_id}`

// Past this many cards coming or going at once (a filter, a search, the
// group toggle on a big library) the grid just swaps: hundreds of cards
// moving at once would cost more than they show.
const BULK = 24

// A card that leaves fades.
const CELL_VARIANTS = {
  shown: { opacity: 1 },
  gone: { opacity: 0, transition: { duration: 0.12, ease: EASE } },
}

// Up to this many cards a change glides; a bigger grid just swaps (a motion
// wrapper on each of hundreds of cards costs more on mount than it shows).
const MOVING_MAX = 120

// A big grid's first frame holds this many cards (more than a screen); the
// rest follow in steps after it, so opening the page never stalls a frame.
const FIRST_CARDS = 30
const CARD_STEP = 30

// Without overflow-clip-margin (Safari) the cell gets the room for the shadow
// as padding taken back by a negative margin instead; the padding lets the
// pointer through to the neighbouring cards.
const BIG_CELL = 'supports-[overflow-clip-margin:1px]:[content-visibility:auto] [contain-intrinsic-size:auto_420px] [overflow-clip-margin:56px]'
  + ' not-supports-[overflow-clip-margin:1px]:[content-visibility:auto] not-supports-[overflow-clip-margin:1px]:p-14 not-supports-[overflow-clip-margin:1px]:-m-14'
  + ' not-supports-[overflow-clip-margin:1px]:pointer-events-none not-supports-[overflow-clip-margin:1px]:*:pointer-events-auto'

/** How many of `total` cards to render: `FIRST_CARDS` at once, then `CARD_STEP` more after each painted frame. Never shrinks. */
function useCardsShown(total: number) {
  const [shown, setShown] = useState(FIRST_CARDS)
  useEffect(() => {
    if (shown >= total) return
    let timer = 0
    const frame = requestAnimationFrame(() => { timer = window.setTimeout(() => setShown(n => n + CARD_STEP)) })
    return () => { cancelAnimationFrame(frame); clearTimeout(timer) }
  }, [shown, total])
  return shown
}

/** The state over a cover. `animated`: a new state cross-fades with the old one. */
function StateBadge({ label, animated, className }: { label: string; animated: boolean; className: string }) {
  const cls = cn('absolute top-2 left-2 inline-flex items-center h-6 px-2.5 rounded-full text-[12px] font-semibold backdrop-blur-sm', className)
  if (!animated) return <span className={cls}>{label}</span>
  return (
    <AnimatePresence initial={false}>
      <m.span key={label} {...SWAP} className={cls}>{label}</m.span>
    </AnimatePresence>
  )
}

function MaybePresence({ on, children }: { on: boolean; children: ReactNode }) {
  return on ? <AnimatePresence mode="popLayout" initial={false}>{children}</AnimatePresence> : <>{children}</>
}

/** One grid cell: glides, leaves and arrives when `moving`, else a plain box. */
function CellBox({ moving, dep, children, ref }: { moving: boolean; dep: number; children: ReactNode; ref?: Ref<HTMLDivElement> }) {
  // A big grid skips the layout and paint of cards off screen. The clip margin
  // keeps the cover's shadow, so this is used only where the margin works.
  if (!moving) return <div ref={ref} className={cn('flex flex-col min-w-0', BIG_CELL)}>{children}</div>
  return (
    <m.div
      ref={ref}
      layout="position"
      layoutDependency={dep}
      initial={false}
      animate="shown"
      exit="gone"
      variants={CELL_VARIANTS}
      transition={LIST}
      className="flex flex-col min-w-0"
    >
      {children}
    </m.div>
  )
}

// One label or badge giving way to another in the same place.
const SWAP = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.14, ease: EASE },
} as const

/** The page's shape while the books load: the filter row and a few cards. */
function HardcoverSkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-6 -mt-1 animate-pulse motion-reduce:animate-none">
      <div className="flex flex-wrap items-center gap-1.5">
        {[72, 96, 120].map(w => <div key={w} className="h-9 rounded-full bg-muted" style={{ width: w }} />)}
      </div>
      <GridSkeleton />
    </div>
  )
}

function GridSkeleton() {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-x-6 gap-y-8">
      {[...Array(10)].map((_, i) => (
        <div key={i} className="flex flex-col">
          <div className="aspect-[2/3] rounded-[8px] bg-muted" />
          <div className="mt-3 h-4 w-4/5 rounded bg-muted" />
          <div className="mt-2 h-3 w-1/2 rounded bg-muted" />
        </div>
      ))}
    </div>
  )
}

export function HardcoverPage() {
  const { t } = useLingui()
  const { toast } = useToast()
  const [status, setStatus] = useState<HardcoverStatus | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [books, setBooks] = useState<HcBook[] | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [grouped, setGrouped] = useState(() => localStorage.getItem(GROUP_KEY) === 'true')
  const [expandedSeries, setExpandedSeries] = useState<string | null>(null)

  function persistGroup(v: boolean) {
    setGrouped(v)
    localStorage.setItem(GROUP_KEY, String(v))
    setExpandedSeries(null)
  }
  // Picker modal state
  const [pickFor, setPickFor] = useState<HcBook | null>(null)
  const [pickQuery, setPickQuery] = useState('')
  const [pickResults, setPickResults] = useState<Candidate[] | null>(null)
  const [pickBusy, setPickBusy] = useState(false)
  // The dialog keeps showing the last book while it animates out.
  const lastPick = useRef<HcBook | null>(null)
  if (pickFor) lastPick.current = pickFor
  const pickShown = pickFor ?? lastPick.current

  const loadStatus = useCallback(async () => {
    try {
      const s = await api.get<HardcoverStatus>('/hardcover/status')
      setStatus(s)
      return s
    } catch (err) {
      if (err instanceof Error && /disabled/i.test(err.message)) setUnavailable(true)
      return null
    }
  }, [])

  const loadBooks = useCallback(async () => {
    try {
      setBooks(await api.get<HcBook[]>('/hardcover/books'))
    } catch { /* status handler surfaces errors */ }
  }, [])

  const load = useCallback(async () => {
    const s = await loadStatus()
    if (s?.linked) await loadBooks()
  }, [loadStatus, loadBooks])

  useEffect(() => { void load() }, [load])

  // Poll only the cheap status while a manual sync runs; refetch the full book
  // list once when the run finishes (not every tick).
  useEffect(() => {
    if (!status?.sync_running) return
    const t = window.setInterval(async () => {
      const s = await loadStatus()
      if (s && !s.sync_running) void loadBooks()
    }, 4000)
    return () => window.clearInterval(t)
  }, [status?.sync_running, loadStatus, loadBooks])

  const filtered = useMemo(() => {
    if (!books) return []
    let out = books
    if (filter === 'errors') out = out.filter(b => b.error != null)
    else if (filter !== 'all') out = out.filter(b => b.state === filter)
    const q = query.trim().toLowerCase()
    if (q) {
      out = out.filter(b =>
        b.title.toLowerCase().includes(q)
        || (b.series ?? '').toLowerCase().includes(q)
        || (b.author ?? '').toLowerCase().includes(q))
    }
    return out
  }, [books, filter, query])

  // Grouped view: fold same-series volumes into one stacked card (matching the
  // All Books "Group series" toggle); an expanded series shows its volumes.
  const cells = useMemo<Cell[]>(() => {
    if (expandedSeries) {
      return filtered.filter(b => b.series === expandedSeries).map(b => ({ kind: 'book', b }))
    }
    if (!grouped) return filtered.map(b => ({ kind: 'book', b }))
    const bySeries = new Map<string, HcBook[]>()
    const out: Cell[] = []
    for (const b of filtered) {
      if (!b.series) { out.push({ kind: 'book', b }); continue }
      let group = bySeries.get(b.series)
      if (!group) {
        group = []
        bySeries.set(b.series, group)
        out.push({ kind: 'stack', volumes: group })
      }
      group.push(b)
    }
    // Single-volume "stacks" render as plain cards.
    return out.map(c => (c.kind === 'stack' && c.volumes.length === 1)
      ? { kind: 'book' as const, b: c.volumes[0] } : c)
  }, [filtered, grouped, expandedSeries])

  // Which cards a change brings and takes: a small change animates (cards
  // glide, leave, appear), a bulk one swaps at once. The layout dependency
  // moves only on a small change, so no other render measures the grid.
  const keys = useMemo(() => cells.map(cellKey), [cells])
  const joined = useMemo(() => keys.join(','), [keys])
  const [shape, setShape] = useState({ joined, keys, dep: 0, bulk: true, epoch: 0 })
  if (shape.joined !== joined) {
    const before = new Set(shape.keys)
    const after = new Set(keys)
    let changed = 0
    for (const k of keys) if (!before.has(k)) changed++
    for (const k of shape.keys) if (!after.has(k)) changed++
    const bulk = shape.keys.length === 0 || changed > BULK
    setShape({ joined, keys, dep: bulk ? shape.dep : shape.dep + 1, bulk, epoch: bulk ? shape.epoch + 1 : shape.epoch })
  }

  const moving = cells.length <= MOVING_MAX
  const cardsShown = useCardsShown(cells.length)
  const cellsShown = cardsShown < cells.length ? cells.slice(0, cardsShown) : cells

  const counts = useMemo(() => {
    const c = { all: books?.length ?? 0, matched: 0, unmatched: 0, excluded: 0, errors: 0 }
    for (const b of books ?? []) {
      if (b.state === 'matched') c.matched++
      else if (b.state === 'unmatched') c.unmatched++
      else if (b.state === 'excluded') c.excluded++
      if (b.error) c.errors++
    }
    return c
  }, [books])

  async function syncNow() {
    try {
      const r = await api.post<{ started: boolean }>('/hardcover/sync-now', {})
      toast.info(r.started ? t`Sync started` : t`A sync is already running`)
      setStatus(s => s ? { ...s, sync_running: true } : s)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Could not start sync`)
    }
  }

  async function rematch(b: HcBook, mode: 'retry' | 'exclude') {
    try {
      await api.post(`/hardcover/books/${b.book_id}/rematch`, { mode })
      toast.info(mode === 'retry'
        ? t`Match cleared. Re-matching on the next sync.`
        : t`Excluded from Hardcover sync`)
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Action failed`)
    }
  }

  async function openPicker(b: HcBook) {
    setPickFor(b)
    const base = b.series && b.series_index != null
      // eslint-disable-next-line lingui/no-unlocalized-strings -- search query, not copy
      ? `${b.series} Vol. ${b.series_index}` : b.title
    const q = b.author ? `${base} ${b.author}` : base
    setPickQuery(q)
    setPickResults(null)
    await runPickSearch(q)
  }

  async function runPickSearch(q: string) {
    setPickBusy(true)
    try {
      setPickResults(await api.get<Candidate[]>(`/hardcover/search?q=${encodeURIComponent(q)}`))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Search failed`)
      setPickResults([])
    } finally {
      setPickBusy(false)
    }
  }

  async function pin(c: Candidate) {
    if (!pickFor) return
    setPickBusy(true)
    try {
      await api.post(`/hardcover/books/${pickFor.book_id}/match`, { hardcover_book_id: c.hardcover_book_id })
      { const title = c.title; toast.success(t`Matched to “${title}”`) }
      setPickFor(null)
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Could not set match`)
    } finally {
      setPickBusy(false)
    }
  }

  // eslint-disable-next-line lingui/no-unlocalized-strings -- product name
  const pageTitle = 'Hardcover'
  const subtitle = t`What your books sync to on Hardcover. Audit matches, fix wrong ones, exclude books.`

  if (unavailable) {
    return (
      <AppShell>
        <PageBody width="wide">
          <PageHeader title={pageTitle} subtitle={subtitle} />
          <p className="py-16 text-center text-[15px] text-muted-foreground"><Trans>Hardcover sync is disabled on this server.</Trans></p>
        </PageBody>
      </AppShell>
    )
  }

  const linked = !!status?.linked
  const eyebrow = linked && status
    ? (() => {
        const user = status.username ?? ''
        if (!status.last_synced_at) return t`@${user}`
        const when = new Date(status.last_synced_at + 'Z').toLocaleDateString(i18n.locale, { month: 'short', day: 'numeric' })
        return t`@${user} · last synced ${when}`
      })()
    : undefined

  return (
    <AppShell>
      <PageBody width="wide">
        <PageHeader
          eyebrow={eyebrow}
          title={pageTitle}
          className="[&>div:last-child]:shrink [&>div:last-child]:min-w-0"
          subtitle={subtitle}
          actions={linked && status ? (
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                <input
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder={t`Filter…`}
                  aria-label={t`Filter books`}
                  className="h-9 w-44 rounded-full bg-secondary pl-9 pr-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </div>
              <Pill
                variant={grouped ? 'secondary' : 'quiet'}
                onClick={() => persistGroup(!grouped)}
                title={grouped ? t`Show individual volumes` : t`Group volumes by series`}
                aria-pressed={grouped}
                className={cn(grouped && 'text-primary')}
              >
                <Layers className="w-4 h-4" />
                <Trans>Group series</Trans>
              </Pill>
              <Pill variant="primary" onClick={() => void syncNow()} disabled={!!status.sync_running}>
                <RefreshCw className={cn('w-4 h-4', status.sync_running && 'animate-spin')} />
                {status.sync_running ? t`Syncing…` : t`Sync now`}
              </Pill>
            </div>
          ) : undefined}
        />

        {!status ? (
          <HardcoverSkeleton />
        ) : !status.linked ? (
          <div className="py-16 flex flex-col items-center gap-4 text-center">
            <p className="text-[15px] text-muted-foreground max-w-[46ch]">
              <Trans>No Hardcover account linked yet. Link your personal API token in Settings and syncing starts right after.</Trans>
            </p>
            <Link
              to="/settings/hardcover"
              className="inline-flex items-center h-9 px-4 rounded-full bg-primary text-primary-foreground text-[14px] font-semibold hover:bg-primary/90 transition-colors touch-feedback"
            >
              <Trans>Open settings</Trans>
            </Link>
          </div>
        ) : (
          <div className="flex flex-col gap-6 -mt-1">
            <PillGroup
              value={filter}
              onChange={setFilter}
              options={FILTERS.filter(f => f.key === 'all' || counts[f.key] > 0).map(f => ({
                value: f.key,
                label: (
                  <span className="inline-flex items-center gap-1.5">
                    {i18n._(f.label)}
                    <RunningNumber value={counts[f.key]} className="opacity-60" />
                  </span>
                ),
              }))}
            />

            <Unfold open={!!expandedSeries} gap={24}>
              <div className="flex items-center gap-2 -mt-2 min-w-0">
                <Pill variant="quiet" onClick={() => setExpandedSeries(null)} className="-ml-3 shrink-0">
                  <ChevronLeft className="w-4 h-4" /> <Trans>All books</Trans>
                </Pill>
                <span className="font-display text-[20px] font-bold text-foreground truncate">{expandedSeries}</span>
              </div>
            </Unfold>

            {books == null ? (
              <GridSkeleton />
            ) : cells.length === 0 ? (
              <p className="py-16 text-center text-[15px] text-muted-foreground"><Trans>No books in this view.</Trans></p>
            ) : (
              // A bulk change mounts a fresh grid (new key): no leaving cards to
              // track. A big grid is plain: no presence, no glides.
              <div key={moving ? shape.epoch : 'still'} className="relative grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-x-6 gap-y-8">
                <MaybePresence on={moving}>
                {cellsShown.map(cell => (
                  <CellBox key={cellKey(cell)} moving={moving} dep={shape.dep}>
                  {cell.kind === 'stack' ? (
                  <SeriesStackCard
                    book={toBookShape(cell.volumes[0], cell.volumes)}
                    count={cell.volumes.length}
                    view="small"
                    onOpen={() => setExpandedSeries(cell.volumes[0].series)}
                  />
                ) : (() => { const b = cell.b; const badge = b.error ? t`Error` : i18n._(STATE_LABEL[b.state]); return (
                  <>
                    <CoverLift>
                    <BookLink
                      book={{ id: b.book_id, title: b.title, author: b.author, series: b.series, series_index: b.series_index }}
                      data-cover-id={b.book_id}
                      className="relative block aspect-[2/3] rounded-[8px] overflow-hidden bg-muted"
                      style={{ boxShadow: 'var(--cover-bloom-fan)' }}
                    >
                      <CoverImage
                        src={coverSrc(b.book_id, { w: 400 })}
                        alt={b.title}
                        imgClassName="absolute inset-0 w-full h-full object-cover"
                        iconClassName="w-8 h-8"
                      />
                      {/* After a match, an exclude or an include the state
                          changes in place: the old badge fades as the new one
                          comes in. */}
                      <StateBadge label={badge} animated={moving} className={cn(
                        b.error ? 'bg-warning text-black'
                          : b.state === 'matched' ? 'bg-success text-black'
                          : b.state === 'excluded' ? 'bg-black/55 text-white/80'
                          : 'bg-black/60 text-white'
                      )} />
                    </BookLink>
                    </CoverLift>
                    <div className="pt-3 flex flex-col gap-1 grow min-w-0">
                      <BookLink
                        book={{ id: b.book_id, title: b.title, author: b.author, series: b.series, series_index: b.series_index }}
                        className="text-[15px] font-semibold text-foreground leading-snug line-clamp-2 hover:text-primary transition-colors"
                      >
                        {displayTitle(b)}
                      </BookLink>
                      {b.slug ? (
                        <a
                          href={`https://hardcover.app/books/${b.slug}`}
                          target="_blank" rel="noopener noreferrer"
                          className="text-[12px] text-muted-foreground hover:text-primary inline-flex items-center gap-1 min-w-0"
                        >
                          <span className="truncate">{b.slug}</span>
                          <ExternalLink className="w-3 h-3 shrink-0" />
                        </a>
                      ) : (
                        <p className="text-[12px] text-muted-foreground truncate">
                          {b.state === 'excluded' ? t`not syncing` : b.isbn ?? t`no ISBN`}
                        </p>
                      )}
                      {b.error && (
                        <p className="text-[12px] text-warning inline-flex items-center gap-1 min-w-0">
                          <AlertTriangle className="w-3 h-3 shrink-0" />
                          <span className="truncate" title={b.error}>{b.error}</span>
                        </p>
                      )}
                      <div className="mt-auto pt-1 flex items-center gap-3 text-[13px] font-medium">
                        <button
                          onClick={() => void openPicker(b)}
                          className="text-primary hover:underline underline-offset-4 touch-feedback"
                          title={t`Search Hardcover and pick the record yourself`}
                        >
                          <Trans>Pick</Trans>
                        </button>
                        {b.state === 'matched' && (
                          <button
                            onClick={() => void rematch(b, 'retry')}
                            className="text-muted-foreground hover:text-foreground touch-feedback"
                            title={t`Remove the entry Tome created on Hardcover and re-match automatically. An entry that was already on your profile is kept.`}
                          >
                            <Trans>Re-match</Trans>
                          </button>
                        )}
                        {b.state === 'excluded' ? (
                          <button
                            onClick={() => void rematch(b, 'retry')}
                            className="ml-auto text-muted-foreground hover:text-foreground touch-feedback"
                            title={t`Start matching this book again`}
                          >
                            <Trans>Include</Trans>
                          </button>
                        ) : (
                          <button
                            onClick={() => void rematch(b, 'exclude')}
                            className="ml-auto text-muted-foreground hover:text-destructive touch-feedback"
                            title={t`Never sync this book. Nothing is removed from Hardcover.`}
                          >
                            <Trans>Exclude</Trans>
                          </button>
                        )}
                      </div>
                    </div>
                  </>
                ) })()}
                  </CellBox>
                ))}
                </MaybePresence>
              </div>
            )}
          </div>
        )}
      </PageBody>

      {/* Pick-a-record dialog */}
      <ModalShell
        open={!!pickFor}
        onClose={() => setPickFor(null)}
        size="md"
        title={pickShown ? (() => { const title = displayTitle(pickShown); return <Trans>Match “{title}” to…</Trans> })() : ''}
        bodyClassName="pb-3"
      >
        <div className="flex gap-2">
          <Input
            value={pickQuery}
            onChange={e => setPickQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') void runPickSearch(pickQuery) }}
            autoFocus
            placeholder={t`Search Hardcover…`}
            aria-label={t`Search Hardcover`}
            className="flex-1 min-w-0"
          />
          <Pill variant="primary" onClick={() => void runPickSearch(pickQuery)} disabled={pickBusy}>
            <Trans>Search</Trans>
          </Pill>
        </div>
        <div className={cn('mt-4 border-t border-border/70 transition-opacity', pickBusy && pickResults != null && 'opacity-60')}>
          {pickResults == null ? (
            <div className="flex items-center gap-2 text-[14px] text-muted-foreground py-4">
              <Loader2 className="w-4 h-4 animate-spin" /> <Trans>Searching…</Trans>
            </div>
          ) : pickResults.length === 0 ? (
            <p className="text-[14px] text-muted-foreground py-4"><Trans>No results. Try a different query.</Trans></p>
          ) : (
            <ul className="relative">
              <AnimatePresence mode="popLayout" initial={false}>
              {pickResults.map(c => (
                <m.li key={c.hardcover_book_id} {...listItem} className="border-b border-border/70 last:border-b-0">
                  <button
                    onClick={() => void pin(c)}
                    disabled={pickBusy}
                    className="w-full flex items-center gap-4 py-3 text-left rounded-lg hover:bg-secondary/60 transition-colors disabled:opacity-50 touch-feedback"
                  >
                    {c.cover_url ? (
                      <img src={c.cover_url} alt="" className="w-10 h-[60px] object-cover rounded-[8px] shrink-0" />
                    ) : (
                      <span className="w-10 h-[60px] rounded-[8px] bg-muted shrink-0 flex items-center justify-center">
                        <BookOpen className="w-4 h-4 text-muted-foreground/60" />
                      </span>
                    )}
                    <span className="min-w-0">
                      <span className="block text-[15px] font-semibold text-foreground truncate">{c.title}</span>
                      <span className="block text-[13px] text-muted-foreground truncate">
                        {c.authors.join(', ') || t`unknown author`}
                        {c.users_count > 0 && ' · ' + plural(c.users_count, { one: '# reader', other: '# readers' })}
                      </span>
                    </span>
                  </button>
                </m.li>
              ))}
              </AnimatePresence>
            </ul>
          )}
        </div>
      </ModalShell>
    </AppShell>
  )
}
