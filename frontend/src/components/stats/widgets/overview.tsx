// Overview-tab stats widgets — chart-only (no card frame, no fixed height), so the
// same component renders inside a StatsPage ChartCard and inside a resizable Lab tile.
import { useEffect, useState } from 'react'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import {
  ResponsiveContainer,
  ComposedChart,
  BarChart,
  Bar,
  Line,
  Area,
  XAxis,
  YAxis,
  Tooltip,
} from 'recharts'
import { FileText, Trash2, Loader2, ChevronDown, BookOpen, ArrowUpDown, Scissors, TriangleAlert } from 'lucide-react'
import { cn, formatDate, formatDuration } from '@/lib/utils'
import { api } from '@/lib/api'
import { InfoHint } from '@/components/InfoHint'
import { useChartColors } from '@/lib/useChartAccent'
import { ChartTooltip, HeatmapChart, type StatsResponse, type SessionEntry } from '@/components/stats/shared'

// Chart style a configurable widget can render as (per-tile setting in the Lab).
export type ChartKind = 'bar' | 'line' | 'area'

// Bare headline-stat body (value + sub) for use as a standalone dashboard tile —
// the tile itself provides the card frame + the label (in its header).
export function HeadlineStatBody({ value, sub }: { value: string; sub?: string }) {
  // Uniform value size + an always-present sub line keep the figure on the same
  // baseline across every headline tile (mixed sizes / optional subs misalign a
  // row). truncate over wrap so a long value (e.g. "568h 28m") never spills onto
  // a second line and clips inside the fixed-height tile.
  return (
    <div className="flex h-full flex-col justify-center">
      <p className="truncate text-xl font-bold leading-none text-foreground tabular-nums">{value}</p>
      <p className="mt-1 truncate text-[11px] leading-tight text-muted-foreground">{sub ?? ' '}</p>
    </div>
  )
}

export function CurrentlyReading({ books }: { books: StatsResponse['books_in_progress'] }) {
  const { accent } = useChartColors()
  if (books.length === 0) {
    return (
      <div className="flex h-full min-h-[3.5rem] flex-col items-center justify-center gap-1 py-3 text-center">
        <BookOpen className="h-5 w-5 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground"><Trans>No books in progress</Trans></p>
        <p className="text-xs text-muted-foreground/60"><Trans>Start reading one and it'll show up here</Trans></p>
      </div>
    )
  }
  // Compact rows (~40px each): a single book then fits one grid row, so the
  // autoH tile snugs down to it instead of rounding up to a half-empty 2-row
  // box — and still grows by a row for every extra ~2 books (2-up on sm+).
  return (
    <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
      {books.map((b) => (
        <a key={b.book_id} href={`/books/${b.book_id}`} className="group flex items-center gap-2.5 rounded-lg px-2 py-0.5 transition-colors hover:bg-accent/30">
          <div className="h-9 w-6 shrink-0 overflow-hidden rounded bg-muted flex items-center justify-center">
            {b.has_cover ? (
              <img src={`/api/books/${b.book_id}/cover`} alt="" className="h-full w-full object-cover" />
            ) : (
              <FileText className="h-3.5 w-3.5 text-muted-foreground" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-foreground transition-colors group-hover:text-primary">{b.title}</div>
            <div className="mt-0.5 flex items-center gap-2">
              {b.author && <span className="max-w-[45%] shrink-0 truncate text-xs text-muted-foreground">{b.author}</span>}
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full transition-all" style={{ width: `${Math.min(b.progress, 100)}%`, backgroundColor: accent }} />
              </div>
              <span className="shrink-0 text-[10px] text-muted-foreground tabular-nums">{b.progress}%</span>
            </div>
          </div>
        </a>
      ))}
    </div>
  )
}

// One-line duration y-tick — recharts wraps multi-word ticks like "1h 40m".
function DurationYTick({ x, y, payload, fill }: { x?: number; y?: number; payload?: { value?: number }; fill?: string }) {
  return (
    <text x={x} y={y} dy={3} textAnchor="end" fontSize={10} fill={fill}>
      {formatDuration(payload?.value ?? 0)}
    </text>
  )
}

export function ReadingTimePerDay({ daily, chartType = 'bar' }: { daily: StatsResponse['daily']; chartType?: ChartKind }) {
  const { accent, tick, cursor } = useChartColors()
  return (
    <ResponsiveContainer initialDimension={{ width: 1, height: 1 }} width="100%" height="100%">
      <ComposedChart data={daily} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 10, fill: tick }} interval="preserveStartEnd" axisLine={false} tickLine={false} />
        <YAxis tick={<DurationYTick fill={tick} />} width={52} axisLine={false} tickLine={false} />
        <Tooltip
          cursor={cursor}
          wrapperStyle={{ outline: 'none', background: 'none', border: 'none', boxShadow: 'none' }}
          isAnimationActive={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const d = payload[0].payload
            return (
              <ChartTooltip>
                <div className="font-medium">{formatDate(d.date)}</div>
                <div>{formatDuration(d.seconds)}</div>
                <div className="text-muted-foreground">
                  {d.sessions} session{d.sessions !== 1 ? 's' : ''}
                  {d.pages > 0 ? (() => { const pages = d.pages.toLocaleString(); return t` · ${pages} pages` })() : ''}
                </div>
              </ChartTooltip>
            )
          }}
        />
        {chartType === 'bar' && <Bar dataKey="seconds" fill={accent} fillOpacity={0.85} radius={[3, 3, 0, 0]} isAnimationActive={false} />}
        {chartType === 'line' && <Line type="monotone" dataKey="seconds" stroke={accent} strokeWidth={2} dot={false} isAnimationActive={false} />}
        {chartType === 'area' && <Area type="monotone" dataKey="seconds" fill={accent} fillOpacity={0.15} stroke={accent} strokeWidth={2} isAnimationActive={false} />}
      </ComposedChart>
    </ResponsiveContainer>
  )
}

