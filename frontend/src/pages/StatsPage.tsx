import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Clock, Activity, BookCheck, Flame, FileText,
  BarChart3, ArrowLeft, Loader2, Trash2, ChevronDown, ChevronUp,
  TrendingUp, TrendingDown, Minus, Trophy, Calendar, Zap,
  ArrowUpDown,
} from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
  AreaChart, Area,
} from 'recharts'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { StatCard } from '@/components/stats/StatCard'
import { CompletionRateCard } from '@/components/stats/CompletionRateCard'
import { HourDowHeatmap } from '@/components/stats/HourDowHeatmap'
import { PaceByFormat } from '@/components/stats/PaceByFormat'
import { SeriesCompletionGrid } from '@/components/stats/SeriesCompletionGrid'
import { AuthorAffinity } from '@/components/stats/AuthorAffinity'
import { CompletionByType } from '@/components/stats/CompletionByType'
import { LibraryGrowthChart } from '@/components/stats/LibraryGrowthChart'

// ── Types ─────────────────────────────────────────────────────────────────────

interface StatsResponse {
  range_days: number
  headline: {
    total_reading_seconds: number
    total_sessions: number
    books_finished: number
    avg_session_seconds: number
    current_streak_days: number
    longest_streak_days: number
    pages_turned: number
  }
  daily: { date: string; seconds: number; sessions: number; pages: number }[]
  heatmap_daily: { date: string; seconds: number; sessions: number; pages: number }[]
  books_finished: { date: string; book_id: number; title: string }[]
  top_books: { book_id: number; title: string; seconds: number; sessions: number }[]
  by_category: { category: string; seconds: number; sessions: number; book_count: number }[]
  reading_pace: { session_id: number; title: string; date: string | null; pages_per_min: number; duration_seconds: number; pages_turned: number }[]
  books_in_progress: { book_id: number; title: string; author: string | null; has_cover: boolean; progress: number; last_read: string | null }[]
  session_timeline: { id: number; title: string; started_at: string; ended_at: string; duration_seconds: number }[]
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
  } | null
  per_book_time: { book_id: number; title: string; author: string | null; has_cover: boolean; seconds: number; sessions: number; pages_turned: number }[]
  monthly_comparison: { month: string; label: string; books_finished: number; reading_hours: number; sessions: number; reading_seconds: number }[]
  genre_over_time: { month: string; [category: string]: number | string }[]
  hour_dow_heatmap: { dow: number; hour: number; seconds: number; sessions: number }[]
  series_completion: { series: string; total: number; read: number; reading: number; pct: number; sample_book_id: number }[]
  author_affinity: { author: string; seconds: number; sessions: number; book_count: number; books_finished: number }[]
  completion_rate: { started: number; finished: number; pct: number }
  completion_by_type: { category: string; started: number; finished: number; pct: number }[]
  pace_by_format: { format: string; pages_per_min: number; sessions: number; pages: number; seconds: number }[]
  library_growth: { month: string; total: number; [category: string]: number | string }[]
}

interface CompletionEstimate {
  book_id: number
  title: string
  author: string | null
  has_cover: boolean
  progress: number
  estimated_days: number | null
  confidence: 'high' | 'medium' | 'low'
}

interface SessionEntry {
  id: number
  book_id: number | null
  book_title: string
  started_at: string | null
  ended_at: string | null
  duration_seconds: number | null
  pages_turned: number | null
  device: string | null
  progress_start: number | null
  progress_end: number | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDuration(seconds: number): string {
  if (seconds === 0) return '0m'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h === 0) return `${m}m`
  return `${h}h ${m}m`
}

function formatDate(iso: string): string {
  return new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

// ── Constants ─────────────────────────────────────────────────────────────────

const RANGES = [
  { days: 7, label: '7d' },
  { days: 30, label: '30d' },
  { days: 90, label: '90d' },
  { days: 365, label: '1y' },
  { days: 0, label: 'All' },
]

const PIE_COLORS = ['#6366f1', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#3b82f6', '#ef4444', '#14b8a6']

// ── Sub-components ────────────────────────────────────────────────────────────

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-card border border-border rounded-xl p-4 flex flex-col gap-3">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">{title}</h3>
      {children}
    </div>
  )
}

// ── Heatmap ───────────────────────────────────────────────────────────────────

function HeatmapChart({ data }: { data: { date: string; seconds: number }[] }) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; date: string; seconds: number } | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollLeft = scrollRef.current.scrollWidth
  }, [])

  const map = new Map(data.map(d => [d.date, d.seconds]))

  const days: string[] = []
  for (let i = 364; i >= 0; i--) {
    const d = new Date()
    d.setDate(d.getDate() - i)
    days.push(d.toISOString().slice(0, 10))
  }

  const firstDow = new Date(days[0]).getDay()
  const padBefore = firstDow === 0 ? 6 : firstDow - 1

  const weeks: (string | null)[][] = []
  let week: (string | null)[] = Array(padBefore).fill(null)
  for (const day of days) {
    week.push(day)
    if (week.length === 7) { weeks.push(week); week = [] }
  }
  if (week.length > 0) {
    while (week.length < 7) week.push(null)
    weeks.push(week)
  }

  const CELL = 12
  const GAP = 3

  const FILL_COLORS = [
    'rgba(148, 163, 184, 0.1)',
    'rgba(99, 102, 241, 0.25)',
    'rgba(99, 102, 241, 0.45)',
    'rgba(99, 102, 241, 0.7)',
    '#6366f1',
  ]
  function getColor(secs: number) {
    if (secs === 0) return FILL_COLORS[0]
    if (secs < 900) return FILL_COLORS[1]
    if (secs < 1800) return FILL_COLORS[2]
    if (secs < 3600) return FILL_COLORS[3]
    return FILL_COLORS[4]
  }

  const monthLabels: { x: number; label: string }[] = []
  let lastMonth = ''
  weeks.forEach((week, wi) => {
    const firstDay = week.find(d => d !== null)
    if (firstDay) {
      const m = new Date(firstDay + 'T00:00:00').toLocaleDateString(undefined, { month: 'short' })
      if (m !== lastMonth) {
        monthLabels.push({ x: 30 + wi * (CELL + GAP), label: m })
        lastMonth = m
      }
    }
  })

  const svgW = 30 + weeks.length * (CELL + GAP)
  const svgH = 20 + 7 * (CELL + GAP)

  return (
    <div ref={scrollRef} className="relative overflow-x-auto">
      <svg width={svgW} height={svgH} className="mx-auto" style={{ display: 'block', minWidth: svgW }}>
        {monthLabels.map(({ x, label }) => (
          <text key={label + x} x={x} y={10} fontSize={9} fill="#94a3b8">{label}</text>
        ))}
        {['M', '', 'W', '', 'F', '', ''].map((d, i) =>
          d ? <text key={i} x={0} y={20 + i * (CELL + GAP) + CELL - 1} fontSize={9} fill="#94a3b8">{d}</text> : null
        )}
        {weeks.map((week, wi) =>
          week.map((day, di) => {
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
                fill={getColor(secs)}
                onMouseEnter={e => setTooltip({ x: e.clientX, y: e.clientY, date: day, seconds: secs })}
                onMouseLeave={() => setTooltip(null)}
                style={{ cursor: 'default' }}
              />
            )
          })
        )}
      </svg>
      {tooltip && (
        <div
          className="fixed z-50 pointer-events-none bg-card border border-border rounded-lg shadow-xl px-3 py-2 text-xs"
          style={{ left: Math.min(tooltip.x + 12, window.innerWidth - 180), top: tooltip.y - 44 }}
        >
          <div className="font-medium">{formatDate(tooltip.date)}</div>
          <div className="text-muted-foreground">{formatDuration(tooltip.seconds)}</div>
        </div>
      )}
    </div>
  )
}

