// Shared stats types + chrome, used by both the Stats page and the Stats Lab dashboard.
// Extracted from StatsPage so the two render from a single source of truth (no drift).
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { formatDate, formatDuration } from '@/lib/utils'
import { useChartColors } from '@/lib/useChartAccent'
import { inWeekOrder, rowOfJsDay } from '@/lib/weekStart'

// ── Types ─────────────────────────────────────────────────────────────────────

// Daily + weekly streaks with their dates (ISO days; week runs are Monday-based).
export interface StreakSummary {
  current_days: number
  current_start: string | null
  longest_days: number
  longest_start: string | null
  longest_end: string | null
  current_weeks: number
  current_weeks_start: string | null
  longest_weeks: number
  longest_weeks_start: string | null
  longest_weeks_end: string | null
}

export interface StatsResponse {
  range_days: number
  headline: {
    total_reading_seconds: number
    total_sessions: number
    books_finished: number
    avg_session_seconds: number
    current_streak_days: number
    longest_streak_days: number
    pages_turned: number
    // "All" range only: the part of time, sessions and pages a KOSync reader
    // counted before it first synced to Tome.
    reader_earlier?: { seconds: number; pages: number; sessions: number; days: number } | null
    streaks: StreakSummary
  }
  daily: { date: string; seconds: number; sessions: number; pages: number }[]
  heatmap_daily: { date: string; seconds: number; sessions: number; pages: number }[]
  books_finished: { date: string; book_id: number; title: string }[]
  top_books: { book_id: number; title: string; seconds: number; sessions: number }[]
  by_category: { category: string; seconds: number; sessions: number; book_count: number }[]
  reading_pace: { session_id: number; title: string; date: string | null; pages_per_min: number; duration_seconds: number; pages_turned: number }[]
  books_in_progress: { book_id: number; title: string; author: string | null; has_cover: boolean; progress: number; last_read: string | null }[]
  // id is a string ("ps-<book>-<start>") for imported KOReader sittings.
  session_timeline: { id: number | string; title: string; started_at: string; ended_at: string; duration_seconds: number }[]
  year_summary?: {
    books_finished: number
    total_hours: number
    top_genre: string | null
    longest_streak_days: number
    total_sessions: number
    most_active_month: string | null
  } | null
  period_comparison?: {
    current_seconds: number
    previous_seconds: number
    pct_change: number | null
    // Headline counts of the window just before the range (older servers omit them).
    previous_sessions?: number
    previous_pages?: number
    previous_books_finished?: number
  } | null
  per_book_time: { book_id: number; title: string; author: string | null; has_cover: boolean; seconds: number; sessions: number; pages_turned: number }[]
  monthly_comparison: { month: string; label: string; books_finished: number; reading_hours: number; sessions: number; reading_seconds: number }[]
  genre_over_time: { month: string; [category: string]: number | string }[]
  hour_dow_heatmap: { dow: number; hour: number; seconds: number; sessions: number }[]
  // next_index: highest volume read or being read, plus one; next_book_id: the library's book holding it (null: not in the library).
  series_completion: { series: string; total: number; read: number; reading: number; pct: number; sample_book_id: number; next_index?: number | null; next_book_id?: number | null }[]
  author_affinity: { author: string; seconds: number; sessions: number; book_count: number; books_finished: number }[]
  completion_rate: { started: number; finished: number; pct: number }
  completion_by_type: { category: string; started: number; finished: number; pct: number }[]
  pace_by_format: { format: string; pages_per_min: number; sessions: number; pages: number; seconds: number }[]
  library_growth: { month: string; total: number; [category: string]: number | string }[]
  ratings: {
    count: number
    avg: number
    distribution: { rating: number; count: number }[]
    by_category: { category: string; avg: number; count: number }[]
    books: { book_id: number; title: string; author: string | null; has_cover: boolean; category: string; rating: number; seconds: number; rated_at: string | null }[]
    series: { series: string; rating: number; sample_book_id: number | null; book_count?: number }[]
    trend: { date: string; rating: number }[]
  }
  lifetime: {
    seconds: number; sessions: number; pages: number; books_finished: number; active_days: number; longest_streak_days: number
    // The part of the figures above a KOSync reader counted before it first synced to Tome.
    reader_earlier?: { seconds: number; pages: number; sessions: number; days: number } | null
  }
  records: { longest_session_seconds: number; longest_session_title: string | null; biggest_day_seconds: number; biggest_day_date: string | null; most_pages_day: number; most_pages_date: string | null }
  tbr: { owned: number; read: number; reading: number; shelved: number; want_to_read: number; unread: number; pct: number; by_type: { type: string; owned: number; read: number; pct: number }[] }
  language: { language: string; code: string; seconds: number; books: number }[]
  words: {
    total_words: number
    books_counted: number
    by_year: { year: number; words: number }[]
  }
  wpm: {
    overall: number
    books_counted: number
    books: { book_id: number; title: string; author: string | null; has_cover: boolean; words: number; seconds: number; wpm: number }[]
  }
  book_lengths: {
    count: number
    avg_words: number
    median_words: number
    longest: { book_id: number; title: string; has_cover: boolean; words: number; finished: string | null } | null
    buckets: { label: string; count: number }[]
    by_year: { year: number; avg_words: number; count: number }[]
    books: { book_id: number; title: string; has_cover: boolean; words: number; finished: string | null }[]
  }
  rereads: {
    books: { book_id: number; title: string; author: string | null; has_cover: boolean; reread_pages: number; total_pages: number; pct: number }[]
  }
  reading_dna?: ReadingDNA
}

