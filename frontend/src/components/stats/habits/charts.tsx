// The Habits page's charts: plain SVG on whole pixels at a measured width, each
// in a box whose height is fixed before any data arrives. Shaping is in model.ts.
// Hover: every mark has a tip (kit/tip.ts) and each chart is one focus stop with
// arrow keys. Entry: marks carry the motion kit's classes (kit/motion.ts); the
// section around the chart decides when they play. Sittings of the last 14 days
// lead to their book (click, Enter on the focused sitting, a second tap on touch).
import { useRef, type CSSProperties, type KeyboardEvent, type MouseEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { t, plural } from '@lingui/core/macro'
import { formatDuration } from '@/lib/utils'
import { K, stagger, type Entry } from '@/components/stats/kit/motion'
import { useTween } from '@/components/stats/kit/tween'
import { guideHost, useTips } from '@/components/stats/kit/tip'
import { Guide, type GuideSlot } from '@/components/stats/kit/Guide'
import { dec1, num } from '@/components/stats/kit/tokens'
import { HEAT_GAP, heatRadius, type HourGrid } from '@/components/stats/overview/model'
import { inWeekOrder } from '@/lib/weekStart'
import {
  TL_PITCH, TL_PITCH_PHONE, TL_TRACK, TL_TRACK_PHONE, TL_ROWS, MONTH_ROW,
  monthsHeight, rhythmGeom, timelineHeight, type PaceDay, type PaceDot, type TlBar, type TlRow,
} from './model'

const LV = ['var(--k-lv0)', 'var(--k-lv1)', 'var(--k-lv2)', 'var(--k-lv3)', 'var(--k-lv4)', 'var(--k-lv5)']
const GRID = 'color-mix(in oklch, var(--border) 70%, transparent)'
const TICK = 'color-mix(in oklch, var(--muted-foreground) 60%, transparent)'
// Text never takes the pointer: the mark under it answers instead.
const SVG = 'block fill-muted-foreground text-[11px] tabular-nums [&_text]:pointer-events-none'
const hh = (h: number) => `${String(h % 24).padStart(2, '0')}:00`
const clock = (d: Date) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
// eslint-disable-next-line lingui/no-unlocalized-strings -- ISO time suffix
const dateOf = (iso: string) => new Date(iso + 'T00:00:00')
/** "Tue 3": the weekday and the day of the month. */
const dayLabel = (iso: string) => {
  const d = dateOf(iso)
  return `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${d.getDate()}`
}
const fmtLong = (iso: string) => dateOf(iso).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
// Onest at 11 px with tabular figures: at most about 0.6 em per character.
const textW = (s: string, px = 11) => Math.ceil(s.length * px * 0.6)
const daysLong = () => inWeekOrder([t`Mondays`, t`Tuesdays`, t`Wednesdays`, t`Thursdays`, t`Fridays`, t`Saturdays`, t`Sundays`])
const sessionsLine = (n: number) => plural(n, { one: '# session', other: '# sessions' })
/** A tip's duration: a few seconds read as "Under a minute", not "0m". */
const dur = (s: number) => (s > 0 && s < 60 ? t`Under a minute` : formatDuration(s))
const share = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 100) : 0)

/** A value label that appears once the bars under it have grown. */
const late = (i: number, n: number): CSSProperties => {
  const step = n > 1 ? Math.min(24, 200 / (n - 1)) : 0
  return { '--k-d': `${320 + Math.round(i * step)}ms` } as CSSProperties
}

const roundedTop = (x: number, yt: number, w: number, h: number, r: number) =>
  // eslint-disable-next-line lingui/no-unlocalized-strings -- SVG path
  `M${x},${yt + h} V${yt + r} Q${x},${yt} ${x + r},${yt} H${x + w - r} Q${x + w},${yt} ${x + w},${yt + r} V${yt + h} Z`