// Single-line, truncated y-axis label — recharts' default tick wraps long book
// titles across several lines (and then drops some), which looks broken.
function TitleTick({ x, y, payload, fill }: { x?: number; y?: number; payload?: { value?: string }; fill?: string }) {
  const t = String(payload?.value ?? '')
  const s = t.length > 20 ? t.slice(0, 19) + '…' : t
  return (
    <text x={x} y={y} dy={4} textAnchor="end" fontSize={10} fill={fill}>
      {s}
    </text>
  )
}

export function TopBooksByTime({ topBooks }: { topBooks: StatsResponse['top_books'] }) {
  const { accent, tick, cursor } = useChartColors()
  if (topBooks.length === 0) {
    return <p className="text-sm text-muted-foreground text-center py-12"><Trans>No reading sessions recorded.</Trans></p>
  }
  return (
    <ResponsiveContainer initialDimension={{ width: 1, height: 1 }} width="100%" height="100%">
      <BarChart data={topBooks} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 8 }}>
        <XAxis type="number" tickFormatter={formatDuration} tick={{ fontSize: 10, fill: tick }} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey="title" width={140} tick={<TitleTick fill={tick} />} axisLine={false} tickLine={false} interval={0} />
        <Tooltip
          cursor={cursor}
          wrapperStyle={{ outline: 'none', background: 'none', border: 'none', boxShadow: 'none' }}
          isAnimationActive={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const d = payload[0].payload
            return (
              <ChartTooltip>
                <div className="font-medium">{d.title}</div>
                <div>{formatDuration(d.seconds)}</div>
                <div className="text-muted-foreground">
                  {d.sessions} session{d.sessions !== 1 ? 's' : ''}
                  {d.sessions > 0 ? (() => { const avg = formatDuration(Math.round(d.seconds / d.sessions)); return t` · avg ${avg}` })() : ''}
                </div>
              </ChartTooltip>
            )
          }}
        />
        <Bar dataKey="seconds" fill={accent} fillOpacity={0.85} radius={[0, 3, 3, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function ReadingActivity365({ heatmap }: { heatmap: StatsResponse['heatmap_daily'] }) {
  const { accent, tick } = useChartColors()
  return (
    <>
      <HeatmapChart data={heatmap} />
      <div className="flex items-center gap-2 justify-end mt-1">
        <span className="text-[10px] text-muted-foreground"><Trans>Less</Trans></span>
        {[0.12, 0.25, 0.45, 0.7, 1].map((op, i) => (
          <div
            key={i}
            className="w-3 h-3 rounded-sm border border-border/30"
            style={i === 0 ? { backgroundColor: tick, opacity: op } : { backgroundColor: accent, opacity: op }}
          />
        ))}
        <span className="text-[10px] text-muted-foreground"><Trans>More</Trans></span>
      </div>
    </>
  )
}

export function BooksFinishedArea({ booksFinished, chartType = 'area' }: { booksFinished: StatsResponse['books_finished']; chartType?: ChartKind }) {
  const { accent, tick, cursor } = useChartColors()
  const sorted = [...booksFinished].sort((a, b) => a.date.localeCompare(b.date))
  const grouped: Record<string, string[]> = {}
  for (const b of sorted) {
    if (!grouped[b.date]) grouped[b.date] = []
    grouped[b.date].push(b.title)
  }
  let count = 0
  const cumulative = Object.entries(grouped)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, titles]) => {
      count += titles.length
      return { date, titles, daily: titles.length, count }
    })

  // Bar mode shows finishes per day; line/area show the cumulative count.
  const dataKey = chartType === 'bar' ? 'daily' : 'count'

  return (
    <ResponsiveContainer initialDimension={{ width: 1, height: 1 }} width="100%" height="100%">
      <ComposedChart data={cumulative} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 10, fill: tick }} interval="preserveStartEnd" axisLine={false} tickLine={false} />
        <YAxis allowDecimals={false} tick={{ fontSize: 10, fill: tick }} width={30} axisLine={false} tickLine={false} />
        <Tooltip
          cursor={cursor}
          wrapperStyle={{ outline: 'none', background: 'none', border: 'none', boxShadow: 'none' }}
          isAnimationActive={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const d = payload[0].payload
            return (
              <ChartTooltip>
                <div className="text-muted-foreground mb-1">{formatDate(d.date)}</div>
                {d.titles.map((t: string) => (
                  <div key={t} className="font-medium">{t}</div>
                ))}
                {(() => { const daily = d.daily; const count = d.count; return <div className="text-muted-foreground mt-1"><Trans>{daily} finished &middot; {count} total</Trans></div> })()}
              </ChartTooltip>
            )
          }}
        />
        {chartType === 'bar' && <Bar dataKey={dataKey} fill={accent} fillOpacity={0.85} radius={[3, 3, 0, 0]} isAnimationActive={false} />}
        {chartType === 'line' && <Line type="monotone" dataKey={dataKey} stroke={accent} strokeWidth={2} dot={false} isAnimationActive={false} />}
        {chartType === 'area' && <Area type="monotone" dataKey={dataKey} fill={accent} fillOpacity={0.15} stroke={accent} strokeWidth={2} isAnimationActive={false} />}
      </ComposedChart>
    </ResponsiveContainer>
  )
}

