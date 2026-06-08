// Library-tab stats widgets — chart/content-only, shared by StatsPage and the Lab.
import { useState } from 'react'
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
} from 'recharts'
import { BookCheck, Clock, Trophy, Flame, Activity, Calendar, FileText, ArrowUpDown, ChevronUp, ChevronDown, type LucideIcon } from 'lucide-react'
import { cn, formatDuration } from '@/lib/utils'
import { useChartPalette } from '@/lib/useChartPalette'
import { ChartTooltip, type StatsResponse } from '@/components/stats/shared'

export function YearInReview({ summary }: { summary: StatsResponse['year_summary'] }) {
  if (!summary) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-center text-xs text-muted-foreground">
        Select <span className="mx-1 font-medium text-foreground">1y</span> or <span className="mx-1 font-medium text-foreground">All</span> to see your year in review.
      </div>
    )
  }
  // Compact (no nested StatCard frame) so all six fit a 2-row tile without clipping.
  const items: { icon: LucideIcon; label: string; value: string }[] = [
    { icon: BookCheck, label: 'Books Finished', value: String(summary.books_finished) },
    { icon: Clock, label: 'Total Hours', value: `${summary.total_hours}h` },
    { icon: Trophy, label: 'Top Genre', value: summary.top_genre ?? '--' },
    { icon: Flame, label: 'Longest Streak', value: `${summary.longest_streak_days}d` },
    { icon: Activity, label: 'Total Sessions', value: String(summary.total_sessions) },
    { icon: Calendar, label: 'Most Active Month', value: summary.most_active_month ?? '--' },
  ]
  return (
    <div className="grid h-full grid-cols-2 gap-2 sm:grid-cols-3">
      {items.map((it) => (
        <div key={it.label} className="flex flex-col justify-center rounded-lg border border-border bg-muted/20 px-3 py-1.5">
          <div className="flex items-center gap-1 text-muted-foreground">
            <it.icon className="h-3 w-3 shrink-0" />
            <span className="truncate text-[10px]">{it.label}</span>
          </div>
          <p className="truncate text-base font-bold leading-tight text-foreground sm:text-lg">{it.value}</p>
        </div>
      ))}
    </div>
  )
}

export function CategoryBreakdown({ data }: { data: StatsResponse['by_category'] }) {
  const palette = useChartPalette()
  if (data.length === 0) return <p className="text-sm text-muted-foreground text-center py-12">No category data.</p>
  const total = data.reduce((s, c) => s + c.seconds, 0)
  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie data={data} dataKey="seconds" nameKey="category" cx="50%" cy="50%" outerRadius="90%" label={false} stroke="none" isAnimationActive={false}>
          {data.map((_, i) => (
            <Cell key={i} fill={palette[i % palette.length]} />
          ))}
        </Pie>
        <Tooltip
          cursor={false}
          wrapperStyle={{ outline: 'none' }}
          isAnimationActive={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const d = payload[0].payload
            return (
              <ChartTooltip>
                <div className="font-medium">{d.category}</div>
                <div>{formatDuration(d.seconds)} ({total > 0 ? Math.round((d.seconds / total) * 100) : 0}%)</div>
                <div className="text-muted-foreground">{d.book_count} book{d.book_count !== 1 ? 's' : ''}</div>
              </ChartTooltip>
            )
          }}
        />
        <Legend formatter={(v) => <span style={{ fontSize: 11 }}>{v}</span>} wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
      </PieChart>
    </ResponsiveContainer>
  )
}

