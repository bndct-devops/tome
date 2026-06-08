// Habits-tab stats widgets — chart/content-only, shared by StatsPage and the Lab.
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
} from 'recharts'
import { TrendingUp, TrendingDown, Zap, Minus, FileText, Loader2 } from 'lucide-react'
import { cn, formatDate, formatDuration } from '@/lib/utils'
import { useChartAccent } from '@/lib/useChartAccent'
import { HourDowHeatmap } from '@/components/stats/HourDowHeatmap'
import { ChartTooltip, type StatsResponse, type CompletionEstimate } from '@/components/stats/shared'

const PACE_COLOR = '#10b981'

export function HourDowCard({ data }: { data: StatsResponse['hour_dow_heatmap'] }) {
  const accent = useChartAccent()
  return (
    <>
      <HourDowHeatmap data={data} />
      <div className="flex items-center gap-2 justify-end mt-1">
        <span className="text-[10px] text-muted-foreground">Less</span>
        {[0.15, 0.37, 0.58, 0.79, 1.0].map((o) => (
          <div key={o} className="w-3 h-3 rounded-sm border border-border/30" style={{ backgroundColor: accent, opacity: o }} />
        ))}
        <span className="text-[10px] text-muted-foreground">More</span>
      </div>
    </>
  )
}

export function SessionTimeline({ sessions }: { sessions: StatsResponse['session_timeline'] }) {
  const accent = useChartAccent()
  const grouped: Record<string, StatsResponse['session_timeline']> = {}
  for (const s of sessions) {
    const d = s.started_at.slice(0, 10)
    if (!grouped[d]) grouped[d] = []
    grouped[d].push(s)
  }
  return (
    <div className="flex flex-col gap-1">
      {Object.entries(grouped)
        .sort(([a], [b]) => b.localeCompare(a))
        .slice(0, 14)
        .map(([dateStr, daySessions]) => (
          <div key={dateStr} className="flex items-center gap-3 py-1">
            <span className="text-[10px] text-muted-foreground w-16 shrink-0 text-right">{formatDate(dateStr)}</span>
            <div className="flex-1 relative h-6 bg-muted/30 rounded overflow-hidden">
              {daySessions.map((s) => {
                const start = new Date(s.started_at)
                const end = new Date(s.ended_at)
                const dayStart = start.getHours() * 60 + start.getMinutes()
                const dayEnd = end.getHours() * 60 + end.getMinutes()
                const left = (dayStart / 1440) * 100
                const width = Math.max(((dayEnd - dayStart) / 1440) * 100, 0.8)
                return (
                  <div
                    key={s.id}
                    className="absolute top-0.5 bottom-0.5 rounded-sm transition-colors"
                    style={{ left: `${left}%`, width: `${width}%`, backgroundColor: accent, opacity: 0.7 }}
                    title={`${s.title} — ${start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} to ${end.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} (${formatDuration(s.duration_seconds)})`}
                  />
                )
              })}
              {[6, 12, 18].map((h) => (
                <div key={h} className="absolute top-0 bottom-0 border-l border-border/40" style={{ left: `${(h / 24) * 100}%` }} />
              ))}
            </div>
            <div className="flex gap-3 text-[9px] text-muted-foreground shrink-0 w-20">
              <span>6</span><span>12</span><span>18</span><span>24</span>
            </div>
          </div>
        ))}
    </div>
  )
}

export function ReadingPaceChart({ pace }: { pace: StatsResponse['reading_pace'] }) {
  if (pace.length === 0) return <p className="text-sm text-muted-foreground text-center py-12">No paced sessions.</p>
  const avg = pace.reduce((s, p) => s + p.pages_per_min, 0) / pace.length
  return (
    <div className="flex h-full flex-col">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={[...pace].reverse()} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 10, fill: '#94a3b8' }} interval="preserveStartEnd" axisLine={false} tickLine={false} />
          <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} width={36} axisLine={false} tickLine={false} tickFormatter={(v: number) => v.toFixed(1)} />
          <Tooltip
            cursor={{ fill: 'rgba(255,255,255,0.04)' }}
            wrapperStyle={{ outline: 'none' }}
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
          <Area dataKey="pages_per_min" fill={PACE_COLOR} fillOpacity={0.15} stroke={PACE_COLOR} strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
      <p className="text-xs text-muted-foreground text-center shrink-0">avg {avg.toFixed(1)} pages/min</p>
    </div>
  )
}

export function ReadingSpeedTrend({ pace }: { pace: StatsResponse['reading_pace'] }) {
  if (pace.length < 4) return <p className="text-sm text-muted-foreground text-center py-12">Not enough sessions yet.</p>
  const paceData = [...pace].reverse()
  const half = Math.floor(paceData.length / 2)
  const avg = (arr: typeof paceData) => arr.reduce((s, p) => s + p.pages_per_min, 0) / arr.length
  const firstAvg = avg(paceData.slice(0, half))
  const secondAvg = avg(paceData.slice(half))
  const pctDiff = firstAvg > 0 ? Math.round(((secondAvg - firstAvg) / firstAvg) * 100) : 0
  const trending = pctDiff > 3 ? 'up' : pctDiff < -3 ? 'down' : 'steady'
  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center gap-2 shrink-0">
        {trending === 'up' ? <TrendingUp className="w-4 h-4 text-emerald-500" /> : trending === 'down' ? <TrendingDown className="w-4 h-4 text-red-500" /> : <Zap className="w-4 h-4 text-muted-foreground" />}
        <span className={cn('text-sm font-semibold', trending === 'up' ? 'text-emerald-500' : trending === 'down' ? 'text-red-500' : 'text-muted-foreground')}>
          {trending === 'up' ? `Reading speed up ${pctDiff}%` : trending === 'down' ? `Reading speed down ${Math.abs(pctDiff)}%` : 'Reading speed steady'}
        </span>
        <span className="text-xs text-muted-foreground ml-1">({secondAvg.toFixed(1)} vs {firstAvg.toFixed(1)} pages/min)</span>
      </div>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={paceData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 10, fill: '#94a3b8' }} interval="preserveStartEnd" axisLine={false} tickLine={false} />
          <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} width={36} axisLine={false} tickLine={false} tickFormatter={(v: number) => v.toFixed(1)} />
          <Tooltip
            cursor={{ fill: 'rgba(255,255,255,0.04)' }}
            wrapperStyle={{ outline: 'none' }}
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
          <Area dataKey="pages_per_min" fill={PACE_COLOR} fillOpacity={0.15} stroke={PACE_COLOR} strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

