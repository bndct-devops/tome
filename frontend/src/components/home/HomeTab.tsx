// The Home tab: the book being read with its progress, the other books in
// progress as a row, four bare numbers, Recently added, a highlight and the
// latest reading sessions as plain rows.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence, m } from 'motion/react'
import { useNavigate } from 'react-router-dom'
import { Trans, useLingui, Plural } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { Eye, Play, RefreshCw, Shuffle } from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatDate, shortAgo } from '@/lib/utils'
import { coverSrc } from '@/lib/covers'
import { useGoals } from '@/lib/goals'
import { getFollows, type FollowOut } from '@/lib/follows'
import { useTelemetry } from '@/lib/useTelemetry'
import type { Book } from '@/lib/books'
import { CoverImage } from '@/components/CoverImage'
import { BookLink, CoverLift, EASE, RunningNumber, SeriesLink, listItem, type BookPreview } from '@/components/motion'
import { HScrollRow } from '@/components/HScrollRow'
import { QuickLook, type QuickLookPreview } from '@/components/QuickLook'
import { TelemetryConsentCard } from '@/components/TelemetryConsentCard'
import { AttributedPagesMark } from '@/components/stats/AttributedPagesMark'
import { HomeGoalStart, HomeGoals } from '@/components/goals/HomeGoals'
import { PageBody, PageHeader, Pill, SectionTitle, Stat, StatStrip } from '@/components/page'

// ── Data ────────────────────────────────────────────────────────────────────

interface FocusBook {
  book_id: number
  title: string
  author: string | null
  series: string | null
  series_index: number | null
  description: string | null
  has_cover: boolean
  cover_color?: string | null
  /** Known from a reader's sync only: Tome has no file to open. */
  no_file?: boolean
  progress: number
  device: string | null
  synced: boolean
  last_sync: string | null
}
interface FocusUpcoming { book_id: number; title: string; series_index: number | null; has_cover: boolean; cover_color?: string | null; description: string | null }
interface FocusReading { book_id: number; title: string; author: string | null; has_cover: boolean; cover_color?: string | null; progress: number }
interface FocusData {
  ready: boolean
  book: FocusBook | null
  upcoming?: FocusUpcoming[]
  ahead_count: number
  reading: FocusReading[]
  reading_total?: number
}
interface HomeStats {
  current_streak_days: number
  books_finished_30d: number
  reading_seconds_30d: number
  pages_turned_30d: number
}
interface ActivityEntry {
  book_id: number
  book_title: string
  book_cover_path: string | null
  started_at: string
  duration_seconds: number
  pages_turned: number | null
  // Pages attributed from a CrossInk reader's page counter, not counted per session.
  pages_attributed?: boolean
}
interface CalendarMonth {
  totals: { seconds: number }
  streaks: { current_days: number; longest_days: number; current_weeks: number; longest_weeks: number }
}
interface HighlightSpotlight {
  on_this_day: boolean
  highlight: {
    id: number | string  // "clipping:<n>" for a CrossInk clipping
    book_id: number
    book_title: string
    book_author: string | null
    highlighted_text: string | null
  } | null
}

interface HomeState {
  focusId: number | null
  focus: FocusData | null
  stats: HomeStats | null
  month: CalendarMonth | null
  total: number | null
  recent: Book[]
  activity: ActivityEntry[]
  spotlight: HighlightSpotlight | null
  upcoming: FollowOut[]
}

// Home as it was last shown, so coming back to it is instant and the sections
// refresh in place.
let homeCache: HomeState | null = null

// Requests that count down the first paint: until every one has settled Home
// shows its skeleton, never a false "Nothing in progress", and the sections
// mount in one paint instead of pushing each other down as they arrive.
const GATED = 9

const EMPTY_FOCUS: FocusData = { ready: false, book: null, ahead_count: 0, reading: [] }

// ── Formatting ──────────────────────────────────────────────────────────────

/** Descriptions arrive as HTML from some sources; the hero shows plain text. */
function plainText(html: string): string {
  const el = document.createElement('div')
  el.innerHTML = html
  return (el.textContent ?? '').replace(/\s+/g, ' ').trim()
}

function relativeTime(iso: string, locale: string): string {
  const then = new Date(iso)
  const diff = Math.floor((Date.now() - then.getTime()) / 1000)
  if (diff < 60) return t`just now`
  if (diff < 3600) { const m = Math.floor(diff / 60); return t`${m} min ago` }
  if (diff < 7200) return t`1 hour ago`
  if (diff < 86400) { const h = Math.floor(diff / 3600); return t`${h} hours ago` }
  if (diff < 172800) return t`yesterday`
  // Past a month, "412 days ago" reads worse than the date itself.
  if (diff >= 30 * 86400) return then.toLocaleDateString(locale, { day: 'numeric', month: 'short' })
  const d = Math.floor(diff / 86400)
  return t`${d} days ago`
}