export function GenreOverTime({ data }: { data: StatsResponse['genre_over_time'] }) {
  const palette = useChartPalette()
  const categories = Array.from(new Set(data.flatMap((d) => Object.keys(d).filter((k) => k !== 'month')))).sort()
  if (categories.length === 0) return <p className="text-sm text-muted-foreground text-center py-12">No category data.</p>

  const chartData = data.map((d) => {
    const entry: Record<string, number | string> = { month: d.month as string }
    for (const cat of categories) entry[cat] = Math.round(((d[cat] as number) || 0) / 60)
    return entry
  })
  const formatMonth = (m: string) => {
    try {
      return new Date(m + '-01T00:00:00').toLocaleDateString(undefined, { month: 'short' })
    } catch {
      return m
    }
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <XAxis dataKey="month" tickFormatter={formatMonth} tick={{ fontSize: 10, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} width={36} axisLine={false} tickLine={false} tickFormatter={(v: number) => (v >= 60 ? `${Math.round(v / 60)}h` : `${v}m`)} />
        <Tooltip
          cursor={{ fill: 'rgba(255,255,255,0.04)' }}
          wrapperStyle={{ outline: 'none' }}
          isAnimationActive={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const month = payload[0]?.payload?.month
            return (
              <ChartTooltip>
                <div className="font-medium mb-1">{formatMonth(month)}</div>
                {payload
                  .filter((p) => (p.value as number) > 0)
                  .sort((a, b) => (b.value as number) - (a.value as number))
                  .map((p) => (
                    <div key={p.dataKey as string} className="flex items-center gap-1.5">
                      <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: p.color }} />
                      <span>{p.dataKey as string}: {(p.value as number) >= 60 ? `${Math.round((p.value as number) / 60)}h ${(p.value as number) % 60}m` : `${p.value}m`}</span>
                    </div>
                  ))}
              </ChartTooltip>
            )
          }}
        />
        <Legend formatter={(v) => <span style={{ fontSize: 11 }}>{v}</span>} wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
        {categories.map((cat, i) => (
          <Area key={cat} type="monotone" dataKey={cat} stackId="1" fill={palette[i % palette.length]} fillOpacity={0.6} stroke={palette[i % palette.length]} strokeWidth={1.5} />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}

type BookTimeSortKey = 'seconds' | 'sessions' | 'pages_turned'

export function PerBookTimeTable({ data }: { data: StatsResponse['per_book_time'] }) {
  const [sortKey, setSortKey] = useState<BookTimeSortKey>('seconds')
  const [sortAsc, setSortAsc] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const handleSort = (key: BookTimeSortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc)
    else {
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
            <tr key={b.book_id} className={cn('hover:bg-accent/30 transition-colors', idx % 2 === 0 ? 'bg-muted/20' : '')}>
              <td className="py-1.5 px-2">
                <div className="w-8 h-12 rounded bg-muted flex items-center justify-center shrink-0 overflow-hidden">
                  {b.has_cover ? <img src={`/api/books/${b.book_id}/cover`} alt="" className="w-full h-full object-cover" loading="lazy" /> : <FileText className="w-3.5 h-3.5 text-muted-foreground" />}
                </div>
              </td>
              <td className="py-1.5 px-2">
                <a href={`/books/${b.book_id}`} className="font-medium text-foreground hover:text-primary transition-colors line-clamp-1">{b.title}</a>
              </td>
              <td className="py-1.5 px-2 text-muted-foreground hidden sm:table-cell truncate max-w-[160px]">{b.author || '--'}</td>
              <td className="py-1.5 px-2 text-right text-muted-foreground tabular-nums">{formatDuration(b.seconds)}</td>
              <td className="py-1.5 px-2 text-right text-muted-foreground tabular-nums">{b.sessions}</td>
              <td className="py-1.5 px-2 text-right text-muted-foreground tabular-nums">{b.pages_turned}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {hasMore && (
        <button onClick={() => setExpanded(!expanded)} className="flex items-center justify-center gap-1.5 py-2 text-xs text-primary hover:text-primary/80 transition-colors w-full">
          <ChevronDown className={cn('w-3.5 h-3.5 transition-transform', expanded && 'rotate-180')} />
          <span>{expanded ? 'Show less' : `Show all (${sorted.length} books)`}</span>
        </button>
      )}
    </div>
  )
}