function Placeholder({ W, H, x = 0, y = 0, w, h }: { W: number; H: number; x?: number; y?: number; w?: number; h?: number }) {
  return (
    <svg className="block" width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
      <rect x={x} y={y} width={w ?? W - x} height={h ?? H - y} rx={8} fill="var(--muted)" />
    </svg>
  )
}

/** A quiet line in the middle of a chart's reserved box (no data). */
export function ChartEmpty({ H, children }: { H: number; children: string }) {
  return <div className="flex items-center justify-center rounded-[12px] bg-secondary/50 px-6 text-center text-[14px] text-muted-foreground" style={{ height: H }}>{children}</div>
}

/**
 * Marks with data-book lead to that book: a click (new tab with Cmd/Ctrl), Enter
 * while the chart has focus on one, or on touch a tap on a sitting whose tip is
 * already showing (the first tap shows the tip).
 */
function useBookLinks() {
  const navigate = useNavigate()
  const wasHot = useRef(false)
  const book = (el: EventTarget | null) => (el instanceof Element ? el.closest('[data-book]') : null)
  const go = (m: Element, newTab: boolean) => {
    const to = `/books/${m.getAttribute('data-book')}`
    if (newTab) window.open(to, '_blank', 'noopener')
    else navigate(to)
  }
  return {
    onPointerDown: (e: ReactPointerEvent) => { wasHot.current = !!book(e.target)?.hasAttribute('data-hot') },
    onClick: (e: MouseEvent) => {
      const m = book(e.target)
      if (!m) return
      if ((e.nativeEvent as PointerEvent).pointerType === 'touch' && !wasHot.current) return
      go(m, e.metaKey || e.ctrlKey)
    },
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key !== 'Enter') return
      const m = e.currentTarget.querySelector('[data-hot][data-book]')
      if (m) go(m, e.metaKey || e.ctrlKey)
    },
  }
}

