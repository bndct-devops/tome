// The Home tab: the book being read with its progress, the other books in
// progress as a row, four bare numbers, Recently added, a highlight and the
// latest reading sessions as plain rows.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Trans, useLingui, Plural } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { Eye, Flame, Play, RefreshCw, Shuffle } from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatDate } from '@/lib/utils'
import { coverSrc } from '@/lib/covers'
import { listGoals, goalMeta, goalTitle, goalSubtext, type Goal } from '@/lib/goals'
import { getFollows, type FollowOut } from '@/lib/follows'
import { useTelemetry } from '@/lib/useTelemetry'
import type { Book } from '@/lib/books'
import { CoverImage } from '@/components/CoverImage'
import { useTilt } from '@/lib/tilt'
import { HScrollRow } from '@/components/HScrollRow'
import { QuickLook } from '@/components/QuickLook'
import { TelemetryConsentCard } from '@/components/TelemetryConsentCard'
import { ProgressRing } from '@/components/stats/ProgressRing'
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
  progress: number
  device: string | null
  synced: boolean
  last_sync: string | null
}
interface FocusData {
  ready: boolean
  book: FocusBook | null
  ahead_count: number
  reading: { book_id: number; title: string; author: string | null; has_cover: boolean; progress: number }[]
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
}
interface CalendarMonth {
  totals: { seconds: number }
  streaks: { current_days: number; longest_days: number; current_weeks: number; longest_weeks: number }
}
interface HighlightSpotlight {
  on_this_day: boolean
  highlight: {
    id: number
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
  goals: Goal[]
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

/** 6px bar on the accent track with the percentage in bold beside it. */
function ProgressBar({ pct, className }: { pct: number; className?: string }) {
  return (
    <div className={cn('flex items-center gap-3', className)}>
      <div className="h-[6px] flex-1 rounded-full overflow-hidden" style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--background))' }}>
        <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
      </div>
      <span className="text-[17px] font-bold tabular-nums text-foreground">{Math.round(pct)}%</span>
    </div>
  )
}

/** The quick-look button in a cover's corner, shown on hover or keyboard focus. */
function PeekButton({ onClick }: { onClick: () => void }) {
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

function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground', className)}>{children}</div>
}