// ── Custom Tooltip ────────────────────────────────────────────────────────────

function ChartTooltip({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card border border-border rounded-lg shadow-xl px-3 py-2 text-xs">
      {children}
    </div>
  )
}

// ── Per-Book Time Table ──────────────────────────────────────────────────

type BookTimeSortKey = 'seconds' | 'sessions' | 'pages_turned'

function PerBookTimeTable({ data }: { data: StatsResponse['per_book_time'] }) {
  const [sortKey, setSortKey] = useState<BookTimeSortKey>('seconds')
  const [sortAsc, setSortAsc] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const handleSort = (key: BookTimeSortKey) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc)
    } else {
      setSortKey(key)
      setSortAsc(false)
    }
  }

  const sorted = [...data].sort((a, b) => {
    const diff = a[sortKey] - b[sortKey]
    return sortAsc ? diff : -diff
  })

  const visible = expanded ? sorted : sorted.slice(0, 10)
  const hasMore = sorted.length > 10

  const SortIcon = ({ col }: { col: BookTimeSortKey }) => {
    if (sortKey !== col) return <ArrowUpDown className="w-3 h-3 opacity-30" />
    return sortAsc ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />
  }

  return (
    <div className="flex flex-col gap-4">
      <ChartCard title="All Books by Reading Time">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">
                <th className="text-left py-2 px-2 w-10" />
                <th className="text-left py-2 px-2">Title</th>
                <th className="text-left py-2 px-2 hidden sm:table-cell">Author</th>
                <th className="text-right py-2 px-2 cursor-pointer select-none whitespace-nowrap" onClick={() => handleSort('seconds')}>
                  <span className="inline-flex items-center gap-1">Time <SortIcon col="seconds" /></span>
                </th>
                <th className="text-right py-2 px-2 cursor-pointer select-none whitespace-nowrap" onClick={() => handleSort('sessions')}>
                  <span className="inline-flex items-center gap-1">Sessions <SortIcon col="sessions" /></span>
                </th>
                <th className="text-right py-2 px-2 cursor-pointer select-none whitespace-nowrap" onClick={() => handleSort('pages_turned')}>
                  <span className="inline-flex items-center gap-1">Pages <SortIcon col="pages_turned" /></span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((b, idx) => (
                <tr
                  key={b.book_id}
                  className={cn(
                    'hover:bg-accent/30 transition-colors',
                    idx % 2 === 0 ? 'bg-muted/20' : ''
                  )}
                >
                  <td className="py-1.5 px-2">
                    <div className="w-8 h-12 rounded bg-muted flex items-center justify-center shrink-0 overflow-hidden">
                      {b.has_cover ? (
                        <img src={`/api/books/${b.book_id}/cover`} alt="" className="w-full h-full object-cover" loading="lazy" />
                      ) : (
                        <FileText className="w-3.5 h-3.5 text-muted-foreground" />
                      )}
                    </div>
                  </td>
                  <td className="py-1.5 px-2">
                    <a href={`/books/${b.book_id}`} className="font-medium text-foreground hover:text-primary transition-colors line-clamp-1">
                      {b.title}
                    </a>
                  </td>
                  <td className="py-1.5 px-2 text-muted-foreground hidden sm:table-cell truncate max-w-[160px]">
                    {b.author || '--'}
                  </td>
                  <td className="py-1.5 px-2 text-right text-muted-foreground tabular-nums">{formatDuration(b.seconds)}</td>
                  <td className="py-1.5 px-2 text-right text-muted-foreground tabular-nums">{b.sessions}</td>
                  <td className="py-1.5 px-2 text-right text-muted-foreground tabular-nums">{b.pages_turned}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {hasMore && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center justify-center gap-1.5 py-2 text-xs text-primary hover:text-primary/80 transition-colors"
          >
            <ChevronDown className={cn('w-3.5 h-3.5 transition-transform', expanded && 'rotate-180')} />
            <span>{expanded ? 'Show less' : `Show all (${sorted.length} books)`}</span>
          </button>
        )}
      </ChartCard>
    </div>
  )
}

// ── Genre Over Time Chart ────────────────────────────────────────────────

