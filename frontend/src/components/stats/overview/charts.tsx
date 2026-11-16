// The Overview's three charts, drawn as plain SVG on whole pixels at a width the
// page measures before its first paint, so each chart's box is final at once and
// nothing resizes when data arrives. Data shaping lives in model.ts.
// Hover: every mark has a tip (kit/tip.ts); each chart is one focus stop with
// arrow keys. Entry: marks carry the motion kit's classes (kit/motion.ts); the
// section around the chart decides when they play.
import type { CSSProperties } from 'react'
import { formatDuration } from '@/lib/utils'
import { t, plural } from '@lingui/core/macro'
import { K, stagger } from '@/components/stats/kit/motion'
import { useTips } from '@/components/stats/kit/tip'
import { columns, type Bar, type HourGrid, type YearModel } from './model'

const fmtShort = (iso: string) => new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
const fmtLong = (iso: string) => new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
/** "3 sessions · 120 pages" (pages left out when none were counted). */
function sessionsLine(sessions: number, pages?: number) {
  const s = plural(sessions, { one: '# session', other: '# sessions' })
  if (!pages) return s
  const p = plural(pages, { one: '# page', other: '# pages' })
  return `${s} · ${p}`
}
const hh = (h: number) => `${String(h % 24).padStart(2, '0')}:00`
// Onest at 10-11 px: about 0.545 em per character, enough to keep a label clear of a bar.
const textW = (s: string, px: number) => Math.ceil(s.length * px * 0.545)

const LV = ['var(--k-lv0)', 'var(--k-lv1)', 'var(--k-lv2)', 'var(--k-lv3)', 'var(--k-lv4)', 'var(--k-lv5)']
const GRID = 'color-mix(in oklch, var(--border) 70%, transparent)'
const TICK = 'color-mix(in oklch, var(--muted-foreground) 60%, transparent)'

// The average's label appears as its line finishes drawing.
const AVG_LABEL = { '--k-d': '460ms' } as CSSProperties

const roundedTop = (x: number, yt: number, w: number, h: number, r: number) =>
  // eslint-disable-next-line lingui/no-unlocalized-strings -- SVG path
  `M${x},${yt + h} V${yt + r} Q${x},${yt} ${x + r},${yt} H${x + w - r} Q${x + w},${yt} ${x + w},${yt + r} V${yt + h} Z`

/** The plot area of a chart that has no data yet: a block in the chart's own box. */
function Placeholder({ W, H, x = 0, y = 0, w, h }: { W: number; H: number; x?: number; y?: number; w?: number; h?: number }) {
  return (
    <svg className="block" width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
      <rect x={x} y={y} width={w ?? W - x} height={h ?? H - y} rx={8} fill="var(--muted)" />
    </svg>
  )
}