export function CompletionEstimatesList({ estimates }: { estimates: CompletionEstimate[] | null }) {
  const accent = useChartAccent()
  if (estimates === null) {
    return (
      <div className="flex justify-center py-10">
        <Loader2 className="w-5 h-5 animate-spin text-primary" />
      </div>
    )
  }
  if (estimates.length === 0) {
    return <p className="text-sm text-muted-foreground text-center py-8">No books currently in progress.</p>
  }
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {estimates.map((est) => (
        <a key={est.book_id} href={`/books/${est.book_id}`} className="group flex items-start gap-3 rounded-lg p-2 hover:bg-accent/30 transition-colors">
          <div className="w-9 h-12 rounded bg-muted flex items-center justify-center shrink-0 overflow-hidden">
            {est.has_cover ? <img src={`/api/books/${est.book_id}/cover`} alt="" className="w-full h-full object-cover" /> : <FileText className="w-4 h-4 text-muted-foreground" />}
          </div>
          <div className="flex-1 min-w-0">
            <p className={cn('text-sm font-medium truncate group-hover:text-primary transition-colors', est.confidence === 'low' ? 'text-muted-foreground' : 'text-foreground')}>{est.title}</p>
            {est.author && <p className="text-xs text-muted-foreground truncate">{est.author}</p>}
            <div className="mt-2 flex items-center gap-2">
              <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                <div className="h-full rounded-full" style={{ width: `${Math.min(est.progress, 100)}%`, backgroundColor: accent }} />
              </div>
              <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">{est.progress}%</span>
            </div>
            <p className={cn('text-xs mt-1.5', est.confidence === 'high' ? 'text-foreground' : est.confidence === 'medium' ? 'text-muted-foreground' : 'text-muted-foreground/60')}>
              {est.estimated_days != null ? `~${est.estimated_days} day${est.estimated_days !== 1 ? 's' : ''} remaining` : 'Just started'}
              {est.confidence === 'low' && est.estimated_days != null && <span className="ml-1 text-muted-foreground/50">(low confidence)</span>}
            </p>
          </div>
        </a>
      ))}
    </div>
  )
}

export function PeriodComparison({ comparison }: { comparison: NonNullable<StatsResponse['period_comparison']> }) {
  const up = comparison.pct_change !== null && comparison.pct_change > 0
  const down = comparison.pct_change !== null && comparison.pct_change < 0
  return (
    <div className="flex h-full items-center gap-4">
      <div className={cn('p-2 rounded-lg shrink-0', up ? 'bg-emerald-500/10' : down ? 'bg-red-500/10' : 'bg-muted')}>
        {comparison.pct_change === null ? <Minus className="w-5 h-5 text-muted-foreground" /> : up ? <TrendingUp className="w-5 h-5 text-emerald-500" /> : down ? <TrendingDown className="w-5 h-5 text-red-500" /> : <Minus className="w-5 h-5 text-muted-foreground" />}
      </div>
      <div className="flex-1 min-w-0">
        <p className={cn('text-lg font-bold', up ? 'text-emerald-500' : down ? 'text-red-500' : 'text-muted-foreground')}>
          {comparison.pct_change === null
            ? 'No previous data to compare'
            : comparison.pct_change === 0 && comparison.current_seconds === 0
            ? 'No reading data'
            : comparison.pct_change === 0
            ? 'Same as previous period'
            : up
            ? `${comparison.pct_change}% more reading this period`
            : `${Math.abs(comparison.pct_change!)}% less reading this period`}
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          This period: <span className="text-foreground font-medium">{formatDuration(comparison.current_seconds)}</span>
          {comparison.pct_change !== null && (
            <> vs previous: <span className="text-foreground font-medium">{formatDuration(comparison.previous_seconds)}</span></>
          )}
        </p>
      </div>
    </div>
  )
}

export function MonthlyComparison({ monthly }: { monthly: StatsResponse['monthly_comparison'] }) {
  const accent = useChartAccent()
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={monthly} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
        <YAxis yAxisId="hours" tick={{ fontSize: 10, fill: '#94a3b8' }} width={36} axisLine={false} tickLine={false} tickFormatter={(v: number) => `${v}h`} />
        <YAxis yAxisId="books" orientation="right" tick={{ fontSize: 10, fill: '#94a3b8' }} width={30} axisLine={false} tickLine={false} allowDecimals={false} />
        <Tooltip
          cursor={{ fill: 'rgba(255,255,255,0.04)' }}
          wrapperStyle={{ outline: 'none' }}
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
        <Legend formatter={(v) => <span style={{ fontSize: 11 }}>{v}</span>} wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
        <Bar yAxisId="hours" dataKey="reading_hours" name="Reading Hours" fill={accent} fillOpacity={0.85} radius={[3, 3, 0, 0]} />
        <Bar yAxisId="books" dataKey="books_finished" name="Books Finished" fill={accent} fillOpacity={0.45} radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}