const GENRE_COLORS = ['#6366f1', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#3b82f6', '#ef4444', '#14b8a6', '#f97316', '#a855f7']

function GenreOverTimeChart({ data }: { data: StatsResponse['genre_over_time'] }) {
  const categories = Array.from(
    new Set(data.flatMap(d => Object.keys(d).filter(k => k !== 'month')))
  ).sort()

  if (categories.length === 0) return null

  const chartData = data.map(d => {
    const entry: Record<string, number | string> = { month: d.month as string }
    for (const cat of categories) {
      entry[cat] = Math.round(((d[cat] as number) || 0) / 60)
    }
    return entry
  })

  const formatMonth = (m: string) => {
    try {
      const d = new Date(m + '-01T00:00:00')
      return d.toLocaleDateString(undefined, { month: 'short' })
    } catch {
      return m
    }
  }

  return (
    <ChartCard title="Reading by Category — Last 12 Months">
      <ResponsiveContainer width="100%" height={280}>
        <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <XAxis
            dataKey="month"
            tickFormatter={formatMonth}
            tick={{ fontSize: 10, fill: '#94a3b8' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 10, fill: '#94a3b8' }}
            width={36}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => v >= 60 ? `${Math.round(v / 60)}h` : `${v}m`}
          />
          <Tooltip
            cursor={{ fill: 'rgba(255,255,255,0.04)' }}
            wrapperStyle={{ outline: 'none', background: 'none', border: 'none', boxShadow: 'none' }}
            isAnimationActive={false}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const month = payload[0]?.payload?.month
              return (
                <ChartTooltip>
                  <div className="font-medium mb-1">{formatMonth(month)}</div>
                  {payload
                    .filter(p => (p.value as number) > 0)
                    .sort((a, b) => (b.value as number) - (a.value as number))
                    .map(p => (
                      <div key={p.dataKey as string} className="flex items-center gap-1.5">
                        <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: p.color }} />
                        <span>{p.dataKey as string}: {(p.value as number) >= 60 ? `${Math.round((p.value as number) / 60)}h ${(p.value as number) % 60}m` : `${p.value}m`}</span>
                      </div>
                    ))}
                </ChartTooltip>
              )
            }}
          />
          <Legend formatter={v => <span style={{ fontSize: 11 }}>{v}</span>} wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
          {categories.map((cat, i) => (
            <Area
              key={cat}
              type="monotone"
              dataKey={cat}
              stackId="1"
              fill={GENRE_COLORS[i % GENRE_COLORS.length]}
              fillOpacity={0.6}
              stroke={GENRE_COLORS[i % GENRE_COLORS.length]}
              strokeWidth={1.5}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export function StatsPage() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<StatsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [statsTab, setStatsTab] = useState<'overview' | 'habits' | 'library'>('overview')

  const [estimates, setEstimates] = useState<CompletionEstimate[] | null>(null)
  const [estimatesLoading, setEstimatesLoading] = useState(false)

  const [sessions, setSessions] = useState<SessionEntry[]>([])
  const [sessionsTotal, setSessionsTotal] = useState(0)
  const [sessionsLoaded, setSessionsLoaded] = useState(0)
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [deleting, setDeleting] = useState<Set<number>>(new Set())

  const loadSessions = (offset: number, replace: boolean) => {
    setSessionsLoading(true)
    api.get<{ total: number; sessions: SessionEntry[] }>(`/stats/sessions?offset=${offset}&limit=20`)
      .then(res => {
        setSessions(prev => replace ? res.sessions : [...prev, ...res.sessions])
        setSessionsTotal(res.total)
        setSessionsLoaded(offset + res.sessions.length)
      })
      .catch(() => {})
      .finally(() => setSessionsLoading(false))
  }

  const deleteSession = (id: number) => {
    setDeleting(prev => new Set(prev).add(id))
    api.delete(`/stats/sessions/${id}`)
      .then(() => {
        setSessions(prev => prev.filter(s => s.id !== id))
        setSessionsTotal(prev => prev - 1)
        setSessionsLoaded(prev => prev - 1)
        api.get<StatsResponse>(`/stats?days=${days}&tz_offset=${tzOffset}`).then(setData).catch(() => {})
      })
      .catch(() => {})
      .finally(() => setDeleting(prev => { const n = new Set(prev); n.delete(id); return n }))
  }

  const tzOffset = new Date().getTimezoneOffset()

  useEffect(() => {
    setLoading(true)
    api.get<StatsResponse>(`/stats?days=${days}&tz_offset=${tzOffset}`)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [days])

  useEffect(() => { loadSessions(0, true) }, [])

  useEffect(() => {
    if (statsTab === 'habits' && estimates === null && !estimatesLoading) {
      setEstimatesLoading(true)
      api.get<CompletionEstimate[]>('/stats/completion-estimates')
        .then(setEstimates)
        .catch(() => setEstimates([]))
        .finally(() => setEstimatesLoading(false))
    }
  }, [statsTab])

  const cumulativeFinished = data ? (() => {
    const sorted = [...data.books_finished].sort((a, b) => a.date.localeCompare(b.date))
    const grouped: Record<string, string[]> = {}
    for (const b of sorted) {
      if (!grouped[b.date]) grouped[b.date] = []
      grouped[b.date].push(b.title)
    }
    let count = 0
    return Object.entries(grouped)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, titles]) => {
        count += titles.length
        return { date, titles, daily: titles.length, count }
      })
  })() : []

  const isEmpty = data && data.headline.total_sessions === 0

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-20 border-b border-border bg-background/80 backdrop-blur-sm safe-top">
        <div className="max-w-5xl mx-auto px-4 h-14 flex items-center gap-3">
          <Link to="/" className="p-2 rounded-lg hover:bg-accent transition-colors text-muted-foreground hover:text-foreground">
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <BarChart3 className="w-4 h-4 text-muted-foreground" />
          <span className="text-sm font-semibold">Reading Stats</span>
          <div className="ml-auto flex items-center gap-1 bg-muted rounded-lg p-0.5">
            {RANGES.map(r => (
              <button
                key={r.days}
                onClick={() => setDays(r.days)}
                className={cn(
                  'px-3 py-1 rounded-md text-xs font-medium transition-all',
                  days === r.days
                    ? 'bg-card shadow-sm text-foreground'
                    : 'text-muted-foreground hover:text-foreground'
                )}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto border-t border-border/50">
          <div className="flex items-center gap-1 px-4 py-1.5 max-w-5xl mx-auto">
            {(['overview', 'habits', 'library'] as const).map(tab => (
              <button
                key={tab}
                onClick={() => setStatsTab(tab)}
                className={cn(
                  'shrink-0 px-3 py-1.5 rounded-md text-xs font-medium transition-all whitespace-nowrap capitalize',
                  statsTab === tab ? 'bg-muted text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                )}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-6">
        {loading ? (
          <div className="flex justify-center py-32">
            <Loader2 className="w-6 h-6 animate-spin text-primary" />
          </div>
        ) : isEmpty ? (
          <div className="flex flex-col items-center justify-center py-32 gap-4 text-muted-foreground">
            <BarChart3 className="w-16 h-16 opacity-20" />
            <p className="text-sm font-medium text-foreground">No reading data yet</p>
            <p className="text-xs text-center max-w-xs">
              Reading stats will appear here once you start using the TomeSync KOReader plugin.
            </p>
            <Link to="/settings" className="text-xs text-primary hover:underline">
              Download the plugin from Settings
            </Link>
          </div>
        ) : data ? (
          <div className="flex flex-col gap-8">

            {/* ── Overview Tab ─────────────────────────────────────────── */}
            {statsTab === 'overview' && (
              <div className="flex flex-col gap-8">

                {/* Headline stats */}
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                  <StatCard
                    icon={<Clock className="w-3.5 h-3.5" />}
                    label="Reading Time"
                    value={formatDuration(data.headline.total_reading_seconds)}
                    sub={`avg ${formatDuration(data.headline.avg_session_seconds)} / session`}
                  />
                  <StatCard
                    icon={<Activity className="w-3.5 h-3.5" />}
                    label="Sessions"
                    value={String(data.headline.total_sessions)}
                  />
                  <StatCard
                    icon={<BookCheck className="w-3.5 h-3.5" />}
                    label="Books Finished"
                    value={String(data.headline.books_finished)}
                  />
                  <StatCard
                    icon={<Flame className="w-3.5 h-3.5" />}
                    label="Streak"
                    value={`${data.headline.current_streak_days}d`}
                    sub={`Longest: ${data.headline.longest_streak_days}d`}
                  />
                  <StatCard
                    icon={<FileText className="w-3.5 h-3.5" />}
                    label="Pages Turned"
                    value={data.headline.pages_turned.toLocaleString()}
                  />
                  <CompletionRateCard data={data.completion_rate} />
                </div>

                {/* Books in progress */}
                {data.books_in_progress.length > 0 && (
                  <ChartCard title="Currently Reading">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {data.books_in_progress.map(b => (
                        <a key={b.book_id} href={`/books/${b.book_id}`} className="group flex items-center gap-3 hover:bg-accent/30 rounded-lg p-2 transition-colors">
                          <div className="w-8 h-11 rounded bg-muted flex items-center justify-center shrink-0 overflow-hidden">
                            {b.has_cover ? (
                              <img src={`/api/books/${b.book_id}/cover`} alt="" className="w-full h-full object-cover" />
                            ) : (
                              <FileText className="w-4 h-4 text-muted-foreground" />
                            )}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-medium text-foreground truncate group-hover:text-primary transition-colors">
                              {b.title}
                            </div>
                            {b.author && <div className="text-xs text-muted-foreground truncate">{b.author}</div>}
                            <div className="mt-1.5 flex items-center gap-2">
                              <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                                <div
                                  className="h-full bg-indigo-500 rounded-full transition-all"
                                  style={{ width: `${Math.min(b.progress, 100)}%` }}
                                />
                              </div>
                              <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">{b.progress}%</span>
                            </div>
                          </div>
                        </a>
                      ))}
                    </div>
                  </ChartCard>
                )}

                {/* Daily chart */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  <ChartCard title="Reading Time per Day">
                    <ResponsiveContainer width="100%" height={200}>
                      <BarChart data={data.daily} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                        <XAxis
                          dataKey="date"
                          tickFormatter={formatDate}
                          tick={{ fontSize: 10, fill: '#94a3b8' }}
                          interval="preserveStartEnd"
                          axisLine={false}
                          tickLine={false}
                        />
                        <YAxis
                          tickFormatter={formatDuration}
                          tick={{ fontSize: 10, fill: '#94a3b8' }}
                          width={42}
                          axisLine={false}
                          tickLine={false}
                        />
                        <Tooltip
                          cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                          wrapperStyle={{ outline: 'none', background: 'none', border: 'none', boxShadow: 'none' }}
                          isAnimationActive={false}
                          content={({ active, payload }) => {
                            if (!active || !payload?.length) return null
                            const d = payload[0].payload
                            return (
                              <ChartTooltip>
                                <div className="font-medium">{formatDate(d.date)}</div>
                                <div>{formatDuration(d.seconds)}</div>
                                <div className="text-muted-foreground">{d.sessions} session{d.sessions !== 1 ? 's' : ''}</div>
                              </ChartTooltip>
                            )
                          }}
                        />
                        <Bar dataKey="seconds" fill="#6366f1" fillOpacity={0.85} radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </ChartCard>

                  <ChartCard title="Top Books by Reading Time">
                    {data.top_books.length === 0 ? (
                      <p className="text-sm text-muted-foreground text-center py-12">No reading sessions recorded.</p>
                    ) : (
                      <ResponsiveContainer width="100%" height={Math.max(180, data.top_books.length * 32)}>
                        <BarChart
                          data={data.top_books}
                          layout="vertical"
                          margin={{ top: 0, right: 8, bottom: 0, left: 8 }}
                        >
                          <XAxis
                            type="number"
                            tickFormatter={formatDuration}
                            tick={{ fontSize: 10, fill: '#94a3b8' }}
                            axisLine={false}
                            tickLine={false}
                          />
                          <YAxis
                            type="category"
                            dataKey="title"
                            width={140}
                            tick={{ fontSize: 10, fill: '#94a3b8' }}
                            axisLine={false}
                            tickLine={false}
                          />
                          <Tooltip
                            cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                            wrapperStyle={{ outline: 'none', background: 'none', border: 'none', boxShadow: 'none' }}
                            isAnimationActive={false}
                            content={({ active, payload }) => {
                              if (!active || !payload?.length) return null
                              const d = payload[0].payload
                              return (
                                <ChartTooltip>
                                  <div className="font-medium">{d.title}</div>
                                  <div>{formatDuration(d.seconds)}</div>
                                  <div className="text-muted-foreground">{d.sessions} session{d.sessions !== 1 ? 's' : ''}</div>
                                </ChartTooltip>
                              )
                            }}
                          />
                          <Bar dataKey="seconds" fill="#6366f1" fillOpacity={0.85} radius={[0, 3, 3, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    )}
                  </ChartCard>
                </div>

                {/* 365-day heatmap */}
                <ChartCard title="Reading Activity — Last 365 Days">
                  <HeatmapChart data={data.heatmap_daily} />
                  <div className="flex items-center gap-2 justify-end mt-1">
                    <span className="text-[10px] text-muted-foreground">Less</span>
                    {[
                      'rgba(148, 163, 184, 0.1)',
                      'rgba(99, 102, 241, 0.25)',
                      'rgba(99, 102, 241, 0.45)',
                      'rgba(99, 102, 241, 0.7)',
                      '#6366f1',
                    ].map((c, i) => (
                      <div key={i} className="w-3 h-3 rounded-sm border border-border/30" style={{ backgroundColor: c }} />
                    ))}
                    <span className="text-[10px] text-muted-foreground">More</span>
                  </div>
                </ChartCard>

                {/* Milestones */}
                {cumulativeFinished.length > 0 && (
                  <ChartCard title="Books Finished">
                    <ResponsiveContainer width="100%" height={200}>
                      <AreaChart data={cumulativeFinished} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                        <XAxis
                          dataKey="date"
                          tickFormatter={formatDate}
                          tick={{ fontSize: 10, fill: '#94a3b8' }}
                          interval="preserveStartEnd"
                          axisLine={false}
                          tickLine={false}
                        />
                        <YAxis
                          allowDecimals={false}
                          tick={{ fontSize: 10, fill: '#94a3b8' }}
                          width={30}
                          axisLine={false}
                          tickLine={false}
                        />
                        <Tooltip
                          cursor={{ fill: 'rgba(255,255,255,0.04)' }}
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
                                <div className="text-muted-foreground mt-1">
                                  {d.daily} finished &middot; {d.count} total
                                </div>
                              </ChartTooltip>
                            )
                          }}
                        />
                        <Area
                          dataKey="count"
                          fill="#6366f1"
                          fillOpacity={0.15}
                          stroke="#6366f1"
                          strokeWidth={2}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </ChartCard>
                )}

                {/* Session log */}
                <div className="flex flex-col gap-4" id="session-log">
                  <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Session Log</h2>
                  <ChartCard title={sessionsTotal > 0 ? `Recent Sessions · ${sessionsTotal}` : 'Recent Sessions'}>
                    {sessions.length === 0 && !sessionsLoading ? (
                      <p className="text-sm text-muted-foreground text-center py-8">No sessions recorded.</p>
                    ) : (
                      <div className="flex flex-col gap-0">
                        <div className="hidden sm:grid grid-cols-[1fr_120px_80px_80px_40px] gap-2 px-2 pb-2 text-[10px] font-semibold text-muted-foreground uppercase tracking-wide border-b border-border">
                          <span>Book</span>
                          <span>Date</span>
                          <span>Duration</span>
                          <span>Device</span>
                          <span />
                        </div>
                        {sessions.map((s, idx) => (
                          <div
                            key={s.id}
                            className={cn(
                              'grid grid-cols-1 sm:grid-cols-[1fr_120px_80px_80px_40px] gap-1 sm:gap-2 px-2 py-2 items-center hover:bg-accent/30 transition-colors text-xs rounded',
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
                            <div className="text-muted-foreground">
                              {s.duration_seconds != null ? formatDuration(s.duration_seconds) : '--'}
                            </div>
                            <div className="text-muted-foreground truncate">{s.device || '--'}</div>
                            <div className="flex justify-end">
                              <button
                                onClick={() => deleteSession(s.id)}
                                disabled={deleting.has(s.id)}
                                className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors disabled:opacity-40"
                                title="Delete session"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </div>
                        ))}
                        {sessionsLoaded < sessionsTotal && (
                          <button
                            onClick={() => loadSessions(sessionsLoaded, false)}
                            disabled={sessionsLoading}
                            className="flex items-center justify-center gap-1.5 py-3 text-xs text-primary hover:text-primary/80 transition-colors disabled:opacity-50"
                          >
                            {sessionsLoading ? (
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            ) : (
                              <>
                                <ChevronDown className="w-3.5 h-3.5" />
                                <span>Show more ({sessionsTotal - sessionsLoaded} remaining)</span>
                              </>
                            )}
                          </button>
                        )}
                      </div>
                    )}
                  </ChartCard>
                </div>

              </div>
            )}

            {/* ── Habits Tab ───────────────────────────────────────────── */}
            {statsTab === 'habits' && (
              <div className="flex flex-col gap-8">

                {/* Hour x DOW heatmap */}
                <div className="flex flex-col gap-4">
                  <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">When You Read</h2>
                  <ChartCard title="Reading Intensity by Hour and Day">
                    <HourDowHeatmap data={data.hour_dow_heatmap} />
                    <div className="flex items-center gap-2 justify-end mt-1">
                      <span className="text-[10px] text-muted-foreground">Less</span>
                      {[0.15, 0.37, 0.58, 0.79, 1.0].map(o => (
                        <div
                          key={o}
                          className="w-3 h-3 rounded-sm border border-border/30"
                          style={{ backgroundColor: `rgba(99, 102, 241, ${o})` }}
                        />
                      ))}
                      <span className="text-[10px] text-muted-foreground">More</span>
                    </div>
                  </ChartCard>
                </div>

                {/* Session timeline */}
                {data.session_timeline.length > 0 && (
                  <ChartCard title="Session Timeline">
                    <div className="flex flex-col gap-1 max-h-[320px] overflow-y-auto">
                      {(() => {
                        const grouped: Record<string, typeof data.session_timeline> = {}
                        for (const s of data.session_timeline) {
                          const d = s.started_at.slice(0, 10)
                          if (!grouped[d]) grouped[d] = []
                          grouped[d].push(s)
                        }
                        return Object.entries(grouped)
                          .sort(([a], [b]) => b.localeCompare(a))
                          .slice(0, 14)
                          .map(([dateStr, daySessions]) => (
                            <div key={dateStr} className="flex items-center gap-3 py-1">
                              <span className="text-[10px] text-muted-foreground w-16 shrink-0 text-right">
                                {formatDate(dateStr)}
                              </span>
                              <div className="flex-1 relative h-6 bg-muted/30 rounded overflow-hidden">
                                {daySessions.map(s => {
                                  const start = new Date(s.started_at)
                                  const end = new Date(s.ended_at)
                                  const dayStart = start.getHours() * 60 + start.getMinutes()
                                  const dayEnd = end.getHours() * 60 + end.getMinutes()
                                  const left = (dayStart / 1440) * 100
                                  const width = Math.max(((dayEnd - dayStart) / 1440) * 100, 0.8)
                                  return (
                                    <div
                                      key={s.id}
                                      className="absolute top-0.5 bottom-0.5 rounded-sm bg-indigo-500/70 hover:bg-indigo-500 transition-colors"
                                      style={{ left: `${left}%`, width: `${width}%` }}
                                      title={`${s.title} — ${start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} to ${end.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} (${formatDuration(s.duration_seconds)})`}
                                    />
                                  )
                                })}
                                {[6, 12, 18].map(h => (
                                  <div
                                    key={h}
                                    className="absolute top-0 bottom-0 border-l border-border/40"
                                    style={{ left: `${(h / 24) * 100}%` }}
                                  />
                                ))}
                              </div>
                              <div className="flex gap-3 text-[9px] text-muted-foreground shrink-0 w-20">
                                <span>6</span><span>12</span><span>18</span><span>24</span>
                              </div>
                            </div>
                          ))
                      })()}
                    </div>
                  </ChartCard>
                )}

                {/* Reading pace + pace by format */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {data.reading_pace.length > 0 && (
                    <ChartCard title="Reading Pace">
                      <ResponsiveContainer width="100%" height={200}>
                        <AreaChart data={[...data.reading_pace].reverse()} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                          <XAxis
                            dataKey="date"
                            tickFormatter={formatDate}
                            tick={{ fontSize: 10, fill: '#94a3b8' }}
                            interval="preserveStartEnd"
                            axisLine={false}
                            tickLine={false}
                          />
                          <YAxis
                            tick={{ fontSize: 10, fill: '#94a3b8' }}
                            width={36}
                            axisLine={false}
                            tickLine={false}
                            tickFormatter={(v: number) => v.toFixed(1)}
                          />
                          <Tooltip
                            cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                            wrapperStyle={{ outline: 'none', background: 'none', border: 'none', boxShadow: 'none' }}
                            isAnimationActive={false}
                            content={({ active, payload }) => {
                              if (!active || !payload?.length) return null
                              const d = payload[0].payload
                              return (
                                <ChartTooltip>
                                  <div className="font-medium">{d.title}</div>
                                  <div>{d.pages_per_min} pages/min</div>
                                  <div className="text-muted-foreground">{d.pages_turned} pages in {formatDuration(d.duration_seconds)}</div>
                                </ChartTooltip>
                              )
                            }}
                          />
                          <Area
                            dataKey="pages_per_min"
                            fill="#10b981"
                            fillOpacity={0.15}
                            stroke="#10b981"
                            strokeWidth={2}
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                      <p className="text-xs text-muted-foreground text-center">
                        avg {(data.reading_pace.reduce((s, p) => s + p.pages_per_min, 0) / data.reading_pace.length).toFixed(1)} pages/min
                      </p>
                    </ChartCard>
                  )}

                  {data.pace_by_format.length > 0 && (
                    <ChartCard title="Pace by Format">
                      <PaceByFormat data={data.pace_by_format} />
                    </ChartCard>
                  )}
                </div>

                {/* Reading speed trend */}
                {data.reading_pace.length >= 4 && (() => {
                  const paceData = [...data.reading_pace].reverse()
                  const half = Math.floor(paceData.length / 2)
                  const firstHalf = paceData.slice(0, half)
                  const secondHalf = paceData.slice(half)
                  const avg = (arr: typeof paceData) =>
                    arr.reduce((s, p) => s + p.pages_per_min, 0) / arr.length
                  const firstAvg = avg(firstHalf)
                  const secondAvg = avg(secondHalf)
                  const pctDiff = firstAvg > 0 ? Math.round(((secondAvg - firstAvg) / firstAvg) * 100) : 0
                  const trending = pctDiff > 3 ? 'up' : pctDiff < -3 ? 'down' : 'steady'
                  return (
                    <div className="flex flex-col gap-4">
                      <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Reading Speed Trend</h2>
                      <div className="bg-card border border-border rounded-xl p-4 flex flex-col gap-3">
                        <div className="flex items-center gap-2">
                          {trending === 'up' ? (
                            <TrendingUp className="w-4 h-4 text-emerald-500" />
                          ) : trending === 'down' ? (
                            <TrendingDown className="w-4 h-4 text-red-500" />
                          ) : (
                            <Zap className="w-4 h-4 text-muted-foreground" />
                          )}
                          <span className={cn(
                            'text-sm font-semibold',
                            trending === 'up' ? 'text-emerald-500' : trending === 'down' ? 'text-red-500' : 'text-muted-foreground',
                          )}>
                            {trending === 'up'
                              ? `Reading speed up ${pctDiff}%`
                              : trending === 'down'
                              ? `Reading speed down ${Math.abs(pctDiff)}%`
                              : 'Reading speed steady'}
                          </span>
                          <span className="text-xs text-muted-foreground ml-1">
                            ({secondAvg.toFixed(1)} vs {firstAvg.toFixed(1)} pages/min)
                          </span>
                        </div>
                        <ResponsiveContainer width="100%" height={140}>
                          <AreaChart data={paceData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                            <XAxis
                              dataKey="date"
                              tickFormatter={formatDate}
                              tick={{ fontSize: 10, fill: '#94a3b8' }}
                              interval="preserveStartEnd"
                              axisLine={false}
                              tickLine={false}
                            />
                            <YAxis
                              tick={{ fontSize: 10, fill: '#94a3b8' }}
                              width={36}
                              axisLine={false}
                              tickLine={false}
                              tickFormatter={(v: number) => v.toFixed(1)}
                            />
                            <Tooltip
                              cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                              wrapperStyle={{ outline: 'none', background: 'none', border: 'none', boxShadow: 'none' }}
                              isAnimationActive={false}
                              content={({ active, payload }) => {
                                if (!active || !payload?.length) return null
                                const d = payload[0].payload
                                return (
                                  <ChartTooltip>
                                    <div className="font-medium">{d.title}</div>
                                    <div>{d.pages_per_min} pages/min</div>
                                  </ChartTooltip>
                                )
                              }}
                            />
                            <Area
                              dataKey="pages_per_min"
                              fill="#10b981"
                              fillOpacity={0.15}
                              stroke="#10b981"
                              strokeWidth={2}
                            />
                          </AreaChart>
                        </ResponsiveContainer>
                      </div>
                    </div>
                  )
                })()}

                {/* Completion estimates */}
                <div className="flex flex-col gap-4">
                  <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Completion Estimates</h2>
                  {estimatesLoading ? (
                    <div className="flex justify-center py-10">
                      <Loader2 className="w-5 h-5 animate-spin text-primary" />
                    </div>
                  ) : estimates && estimates.length > 0 ? (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {estimates.map(est => (
                        <a
                          key={est.book_id}
                          href={`/books/${est.book_id}`}
                          className="group bg-card border border-border rounded-xl p-4 flex items-start gap-3 hover:bg-accent/30 transition-colors"
                        >
                          <div className="w-9 h-12 rounded bg-muted flex items-center justify-center shrink-0 overflow-hidden">
                            {est.has_cover ? (
                              <img src={`/api/books/${est.book_id}/cover`} alt="" className="w-full h-full object-cover" />
                            ) : (
                              <FileText className="w-4 h-4 text-muted-foreground" />
                            )}
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className={cn(
                              'text-sm font-medium truncate group-hover:text-primary transition-colors',
                              est.confidence === 'low' ? 'text-muted-foreground' : 'text-foreground',
                            )}>
                              {est.title}
                            </p>
                            {est.author && (
                              <p className="text-xs text-muted-foreground truncate">{est.author}</p>
                            )}
                            <div className="mt-2 flex items-center gap-2">
                              <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                                <div
                                  className="h-full bg-indigo-500 rounded-full"
                                  style={{ width: `${Math.min(est.progress, 100)}%` }}
                                />
                              </div>
                              <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">{est.progress}%</span>
                            </div>
                            <p className={cn(
                              'text-xs mt-1.5',
                              est.confidence === 'high'
                                ? 'text-foreground'
                                : est.confidence === 'medium'
                                ? 'text-muted-foreground'
                                : 'text-muted-foreground/60',
                            )}>
                              {est.estimated_days != null
                                ? `~${est.estimated_days} day${est.estimated_days !== 1 ? 's' : ''} remaining`
                                : 'Just started'}
                              {est.confidence === 'low' && est.estimated_days != null && (
                                <span className="ml-1 text-muted-foreground/50">(low confidence)</span>
                              )}
                            </p>
                          </div>
                        </a>
                      ))}
                    </div>
                  ) : estimates !== null ? (
                    <div className="bg-card border border-border rounded-xl p-8 text-center text-muted-foreground text-sm">
                      No books currently in progress.
                    </div>
                  ) : null}
                </div>

                {/* Period comparison */}
                {data.period_comparison && (
                  <div className="flex flex-col gap-4">
                    <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Period Comparison</h2>
                    <div className="bg-card border border-border rounded-xl p-5 flex items-start gap-4">
                      <div className={cn(
                        'p-2 rounded-lg shrink-0',
                        data.period_comparison.pct_change === null
                          ? 'bg-muted'
                          : data.period_comparison.pct_change > 0
                          ? 'bg-emerald-500/10'
                          : data.period_comparison.pct_change < 0
                          ? 'bg-red-500/10'
                          : 'bg-muted',
                      )}>
                        {data.period_comparison.pct_change === null ? (
                          <Minus className="w-5 h-5 text-muted-foreground" />
                        ) : data.period_comparison.pct_change > 0 ? (
                          <TrendingUp className="w-5 h-5 text-emerald-500" />
                        ) : data.period_comparison.pct_change < 0 ? (
                          <TrendingDown className="w-5 h-5 text-red-500" />
                        ) : (
                          <Minus className="w-5 h-5 text-muted-foreground" />
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className={cn(
                          'text-lg font-bold',
                          data.period_comparison.pct_change === null
                            ? 'text-muted-foreground'
                            : data.period_comparison.pct_change > 0
                            ? 'text-emerald-500'
                            : data.period_comparison.pct_change < 0
                            ? 'text-red-500'
                            : 'text-muted-foreground',
                        )}>
                          {data.period_comparison.pct_change === null
                            ? 'No previous data to compare'
                            : data.period_comparison.pct_change === 0 && data.period_comparison.current_seconds === 0
                            ? 'No reading data'
                            : data.period_comparison.pct_change === 0
                            ? 'Same as previous period'
                            : data.period_comparison.pct_change > 0
                            ? `${data.period_comparison.pct_change}% more reading this period`
                            : `${Math.abs(data.period_comparison.pct_change)}% less reading this period`}
                        </p>
                        <p className="text-xs text-muted-foreground mt-1">
                          This period: <span className="text-foreground font-medium">{formatDuration(data.period_comparison.current_seconds)}</span>
                          {data.period_comparison.pct_change !== null && (
                            <>{' '}vs previous: <span className="text-foreground font-medium">{formatDuration(data.period_comparison.previous_seconds)}</span></>
                          )}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {/* Monthly comparison */}
                {data.monthly_comparison.length > 0 && (
                  <div className="flex flex-col gap-4">
                    <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Monthly Comparison</h2>
                    <ChartCard title="Reading Hours & Books Finished — Last 12 Months">
                      <ResponsiveContainer width="100%" height={260}>
                        <BarChart data={data.monthly_comparison} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                          <XAxis
                            dataKey="label"
                            tick={{ fontSize: 10, fill: '#94a3b8' }}
                            axisLine={false}
                            tickLine={false}
                          />
                          <YAxis
                            yAxisId="hours"
                            tick={{ fontSize: 10, fill: '#94a3b8' }}
                            width={36}
                            axisLine={false}
                            tickLine={false}
                            tickFormatter={(v: number) => `${v}h`}
                          />
                          <YAxis
                            yAxisId="books"
                            orientation="right"
                            tick={{ fontSize: 10, fill: '#94a3b8' }}
                            width={30}
                            axisLine={false}
                            tickLine={false}
                            allowDecimals={false}
                          />
                          <Tooltip
                            cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                            wrapperStyle={{ outline: 'none', background: 'none', border: 'none', boxShadow: 'none' }}
                            isAnimationActive={false}
                            content={({ active, payload }) => {
                              if (!active || !payload?.length) return null
                              const d = payload[0].payload
                              return (
                                <ChartTooltip>
                                  <div className="font-medium">{d.month}</div>
                                  <div>{d.reading_hours}h reading</div>
                                  <div>{d.books_finished} book{d.books_finished !== 1 ? 's' : ''} finished</div>
                                  <div className="text-muted-foreground">{d.sessions} session{d.sessions !== 1 ? 's' : ''}</div>
                                </ChartTooltip>
                              )
                            }}
                          />
                          <Legend formatter={v => <span style={{ fontSize: 11 }}>{v}</span>} wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
                          <Bar yAxisId="hours" dataKey="reading_hours" name="Reading Hours" fill="#6366f1" fillOpacity={0.85} radius={[3, 3, 0, 0]} />
                          <Bar yAxisId="books" dataKey="books_finished" name="Books Finished" fill="#8b5cf6" fillOpacity={0.55} radius={[3, 3, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </ChartCard>
                  </div>
                )}

              </div>
            )}

            {/* ── Library Tab ──────────────────────────────────────────── */}
            {statsTab === 'library' && (
              <div className="flex flex-col gap-8">

                {/* Year summary */}
                <div className="flex flex-col gap-4">
                  <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Year in Review</h2>
                  {data.year_summary ? (
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                      <StatCard
                        icon={<BookCheck className="w-3.5 h-3.5" />}
                        label="Books Finished"
                        value={String(data.year_summary.books_finished)}
                      />
                      <StatCard
                        icon={<Clock className="w-3.5 h-3.5" />}
                        label="Total Hours"
                        value={`${data.year_summary.total_hours}h`}
                      />
                      <StatCard
                        icon={<Trophy className="w-3.5 h-3.5" />}
                        label="Top Genre"
                        value={data.year_summary.top_genre ?? '--'}
                      />
                      <StatCard
                        icon={<Flame className="w-3.5 h-3.5" />}
                        label="Longest Streak"
                        value={`${data.year_summary.longest_streak_days}d`}
                      />
                      <StatCard
                        icon={<Activity className="w-3.5 h-3.5" />}
                        label="Total Sessions"
                        value={String(data.year_summary.total_sessions)}
                      />
                      <StatCard
                        icon={<Calendar className="w-3.5 h-3.5" />}
                        label="Most Active Month"
                        value={data.year_summary.most_active_month ?? '--'}
                      />
                    </div>
                  ) : (
                    <div className="bg-card border border-border rounded-xl p-5 text-center text-muted-foreground text-xs">
                      Select <span className="font-medium text-foreground">1 Year</span> or <span className="font-medium text-foreground">All Time</span> to see your year in review.
                    </div>
                  )}
                </div>

                {/* Series completion */}
                <div className="flex flex-col gap-4">
                  <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Series Completion <span className="text-muted-foreground/60 normal-case tracking-normal font-normal ml-1">· all time</span></h2>
                  <SeriesCompletionGrid data={data.series_completion} />
                </div>

                {/* Author affinity */}
                {data.author_affinity.length > 0 && (
                  <div className="flex flex-col gap-4">
                    <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Author Affinity</h2>
                    <ChartCard title="Top Authors by Reading Time">
                      <AuthorAffinity data={data.author_affinity} />
                    </ChartCard>
                  </div>
                )}

                {/* Completion by type */}
                {data.completion_by_type.length > 0 && (
                  <div className="flex flex-col gap-4">
                    <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Completion by Type <span className="text-muted-foreground/60 normal-case tracking-normal font-normal ml-1">· all time</span></h2>
                    <ChartCard title="Finish Rate per Book Category">
                      <CompletionByType data={data.completion_by_type} />
                    </ChartCard>
                  </div>
                )}

                {/* By category pie */}
                <ChartCard title="Category Breakdown">
                  {data.by_category.length === 0 ? (
                    <p className="text-sm text-muted-foreground text-center py-12">No category data.</p>
                  ) : (
                    <ResponsiveContainer width="100%" height={200}>
                      <PieChart>
                        <Pie
                          data={data.by_category}
                          dataKey="seconds"
                          nameKey="category"
                          cx="50%"
                          cy="50%"
                          outerRadius={72}
                          label={false}
                          stroke="none"
                        >
                          {data.by_category.map((_, i) => (
                            <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip
                          cursor={false}
                          wrapperStyle={{ outline: 'none', background: 'none', border: 'none', boxShadow: 'none' }}
                          isAnimationActive={false}
                          content={({ active, payload }) => {
                            if (!active || !payload?.length) return null
                            const d = payload[0].payload
                            const total = data.by_category.reduce((s, c) => s + c.seconds, 0)
                            return (
                              <ChartTooltip>
                                <div className="font-medium">{d.category}</div>
                                <div>{formatDuration(d.seconds)} ({total > 0 ? Math.round(d.seconds / total * 100) : 0}%)</div>
                                <div className="text-muted-foreground">{d.book_count} book{d.book_count !== 1 ? 's' : ''}</div>
                              </ChartTooltip>
                            )
                          }}
                        />
                        <Legend formatter={v => <span style={{ fontSize: 11 }}>{v}</span>} wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
                      </PieChart>
                    </ResponsiveContainer>
                  )}
                </ChartCard>

                {/* Genre over time */}
                <GenreOverTimeChart data={data.genre_over_time} />

                {/* Library growth */}
                <div className="flex flex-col gap-4">
                  <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Library Growth</h2>
                  <ChartCard title="Cumulative Books Added — Last 24 Months">
                    <LibraryGrowthChart data={data.library_growth} />
                  </ChartCard>
                </div>

                {/* Per-book time table */}
                {data.per_book_time.length > 0 && (
                  <PerBookTimeTable data={data.per_book_time} />
                )}

              </div>
            )}

          </div>
        ) : null}
      </main>
    </div>
  )
}
