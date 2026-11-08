// Overview-tab stats widgets - chart-only (no card frame, no fixed height), so the
// same component renders inside a StatsPage ChartCard and inside a resizable Lab tile.
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  Area,
  XAxis,
  YAxis,
  Tooltip,
} from 'recharts'
import { FileText } from 'lucide-react'
import { cn, formatDate, formatDuration } from '@/lib/utils'
import { useChartColors } from '@/lib/useChartAccent'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChartTooltip, type StatsResponse } from '@/components/stats/shared'
import { axisDuration, dayIso, evenDateTicks, isoDay, niceDurationTicks } from '@/components/stats/axis'
import { coverSrc } from '@/lib/covers'

// Chart style a configurable widget can render as (per-tile setting in the Lab).
export type ChartKind = 'bar' | 'line' | 'area'

// Bare headline-stat body (value + sub) for use as a standalone dashboard tile  - 
// the tile itself provides the card frame + the label (in its header).
export function HeadlineStatBody({ value, unit, sub }: { value: string; unit?: string; sub?: string }) {
  // The Stat look without its label (the tile header is the label): display
  // number, optional unit, one foot line. The foot line is always present so
  // every number in the row sits on the same baseline; truncate over wrap so a
  // long value ("568h 28m") never spills onto a second line in a 1-row tile.
  return (
    // @container + the inline font size: a long value ("37h 20m") steps down
    // from 40px so it keeps clear air before the next tile in the strip.
    <div className="@container flex h-full flex-col">
      <div className={cn('flex min-w-0 items-baseline text-foreground', unit === '%' ? 'gap-0' : 'gap-1.5')}>
        <span
          className="truncate font-display text-[40px] font-bold leading-none"
          style={value.length > 4 ? { fontSize: `min(40px, calc((100cqw - 28px) / ${(value.length * 0.6).toFixed(2)}))` } : undefined}
        >{value}</span>
        {unit && <span className="shrink-0 text-[16px] font-semibold">{unit}</span>}
      </div>
      <p className="mt-1.5 truncate text-[14px] leading-[18px] text-muted-foreground">{sub ?? '\u00a0'}</p>
    </div>
  )
}

export function CurrentlyReading({ books }: { books: StatsResponse['books_in_progress'] }) {
  const { accent } = useChartColors()
  if (books.length === 0) {
    return (
      <div className="flex flex-col gap-1 py-2">
        <p className="text-[14px] text-muted-foreground"><Trans>No books in progress</Trans></p>
        <p className="text-[13px] text-muted-foreground/60"><Trans>Start reading one and it'll show up here</Trans></p>
      </div>
    )
  }
  // Compact rows (~40px each): a single book then fits one grid row, so the
  // autoH tile snugs down to it instead of rounding up to a half-empty 2-row
  // box - and still grows by a row for every extra ~2 books (2-up on sm+).
  return (
    <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
      {books.map((b) => (
        <a key={b.book_id} href={`/books/${b.book_id}`} className="group flex items-center gap-2.5 rounded-lg px-2 py-0.5 transition-colors hover:bg-accent/30">
          <div className="h-9 w-6 shrink-0 overflow-hidden rounded bg-muted flex items-center justify-center">
            {b.has_cover ? (
              <img src={coverSrc(b.book_id, { w: 160 })} alt="" className="h-full w-full object-cover" />
            ) : (
              <FileText className="h-3.5 w-3.5 text-muted-foreground" />
            )}
          </div>
          {/* Title and author share the first line, the bar gets the whole
              second line: every bar starts at the same x whatever the author
              length, and the fixed-width percent keeps the ends aligned too. */}
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-baseline gap-2">
              <span className="min-w-0 shrink truncate text-sm font-medium text-foreground transition-colors group-hover:text-primary">{b.title}</span>
              {b.author && <span className="min-w-0 max-w-[45%] shrink-0 truncate text-[13px] text-muted-foreground">{b.author}</span>}
            </div>
            <div className="mt-1.5 flex items-center gap-2">
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full transition-all" style={{ width: `${Math.min(b.progress, 100)}%`, backgroundColor: accent }} />
              </div>
              <span className="w-9 shrink-0 text-right text-[12px] proportional-nums text-muted-foreground">{Math.round(b.progress)}%</span>
            </div>
          </div>
        </a>
      ))}
    </div>
  )
}