// ── When you read: hour × weekday, weekday totals right, hour totals under ────
export function Rhythm({ entry, W, grid, byCol, colN, byDow, phone, loading }: {
  /** The section's entry: a new range moves the totals to their new sizes (the cells take their new shade at once). */
  entry: Entry
  W: number
  grid: HourGrid
  byCol: number[]
  colN: number[]
  byDow: { s: number[]; n: number[] }
  phone: boolean
  loading?: boolean
}) {
  const { ncol, labelW, xs, ws, end: heatR, rowH, heatH, histH, H } = rhythmGeom(W, phone)
  const span = phone ? 2 : 1
  const gap = HEAT_GAP
  const rp = rowH + gap
  const hy = heatH + 12
  const base = hy + histH
  const total = byDow.s.reduce((a, s) => a + s, 0)
  // Marks in DOM order: the 7 rows of cells, the hour columns, then the 7 weekday totals.
  const tips = useTips((i) => {
    const range = (k: number) => `${hh(k * span)}–${hh(k * span + span)}`
    if (i < 7 * ncol) {
      const r = Math.floor(i / ncol), k = i % ncol
      const c = grid[r]?.[k]
      if (!c) return null
      return { title: `${daysLong()[r]} ${range(k)}`, value: c.s > 0 ? dur(c.s) : t`No reading`, note: c.s > 0 ? sessionsLine(c.n) : undefined }
    }
    if (i < 8 * ncol) {
      const k = i - 7 * ncol
      const s = byCol[k] ?? 0
      const r = range(k)
      return { title: t`${r}, all days`, value: s > 0 ? dur(s) : t`No reading`, note: s > 0 ? sessionsLine(colN[k] ?? 0) : undefined }
    }
    const r = i - 8 * ncol
    const s = byDow.s[r] ?? 0
    const pct = share(s, total)
    const sessions = sessionsLine(byDow.n[r] ?? 0)
    return { title: daysLong()[r], value: s > 0 ? dur(s) : t`No reading`, note: s > 0 ? t`${sessions} · ${pct}% of the reading` : undefined }
  }, { x: 1, y: ncol })
  const tw = useTween(entry, loading ? null : byCol, `${W}:${phone}`)
  if (loading) return <Placeholder W={W} H={H} x={labelW} h={heatH} />
  const days = inWeekOrder([t`Mon`, t`Tue`, t`Wed`, t`Thu`, t`Fri`, t`Sat`, t`Sun`])
  const maxDow = Math.max(1, ...byDow.s)
  const barX = heatR + 24
  const barMax = W - 64 - barX
  const maxH = Math.max(1, ...byCol)
  return (
    <svg {...tips} ref={tw} className={SVG} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Reading time by hour and weekday`}>
      {grid.map((row, r) => {
        const y = r * rp
        return (
          <g key={r}>
            <text x={0} y={y + rowH / 2 + 4}>{days[r]}</text>
            {row.map((c, k) => (
              <rect key={k} className={`k-cell ${K.fade}`} style={stagger(k, ncol)} data-tip-i={r * ncol + k} x={xs[k]} y={y} width={ws[k]} height={rowH} rx={heatRadius(rowH)} fill={LV[c.lv]} />
            ))}
          </g>
        )
      })}
      <g className="k-bars">
      {byCol.map((s, k) => {
        const h = Math.round((s / maxH) * histH)
        return (
          <g key={k} className="k-col">
            <rect className="k-hit" data-tip-i={7 * ncol + k} x={xs[k] - gap / 2} y={hy} width={ws[k] + gap} height={histH} />
            {h > 0 && <path className={`${K.grow} k-bar k-nohit`} data-k={`h${k}`} style={stagger(k, ncol)} d={roundedTop(xs[k], base - h, ws[k], h, Math.min(2, h))} fill="var(--chart-accent)" />}
          </g>
        )
      })}
      </g>
      <g className="k-bars">
      {byDow.s.map((s, r) => {
        const y = r * rp
        return (
          <g key={r} className="k-col">
            <rect className="k-hit" data-tip-i={8 * ncol + r} x={heatR + 8} y={y - gap / 2} width={W - heatR - 8} height={rp} rx={3} />
            {!phone && s > 0 && (
              <rect className={`${K.growX} k-bar k-nohit`} data-k={`d${r}`} data-kt="barx" style={stagger(r, 7)} x={barX} y={y + (rowH - 12) / 2} width={Math.max(2, Math.round((s / maxDow) * barMax))} height={12} rx={2} fill="var(--chart-accent)" />
            )}
            {s > 0 ? (
              <text className={K.fade} style={late(r, 7)} x={W} y={y + rowH / 2 + 4} textAnchor="end" fill="var(--foreground)" fontSize={12} fontWeight={500}>{formatDuration(s)}</text>
            ) : (
              <text x={W} y={y + rowH / 2 + 4} textAnchor="end" opacity={0.5}>–</text>
            )}
          </g>
        )
      })}
      </g>
      <line className="k-nohit" x1={labelW} x2={heatR} y1={base + 0.5} y2={base + 0.5} stroke="var(--border)" shapeRendering="crispEdges" />
      {[0, 6, 12, 18, 24].map((hr) => {
        const k = hr / span
        const x = k >= ncol ? heatR : xs[k]
        return (
          <g key={hr} className="k-nohit">
            <line x1={x + 0.5} x2={x + 0.5} y1={base} y2={base + 4} stroke={TICK} shapeRendering="crispEdges" />
            <text x={x} y={H - 3} textAnchor={hr === 24 ? 'end' : hr === 0 ? 'start' : 'middle'}>{hr}</text>
          </g>
        )
      })}
    </svg>
  )
}

// ── Last 14 days: a 04:00 → 04:00 track per reading day ───────────────────────
type TlMark = { row: TlRow; bar?: TlBar }

export function Timeline({ W, rows, phone, loading }: { W: number; rows: TlRow[]; phone: boolean; loading?: boolean }) {
  const pitch = phone ? TL_PITCH_PHONE : TL_PITCH
  const trackH = phone ? TL_TRACK_PHONE : TL_TRACK
  const labelW = phone ? 46 : 56
  const totalW = phone ? 54 : 64
  const x0 = labelW, tw = W - totalW - x0
  const H = timelineHeight(phone)
  const xm = (m: number) => x0 + (m / 1440) * tw
  const yTrack = (i: number) => i * pitch + Math.round((pitch - trackH) / 2) - (pitch - trackH > 8 ? 2 : 0)
  const yb = yTrack(TL_ROWS - 1) + trackH
  const fontSize = 12
  const list: (TlRow | null)[] = loading ? Array.from({ length: TL_ROWS }, () => null) : rows
  // Marks in DOM order: each day's row (its total), then the day's sittings.
  const marks: TlMark[] = []
  for (const r of list) {
    if (!r) continue
    marks.push({ row: r })
    for (const b of r.bars) marks.push({ row: r, bar: b })
  }
  const tips = useTips((i) => {
    const m = marks[i]
    if (!m) return null
    const day = fmtLong(m.row.date)
    if (!m.bar) {
      return { title: day, value: m.row.seconds > 0 ? dur(m.row.seconds) : t`No reading`, note: m.row.sittings > 0 ? plural(m.row.sittings, { one: '# sitting', other: '# sittings' }) : undefined }
    }
    const b = m.bar
    const from = clock(b.started)
    const to = clock(new Date(b.started.getTime() + b.seconds * 1000))
    const len = dur(b.seconds)
    return { title: b.title ?? t`Unknown book`, value: `${from}–${to}`, note: `${len} · ${day}` }
  })
  const links = useBookLinks()
  let at = 0
  return (
    <svg {...tips} {...links} className={SVG} style={{ fontSize }} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Sittings in the last 14 reading days, 04:00 to 04:00`}>
      {list.map((r, i) => {
        const y = yTrack(i)
        if (!r) {
          return (
            <g key={i}>
              <rect x={0} y={y + trackH / 2 - 7} width={labelW - 16} height={14} rx={4} fill="var(--muted)" />
              <rect x={x0} y={y} width={tw} height={trackH} rx={3} fill={LV[0]} />
              <rect x={W - 40} y={y + trackH / 2 - 7} width={40} height={14} rx={4} fill="var(--muted)" />
            </g>
          )
        }
        const dayI = at++
        return (
          <g key={r.date}>
            {/* The row answers with the day's total; its sittings, on top, with themselves. */}
            <rect className="k-hit" data-tip-i={dayI} x={0} y={y - (pitch - trackH) / 2 + 1} width={W} height={pitch - 2} rx={4} />
            <text x={0} y={y + trackH / 2 + 4}>{dayLabel(r.date)}</text>
            <rect className="k-nohit" x={x0} y={y} width={tw} height={trackH} rx={3} fill={LV[0]} />
            {r.bars.map((b, k) => {
              const a = Math.round(xm(b.m0))
              const e = Math.max(a + 2, Math.round(xm(b.m1)))
              return (
                <rect
                  key={k}
                  className={`k-cell ${K.growX} ${b.bookId != null ? 'cursor-pointer' : ''}`}
                  style={stagger(i, TL_ROWS)}
                  data-tip-i={at++}
                  data-book={b.bookId ?? undefined}
                  x={a} y={y + 2} width={e - a} height={trackH - 4} rx={2} fill="var(--chart-accent)"
                />
              )
            })}
            {r.seconds > 0 ? (
              <text className={K.fade} style={late(i, TL_ROWS)} x={W} y={y + trackH / 2 + 4} textAnchor="end" fill="var(--foreground)" fontWeight={500}>{formatDuration(r.seconds)}</text>
            ) : (
              <text x={W} y={y + trackH / 2 + 4} textAnchor="end" opacity={0.5}>–</text>
            )}
          </g>
        )
      })}
      {[6, 12, 18].map((h) => {
        const x = Math.round(xm(h * 60)) + 0.5
        return <line key={h} className="k-nohit" x1={x} x2={x} y1={yTrack(0)} y2={yb} stroke="var(--background)" shapeRendering="crispEdges" />
      })}
      {[0, 6, 12, 18, 24].map((h) => (
        <text key={h} x={Math.round(xm(h * 60))} y={yb + 14} textAnchor={h === 0 ? 'start' : h === 24 ? 'end' : 'middle'}>{(h + 4) % 24}</text>
      ))}
    </svg>
  )
}