function SeeAll({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="text-primary hover:underline underline-offset-4">
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
  const [goals, setGoals] = useState<Goal[]>(seed?.goals ?? [])
  const [upcoming, setUpcoming] = useState<FollowOut[]>(seed?.upcoming ?? [])
  const [switching, setSwitching] = useState(false)
  const [peek, setPeek] = useState<number | null>(null)

  // The telemetry question (admins) waits with the rest, or its card lands on
  // top a moment later and pushes the whole page down.
  const telemetry = useTelemetry()
  const [settled, setSettled] = useState(seed ? GATED : 0)
  const hydrated = settled >= GATED && telemetry.settled
  const countSettled = () => setSettled(n => n + 1)

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
    api.get<HighlightSpotlight>('/annotations/spotlight').finally(countSettled).then(setSpotlight).catch(() => {})
    listGoals().finally(countSettled).then(setGoals).catch(() => {})
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
    homeCache = { focusId, focus, stats, month, total, recent, activity, spotlight, goals, upcoming }
  }, [hydrated, focusId, focus, stats, month, total, recent, activity, spotlight, goals, upcoming])

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
      .then(d => { setFocus(d); setSwitching(false) })
      .catch(e => { if ((e as Error).name !== 'AbortError') setSwitching(false) })
  }

  const shuffleSpotlight = () => {
    const cur = spotlight?.highlight?.id
    api.get<HighlightSpotlight>(`/annotations/spotlight${cur ? `?exclude=${cur}` : ''}`).then(setSpotlight).catch(() => {})
  }

  const dailyStreak = month?.streaks.current_days ?? stats?.current_streak_days ?? 0
  const today = new Date().toLocaleDateString(i18n.locale, { weekday: 'long', day: 'numeric', month: 'long' })
  const b = focus?.book ?? null

  const header = (
    <PageHeader
      eyebrow={today}
      title={<Trans>Home</Trans>}
      actions={dailyStreak > 0 ? (
        <div
          className="inline-flex items-center gap-1.5 rounded-full px-3.5 h-10 bg-primary/15 text-primary font-semibold text-[15px]"
          title={plural(dailyStreak, { one: '# day streak', other: '# day streak' })}
        >
          <Flame className="w-4 h-4" />
          <span className="tabular-nums">{dailyStreak}</span>
        </div>
      ) : undefined}
    />
  )

  if (!hydrated) {
    return (
      <PageBody width="wide">
        {header}
        <HomeSkeleton />
      </PageBody>
    )
  }

  // Back-to-back sessions of the same book collapse into one row.
  const activityRows: { entry: ActivityEntry; sessions: number; seconds: number; pages: number }[] = []
  for (const entry of activity) {
    const last = activityRows[activityRows.length - 1]
    if (last && last.entry.book_id === entry.book_id
      && new Date(last.entry.started_at).toDateString() === new Date(entry.started_at).toDateString()) {
      last.sessions += 1
      last.seconds += entry.duration_seconds
      last.pages += entry.pages_turned ?? 0
    } else {
      activityRows.push({ entry, sessions: 1, seconds: entry.duration_seconds, pages: entry.pages_turned ?? 0 })
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

  return (
    <PageBody width="wide">
      <div className="flex flex-col gap-5">
        {header}
        <TelemetryConsentCard telemetry={telemetry} />
      </div>

      {/* ── Continue reading ─────────────────────────────────────────────── */}
      {!b ? (
        <section className="py-6">
          <div className="text-[17px] font-semibold text-foreground"><Trans>Nothing in progress</Trans></div>
          <div className="text-[15px] text-muted-foreground mt-1"><Trans>Start a book and it shows up here</Trans></div>
          <Pill variant="secondary" className="mt-4" onClick={onBrowse}><Trans>Browse library</Trans></Pill>
        </section>
      ) : (
        <section className={cn('flex flex-col sm:flex-row gap-8 sm:items-center transition-opacity', switching && 'opacity-60')}>
          <TiltCover className="shrink-0 w-[180px] mx-auto sm:mx-0" degrees={4}>
            <Link
              to={`/books/${b.book_id}`}
              className="block rounded-[12px] overflow-hidden bg-muted"
              style={{ boxShadow: 'var(--cover-bloom-hero)' }}
              aria-label={b.title}
            >
              <div className="relative aspect-[2/3]">
                <CoverImage src={b.has_cover ? coverSrc(b.book_id, { w: 400 }) : null} alt="" loading="eager" iconClassName="w-10 h-10" />
              </div>
            </Link>
          </TiltCover>
          <div className="min-w-0 flex-1 max-w-[560px]">
            <Eyebrow className="text-primary"><Trans>Continue reading</Trans></Eyebrow>
            <h2 className="font-display text-[32px] leading-[1.05] font-bold text-foreground mt-2">
              <Link to={`/books/${b.book_id}`} className="hover:underline decoration-2 underline-offset-4">{b.title}</Link>
            </h2>
            {(b.series_index != null || b.author) && (
              <div className="mt-1.5 text-[16px] text-muted-foreground">
                {b.series_index != null && (() => {
                  const v = fmtIndex(b.series_index)
                  return <span className="text-foreground font-medium"><Trans>Volume {v}</Trans></span>
                })()}
                {b.series_index != null && b.author && ' · '}
                {b.author}
              </div>
            )}
            <ProgressBar pct={b.progress} className="mt-5" />
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Pill variant="primary" size="lg" onClick={() => navigate(`/reader/${b.book_id}`)}>
                <Play className="w-4 h-4 fill-current" />
                <Trans>Resume</Trans>
              </Pill>
              {b.last_sync && (
                <div className="inline-flex items-center gap-2 rounded-full bg-secondary px-3.5 h-9 text-[14px] font-medium text-foreground">
                  <RefreshCw className="w-3.5 h-3.5" />
                  {deviceLabel(b.device)}
                  <span className="text-muted-foreground">· {relativeTime(b.last_sync, i18n.locale)}</span>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ── Also reading: every book in progress, the hero ringed ────────── */}
      {focus && focus.reading.length > 1 && (
        <section>
          <Eyebrow className="mb-2"><Trans>Also reading</Trans></Eyebrow>
          <HScrollRow className="gap-4 px-2 pt-3 pb-2" wrapClassName="-mx-2 -mt-3" controlsTop="top-[84px]">
            {focus.reading.map(r => {
              const active = r.book_id === b?.book_id
              return (
                <div key={r.book_id} className="shrink-0 w-[104px]">
                  <TiltCover className="relative group/cover">
                    <button
                      type="button"
                      onClick={() => switchTo(r.book_id)}
                      aria-current={active}
                      aria-label={r.title}
                      className={cn(
                        'relative block w-full aspect-[2/3] rounded-[8px] overflow-hidden bg-muted transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                        active ? 'ring-2 ring-primary ring-offset-2 ring-offset-background' : 'opacity-80 hover:opacity-100',
                      )}
                    >
                      <CoverImage src={r.has_cover ? coverSrc(r.book_id, { w: 400 }) : null} alt="" iconClassName="w-5 h-5" />
                    </button>
                    <PeekButton onClick={() => setPeek(r.book_id)} />
                  </TiltCover>
                  <div className="mt-2 h-[3px] rounded-full overflow-hidden" style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--background))' }}>
                    <div className="h-full bg-primary" style={{ width: `${Math.min(100, r.progress)}%` }} />
                  </div>
                  <div className={cn('mt-1.5 text-[13px] truncate', active ? 'text-foreground font-semibold' : 'text-muted-foreground')}>{r.title}</div>
                </div>
              )
            })}
            {more > 0 && (
              <button type="button" onClick={() => navigate('/?tab=books&reading_status=reading')} className="shrink-0 w-[104px] text-left group">
                <div className="aspect-[2/3] rounded-[8px] bg-secondary grid place-items-center text-[15px] font-semibold text-muted-foreground group-hover:text-foreground transition-colors">
                  +{more}
                </div>
                <div className="mt-[17px] text-[13px] text-muted-foreground"><Trans>See all</Trans></div>
              </button>
            )}
          </HScrollRow>
        </section>
      )}

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
          <Stat
            label={<Trans>This month</Trans>}
            value={hours > 0 ? t`${hours}h` : t`${mins}m`}
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

        {goals.length > 0 && (
          <div className="flex flex-wrap gap-x-8 gap-y-3">
            {goals.map(goal => (
              <button
                key={goal.id}
                type="button"
                onClick={() => navigate('/stats')}
                title={goalSubtext(goal)}
                className="flex items-center gap-3 text-left rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <ProgressRing pct={goal.pct} size={28} stroke={4} />
                <span>
                  <span className="block text-[13px] text-muted-foreground">{goalTitle(goal)}</span>
                  <span className="block text-[15px] font-semibold text-foreground tabular-nums">
                    {Math.round(goal.current)}<span className="text-muted-foreground font-medium">/{goal.target}{goalMeta(goal.kind).unit}</span>
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ── Recently added ───────────────────────────────────────────────── */}
      {recent.length > 0 && (
        <section>
          <SectionTitle action={<SeeAll onClick={onSeeAllRecent} />}><Trans>Recently added</Trans></SectionTitle>
          <HScrollRow className="gap-4 px-2 pt-5 pb-3" wrapClassName="-mx-2 -mt-1" controlsTop="top-[128px]">
            {recent.map(book => (
              <div key={book.id} className="shrink-0 w-[150px]">
                <TiltCover className="relative group/cover">
                  <Link
                    to={`/books/${book.id}`}
                    className="relative block aspect-[2/3] rounded-[12px] overflow-hidden bg-muted"
                    style={{ boxShadow: 'var(--cover-bloom-fan)' }}
                    aria-label={book.title}
                  >
                    <CoverImage src={book.cover_path ? coverSrc(book.id, { w: 400, v: book.updated_at }) : null} alt="" />
                  </Link>
                  <PeekButton onClick={() => setPeek(book.id)} />
                </TiltCover>
                <Link to={`/books/${book.id}`} className="mt-2.5 block text-[15px] text-foreground leading-snug line-clamp-2 hover:text-primary transition-colors">
                  {book.title}
                </Link>
              </div>
            ))}
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
          <Link to={`/books/${spotlight.highlight.book_id}`} className="mt-4 block group max-w-[760px]">
            <blockquote className="border-l-2 border-primary/50 pl-5 text-[17px] leading-relaxed text-foreground line-clamp-5">
              {spotlight.highlight.highlighted_text}
            </blockquote>
            <div className="mt-2.5 pl-5 text-[14px] text-muted-foreground group-hover:text-primary transition-colors">
              {spotlight.highlight.book_author
                ? (() => { const title = spotlight.highlight.book_title; const author = spotlight.highlight.book_author; return <Trans>{title}, {author}</Trans> })()
                : spotlight.highlight.book_title}
            </div>
          </Link>
        </section>
      )}

      {/* ── Recent activity ──────────────────────────────────────────────── */}
      {rows.length > 0 && (
        <section>
          <SectionTitle action={<SeeAll onClick={() => navigate('/stats')} />}><Trans>Recent activity</Trans></SectionTitle>
          <div className="mt-3">
            {rows.map(({ entry, sessions, seconds, pages }, i) => (
              <Link
                key={entry.started_at}
                to={`/books/${entry.book_id}`}
                className={cn('flex items-center gap-4 py-3 group', i > 0 && 'border-t border-border/70')}
              >
                <div className="relative w-11 h-16 rounded-md overflow-hidden bg-muted shrink-0">
                  <CoverImage src={entry.book_cover_path ? coverSrc(entry.book_id, { w: 160 }) : null} alt="" iconClassName="w-4 h-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[16px] font-semibold text-foreground truncate group-hover:text-primary transition-colors">{entry.book_title}</div>
                  <div className="text-[14px] text-muted-foreground mt-0.5">
                    {sessions > 1 && <><Plural value={sessions} one="# session" other="# sessions" />{' · '}</>}
                    {duration(seconds)}
                    {pages > 0 && <>{' · '}<Plural value={pages} one="# page" other="# pages" /></>}
                  </div>
                </div>
                <div className="text-[14px] text-muted-foreground shrink-0">{relativeTime(entry.started_at, i18n.locale)}</div>
              </Link>
            ))}
          </div>
        </section>
      )}

      {peek != null && <QuickLook bookId={peek} onClose={() => setPeek(null)} />}
    </PageBody>
  )
}

/** A cover that tilts toward the pointer, as the Library cards do. */
function TiltCover({ className, degrees, children }: { className?: string; degrees?: number; children: ReactNode }) {
  const { wrapperProps, tiltProps } = useTilt(true, degrees)
  return (
    <div {...wrapperProps} className="shrink-0">
      <div {...tiltProps} className={className}>{children}</div>
    </div>
  )
}
