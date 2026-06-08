// Overview-tab stats widgets — chart-only (no card frame, no fixed height), so the
// same component renders inside a StatsPage ChartCard and inside a resizable Lab tile.
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
} from 'recharts'
import { Clock, Activity, BookCheck, Flame, FileText } from 'lucide-react'
import { formatDate, formatDuration } from '@/lib/utils'
import { useChartAccent } from '@/lib/useChartAccent'
import { StatCard } from '@/components/stats/StatCard'
import { CompletionRateCard } from '@/components/stats/CompletionRateCard'
import { ChartTooltip, HeatmapChart, type StatsResponse } from '@/components/stats/shared'

export function OverviewHeadline({
  headline,
  completionRate,
}: {
  headline: StatsResponse['headline']
  completionRate: StatsResponse['completion_rate']
}) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      <StatCard
        icon={<Clock className="w-3.5 h-3.5" />}
        label="Reading Time"
        value={formatDuration(headline.total_reading_seconds)}
        sub={`avg ${formatDuration(headline.avg_session_seconds)} / session`}
      />
      <StatCard icon={<Activity className="w-3.5 h-3.5" />} label="Sessions" value={String(headline.total_sessions)} />
      <StatCard icon={<BookCheck className="w-3.5 h-3.5" />} label="Books Finished" value={String(headline.books_finished)} />
      <StatCard
        icon={<Flame className="w-3.5 h-3.5" />}
        label="Streak"
        value={`${headline.current_streak_days}d`}
        sub={`Longest: ${headline.longest_streak_days}d`}
      />
      <StatCard icon={<FileText className="w-3.5 h-3.5" />} label="Pages Turned" value={headline.pages_turned.toLocaleString()} />
      <CompletionRateCard data={completionRate} />
    </div>
  )
}

// Bare headline-stat body (value + sub) for use as a standalone dashboard tile —
// the tile itself provides the card frame + the label (in its header).
export function HeadlineStatBody({ value, sub }: { value: string; sub?: string }) {
  return (
    <div className="flex h-full flex-col justify-center">
      <p className="text-xl font-bold leading-none text-foreground tabular-nums sm:text-2xl">{value}</p>
      {sub && <p className="mt-1.5 text-xs text-muted-foreground">{sub}</p>}
    </div>
  )
}

export function CurrentlyReading({ books }: { books: StatsResponse['books_in_progress'] }) {
  const accent = useChartAccent()
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
      {books.map((b) => (
        <a key={b.book_id} href={`/books/${b.book_id}`} className="group flex items-center gap-3 hover:bg-accent/30 rounded-lg p-2 transition-colors">
          <div className="w-8 h-11 rounded bg-muted flex items-center justify-center shrink-0 overflow-hidden">
            {b.has_cover ? (
              <img src={`/api/books/${b.book_id}/cover`} alt="" className="w-full h-full object-cover" />
            ) : (
              <FileText className="w-4 h-4 text-muted-foreground" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-foreground truncate group-hover:text-primary transition-colors">{b.title}</div>
            {b.author && <div className="text-xs text-muted-foreground truncate">{b.author}</div>}
            <div className="mt-1.5 flex items-center gap-2">
              <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                <div className="h-full rounded-full transition-all" style={{ width: `${Math.min(b.progress, 100)}%`, backgroundColor: accent }} />
              </div>
              <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">{b.progress}%</span>
            </div>
          </div>
        </a>
      ))}
    </div>
  )
}

// One-line duration y-tick — recharts wraps multi-word ticks like "1h 40m".
function DurationYTick({ x, y, payload }: { x?: number; y?: number; payload?: { value?: number } }) {
  return (
    <text x={x} y={y} dy={3} textAnchor="end" fontSize={10} fill="#94a3b8">
      {formatDuration(payload?.value ?? 0)}
    </text>
  )
}

export function ReadingTimePerDay({ daily }: { daily: StatsResponse['daily'] }) {
  const accent = useChartAccent()
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={daily} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 10, fill: '#94a3b8' }} interval="preserveStartEnd" axisLine={false} tickLine={false} />
        <YAxis tick={<DurationYTick />} width={52} axisLine={false} tickLine={false} />
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
        <Bar dataKey="seconds" fill={accent} fillOpacity={0.85} radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

// Single-line, truncated y-axis label — recharts' default tick wraps long book
// titles across several lines (and then drops some), which looks broken.
function TitleTick({ x, y, payload }: { x?: number; y?: number; payload?: { value?: string } }) {
  const t = String(payload?.value ?? '')
  const s = t.length > 20 ? t.slice(0, 19) + '…' : t
  return (
    <text x={x} y={y} dy={4} textAnchor="end" fontSize={10} fill="#94a3b8">
      {s}
    </text>
  )
}

export function TopBooksByTime({ topBooks }: { topBooks: StatsResponse['top_books'] }) {
  const accent = useChartAccent()
  if (topBooks.length === 0) {
    return <p className="text-sm text-muted-foreground text-center py-12">No reading sessions recorded.</p>
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={topBooks} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 8 }}>
        <XAxis type="number" tickFormatter={formatDuration} tick={{ fontSize: 10, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey="title" width={140} tick={<TitleTick />} axisLine={false} tickLine={false} interval={0} />
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
        <Bar dataKey="seconds" fill={accent} fillOpacity={0.85} radius={[0, 3, 3, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function ReadingActivity365({ heatmap }: { heatmap: StatsResponse['heatmap_daily'] }) {
  const accent = useChartAccent()
  return (
    <>
      <HeatmapChart data={heatmap} />
      <div className="flex items-center gap-2 justify-end mt-1">
        <span className="text-[10px] text-muted-foreground">Less</span>
        {[0, 0.25, 0.45, 0.7, 1].map((op, i) => (
          <div
            key={i}
            className="w-3 h-3 rounded-sm border border-border/30"
            style={op === 0 ? { backgroundColor: 'rgba(148, 163, 184, 0.1)' } : { backgroundColor: accent, opacity: op }}
          />
        ))}
        <span className="text-[10px] text-muted-foreground">More</span>
      </div>
    </>
  )
}

export function BooksFinishedArea({ booksFinished }: { booksFinished: StatsResponse['books_finished'] }) {
  const accent = useChartAccent()
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

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={cumulative} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 10, fill: '#94a3b8' }} interval="preserveStartEnd" axisLine={false} tickLine={false} />
        <YAxis allowDecimals={false} tick={{ fontSize: 10, fill: '#94a3b8' }} width={30} axisLine={false} tickLine={false} />
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
                <div className="text-muted-foreground mt-1">{d.daily} finished &middot; {d.count} total</div>
              </ChartTooltip>
            )
          }}
        />
        <Area dataKey="count" fill={accent} fillOpacity={0.15} stroke={accent} strokeWidth={2} />
      </AreaChart>
    </ResponsiveContainer>
  )
}