// One-line duration y-tick - recharts wraps multi-word ticks like "1h 40m".
function DurationYTick({ x, y, payload, fill }: { x?: number; y?: number; payload?: { value?: number }; fill?: string }) {
  return (
    <text x={x} y={y} dy={3} textAnchor="end" fontSize={10} fill={fill}>
      {axisDuration(payload?.value ?? 0)}
    </text>
  )
}

export function ReadingTimePerDay({ daily, chartType = 'bar' }: { daily: StatsResponse['daily']; chartType?: ChartKind }) {
  const { accent, tick, cursor } = useChartColors()
  // Round-number y ticks and an even x interval: recharts' own picks land on
  // values like "1h 31m" and drop or crowd date labels.
  const yTicks = niceDurationTicks(Math.max(0, ...daily.map((d) => d.seconds)))
  const yWidth = Math.max(24, ...yTicks.map((v) => axisDuration(v).length * 6 + 10))
  const xTicks = daily.length ? evenDateTicks(daily[0].date, daily[daily.length - 1].date) : []
  return (
    <ResponsiveContainer initialDimension={{ width: 1, height: 1 }} width="100%" height="100%">
      <ComposedChart data={daily} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
        <XAxis dataKey="date" ticks={xTicks} interval={0} tickFormatter={formatDate} tick={{ fontSize: 10, fill: tick }} axisLine={false} tickLine={false} />
        <YAxis ticks={yTicks} domain={[0, yTicks[yTicks.length - 1]]} interval={0} tick={<DurationYTick fill={tick} />} width={yWidth} axisLine={false} tickLine={false} />
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

export function TopBooksByTime({ topBooks }: { topBooks: StatsResponse['top_books'] }) {
  const { accent } = useChartColors()
  if (topBooks.length === 0) {
    return <p className="py-2 text-[14px] text-muted-foreground"><Trans>No reading sessions recorded.</Trans></p>
  }
  // A ranked bar list rather than a chart: one book reads as one short row
  // instead of a single bar stretched over the whole tile.
  const max = Math.max(...topBooks.map((b) => b.seconds), 1)
  return (
    <div className="flex flex-col">
      {topBooks.map((b) => {
        const sessions = plural(b.sessions, { one: '# session', other: '# sessions' })
        const avg = b.sessions > 0 ? formatDuration(Math.round(b.seconds / b.sessions)) : null
        return (
          <a
            key={b.book_id}
            href={`/books/${b.book_id}`}
            title={avg ? t`${sessions}, avg ${avg}` : sessions}
            className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-b border-border/70 py-2 last:border-b-0"
          >
            <span className="truncate text-[14px] font-medium text-foreground transition-colors group-hover:text-primary">{b.title}</span>
            <span className="text-[13px] text-muted-foreground">{formatDuration(b.seconds)}</span>
            <span className="col-span-2 h-1.5 overflow-hidden rounded-full" style={{ background: `color-mix(in oklch, ${accent} 22%, transparent)` }}>
              <span className="block h-full rounded-full" style={{ width: `${Math.max(2, (b.seconds / max) * 100)}%`, backgroundColor: accent }} />
            </span>
          </a>
        )
      })}
      {topBooks.length === 1 && (
        <p className="pt-2 text-[13px] text-muted-foreground"><Trans>Only one book read in this range.</Trans></p>
      )}
    </div>
  )
}

// Opacity per intensity level: empty first, then four reading levels. The first
// reading level sits well clear of empty so a short day still reads in amber.
const ACTIVITY_LEVELS = [0.12, 0.4, 0.58, 0.78, 1]
function activityLevel(secs: number) {
  if (secs === 0) return 0
  if (secs < 900) return 1
  if (secs < 1800) return 2
  if (secs < 3600) return 3
  return 4
}

// The 365-day grid, sized from the tile: 53 week columns plus gaps fill the
// available width, capped at a sensible cell size on very wide tiles.
function ActivityGrid({ data, maxHeight }: { data: { date: string; seconds: number }[]; maxHeight: number }) {
  const { accent, tick } = useChartColors()
  const [tooltip, setTooltip] = useState<{ x: number; y: number; date: string; seconds: number } | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const map = new Map(data.map((d) => [d.date, d.seconds]))
  // Local-date keys, NOT toISOString (= UTC): the backend buckets sessions into
  // local days via tz_offset.
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

  const LABEL_W = 22
  const TOP = 18
  const MIN_CELL = 8
  const MAX_CELL = 22
  const cols = weeks.length
  // Gap scales a little with the cell so big cells do not look crammed.
  const fitW = width > 0 ? (width - LABEL_W) / (cols * 1.25 - 0.25) : 12
  // In a fixed-height tile the grid also has to fit the height (7 rows).
  const fitH = maxHeight > 0 ? (maxHeight - TOP) / (7 * 1.25 - 0.25) : Infinity
  // Columns always fill the width; when the tile is short the rows get a
  // little flatter rather than leaving a third of the tile empty.
  const cellW = Math.max(MIN_CELL, Math.min(MAX_CELL, fitW))
  const cellH = Math.max(MIN_CELL, Math.min(cellW, fitH))
  const gap = Math.min(cellW, cellH) * 0.25
  const stepX = cellW + gap
  const stepY = cellH + gap
  const gridW = LABEL_W + cols * stepX - gap
  const svgH = TOP + 7 * stepY - gap

  const monthLabels: { x: number; label: string }[] = []
  let lastMonth = ''
  weeks.forEach((wk, wi) => {
    const firstDay = wk.find((d) => d !== null)
    if (firstDay) {
      const m = new Date(firstDay + 'T00:00:00').toLocaleDateString(undefined, { month: 'short' })
      if (m !== lastMonth) {
        monthLabels.push({ x: LABEL_W + wi * stepX, label: m })
        lastMonth = m
      }
    }
  })

  return (
    <div ref={boxRef} className="relative w-full overflow-x-auto">
      <svg width={gridW} height={svgH} style={{ display: 'block', overflow: 'visible' }}>
        {monthLabels.map(({ x, label }) => (
          <text key={label + x} x={Math.min(x, gridW - 18)} y={10} fontSize={10} fill={tick}>{label}</text>
        ))}
        {['M', '', 'W', '', 'F', '', ''].map((d, i) =>
          d ? <text key={i} x={0} y={TOP + i * stepY + cellH / 2 + 3.5} fontSize={10} fill={tick}>{d}</text> : null,
        )}
        {weeks.map((wk, wi) =>
          wk.map((day, di) => {
            if (!day) return null
            const secs = map.get(day) ?? 0
            const lvl = activityLevel(secs)
            return (
              <rect
                key={day}
                x={LABEL_W + wi * stepX}
                y={TOP + di * stepY}
                width={cellW}
                height={cellH}
                rx={Math.min(3, Math.min(cellW, cellH) / 5)}
                fill={lvl === 0 ? tick : accent}
                fillOpacity={ACTIVITY_LEVELS[lvl]}
                onMouseEnter={(e) => setTooltip({ x: e.clientX, y: e.clientY, date: day, seconds: secs })}
                onMouseLeave={() => setTooltip(null)}
              />
            )
          }),
        )}
      </svg>
      {tooltip &&
        createPortal(
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

export function ReadingActivity365({ heatmap }: { heatmap: StatsResponse['heatmap_daily'] }) {
  const { accent, tick } = useChartColors()
  // The tile body has a definite height; the grid gets what the legend leaves.
  // Outside a sized box the measured height is the content's own, which keeps
  // the width-fitted size (heights under 60px are ignored).
  const outerRef = useRef<HTMLDivElement>(null)
  const [outerH, setOuterH] = useState(0)
  useEffect(() => {
    const el = outerRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setOuterH(el.clientHeight))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const LEGEND_H = 24
  const maxHeight = outerH >= 60 ? outerH - LEGEND_H : 0
  return (
    <div ref={outerRef} className="flex h-full flex-col">
      <ActivityGrid data={heatmap} maxHeight={maxHeight} />
      <div className="flex items-center gap-2 justify-end mt-1">
        <span className="text-[12px] text-muted-foreground"><Trans>Less</Trans></span>
        {ACTIVITY_LEVELS.map((op, i) => (
          <div
            key={i}
            className="w-3 h-3 rounded-sm border border-border/30"
            style={i === 0 ? { backgroundColor: tick, opacity: op } : { backgroundColor: accent, opacity: op }}
          />
        ))}
        <span className="text-[12px] text-muted-foreground"><Trans>More</Trans></span>
      </div>
    </div>
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
      return { date, day: isoDay(date), titles, daily: titles.length, count }
    })

  // A single day of finishes draws one dot in an empty box: say it instead.
  if (cumulative.length === 0) {
    return <p className="py-2 text-[14px] text-muted-foreground"><Trans>Nothing finished in this range.</Trans></p>
  }
  if (cumulative.length === 1) {
    const only = cumulative[0]
    const when = formatDate(only.date)
    const titles = only.titles.join(', ')
    return (
      <div className="py-1">
        <p className="text-[15px] text-foreground">{titles}</p>
        <p className="mt-1 text-[13px] text-muted-foreground">
          {only.daily === 1
            ? t`Finished ${when}, the only finish in this range.`
            : (() => { const n = only.daily; return t`${n} finished ${when}, the only finishes in this range.` })()}
        </p>
      </div>
    )
  }

  // Bar mode shows finishes per day; line/area show the cumulative count.
  const dataKey = chartType === 'bar' ? 'daily' : 'count'
  const firstDay = cumulative[0].day
  const lastDay = cumulative[cumulative.length - 1].day

  return (
    <div className="h-full min-h-[176px]">
    <ResponsiveContainer initialDimension={{ width: 1, height: 1 }} width="100%" height="100%">
      <ComposedChart data={cumulative} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
        {/* A time axis, not one slot per finish: finishes a day apart and a
            month apart no longer sit the same distance from each other. */}
        <XAxis
          dataKey="day"
          type="number"
          domain={[firstDay, lastDay]}
          ticks={evenDateTicks(cumulative[0].date, cumulative[cumulative.length - 1].date).map(isoDay)}
          interval={0}
          tickFormatter={(v: number) => formatDate(dayIso(v))}
          tick={{ fontSize: 10, fill: tick }}
          axisLine={false}
          tickLine={false}
        />
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
    </div>
  )
}

// Latest finishes, newest first, with covers - list tile (scrolls in the Lab).
export function RecentlyFinished({ booksFinished }: { booksFinished: StatsResponse['books_finished'] }) {
  const rows = [...booksFinished].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 12)
  if (rows.length === 0) {
    return <p className="py-2 text-[14px] text-muted-foreground"><Trans>Nothing finished in this range.</Trans></p>
  }
  return (
    <div className="flex flex-col gap-1">
      {rows.map((b, i) => (
        <a key={`${b.book_id}-${i}`} href={`/books/${b.book_id}`} className="group flex items-center gap-3 rounded-lg p-1.5 hover:bg-accent/30 transition-colors">
          <div className="w-7 h-10 rounded bg-muted flex items-center justify-center shrink-0 overflow-hidden">
            <img
              src={coverSrc(b.book_id, { w: 160 })}
              alt=""
              className="w-full h-full object-cover"
              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          </div>
          <span className="flex-1 min-w-0 truncate text-sm font-medium text-foreground group-hover:text-primary transition-colors">{b.title}</span>
          <span className="shrink-0 text-[13px] text-muted-foreground">{formatDate(b.date)}</span>
        </a>
      ))}
    </div>
  )
}

// Current month at a glance - days with reading filled, today outlined.
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
        <span className="text-[13px] font-semibold text-foreground">{now.toLocaleDateString(undefined, { month: 'long' })}</span>
        <span className="text-[12px] text-muted-foreground">{plural(readDays, { one: '# day read', other: '# days read' })}</span>
      </div>
      <div className="grid flex-1 grid-cols-7 gap-1 content-start">
        {Array.from({ length: 7 }, (_, i) => new Date(2024, 0, 1 + i).toLocaleDateString(undefined, { weekday: 'narrow' })).map((w, i) => (
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
              title={`${formatDate(dIso)}${secs > 0 ? `: ${formatDuration(secs)}` : ''}`}
              className={cn(
                'flex aspect-square items-center justify-center rounded text-[9px]',
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
