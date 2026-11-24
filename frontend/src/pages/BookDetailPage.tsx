import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent, type ReactNode, type RefObject } from 'react'
import { flushSync } from 'react-dom'
import { useParams, useNavigate, useLocation, Link } from 'react-router-dom'
import { AnimatePresence, m } from 'motion/react'
import {
  Camera, Download, Edit2, X, Trash2, Loader2, Sparkles, Library as LibraryIcon, Check,
  ChevronLeft, ChevronRight, StickyNote, ChevronDown, Play, Link2, RefreshCw,
  Plus, TrendingUp, TrendingDown, Minus, History as HistoryIcon, Share2, Eye,
  ExternalLink, CalendarCheck,
} from 'lucide-react'
import { useAuth, isAdmin, isMember } from '@/contexts/AuthContext'
import { useSidebarLists } from '@/lib/sidebarLists'
import { refreshDeviceInventories, useDeviceInventories } from '@/lib/deviceBooks'
import { useToast } from '@/contexts/ToastContext'
import { FinishByPill } from '@/components/goals/FinishByPill'
import { Trans, useLingui } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { MetadataFetchModal } from '@/components/MetadataFetchModal'
import { CoverPickerModal } from '@/components/CoverPickerModal'
import { PositionHistoryModal } from '@/components/PositionHistoryModal'
import { ShareModal } from '@/components/ShareShelfModal'
import { SendButton } from '@/components/SendButton'
import { DevicePills } from '@/components/DevicePills'
import { StarRating } from '@/components/StarRating'
import { CoverImage } from '@/components/CoverImage'
import { AutocompleteInput } from '@/components/AutocompleteInput'
import { AppShell } from '@/components/AppShell'
import { QuickLook } from '@/components/QuickLook'
import { Glide, GlideGroup, PageBody, SectionTitle, Stat, StatStrip, Field, Pill, Input, Select, Swap, Textarea, FormLabel, INPUT_CLASS, PRESS, PRESS_LABEL } from '@/components/page'
import { BookLink, CoverLift, EASE, EASE_CSS, Pop, RunningNumber, SeriesLink, Unfold, listItem, useSeriesNav, type BookPreview } from '@/components/motion'
import { bookRequests, dropBookSeed, fetchBookSeed, heroCoverSrc, hintSeries, peekBookSeed, prefetchSeries, seriesDetailUrl, type BookSeed, type BookSeedKey } from '@/components/motion/nav'
import { ModalShell, isModalOpen } from '@/components/ModalShell'
import { NamedIcon } from '@/lib/icons'
import { coverSrc } from '@/lib/covers'
import { isPlainClick, readPreview } from '@/lib/coverTransition'
import { SessionLog, SessionLogHint } from '@/components/stats/SessionLog'
import { InfoHint } from '@/components/InfoHint'
import { api } from '@/lib/api'
import type { BookDetail, BookFile, Library as LibraryType, BookStatus, ReadingStatus } from '@/lib/books'
import { formatBytes } from '@/lib/books'
import { useBookTypes } from '@/lib/bookTypes'
import { progressPercent, cn, formatDuration, formatDate } from '@/lib/utils'
import { describePace, formatEstimateDays, formatEstimateHours, type BookEstimate, type EstimatePace } from '@/lib/backlog'

interface Facets {
  authors: string[]
  series: string[]
  tags: string[]
  formats: string[]
}

interface Annotation {
  id: number | string  // "clipping:<n>" for a CrossInk clipping
  anchor: string | null
  // Set on CrossInk clippings: the reader owns them, so Tome only shows them.
  source?: 'crossink'
  read_only?: boolean
  highlighted_text: string | null
  note: string | null
  chapter: string | null
  color: string | null
  datetime: string | null
  updated_at: string
}

export interface BookReadingStats {
  total_seconds: number
  // Reconciled sitting count: recorded web/manual sessions plus gap-clustered
  // imported KOReader history - exactly the rows the session log lists.
  sessions: number
  pages_turned: number
  avg_session_seconds: number
  pace_pages_per_min: number | null
  first_read: string | null
  last_read: string | null
  finished_at: string | null
  progress: number | null
  status: string
  session_timeline: { date: string; seconds: number; pages: number; progress_pct: number | null }[]
  by_source: { device: string; seconds: number; sessions: number }[]
  momentum: { recent_seconds: number; prior_seconds: number; delta_pct: number | null; direction: string } | null
  estimated_finish_seconds: number | null
  estimated_finish_date: string | null
}

export interface BookIntensity {
  bins: number
  curve: number[]
  total_seconds: number
  total_pages: number
  pages_read: number
  pct_read: number
  reread_bins: number
}

interface ChapterSitting {
  start_ts: number
  end_ts: number
}

export interface ChapterTime {
  idx: number
  title: string
  start_fraction: number
  end_fraction: number
  seconds: number
  sittings: ChapterSitting[]
}

// When a chapter was actually read, honest about sittings:
//   one sitting  → "Jun 2, 17:40 - 18:54"
//   two or three → "Jun 2 17:40-18:10 · Jun 3 21:00-21:34"
//   more         → "5 sittings · Jun 2 - Jun 8"
function chapterReadSpan(c: ChapterTime): string {
  const s = c.sittings
  if (!s || s.length === 0) return ''
  const day = (ts: number) =>
    new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  const clock = (ts: number) =>
    new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  if (s.length === 1) {
    const d = day(s[0].start_ts), from = clock(s[0].start_ts), to = clock(s[0].end_ts)
    return t`read ${d}, ${from} to ${to}`
  }
  if (s.length <= 3) {
    const span = s.map(x => `${day(x.start_ts)} ${clock(x.start_ts)}-${clock(x.end_ts)}`).join(' · ')
    return t`read ${span}`
  }
  const count = s.length, from = day(s[0].start_ts), to = day(s[s.length - 1].end_ts)
  return t`${count} sittings · ${from} to ${to}`
}

// Short variant for the line-chart tooltip (dates only, no clock times).
function chapterReadSpanShort(c: ChapterTime): string {
  const s = c.sittings
  if (!s || s.length === 0) return ''
  const day = (ts: number) =>
    new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  const a = day(s[0].start_ts)
  const b = day(s[s.length - 1].end_ts)
  return a === b ? t`read ${a}` : t`read ${a} to ${b}`
}

interface ReadingStatsResponse {
  own: BookReadingStats
  aggregate: {
    total_seconds: number
    total_sessions: number
    distinct_readers: number
  } | null
  intensity: BookIntensity | null
  chapters: ChapterTime[] | null
}

interface KosyncUnlinkedDoc {
  document: string
  percentage: number
  device: string | null
  timestamp: number
  /** What the reader called the file, when it sent metadata. */
  title?: string | null
}

interface KosyncStatus {
  linked: boolean
  device?: string
  percentage?: number | null
  timestamp?: number | null
  unlinked_documents?: KosyncUnlinkedDoc[]
}

interface SeriesVolume {
  id: number
  title: string
  series_index: number | null
  cover_path: string | null
  cover_color?: string | null
  updated_at: string | null
  reading_status: string | null
}