function deviceLabel(device: string | null): string {
  if (!device) return t`Synced`
  const d = device.toLowerCase()
  if (d === 'web') return t`Read on the web`
  if (d.includes('kindle')) return t`Synced from Kindle`
  if (d.includes('kobo')) return t`Synced from Kobo`
  if (d.includes('koreader')) return t`Synced from KOReader`
  return t`Synced from ${device}`
}

function duration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h === 0) { const mm = Math.max(1, m); return t`${mm} min` }
  if (m === 0) return t`${h}h`
  return t`${h}h ${m}m`
}

const fmtIndex = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1))

function monthKey(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

// ── Pieces ──────────────────────────────────────────────────────────────────

/** 6px bar on the accent track with the percentage in bold beside it. The
 *  bar and the figure move when the book's progress changes; key it by book
 *  so another book starts from its own value. */
function ProgressBar({ pct, className, steady }: { pct: number; className?: string; steady?: boolean }) {
  return (
    <div className={cn('flex items-center gap-3', className)}>
      <div className="h-[6px] flex-1 rounded-full overflow-hidden" style={{ background: 'var(--rail)' }}>
        <div className="h-full rounded-full bg-primary transition-[width] duration-300 ease-(--spring)" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
      </div>
      {/* steady: the number keeps a minimum width, so "2%" and "64%" leave tracks of one length */}
      <span className={cn('text-[17px] font-bold tabular-nums text-foreground', steady && 'min-w-[3.2ch] text-right')}>
        <RunningNumber value={Math.round(pct)} format={n => `${Math.round(n)}%`} />
      </span>
    </div>
  )
}

/** The quick-look button in a cover's corner, shown on hover or keyboard focus. */
function PeekButton({ onClick }: { onClick: (e: React.MouseEvent<HTMLButtonElement>) => void }) {
  const { t } = useLingui()
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={t`Quick look`}
      title={t`Quick look`}
      className="absolute bottom-1.5 right-1.5 z-10 grid place-items-center w-7 h-7 rounded-full bg-background/85 text-foreground shadow-sm opacity-0 group-hover/cover:opacity-100 focus-visible:opacity-100 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <Eye className="w-3.5 h-3.5" />
    </button>
  )
}

/** A shelf book with room to spare: the cover, and beside it, centred on it,
 *  the title (up to two lines) linking to the book and what follows it. */
function ShelfCard({ cover, book, children }: { cover: ReactNode; book: BookPreview; children: ReactNode }) {
  return (
    <div className="flex min-w-0 items-center gap-5">
      <div className="w-[104px] shrink-0">{cover}</div>
      <div className="min-w-0 flex-1">
        <BookLink
          book={book}
          className="font-display text-[18px] lg:text-[20px] leading-[1.15] font-bold text-foreground line-clamp-2 hover:underline decoration-2 underline-offset-4"
        >
          {book.title}
        </BookLink>
        {children}
      </div>
    </div>
  )
}

function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground', className)}>{children}</div>
}

function SeeAll({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="text-primary hover:underline underline-offset-4 touch-feedback">
      <Trans>See all</Trans>
    </button>
  )
}

/** Matches the loaded layout: header stays real, hero, row and strip are blocks. */
function HomeSkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-7 animate-pulse">
      <section className="flex flex-col sm:flex-row gap-8 sm:items-center">
        <div className="shrink-0 w-[180px] aspect-[2/3] mx-auto sm:mx-0 rounded-[12px] bg-muted" />
        <div className="min-w-0 flex-1 max-w-[560px] w-full">
          <div className="h-3 w-32 rounded bg-muted" />
          <div className="mt-3 h-8 w-3/4 rounded-lg bg-muted" />
          <div className="mt-3 h-4 w-48 rounded bg-muted" />
          <div className="mt-6 h-[6px] w-full rounded-full bg-muted" />
          <div className="mt-5 h-11 w-36 rounded-full bg-muted" />
        </div>
      </section>
      <section>
        <div className="h-3 w-28 rounded bg-muted mb-3" />
        <div className="flex gap-4 overflow-hidden">
          {[...Array(7)].map((_, i) => <div key={i} className="shrink-0 w-[104px] aspect-[2/3] rounded-[8px] bg-muted" />)}
        </div>
      </section>
      <StatStrip cols={4}>
        {[...Array(4)].map((_, i) => (
          <div key={i}>
            <div className="h-3.5 w-20 rounded bg-muted" />
            <div className="mt-2.5 h-9 w-24 rounded-lg bg-muted" />
            <div className="mt-2.5 h-3.5 w-28 rounded bg-muted" />
          </div>
        ))}
      </StatStrip>
    </div>
  )
}

// ── Home ────────────────────────────────────────────────────────────────────