// Legend for the session log's labels and actions — rendered as an InfoHint
// beside whatever heads the list (the "Sessions (N)" header on BookDetailPage,
// the Recent Sessions tile title on Stats). Kept next to SessionLog so the
// explanation and the rows it explains stay in one file.
export function SessionLogHint() {
  return (
    <InfoHint wide>
      <span className="flex flex-col gap-1.5 text-left font-normal normal-case">
        <span><Trans>Each row is one reading session.</Trans></span>
        <span>
          <Trans><span className="font-medium text-foreground">Device name</span> (e.g. Kindle) — recorded
          live by the KOReader plugin. <span className="font-medium text-foreground">web</span> /{' '}
          <span className="font-medium text-foreground">manual</span> — the web reader, or logged by
          hand.</Trans>
        </span>
        <span>
          <Trans><span className="font-medium text-foreground">imported</span> — from KOReader&apos;s synced
          reading history. Delete only: KOReader already caps idle time, so there is nothing to trim.</Trans>
        </span>
        <span>
          <Trans><span className="font-medium text-foreground">not counted</span> — a live device session
          whose sitting is also described by imported history. Listed for completeness,
          excluded from the totals so nothing is counted twice.</Trans>
        </span>
        <span>
          <Trans>The triangle marks an implausibly long session. Scissors shorten a session, the bin
          deletes it.</Trans>
        </span>
      </span>
    </InfoHint>
  )
}