// ── Reading time per day or week ──────────────────────────────────────────────
// The average's label sits inside the plot at the right end, above the dashed
// line, unless a bar would run through it; then it moves to a key above the plot.
export function BarsChart({ W, H, bars, avg, monthStarts, nDays, weekly, loading }: {
  W: number
  H: number
  bars: Bar[]
  avg: number
  monthStarts: { index: number; label: string }[]
  nDays: number
  weekly: boolean
  loading?: boolean
}) {
  const padL = 30, padB = 24
  const tips = useTips((i, el) => {
    const b = bars[i]
    if (!b) return null
    const from = fmtShort(b.start), to = fmtShort(b.end)
    const title = b.days === 1 ? fmtLong(b.start) : t`${from} to ${to}`
    const note = b.seconds > 0 ? sessionsLine(b.sessions, b.pages) : undefined
    const today = b.current ? (b.days === 1 ? t`today, still counting` : t`includes today`) : undefined
    return { title, value: b.seconds > 0 ? formatDuration(b.seconds) : t`No reading`, note: [note, today].filter(Boolean).join(' · ') || undefined, anchor: el.nextElementSibling ?? el }
  })
  if (loading || W <= padL) return <Placeholder W={Math.max(W, 1)} H={H} x={padL} y={10} h={H - 10 - padB} />
  const avgLabel = formatDuration(Math.round(avg))
  const avgWord = t({ message: 'avg', comment: 'Short for average, before a duration on a chart' })
  const avgText = `${avgWord} ${avgLabel}`
  const labW = textW(avgText, 10) + 2
  const layout = (padT: number) => {
    const plotW = W - padL, plotH = H - padT - padB
    const maxV = Math.max(1, ...bars.map((b) => b.seconds), avg)
    const hours = maxV / 3600
    const STEPS = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60, 80, 100]
    const step = [...STEPS].reverse().find((s) => Math.floor(hours / s) >= 2) ?? 1
    const top = maxV * 1.08
    const y = (v: number) => padT + plotH - (v / top) * plotH
    const slot = plotW / Math.max(1, bars.length)
    const bw = Math.max(1, Math.round(Math.min(slot * 0.52, 40)))
    const rects = bars.map((b, i) => ({ x: Math.round(padL + slot * i + (slot - bw) / 2), yt: Math.round(y(b.seconds)), w: bw }))
    return { plotW, step, top, y, rects }
  }
  let padT = 10
  let g = layout(padT)
  const ay0 = Math.round(g.y(avg)) + 0.5
  const box = { x0: W - labW - 2, x1: W, y0: ay0 - 16, y1: ay0 - 2 }
  const inside = avg > 0 && !g.rects.some((r) => r.x < box.x1 && r.x + r.w > box.x0 && r.yt < box.y1)
  if (avg > 0 && !inside) { padT = 24; g = layout(padT) }
  const { plotW, step, top, y, rects } = g
  const slotW = plotW / Math.max(1, bars.length)
  const gridLines: number[] = []
  // Hour lines only when the scale is at least an hour; short ranges keep the base line.
  if (top >= 3600) for (let v = step; v * 3600 <= top; v += step) gridLines.push(v)
  const by = Math.round(y(0)) + 0.5
  const ay = Math.round(y(avg)) + 0.5
  const label = weekly ? t`Reading time per week` : t`Reading time per day`
  return (
    <svg {...tips} className="block fill-muted-foreground text-[11px] tabular-nums" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      {gridLines.map((v) => {
        const yy = Math.round(y(v * 3600)) + 0.5
        return (
          <g key={v}>
            <line x1={padL} x2={W} y1={yy} y2={yy} stroke={GRID} shapeRendering="crispEdges" />
            <text x={padL - 8} y={yy + 4} textAnchor="end">{v}h</text>
          </g>
        )
      })}
      <line x1={padL} x2={W} y1={by} y2={by} stroke="var(--border)" shapeRendering="crispEdges" />
      <text x={padL - 8} y={by + 4} textAnchor="end">0</text>
      {bars.map((b, i) => {
        const { x, yt, w } = rects[i]
        const h = Math.round(y(0)) - yt
        // The whole column answers the pointer (washed on hover); the bar on top ignores it.
        return (
          <g key={b.start}>
            <rect className="k-hit" data-tip-i={i} x={padL + slotW * i} y={padT} width={slotW} height={Math.round(y(0)) - padT} />
            {h > 0 && <path className={`${K.grow} k-nohit`} style={stagger(i, bars.length)} d={roundedTop(x, yt, w, h, Math.min(4, h))} fill="var(--chart-accent)" fillOpacity={b.current ? 0.45 : 1} />}
          </g>
        )
      })}
      {monthStarts.map((m) => {
        const x = Math.round(padL + (m.index / nDays) * plotW) + 0.5
        return (
          <g key={m.index}>
            <line x1={x} x2={x} y1={by} y2={by + 5} stroke={TICK} shapeRendering="crispEdges" />
            <text x={x + 4} y={H - 6}>{m.label}</text>
          </g>
        )
      })}
      {avg > 0 && (
        <>
          <line className={`${K.wipe} k-nohit`} x1={padL} x2={W} y1={ay} y2={ay} stroke="var(--foreground)" strokeOpacity={0.55} strokeDasharray="3 3" shapeRendering="crispEdges" />
          {inside ? (
            <text className={`${K.fade} k-nohit`} style={AVG_LABEL} x={W} y={ay - 6} textAnchor="end" fontSize={10}>{avgWord} <tspan fill="var(--foreground)" fontWeight={600}>{avgLabel}</tspan></text>
          ) : (
            <>
              <line className={K.fade} style={AVG_LABEL} x1={W - labW - 26} x2={W - labW - 8} y1={8.5} y2={8.5} stroke="var(--foreground)" strokeOpacity={0.55} strokeDasharray="3 3" shapeRendering="crispEdges" />
              <text className={K.fade} style={AVG_LABEL} x={W} y={12} textAnchor="end">{avgWord} <tspan fill="var(--foreground)" fontWeight={600}>{avgLabel}</tspan></text>
            </>
          )}
        </>
      )}
    </svg>
  )
}

// ── Hour by weekday, with the time per hour under it on the same columns ──────
const daysLong = () => [t`Mondays`, t`Tuesdays`, t`Wednesdays`, t`Thursdays`, t`Fridays`, t`Saturdays`, t`Sundays`]

