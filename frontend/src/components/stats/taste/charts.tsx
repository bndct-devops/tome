// The Taste page's two charts, plain SVG on whole pixels at a width the page
// measures before its first paint (like the Overview's). Shaping is in model.ts.
// Hover: every dot has a tip (kit/tip.ts) and each chart is one focus stop with
// arrow keys; a dot that is one book opens it (click, Enter, or a second tap on
// a phone, the first tap shows the tip). Entry: dots fade in along the x axis and
// the mean line draws (kit/motion.ts); the section around the chart decides when.
import { useRef, type CSSProperties, type KeyboardEvent, type MouseEvent, type PointerEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { t, plural } from '@lingui/core/macro'
import { formatDuration } from '@/lib/utils'
import { K, stagger } from '@/components/stats/kit/motion'
import { useTips } from '@/components/stats/kit/tip'
import { fmtDay } from '@/components/stats/kit/tokens'
import { STAR_PATH, dayNum, hourStep, placeCounts, scatterDots, timeline, type RatedBook, type ScatterDot, type TimeGroup } from './model'

const GRID = 'color-mix(in oklch, var(--border) 70%, transparent)'
const TICK = 'color-mix(in oklch, var(--muted-foreground) 60%, transparent)'
const YSTAR = 'color-mix(in oklch, var(--muted-foreground) 70%, transparent)'
const GROUND = 'var(--background)'

const starsWord = (r: number) => plural(r, { one: '# star', other: '# stars' })
// The merged dots' counts appear as the dots finish fading in.
const LATE = { '--k-d': '320ms' } as CSSProperties

/**
 * Handlers of a chart whose dots lead to a book: `bookOf(i)` is the book of
 * mark i, or null. A mouse click opens it; on touch the first tap shows the tip
 * (TipLayer) and a tap on the dot whose tip is showing opens it; Enter opens the
 * dot reached with the arrow keys.
 */
function useDotLinks(bookOf: (i: number) => number | null) {
  const navigate = useNavigate()
  const tap = useRef<{ touch: boolean; hot: boolean }>({ touch: false, hot: false })
  const markOf = (el: EventTarget | null) => (el instanceof Element ? el.closest('[data-tip-i]') : null)
  const open = (m: Element | null) => {
    const id = m ? bookOf(Number(m.getAttribute('data-tip-i'))) : null
    if (id != null) navigate(`/books/${id}`)
  }
  return {
    onPointerDown: (e: PointerEvent) => { const m = markOf(e.target); tap.current = { touch: e.pointerType !== 'mouse', hot: !!m?.hasAttribute('data-hot') } },
    onClick: (e: MouseEvent) => { if (!tap.current.touch || tap.current.hot) open(markOf(e.target)) },
    onKeyDown: (e: KeyboardEvent) => { if (e.key === 'Enter') open((e.currentTarget as Element).querySelector('[data-tip-i][data-hot]')) },
  }
}

/** "5 ★" on the y axis: the number, then a small star. */
function YLabel({ x, y, r }: { x: number; y: number; r: number }) {
  return (
    <>
      <text x={x - 22} y={y + 4} textAnchor="end">{r}</text>
      <path d={STAR_PATH} transform={`translate(${x - 19},${y - 5}) scale(${10 / 24})`} fill={YSTAR} />
    </>
  )
}

/** The plot area of a chart without data: a block in the chart's own box. */
function Placeholder({ W, H, x, y, h }: { W: number; H: number; x: number; y: number; h: number }) {
  return (
    <svg className="block" width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
      <rect x={x} y={y} width={Math.max(0, W - x)} height={h} rx={8} fill="var(--muted)" />
    </svg>
  )
}

// ── Does time spent match the rating? ─────────────────────────────────────────
export function TimeVsRating({ W, H, books, loading }: { W: number; H: number; books: RatedBook[]; loading: boolean }) {
  const padL = 34, padB = 24, padT = 4, padR = 6
  const shown = useRef<ScatterDot[]>([])
  const tips = useTips((i) => {
    const b = shown.current[i]?.book
    if (!b) return null
    return { title: b.title, value: starsWord(b.rating), note: b.seconds > 0 ? (() => { const d = formatDuration(b.seconds); return t`${d} read in Tome` })() : t`No reading time in Tome` }
  })
  const links = useDotLinks((i) => shown.current[i]?.book.book_id ?? null)
  if (loading || W <= padL + padR) return <Placeholder W={Math.max(1, W)} H={H} x={padL} y={padT} h={H - padT - padB} />
  const plotW = W - padL - padR, plotH = H - padT - padB
  const maxH = Math.max(1, ...books.map((b) => b.seconds / 3600))
  const { step, top } = hourStep(maxH)
  const x = (s: number) => padL + (s / 3600 / top) * plotW
  const P = plotH / 5
  const y = (r: number) => padT + (5 - r) * P + P / 2
  const laneH = Math.round(P * 0.74)
  const rad = W < 500 ? 4 : 4.5
  const dots = scatterDots(books, x, y, laneH, rad)
  shown.current = dots
  const ticks: number[] = []
  for (let v = 0; v <= top; v += step) ticks.push(v)
  return (
    <svg {...tips} {...links} className="block fill-muted-foreground text-[11px] tabular-nums" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Hours read against rating, one dot per rated book`}>
      {[1, 2, 3, 4, 5].map((r) => {
        const yc = Math.round(y(r))
        return (
          <g key={r}>
            <rect x={padL} y={Math.round(yc - laneH / 2)} width={plotW} height={laneH} rx={4} fill="var(--k-lv0)" />
            <YLabel x={padL} y={yc} r={r} />
          </g>
        )
      })}
      {ticks.map((v) => {
        const xx = Math.round(x(v * 3600)) + 0.5
        return (
          <g key={v}>
            {v > 0 && <line x1={xx} x2={xx} y1={padT} y2={padT + plotH} stroke={GRID} />}
            <text x={xx} y={H - 6} textAnchor={v === 0 ? 'start' : v === top ? 'end' : 'middle'}>{v === 0 ? '0' : t({ message: `${v}h`, comment: 'Hours on a chart axis' })}</text>
          </g>
        )
      })}
      {dots.map((d, i) => (
        <circle
          key={d.book.book_id}
          data-tip-i={i}
          className={`k-cell cursor-pointer ${K.fade}`}
          style={stagger(i, dots.length)}
          cx={d.cx.toFixed(1)}
          cy={d.cy.toFixed(1)}
          r={rad}
          fill={d.half ? GROUND : 'var(--chart-accent)'}
          stroke={d.half ? 'var(--chart-accent)' : GROUND}
          strokeWidth={d.half ? 1.75 : 1.25}
        />
      ))}
    </svg>
  )
}

// ── Ratings over time ─────────────────────────────────────────────────────────
export const OVER_TIME_H = 180
const LANE = 30

export function OverTime({ W, books, loading }: { W: number; books: RatedBook[]; loading: boolean }) {
  const H = OVER_TIME_H
  const padL = 34, padT = 6, padR = 8
  const shown = useRef<TimeGroup[]>([])
  const tips = useTips((i) => {
    const g = shown.current[i]
    if (!g) return null
    const day = fmtDay(g.date)
    const n = g.titles.length
    if (n === 1) return { title: g.titles[0], value: starsWord(g.rating), note: t`Rated ${day}` }
    const MAX = 4
    const names = g.titles.slice(0, MAX).join(', ')
    const rest = n - MAX
    const stars = starsWord(g.rating)
    return { title: t`${n} books at ${stars}`, value: rest > 0 ? plural(rest, { one: `${names} and # more`, other: `${names} and # more` }) : names, note: t`Rated ${day}` }
  }, { x: 1, y: 1 })
  const links = useDotLinks((i) => { const g = shown.current[i]; return g && g.ids.length === 1 ? g.ids[0] : null })
  if (loading || W <= padL + padR) return <Placeholder W={Math.max(1, W)} H={H} x={padL} y={padT} h={5 * LANE} />
  const { points, groups } = timeline(books)
  shown.current = groups
  const plotW = W - padL - padR
  const d0 = dayNum(points[0].date) - 3, d1 = dayNum(points[points.length - 1].date) + 3
  const x = (iso: string) => padL + ((dayNum(iso) - d0) / (d1 - d0)) * plotW
  const y = (r: number) => padT + (5 - r) * LANE + LANE / 2
  const by = padT + 5 * LANE + 0.5

  /* eslint-disable lingui/no-unlocalized-strings -- date parts and SVG path commands */
  // Month ticks; every k-th month labelled when a long history would crowd them.
  const months: { iso: string; label: string }[] = []
  const start = new Date(points[0].date + 'T12:00:00Z')
  for (let m = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1)); dayNum(m.toISOString().slice(0, 10)) <= d1; m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1))) {
    const jan = m.getUTCMonth() === 0
    months.push({ iso: m.toISOString().slice(0, 10), label: m.toLocaleDateString(undefined, jan ? { year: 'numeric', timeZone: 'UTC' } : { month: 'short', timeZone: 'UTC' }) })
  }
  const every = Math.max(1, Math.ceil((months.length * 44) / plotW))
  const shownMonths = every === 1 ? months : months.filter((_, i) => i % every === 0)

  // Stepped mean of the last five: holds its value until the next rating.
  const meanPts = points.filter((p) => p.mean != null).map((p) => ({ x: x(p.date), y: y(p.mean!) }))
  let path = ''
  if (meanPts.length > 0) {
    path = `M${meanPts[0].x.toFixed(1)},${meanPts[0].y.toFixed(1)}`
    for (let k = 1; k < meanPts.length; k++) path += ` H${meanPts[k].x.toFixed(1)} V${meanPts[k].y.toFixed(1)}`
  }
  /* eslint-enable lingui/no-unlocalized-strings */
  const rad = 4
  const placed = groups.map((g) => ({ cx: x(g.date), cy: y(g.rating), n: g.titles.length }))
  const counts = placeCounts(placed, meanPts, rad, { x0: padL, x1: W, y0: 0, y1: padT + 5 * LANE })

  return (
    <svg {...tips} {...links} className="block fill-muted-foreground text-[11px] tabular-nums" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Ratings over time, one dot per rating, with the mean of the last five`}>
      {[1, 2, 3, 4, 5].map((r) => {
        const yy = Math.round(y(r)) + 0.5
        return (
          <g key={r}>
            <line x1={padL} x2={W - padR} y1={yy} y2={yy} stroke={GRID} />
            <YLabel x={padL} y={Math.round(yy)} r={r} />
          </g>
        )
      })}
      <line x1={padL} x2={W - padR} y1={by} y2={by} stroke={TICK} />
      {shownMonths.map((m) => {
        const xx = Math.round(x(m.iso)) + 0.5
        return (
          <g key={m.iso}>
            <line x1={xx} x2={xx} y1={by} y2={by + 5} stroke={TICK} />
            {xx + 4 + 30 <= W && <text x={xx + 4} y={H - 6}>{m.label}</text>}
          </g>
        )
      })}
      {path && <path className={`${K.draw} k-nohit`} pathLength={1} d={path} fill="none" stroke="var(--foreground)" strokeOpacity={0.7} strokeWidth={1.5} strokeLinejoin="round" />}
      {groups.map((g, i) => {
        const half = g.rating % 1 !== 0
        return (
          <circle
            key={`${g.date}-${g.rating}`}
            data-tip-i={i}
            className={`k-cell ${K.fade} ${g.ids.length === 1 ? 'cursor-pointer' : ''}`}
            style={stagger(i, groups.length)}
            cx={placed[i].cx.toFixed(1)}
            cy={placed[i].cy.toFixed(1)}
            r={rad}
            fill={half ? GROUND : 'var(--chart-accent)'}
            stroke={half ? 'var(--chart-accent)' : GROUND}
            strokeWidth={half ? 1.75 : 1.25}
          />
        )
      })}
      {counts.map((c, i) => c && (
        <text key={i} x={c.x.toFixed(1)} y={c.y.toFixed(1)} className={`${K.fade} k-nohit fill-foreground text-[10px] font-semibold`} style={LATE}>{c.label}</text>
      ))}
    </svg>
  )
}