// Paginated session log — same rows as the Stats page's "Recent Sessions" card,
// without the card frame. Self-contained: fetches, paginates, deletes and trims
// via the API, so it works as a dashboard tile without pushing state upward.
// With `bookId` it becomes the per-book drill-down inside the time table and on
// BookDetailPage (#150); `onChange` lets that host refresh its own aggregates
// after a trim or delete. `day` (a reading-day string from session_timeline)
// narrows it to sittings that started that day — the Activity-bar click-through.
export function SessionLog({ bookId, day, onChange }: { bookId?: number; day?: string; onChange?: () => void } = {}) {
  const [sessions, setSessions] = useState<SessionEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loaded, setLoaded] = useState(0)
  const [loading, setLoading] = useState(false)
  const [deleting, setDeleting] = useState<Set<number | string>>(new Set())
  const [sort, setSort] = useState<'recent' | 'longest'>('recent')
  const [trimId, setTrimId] = useState<number | string | null>(null)
  const [trimMinutes, setTrimMinutes] = useState('')
  const [trimBusy, setTrimBusy] = useState(false)

  const load = (offset: number, replace: boolean) => {
    setLoading(true)
    const bookParam = bookId != null ? `&book_id=${bookId}` : ''
    // Reading-day bucketing needs the client timezone; sent always so `day`
    // filtering and the server's day labels agree.
    const dayParam = day ? `&day=${day}` : ''
    const tzParam = `&tz_offset=${new Date().getTimezoneOffset()}`
    api.get<{ total: number; sessions: SessionEntry[] }>(`/stats/sessions?offset=${offset}&limit=20&sort=${sort}${bookParam}${dayParam}${tzParam}`)
      .then((res) => {
        setSessions((prev) => (replace ? res.sessions : [...prev, ...res.sessions]))
        setTotal(res.total)
        setLoaded(offset + res.sessions.length)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    load(0, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sort, bookId, day])

  const openTrim = (s: SessionEntry) => {
    setTrimId(s.id)
    const preFill = s.suggested_seconds ?? s.duration_seconds ?? 60
    setTrimMinutes(String(Math.max(1, Math.round(preFill / 60))))
  }

  const applyTrim = (s: SessionEntry) => {
    const seconds = Math.round(Number(trimMinutes) * 60)
    if (!Number.isFinite(seconds) || seconds < 60) return
    setTrimBusy(true)
    api.patch(`/stats/sessions/${s.id}`, { duration_seconds: seconds })
      .then(() => {
        setSessions((prev) =>
          prev.map((row) =>
            row.id === s.id
              ? { ...row, duration_seconds: seconds, suspect: false, suggested_seconds: null }
              : row,
          ),
        )
        setTrimId(null)
        onChange?.()
      })
      .catch(() => {})
      .finally(() => setTrimBusy(false))
  }

  const deleteSession = (s: SessionEntry) => {
    setDeleting((prev) => new Set(prev).add(s.id))
    const req =
      s.kind === 'imported'
        ? api.delete(`/stats/imported-sessions?book_id=${s.book_id}&start=${s.range_start}&end=${s.range_end}`)
        : api.delete(`/stats/sessions/${s.id}`)
    req
      .then(() => {
        setSessions((prev) => prev.filter((row) => row.id !== s.id))
        setTotal((prev) => prev - 1)
        setLoaded((prev) => prev - 1)
        onChange?.()
      })
      .catch(() => {})
      .finally(() => setDeleting((prev) => { const n = new Set(prev); n.delete(s.id); return n }))
  }

  if (sessions.length === 0 && !loading) {
    return <p className="text-sm text-muted-foreground text-center py-8"><Trans>No sessions recorded.</Trans></p>
  }
  return (
    <div className="flex flex-col gap-0">
      <div className="flex justify-end pb-1">
        <button
          onClick={() => setSort(sort === 'recent' ? 'longest' : 'recent')}
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
          title={t`Toggle sort order`}
        >
          <ArrowUpDown className="w-3 h-3" />
          {sort === 'recent' ? t`Newest first` : t`Longest first`}
        </button>
      </div>
      <div className="hidden sm:grid grid-cols-[1fr_120px_90px_80px_60px] gap-2 px-2 pb-2 text-[11px] font-medium text-muted-foreground border-b border-border">
        <span><Trans>Book</Trans></span>
        <span><Trans>Date</Trans></span>
        <span><Trans>Duration</Trans></span>
        <span><Trans>Device</Trans></span>
        <span />
      </div>
      {sessions.map((s, idx) => (
        <div key={s.id}>
          <div
            className={cn(
              'grid grid-cols-1 sm:grid-cols-[1fr_120px_90px_80px_60px] gap-1 sm:gap-2 px-2 py-2 items-center hover:bg-accent/30 transition-colors text-xs rounded',
              idx % 2 === 0 ? 'bg-muted/20' : ''
            )}
          >
            <div className="font-medium text-foreground truncate">
              {s.book_id ? (
                <a href={`/books/${s.book_id}`} className="hover:text-primary transition-colors">{s.book_title}</a>
              ) : (
                <span className="text-muted-foreground">{s.book_title}</span>
              )}
            </div>
            <div className="text-muted-foreground">
              {s.started_at ? new Date(s.started_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '--'}
            </div>
            <div className="text-muted-foreground inline-flex items-center gap-1">
              {s.duration_seconds != null ? formatDuration(s.duration_seconds) : '--'}
              {s.suspect && (
                <TriangleAlert
                  className="w-3 h-3 text-warning shrink-0"
                  aria-label={t`Unusually long session`}
                />
              )}
            </div>
            <div
              className="text-muted-foreground truncate"
              title={
                s.kind === 'imported'
                  ? 'Imported from KOReader reading history'
                  : !s.counted
                    ? 'Not in totals — this book’s imported KOReader history already covers this reading'
                    : undefined
              }
            >
              {s.device || '--'}
              {s.kind === 'imported' ? (
                <span className="block text-[10px] text-muted-foreground/70">imported</span>
              ) : !s.counted ? (
                <span className="block text-[10px] text-muted-foreground/70"><Trans>not counted</Trans></span>
              ) : null}
            </div>
            <div className="flex justify-end gap-0.5">
              {s.kind !== 'imported' && (
                <button
                  onClick={() => (trimId === s.id ? setTrimId(null) : openTrim(s))}
                  className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                  title={t`Trim session`}
                >
                  <Scissors className="w-3.5 h-3.5" />
                </button>
              )}
              <button
                onClick={() => deleteSession(s)}
                disabled={deleting.has(s.id)}
                className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors disabled:opacity-40"
                title={s.kind === 'imported' ? t`Delete this imported sitting` : t`Delete session`}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
          {trimId === s.id && (
            <div className="flex flex-wrap items-center gap-2 rounded bg-muted/40 px-2 py-2 text-xs">
              <span className="text-muted-foreground"><Trans>New duration</Trans></span>
              <InfoHint text={t`Trimming only shortens this session's length and end time — page turns, progress and every other session stay untouched, and the previous duration is kept in the audit log. The suggestion re-prices this session's page turns at your usual reading pace.`} />
              <input
                type="number"
                min={1}
                value={trimMinutes}
                onChange={(e) => setTrimMinutes(e.target.value)}
                className="w-16 rounded border border-border bg-background px-1.5 py-0.5 text-right tabular-nums"
              />
              <span className="text-muted-foreground"><Trans>min</Trans></span>
              {s.suggested_seconds != null && (
                <button
                  onClick={() => setTrimMinutes(String(Math.max(1, Math.round(s.suggested_seconds! / 60))))}
                  className="text-primary hover:text-primary/80 transition-colors"
                  title={t`Your page turns at your usual reading pace`}
                >
                  {(() => { const dur = formatDuration(s.suggested_seconds); return t`Suggested: ${dur}` })()}
                </button>
              )}
              <div className="ml-auto flex gap-1.5">
                <button
                  onClick={() => applyTrim(s)}
                  disabled={trimBusy}
                  className="rounded bg-primary px-2 py-0.5 font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
                >
                  <Trans>Trim</Trans>
                </button>
                <button
                  onClick={() => setTrimId(null)}
                  className="rounded border border-border px-2 py-0.5 text-muted-foreground hover:text-foreground transition-colors"
                >
                  <Trans>Cancel</Trans>
                </button>
              </div>
            </div>
          )}
        </div>
      ))}
      {loaded < total && (
        <button
          onClick={() => load(loaded, false)}
          disabled={loading}
          className="flex items-center justify-center gap-1.5 py-3 text-xs text-primary hover:text-primary/80 transition-colors disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <>
              <ChevronDown className="w-3.5 h-3.5" />
              {(() => { const left = total - loaded; return <span><Trans>Show more ({left} remaining)</Trans></span> })()}
            </>
          )}
        </button>
      )}
    </div>
  )
}