async function downloadFile(book: BookDetail, f: BookFile) {
  const token = localStorage.getItem('tome_token')
  const resp = await fetch(`/api/books/${book.id}/download/${f.id}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  // eslint-disable-next-line lingui/no-unlocalized-strings -- internal, the caller toasts its own message
  if (!resp.ok) throw new Error('download failed')
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = f.filename || `${book.title}.${f.format.toLowerCase()}`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// The page frame, shared by the page and its preview (below) so the hero cover
// sits in the same place in both and React keeps the same element.
const HEADER_CLS = 'flex min-h-9 flex-wrap items-center justify-between gap-x-4 gap-y-2'
const GRID_CLS = 'grid items-start gap-10 lg:grid-cols-[220px_minmax(0,1fr)] lg:grid-rows-[auto_1fr]'
const RAIL_COVER_CLS = 'mx-auto w-[200px] sm:w-[220px] lg:mx-0 lg:col-start-1 lg:row-start-1'
const COVER_CLS = 'relative aspect-[2/3] overflow-hidden rounded-[12px] bg-muted'
const MAIN_COL_CLS = 'order-2 flex min-w-0 flex-col gap-7 lg:order-none lg:col-start-2 lg:row-start-1 lg:row-span-2'
const HERO_CLS = 'min-w-0 max-w-[680px]'
// min-h-9: the previous/next buttons arrive later and must not grow the row,
// and the crumbs sit at the actions' height whether or not those share their line.
// Counts as 14rem wide when the row decides whether to wrap, then takes the
// room that is left: a long series name is cut with an ellipsis (it is printed
// in full above the title) before the actions drop to a second line.
const CRUMBS_CLS = 'flex min-h-9 min-w-0 flex-1 basis-56 items-center gap-1.5 text-[13px] text-muted-foreground'
const SERIES_LINE_CLS = 'block text-left text-[12px] font-bold uppercase tracking-[0.1em] text-primary hover:underline underline-offset-4'
const TITLE_CLS = 'mt-2 font-display text-[30px] sm:text-[34px] font-bold leading-[1.05] text-foreground'
const AUTHOR_CLS = 'mt-1.5 block text-left text-[16px] text-muted-foreground hover:text-primary transition-colors'
// The sections under the hero, arriving after it: a short rise from partly there.
const RISE_FROM = { opacity: 0.5, y: 10 }
const RISE_TO = { opacity: 1, y: 0, transition: { duration: 0.24, ease: EASE } }

// The header row's height at a viewport width, as the last page drew it: its
// actions wrap at narrow widths, so the preview reserves what the page used.
// Kept across visits (per browser) so the first book of a visit reserves it too.
const HEADER_ROW_KEY = 'tome_book_header_row'
let headerRow: { width: number; height: number } | null = (() => {
  try { return JSON.parse(localStorage.getItem(HEADER_ROW_KEY) ?? 'null') } catch { return null }
})()
// The hero's title block as this book drew it: while the next book's lines
// stand in, the block keeps at least this height, so the rows under it (and
// the status pills' indicator) do not move before the page changes.
let heroTitleH = 0

/** The finish-by pill's usual label, unseen, so its slot is held on books that have none.
 *  A goal's day ("Finish by Sep 28") is wider and is not reserved: that width would wrap
 *  the header in a narrow window on every book. */
function FinishByReserve() {
  return (
    <Pill variant="quiet" tabIndex={-1}>
      <CalendarCheck className="h-4 w-4" />
      <span className="hidden whitespace-nowrap sm:inline"><Trans>Finish by…</Trans></span>
    </Pill>
  )
}

const REDUCED = '(prefers-reduced-motion: reduce)'
const reduced = () => window.matchMedia(REDUCED).matches

// The title block as last drawn (mid-glide too) and when its page went: the
// next book's page, mounting in the same commit, glides from that height
// instead of moving the rows under it in one jump.
let titleDrawn = { h: 0, at: 0 }
const TITLE_GLIDE_MS = 240

/** The hero's title block: when its height changes (a title on more or fewer
 *  lines) the block glides to the new height. `innerRef` is the block itself. */
function TitleGlide({ innerRef, style, children }: {
  innerRef: RefObject<HTMLDivElement | null>
  style?: CSSProperties
  children: ReactNode
}) {
  const outerRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const box = outerRef.current, el = innerRef.current
    if (!box || !el) return
    let last = el.offsetHeight
    let run: Animation | null = null
    const glide = (from: number, to: number) => {
      run?.cancel()
      run = null
      if (Math.abs(from - to) < 1 || reduced()) return
      box.style.overflow = 'clip'
      run = box.animate([{ height: `${from}px` }, { height: `${to}px` }], { duration: TITLE_GLIDE_MS, easing: EASE_CSS })
      const mine = run
      const end = () => { if (run === mine) { run = null; box.style.overflow = '' } }
      mine.onfinish = end
      mine.oncancel = () => { if (run === mine || run === null) box.style.overflow = '' }
    }
    if (titleDrawn.h && performance.now() - titleDrawn.at < 100) glide(titleDrawn.h, last)
    titleDrawn = { h: 0, at: 0 }
    const ro = new ResizeObserver(() => {
      const h = el.offsetHeight
      if (h === last) return
      const from = run ? box.getBoundingClientRect().height : last
      last = h
      glide(from, h)
    })
    ro.observe(el)
    return () => {
      titleDrawn = { h: run && box.isConnected ? box.getBoundingClientRect().height : last, at: performance.now() }
      ro.disconnect()
      run?.cancel()
    }
  }, [innerRef])
  return (
    <div ref={outerRef}>
      <div ref={innerRef} style={style}>{children}</div>
    </div>
  )
}

// A step to the previous or next volume with the arrows: the arriving cover
// and title come in from the side stepped towards. Played once per step,
// across the dimmed wait and the page that follows it (which picks it up at
// the same point rather than starting over).
const CUE_MS = 200
const CUE_PX = 10
let cue: { id: number; dir: 1 | -1; at: number; started: number } | null = null
function playCue(id: number, els: (HTMLElement | null)[]) {
  if (!cue || cue.id !== id || reduced()) return
  const now = performance.now()
  if (!cue.started && now - cue.at > 2000) { cue = null; return }
  const elapsed = cue.started ? now - cue.started : 0
  if (elapsed >= CUE_MS) return
  if (!cue.started) cue.started = now
  const { dir } = cue
  for (const el of els) {
    if (!el) continue
    const a = el.animate(
      [{ transform: `translateX(${dir * CUE_PX}px)`, opacity: 0.6 }, { transform: 'none', opacity: 1 }],
      { duration: CUE_MS, easing: EASE_CSS },
    )
    a.currentTime = elapsed
  }
}

/** The hero cover. Opened from a card it starts with the card's thumbnail
 *  (already in the cache, so it paints at once) and the full-size image fades
 *  in over it once loaded; a direct visit shows the full image as before. */
function cached(src: string): boolean {
  const img = new Image()
  img.src = src
  return img.complete && img.naturalWidth > 0
}

// A series link asks for the series' answers again when they are older than this.
const SERIES_WARM_MS = 3000

/** The series' first cover in the "In this series" strip of the section
 *  holding `link`, when it shows whole (not scrolled out of the strip). */
function stripCover(link: Element): HTMLElement | null {
  const el = link.closest('section')?.querySelector<HTMLElement>('[data-series-cover]')
  const strip = el?.parentElement?.closest<HTMLElement>('.overflow-x-auto')
  if (!el || !strip) return null
  const r = el.getBoundingClientRect(), s = strip.getBoundingClientRect()
  return r.left >= s.left - 1 && r.right <= s.right + 1 ? el : null
}

function HeroCover({ thumb, full, alt, color }: { thumb?: string | null; full: string | null; alt: string; color?: string | null }) {
  // Already fetched (asked for ahead): shown at once instead of fading in over the thumbnail.
  const [readySrc, setReadySrc] = useState<string | null>(() => (full && cached(full) ? full : null))
  if (!thumb) return <CoverImage src={full} alt={alt} loading="eager" color={color} iconClassName="w-12 h-12" />
  return (
    <>
      {color && <div className="absolute inset-0" style={{ backgroundColor: color }} />}
      <img src={thumb} alt="" decoding="sync" className="absolute inset-0 h-full w-full object-cover" />
      {full && (
        <img
          key={full}
          src={full}
          alt={alt}
          ref={el => { if (el?.complete && el.naturalWidth) setReadySrc(full) }}
          onLoad={() => setReadySrc(full)}
          className={cn('absolute inset-0 h-full w-full object-cover transition-opacity duration-300', readySrc === full ? 'opacity-100' : 'opacity-0')}
        />
      )}
    </>
  )
}

// What a book page loads (components/motion/nav.ts): asked for ahead when a
// link to the book is pressed or hovered, else on arrival; moving from one
// book to another fetches them while the old page stays up.
type SeedKey = BookSeedKey
interface Seed extends Pick<BookSeed, 'values' | 'pending'> {
  /** Handed over by a move from another book, which has waited for it already. */
  waited?: boolean
}
const SEED_KEYS: SeedKey[] = ['book', 'status', 'adjacent', 'kosync', 'annotations', 'stats', 'estimate', 'series']
const complete = (s: BookSeed) => SEED_KEYS.every(k => k in s.values)

// The longest the old page waits, dimmed, for the next book's answers.
const SWAP_CAP_MS = 1200
// The longest a page that arrived on its own holds its sections back for the
// late answers (reading stats, highlights) so they land together.
const REVEAL_CAP_MS = 900

// Held per login (the token the answer was asked with), like useKosyncStatus:
// signing out or impersonating does not reload the page, and another account's
// libraries or pace must never show.
const currentToken = () => localStorage.getItem('tome_token')
const mine = <T,>(c: { token: string | null; value: T } | null): T | null =>
  (c && c.token === currentToken() ? c.value : null)

// Libraries change rarely: the last answer draws the Libraries pill at once on
// the next book instead of it arriving and moving the actions beside it.
let lastLibraries: { token: string | null; value: LibraryType[] } | null = null

// Whether this visit has asked for the device lists (see devicesIn).
let devicesAsked = false
const DEVICES_CAP_MS = 500

// The reader's pace from the last estimate (it is the same for every book):
// a book with a word count shows its estimate from it at once, the way the
// server works it out (backend/services/backlog_estimate.py), until its own answer lands.
let lastPace: { token: string | null; value: EstimatePace } | null = null
function estimateFromPace(words: number, pace: EstimatePace): BookEstimate {
  const seconds = Math.round(words / (pace.wpm || pace.default_wpm) * 60)
  return {
    seconds,
    days: pace.minutes_per_day ? Math.round(seconds / 60 / pace.minutes_per_day * 10) / 10 : null,
    method: pace.wpm ? 'words' : 'default',
    pace,
  }
}

/**
 * One book page. Moving to another book keeps the page up: the next book's
 * cover, title and series take the hero at once (from the preview the link
 * handed over), the rest dims, and once its answers are in (or after
 * SWAP_CAP_MS) a fresh page for it mounts with them. Each book still gets its
 * own mount (keyed on the id), so nothing of the old book survives and a late
 * answer for it can never land on the new one.
 */
export function BookDetailPage() {
  const { id } = useParams<{ id: string }>()
  const location = useLocation()
  // Opened from a link that asked ahead: the page mounts with what is in.
  const [page, setPage] = useState<{ id: string | undefined; seed: Seed | null }>(() => {
    const s = id ? peekBookSeed(Number(id)) : null
    // A copy: the page reads which answers came with it once, at its mount.
    return { id, seed: s && { values: { ...s.values }, pending: s.pending } }
  })
  // A move whose answers are all in already swaps in this render, without a dimmed frame.
  const ready = id && id !== page.id ? peekBookSeed(Number(id)) : null
  if (id && ready && complete(ready)) setPage({ id, seed: { values: { ...ready.values }, pending: ready.pending, waited: true } })
  useEffect(() => {
    if (!id || id === page.id) return
    let live = true
    const s = peekBookSeed(Number(id)) ?? fetchBookSeed(id)
    const swap = () => { if (live) { live = false; setPage({ id, seed: { values: { ...s.values }, pending: s.pending, waited: true } }) } }
    const timer = setTimeout(swap, SWAP_CAP_MS)
    s.all.then(swap)
    return () => { live = false; clearTimeout(timer) }
  }, [id, page.id])
  const next = id && id !== page.id ? (readPreview(location.state, Number(id)) ?? { id: Number(id), title: '' }) : null
  // The shell stays across books (its sidebar asks nothing again); each book gets its own mount.
  return <AppShell><BookDetailView key={page.id} id={page.id ?? ''} seed={page.seed} next={next} /></AppShell>
}

function BookDetailView({ id, seed, next }: {
  id: string
  seed: Seed | null
  /** The book being moved to while its answers load: its preview takes the hero, the rest dims. */
  next: BookPreview | null
}) {
  const { t, i18n } = useLingui()
  const navigate = useNavigate()
  const openSeries = useSeriesNav()
  const location = useLocation()
  // What the card that opened this page knew: the hero paints from it at once.
  // Read once: the router state moves on to the next book before this page goes.
  const [preview] = useState(() => readPreview(location.state, Number(id)))
  const seeded = <T,>(k: SeedKey): T | undefined => seed?.values[k] as T | undefined
  const known = (k: SeedKey) => !!seed && k in seed.values
  const seedStatus = seeded<BookStatus>('status')
  const seedKosync = seeded<KosyncStatus>('kosync')
  const headerRef = useRef<HTMLDivElement>(null)
  const [heldHeader] = useState(() => (headerRow?.width === window.innerWidth ? headerRow.height : null))
  useLayoutEffect(() => {
    const el = headerRef.current
    if (!el || (headerRow?.width === window.innerWidth && headerRow.height === el.offsetHeight)) return
    headerRow = { width: window.innerWidth, height: el.offsetHeight }
    try { localStorage.setItem(HEADER_ROW_KEY, JSON.stringify(headerRow)) } catch { /* storage off */ }
  })
  const { user } = useAuth()
  const { toast } = useToast()
  const [book, setBook] = useState<BookDetail | null>(() => seeded<BookDetail>('book') ?? null)
  const [loading, setLoading] = useState(() => !known('book'))
  // The "On <device>" pills sit in the hero above the status row: the first
  // page of a visit waits (briefly) for the device lists so they do not arrive
  // later and push everything under them down.
  const devicesNow = useDeviceInventories()
  const [devicesIn, setDevicesIn] = useState(() => devicesAsked || devicesNow.length > 0)
  useEffect(() => {
    if (devicesIn) return
    devicesAsked = true
    let live = true
    const done = () => { if (live) setDevicesIn(true) }
    const cap = setTimeout(done, DEVICES_CAP_MS)
    refreshDeviceInventories().finally(done)
    return () => { live = false; clearTimeout(cap) }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per mount
  }, [])
  const bookTypes = useBookTypes()
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [downloadingId, setDownloadingId] = useState<number | null>(null)
  const [draft, setDraft] = useState<Partial<BookDetail>>(() => seeded<BookDetail>('book') ?? {})
  const [draftBookTypeId, setDraftBookTypeId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fetchModalOpen, setFetchModalOpen] = useState(false)
  const [coverPickerOpen, setCoverPickerOpen] = useState(false)
  // The sidebar's copy of the libraries (same answer) draws the Libraries pill at
  // once when this page has none yet; it is refreshed below either way.
  const sidebarLibraries = useSidebarLists(user?.id).libraries
  const [libraries, setLibraries] = useState<LibraryType[]>(() => mine(lastLibraries) ?? sidebarLibraries)
  const [librariesIn, setLibrariesIn] = useState(() => mine(lastLibraries) != null || sidebarLibraries.length > 0)
  const [libMenuOpen, setLibMenuOpen] = useState(false)
  const libMenuRef = useRef<HTMLDivElement>(null)
  const [libPending, setLibPending] = useState<Set<number>>(new Set())
  // optimistic local library_ids so UI updates instantly
  const [localLibIds, setLocalLibIds] = useState<number[]>(() => seeded<BookDetail>('book')?.library_ids ?? [])
  const [bookStatus, setBookStatus] = useState<ReadingStatus>(() => seedStatus?.status ?? 'unread')
  const [statusKnown, setStatusKnown] = useState(() => known('status'))
  const [progressPct, setProgressPct] = useState<number | null>(() => seedStatus?.progress_pct ?? null)
  const [cfi, setCfi] = useState<string | null>(() => seedStatus?.cfi ?? null)
  // A page that mounted with its answers shows the bar at its value (another
  // book's bar is not this one's to grow from); one that waited grows it.
  const [progressAnimated, setProgressAnimated] = useState(() => known('status'))
  const [statusSaving, setStatusSaving] = useState(false)
  const [rating, setRating] = useState<number | null>(() => seedStatus?.rating ?? null)
  // The stars' fill-in runs only once the pointer is on them, not on load.
  const [ratingSettled, setRatingSettled] = useState(true)
  const [review, setReview] = useState(() => seedStatus?.review ?? '')       // saved review text
  const [reviewDraft, setReviewDraft] = useState('')  // in-progress edit
  const [editingReview, setEditingReview] = useState(false)
  const [kosyncDevice, setKosyncDevice] = useState<string | null>(() => (seedKosync?.linked && seedKosync.device) || null)
  // Where the linked KOSync position stands, for the "Synced from" pill.
  const [kosyncLinked, setKosyncLinked] = useState<{ percentage: number | null; timestamp: number | null } | null>(() =>
    seedKosync?.linked ? { percentage: seedKosync.percentage ?? null, timestamp: seedKosync.timestamp ?? null } : null)
  // Manual KOSync linking (#156): sync documents pushed by a KOSync client
  // (stock KOReader / Readest) that aren't attached to any Tome book yet.
  const [kosyncUnlinked, setKosyncUnlinked] = useState<KosyncUnlinkedDoc[]>(() => (!seedKosync?.linked && seedKosync?.unlinked_documents) || [])
  const [kosyncPickerOpen, setKosyncPickerOpen] = useState(false)
  const [kosyncLinkDoc, setKosyncLinkDoc] = useState('')
  const [kosyncLinking, setKosyncLinking] = useState(false)
  const [annotations, setAnnotations] = useState<Annotation[]>(() => seeded<Annotation[]>('annotations') ?? [])
  const [confirmingHighlight, setConfirmingHighlight] = useState<number | string | null>(null)
  const [highlightsOpen, setHighlightsOpen] = useState(true)
  const [readingStats, setReadingStats] = useState<ReadingStatsResponse | null>(() => seeded<ReadingStatsResponse>('stats') ?? null)
  const [estimate, setEstimate] = useState<BookEstimate | null>(() => { const e = seeded<BookEstimate>('estimate'); if (e) lastPace = { token: currentToken(), value: e.pace }; return e ?? null })
  // The late answers: the sections under the hero wait for them (or REVEAL_CAP_MS)
  // and arrive together, so none of them lands later and pushes the others.
  const [settled, setSettled] = useState(() => ({ stats: known('stats'), annotations: known('annotations'), estimate: known('estimate') }))
  const settle = (k: keyof typeof settled) => setSettled(v => (v[k] ? v : { ...v, [k]: true }))
  // A page handed its answers by the move from another book has waited already.
  const [capped, setCapped] = useState(() => !!seed?.waited)
  // Mounted with everything a block needs: it just appears with the page.
  const [rise] = useState(() => !(known('book') && known('stats') && known('annotations')))
  const [riseRail] = useState(() => rise || !known('estimate'))
  // Per-book session list (#150) - collapsed by default; the always-available
  // place to trim/delete a runaway session (the Stats tiles are removable).
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [showPositionHistory, setShowPositionHistory] = useState(false)
  const [showShare, setShowShare] = useState(false)
  const [editingNoteId, setEditingNoteId] = useState<number | string | null>(null)
  const [editingNoteDraft, setEditingNoteDraft] = useState('')
  const [descExpanded, setDescExpanded] = useState(false)
  // "Show more" only when the 4-line clamp actually hides text.
  const descRef = useRef<HTMLParagraphElement>(null)
  const [descClamped, setDescClamped] = useState(false)
  // While the description opens or closes it is unclamped and its height runs.
  const [descMoving, setDescMoving] = useState(false)
  const descRun = useRef<Animation | null>(null)
  const descShortH = useRef(0)
  const [downloadMenuOpen, setDownloadMenuOpen] = useState(false)
  const downloadMenuRef = useRef<HTMLDivElement>(null)
  const [seriesVolumes, setSeriesVolumes] = useState<SeriesVolume[]>(() => seeded<SeriesVolume[]>('series') ?? [])
  const [seriesIn, setSeriesIn] = useState(() => known('series'))
  // eslint-disable-next-line lingui/no-unlocalized-strings -- media query (Tailwind lg)
  const [railBeside] = useState(() => window.matchMedia('(min-width: 64rem)').matches)
  const seriesSeeded = useRef(known('series'))
  const seriesPending = useRef(seed && !known('series') ? (seed.pending.series as Promise<SeriesVolume[]> | undefined) ?? null : null)
  const [quickLookId, setQuickLookId] = useState<number | null>(null)
  const [quickLookFrom, setQuickLookFrom] = useState<{ origin?: DOMRect; cover?: string | null }>({})
  const [facets, setFacets] = useState<Facets>({ authors: [], series: [], tags: [], formats: [] })
  const [draftTags, setDraftTags] = useState<string[]>([])
  const [tagInput, setTagInput] = useState('')
  const [adjacent, setAdjacent] = useState<{
    prev: { id: number; title: string; series_index?: number } | null
    next: { id: number; title: string; series_index?: number } | null
    mode: 'series' | 'author' | null
  } | null>(() => seeded('adjacent') ?? null)

  const isDirty = editing && book != null && JSON.stringify(draft) !== JSON.stringify(book)
  // Same rule as the server: an admin changes any book, a member only the
  // books they uploaded. Libraries and sharing stay open to every member.
  const member = isMember(user)
  const canEdit = isAdmin(user) || (member && book != null && user != null && book.added_by === user.id)
  const canDelete = canEdit

  useEffect(() => {
    if (!isDirty) return
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault()
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [isDirty])


  useEffect(() => {
    if (!libMenuOpen) return
    function onMouseDown(e: MouseEvent) {
      if (libMenuRef.current && !libMenuRef.current.contains(e.target as Node)) {
        setLibMenuOpen(false)
      }
    }
    // Escape closes the menu and stops there: the page's own Escape (go back)
    // listens on window in the bubble phase and never sees it.
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      setLibMenuOpen(false)
    }
    document.addEventListener('mousedown', onMouseDown)
    window.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      window.removeEventListener('keydown', onKey, true)
    }
  }, [libMenuOpen])

  useEffect(() => {
    if (!downloadMenuOpen) return
    function onMouseDown(e: MouseEvent) {
      if (downloadMenuRef.current && !downloadMenuRef.current.contains(e.target as Node)) {
        setDownloadMenuOpen(false)
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      setDownloadMenuOpen(false)
    }
    document.addEventListener('mousedown', onMouseDown)
    window.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      window.removeEventListener('keydown', onKey, true)
    }
  }, [downloadMenuOpen])

  // Before paint: "Show more" arriving a frame late would push the sections under it.
  useLayoutEffect(() => {
    const el = descRef.current
    if (!el) { setDescClamped(false); return }
    // Not while it moves: it is unclamped then; it measures again when the clamp is back.
    const measure = () => { if (!descExpanded && !descMoving) setDescClamped(el.scrollHeight > el.clientHeight + 1) }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [book?.description, editing, descExpanded, descMoving, loading, devicesIn, settled, capped])

  const bookIn = book != null
  useEffect(() => {
    if (!bookIn) return
    const name = book?.series
    // The first answer came with the page, or is on its way with it.
    if (seriesSeeded.current) { seriesSeeded.current = false; return }
    const first = seriesPending.current
    seriesPending.current = null
    if (!name) { setSeriesVolumes([]); setSeriesIn(true); return }
    ;(first ?? api.get<{ books: SeriesVolume[] }>(seriesDetailUrl(name)).then(d => d.books ?? []))
      .then(setSeriesVolumes)
      .catch(() => setSeriesVolumes([]))
      .finally(() => setSeriesIn(true))
  }, [bookIn, book?.series])

  // The series page's hero (its first volume with a cover), fetched and decoded
  // once this page is idle: every link to the series hands it on, so the series
  // page paints it in its first frame, hovered first or not.
  const seriesHeroId = seriesVolumes.find(v => v.cover_path)?.id
  useEffect(() => {
    const name = book?.series
    if (!name || seriesHeroId == null) return
    const hint = () => { hintSeries(name, coverSrc(seriesHeroId, { w: 400 })); prefetchSeries(name) }
    if (typeof window.requestIdleCallback !== 'function') {
      const timer = setTimeout(hint, 300)
      return () => clearTimeout(timer)
    }
    const idle = window.requestIdleCallback(hint, { timeout: 1500 })
    return () => window.cancelIdleCallback(idle)
  }, [book?.series, seriesHeroId])

  // Waiting to swap to another book: this page is on its way out, so its
  // shortcuts (r, e, Escape) must not act on the outgoing book.
  const swapping = next != null
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.defaultPrevented || swapping) return
      // A dialog (ModalShell, the quick look) owns the keyboard while open.
      if (isModalOpen() || quickLookId != null) return
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.key === 'Escape') {
        // The page's own overlays first: an open menu closes itself (capture
        // listeners above); the inline delete confirm cancels; Edit mode never
        // leaves the page with unsaved changes behind.
        if (libMenuOpen || downloadMenuOpen || kosyncPickerOpen) return
        if (confirmDelete) { e.preventDefault(); setConfirmDelete(false); return }
        if (editing) return
        e.preventDefault()
        navigate(-1)
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'r' && book) {
        const hasReadableFile = book.files?.some(f => ['epub', 'cbz', 'cbr', 'pdf'].includes(f.format))
        if (hasReadableFile) {
          navigate(`/reader/${book.id}`)
        }
        return
      }
      if (e.key === 'e' && canEdit) {
        e.preventDefault()
        if (editing) {
          cancelEdit()
        } else {
          startEdit()
        }
        return
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [navigate, book, editing, canEdit, quickLookId, libMenuOpen, downloadMenuOpen, kosyncPickerOpen, confirmDelete, swapping])

  useEffect(() => {
    if (!id) return
    dropBookSeed(Number(id))
    // The shell scrolls inside <main>; a new book starts at its top.
    document.querySelector('main')?.scrollTo(0, 0)
    const urls = bookRequests(id)
    // An answer that came with the page is in its state already; one on its way is taken, not asked again.
    const load = <T,>(k: Exclude<SeedKey, 'series'>): Promise<T> | null =>
      known(k) ? null : ((seed?.pending[k] as Promise<T> | undefined) ?? api.get<T>(urls[k]))
    load<BookDetail>('book')
      ?.then(b => { setBook(b); setDraft(b); setLocalLibIds(b.library_ids ?? []) })
      .catch(() => setError(t`Book not found`))
      .finally(() => setLoading(false))
    // With a copy from the last book the refresh waits until the page is in (one request fewer while it loads).
    const libs = () => { const token = currentToken(); return api.get<LibraryType[]>('/libraries').then(l => { lastLibraries = { token, value: l }; setLibraries(l); setLibrariesIn(true) }).catch(() => toast.error(t`Failed to load libraries`)) }
    const libsLater = librariesIn ? setTimeout(libs, 1500) : (libs(), undefined)
    load<BookStatus>('status')?.then(s => { setBookStatus(s.status); setStatusKnown(true); setProgressPct(s.progress_pct); setCfi(s.cfi ?? null); setProgressAnimated(false); setRating(s.rating ?? null); setReview(s.review ?? ''); setEditingReview(false) }).catch(() => setStatusKnown(true))
    load<typeof adjacent>('adjacent')?.then(setAdjacent).catch(() => {})
    load<KosyncStatus>('kosync')
      ?.then(r => {
        if (r.linked && r.device) setKosyncDevice(r.device)
        setKosyncLinked(r.linked ? { percentage: r.percentage ?? null, timestamp: r.timestamp ?? null } : null)
        setKosyncUnlinked(!r.linked && r.unlinked_documents ? r.unlinked_documents : [])
      })
      .catch(() => {})  // KOSync is optional - silent fail is fine
    load<Annotation[]>('annotations')?.then(setAnnotations).catch(() => {}).finally(() => settle('annotations'))
    load<ReadingStatsResponse>('stats')?.then(setReadingStats).catch(() => {}).finally(() => settle('stats'))
    const paceToken = currentToken()
    load<BookEstimate>('estimate')?.then(e => { lastPace = { token: paceToken, value: e.pace }; setEstimate(e) }).catch(() => {}).finally(() => settle('estimate'))
    const cap = setTimeout(() => setCapped(true), REVEAL_CAP_MS)
    return () => { clearTimeout(cap); clearTimeout(libsLater) }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per mount (the page is keyed on the book)
  }, [id])

  // The neighbours' covers, fetched ahead so previous/next paints its cover at once.
  useEffect(() => {
    for (const n of [adjacent?.prev, adjacent?.next]) {
      const v = n && seriesVolumes.find(x => x.id === n.id)
      if (v?.cover_path) new Image().src = coverSrc(v.id, { w: 160, v: v.updated_at })
    }
  }, [adjacent, seriesVolumes])

  // Moving to another book: its hero shows at the top while the rest loads.
  const heroTitleRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (!next && heroTitleRef.current) heroTitleH = heroTitleRef.current.offsetHeight
  })
  const nextId = next?.id
  useLayoutEffect(() => {
    if (nextId != null) document.querySelector('main')?.scrollTo(0, 0)
  }, [nextId])
  // The step's cue, on the book shown now (the one moved to, or this one once it is in).
  const coverRef = useRef<HTMLDivElement>(null)
  const shownId = nextId ?? book?.id
  useLayoutEffect(() => {
    if (shownId != null) playCue(shownId, [coverRef.current, heroTitleRef.current])
  }, [shownId])

  // Animate progress bar from 0 after it loads (one frame at 0 first)
  useEffect(() => {
    if (progressPct != null && progressPct > 0 && !progressAnimated) {
      const t = setTimeout(() => setProgressAnimated(true), 20)
      return () => clearTimeout(t)
    }
  }, [progressPct, progressAnimated])

  async function toggleLibrary(libId: number) {
    if (!book || libPending.has(libId)) return
    const inLib = localLibIds.includes(libId)
    // optimistic update
    setLocalLibIds(prev => inLib ? prev.filter(x => x !== libId) : [...prev, libId])
    setLibPending(prev => new Set(prev).add(libId))
    try {
      if (inLib) {
        await api.delete(`/libraries/${libId}/books/${book.id}`)
      } else {
        await api.post(`/libraries/${libId}/books`, { book_ids: [book.id] })
      }
    } catch {
      // revert on error
      setLocalLibIds(prev => inLib ? [...prev, libId] : prev.filter(x => x !== libId))
    } finally {
      setLibPending(prev => { const s = new Set(prev); s.delete(libId); return s })
    }
  }

  // The bar keeps its width and moves to the new one (progressAnimated stays
  // set); only a bar that was not shown yet grows from zero.
  function applyStatus(s: BookStatus) {
    setBookStatus(s.status)
    setProgressPct(s.progress_pct)
    setCfi(s.cfi ?? null)
  }

  async function restoreStatus(prev: { status: ReadingStatus; progress_pct: number | null; cfi: string | null }) {
    if (!id) return
    try {
      // Send progress + cfi back too, so undoing an "unread" (which clears them) restores your position.
      const restored = await api.put<BookStatus>(`/books/${id}/status`, prev)
      applyStatus(restored)
    } catch {
      toast.error(t`Failed to undo`)
    }
  }

  const STATUS_LABEL: Record<ReadingStatus, string> = {
    unread: t`Marked unread, reading progress cleared`,
    reading: t`Marked as reading`,
    read: t`Marked as read`,
    shelved: t`Put on hold, kept your progress`,
    want_to_read: t`Added to Want to Read`,
  }

  async function handleStatusChange(s: ReadingStatus) {
    if (!id || statusSaving || s === bookStatus) return
    const prev = { status: bookStatus, progress_pct: progressPct, cfi }
    setStatusSaving(true)
    setBookStatus(s)
    try {
      const updated = await api.put<BookStatus>(`/books/${id}/status`, { status: s })
      applyStatus(updated)
      toast.info(STATUS_LABEL[s], { action: { label: t`Undo`, onClick: () => restoreStatus(prev) } })
    } catch {
      setBookStatus(prev.status)
      toast.error(t`Failed to update reading status`)
    } finally {
      setStatusSaving(false)
    }
  }

  async function linkKosyncDocument() {
    if (!id || !kosyncLinkDoc || kosyncLinking) return
    setKosyncLinking(true)
    try {
      await api.post(`/books/${id}/link-kosync`, { document: kosyncLinkDoc })
      // Linking applies the synced progress to this book - refresh both.
      const [ks, s] = await Promise.all([
        api.get<KosyncStatus>(`/books/${id}/kosync-progress`),
        api.get<BookStatus>(`/books/${id}/status`),
      ])
      if (ks.linked && ks.device) setKosyncDevice(ks.device)
      setKosyncLinked(ks.linked ? { percentage: ks.percentage ?? null, timestamp: ks.timestamp ?? null } : null)
      setKosyncUnlinked(!ks.linked && ks.unlinked_documents ? ks.unlinked_documents : [])
      applyStatus(s)
      setKosyncPickerOpen(false)
      toast.info(t`KOReader sync linked`)
    } catch {
      toast.error(t`Failed to link sync document`)
    } finally {
      setKosyncLinking(false)
    }
  }

  async function saveRating(next: number | null) {
    if (!id) return
    const prev = rating
    setRating(next)                  // optimistic
    try {
      await api.put<BookStatus>(`/books/${id}/rating`, { rating: next })
    } catch {
      setRating(prev)
      toast.error(t`Failed to save rating`)
    }
  }

  function startEditingReview() {
    setReviewDraft(review)
    setEditingReview(true)
  }

  async function saveReview() {
    if (!id) return
    const next = reviewDraft.trim()
    setReview(next)                  // optimistic - collapse to rendered view
    setEditingReview(false)
    try {
      await api.put<BookStatus>(`/books/${id}/rating`, { review: next || null })
    } catch {
      toast.error(t`Failed to save review`)
    }
  }

  async function handleDelete() {
    if (!book) return
    setDeleting(true)
    try {
      await api.delete(`/books/${book.id}`)
      toast.success(t`Book deleted`)
      navigate('/')
    } catch (err) {
      const msg = err instanceof Error ? err.message : t`Delete failed`
      setError(msg)
      toast.error(msg)
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  function startEdit() {
    if (!book) return
    setDraft({ ...book })
    setDraftTags(book.tags.map(t => t.tag))
    setDraftBookTypeId(book.book_type_id ?? null)
    setEditing(true)
    api.get<Facets>('/books/facets').then(setFacets).catch(() => toast.error(t`Failed to load facets`))
  }
  function cancelEdit() {
    if (!book) return
    setDraft({ ...book })
    setDraftTags(book.tags.map(t => t.tag))
    setDraftBookTypeId(book.book_type_id ?? null)
    setTagInput('')
    setEditing(false)
  }

  async function saveEdit() {
    if (!book) return
    setSaving(true)
    try {
      const updated = await api.put<BookDetail>(`/books/${book.id}`, {
        title: draft.title,
        subtitle: draft.subtitle,
        author: draft.author,
        series: draft.series,
        series_index: draft.series_index,
        isbn: draft.isbn,
        publisher: draft.publisher,
        description: draft.description,
        language: draft.language,
        year: draft.year,
        tags: draftTags,
        book_type_id: draftBookTypeId,
        content_type: draft.content_type,
      })
      setBook(updated)
      setDraftTags(updated.tags.map(t => t.tag))
      setDraftBookTypeId(updated.book_type_id ?? null)
      setEditing(false)
      toast.success(t`Metadata saved`)
    } catch (err) {
      setError(err instanceof Error ? err.message : t`Save failed`)
      toast.error(err instanceof Error ? err.message : t`Save failed`)
    } finally {
      setSaving(false)
    }
  }

  function field(key: keyof BookDetail) {
    return (draft as Record<string, unknown>)[key] as string | undefined
  }
  function setField(key: keyof BookDetail, value: string) {
    setDraft(d => ({ ...d, [key]: value || null }))
  }

  // Until the book is in: the page frame, its hero from the preview when a
  // link handed one over, else the cover's box and the title's lines held
  // empty (no loader in the middle of an empty page).
  if (loading || !devicesIn) return (
    <>
      <PageBody width="wide">
        <div className={cn(HEADER_CLS, 'content-start')} style={heldHeader ? { minHeight: heldHeader } : undefined}>
          <nav className={CRUMBS_CLS} aria-label={t`Breadcrumb`}>
            <Link to="/?tab=books" className="shrink-0 hover:text-foreground transition-colors"><Trans>All books</Trans></Link>
          </nav>
        </div>
        <div className={GRID_CLS} aria-busy>
          <div className={RAIL_COVER_CLS}>
            <div className={COVER_CLS} style={preview ? { boxShadow: 'var(--cover-bloom-hero)' } : undefined} data-hero-cover={preview?.id}>
              {preview && <HeroCover thumb={preview.cover} full={null} alt={preview.title} color={preview.coverColor} />}
            </div>
          </div>
          <div className={MAIN_COL_CLS}>
            {!preview ? (
              <div className={HERO_CLS}>
                <div className="mt-2 h-[34px] w-3/5 rounded-[8px] bg-muted" />
                <div className="mt-3 h-[18px] w-2/5 rounded-[6px] bg-muted/70" />
              </div>
            ) : (
            <div className={HERO_CLS}>
              {preview.series && (
                <button type="button" className={SERIES_LINE_CLS}>
                  {preview.series}{preview.series_index != null && (() => { const v = preview.series_index; return <> · <Trans>Volume {v}</Trans></> })()}
                </button>
              )}
              <h1 className={TITLE_CLS}>{preview.title}</h1>
              {preview.author && <button type="button" className={AUTHOR_CLS}>{preview.author}</button>}
            </div>
            )}
          </div>
        </div>
      </PageBody>
      {/* The page's children are a list (PageBody, then its dialogs); the same
          shape here lets React keep the hero element when the page arrives. */}
      {null}
    </>
  )

  if (error || !book) return (
    <>
      <div className="flex h-full min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
        <p className="text-muted-foreground">{error ?? t`Book not found`}</p>
        <Link to="/" className="text-sm text-primary hover:underline"><Trans>Back to library</Trans></Link>
      </div>
    </>
  )

  // ── Pieces ──────────────────────────────────────────────────────────────────

  // Show more / less: the paragraph's height runs from where it is to the
  // other state (220 ms); a second click turns it around midway. The clamp
  // (with its ellipsis) is off while it moves and comes back when it is short.
  function toggleDescription() {
    const el = descRef.current
    const open = !descExpanded
    // eslint-disable-next-line lingui/no-unlocalized-strings -- media query
    if (!el || window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setDescExpanded(open); return }
    const from = el.getBoundingClientRect().height
    if (open && !descMoving) descShortH.current = from
    descRun.current?.cancel()
    flushSync(() => { setDescExpanded(open); setDescMoving(true) })
    const short = descShortH.current || 4 * parseFloat(getComputedStyle(el).lineHeight)
    const to = open ? el.scrollHeight : short
    el.style.overflow = 'hidden'
    // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS easing
    const run = el.animate([{ height: `${from}px` }, { height: `${to}px` }], { duration: 220, easing: EASE_CSS })
    descRun.current = run
    run.onfinish = () => {
      if (descRun.current !== run) return
      el.style.overflow = ''
      descRun.current = null
      setDescMoving(false)
    }
  }

  const refreshStats = () =>
    api.get<ReadingStatsResponse>(`/books/${id}/reading-stats?tz_offset=${new Date().getTimezoneOffset()}`).then(setReadingStats).catch(() => {})

  async function deleteHighlight(annotationId: number | string) {
    try {
      await api.delete(`/annotations/${annotationId}`)
      setAnnotations(prev => prev.filter(a => a.id !== annotationId))
      setConfirmingHighlight(null)
      toast.success(t`Highlight deleted`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t`Failed to delete highlight`)
    }
  }

  async function saveHighlightNote(annotationId: number | string) {
    try {
      const note = editingNoteDraft.trim() || null
      await api.put(`/annotations/${annotationId}`, { note })
      setAnnotations(prev => prev.map(a => (a.id === annotationId ? { ...a, note } : a)))
      setEditingNoteId(null)
      toast.success(note ? t`Note saved, syncs to your devices` : t`Note removed`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t`Failed to save note`)
    }
  }

  async function downloadOne(f: BookFile) {
    if (!book) return
    setDownloadMenuOpen(false)
    setDownloadingId(f.id)
    try { await downloadFile(book, f) }
    catch { toast.error(t`Download failed`) }
    finally { setDownloadingId(null) }
  }

  // The main column waits for its late answers (reading stats, highlights) so
  // neither lands later and pushes the other; the rail also for the estimate.
  const below = (settled.stats && settled.annotations) || capped
  // A book with a word count always gets an estimate: its slot is held (filled
  // at once from the last pace seen), so the rail need not wait for the answer.
  const pace = mine(lastPace)
  const shownEstimate = estimate ?? (book.word_count && pace ? estimateFromPace(book.word_count, pace) : null)
  // Past the cap the sections still arrive in order: one never lands above one
  // already shown (the reading blocks before highlights, both before the series).
  const statsIn = settled.stats
  const notesIn = statsIn && settled.annotations
  // On a phone the rail comes after the whole column, the series strip included.
  const belowRail = railBeside
    ? (below && (settled.estimate || !!book.word_count)) || capped
    : notesIn && seriesIn && (settled.estimate || !!book.word_count || capped)
  // While the next book loads, everything of this one but the hero's lines dims and stops taking input.
  const dim = cn('transition-opacity duration-150', next && 'pointer-events-none opacity-40')
  const readable = book.files.some(f => ['epub', 'cbz', 'cbr', 'pdf'].includes(f.format))
  const showProgress = progressPct != null && progressPct > 0 && bookStatus !== 'unread'
  const pctShown = Math.round(progressPercent(progressPct))
  const own = readingStats?.own ?? null
  const hasSessions = !!own && own.sessions > 0
  const typeLabel = bookTypes.find(bt => bt.id === book.book_type_id)?.label
  const seriesHref = book.series ? `/?tab=series&series_detail=${encodeURIComponent(book.series)}` : null
  const shortDay = (d: Date) => d.toLocaleDateString(i18n.locale, { month: 'short', day: 'numeric' })
  /* eslint-disable lingui/no-unlocalized-strings -- Tailwind classes */
  const inputCls = INPUT_CLASS
  const smallCaps = 'text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground'
  const menuCls = 'absolute top-full mt-1.5 z-40 min-w-52 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl bg-card p-1 shadow-lg ring-1 ring-panel-ring'
  /* eslint-enable lingui/no-unlocalized-strings */

  const STATUS_PILLS: { id: ReadingStatus; label: string; title?: string }[] = [
    { id: 'unread', label: t`Unread` },
    { id: 'reading', label: t`Reading` },
    { id: 'read', label: t`Read` },
    { id: 'want_to_read', label: t`Want to read`, title: t`Queue this book up next` },
    { id: 'shelved', label: t`On hold`, title: t`Set aside, keeps your progress` },
  ]

  // ── Header line: where you are, previous/next volume, the page's actions ────

  // What the next page's hero starts with: the neighbour's title in this
  // series and the cover thumbnail the strip shows (fetched ahead above).
  const neighbour = (n: { id: number; title: string; series_index?: number }): BookPreview => {
    const v = seriesVolumes.find(x => x.id === n.id)
    const sameSeries = adjacent?.mode === 'series' && !!book.series
    return {
      id: n.id, title: n.title,
      author: adjacent?.mode ? book.author : undefined,
      series: sameSeries ? book.series : undefined,
      series_index: sameSeries ? (n.series_index ?? v?.series_index ?? null) : undefined,
      cover: v ? (v.cover_path ? coverSrc(v.id, { w: 160, v: v.updated_at }) : null) : undefined,
      coverColor: v?.cover_color ?? null,
    }
  }

  // Pointed at or pressed before this page went idle: the series hero is asked for then.
  // Its answers are asked for again then when a few seconds old (a status may have changed).
  const warm = () => {
    if (!book.series) return
    if (seriesHeroId != null) hintSeries(book.series, coverSrc(seriesHeroId, { w: 400 }))
    prefetchSeries(book.series, SERIES_WARM_MS)
  }
  // Opening the series: the cover nearest the link grows into the series
  // hero (the crumb: this book's hero; "Open series": the series' first
  // cover in the strip below it); going back it lands on this hero.
  const toSeries = (from: 'hero' | 'strip') => (e: ReactMouseEvent<HTMLAnchorElement>) => {
    if (!book.series || !isPlainClick(e)) return
    e.preventDefault()
    prefetchSeries(book.series, SERIES_WARM_MS)
    const el = from === 'hero' ? coverRef.current : stripCover(e.currentTarget) ?? coverRef.current
    openSeries(book.series, el, {
      near: e.currentTarget,
      from: {
        id: book.id, title: book.title, author: book.author, series: book.series, series_index: book.series_index,
        cover: book.cover_path ? heroCoverSrc(book) : null, coverColor: book.cover_color,
      },
    })
  }
  const warmSeries = { onPointerEnter: warm, onPointerDown: warm, onFocus: warm }

  const crumbs = (
    <nav className={CRUMBS_CLS} aria-label={t`Breadcrumb`}>
      <Link to="/?tab=books" className="shrink-0 hover:text-foreground transition-colors"><Trans>All books</Trans></Link>
      {book.series && seriesHref ? (
        <>
          <span aria-hidden>/</span>
          <SeriesLink name={book.series} {...warmSeries} onClick={toSeries('hero')} className="truncate hover:text-foreground transition-colors">{book.series}</SeriesLink>
        </>
      ) : adjacent?.mode === 'author' && book.author ? (
        <>
          <span aria-hidden>/</span>
          <Link to={`/?tab=books&author=${encodeURIComponent(book.author)}`} className="truncate hover:text-foreground transition-colors">{book.author}</Link>
        </>
      ) : null}
      {adjacent && (adjacent.prev || adjacent.next) && (
        <span className="ml-1 inline-flex shrink-0 items-center">
          {adjacent.prev ? (
            <BookLink book={neighbour(adjacent.prev)} cover={null} title={adjacent.prev.title} aria-label={t`Previous volume`}
              onClick={() => { cue = { id: adjacent.prev!.id, dir: -1, at: performance.now(), started: 0 } }}
              className={cn('grid h-7 w-7 place-items-center rounded-full hover:bg-secondary hover:text-foreground touch-feedback', PRESS)}>
              <ChevronLeft className="h-4 w-4" />
            </BookLink>
          ) : <span className="grid h-7 w-7 place-items-center opacity-30" aria-hidden><ChevronLeft className="h-4 w-4" /></span>}
          {adjacent.next ? (
            <BookLink book={neighbour(adjacent.next)} cover={null} title={adjacent.next.title} aria-label={t`Next volume`}
              onClick={() => { cue = { id: adjacent.next!.id, dir: 1, at: performance.now(), started: 0 } }}
              className={cn('grid h-7 w-7 place-items-center rounded-full hover:bg-secondary hover:text-foreground touch-feedback', PRESS)}>
              <ChevronRight className="h-4 w-4" />
            </BookLink>
          ) : <span className="grid h-7 w-7 place-items-center opacity-30" aria-hidden><ChevronRight className="h-4 w-4" /></span>}
        </span>
      )}
    </nav>
  )

  const actionsMode = editing ? 'edit' : confirmDelete ? 'confirm' : 'view'
  const pageActions = editing ? (
    <div className="flex items-center gap-1.5">
      <Pill variant="quiet" onClick={cancelEdit}><X className="h-4 w-4" /><Trans>Cancel</Trans></Pill>
      <Pill variant="primary" onClick={saveEdit} disabled={saving}>
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
        {saving ? t`Saving…` : t`Save`}
      </Pill>
    </div>
  ) : confirmDelete ? (
    <div className="flex items-center gap-1.5">
      {(() => { const title = book.title; return <span className="hidden max-w-[220px] truncate text-[14px] text-muted-foreground sm:inline"><Trans>Delete "{title}"?</Trans></span> })()}
      <Pill variant="quiet" onClick={() => setConfirmDelete(false)}><Trans>Cancel</Trans></Pill>
      <Pill onClick={handleDelete} disabled={deleting} className="bg-destructive text-white hover:bg-destructive/90">
        {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
        {deleting ? t`Deleting…` : t`Delete`}
      </Pill>
    </div>
  ) : (
    <div className="-mr-2 ml-auto flex flex-wrap items-center justify-end gap-0.5">
      {/* First, so its late arrival or longer label ("Finish by Dec 31") moves nothing to its right. */}
      {/* Its slot is held on every book at its widest label (unseen under it), so a read volume,
          which has none, or one with a goal leaves the actions and the previous/next arrows where they were. */}
      {book.content_type !== 'chapter' && (
        <span className="grid">
          <span className="invisible col-start-1 row-start-1 flex justify-end" aria-hidden inert>
            <FinishByReserve />
          </span>
          <span className="col-start-1 row-start-1 flex justify-end">
            <FinishByPill target={{ bookId: book.id, title: book.title, cover: book.cover_path ? book.id : null }} done={bookStatus === 'read'} pending={!statusKnown} iconOnPhone />
          </span>
        </span>
      )}
      {canEdit && (
        <Pill variant="quiet" onClick={startEdit} aria-label={t`Edit`}>
          <Edit2 className="h-4 w-4" /><span className="hidden sm:inline"><Trans>Edit</Trans></span>
        </Pill>
      )}
      {canEdit && book.content_type !== 'chapter' && (
        <Pill variant="quiet" onClick={() => setFetchModalOpen(true)} aria-label={t`Fetch metadata`}>
          <Sparkles className="h-4 w-4" /><span className="hidden sm:inline"><Trans>Fetch metadata</Trans></span>
        </Pill>
      )}
      {/* A book without a file is a personal reading record: it never goes into a library. */}
      {/* An admin edits every library: until the list is in, the pill holds its place unseen. */}
      {member && book.status !== 'no_file' && (libraries.some(l => l.can_edit) || (!librariesIn && isAdmin(user))) && (
        <div className="relative" ref={libMenuRef}>
          <Pill variant="quiet" onClick={() => setLibMenuOpen(o => !o)} aria-expanded={libMenuOpen} aria-label={t`Libraries`}
            disabled={!librariesIn} aria-hidden={!librariesIn || undefined} tabIndex={librariesIn ? undefined : -1}
            className={cn(libMenuOpen && 'bg-secondary text-foreground', !librariesIn && 'invisible')}>
            <LibraryIcon className="h-4 w-4" /><span className="hidden sm:inline"><Trans>Libraries</Trans></span>
            {localLibIds.length > 0 && (
              <span className="ml-0.5 grid h-5 min-w-5 place-items-center rounded-full bg-secondary px-1.5 text-[12px] font-semibold tabular-nums text-foreground">
                <RunningNumber value={localLibIds.length} />
              </span>
            )}
          </Pill>
          <Pop open={libMenuOpen} origin="top" role="menu" className={cn(menuCls, 'left-0 w-60 origin-top-left sm:left-auto sm:right-0 sm:origin-top-right')}>
              <p className="px-3 pb-1.5 pt-2 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
                <Trans>Add to library</Trans>
              </p>
              {libraries.filter(l => l.can_edit).map(lib => {
                const inLib = localLibIds.includes(lib.id)
                const pending = libPending.has(lib.id)
                return (
                  <div key={lib.id} className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[14px] hover:bg-secondary transition-colors">
                    {pending
                      ? <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
                      : <NamedIcon icon={lib.icon} className="h-4 w-4 shrink-0 text-muted-foreground" />}
                    <Link to={`/?tab=books&library_id=${lib.id}`} onClick={() => setLibMenuOpen(false)}
                      className="min-w-0 flex-1 truncate text-foreground hover:text-primary transition-colors">
                      {lib.name}
                    </Link>
                    <button type="button" role="menuitemcheckbox" aria-checked={inLib}
                      onClick={() => toggleLibrary(lib.id)} disabled={pending}
                      className="grid h-7 w-7 shrink-0 place-items-center rounded-full hover:bg-surface-hover transition-colors disabled:opacity-60"
                      title={inLib ? t`Remove from library` : t`Add to library`}
                      aria-label={inLib ? t`Remove from library` : t`Add to library`}>
                      <span className={cn('grid h-[18px] w-[18px] place-items-center rounded-[5px] transition-colors',
                        inLib ? 'bg-primary' : 'ring-1 ring-border')}>
                        {inLib && <Check className="h-3 w-3 text-primary-foreground" strokeWidth={3} />}
                      </span>
                    </button>
                  </div>
                )
              })}
          </Pop>
        </div>
      )}
      {member && book.status !== 'no_file' && (
        <Pill variant="quiet" onClick={() => setShowShare(true)} title={t`Share this book as a public page with its metadata only`} aria-label={t`Share`}>
          <Share2 className="h-4 w-4" /><span className="hidden sm:inline"><Trans>Share</Trans></span>
        </Pill>
      )}
      {canDelete && (
        <Pill variant="destructive" icon onClick={() => setConfirmDelete(true)} aria-label={t`Delete`} title={t`Delete`}>
          <Trash2 className="h-4 w-4" />
        </Pill>
      )}
    </div>
  )

  // ── Rail: cover, files, details, genres ─────────────────────────────────────

  // Moving to another book: its cover takes the hero now (keyed, so the old
  // picture is gone at once instead of lingering under a loading one).
  const cover = (
    <div
      className={cn(COVER_CLS, canEdit && !next && 'group cursor-pointer')}
      style={{ boxShadow: 'var(--cover-bloom-hero)' }}
      onClick={() => canEdit && !next && setCoverPickerOpen(true)}
      title={canEdit && !next ? t`Change cover` : undefined}
      data-hero-cover={next?.id ?? book.id}
      ref={coverRef}
    >
      {next ? (
        next.cover !== undefined
          ? <HeroCover key={next.id} thumb={next.cover} full={null} alt={next.title} color={next.coverColor} />
          : <CoverImage key={next.id} src={coverSrc(next.id, { w: 400 })} alt={next.title} loading="eager" color={next.coverColor} iconClassName="w-12 h-12" />
      ) : (
      <HeroCover
        thumb={book.cover_path ? preview?.cover : null}
        full={book.cover_path ? heroCoverSrc(book) : null}
        alt={book.title}
        color={book.cover_color ?? preview?.coverColor}
      />
      )}
      {canEdit && !next && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 transition-opacity group-hover:opacity-100">
          <Camera className="h-8 w-8 text-white drop-shadow" />
        </div>
      )}
    </div>
  )

  const fileLines = book.files.length > 0 ? (
    <div className="mt-4 space-y-1 px-1">
      {book.files.map(f => (
        <div key={f.id} className="flex items-center justify-between text-[13px] text-muted-foreground">
          <span className="uppercase">{f.format}</span>
          {f.file_size ? <span className="tabular-nums">{formatBytes(f.file_size)}</span> : null}
        </div>
      ))}
    </div>
  ) : null

  // The label says "Words"; the value is the number alone.
  const wordsValue = book.word_count != null ? book.word_count.toLocaleString(i18n.locale) : null

  const details = editing ? (
    <div className="grid grid-cols-1 gap-y-3">
      <EditField label={t`Year`} value={field('year') ?? ''} onChange={v => setField('year', v)} type="number" placeholder="2024" />
      <EditField label={t`Language`} value={field('language') ?? ''} onChange={v => setField('language', v)} placeholder="en" />
      <EditField label={t`ISBN`} value={field('isbn') ?? ''} onChange={v => setField('isbn', v)} placeholder="978-..." />
      <EditField label={t`Publisher`} value={field('publisher') ?? ''} onChange={v => setField('publisher', v)} placeholder={t`Publisher`} />
      <div>
        <FormLabel htmlFor="edit-type"><Trans>Type</Trans></FormLabel>
        <Select id="edit-type" value={draftBookTypeId ?? ''} onChange={e => setDraftBookTypeId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">{t`No type`}</option>
          {bookTypes.map(bt => <option key={bt.id} value={bt.id}>{bt.label}</option>)}
        </Select>
      </div>
      <div>
        <FormLabel htmlFor="edit-content"><Trans>Content</Trans></FormLabel>
        <Select id="edit-content" value={draft.content_type ?? 'volume'} onChange={e => setDraft(d => ({ ...d, content_type: e.target.value }))}>
          <option value="volume">{t`Volume`}</option>
          <option value="chapter">{t`Chapter`}</option>
        </Select>
      </div>
    </div>
  ) : (
    <div className="grid grid-cols-2 gap-x-4 gap-y-4">
      {book.year != null && <Field label={t`Year`} value={String(book.year)} />}
      {book.language && <Field label={t`Language`} value={book.language} />}
      {book.files.length > 0 && <Field label={t`Format`} value={book.files.map(f => f.format.toUpperCase()).join(', ')} />}
      {/* How long the whole book would take at your pace (#187). In-progress
          books additionally get "Est. remaining" in the Reading section. */}
      {shownEstimate?.seconds != null ? (
        <Field label={t`Est. read time`} value={
          <span title={[shownEstimate.days != null ? formatEstimateDays(shownEstimate.days) : null, describePace(shownEstimate.pace, shownEstimate.method)].filter(Boolean).join(' · ')}>
            {formatEstimateHours(shownEstimate.seconds)}
          </span>
        } />
      ) : !settled.estimate && book.word_count ? (
        <Field label={t`Est. read time`} value={<span className="invisible">{formatEstimateHours(3600)}</span>} />
      ) : null}
      {/* Hardcover match, only when the sync matcher has linked this book */}
      {book.hardcover_slug && (
        // eslint-disable-next-line lingui/no-unlocalized-strings -- product name
        <Field label="Hardcover" value={
          <a href={`https://hardcover.app/books/${book.hardcover_slug}`} target="_blank" rel="noopener noreferrer"
            title={book.hardcover_slug}
            className="inline-flex items-center gap-1 text-primary hover:underline underline-offset-4">
            <Trans>Open</Trans>
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        } />
      )}
      {typeLabel && <Field label={t`Type`} value={typeLabel} />}
      <Field label={t`Content`} value={book.content_type === 'chapter' ? t`Chapter` : t`Volume`} />
      {wordsValue && <Field label={t`Words`} value={<span className="tabular-nums">{wordsValue}</span>} />}
      {book.publisher && <Field label={t`Publisher`} value={book.publisher} />}
      {book.isbn && <Field label={t`ISBN`} value={<span className="break-all tabular-nums">{book.isbn}</span>} />}
    </div>
  )

  const genres = editing ? (
    <div>
      <FormLabel><Trans>Genres</Trans></FormLabel>
      {draftTags.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {draftTags.map(tag => (
            <span key={tag} className="inline-flex h-7 items-center gap-1 rounded-full bg-secondary pl-3 pr-2 text-[13px] text-foreground">
              {tag}
              <button type="button" onClick={() => setDraftTags(prev => prev.filter(x => x !== tag))}
                aria-label={t`Remove`} className="text-muted-foreground hover:text-destructive transition-colors">
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <AutocompleteInput
        value={tagInput}
        onChange={setTagInput}
        suggestions={facets.tags.filter(x => !draftTags.includes(x))}
        placeholder={t`Add tag…`}
        onSelect={tag => {
          if (tag && !draftTags.includes(tag)) {
            setDraftTags(prev => [...prev, tag])
            setTagInput('')
          }
        }}
        onEnter={val => {
          const trimmed = val.trim()
          if (trimmed && !draftTags.includes(trimmed)) {
            setDraftTags(prev => [...prev, trimmed])
            setTagInput('')
          }
        }}
        className={inputCls}
      />
    </div>
  ) : book.tags.length > 0 ? (
    <div>
      <div className="mb-2 text-[13px] text-muted-foreground"><Trans>Genres</Trans></div>
      <div className="flex flex-wrap gap-1.5">
        {book.tags.map(tg => (
          <button key={tg.id} type="button" onClick={() => navigate(`/?tab=books&tag=${encodeURIComponent(tg.tag)}`)}
            className={cn('inline-flex h-7 items-center rounded-full bg-secondary px-3 text-[13px] text-foreground hover:bg-surface-hover hover:text-primary touch-feedback', PRESS)}>
            {tg.tag}
          </button>
        ))}
      </div>
    </div>
  ) : null

  // The rail is two grid children so phones can read cover, then the hero,
  // then details; on desktop both sit in the left column.
  const railCover = (
    <div className={RAIL_COVER_CLS}>
      {cover}
      {fileLines}
    </div>
  )
  const railDetails = (
    <m.aside className="order-3 mx-auto w-full max-w-[420px] lg:order-none lg:mx-0 lg:max-w-none lg:col-start-1 lg:row-start-2"
      initial={riseRail ? RISE_FROM : false} animate={RISE_TO}>
      <div className={dim} inert={!!next}>
      <div className={cn(smallCaps, 'mb-3')}><Trans>Details</Trans></div>
      <Swap id={editing ? 'edit' : 'view'}>{details}</Swap>
      {genres && <Swap id={editing ? 'edit' : 'view'} className="mt-6">{genres}</Swap>}
      </div>
    </m.aside>
  )

  // ── Hero: series, title, author, status, progress, actions, sync, rating ───

  // The hero's lines for the book being moved to, from its preview.
  const nextTitle = next && next.title ? (
    <>
      {next.series && (
        <button type="button" className={SERIES_LINE_CLS} tabIndex={-1}>
          {next.series}{next.series_index != null && (() => { const v = next.series_index; return <> · <Trans>Volume {v}</Trans></> })()}
        </button>
      )}
      <h1 className={TITLE_CLS}>{next.title}</h1>
      {next.author && <button type="button" className={AUTHOR_CLS} tabIndex={-1}>{next.author}</button>}
    </>
  ) : null

  const heroTitle = nextTitle ?? (editing ? (
    <div className="flex flex-col gap-4">
      <div>
        <FormLabel htmlFor="edit-title"><Trans>Title</Trans></FormLabel>
        <Input id="edit-title" value={field('title') ?? ''} onChange={e => setField('title', e.target.value)} placeholder={t`Title`}
          className="h-12 font-display text-[22px] font-bold" />
      </div>
      <div>
        <FormLabel htmlFor="edit-subtitle" hint={t`optional`}><Trans>Subtitle</Trans></FormLabel>
        <Input id="edit-subtitle" value={field('subtitle') ?? ''} onChange={e => setField('subtitle', e.target.value)} placeholder={t`Subtitle`} />
      </div>
      <div>
        <FormLabel><Trans>Author</Trans></FormLabel>
        <AutocompleteInput value={field('author') ?? ''} onChange={v => setField('author', v)} suggestions={facets.authors}
          placeholder={t`Author`} className={inputCls} />
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_96px] gap-3">
        <div className="min-w-0">
          <FormLabel><Trans>Series</Trans></FormLabel>
          <AutocompleteInput value={field('series') ?? ''} onChange={v => setField('series', v)} suggestions={facets.series}
            placeholder={t`Series name`} className={inputCls} />
        </div>
        <div>
          <FormLabel htmlFor="edit-volume"><Trans>Volume</Trans></FormLabel>
          <Input id="edit-volume" value={field('series_index') ?? ''} onChange={e => setField('series_index', e.target.value)} type="number" step="0.1"
            placeholder="#" className="tabular-nums" />
        </div>
      </div>
    </div>
  ) : (
    <>
      {book.series && (
        <button type="button" onClick={() => navigate(`/?tab=books&series=${encodeURIComponent(book.series!)}`)}
          className={SERIES_LINE_CLS}>
          {book.series}{book.series_index != null && (() => { const v = book.series_index; return <> · <Trans>Volume {v}</Trans></> })()}
        </button>
      )}
      <h1 className={TITLE_CLS}>
        {book.title}
        {book.content_type === 'chapter' && (
          <span className="ml-3 inline-flex h-6 -translate-y-1 items-center rounded-full bg-secondary px-2.5 align-middle font-sans text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
            <Trans>Chapter</Trans>
          </span>
        )}
      </h1>
      {book.subtitle && <p className="mt-1.5 text-[16px] text-foreground/80">{book.subtitle}</p>}
      {book.author && (
        <button type="button" onClick={() => navigate(`/?tab=books&author=${encodeURIComponent(book.author!)}`)}
          className={AUTHOR_CLS}>
          {book.author}
        </button>
      )}
      <DevicePills bookId={book.id} className="mt-3 flex" />
    </>
  ))

  // One indicator glides to the chosen status (it moves on the click; the
  // request follows and undoes it on failure).
  const statusRow = (
    <GlideGroup value={bookStatus}>
      <div className="isolate mt-5 flex flex-wrap items-center gap-2">
        {STATUS_PILLS.map(s => (
          <button
            key={s.id}
            type="button"
            disabled={statusSaving}
            onClick={() => handleStatusChange(s.id)}
            title={s.title}
            aria-pressed={bookStatus === s.id}
            className={cn(
              'group/pg relative h-8 rounded-full bg-secondary px-3.5 text-[13px] font-medium transition-colors duration-200 disabled:cursor-default',
              bookStatus === s.id ? 'text-background' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {bookStatus === s.id && <Glide className="bg-foreground" />}
            <span className={cn('relative z-[1]', PRESS_LABEL)}>{s.label}</span>
          </button>
        ))}
        {statusSaving && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
      </div>
    </GlideGroup>
  )

  const progressRow = showProgress ? (
    <div className="mt-5 max-w-[560px]">
      <div className="flex items-center gap-3">
        <div className="h-[6px] flex-1 overflow-hidden rounded-full" style={{ background: 'var(--rail)' }}>
          <div className="h-full rounded-full bg-primary transition-[width] duration-300 ease-(--spring)"
            style={{ width: progressAnimated ? `${Math.min(100, pctShown)}%` : '0%' }} />
        </div>
        <span className="min-w-[3.25em] text-right text-[17px] font-bold proportional-nums text-foreground"><RunningNumber value={pctShown} className="proportional-nums" />%</span>
      </div>
      {book.hardcover_pages != null && book.hardcover_pages > 0 && (() => {
        // Print-edition pagination (from the matched Hardcover edition):
        // font-size agnostic, unlike the device's reflowed page count.
        const page = Math.max(1, Math.round(progressPercent(progressPct) / 100 * book.hardcover_pages))
        const totalPages = book.hardcover_pages
        return <div className="mt-1.5 text-[13px] text-muted-foreground"><Trans>p. {page} of {totalPages}</Trans></div>
      })()}
    </div>
  ) : null

  const downloadPill = book.files.length === 1 ? (
    <Pill size="lg" icon onClick={() => downloadOne(book.files[0])} disabled={downloadingId != null}
      aria-label={t`Download`} title={(() => { const fmt = book.files[0].format.toUpperCase(); return t`Download ${fmt}` })()}>
      {downloadingId != null ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
    </Pill>
  ) : book.files.length > 1 ? (
    <div className="relative" ref={downloadMenuRef}>
      <Pill size="lg" icon onClick={() => setDownloadMenuOpen(o => !o)} aria-expanded={downloadMenuOpen} aria-label={t`Download`} title={t`Download`}>
        {downloadingId != null ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
      </Pill>
      <Pop open={downloadMenuOpen} origin="top-left" className={cn(menuCls, 'left-0 min-w-44')}>
          {book.files.map(f => (
            <button key={f.id} type="button" onClick={() => downloadOne(f)} disabled={downloadingId === f.id}
              className="flex w-full items-center justify-between gap-4 rounded-lg px-3 py-2 text-[14px] text-foreground hover:bg-secondary transition-colors disabled:opacity-60">
              <span className="flex items-center gap-2 font-medium uppercase"><Download className="h-3.5 w-3.5 text-muted-foreground" />{f.format}</span>
              {f.file_size ? <span className="text-[13px] tabular-nums text-muted-foreground">{formatBytes(f.file_size)}</span> : null}
            </button>
          ))}
      </Pop>
    </div>
  ) : null

  // A book Tome only knows from a reader's sync: nothing to read or download here.
  const actionRow = book.status === 'no_file' ? (
    <p className="mt-5 max-w-[60ch] text-[14px] leading-relaxed text-muted-foreground">
      <Trans>Tome has no file for this book. It was added from your reader's sync so the reading has a place. If you add the book to your library, use Link KOReader sync on its page to move the reading there.</Trans>
    </p>
  ) : (readable || book.files.length > 0) ? (
    <div className="mt-5 flex flex-wrap items-center gap-2">
      {readable && (
        <Pill variant="primary" size="lg" onClick={() => navigate(`/reader/${book.id}`)}>
          <Play className="h-4 w-4 fill-current" />
          {bookStatus === 'read' && pctShown >= 100 ? <Trans>Read again</Trans> : showProgress ? <Trans>Resume</Trans> : <Trans context="action">Read</Trans>}
        </Pill>
      )}
      {readable && (
        <Pill
          variant="quiet"
          size="lg"
          onClick={() => navigate(`/reader/${book.id}?preview=1`)}
          title={t`Open in the reader without updating progress or stats`}
        >
          <Eye className="h-4 w-4" />
          <Trans>Preview</Trans>
        </Pill>
      )}
      {isMember(user) && book.files.length > 0 && (
        <SendButton books={[{ id: book.id, title: book.title, files: book.files }]} variant="pill" />
      )}
      {downloadPill}
    </div>
  ) : null

  const syncMode = kosyncDevice && kosyncLinked ? 'linked' : kosyncUnlinked.length > 0 && !kosyncDevice ? 'unlinked' : 'none'
  const syncRow = kosyncDevice && kosyncLinked ? (
    <div className="mt-4">
      <span className="inline-flex h-9 max-w-full items-center gap-2 rounded-full bg-secondary px-4 text-[14px] font-medium text-foreground">
        <RefreshCw className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">
          {(() => { const device = kosyncDevice; return <Trans>Synced from {device}</Trans> })()}
          <span className="text-muted-foreground">
            {kosyncLinked.percentage != null && ` · ${Math.round(kosyncLinked.percentage * 100)}%`}
            {kosyncLinked.timestamp ? ` · ${shortDay(new Date(kosyncLinked.timestamp * 1000))}` : ''}
          </span>
        </span>
      </span>
    </div>
  ) : kosyncUnlinked.length > 0 && !kosyncDevice ? (
    <div className="mt-4">
      <Swap id={kosyncPickerOpen ? 'picker' : 'link'}>
      {!kosyncPickerOpen ? (
        <Pill variant="quiet" className="-ml-3"
          onClick={() => { setKosyncPickerOpen(true); setKosyncLinkDoc(kosyncUnlinked[0]?.document ?? '') }}
          title={t`Attach progress synced from a KOReader-compatible app to this book`}>
          <Link2 className="h-4 w-4" /><Trans>Link KOReader sync</Trans>
        </Pill>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[14px] text-muted-foreground"><Trans>Synced document</Trans></span>
          <Select value={kosyncLinkDoc} onChange={e => setKosyncLinkDoc(e.target.value)} aria-label={t`Synced document`}
            className="w-auto max-w-[18rem]">
            {kosyncUnlinked.map(d => (
              <option key={d.document} value={d.document}>
                {d.title ? `${d.title} · ` : ''}{d.device || t`unknown device`} · {Math.round(d.percentage * 100)}% · {shortDay(new Date(d.timestamp * 1000))}
              </option>
            ))}
          </Select>
          <Pill disabled={kosyncLinking || !kosyncLinkDoc} onClick={linkKosyncDocument}>
            {kosyncLinking ? t`Linking…` : t`Link`}
          </Pill>
          <Pill variant="quiet" onClick={() => setKosyncPickerOpen(false)}><Trans>Cancel</Trans></Pill>
        </div>
      )}
      </Swap>
    </div>
  ) : null

  const ratingRow = (
    <div className="mt-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {/* Stars fill one after another up to the pointer (styles/view-transitions.css)
            once the pointer is on them; the clicked star pops. */}
        <div
          className="rating-motion"
          data-settled={ratingSettled ? '' : undefined}
          onPointerEnter={() => setRatingSettled(false)}
          onClickCapture={e => {
            const star = (e.target as Element).closest('button')
            if (star && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
              star.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.3)' }, { transform: 'scale(1)' }], { duration: 240, easing: EASE_CSS })
            }
          }}
        >
          <StarRating value={rating} onChange={saveRating} />
        </div>
        <span className="text-[13px] text-muted-foreground">
          {rating ? t`You rated this ${rating}/5` : t`Rate this book`}
        </span>
        {!review && !editingReview && (
          <button type="button" onClick={startEditingReview} className="text-[13px] text-primary hover:underline underline-offset-4">
            <Trans>Add a review</Trans>
          </button>
        )}
      </div>
      {/* The review opens under the stars, and its text and its editor swap in place. */}
      <Unfold open={editingReview || !!review}>
      <Swap id={editingReview ? 'edit' : 'text'}>
      {editingReview ? (
        <Textarea
          value={reviewDraft}
          onChange={e => setReviewDraft(e.target.value)}
          onBlur={saveReview}
          autoFocus
          rows={3}
          placeholder={t`What did you think? (optional, saved automatically)`}
          className="mt-3 text-[15px]"
        />
      ) : review ? (
        <div className="group/review relative mt-3 border-l-2 border-primary/30 pl-3">
          <p className="whitespace-pre-wrap pr-7 text-[15px] leading-relaxed text-foreground/90">{review}</p>
          <button type="button" onClick={startEditingReview} aria-label={t`Edit review`} title={t`Edit review`}
            className="absolute right-0 top-0 p-1 text-muted-foreground/50 transition-colors hover:text-foreground">
            <Edit2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : null}
      </Swap>
      </Unfold>
    </div>
  )

  const hero = (
    <div className={HERO_CLS}>
      <TitleGlide innerRef={heroTitleRef} style={next ? { minHeight: heroTitleH } : undefined}>
        <Swap id={editing && !next ? 'edit' : 'view'}>{heroTitle}</Swap>
      </TitleGlide>
      <div className={dim} inert={!!next}>
        {statusRow}
        {progressRow}
        {actionRow}
        <Swap id={syncMode}>{syncRow}</Swap>
        {ratingRow}
      </div>
    </div>
  )

  // ── Sections ────────────────────────────────────────────────────────────────

  const descriptionSection = (
    <section className="max-w-[720px]">
      <SectionTitle size={20}><Trans>Description</Trans></SectionTitle>
      <Swap id={editing ? 'edit' : 'view'}>
      {editing ? (
        <Textarea value={field('description') ?? ''} onChange={e => setField('description', e.target.value)} rows={8}
          placeholder={t`No description`} className="mt-3 text-[15px]" aria-label={t`Description`} />
      ) : book.description ? (
        <>
          <p ref={descRef} className={cn('mt-3 text-[15px] leading-relaxed text-foreground/85', !descExpanded && !descMoving && 'line-clamp-4')}>
            {book.description}
          </p>
          {(descClamped || descExpanded) && (
            <button type="button" onClick={toggleDescription}
              className="mt-2 text-[14px] font-medium text-primary hover:underline underline-offset-4">
              {descExpanded ? t`Show less` : t`Show more`}
            </button>
          )}
        </>
      ) : (
        <p className="mt-3 text-[15px] text-muted-foreground"><Trans>No description</Trans></p>
      )}
      </Swap>
    </section>
  )

  const readingSection = readingStats ? (
    <section>
      <SectionTitle size={20} action={
        <Pill variant="quiet" className="-my-2 -mr-3" onClick={() => setShowPositionHistory(true)} title={t`Restore an earlier reading position after a bad sync`}>
          <HistoryIcon className="h-4 w-4" /><Trans>Position history</Trans>
        </Pill>
      }>
        <Trans>Reading</Trans>
      </SectionTitle>
      {hasSessions && own ? (
        <ReadingBody own={own} aggregate={readingStats.aggregate} bookId={Number(id)} onChange={refreshStats} />
      ) : (
        // No history yet: the manual-tracking entry point (paper / un-synced device).
        <div className="mt-3 flex flex-col items-start gap-3">
          <p className="max-w-[60ch] text-[15px] text-muted-foreground">
            <Trans>No reading logged yet. Track time by hand, handy for a paper copy or a device that isn&apos;t synced.</Trans>
          </p>
          <ManualLogControls bookId={Number(id)} onChange={refreshStats} />
        </div>
      )}
    </section>
  ) : null

  const sessionsSection = hasSessions && own ? (
    <section>
      <div className="flex items-center gap-2">
        <button type="button" onClick={() => setSessionsOpen(o => !o)} aria-expanded={sessionsOpen}
          className="flex items-center gap-2 text-left">
          <h2 className="font-display text-[20px] font-bold leading-none text-foreground">
            {(() => { const n = own.sessions; return <Trans>Sessions <span className="font-semibold text-muted-foreground">({n})</span></Trans> })()}
          </h2>
          <ChevronDown className={cn('h-5 w-5 text-muted-foreground transition-transform duration-200 ease-(--spring)', sessionsOpen && 'rotate-180')} />
        </button>
        <SessionLogHint />
      </div>
      <Unfold open={sessionsOpen}>
        <div className="pt-4">
          <SessionLog bookId={Number(id)} onChange={refreshStats} expected={own.sessions} />
        </div>
      </Unfold>
    </section>
  ) : null

  const highlightsSection = annotations.length > 0 ? (
    <section>
      <div className="flex items-baseline justify-between gap-4">
        <button type="button" onClick={() => setHighlightsOpen(o => !o)} aria-expanded={highlightsOpen}
          className="flex items-center gap-2 text-left">
          <h2 className="font-display text-[20px] font-bold leading-none text-foreground">
            {(() => { const n = annotations.length; return <Trans>Highlights and notes <span className="font-semibold text-muted-foreground">({n})</span></Trans> })()}
          </h2>
          <ChevronDown className={cn('h-5 w-5 text-muted-foreground transition-transform duration-200 ease-(--spring)', highlightsOpen && 'rotate-180')} />
        </button>
        <Link to="/highlights" className="shrink-0 text-[15px] font-medium text-primary hover:underline underline-offset-4">
          <Trans>All highlights</Trans>
        </Link>
      </div>
      <Unfold open={highlightsOpen}>
        <ul className="pt-2">
          <AnimatePresence initial={false}>
          {annotations.map((a, i) => (
            <m.li key={a.id} {...listItem} className={cn('group py-4', i > 0 && 'border-t border-border/70')}>
              {a.highlighted_text && (
                <p className="max-w-[720px] text-[15px] leading-relaxed text-foreground/90">{a.highlighted_text}</p>
              )}
              <Unfold open={editingNoteId === a.id}>
                <div className="flex max-w-[720px] flex-col gap-2 pt-2.5">
                  <Textarea value={editingNoteDraft} onChange={e => setEditingNoteDraft(e.target.value)} autoFocus rows={2}
                    placeholder={t`Your note, syncs back to KOReader on the next sync`}
                    className="min-h-[64px]" />
                  <span className="flex items-center gap-1.5">
                    <Pill variant="primary" onClick={() => saveHighlightNote(a.id)}><Trans>Save note</Trans></Pill>
                    <Pill variant="quiet" onClick={() => setEditingNoteId(null)}><Trans>Cancel</Trans></Pill>
                  </span>
                </div>
              </Unfold>
              {editingNoteId !== a.id && a.note ? (
                <p className="mt-1.5 flex max-w-[720px] items-start gap-1.5 text-[14px] text-muted-foreground">
                  <StickyNote className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span className="leading-relaxed">{a.note}</span>
                </p>
              ) : null}
              <div className="mt-1.5 flex items-center justify-between gap-3">
                <span className="min-w-0 truncate text-[12px] text-muted-foreground/80">
                  {[a.chapter, a.datetime ? shortDay(new Date(a.datetime)) : null].filter(Boolean).join(' · ')}
                </span>
                {a.read_only ? (
                  <span className="shrink-0 text-[12px] text-muted-foreground/80" title={t`Clippings are changed on the reader that made them`}>
                    <Trans>CrossInk clipping</Trans>
                  </span>
                ) : confirmingHighlight === a.id ? (
                  <m.span key="confirm" initial={{ opacity: 0.6, x: 4 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.16, ease: EASE }}
                    className="flex shrink-0 items-center gap-3 text-[13px]">
                    <button onClick={() => deleteHighlight(a.id)} className="font-medium text-destructive hover:underline">
                      <Trans>Delete</Trans>
                    </button>
                    <button onClick={() => setConfirmingHighlight(null)} className="text-muted-foreground hover:text-foreground">
                      <Trans>Cancel</Trans>
                    </button>
                  </m.span>
                ) : (
                  <span className="flex shrink-0 items-center gap-0.5">
                    <button
                      onClick={() => { setEditingNoteDraft(a.note ?? ''); setEditingNoteId(prev => (prev === a.id ? null : a.id)) }}
                      title={a.note ? t`Edit note` : t`Add a note`}
                      aria-label={a.note ? t`Edit note` : t`Add a note`}
                      className="rounded p-1 text-muted-foreground/60 opacity-60 transition-all hover:text-foreground focus:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
                    >
                      <Edit2 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => setConfirmingHighlight(a.id)}
                      title={t`Delete this highlight`}
                      aria-label={t`Delete this highlight`}
                      className="rounded p-1 text-muted-foreground/60 opacity-60 transition-all hover:text-destructive focus:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </span>
                )}
              </div>
            </m.li>
          ))}
          </AnimatePresence>
        </ul>
      </Unfold>
    </section>
  ) : null

  const seriesSection = seriesVolumes.length > 1 && book.series ? (
    <section>
      <SectionTitle size={20} action={
        <SeriesLink name={book.series} {...warmSeries} onClick={toSeries('strip')} className="text-primary hover:underline underline-offset-4"><Trans>Open series</Trans></SeriesLink>
      }>
        <Trans>In this series</Trans>
      </SectionTitle>
      <div className="-mx-1 mt-4 flex gap-4 overflow-x-auto px-1 pb-2 pt-1">
        {seriesVolumes.map(v => {
          const here = v.id === book.id
          const idx = v.series_index
          return (
            <div key={v.id} className="w-[104px] shrink-0">
              {/* A cover opens the quick look; the caption opens the book, its cover growing into the hero. */}
              <CoverLift disabled={here}>
              <button type="button" aria-label={v.title} title={v.title}
                onClick={e => {
                  if (here) return
                  setQuickLookFrom({ origin: e.currentTarget.getBoundingClientRect(), cover: v.cover_path ? coverSrc(v.id, { w: 400, v: v.updated_at }) : null })
                  setQuickLookId(v.id)
                }}
                data-cover-id={here ? undefined : v.id}
                data-series-cover={v.id === seriesHeroId ? book.series : undefined}
                className={cn(
                  'relative block aspect-[2/3] w-full overflow-hidden rounded-[8px] bg-muted transition-opacity duration-150',
                  here ? 'cursor-default ring-2 ring-primary ring-offset-2 ring-offset-background'
                    : v.reading_status === 'read' ? 'opacity-55 hover:opacity-100' : '',
                )}
                style={{ boxShadow: 'var(--cover-bloom-fan)' }}>
                <CoverImage src={v.cover_path ? coverSrc(v.id, { w: 160, v: v.updated_at }) : null} alt="" color={v.cover_color} iconClassName="w-5 h-5" />
              </button>
              </CoverLift>
              {here ? (
                <div className="mt-2 truncate text-[13px] font-medium text-foreground">{idx != null ? t`Vol. ${idx}` : v.title}</div>
              ) : (
                <BookLink book={{ id: v.id, title: v.title, author: book.author, series: book.series, series_index: v.series_index, coverColor: v.cover_color }}
                  title={v.title}
                  className="mt-2 block truncate text-[13px] text-muted-foreground hover:text-foreground transition-colors">
                  {idx != null ? t`Vol. ${idx}` : v.title}
                </BookLink>
              )}
            </div>
          )
        })}
      </div>
    </section>
  ) : null

  return (
    <>
      {/* Phones: the app-wide keyboard-shortcut button (App.tsx) sits over the
          book's action pills, and a phone has no keyboard to use it with. */}
      {/* eslint-disable-next-line lingui/no-unlocalized-strings -- CSS */}
      <PageBody width="wide">
        {/* The page header for a book: where you are on the left, the page's
            actions on the right. The hero's h1 is the title. */}
        {/* Opened from a card, the row keeps the height the preview reserved
            while its later parts (previous/next, Finish by) arrive. */}
        <div ref={headerRef} className={cn(HEADER_CLS, 'content-start')} style={preview && heldHeader ? { minHeight: heldHeader } : undefined}>
          {crumbs}
          <div className={cn('ml-auto min-w-0', dim)} inert={!!next}><Swap id={actionsMode}>{pageActions}</Swap></div>
        </div>

        <div className={GRID_CLS}>
          {railCover}
          <div className={MAIN_COL_CLS}>
            {hero}
            {/* The sections arrive together once the late answers are in, rising a
                little; arriving with the page from another book, they just show. */}
            {below && (
              <div className={dim} inert={!!next}>
              <m.div className="flex flex-col gap-7" initial={rise ? RISE_FROM : false} animate={RISE_TO}>
                {descriptionSection}
                {statsIn && readingSection}
                {statsIn && readingStats?.intensity && (
                  <IntensityBlock data={readingStats.intensity} bookId={Number(id)} onChange={refreshStats} />
                )}
                {statsIn && readingStats?.chapters && readingStats.chapters.length > 0 && (
                  <ChapterTimesBlock chapters={readingStats.chapters} />
                )}
                {statsIn && sessionsSection}
                {notesIn && highlightsSection}
                {notesIn && seriesSection}
              </m.div>
              </div>
            )}
          </div>
          {belowRail && railDetails}
        </div>
      </PageBody>

      <AnimatePresence>
        {quickLookId != null && (() => {
          const v = seriesVolumes.find(x => x.id === quickLookId)
          return <QuickLook key={quickLookId} bookId={quickLookId} origin={quickLookFrom.origin} cover={quickLookFrom.cover}
            preview={v ? { title: v.title, author: book.author, series: book.series, series_index: v.series_index } : undefined}
            onClose={() => setQuickLookId(null)} />
        })()}
      </AnimatePresence>
      <MetadataFetchModal
        book={book}
        open={fetchModalOpen}
        onClose={() => setFetchModalOpen(false)}
        onApplied={updated => { setBook(updated); setDraft(updated) }}
      />
      <CoverPickerModal
        book={book}
        open={coverPickerOpen}
        onClose={() => setCoverPickerOpen(false)}
        onApplied={updated => { setBook(updated); setDraft(updated) }}
      />
      <AnimatePresence>
      {showShare && (
        <ShareModal
          key="share"
          title={book.title}
          endpoint={`/books/${book.id}/share`}
          noun="book"
          onClose={() => setShowShare(false)}
        />
      )}
      {showPositionHistory && (
        <PositionHistoryModal
          key="positions"
          bookId={Number(id)}
          onClose={() => setShowPositionHistory(false)}
          onRestored={() => {
            refreshStats()
            api.get<BookStatus>(`/books/${id}/status`)
              .then(s => { setBookStatus(s.status); setProgressPct(s.progress_pct); setCfi(s.cfi ?? null) })
              .catch(() => {})
          }}
        />
      )}
      </AnimatePresence>
    </>
  )
}

// ── Reading stats sub-components ─────────────────────────────────────────────

interface StatsLayoutProps {
  own: BookReadingStats
  aggregate: { total_seconds: number; total_sessions: number; distinct_readers: number } | null
  bookId: number
  onChange: () => void
}

/** Activity bars (minutes per reading day) + an optional progress lane below.
 *  The progress lane is the per-book "journey" - % complete over calendar time  - 
 *  drawn whenever the timeline carries 2+ progress points (the backend only
 *  emits progress_pct where a position is actually known). */
export function ActivityChart({ timeline, bookId, onChange }: {
  timeline: { date: string; seconds: number; pages: number; progress_pct?: number | null }[]
  bookId?: number
  onChange?: () => void
}) {
  const { t, i18n } = useLingui()
  // Click-through: a bar opens that day's sessions for quick trim/delete  - 
  // e.g. an accidentally imported sitting, without scrolling to the full log.
  const [dayOpen, setDayOpen] = useState<string | null>(null)
  // The day stays on screen while the dialog animates out.
  const [shownDay, setShownDay] = useState<string | null>(null)
  if (dayOpen && dayOpen !== shownDay) setShownDay(dayOpen)
  // eslint-disable-next-line lingui/no-unlocalized-strings -- ISO time suffix
  const dayLabel = shownDay ? new Date(shownDay + 'T00:00:00').toLocaleDateString(i18n.locale, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }) : ''
  if (timeline.length < 2) return null
  // Real time axis: fill the calendar days between the first and last reading
  // day with zero bars. Active-days-only bars, evenly spaced, misrepresent the
  // span (two adjacent bars could be one day or a month apart) - and with 2-3
  // reading days they rendered as giant full-width slabs. Capped at the last
  // 365 days so a years-long log can't produce more bars than pixels.
  const byDate = new Map(timeline.map(d => [d.date, d]))
  // eslint-disable-next-line lingui/no-unlocalized-strings -- ISO time suffix
  const startD = new Date(timeline[0].date + 'T00:00:00')
  // eslint-disable-next-line lingui/no-unlocalized-strings -- ISO time suffix
  const endD = new Date(timeline[timeline.length - 1].date + 'T00:00:00')
  let days: typeof timeline = []
  for (const d = new Date(startD); d <= endD; d.setDate(d.getDate() + 1)) {
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    days.push(byDate.get(key) ?? { date: key, seconds: 0, pages: 0 })
  }
  if (days.length > 365) days = days.slice(-365)
  const max = Math.max(...days.map(d => d.seconds), 1)
  const n = days.length
  const first = days[0]
  const last = days[days.length - 1]
  const fmtDay = (iso: string) =>
    new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  // Progress journey: % complete on each reading day, drawn as a slim lane below.
  const hasJourney = days.some(d => d.progress_pct != null)
  const lanePts = (days
    .map((d, i) => (d.progress_pct != null
      ? `${((i / (n - 1)) * 100).toFixed(2)},${(100 - d.progress_pct).toFixed(2)}`
      : null))
    .filter(Boolean) as string[])
  const lastProgress = [...days].reverse().find(d => d.progress_pct != null)?.progress_pct ?? null
  return (
    // Always full card width - a narrower "day-sized bars" cap was tried and
    // looked half-finished against the full-width card chrome. Big bars for a
    // short history are fine; the gap-filled axis keeps the spacing honest.
    <div className="flex flex-col">
      <p className="text-[13px] text-muted-foreground/70 mb-1.5 shrink-0"><Trans>Activity</Trans></p>
      {/* Fixed-height strip: flex-1 fill only works inside a sized flex parent,
          and the hero wraps this in a plain div - flex-1 collapsed to 0px there
          (bars rendered into no vertical space at all) */}
      <div className="relative h-20">
        {/* faint baseline rule */}
        <div className="absolute bottom-0 left-0 right-0 h-px bg-border/40" />
        <div className={cn('absolute inset-0 flex items-end', n > 60 ? 'gap-px' : 'gap-1.5')}>
          {days.map(d => {
            const pct = d.seconds > 0 ? Math.max(d.seconds / max, 0.06) : 0
            const mins = Math.round(d.seconds / 60)
            const clickable = d.seconds > 0 && bookId != null
            const dayStr = fmtDay(d.date)
            const pages = d.pages, prog = d.progress_pct
            const tip = t`${dayStr}: ${mins}m` +
              (pages > 0 ? t`, ${pages} pages` : '') +
              (prog != null ? t` · ${prog}% in` : '') +
              (clickable ? t`, click for sessions` : '')
            return (
              <div
                key={d.date}
                className={cn(
                  // Height runs to a new value after a log, trim or delete (not on arrival: the first paint has no "before").
                  'flex-1 min-w-px bg-primary/60 hover:bg-primary transition-[background-color,height] duration-300 ease-(--spring) motion-reduce:transition-none rounded-t-sm',
                  clickable && 'cursor-pointer',
                )}
                style={{ height: pct > 0 ? `${Math.round(pct * 100)}%` : '0%' }}
                title={d.seconds > 0 ? tip : undefined}
                onClick={clickable ? () => setDayOpen(d.date) : undefined}
              />
            )
          })}
        </div>
      </div>
      <div className="flex justify-between text-[13px] text-muted-foreground/50 mt-1 shrink-0">
        <span>{first?.date ? fmtDay(first.date) : ''}</span>
        <span>{last?.date ? fmtDay(last.date) : ''}</span>
      </div>
      {bookId != null && (
        <ModalShell
          open={dayOpen != null}
          onClose={() => setDayOpen(null)}
          eyebrow={<Trans>Sessions</Trans>}
          title={dayLabel}
          headerActions={<SessionLogHint />}
          size="lg"
          footer={<Pill variant="quiet" onClick={() => setDayOpen(null)}><Trans>Close</Trans></Pill>}
        >
          {shownDay && <SessionLog bookId={bookId} day={shownDay} onChange={onChange} />}
        </ModalShell>
      )}
      {hasJourney && lanePts.length > 1 && (
        <div className="mt-3">
          <div className="flex items-center justify-between mb-1">
            <span className="flex items-center gap-1">
              <p className="text-[13px] text-muted-foreground/70"><Trans>Progress</Trans></p>
              <InfoHint text={t`How far through the book you'd read by each date.`} />
            </span>
            {lastProgress != null && (
              <p className="min-w-[3em] text-right text-[13px] proportional-nums text-muted-foreground/60"><RunningNumber value={Math.round(lastProgress)} className="proportional-nums" />%</p>
            )}
          </div>
          <div className="relative h-7">
            <div className="absolute bottom-0 left-0 right-0 h-px bg-border/40" />
            <svg className="absolute inset-0 w-full h-full text-primary" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
              <polygon points={`0,100 ${lanePts.join(' ')} 100,100`} fill="currentColor" opacity={0.14} />
              <polyline points={lanePts.join(' ')} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            </svg>
          </div>
        </div>
      )}
    </div>
  )
}

// Friendly label for a ReadingSession.device value.
function sourceLabel(device: string): string {
  if (!device || device === 'web-reader' || device === 'web') return t`Web reader`
  if (device === 'manual') return t`Manual`
  // eslint-disable-next-line lingui/no-unlocalized-strings -- product name
  if (device === 'koreader') return 'KOReader'
  return device
}

const SOURCE_SHADES = ['bg-primary', 'bg-primary/65', 'bg-primary/40', 'bg-primary/25']

// Where the reading time came from: a thin stacked bar + legend.
export function SourceSplit({ sources }: { sources: { device: string; seconds: number; sessions: number }[] }) {
  const total = sources.reduce((sum, s) => sum + s.seconds, 0)
  // A single source is a full-width solid bar - pointless; only split when it splits.
  if (total <= 0 || sources.length < 2) return null
  return (
    <div className="mt-4">
      <p className="text-[13px] text-muted-foreground/70 mb-1.5"><Trans>Where you read</Trans></p>
      {/* gap-px: adjacent primary shades are close - a hairline seam marks the split */}
      <div className="flex gap-px h-2 rounded-full overflow-hidden bg-muted">
        {sources.map((s, i) => (
          <div
            key={s.device}
            className={cn(SOURCE_SHADES[Math.min(i, SOURCE_SHADES.length - 1)], 'transition-all')}
            style={{ width: `${(s.seconds / total) * 100}%` }}
            title={`${sourceLabel(s.device)}: ${formatDuration(s.seconds)}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
        {sources.map((s, i) => (
          <div key={s.device} className="flex items-center gap-1.5">
            <span className={cn('inline-block w-2 h-2 rounded-full', SOURCE_SHADES[Math.min(i, SOURCE_SHADES.length - 1)])} />
            <span className="text-[13px] text-muted-foreground">{sourceLabel(s.device)}</span>
            <span className="text-[13px] font-medium tabular-nums text-foreground">{formatDuration(s.seconds)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Reading intensity: per-page dwell across the book, from imported KOReader page-stats ─────

export function IntensityBlock({ data, bookId, onChange }: { data: BookIntensity; bookId: number; onChange?: () => void }) {
  const { t } = useLingui()
  const max = Math.max(...data.curve, 1)
  const finished = data.pct_read >= 99
  const [confirmClear, setConfirmClear] = useState(false)
  const [clearing, setClearing] = useState(false)
  const clearImported = () => {
    setClearing(true)
    api.delete(`/books/${bookId}/imported-history`)
      .then(() => { setConfirmClear(false); onChange?.() })
      .catch(() => {})
      .finally(() => setClearing(false))
  }
  return (
    <section>
      <ChartSectionTitle
        title={<Trans>Reading intensity</Trans>}
        hint={t`Where in the book your time went. Taller means more time spent on those pages.`}
        foot={<>
          <span className="font-medium tabular-nums text-foreground">{data.pages_read.toLocaleString()}</span>
          {' '}{(() => { const totalPages = data.total_pages.toLocaleString(); return <Trans>of {totalPages} pages</Trans> })()}
          {!finished && <span className="tabular-nums"> · {data.pct_read}%</span>}
          {' · '}{(() => { const dur = formatDuration(data.total_seconds); return <Trans>{dur} on device</Trans> })()}
        </>}
        action={
          <Pill variant="destructive" icon className="-my-2 -mr-2" onClick={() => setConfirmClear(o => !o)}
            aria-label={t`Clear imported history`}
            title={t`Clear imported history: remove this book's KOReader page data`}>
            <Trash2 className="h-4 w-4" />
          </Pill>
        }
      />
      <Unfold open={confirmClear}>
        <div className="flex flex-wrap items-center gap-2 pt-3 text-[14px]">
          <span className="text-muted-foreground">
            <Trans>Remove all of this book&apos;s imported KOReader history? Web and manual sessions stay. Reading it on the device again will re-import from there on.</Trans>
          </span>
          <Pill onClick={clearImported} disabled={clearing} className="bg-destructive text-white hover:bg-destructive/90">
            {clearing ? t`Clearing…` : t`Clear`}
          </Pill>
          <Pill variant="quiet" onClick={() => setConfirmClear(false)}>
            <Trans>Cancel</Trans>
          </Pill>
        </div>
      </Unfold>
      {/* Dwell across the book, 0% → 100%. Taller bar = more time spent there. */}
      <div className="mt-4 relative h-16">
        <div className="absolute bottom-0 left-0 right-0 h-px bg-border/40" />
        <div className="absolute inset-0 flex items-end gap-px">
          {data.curve.map((secs, i) => {
            const pct = secs > 0 ? Math.max(secs / max, 0.04) : 0
            const at = Math.round((i / data.curve.length) * 100)
            const dur = formatDuration(secs)
            const tip = secs > 0 ? t`~${at}% in · ${dur}` : t`~${at}% in · not read`
            return (
              <div
                key={i}
                className="flex-1 min-w-px bg-primary/60 hover:bg-primary transition-colors rounded-t-[1px]"
                style={{ height: pct > 0 ? `${Math.round(pct * 100)}%` : '0%' }}
                title={tip}
              />
            )
          })}
        </div>
      </div>
      <div className="flex justify-between text-[13px] text-muted-foreground/50 mt-1">
        <span>{t`start`}</span>
        {data.reread_bins > 0 && (
          <span className="text-muted-foreground/70">{plural(data.reread_bins, { one: '# stretch re-read', other: '# stretches re-read' })}</span>
        )}
        <span>{t`end`}</span>
      </div>
    </section>
  )
}

// ── Time per chapter: page-stat dwell bucketed into the book's TOC boundaries ─────

export function ChapterTimesBlock({ chapters }: { chapters: ChapterTime[] }) {
  const { t } = useLingui()
  const [showDetails, setShowDetails] = useState(false)
  const max = Math.max(...chapters.map(c => c.seconds), 1)
  const total = chapters.reduce((s, c) => s + c.seconds, 0)
  return (
    <section>
      <ChartSectionTitle
        title={<Trans>Time per chapter</Trans>}
        hint={t`Device reading time mapped into the book's chapters. Hover the line for a chapter's time; a flat stretch hasn't been read on a synced device.`}
        foot={(() => { const dur = formatDuration(total); const n = chapters.length; return <span className="tabular-nums">{t`${dur} across ${n} chapters`}</span> })()}
      />
      <ChapterLineChart chapters={chapters} />
      <Unfold open={showDetails}>
        <ul className="space-y-1.5 pt-3">
          {chapters.map(c => (
            <li
              key={c.idx}
              className="flex items-center gap-3 -mx-2 px-2 py-0.5 rounded-md hover:bg-muted/50 transition-colors"
              title={[c.title, chapterReadSpan(c)].filter(Boolean).join(' · ')}
            >
              <span className="w-40 sm:w-56 shrink-0 truncate text-[13px] text-muted-foreground">
                {c.title}
              </span>
              <span className="flex-1 h-2 rounded bg-muted/60 overflow-hidden">
                <span
                  className="block h-full rounded bg-primary/60"
                  style={{ width: c.seconds > 0 ? `${Math.max(Math.round((c.seconds / max) * 100), 3)}%` : '0%' }}
                />
              </span>
              <span className="w-14 shrink-0 text-right text-[13px] tabular-nums text-muted-foreground">
                {c.seconds <= 0 ? '-' : c.seconds < 60 ? '<1m' : formatDuration(c.seconds)}
              </span>
            </li>
          ))}
        </ul>
      </Unfold>
      <button
        onClick={() => setShowDetails(v => !v)}
        className="mt-3 text-[14px] font-medium text-primary hover:underline underline-offset-4"
      >
        {showDetails ? t`Hide details` : t`Show details`}
      </button>
    </section>
  )
}

// Line/area rendering of time-per-chapter: one series, so no legend - the
// block title names it. Hover snaps to the nearest chapter and shows a styled
// tooltip (name + time) with a marker dot; both are HTML overlays positioned
// in percent, because the SVG stretches (preserveAspectRatio="none") and
// would distort anything drawn inside it.
function ChapterLineChart({ chapters }: { chapters: ChapterTime[] }) {
  const { t } = useLingui()
  const W = 600, H = 80, PAD = 2
  const n = chapters.length
  const max = Math.max(...chapters.map(c => c.seconds), 1)
  const [hover, setHover] = useState<number | null>(null)
  const x = (i: number) => n === 1 ? W / 2 : PAD + (i / (n - 1)) * (W - 2 * PAD)
  const y = (s: number) => H - PAD - (s / max) * (H - 2 * PAD)
  const pts = chapters.map((c, i) => `${x(i).toFixed(1)},${y(c.seconds).toFixed(1)}`).join(' ')
  const area = `${PAD},${H - PAD} ${pts} ${(W - PAD).toFixed(1)},${H - PAD}`

  const onMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const frac = (e.clientX - rect.left) / rect.width
    setHover(Math.min(Math.max(Math.round(frac * (n - 1)), 0), n - 1))
  }
  const h = hover !== null ? chapters[hover] : null
  const hx = hover !== null ? (x(hover) / W) * 100 : 0
  const hy = h ? (y(h.seconds) / H) * 100 : 0

  return (
    <div className="mt-3">
      <div className="relative" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-20 block" preserveAspectRatio="none" role="img" aria-label={t`Time per chapter, line view`}>
          <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} className="stroke-border/60" strokeWidth="1" />
          <polygon points={area} className="fill-primary/15" />
          <polyline points={pts} className="stroke-primary/70 fill-none" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        </svg>
        {h && (
          <>
            <span
              className="pointer-events-none absolute w-2 h-2 rounded-full bg-primary ring-2 ring-card -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${hx}%`, top: `${hy}%` }}
            />
            <div
              className="pointer-events-none absolute z-10 whitespace-nowrap rounded-lg bg-card px-2.5 py-1.5 text-[13px] shadow-lg ring-1 ring-panel-ring"
              style={{
                left: `${Math.min(Math.max(hx, 10), 90)}%`,
                top: `${Math.max(hy - 8, 0)}%`,
                transform: 'translate(-50%, -100%)',
              }}
            >
              <span className="text-foreground">{h.title}</span>
              <span className="text-muted-foreground tabular-nums">
                {' · '}{h.seconds <= 0 ? t`not read` : h.seconds < 60 ? '<1m' : formatDuration(h.seconds)}
                {chapterReadSpanShort(h) && ` · ${chapterReadSpanShort(h)}`}
              </span>
            </div>
          </>
        )}
      </div>
      <div className="flex justify-between text-[13px] text-muted-foreground/50 mt-1">
        <span><Trans>ch. 1</Trans></span>
        <span><Trans>ch. {n}</Trans></span>
      </div>
    </div>
  )
}

// ── Hero layout: large time-read headline + activity chart, bottom-pinned date/span stats ─────

// Manual reading-log entry point: log a session by hand (paper / un-synced
// device) and export the log. Used both in the stats hero and on books with no
// reading history yet, so manual trackers always have a way in.
export function ManualLogControls({ bookId, onChange, exportRows }: {
  bookId: number
  onChange: () => void
  exportRows?: { date: string; seconds: number; pages: number; progress_pct: number | null }[]
}) {
  const { t } = useLingui()
  const { toast } = useToast()
  const [logging, setLogging] = useState(false)
  const [minutes, setMinutes] = useState('')
  const [pct, setPct] = useState('')
  const [saving, setSaving] = useState(false)

  const submitLog = async (e: React.FormEvent) => {
    e.preventDefault()
    const mins = parseFloat(minutes)
    if (!mins || mins <= 0) return
    setSaving(true)
    try {
      const body: { duration_minutes: number; end_progress?: number } = { duration_minutes: mins }
      if (pct.trim() !== '') {
        const p = parseFloat(pct)
        if (!Number.isNaN(p)) body.end_progress = Math.min(Math.max(p / 100, 0), 1)
      }
      await api.post(`/books/${bookId}/sessions?tz_offset=${new Date().getTimezoneOffset()}`, body)
      setMinutes(''); setPct(''); setLogging(false)
      onChange()
    } catch (e) {
      toast.error((e as Error).message ?? t`Failed to log session`)
    } finally {
      setSaving(false)
    }
  }

  const exportSessions = (fmt: 'csv' | 'json') => {
    const rows = exportRows ?? []
    let content: string
    let mime: string
    if (fmt === 'csv') {
      content = 'date,minutes,pages,progress_pct\n' +
        rows.map(r => `${r.date},${Math.round(r.seconds / 60)},${r.pages},${r.progress_pct ?? ''}`).join('\n')
      mime = 'text/csv'
    } else {
      content = JSON.stringify(rows, null, 2)
      mime = 'application/json'
    }
    const blob = new Blob([content], { type: mime })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `reading-log-${bookId}.${fmt}`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const canExport = exportRows != null && exportRows.length > 0

  return (
    <div>
      <div className="-ml-3 flex flex-wrap items-center gap-1">
        <Pill variant="quiet" onClick={() => setLogging(o => !o)} aria-expanded={logging}
          className={cn(logging && 'bg-secondary text-foreground')}>
          <Plus className="h-4 w-4" /> <Trans>Log session</Trans>
        </Pill>
        {canExport && (
          <>
            {/* eslint-disable-next-line lingui/no-unlocalized-strings -- file format names */}
            <Pill variant="quiet" onClick={() => exportSessions('csv')} title={t`Export the reading log as CSV`}><Download className="h-4 w-4" />CSV</Pill>
            {/* eslint-disable-next-line lingui/no-unlocalized-strings -- file format names */}
            <Pill variant="quiet" onClick={() => exportSessions('json')} title={t`Export the reading log as JSON`}><Download className="h-4 w-4" />JSON</Pill>
          </>
        )}
      </div>
      <Unfold open={logging}>
        <form onSubmit={submitLog} className="flex flex-wrap items-end gap-3 pt-3">
          <div>
            <FormLabel htmlFor={`log-minutes-${bookId}`}><Trans>Minutes</Trans></FormLabel>
            <Input
              id={`log-minutes-${bookId}`}
              type="number" min="1" step="1" required autoFocus
              value={minutes} onChange={e => setMinutes(e.target.value)}
              className="w-24 tabular-nums"
            />
          </div>
          <div>
            <FormLabel htmlFor={`log-pct-${bookId}`} hint={t`optional`}><Trans>Progress %</Trans></FormLabel>
            <Input
              id={`log-pct-${bookId}`}
              type="number" min="0" max="100" step="1"
              value={pct} onChange={e => setPct(e.target.value)}
              className="w-28 tabular-nums"
            />
          </div>
          <div className="flex items-center gap-1.5">
            <Pill variant="quiet" onClick={() => { setLogging(false); setMinutes(''); setPct('') }}>
              <Trans>Cancel</Trans>
            </Pill>
            <Pill type="submit" variant="primary" disabled={saving || !minutes}>
              {saving ? t`Saving…` : t`Add session`}
            </Pill>
          </div>
        </form>
      </Unfold>
    </div>
  )
}

function MomentumChip({ momentum }: { momentum: NonNullable<BookReadingStats['momentum']> }) {
  const { t } = useLingui()
  const Icon = momentum.direction === 'up' ? TrendingUp : momentum.direction === 'down' ? TrendingDown : Minus
  const tone =
    // eslint-disable-next-line lingui/no-unlocalized-strings -- Tailwind classes
    momentum.direction === 'up' ? 'text-emerald-600 dark:text-emerald-400'
      : momentum.direction === 'down' ? 'text-muted-foreground'
        : 'text-muted-foreground'
  const deltaStr = momentum.delta_pct != null ? `${momentum.delta_pct > 0 ? '+' : ''}${momentum.delta_pct}` : ''
  const label = momentum.delta_pct != null
    ? t`${deltaStr}% vs last week`
    : t`new this week`
  return (
    <span
      className={cn('flex items-center gap-1 text-[13px] font-medium', tone)}
      title={(() => { const recent = formatDuration(momentum.recent_seconds); const prior = formatDuration(momentum.prior_seconds); return t`${recent} in the last 7 days vs ${prior} the week before` })()}
    >
      <Icon className="w-3.5 h-3.5" />
      {label}
    </span>
  )
}

/** The Reading section's body: the numbers as a bare strip, the existing
 *  charts as they are, the dates as fields, then the manual log. */
function ReadingBody({ own, aggregate, bookId, onChange }: StatsLayoutProps) {
  const { t } = useLingui()
  const h = Math.floor(own.total_seconds / 3600)
  const m = Math.round((own.total_seconds % 3600) / 60)
  const pace = own.pace_pages_per_min
  const hours = (n: number) => { const h = Math.round(n); return t`${h}h` }

  const dates: { label: string; value: string }[] = []
  if (own.first_read) dates.push({ label: t`First read`, value: formatDate(own.first_read.slice(0, 10)) })
  if (own.last_read) dates.push({ label: t`Last read`, value: formatDate(own.last_read.slice(0, 10)) })
  if (own.finished_at) dates.push({ label: t({ message: 'Finished', context: 'reading' }), value: formatDate(own.finished_at.slice(0, 10)) })
  if (own.session_timeline.length > 0) dates.push({ label: t`Reading days`, value: String(own.session_timeline.length) })
  if (own.status === 'reading' && own.estimated_finish_seconds != null) {
    dates.push({ label: t`Est. remaining`, value: formatDuration(own.estimated_finish_seconds) })
  }
  // Date at your recent per-day rate on this book; absent when it's paused.
  if (own.status === 'reading' && own.estimated_finish_date) {
    dates.push({ label: t`Est. finish`, value: formatDate(own.estimated_finish_date) })
  }

  return (
    <div className="mt-5 flex flex-col gap-6">
      <StatStrip cols={5} bordered={false} className="gap-y-6">
        <Stat size={30} label={t`Time reading`}
          value={h > 0 ? h : m} measure={h > 0 ? 'h' : 'min'}
          format={h > 0 ? hours : undefined}
          unit={h > 0 ? t`${m}m` : t`min`}
          foot={own.momentum ? <MomentumChip momentum={own.momentum} /> : undefined} />
        <Stat size={30} label={t`Sessions`} value={own.sessions} />
        <Stat size={30} label={t`Pages`} value={own.pages_turned > 0 ? own.pages_turned : '-'} />
        <Stat size={30} label={t`Avg session`} value={Math.round(own.avg_session_seconds / 60)} unit={t`min`} />
        <Stat size={30} label={t`Pace`} value={pace ?? '-'} unit={pace != null ? t`pg/min` : undefined} />
      </StatStrip>

      {/* The charts, unchanged: activity on a real date axis, progress, where you read. */}
      <div>
        {own.session_timeline.length > 1 && (
          <ActivityChart timeline={own.session_timeline} bookId={bookId} onChange={onChange} />
        )}
        {/* Current progress, only when the journey line doesn't actually render
            (it needs 2+ progress points; a single point used to suppress BOTH,
            leaving real progress displayed nowhere). Web books with a drawn line
            show their % in its header, so this avoids two "Progress" rows. */}
        {own.progress != null && own.progress > 0 &&
          !(own.session_timeline.length > 1 &&
            own.session_timeline.filter(d => d.progress_pct != null).length > 1) && (
          <div className="mt-4">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[13px] text-muted-foreground/70"><Trans>Progress</Trans></span>
              <span className="min-w-[3em] text-right text-[13px] font-medium proportional-nums text-foreground"><RunningNumber value={Math.round(own.progress * 100)} className="proportional-nums" />%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-primary transition-[width] duration-300 ease-(--spring)" style={{ width: `${Math.min(Math.round(own.progress * 100), 100)}%` }} />
            </div>
          </div>
        )}
        <SourceSplit sources={own.by_source} />
      </div>

      {dates.length > 0 && (
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-6">
          {dates.map(d => <Field key={d.label} label={d.label} value={<span className="tabular-nums">{d.value}</span>} />)}
        </div>
      )}

      {/* Skip when the only reader is you: the line would just repeat the numbers.
          Still shown when someone ELSE read a book you haven't touched. */}
      {aggregate && (aggregate.distinct_readers > 1 || own.total_seconds === 0) && (
        <p className="text-[13px] text-muted-foreground">
          {(() => {
            const dur = formatDuration(aggregate.total_seconds)
            const days = plural(aggregate.total_sessions, { one: '# reading day', other: '# reading days' })
            const readers = plural(aggregate.distinct_readers, { one: '# reader', other: '# readers' })
            return t`All readers: ${dur} · ${days} · ${readers}`
          })()}
        </p>
      )}

      <ManualLogControls bookId={bookId} onChange={onChange} exportRows={own.session_timeline} />
    </div>
  )
}

function EditField({ label, value, onChange, type = 'text', placeholder = '' }: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
}) {
  const id = useId()
  return (
    <div>
      <FormLabel htmlFor={id}>{label}</FormLabel>
      <Input id={id} type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  )
}

/** A section heading with the block's explainer beside it and an optional
 *  line of figures under it, for the reading charts. */
function ChartSectionTitle({ title, hint, foot, action }: { title: ReactNode; hint: string; foot?: ReactNode; action?: ReactNode }) {
  return (
    <div>
      <SectionTitle size={20} action={action}>
        <span className="inline-flex items-center gap-2">
          {title}
          <span className="font-sans text-[14px] font-normal"><InfoHint text={hint} /></span>
        </span>
      </SectionTitle>
      {foot && <div className="mt-2 text-[14px] text-muted-foreground">{foot}</div>}
    </div>
  )
}