export interface ReadingDNATrait {
  key: string
  score: number   // 0 = low pole, 100 = high pole
  low: string
  high: string
}

export interface ReadingDNA {
  ready: boolean
  archetype: string | null
  summary: string | null
  traits: ReadingDNATrait[]
}

export interface CompletionEstimate {
  book_id: number
  title: string
  author: string | null
  has_cover: boolean
  progress: number
  estimated_days: number | null
  confidence: 'high' | 'medium' | 'low'
}

export interface SessionEntry {
  // Recorded sessions have a numeric DB id; imported KOReader history clusters
  // get a synthetic string id ("ps-<book>-<start>").
  id: number | string
  kind: 'session' | 'imported'
  // False for a device live session whose sitting imported KOReader history
  // also describes (page-stats overlap it in time), so this row is listed
  // (labeled "not counted") but excluded from totals.
  counted: boolean
  book_id: number | null
  book_title: string
  started_at: string | null
  ended_at: string | null
  duration_seconds: number | null
  pages_turned: number | null
  // True when pages_turned was attributed from a CrossInk reader's page
  // counter (its growth between two syncs, split by reading time).
  pages_attributed?: boolean
  device: string | null
  progress_start: number | null
  progress_end: number | null
  // Runaway-session hygiene (#150): implausibly long for one sitting, and the
  // plausible real duration (page turns at the user's median pace) if computable.
  suspect: boolean
  suggested_seconds: number | null
  // Imported clusters only: the epoch range for DELETE /stats/imported-sessions.
  range_start?: number
  range_end?: number
}

// ── Chrome ────────────────────────────────────────────────────────────────────

export function ChartCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="bg-card rounded-[var(--radius-panel)] p-5 flex flex-col gap-3">
      <h3 className="text-[12px] font-bold text-muted-foreground uppercase tracking-[0.1em]">{title}</h3>
      {children}
    </div>
  )
}

export function ChartTooltip({ children }: { children: ReactNode }) {
  return (
    <div className="bg-card rounded-lg shadow-lg ring-1 ring-panel-ring px-3 py-2 text-[13px]">{children}</div>
  )
}

// ── 365-day activity heatmap ───────────────────────────────────────────────────

