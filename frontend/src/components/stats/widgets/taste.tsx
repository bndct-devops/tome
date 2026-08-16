// Taste-tab stats widgets (ratings) — chart/content-only, shared by StatsPage and the Lab.
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  ScatterChart,
  Scatter,
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts'
import { FileText } from 'lucide-react'
import { formatDuration } from '@/lib/utils'
import { StarRating } from '@/components/StarRating'
import { useChartColors } from '@/lib/useChartAccent'
import { ChartTooltip, type StatsResponse } from '@/components/stats/shared'

type Ratings = StatsResponse['ratings']

function Empty({ text = 'No ratings yet.' }: { text?: string }) {
  return <p className="text-sm text-muted-foreground text-center py-10">{text}</p>
}

function Stars({ value }: { value: number }) {
  // Half-star aware (ratings are 0.5 steps now); uses the themed rating token
  // like every other star row instead of hardcoded amber.
  return <StarRating value={value} readOnly starClassName="h-3 w-3" className="shrink-0" />
}

function Cover({ id, has, alt }: { id: number | null; has: boolean; alt: string }) {
  return (
    <div className="h-12 w-8 shrink-0 overflow-hidden rounded bg-muted flex items-center justify-center">
      {id && has ? (
        <img src={`/api/books/${id}/cover`} alt={alt} className="h-full w-full object-cover" loading="lazy"
          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }} />
      ) : (
        <FileText className="h-3.5 w-3.5 text-muted-foreground" />
      )}
    </div>
  )
}

// 1 ── How you rate: 1–5★ histogram ─────────────────────────────────────────────
export function RatingDistribution({ data }: { data: Ratings['distribution'] }) {
  const { accent, tick, cursor } = useChartColors()
  const total = data.reduce((s, d) => s + d.count, 0)
  if (total === 0) return <Empty />
  const chartData = data.map((d) => ({ label: `${d.rating}★`, count: d.count, rating: d.rating }))
  return (
    <ResponsiveContainer initialDimension={{ width: 1, height: 1 }} width="100%" height="100%">
      <BarChart data={chartData} margin={{ top: 6, right: 4, bottom: 0, left: 0 }}>
        <XAxis dataKey="label" tick={{ fontSize: 12, fill: tick }} axisLine={false} tickLine={false} />
        <YAxis allowDecimals={false} tick={{ fontSize: 10, fill: tick }} width={26} axisLine={false} tickLine={false} />
        <Tooltip
          cursor={cursor} wrapperStyle={{ outline: 'none' }} isAnimationActive={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const d = payload[0].payload
            return <ChartTooltip><div className="font-medium">{d.rating}★</div><div className="text-muted-foreground">{d.count} book{d.count !== 1 ? 's' : ''} ({Math.round((d.count / total) * 100)}%)</div></ChartTooltip>
          }}
        />
        <Bar dataKey="count" fill={accent} radius={[4, 4, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  )
}

// 2 ── Taste by genre: avg rating per book-type (radar, bars fallback) ──────────
export function TasteByGenre({ data }: { data: Ratings['by_category'] }) {
  const { accent, tick } = useChartColors()
  if (data.length === 0) return <Empty text="No rated books yet." />
  if (data.length < 3) {
    return (
      <div className="flex h-full flex-col justify-center gap-3">
        {data.map((d) => (
          <div key={d.category}>
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="truncate font-medium text-foreground">{d.category}</span>
              <span className="tabular-nums text-muted-foreground">{d.avg.toFixed(1)} · {d.count}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full" style={{ width: `${(d.avg / 5) * 100}%`, backgroundColor: accent }} />
            </div>
          </div>
        ))}
      </div>
    )
  }
  return (
    <ResponsiveContainer initialDimension={{ width: 1, height: 1 }} width="100%" height="100%">
      <RadarChart data={data} margin={{ top: 8, right: 28, bottom: 8, left: 28 }}>
        <PolarGrid stroke={tick} strokeOpacity={0.2} />
        <PolarAngleAxis dataKey="category" tick={{ fontSize: 10, fill: tick }} />
        <PolarRadiusAxis domain={[0, 5]} tickCount={6} tick={{ fontSize: 9, fill: tick }} angle={90} axisLine={false} />
        <Radar dataKey="avg" stroke={accent} fill={accent} fillOpacity={0.35} isAnimationActive={false} />
        <Tooltip
          wrapperStyle={{ outline: 'none' }} isAnimationActive={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const d = payload[0].payload
            return <ChartTooltip><div className="font-medium">{d.category}</div><div className="text-muted-foreground">avg {d.avg.toFixed(1)}★ · {d.count} book{d.count !== 1 ? 's' : ''}</div></ChartTooltip>
          }}
        />
      </RadarChart>
    </ResponsiveContainer>
  )
}