// Latest finishes, newest first, with covers — list tile (scrolls in the Lab).
export function RecentlyFinished({ booksFinished }: { booksFinished: StatsResponse['books_finished'] }) {
  const rows = [...booksFinished].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 12)
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground text-center py-8"><Trans>Nothing finished in this range.</Trans></p>
  }
  return (
    <div className="flex flex-col gap-1">
      {rows.map((b, i) => (
        <a key={`${b.book_id}-${i}`} href={`/books/${b.book_id}`} className="group flex items-center gap-3 rounded-lg p-1.5 hover:bg-accent/30 transition-colors">
          <div className="w-7 h-10 rounded bg-muted flex items-center justify-center shrink-0 overflow-hidden">
            <img
              src={`/api/books/${b.book_id}/cover`}
              alt=""
              className="w-full h-full object-cover"
              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          </div>
          <span className="flex-1 min-w-0 truncate text-sm font-medium text-foreground group-hover:text-primary transition-colors">{b.title}</span>
          <span className="shrink-0 text-xs text-muted-foreground">{formatDate(b.date)}</span>
        </a>
      ))}
    </div>
  )
}

// Current month at a glance — days with reading filled, today outlined.
// Uses the 365-day heatmap data, so it ignores the page range (always current).
export function StreakCalendar({ heatmap }: { heatmap: StatsResponse['heatmap_daily'] }) {
  const { accent } = useChartColors()
  const read = new Map(heatmap.map((d) => [d.date, d.seconds]))
  const now = new Date()
  const y = now.getFullYear()
  const m = now.getMonth()
  const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const todayIso = iso(now)
  const firstDow = (new Date(y, m, 1).getDay() + 6) % 7 // Mon = 0
  const daysInMonth = new Date(y, m + 1, 0).getDate()
  const cells: (Date | null)[] = [...Array(firstDow).fill(null), ...Array.from({ length: daysInMonth }, (_, i) => new Date(y, m, i + 1))]
  const readDays = cells.filter((d) => d && (read.get(iso(d)) ?? 0) > 0).length

  return (
    <div className="flex h-full flex-col">
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-xs font-semibold text-foreground">{now.toLocaleDateString(undefined, { month: 'long' })}</span>
        <span className="text-[10px] text-muted-foreground">{readDays} day{readDays !== 1 ? 's' : ''} read</span>
      </div>
      <div className="grid flex-1 grid-cols-7 gap-1 content-start">
        {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((w, i) => (
          <div key={i} className="text-center text-[9px] text-muted-foreground">{w}</div>
        ))}
        {cells.map((d, i) => {
          if (!d) return <div key={i} />
          const dIso = iso(d)
          const secs = read.get(dIso) ?? 0
          const isToday = dIso === todayIso
          const future = dIso > todayIso
          return (
            <div
              key={i}
              title={`${formatDate(dIso)}${secs > 0 ? ` — ${formatDuration(secs)}` : ''}`}
              className={cn(
                'flex aspect-square items-center justify-center rounded text-[9px] tabular-nums',
                secs > 0 ? 'font-semibold text-primary-foreground' : future ? 'text-muted-foreground/40' : 'bg-muted/40 text-muted-foreground',
                isToday && 'ring-1 ring-primary',
              )}
              style={secs > 0 ? { backgroundColor: accent, opacity: Math.min(0.45 + secs / 7200, 1) } : undefined}
            >
              {d.getDate()}
            </div>
          )
        })}
      </div>
    </div>
  )
}