export function HomeTab({ onBrowse, onSeeAllRecent }: { onBrowse: () => void; onSeeAllRecent: () => void }) {
  const { t, i18n } = useLingui()
  const navigate = useNavigate()
  const [seed] = useState(() => homeCache)
  const [focusId, setFocusId] = useState<number | null>(seed?.focusId ?? null)
  const [focus, setFocus] = useState<FocusData | null>(seed?.focus ?? null)
  const [stats, setStats] = useState<HomeStats | null>(seed?.stats ?? null)
  const [month, setMonth] = useState<CalendarMonth | null>(seed?.month ?? null)
  const [total, setTotal] = useState<number | null>(seed?.total ?? null)
  const [recent, setRecent] = useState<Book[]>(seed?.recent ?? [])
  const [activity, setActivity] = useState<ActivityEntry[]>(seed?.activity ?? [])
  const [spotlight, setSpotlight] = useState<HighlightSpotlight | null>(seed?.spotlight ?? null)
  const [upcoming, setUpcoming] = useState<FollowOut[]>(seed?.upcoming ?? [])
  const [switching, setSwitching] = useState(false)
  // Another highlight is on its way: the shown one dims until it is in.
  const [shuffling, setShuffling] = useState(false)
  const [peek, setPeek] = useState<number | null>(null)
  // The cover the quick look grows out of (and shrinks back into).
  const [peekFrom, setPeekFrom] = useState<{ origin?: DOMRect; cover?: string | null; preview?: QuickLookPreview }>({})
  const openPeek = (id: number, from: Element | null | undefined, cover: string | null, preview?: QuickLookPreview) => { setPeekFrom({ origin: from?.getBoundingClientRect(), cover, preview }); setPeek(id) }

  // The telemetry question (admins) waits with the rest, or its card lands on
  // top a moment later and pushes the whole page down.
  const telemetry = useTelemetry()
  const [settled, setSettled] = useState(seed ? GATED : 0)
  const hydrated = settled >= GATED && telemetry.settled
  const countSettled = () => setSettled(n => n + 1)
  // The goals are the shared copy (lib/goals): its first answer counts towards the gate.
  const { data: goalsAnswer } = useGoals(countSettled)

  useEffect(() => {
    const tz = new Date().getTimezoneOffset()
    const today = new Date().toISOString().slice(0, 10)
    const q = focusId != null ? `?book_id=${focusId}` : ''
    api.get<FocusData>(`/home/focus${q}`).finally(countSettled).then(setFocus).catch(() => setFocus(f => f ?? EMPTY_FOCUS))
    api.get<HomeStats>(`/home/stats?tz_offset=${tz}`).finally(countSettled).then(setStats).catch(() => {})
    api.get<CalendarMonth>(`/stats/calendar?month=${monthKey()}&tz_offset=${tz}`).finally(countSettled).then(setMonth).catch(() => {})
    api.getWithHeaders<Book[]>('/books?limit=1').finally(countSettled).then(({ headers }) => {
      const raw = headers.get('x-total-count')
      if (raw != null) setTotal(Number(raw))
    }).catch(() => {})
    api.get<Book[]>('/books?sort=added_at&order=desc&limit=12').finally(countSettled).then(setRecent).catch(() => {})
    api.get<ActivityEntry[]>('/home/activity').finally(countSettled).then(setActivity).catch(() => {})
    api.get<HighlightSpotlight>(`/annotations/spotlight?include_clippings=1&tz_offset=${new Date().getTimezoneOffset()}`).finally(countSettled).then(setSpotlight).catch(() => {})
    getFollows().finally(countSettled).then(all => {
      setUpcoming((all ?? [])
        .filter(f => f.latest_release_date != null && f.latest_release_date >= today)
        .sort((a, b) => (a.latest_release_date! < b.latest_release_date! ? -1 : 1))
        .slice(0, 5))
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!hydrated) return
    homeCache = { focusId, focus, stats, month, total, recent, activity, spotlight, upcoming }
  }, [hydrated, focusId, focus, stats, month, total, recent, activity, spotlight, upcoming])

  // Switching the hero: the old book stays, dimmed, until the new one is in.
  // Aborted on re-fire, so a slower earlier answer can never land last.
  const switchAbort = useRef<AbortController | null>(null)
  const switchTo = (id: number) => {
    if (id === focus?.book?.book_id) return
    switchAbort.current?.abort()
    const ctrl = new AbortController()
    switchAbort.current = ctrl
    setFocusId(id)
    setSwitching(true)
    api.get<FocusData>(`/home/focus?book_id=${id}`, ctrl.signal)
      // The shelves that change keep their old covers (dimmed) until the new
      // ones are in, at most 300 ms.
      .then(async d => {
        const ids = [...(d.upcoming ?? []).filter(u => u.has_cover), ...d.reading.filter(r => r.has_cover)].map(x => x.book_id)
        await Promise.race([
          Promise.all(ids.slice(0, 12).map(bid => { const img = new Image(); img.src = coverSrc(bid, { w: 400 }); return img.decode().catch(() => {}) })),
          new Promise(r => setTimeout(r, 300)),
        ])
        if (!ctrl.signal.aborted) { setFocus(d); setSwitching(false) }
      })
      .catch(e => { if ((e as Error).name !== 'AbortError') setSwitching(false) })
  }

  const shuffleSpotlight = () => {
    const cur = spotlight?.highlight?.id
    setShuffling(true)
    api.get<HighlightSpotlight>(`/annotations/spotlight?include_clippings=1&tz_offset=${new Date().getTimezoneOffset()}${cur ? `&exclude=${encodeURIComponent(cur)}` : ''}`)
      .then(setSpotlight).catch(() => {}).finally(() => setShuffling(false))
  }

  const dailyStreak = month?.streaks.current_days ?? stats?.current_streak_days ?? 0
  const today = new Date().toLocaleDateString(i18n.locale, { weekday: 'long', day: 'numeric', month: 'long' })
  const b = focus?.book ?? null

  const header = (
    <PageHeader
      eyebrow={today}
      title={<Trans>Home</Trans>}
    />
  )

  // The skeleton fades away over the page that lands under it.
  const skeleton = (
    <AnimatePresence mode="popLayout" initial={false}>
      {!hydrated && (
        <m.div key="skeleton" exit={{ opacity: 0 }} transition={{ duration: 0.18 }} className="w-full">
          <HomeSkeleton />
        </m.div>
      )}
    </AnimatePresence>
  )

  if (!hydrated) {
    return (
      <PageBody width="wide">
        <div className="flex flex-col gap-5">{header}</div>
        <div className="relative flex flex-col gap-7">{skeleton}</div>
      </PageBody>
    )
  }

  // Back-to-back sessions of the same book collapse into one row.
  const activityRows: { entry: ActivityEntry; sessions: number; seconds: number; pages: number; attributed: boolean }[] = []
  for (const entry of activity) {
    const last = activityRows[activityRows.length - 1]
    if (last && last.entry.book_id === entry.book_id
      && new Date(last.entry.started_at).toDateString() === new Date(entry.started_at).toDateString()) {
      last.sessions += 1
      last.seconds += entry.duration_seconds
      last.pages += entry.pages_turned ?? 0
      last.attributed ||= !!entry.pages_attributed && (entry.pages_turned ?? 0) > 0
    } else {
      activityRows.push({
        entry, sessions: 1, seconds: entry.duration_seconds, pages: entry.pages_turned ?? 0,
        attributed: !!entry.pages_attributed && (entry.pages_turned ?? 0) > 0,
      })
    }
    if (activityRows.length > 5) break
  }
  const rows = activityRows.slice(0, 5)

  const time = month?.totals.seconds ?? stats?.reading_seconds_30d ?? 0
  const hours = Math.floor(time / 3600)
  const mins = Math.floor((time % 3600) / 60)
  const weekly = !!month && month.streaks.current_days === 0 && month.streaks.current_weeks > 0
  const finished = stats?.books_finished_30d ?? 0
  const more = (focus?.reading_total ?? 0) - (focus?.reading.length ?? 0)
  // The hero is not repeated as the first cover of the row.
  const others = (focus?.reading ?? []).filter(r => r.book_id !== b?.book_id)

  const ups = b ? (focus?.upcoming ?? []).slice(0, 5) : []
  // One or two books across both shelves show as cards; the "+N" tile only
  // exists with a full shelf, so it never meets a card.
  const cards = others.length + ups.length <= 2 && more <= 0
  const upLabel = (() => { const n = focus?.ahead_count ?? 0; return <Trans>Up next · {n} ahead</Trans> })()

  // The cover of an Also reading book: a click puts it in the hero, the corner
  // button opens the quick look. Dimmed until hovered among the covers only.
  // It shares its layoutId with the hero's cover: the two swap places.
  const readingCover = (r: FocusReading, dim: boolean) => {
    const title = r.title
    return (
      <m.div layoutId={`home-cover-${r.book_id}`} transition={{ layout: { duration: 0.3, ease: EASE } }}>
      <CoverLift className="relative group/cover">
        <button
          type="button"
          onClick={() => switchTo(r.book_id)}
          aria-label={t`Show ${title} here`}
          title={t`Show ${title} here`}
          data-cover-id={r.book_id}
          className={cn('relative block w-full aspect-[2/3] rounded-[8px] overflow-hidden bg-muted transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-ring', dim && 'opacity-80 hover:opacity-100')}
        >
          <CoverImage src={r.has_cover ? coverSrc(r.book_id, { w: 400 }) : null} alt="" color={r.cover_color} iconClassName="w-5 h-5" />
        </button>
        <PeekButton onClick={e => openPeek(r.book_id, e.currentTarget.previousElementSibling, r.has_cover ? coverSrc(r.book_id, { w: 400 }) : null, { title: r.title, author: r.author })} />
      </CoverLift>
      </m.div>
    )
  }

  // The cover of an Up next volume opens its quick look. It lifts like every
  // other cover, in the card and in the packed row alike.
  const upCover = (u: FocusUpcoming, dim: boolean) => {
    const title = u.title
    return (
      <CoverLift>
      <button
        type="button"
        onClick={e => openPeek(u.book_id, e.currentTarget.firstElementChild, u.has_cover ? coverSrc(u.book_id, { w: 400 }) : null, { title: u.title, author: b?.author, series: b?.series, series_index: u.series_index })}
        className="block w-full text-left group focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-[8px]"
        aria-label={t`Quick look: ${title}`}
        title={t`Quick look`}
      >
        <div data-cover-id={u.book_id} className={cn('relative aspect-[2/3] rounded-[8px] overflow-hidden bg-muted transition-opacity', dim && 'opacity-80 group-hover:opacity-100')}>
          <CoverImage src={u.has_cover ? coverSrc(u.book_id, { w: 400 }) : null} alt="" color={u.cover_color} iconClassName="w-5 h-5" />
        </div>
      </button>
      </CoverLift>
    )
  }

  // What a link to a shelf book hands the book page, so its hero paints at once.
  const readingPreview = (r: FocusReading): BookPreview => ({ id: r.book_id, title: r.title, author: r.author, coverColor: r.cover_color })
  const upPreview = (u: FocusUpcoming): BookPreview => ({
    id: u.book_id, title: u.title, author: b?.author ?? null, series: b?.series ?? null, series_index: u.series_index, coverColor: u.cover_color,
  })

  const readingCard = (r: FocusReading) => (
    <ShelfCard key={r.book_id} cover={readingCover(r, false)} book={readingPreview(r)}>
      {r.author && <div className="mt-1 truncate text-[15px] leading-[1.4] text-muted-foreground">{r.author}</div>}
      <ProgressBar pct={r.progress} className="mt-3 h-5" steady />
    </ShelfCard>
  )

  const upCard = (u: FocusUpcoming, i: number) => {
    const series = b?.series ?? ''
    const v = u.series_index != null ? fmtIndex(u.series_index) : null
    const where = i === 0
      ? (v != null ? <Trans>Volume {v} · next in {series}</Trans> : <Trans>Next in {series}</Trans>)
      : (v != null ? <Trans>Volume {v} · {series}</Trans> : series)
    const blurb = u.description ? plainText(u.description) : ''
    return (
      <ShelfCard key={u.book_id} cover={upCover(u, false)} book={upPreview(u)}>
        <div className="mt-1 truncate text-[15px] leading-[1.4] text-muted-foreground">{where}</div>
        {blurb && <p className="mt-3 hidden text-[14px] leading-5 text-muted-foreground lg:line-clamp-2">{blurb}</p>}
      </ShelfCard>
    )
  }

  const heroPreview: BookPreview | null = b && {
    id: b.book_id, title: b.title, author: b.author, series: b.series, series_index: b.series_index,
    cover: b.has_cover ? coverSrc(b.book_id, { w: 400 }) : null, coverColor: b.cover_color,
  }

  return (
    <PageBody width="wide">
      <div className="flex flex-col gap-5">
        {header}
        <TelemetryConsentCard telemetry={telemetry} />
      </div>

      <div className="relative flex flex-col gap-7">
      {skeleton}

      {/* ── Continue reading ─────────────────────────────────────────────── */}
      {!b || !heroPreview ? (
        <section className="py-6">
          <div className="text-[17px] font-semibold text-foreground"><Trans>Nothing in progress</Trans></div>
          <div className="text-[15px] text-muted-foreground mt-1"><Trans>Start a book and it shows up here</Trans></div>
          <Pill variant="secondary" className="mt-4" onClick={onBrowse}><Trans>Browse library</Trans></Pill>
        </section>
      ) : (
        <section className={cn('flex flex-col sm:flex-row gap-7 sm:items-start transition-opacity', switching && 'opacity-60')}>
          <m.div key={b.book_id} layoutId={`home-cover-${b.book_id}`} transition={{ layout: { duration: 0.3, ease: EASE } }} className="shrink-0 w-[180px] mx-auto sm:mx-0">
          <CoverLift>
            <BookLink
              book={heroPreview}
              data-cover-id={b.book_id}
              className="block rounded-[12px] overflow-hidden bg-muted"
              style={{ boxShadow: 'var(--cover-bloom-hero)' }}
              aria-label={b.title}
            >
              <div className="relative aspect-[2/3]">
                <CoverImage src={b.has_cover ? coverSrc(b.book_id, { w: 400 }) : null} alt="" loading="eager" color={b.cover_color} iconClassName="w-10 h-10" />
              </div>
            </BookLink>
          </CoverLift>
          </m.div>
          <div className="min-w-0 flex-1 max-w-[640px]">
            {/* The eyebrow names the series when there is one, with the volume;
                a standalone book keeps "Continue reading". */}
            {b.series ? (
              <SeriesLink
                name={b.series}
                className="inline-block text-[12px] font-bold uppercase tracking-[0.1em] text-primary hover:underline underline-offset-4"
              >
                {b.series}{b.series_index != null && (() => { const v = fmtIndex(b.series_index); return <> · <Trans>Volume {v}</Trans></> })()}
              </SeriesLink>
            ) : (
              <Eyebrow className="text-primary"><Trans>Continue reading</Trans></Eyebrow>
            )}
            <h2 className="font-display text-[32px] leading-[1.05] font-bold text-foreground mt-2">
              <BookLink book={heroPreview} className="hover:underline decoration-2 underline-offset-4">{b.title}</BookLink>
            </h2>
            {b.author && <div className="mt-1.5 text-[16px] text-muted-foreground">{b.author}</div>}
            {b.description && (
              <p className="mt-2.5 text-[15px] leading-relaxed text-foreground/80 line-clamp-2 max-w-[64ch]">{plainText(b.description)}</p>
            )}
            <ProgressBar key={b.book_id} pct={b.progress} className="mt-4" />
            <div className="mt-3.5 flex flex-wrap items-center gap-3">
              {b.no_file ? (
                <span className="inline-flex h-9 items-center rounded-full bg-secondary px-4 text-[14px] font-medium text-foreground">
                  <Trans>On your reader</Trans>
                </span>
              ) : (
                <Pill variant="primary" size="lg" onClick={() => navigate(`/reader/${b.book_id}`)}>
                  <Play className="w-4 h-4 fill-current" />
                  <Trans>Resume</Trans>
                </Pill>
              )}
              {b.last_sync && (
                <span className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground">
                  <RefreshCw className="w-3.5 h-3.5" />
                  {deviceLabel(b.device)} · {shortAgo(b.last_sync)}
                </span>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ── Also reading and Up next: the other books in progress, and the
          volumes after the hero in its series, always one row on desktop. One or
          two books in all show as cards in two equal halves; from three on, the
          covers with captions, both shelves packed from the left. The hero stays
          short so the stat strip is on the first screen. ──────────────────── */}
      {focus && (others.length > 0 || ups.length > 0) && (cards ? (
        <section className="grid grid-cols-1 gap-8 lg:grid-cols-2">
          {others.length > 0 && ups.length > 0 ? (
            <>
              <div className="min-w-0">
                <Eyebrow className="mb-2"><Trans>Also reading</Trans></Eyebrow>
                {readingCard(others[0])}
              </div>
              <div className="min-w-0 lg:border-l lg:border-border/70 lg:pl-8">
                <Eyebrow className="mb-2">{upLabel}</Eyebrow>
                {upCard(ups[0], 0)}
              </div>
            </>
          ) : (
            // One shelf: its label spans the row, its one or two cards sit in the halves.
            <div className="min-w-0 lg:col-span-2">
              <Eyebrow className="mb-2">{others.length > 0 ? <Trans>Also reading</Trans> : upLabel}</Eyebrow>
              <div className="grid grid-cols-1 gap-x-8 gap-y-5 lg:grid-cols-2">
                {others.length > 0 ? others.map(readingCard) : ups.map(upCard)}
              </div>
            </div>
          )}
        </section>
      ) : (
        <section className="flex flex-col gap-8 lg:flex-row lg:items-start">
          {others.length > 0 && (
            // Packed from the left: as wide as its covers, and it shrinks and
            // scrolls sideways when the row does not fit; Up next keeps its width.
            <div className="min-w-0">
              <Eyebrow className="mb-2"><Trans>Also reading</Trans></Eyebrow>
              <HScrollRow className="gap-4 px-2 pt-3 pb-2" wrapClassName="-mx-2 -mt-3" controlsTop="top-[84px]">
                <AnimatePresence initial={false}>
                {others.map(r => (
                  <m.div key={r.book_id} {...listItem} className="shrink-0 w-[104px]">
                    {readingCover(r, true)}
                    <div className="mt-2 h-[3px] rounded-full overflow-hidden" style={{ background: 'var(--rail)' }}>
                      <div className="h-full bg-primary transition-[width] duration-300 ease-(--spring)" style={{ width: `${Math.min(100, r.progress)}%` }} />
                    </div>
                    <BookLink book={readingPreview(r)} className="mt-1.5 block text-[13px] truncate text-muted-foreground hover:text-primary transition-colors">{r.title}</BookLink>
                  </m.div>
                ))}
                {more > 0 && (
                  <m.button key="more" layout="position" transition={listItem.transition} type="button" onClick={() => navigate('/?tab=books&reading_status=reading')} className="shrink-0 w-[104px] text-left group touch-feedback">
                    <div className="aspect-[2/3] rounded-[8px] bg-secondary grid place-items-center text-[15px] font-semibold text-muted-foreground group-hover:text-foreground transition-colors">
                      +{more}
                    </div>
                    <div className="mt-[17px] text-[13px] text-muted-foreground"><Trans>See all</Trans></div>
                  </m.button>
                )}
                </AnimatePresence>
              </HScrollRow>
            </div>
          )}
          {ups.length > 0 && (
            <div className={cn('shrink-0', others.length > 0 && 'lg:border-l lg:border-border/70 lg:pl-8')}>
              <Eyebrow className="mb-2">{upLabel}</Eyebrow>
              <div className="flex gap-4 pb-2">
                <AnimatePresence initial={false}>
                {ups.map(u => (
                  <m.div key={u.book_id} {...listItem} className="shrink-0 w-[104px]">
                    {upCover(u, true)}
                    <BookLink book={upPreview(u)} className="mt-[17px] block text-[13px] text-muted-foreground truncate hover:text-primary transition-colors">
                      {u.series_index != null ? (() => { const v = fmtIndex(u.series_index); return <Trans>Volume {v}</Trans> })() : u.title}
                    </BookLink>
                  </m.div>
                ))}
                </AnimatePresence>
              </div>
            </div>
          )}
        </section>
      ))}

      {/* ── The numbers ──────────────────────────────────────────────────── */}
      <div className="flex flex-col gap-5">
        <StatStrip cols={4}>
          {weekly ? (
            <Stat
              label={<Trans>Streak</Trans>}
              value={month!.streaks.current_weeks}
              unit={plural(month!.streaks.current_weeks, { one: 'week', other: 'weeks' })}
              tone="primary"
              foot={(() => { const n = month!.streaks.longest_weeks; return <Trans>Reading every week · Best {n} weeks</Trans> })()}
            />
          ) : (
            <Stat
              label={<Trans>Streak</Trans>}
              value={dailyStreak}
              unit={plural(dailyStreak, { one: 'day', other: 'days' })}
              tone="primary"
              foot={month ? (() => { const n = month.streaks.longest_days; return <Trans>Best {n} days</Trans> })() : undefined}
            />
          )}
          {/* The leading figure runs; hours and minutes keep their own words. */}
          <Stat
            label={<Trans>This month</Trans>}
            value={hours > 0 ? hours : mins}
            format={hours > 0 ? (n => { const hours = Math.round(n); return t`${hours}h` }) : (n => { const mins = Math.round(n); return t`${mins}m` })}
            unit={hours > 0 ? t`${mins}m` : undefined}
            tone="info"
            foot={<Trans>Time reading</Trans>}
          />
          <Stat
            label={<Trans>Finished</Trans>}
            value={finished}
            unit={plural(finished, { one: 'book', other: 'books' })}
            tone="success"
            foot={<Trans>Last 30 days</Trans>}
          />
          <Stat
            label={<Trans>Library</Trans>}
            value={total ?? '-'}
            unit={total != null ? plural(total, { one: 'book', other: 'books' }) : undefined}
            foot={<Trans>On the server</Trans>}
          />
        </StatStrip>

        {/* The goals, or the way to a first one: both come with the page's one paint. */}
        {goalsAnswer && (goalsAnswer.goals.length > 0 ? <HomeGoals answer={goalsAnswer} /> : <HomeGoalStart goals={goalsAnswer.goals} />)}
      </div>

      {/* ── Recently added ───────────────────────────────────────────────── */}
      {recent.length > 0 && (
        <section data-box="home-recent">
          <SectionTitle action={<SeeAll onClick={onSeeAllRecent} />}><Trans>Recently added</Trans></SectionTitle>
          <HScrollRow className="gap-4 px-2 pt-5 pb-3" wrapClassName="-mx-2 -mt-1" controlsTop="top-[128px]">
            {recent.map(book => { const title = book.title; const cover = book.cover_path ? coverSrc(book.id, { w: 400, v: book.updated_at }) : null; return (
              <div key={book.id} className="shrink-0 w-[150px]">
                <CoverLift className="relative">
                  <button
                    type="button"
                    onClick={e => openPeek(book.id, e.currentTarget, cover, { title: book.title, author: book.author, series: book.series, series_index: book.series_index })}
                    data-cover-id={book.id}
                    className="relative block w-full aspect-[2/3] rounded-[12px] overflow-hidden bg-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    style={{ boxShadow: 'var(--cover-bloom-fan)' }}
                    aria-label={t`Quick look: ${title}`}
                    title={t`Quick look`}
                  >
                    <CoverImage src={cover} alt="" color={book.cover_color} />
                  </button>
                </CoverLift>
                <BookLink
                  book={{ id: book.id, title: book.title, author: book.author, series: book.series, series_index: book.series_index, cover, coverColor: book.cover_color }}
                  className="mt-2.5 block text-[15px] text-foreground leading-snug line-clamp-2 hover:text-primary transition-colors"
                >
                  {book.title}
                </BookLink>
              </div>
            ) })}
          </HScrollRow>
        </section>
      )}

      {/* ── Upcoming releases of followed series ─────────────────────────── */}
      {upcoming.length > 0 && (
        <section>
          <SectionTitle><Trans>Upcoming releases</Trans></SectionTitle>
          <div className="mt-3">
            {upcoming.map((f, i) => (
              <div key={f.id} className={cn('flex items-center gap-4 py-3', i > 0 && 'border-t border-border/70')}>
                <div className="relative w-11 h-16 rounded-md overflow-hidden bg-muted shrink-0">
                  <CoverImage src={f.cover_url} alt="" iconClassName="w-4 h-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[16px] font-semibold text-foreground truncate">{f.name}</div>
                  {f.latest_known_index != null && (
                    <div className="text-[14px] text-muted-foreground mt-0.5">
                      {(() => { const v = fmtIndex(f.latest_known_index); return <Trans>Volume {v}</Trans> })()}
                    </div>
                  )}
                </div>
                <div className="text-[14px] text-muted-foreground shrink-0">{formatDate(f.latest_release_date!)}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── A highlight ──────────────────────────────────────────────────── */}
      {spotlight?.highlight?.highlighted_text && (
        <section>
          <SectionTitle
            action={
              <div className="flex items-center gap-1">
                <Pill variant="quiet" icon onClick={shuffleSpotlight} aria-label={t`Show another highlight`} title={t`Show another highlight`}>
                  <Shuffle className="w-4 h-4" />
                </Pill>
                <Pill variant="quiet" onClick={() => navigate('/highlights')}><Trans>All</Trans></Pill>
              </div>
            }
          >
            {spotlight.on_this_day ? <Trans>On this day</Trans> : <Trans>From your highlights</Trans>}
          </SectionTitle>
          <BookLink
            book={{ id: spotlight.highlight.book_id, title: spotlight.highlight.book_title, author: spotlight.highlight.book_author }}
            className={cn('mt-4 block group max-w-[760px] transition-opacity duration-150', shuffling && 'opacity-50')}
          >
            <blockquote className="border-l-2 border-primary/50 pl-5 text-[17px] leading-relaxed text-foreground line-clamp-5">
              {spotlight.highlight.highlighted_text}
            </blockquote>
            <div className="mt-2.5 pl-5 text-[14px] text-muted-foreground group-hover:text-primary transition-colors">
              {spotlight.highlight.book_author
                ? (() => { const title = spotlight.highlight.book_title; const author = spotlight.highlight.book_author; return <Trans>{title}, {author}</Trans> })()
                : spotlight.highlight.book_title}
            </div>
          </BookLink>
        </section>
      )}

      {/* ── Recent activity ──────────────────────────────────────────────── */}
      {rows.length > 0 && (
        <section>
          <SectionTitle action={<SeeAll onClick={() => navigate('/stats')} />}><Trans>Recent activity</Trans></SectionTitle>
          <div className="mt-3">
            {rows.map(({ entry, sessions, seconds, pages, attributed }, i) => (
              <BookLink
                key={entry.started_at}
                book={{ id: entry.book_id, title: entry.book_title, cover: entry.book_cover_path ? coverSrc(entry.book_id, { w: 160 }) : null }}
                className={cn('flex items-center gap-4 py-3 group touch-feedback', i > 0 && 'border-t border-border/70')}
              >
                <div data-cover-id={entry.book_id} className="relative w-11 h-16 rounded-md overflow-hidden bg-muted shrink-0">
                  <CoverImage src={entry.book_cover_path ? coverSrc(entry.book_id, { w: 160 }) : null} alt="" iconClassName="w-4 h-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[16px] font-semibold text-foreground truncate group-hover:text-primary transition-colors">{entry.book_title}</div>
                  <div className="text-[14px] text-muted-foreground mt-0.5">
                    {sessions > 1 && <><Plural value={sessions} one="# session" other="# sessions" />{' · '}</>}
                    {duration(seconds)}
                    {pages > 0 && <>{' · '}<Plural value={pages} one="# page" other="# pages" />{attributed && <>{' '}<AttributedPagesMark includes /></>}</>}
                  </div>
                </div>
                <div className="text-[14px] text-muted-foreground shrink-0">{relativeTime(entry.started_at, i18n.locale)}</div>
              </BookLink>
            ))}
          </div>
        </section>
      )}
      </div>

      <AnimatePresence>
        {peek != null && <QuickLook key={peek} bookId={peek} origin={peekFrom.origin} cover={peekFrom.cover} preview={peekFrom.preview} onClose={() => setPeek(null)} />}
      </AnimatePresence>
    </PageBody>
  )
}