function RatedRow({ b }: { b: Ratings['books'][number] }) {
  return (
    <div className="flex items-center gap-2.5">
      <Cover id={b.book_id} has={b.has_cover} alt="" />
      <div className="min-w-0 flex-1">
        <a href={`/books/${b.book_id}`} className="line-clamp-1 text-sm font-medium text-foreground hover:text-primary transition-colors">{b.title}</a>
        {b.author && <p className="truncate text-xs text-muted-foreground">{b.author}</p>}
      </div>
      <Stars value={b.rating} />
    </div>
  )
}

// 3 ── Highest & lowest rated books ──────────────────────────────────────────────
export function TopRatedBooks({ books }: { books: Ratings['books'] }) {
  if (books.length === 0) return <Empty />
  const top = books.slice(0, 5)
  const low = books.length > 8 ? books.slice(-4).reverse() : []
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2.5">
        {low.length > 0 && <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Highest</p>}
        {top.map((b) => <RatedRow key={b.book_id} b={b} />)}
      </div>
      {low.length > 0 && (
        <div className="flex flex-col gap-2.5">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Lowest</p>
          {low.map((b) => <RatedRow key={b.book_id} b={b} />)}
        </div>
      )}
    </div>
  )
}

// 4 ── Rating vs time spent (scatter) ────────────────────────────────────────────
export function RatingVsTime({ books }: { books: Ratings['books'] }) {
  const { accent, tick, cursor } = useChartColors()
  const pts = books.filter((b) => b.seconds > 0).map((b) => ({ hours: +(b.seconds / 3600).toFixed(1), rating: b.rating, title: b.title }))
  if (pts.length === 0) return <Empty text="No rated books with reading time yet." />
  return (
    <ResponsiveContainer initialDimension={{ width: 1, height: 1 }} width="100%" height="100%">
      <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid stroke={tick} strokeOpacity={0.15} />
        <XAxis type="number" dataKey="hours" name="Hours" tick={{ fontSize: 10, fill: tick }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `${v}h`} />
        <YAxis type="number" dataKey="rating" domain={[0, 5.5]} ticks={[1, 2, 3, 4, 5]} tick={{ fontSize: 10, fill: tick }} width={24} axisLine={false} tickLine={false} />
        <Tooltip
          cursor={cursor} wrapperStyle={{ outline: 'none' }} isAnimationActive={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const d = payload[0].payload
            return <ChartTooltip><div className="line-clamp-1 max-w-[180px] font-medium">{d.title}</div><div className="text-muted-foreground">{d.rating}★ · {formatDuration(d.hours * 3600)}</div></ChartTooltip>
          }}
        />
        <Scatter data={pts} fill={accent} fillOpacity={0.75} isAnimationActive={false} />
      </ScatterChart>
    </ResponsiveContainer>
  )
}

// 5 ── Best-rated series ─────────────────────────────────────────────────────────
export function BestRatedSeries({ series }: { series: Ratings['series'] }) {
  if (series.length === 0) return <Empty text="No rated series yet." />
  return (
    <div className="flex flex-col gap-2.5">
      {series.slice(0, 12).map((s) => (
        <div key={s.series} className="flex items-center gap-2.5">
          <Cover id={s.sample_book_id} has={!!s.sample_book_id} alt="" />
          <p className="min-w-0 flex-1 line-clamp-1 text-sm font-medium text-foreground">{s.series}</p>
          <Stars value={s.rating} />
        </div>
      ))}
    </div>
  )
}

// 6 ── Rating trend over time ────────────────────────────────────────────────────
export function RatingTrend({ trend }: { trend: Ratings['trend'] }) {
  const { accent, tick, cursor } = useChartColors()
  if (trend.length < 2) return <Empty text="Rate a few books to see a trend." />
  const fmt = (d: string) => {
    try { return new Date(d + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', year: '2-digit' }) } catch { return d }
  }
  return (
    <ResponsiveContainer initialDimension={{ width: 1, height: 1 }} width="100%" height="100%">
      <LineChart data={trend} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={tick} strokeOpacity={0.15} vertical={false} />
        <XAxis dataKey="date" tickFormatter={fmt} tick={{ fontSize: 10, fill: tick }} axisLine={false} tickLine={false} minTickGap={28} />
        <YAxis domain={[0, 5.5]} ticks={[1, 2, 3, 4, 5]} tick={{ fontSize: 10, fill: tick }} width={24} axisLine={false} tickLine={false} />
        <Tooltip
          cursor={cursor} wrapperStyle={{ outline: 'none' }} isAnimationActive={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const d = payload[0].payload
            return <ChartTooltip><div className="font-medium">{d.rating}★</div><div className="text-muted-foreground">{fmt(d.date)}</div></ChartTooltip>
          }}
        />
        <Line type="monotone" dataKey="rating" stroke={accent} strokeWidth={2} dot={{ r: 2.5, fill: accent }} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}