// ── Session length: five buckets, the count above each bar ────────────────────
export function Lengths({ entry, W, H, counts, loading }: { entry: Entry; W: number; H: number; counts: number[]; loading?: boolean }) {
  const valH = 18, labH = 22
  const plotH = H - valH - labH
  const base = valH + plotH
  const total = counts.reduce((a, n) => a + n, 0)
  const tips = useTips((i) => {
    const n = counts[i] ?? 0
    const long = [t`Under 15 minutes`, t`15 to 30 minutes`, t`30 to 60 minutes`, t`1 to 2 hours`, t`Over 2 hours`][i]
    const pct = share(n, total)
    return { title: long, value: plural(n, { one: '# sitting', other: '# sittings' }), note: t`${pct}% of sittings` }
  })
  const tw = useTween(entry, loading ? null : counts, W)
  if (loading) return <Placeholder W={W} H={H} y={valH} h={plotH} />
  const labels = [t`Under 15m`, t`15 to 30m`, t`30 to 60m`, t`1 to 2h`, t`Over 2h`]
  const slot = W / labels.length
  const bw = Math.round(Math.min(slot * 0.62, 56))
  const max = Math.max(1, ...counts)
  return (
    <svg {...tips} ref={tw} className={SVG} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Sittings by length`}>
      <g className="k-bars">
      {counts.map((n, i) => {
        const x = Math.round(slot * i + (slot - bw) / 2)
        const h = Math.round((n / max) * plotH)
        return (
          <g key={i} className="k-col">
            <rect className="k-hit" data-tip-i={i} x={Math.round(slot * i)} y={0} width={Math.round(slot)} height={base} rx={4} />
            {h > 0 && <path className={`${K.grow} k-bar k-nohit`} data-k={`l${i}`} style={stagger(i, counts.length)} d={roundedTop(x, base - h, bw, h, Math.min(3, h))} fill="var(--chart-accent)" />}
            <text className={K.fade} data-k={`n${i}`} data-kt="move" style={late(i, counts.length)} x={x + bw / 2} y={base - h - 5} textAnchor="middle" fill="var(--foreground)" fontWeight={500}>{num(n)}</text>
            <text x={x + bw / 2} y={H - 4} textAnchor="middle">{labels[i]}</text>
          </g>
        )
      })}
      </g>
      <line className="k-nohit" x1={0} x2={W} y1={base + 0.5} y2={base + 0.5} stroke="var(--border)" shapeRendering="crispEdges" />
    </svg>
  )
}

// ── Pace: a dot per reading day, the trailing 7-day mean as a line ────────────
// Hover anywhere in the plot: a guide snaps to the nearest day (kit/Guide.tsx).
export function Pace({ W, H, dots, days, nDays, ticks, loading }: { W: number; H: number; dots: PaceDot[]; days: PaceDay[]; nDays: number; ticks: { index: number; label: string }[]; loading?: boolean }) {
  const padL = 28, padB = 22, padT = 8
  const n = dots.length
  if (loading) return <Placeholder W={W} H={H} x={padL} y={padT} h={H - padT - padB} />
  const plotW = W - padL, plotH = H - padT - padB
  const top = Math.ceil(Math.max(1, ...dots.map((d) => d.ppm)) + 0.25)
  const step = top > 12 ? Math.ceil(top / 6) : 1
  const y = (v: number) => padT + plotH - (v / top) * plotH
  const x = (i: number) => padL + 6 + (nDays > 1 ? i / (nDays - 1) : 0.5) * (plotW - 12)
  const by = Math.round(y(0)) + 0.5
  const lines: number[] = []
  for (let v = step; v <= top; v += step) lines.push(v)
  type Pt = readonly [number, number]
  const pts: Pt[] = dots.map((d) => [x(d.i), y(d.mean)] as const)
  const xy = (p: Pt) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`
  const path = pts.map((p, k) => `${k ? 'L' : 'M'}${xy(p)}`).join(' ')
  // One guide slot per day of the range. The ring sits on the line only on a reading
  // day (the line is drawn through those); other days say their 7-day mean in the tip.
  const slots: GuideSlot[] = days.map((d, i) => ({
    x: x(i),
    dots: d.dot ? [String(i)] : [],
    lineY: d.dot && n > 1 ? y(d.dot.mean) : null,
    tip: () => {
      const meanLine = d.mean != null ? (() => { const m = dec1(d.mean); return t`7-day mean ${m}` })() : t`no reading in the last 7 days`
      if (!d.dot) return { title: fmtLong(d.date), value: t`No reading`, note: meanLine }
      const ppm = dec1(d.dot.ppm)
      const pages = plural(d.dot.pages, { one: '# page', other: '# pages' })
      const len = dur(d.dot.seconds)
      return { title: fmtLong(d.date), value: t`${ppm} pages a minute, ${pages} in ${len}`, note: meanLine }
    },
  }))
  // Tick labels kept apart and inside the chart: one too close to the last shown is left out.
  let lastEnd = -Infinity
  const shown = ticks.filter((m) => {
    const xx = Math.round(x(m.index)) + 4
    const end = xx + textW(m.label)
    if (xx < lastEnd + 8 || end > W) return false
    lastEnd = end
    return true
  })
  return (
    <svg {...guideHost} className={SVG} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Pages per minute per reading day`}>
      {lines.map((v) => {
        const yy = Math.round(y(v)) + 0.5
        return (
          <g key={v} className="k-nohit">
            <line x1={padL} x2={W} y1={yy} y2={yy} stroke={GRID} shapeRendering="crispEdges" />
            <text x={padL - 8} y={yy + 4} textAnchor="end">{v}</text>
          </g>
        )
      })}
      <line className="k-nohit" x1={padL} x2={W} y1={by} y2={by} stroke="var(--border)" shapeRendering="crispEdges" />
      <text x={padL - 8} y={by + 4} textAnchor="end">0</text>
      {ticks.map((m) => {
        const xx = Math.round(x(m.index)) + 0.5
        return <line key={m.index} className="k-nohit" x1={xx} x2={xx} y1={by} y2={by + 5} stroke={TICK} shapeRendering="crispEdges" />
      })}
      {shown.map((m) => <text key={m.index} x={Math.round(x(m.index)) + 4.5} y={H - 5}>{m.label}</text>)}
      {n > 1 && <path className={`${K.draw} k-nohit`} pathLength={1} d={path} fill="none" stroke="var(--chart-accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />}
      {dots.map((d, k) => (
        <circle key={d.i} className={`k-cell k-nohit ${K.fade}`} style={stagger(k, n)} data-dot={d.i} cx={x(d.i)} cy={y(d.ppm)} r={3} fill="var(--chart-accent)" fillOpacity={0.5} stroke="transparent" strokeWidth={6} />
      ))}
      <Guide x0={padL} x1={W} y0={padT} y1={by} slots={slots} />
    </svg>
  )
}

// ── The last 12 months: reading hours over books finished ─────────────────────
type Month = { key: string; label: string; seconds: number; sessions: number; books: number }
export function Months({ W, months, phone, loading }: { W: number; months: Month[]; phone: boolean; loading?: boolean }) {
  const labelW = phone ? 0 : 132
  const rowGap = phone ? 40 : 24, valH = 18
  const H = monthsHeight(phone)
  const top1 = phone ? 22 : 0
  const base1 = top1 + MONTH_ROW
  const top2 = base1 + rowGap + (phone ? 22 : 0)
  const base2 = top2 + MONTH_ROW
  const n = Math.max(1, months.length)
  const last = months.length - 1
  // Marks in DOM order: the hours row's months, then the books row's.
  const tips = useTips((i) => {
    const books = i >= n
    const k = i % n
    const m = months[k]
    if (!m) return null
    const title = dateOf(m.key + '-01').toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
    const sofar = k === last ? t`so far` : undefined
    if (books) return { title, value: plural(m.books, { one: '# book finished', other: '# books finished' }), note: sofar }
    const note = [m.seconds > 0 ? sessionsLine(m.sessions) : null, sofar].filter(Boolean).join(' · ')
    return { title, value: m.seconds > 0 ? dur(m.seconds) : t`No reading`, note: note || undefined }
  }, { x: 1, y: n })
  const rowLabels = [t`Reading hours`, t`Books finished`]
  if (loading) {
    return (
      <svg className="block" width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
        <rect x={labelW} y={top1} width={W - labelW} height={MONTH_ROW} rx={8} fill="var(--muted)" />
        <rect x={labelW} y={top2} width={W - labelW} height={MONTH_ROW} rx={8} fill="var(--muted)" />
      </svg>
    )
  }
  const slot = (W - labelW) / n
  const bw = Math.round(Math.min(slot * 0.5, 40))
  const xs = months.map((_, i) => Math.round(labelW + slot * i + (slot - bw) / 2))
  const row = (key: 'hours' | 'books', base: number) => {
    const val = (m: Month) => (key === 'hours' ? m.seconds : m.books)
    const lab = (m: Month) => (key === 'hours' ? formatDuration(m.seconds) : num(m.books))
    const max = Math.max(1, ...months.map(val))
    const fill = key === 'hours' ? 'var(--chart-accent)' : 'var(--success)'
    const rowLabel = rowLabels[key === 'hours' ? 0 : 1]
    const off = key === 'hours' ? 0 : n
    return (
      <g key={key} className="k-bars">
        <text x={0} y={phone ? base - MONTH_ROW - 8 : base - 2} fontSize={13}>{rowLabel}</text>
        {months.map((m, i) => {
          const v = val(m)
          const h = Math.round((v / max) * (MONTH_ROW - valH))
          const label = lab(m)
          const show = !phone || key === 'books' || (last - i) % 2 === 0
          let text = null
          if (show && phone && key === 'hours') {
            const tw = Math.ceil(label.length * 11 * 0.55)
            const cx = xs[i] + bw / 2
            const edge = cx + tw / 2 > W
            text = <text className={K.fade} style={late(i, n)} x={edge ? W : cx} y={base - MONTH_ROW + 11} textAnchor={edge ? 'end' : 'middle'} fill="var(--foreground)" fontWeight={500}>{label}</text>
          } else if (show) {
            text = <text className={K.fade} style={late(i, n)} x={xs[i] + bw / 2} y={base - h - 5} textAnchor="middle" fill="var(--foreground)" fontWeight={500}>{label}</text>
          }
          return (
            <g key={i} className="k-col">
              <rect className="k-hit" data-tip-i={off + i} x={Math.round(labelW + slot * i)} y={base - MONTH_ROW} width={Math.round(slot)} height={MONTH_ROW} rx={4} />
              {h > 0 && (
                <path className={`${K.grow} k-bar k-nohit`} style={stagger(i, n)} d={roundedTop(xs[i], base - h, bw, h, Math.min(3, h))} fill={fill} fillOpacity={i === last ? 0.45 : 1} />
              )}
              {text}
            </g>
          )
        })}
        <line className="k-nohit" x1={labelW} x2={W} y1={base + 0.5} y2={base + 0.5} stroke="var(--border)" shapeRendering="crispEdges" />
      </g>
    )
  }
  return (
    <svg {...tips} className={SVG} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Reading hours and books finished per month`}>
      {row('hours', base1)}
      {row('books', base2)}
      {months.map((m, i) => <text key={i} x={xs[i] + bw / 2} y={H - 4} textAnchor="middle">{m.label}</text>)}
    </svg>
  )
}
