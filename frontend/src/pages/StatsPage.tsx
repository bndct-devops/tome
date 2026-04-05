import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Clock, Activity, BookCheck, Flame, FileText,
  BarChart3, ArrowLeft, Loader2, Trash2, ChevronDown,
} from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
  AreaChart, Area,
} from 'recharts'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

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
  hourly: { hour: number; seconds: number; sessions: number }[]
  weekly: { day: string; seconds: number; sessions: number }[]
  reading_pace: { session_id: number; title: string; date: string | null; pages_per_min: number; duration_seconds: number; pages_turned: number }[]
  books_in_progress: { book_id: number; title: string; author: string | null; has_cover: boolean; progress: number; last_read: string | null }[]
  session_timeline: { id: number; title: string; started_at: string; ended_at: string; duration_seconds: number }[]
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

function StatCard({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub?: string }) {
  return (
    <div className="bg-card border border-border rounded-xl p-4 flex flex-col gap-1">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        {icon}
        <span className="text-xs">{label}</span>
      </div>
      <p className="text-2xl font-bold text-foreground leading-tight">{value}</p>
      {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
    </div>
  )
}

// ── Heatmap ───────────────────────────────────────────────────────────────────

function HeatmapChart({ data }: { data: { date: string; seconds: number }[] }) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; date: string; seconds: number } | null>(null)

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
    <div className="relative overflow-x-auto flex justify-center">
      <svg width={svgW} height={svgH} style={{ display: 'block' }}>
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
          style={{ left: tooltip.x + 12, top: tooltip.y - 44 }}
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

// ── Main Page ─────────────────────────────────────────────────────────────────

export function StatsPage() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<StatsResponse | null>(null)
  const [loading, setLoading] = useState(true)

  // Sessions list state
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
        // Refresh stats to update totals
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

  // Load sessions on mount
  useEffect(() => { loadSessions(0, true) }, [])

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
      <header className="sticky top-0 z-20 border-b border-border bg-background/80 backdrop-blur-sm">
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

            {/* ── Overview ─────────────────────────────────────────── */}
            <div className="flex flex-col gap-4">
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
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
              </div>

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
            </div>

            {/* ── Patterns ─────────────────────────────────────────── */}
            <div className="flex flex-col gap-4">
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">When You Read</h2>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <ChartCard title="Time of Day">
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={data.hourly} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                      <XAxis
                        dataKey="hour"
                        tickFormatter={(h: number) => `${h}:00`}
                        tick={{ fontSize: 10, fill: '#94a3b8' }}
                        interval={2}
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
                              <div className="font-medium">{d.hour}:00 - {d.hour}:59</div>
                              <div>{formatDuration(d.seconds)}</div>
                              <div className="text-muted-foreground">{d.sessions} session{d.sessions !== 1 ? 's' : ''}</div>
                            </ChartTooltip>
                          )
                        }}
                      />
                      <Bar dataKey="seconds" fill="#8b5cf6" fillOpacity={0.85} radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </ChartCard>

                <ChartCard title="Weekly Pattern">
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={data.weekly} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                      <XAxis
                        dataKey="day"
                        tick={{ fontSize: 10, fill: '#94a3b8' }}
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
                              <div className="font-medium">{d.day}</div>
                              <div>{formatDuration(d.seconds)}</div>
                              <div className="text-muted-foreground">{d.sessions} session{d.sessions !== 1 ? 's' : ''}</div>
                            </ChartTooltip>
                          )
                        }}
                      />
                      <Bar dataKey="seconds" fill="#ec4899" fillOpacity={0.85} radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </ChartCard>
              </div>

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
            </div>

            {/* ── Activity ─────────────────────────────────────────── */}
            <div className="flex flex-col gap-4">
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Activity</h2>
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
              </div>

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
            </div>

            {/* ── Milestones ───────────────────────────────────────── */}
            {cumulativeFinished.length > 0 && (
              <div className="flex flex-col gap-4">
                <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Milestones</h2>
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
              </div>
            )}

            {/* ── Session Log ──────────────────────────────────────── */}
            <div className="flex flex-col gap-4">
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
        ) : null}
      </main>
    </div>
  )
}
