// Batch-2 "insights" stats widgets — lifetime, records, TBR, reading clock, language.
import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip } from 'recharts'
import { Clock, FileText, BookCheck, Activity, Calendar, Flame, Timer, Sun, type LucideIcon } from 'lucide-react'
import { formatDuration } from '@/lib/utils'
import { useChartColors } from '@/lib/useChartAccent'
import { useChartPalette } from '@/lib/useChartPalette'
import { ChartTooltip, type StatsResponse } from '@/components/stats/shared'

const fmtDay = (d: string | null) =>
  d ? new Date(d + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '--'

const Empty = () => <p className="py-10 text-center text-sm text-muted-foreground">No data yet.</p>

// ── Lifetime totals (all-time, range-independent) ───────────────────────────────
export function LifetimeTotals({ data }: { data: StatsResponse['lifetime'] }) {
  if (!data) return <Empty />
  const items: { icon: LucideIcon; label: string; value: string }[] = [
    { icon: Clock, label: 'Total Time', value: formatDuration(data.seconds) },
    { icon: FileText, label: 'Pages', value: data.pages.toLocaleString() },
    { icon: BookCheck, label: 'Books Finished', value: String(data.books_finished) },
    { icon: Activity, label: 'Sessions', value: String(data.sessions) },
    { icon: Calendar, label: 'Active Days', value: String(data.active_days) },
    { icon: Flame, label: 'Longest Streak', value: `${data.longest_streak_days}d` },
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

// ── Personal records ────────────────────────────────────────────────────────────
export function PersonalRecords({ data }: { data: StatsResponse['records'] }) {
  if (!data) return <Empty />
  const rows: { icon: LucideIcon; label: string; value: string; sub: string | null }[] = [
    { icon: Timer, label: 'Longest session', value: formatDuration(data.longest_session_seconds), sub: data.longest_session_title },
    { icon: Sun, label: 'Biggest reading day', value: formatDuration(data.biggest_day_seconds), sub: fmtDay(data.biggest_day_date) },
    { icon: FileText, label: 'Most pages in a day', value: data.most_pages_day.toLocaleString(), sub: fmtDay(data.most_pages_date) },
  ]
  return (
    <div className="flex flex-col gap-2.5">
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-3 rounded-lg border border-border bg-muted/20 px-3 py-2">
          <r.icon className="h-4 w-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <p className="text-xs text-muted-foreground">{r.label}</p>
            {r.sub && <p className="truncate text-[11px] text-muted-foreground/70">{r.sub}</p>}
          </div>
          <p className="shrink-0 text-base font-bold tabular-nums text-foreground">{r.value}</p>
        </div>
      ))}
    </div>
  )
}

// ── Library completion / TBR ────────────────────────────────────────────────────
export function LibraryCompletion({ data }: { data: StatsResponse['tbr'] }) {
  const { accent } = useChartColors()
  if (!data) return <Empty />
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-bold text-foreground">{data.pct}%</span>
        <span className="text-xs text-muted-foreground">{data.read} of {data.owned} owned read{data.reading ? ` · ${data.reading} reading` : ''}{data.want_to_read ? ` · ${data.want_to_read} want to read` : ''}</span>
      </div>
      <div className="flex flex-col gap-2">
        {data.by_type.map((t) => (
          <div key={t.type}>
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="truncate font-medium text-foreground">{t.type}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">{t.read}/{t.owned} · {t.pct}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full" style={{ width: `${Math.min(t.pct, 100)}%`, backgroundColor: accent }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Reading clock — 24h radial of when you read ─────────────────────────────────
export function ReadingClock({ data }: { data: StatsResponse['hour_dow_heatmap'] }) {
  const { accent, tick } = useChartColors()
  if (!data) return <Empty />
  const byHour = Array(24).fill(0) as number[]
  for (const c of data) byHour[c.hour] += c.seconds
  const max = Math.max(...byHour, 1)
  if (max <= 1) return <p className="py-10 text-center text-sm text-muted-foreground">No session-time data yet.</p>

  const SIZE = 200, cx = SIZE / 2, cy = SIZE / 2, r0 = 24, rmax = 74
  const total = byHour.reduce((s, v) => s + v, 0)
  const peak = byHour.indexOf(Math.max(...byHour))
  const ang = (h: number) => (h / 24) * 2 * Math.PI - Math.PI / 2 // 0h at top, clockwise
  return (
    <div className="flex h-full flex-col items-center justify-center">
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="h-full max-h-[220px] w-auto">
        <circle cx={cx} cy={cy} r={rmax} fill="none" stroke={tick} strokeOpacity={0.15} />
        {byHour.map((v, h) => {
          const len = r0 + (v / max) * (rmax - r0)
          const a = ang(h)
          return (
            <line key={h}
              x1={cx + r0 * Math.cos(a)} y1={cy + r0 * Math.sin(a)}
              x2={cx + len * Math.cos(a)} y2={cy + len * Math.sin(a)}
              stroke={accent} strokeOpacity={v === 0 ? 0.12 : 0.85} strokeWidth={4} strokeLinecap="round" />
          )
        })}
        {[0, 6, 12, 18].map((h) => {
          const a = ang(h), lr = rmax + 10
          return <text key={h} x={cx + lr * Math.cos(a)} y={cy + lr * Math.sin(a) + 3} fontSize={9} fill={tick} textAnchor="middle">{h}h</text>
        })}
      </svg>
      <p className="text-[11px] text-muted-foreground">Peak hour: <span className="font-medium text-foreground">{peak}:00–{(peak + 1) % 24}:00</span> · {formatDuration(total)} total</p>
    </div>
  )
}

// ── Reading time by language ────────────────────────────────────────────────────
export function ReadingByLanguage({ data }: { data: StatsResponse['language'] }) {
  const palette = useChartPalette()
  const pts = (data ?? []).filter((l) => l.seconds > 0)
  if (pts.length === 0) return <p className="py-12 text-center text-sm text-muted-foreground">No reading-time data yet.</p>
  if (pts.length === 1) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1">
        <p className="text-xl font-bold text-foreground">{pts[0].language}</p>
        <p className="text-xs text-muted-foreground">{formatDuration(pts[0].seconds)} · {pts[0].books} books — your only language</p>
      </div>
    )
  }
  const total = pts.reduce((s, l) => s + l.seconds, 0)
  return (
    <ResponsiveContainer initialDimension={{ width: 1, height: 1 }} width="100%" height="100%">
      <PieChart>
        <Pie data={pts} dataKey="seconds" nameKey="language" cx="50%" cy="50%" innerRadius="45%" outerRadius="85%" label={false} stroke="none" isAnimationActive={false}>
          {pts.map((_, i) => <Cell key={i} fill={palette[i % palette.length]} />)}
        </Pie>
        <Tooltip
          wrapperStyle={{ outline: 'none' }} isAnimationActive={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const d = payload[0].payload
            return <ChartTooltip><div className="font-medium">{d.language}</div><div>{formatDuration(d.seconds)} ({Math.round((d.seconds / total) * 100)}%)</div><div className="text-muted-foreground">{d.books} book{d.books !== 1 ? 's' : ''}</div></ChartTooltip>
          }}
        />
      </PieChart>
    </ResponsiveContainer>
  )
}