export function HeatmapChart({ data }: { data: { date: string; seconds: number }[] }) {
  const { accent, tick } = useChartColors()
  const [tooltip, setTooltip] = useState<{ x: number; y: number; date: string; seconds: number } | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollLeft = scrollRef.current.scrollWidth
  }, [])

  const map = new Map(data.map((d) => [d.date, d.seconds]))

  // Local-date keys, NOT toISOString (= UTC): the backend buckets sessions into
  // local days via tz_offset, so UTC keys shift evening reads onto the wrong day
  // for anyone east of Greenwich.
  const localIso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const days: string[] = []
  let firstDow = 0
  for (let i = 364; i >= 0; i--) {
    const d = new Date()
    d.setDate(d.getDate() - i)
    if (i === 364) firstDow = d.getDay()
    days.push(localIso(d))
  }

  const padBefore = rowOfJsDay(firstDow)

  const weeks: (string | null)[][] = []
  let week: (string | null)[] = Array(padBefore).fill(null)
  for (const day of days) {
    week.push(day)
    if (week.length === 7) {
      weeks.push(week)
      week = []
    }
  }
  if (week.length > 0) {
    while (week.length < 7) week.push(null)
    weeks.push(week)
  }

  const CELL = 12
  const GAP = 3
  function getOpacity(secs: number) {
    if (secs === 0) return 0.12
    if (secs < 900) return 0.25
    if (secs < 1800) return 0.45
    if (secs < 3600) return 0.7
    return 1
  }

  const monthLabels: { x: number; label: string }[] = []
  let lastMonth = ''
  weeks.forEach((wk, wi) => {
    const firstDay = wk.find((d) => d !== null)
    if (firstDay) {
      const m = new Date(firstDay + 'T00:00:00').toLocaleDateString(undefined, { month: 'short' })
      if (m !== lastMonth) {
        monthLabels.push({ x: 30 + wi * (CELL + GAP), label: m })
        lastMonth = m
      }
    }
  })

  // + 16: room for the last month label, which starts on the final column.
  const svgW = 30 + weeks.length * (CELL + GAP) + 16
  const svgH = 20 + 7 * (CELL + GAP)

  return (
    <div ref={scrollRef} className="relative overflow-x-auto">
      <svg width={svgW} height={svgH} style={{ display: 'block', minWidth: svgW }}>
        {monthLabels.map(({ x, label }) => (
          <text key={label + x} x={x} y={10} fontSize={9} fill={tick}>{label}</text>
        ))}
        {inWeekOrder(['M', '', 'W', '', 'F', '', '']).map((d, i) =>
          d ? <text key={i} x={0} y={20 + i * (CELL + GAP) + CELL - 1} fontSize={9} fill={tick}>{d}</text> : null,
        )}
        {weeks.map((wk, wi) =>
          wk.map((day, di) => {
            if (!day) return null
            const secs = map.get(day) ?? 0
            return (
              <rect
                key={day}
                x={30 + wi * (CELL + GAP)}
                y={20 + di * (CELL + GAP)}
                width={CELL}
                height={CELL}
                rx={2}
                fill={secs === 0 ? tick : accent}
                fillOpacity={getOpacity(secs)}
                onMouseEnter={(e) => setTooltip({ x: e.clientX, y: e.clientY, date: day, seconds: secs })}
                onMouseLeave={() => setTooltip(null)}
                style={{ cursor: 'default' }}
              />
            )
          }),
        )}
      </svg>
      {tooltip &&
        createPortal(
          // Portaled to <body>: inside a dashboard tile an ancestor transform would
          // re-anchor position:fixed to the tile and trap z-index in its stacking
          // context, putting the tooltip far from the cursor and behind siblings.
          <div
            className="fixed z-50 pointer-events-none bg-card rounded-lg shadow-lg ring-1 ring-panel-ring px-3 py-2 text-[13px]"
            style={{ left: Math.min(tooltip.x + 12, window.innerWidth - 180), top: tooltip.y - 44 }}
          >
            <div className="font-medium">{formatDate(tooltip.date)}</div>
            <div className="text-muted-foreground">{formatDuration(tooltip.seconds)}</div>
          </div>,
          document.body,
        )}
    </div>
  )
}

// ── Designed-page endpoints ────────────────────────────────────────────────────

/** GET /api/stats/habits?days|start&end&tz_offset: what Habits needs beyond /stats. */
export interface HabitsResponse {
  /** Counted sittings in the range; equals headline.total_sessions for the same range. */
  sessions: number
  session_lengths: { bucket: 'under_15m' | '15_to_30m' | '30_to_60m' | '1_to_2h' | 'over_2h'; count: number }[]
  longest_session: { seconds: number; book_id: number | null; title: string | null; started_at: string } | null
  /** The last 14 reading days (4 h rollover), oldest first, with each day's sittings (UTC ISO starts). */
  last_14_days: { date: string; seconds: number; sittings: { started_at: string; seconds: number; book_id: number | null; title: string | null }[] }[]
}

/** GET /api/stats/library?tz_offset: all time. */
export interface LibraryResponse {
  finished: number
  published: { years: { year: number; count: number }[]; no_year: number }
  time_to_finish: {
    books: number
    median_days: number | null
    buckets: { bucket: '1_day' | '2_to_3' | '4_to_7' | '1_to_2_weeks' | 'longer'; count: number }[]
    quickest: { days: number; count: number } | null
    slowest: { days: number; book_id: number; title: string } | null
  }
}

/** GET /api/stats/highlights. */
export interface HighlightsResponse {
  count: number
  books: number
  top: { book_id: number; title: string; count: number }[]
}

/** GET /api/stats/sessions with `counted` and `day_totals`. */
export interface SessionsPage {
  total: number
  counts: { all: number; counted: number; not_counted: number }
  sessions: SessionEntry[]
  /** Local calendar day (YYYY-MM-DD) -> seconds of every counted row that started that day. */
  day_totals?: Record<string, number>
}