export function HourHeat({ W, grid, byCol, colN, two, histH, loading }: { W: number; grid: HourGrid; byCol: number[]; colN: number[]; two: boolean; histH: number; loading?: boolean }) {
  const ncol = two ? 12 : 24
  const labelW = 34, gap = 2
  const { cell, xs } = columns(labelW, W - labelW, ncol, gap)
  const rp = cell + gap
  const heatH = 7 * rp - gap
  const hy = heatH + 12
  const H = hy + histH + 20
  const days = [t`Mon`, t`Tue`, t`Wed`, t`Thu`, t`Fri`, t`Sat`, t`Sun`]
  const span = two ? 2 : 1
  // Marks in DOM order: the 7 rows of cells, then one column of the histogram per hour.
  const tips = useTips((i) => {
    const r = Math.floor(i / ncol), k = i % ncol
    const range = `${hh(k * span)}–${hh(k * span + span)}`
    if (r < 7) {
      const c = grid[r]?.[k]
      if (!c) return null
      const day = daysLong()[r]
      return { title: `${day} ${range}`, value: c.s > 0 ? formatDuration(c.s) : t`No reading`, note: c.s > 0 ? sessionsLine(c.n) : undefined }
    }
    const s = byCol[k] ?? 0
    return { title: t`${range}, all days`, value: s > 0 ? formatDuration(s) : t`No reading`, note: s > 0 ? sessionsLine(colN[k] ?? 0) : undefined }
  }, { x: 1, y: ncol })
  if (loading) return <Placeholder W={W} H={H} x={labelW} h={heatH} />
  const maxH = Math.max(1, ...byCol)
  const base = hy + histH
  const perHour = two ? 0.5 : 1
  return (
    <svg {...tips} className="block fill-muted-foreground text-[11px] tabular-nums" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Reading time by hour and weekday`}>
      {grid.map((row, r) => (
        <g key={r}>
          <text x={labelW - 8} y={r * rp + cell / 2 + 4} textAnchor="end">{days[r]}</text>
          {row.map((c, k) => (
            <rect key={k} className={`k-cell ${K.fade}`} style={stagger(k, ncol)} data-tip-i={r * ncol + k} x={xs[k]} y={r * rp} width={cell} height={cell} rx={cell >= 16 ? 3 : 2} fill={LV[c.lv]} />
          ))}
        </g>
      ))}
      {byCol.map((s, k) => {
        const h = Math.round((s / maxH) * histH)
        return (
          <g key={k}>
            <rect className="k-hit" data-tip-i={7 * ncol + k} x={xs[k] - gap / 2} y={hy} width={cell + gap} height={histH} />
            {h > 0 && <path className={`${K.grow} k-nohit`} style={stagger(k, ncol)} d={roundedTop(xs[k], base - h, cell, h, Math.min(2, h))} fill="var(--chart-accent)" />}
          </g>
        )
      })}
      <line x1={labelW} x2={W} y1={base + 0.5} y2={base + 0.5} stroke="var(--border)" shapeRendering="crispEdges" />
      {[0, 6, 12, 18, 24].map((hr) => {
        const k = hr * perHour
        const x = k >= ncol ? xs[ncol - 1] + cell : xs[k]
        return (
          <g key={hr}>
            <line x1={x + 0.5} x2={x + 0.5} y1={base} y2={base + 4} stroke={TICK} shapeRendering="crispEdges" />
            <text x={x} y={H - 3} textAnchor={hr === 24 ? 'end' : hr === 0 ? 'start' : 'middle'}>{hr}</text>
          </g>
        )
      })}
    </svg>
  )
}

// ── The year ──────────────────────────────────────────────────────────────────
export function YearGrid({ W, year, weeks, loading }: { W: number; year: YearModel; weeks: number | null; loading?: boolean }) {
  const firstCol = weeks ? Math.max(0, year.cols - weeks) : 0
  const n = weeks ?? year.cols
  const labelW = 22, gap = 3, top = 20
  const { cell, xs } = columns(labelW, W - labelW, n, gap)
  const rp = cell + gap
  const H = top + 7 * rp - gap
  // Cells in date order, a week per column: Left/Right a week, Up/Down a day.
  const tips = useTips((i) => {
    const c = year.cells[i]
    if (!c) return null
    return { title: fmtLong(c.date), value: c.s > 0 ? formatDuration(c.s) : t`No reading`, note: c.s > 0 ? sessionsLine(c.n, c.pages) : undefined }
  }, { x: 7, y: 1 })
  if (loading) return <Placeholder W={W} H={H} x={labelW} y={top} />
  const dayLetters: [number, string][] = [[0, t`M`], [2, t`W`], [4, t`F`]]
  return (
    <svg {...tips} className="block fill-muted-foreground text-[11px] tabular-nums" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Reading time per day, last 365 days`}>
      {year.months.map((m) => {
        const c = m.col - firstCol
        if (c < 0 || c > n - 2) return null
        return <text key={m.col} x={xs[c]} y={12}>{m.label}</text>
      })}
      {dayLetters.map(([r, l]) => <text key={r} x={0} y={top + r * rp + cell / 2 + 4}>{l}</text>)}
      {year.cells.map((c, i) => {
        const k = c.col - firstCol
        if (k < 0 || k >= n) return null
        return <rect key={c.date} className={`k-cell ${K.fade}`} style={stagger(k, n)} data-tip-i={i} x={xs[k]} y={top + c.row * rp} width={cell} height={cell} rx={cell >= 14 ? 3 : 2} fill={LV[c.lv]} />
      })}
    </svg>
  )
}
