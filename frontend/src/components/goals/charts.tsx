// The Goals tab's charts: the period so far against the pace (burn-up), past
// periods against the target, the week day by day, and the last weeks as a grid
// of days. SVG at the width they are given, drawn once at their final size;
// marks carry their tips (kit/tip.ts) and enter with the kit's motion classes.
import { t, plural } from '@lingui/core/macro'
import type { Goal, GoalHistory, HistoryDay, HistoryPeriod, YearMonth } from '@/lib/goals'
import { addDays, placeEndLabels, type Box, type EndLabel, type Placed, type Seg } from '@/lib/goalsModel'
import { K, stagger } from '@/components/stats/kit/motion'
import { tipAttrs } from '@/components/stats/kit/tip'
import { num, useWidth } from '@/components/stats/kit/tokens'
import { cn } from '@/lib/utils'
import { Dot, FUTURE, MISS } from './GoalCard'
import { gridMark } from './marks'
import { textWidth, useFontsReady } from './measure'
import { dayName, mediumDate, monthName, monthShort, shortDate, unitWord, weekdayLetter } from './text'

const NEUTRAL = 'color-mix(in oklch, var(--foreground) 30%, var(--background))'

/** Round a chart's top up to a readable whole step (four lines at most). */
function niceMax(v: number): { max: number; step: number } {
  const raw = Math.max(1, v)
  for (let mag = 1; ; mag *= 10) {
    for (const f of [1, 2, 5]) {
      const step = f * mag
      const max = Math.ceil(raw / step) * step
      if (max / step <= 4) return { max, step }
    }
  }
}

// eslint-disable-next-line lingui/no-unlocalized-strings -- ISO time suffix
const dayCount = (a: string, b: string) => Math.round((Date.parse(b + 'T00:00:00Z') - Date.parse(a + 'T00:00:00Z')) / 86_400_000)

/** The period so far: the reader's line, the pace to the target, the projection to the period's end.
    The two end labels go where they touch no line, point, the "today" marker or each other
    (goalsModel.placeEndLabels); with no such place they move to a small legend under the plot. */
export function BurnUp({ goal, history, h }: { goal: Goal; history: GoalHistory; h: number }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  useFontsReady()
  const cur = history.current
  const days = cur?.days ?? []
  const total = goal.days_total
  const target = goal.target
  const proj = cur?.projection ?? null
  const last = days.length ? days[days.length - 1].cumulative : 0
  const { max, step } = niceMax(Math.max(target, proj ?? 0, last) * 1.08)
  const ok = goal.reached
  const showProj = proj != null && !ok
  const paceText = (() => { const n = num(target); return t`pace for ${n}` })()
  const projText = showProj ? (() => { const n = num(proj!); return t`on course for ${n}` })() : ''
  const L = 30, R = 10, T = 18, LEGEND = 18

  const geometry = (B: number) => {
    const X = (i: number) => L + (i / total) * (w - L - R)
    const Y = (v: number) => T + (1 - v / max) * (h - T - B)
    // A step line: books arrive one at a time. Day i's finishes land at its start.
    const xy: [number, number][] = [[X(0), Y(0)]]
    days.forEach((d, i) => {
      const before = i > 0 ? days[i - 1].cumulative : 0
      if (d.cumulative !== before) { xy.push([X(i), Y(before)]); xy.push([X(i), Y(d.cumulative)]) }
    })
    const todayX = X(days.length)
    xy.push([todayX, Y(last)])
    return { X, Y, xy, todayX, B }
  }
  let g = geometry(22)
  let placed: Placed[] | null = null
  let legend = false
  if (w > 0) {
    const fit = (gg: ReturnType<typeof geometry>) => {
      const segs: Seg[] = []
      for (let i = 1; i < gg.xy.length; i++) segs.push({ x0: gg.xy[i - 1][0], y0: gg.xy[i - 1][1], x1: gg.xy[i][0], y1: gg.xy[i][1] })
      segs.push({ x0: gg.X(0), y0: gg.Y(0), x1: gg.X(total), y1: gg.Y(target) })
      if (showProj) segs.push({ x0: gg.todayX, y0: gg.Y(last), x1: gg.X(total), y1: gg.Y(proj!) })
      segs.push({ x0: gg.todayX, y0: T - 6, x1: gg.todayX, y1: h - gg.B })
      const tw = textWidth(t`today`, 11, 600)
      const boxes: Box[] = [{ x0: gg.todayX - tw / 2 - 2, x1: gg.todayX + tw / 2 + 2, y0: 0, y1: T - 6 }]
      days.forEach((d, i) => { if (d.value > 0) boxes.push({ x0: gg.X(i) - 5, x1: gg.X(i) + 5, y0: gg.Y(d.cumulative) - 5, y1: gg.Y(d.cumulative) + 5 }) })
      const labels: EndLabel[] = [{ id: 'pace', ex: gg.X(total), ey: gg.Y(target), w: textWidth(paceText, 11), prefer: showProj && proj! > target ? 'below' : 'above' }]
      if (showProj) labels.push({ id: 'proj', ex: gg.X(total), ey: gg.Y(proj!), w: textWidth(projText, 11), prefer: proj! > target ? 'above' : 'below' })
      return placeEndLabels(labels, { segs, boxes }, { x0: L, y0: 0, x1: w, y1: h - gg.B })
    }
    placed = fit(g)
    if (!placed) { legend = true; g = geometry(22 + LEGEND) }
  }
  const { X, Y, xy, todayX, B } = g
  const pts = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
  const at = (id: string) => placed?.find((p) => p.id === id) ?? null
  const ticks: { i: number; label: string }[] = []
  if (goal.period === 'year') {
    for (let m = 0; m < 12; m += 3) { const iso = `${goal.period_start.slice(0, 4)}-${String(m + 1).padStart(2, '0')}-01`; ticks.push({ i: dayCount(goal.period_start, iso), label: monthShort(iso) }) }
  } else {
    for (let d = 0; d < total; d += 7) ticks.push({ i: d, label: shortDate(addDays(goal.period_start, d)) })
  }
  const ys: number[] = []
  for (let v = 0; v <= max + 1e-9; v += step) ys.push(v)
  const unit = (n: number) => unitWord(goal.metric, n)
  const col = ok ? 'var(--success)' : 'var(--primary)'
  const tickY = h - B + 18
  const paceAt = at('pace'), projAt = at('proj')
  const legendProjX = L + 22 + textWidth(paceText, 11) + 16
  return (
    <div ref={ref} style={{ height: h }}>
      {w > 0 && (
        <svg width={w} height={h} className="block overflow-visible text-[11px]" role="img" aria-label={t`This period so far against the pace`}>
          {ys.map((v) => (
            <g key={v}>
              <line x1={L} x2={w - R} y1={Y(v)} y2={Y(v)} stroke="var(--border)" strokeOpacity={v === 0 ? 1 : 0.5} />
              <text x={L - 8} y={Y(v) + 4} textAnchor="end" fill="var(--muted-foreground)">{num(v)}</text>
            </g>
          ))}
          {ticks.map((k) => <text key={k.i} x={X(k.i)} y={tickY} textAnchor={k.i === 0 ? 'start' : 'middle'} fill="var(--muted-foreground)">{k.label}</text>)}
          <line className={K.wipe} x1={X(0)} y1={Y(0)} x2={X(total)} y2={Y(target)} stroke="var(--muted-foreground)" strokeDasharray="5 4" />
          {paceAt && <text data-label="pace" x={paceAt.x} y={paceAt.y} textAnchor="end" fill="var(--muted-foreground)">{paceText}</text>}
          {showProj && (
            <g className={K.wipe}>
              <line x1={todayX} y1={Y(last)} x2={X(total)} y2={Y(proj!)} stroke="var(--primary)" strokeOpacity="0.75" strokeWidth="2" strokeDasharray="2 4" strokeLinecap="round" />
              {projAt && <text data-label="proj" x={projAt.x} y={projAt.y} textAnchor="end" fill="var(--primary)">{projText}</text>}
            </g>
          )}
          <polyline className={K.draw} pathLength={1} points={pts.join(' ')} fill="none" stroke={col} strokeWidth="2.5" strokeLinejoin="round" />
          {days.map((d, i) => d.value > 0 && (
            <circle
              key={d.date}
              className={cn('k-cell', K.fade)}
              style={stagger(i, days.length)}
              cx={X(i)}
              cy={Y(d.cumulative)}
              r="4"
              fill={col}
              {...tipAttrs({ title: dayName(d.date), value: plural(d.value, { one: 'Finished # book', other: 'Finished # books' }), note: (() => { const c = num(d.cumulative); const u = unit(d.cumulative); return goal.period === 'year' ? t`${c} ${u} this year` : t`${c} ${u} this month` })() })}
            />
          ))}
          <line x1={todayX} x2={todayX} y1={T - 6} y2={h - B} stroke="var(--foreground)" strokeOpacity="0.5" />
          <text x={todayX} y={T - 9} textAnchor="middle" fill="var(--foreground)" fontWeight="600">{t`today`}</text>
          {legend && (
            <g data-legend="" transform={`translate(0 ${h - 3})`}>
              <line x1={L} x2={L + 16} y1={-4} y2={-4} stroke="var(--muted-foreground)" strokeDasharray="5 4" />
              <text x={L + 22} y={0} fill="var(--muted-foreground)">{paceText}</text>
              {showProj && (
                <>
                  <line x1={legendProjX} x2={legendProjX + 16} y1={-4} y2={-4} stroke="var(--primary)" strokeOpacity="0.75" strokeWidth="2" strokeDasharray="2 4" strokeLinecap="round" />
                  <text x={legendProjX + 22} y={0} fill="var(--primary)">{projText}</text>
                </>
              )}
            </g>
          )}
        </svg>
      )}
    </div>
  )
}

/** A past period's name in a tip ("October 2026", "Week of Nov 9", "2025"). */
function periodName(p: HistoryPeriod, period: string): string {
  if (period === 'year') return p.start.slice(0, 4)
  if (period === 'month') return monthName(p.start, true)
  const d = shortDate(p.start)
  return t`Week of ${d}`
}
function periodNote(p: HistoryPeriod, books: boolean): string {
  switch (p.state) {
    case 'met': return t`Met`
    case 'missed': return t`Missed`
    case 'current': return p.reached ? t`Reached, still open` : t`Still open`
    case 'no_data': return books ? t`No finish dates yet` : t`No reading recorded yet`
    default: return p.reached ? t`Before the goal: it would have been met` : t`Before the goal: it would have been missed`
  }
}

/** Past periods against the target that applied (met in success, missed neutral, no data dashed), and the running one. */
export function PeriodBars({ goal, history, h }: { goal: Goal; history: GoalHistory; h: number }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const all: HistoryPeriod[] = [...history.periods, ...(history.current ? [history.current] : [])]
  const n = all.length
  const books = goal.metric === 'books'
  const target = goal.target
  const ymax = Math.max(target * 2, 1)
  const B = 18, T = 8
  const gap = n > 8 ? 8 : 12
  const bw = n ? (w - gap * (n - 1)) / n : 0
  const Y = (v: number) => T + (1 - Math.min(v, ymax) / ymax) * (h - T - B)
  const label = (p: HistoryPeriod, i: number) => {
    if (goal.period === 'year') return p.start.slice(0, 4)
    if (goal.period === 'month') return monthShort(p.start)
    return i % 3 === (n - 1) % 3 ? shortDate(p.start) : ''
  }
  const value = (v: number) => goal.metric === 'minutes' ? (() => { const m = num(Math.round(v)); return t`${m} min` })() : `${num(Math.round(v))} ${unitWord(goal.metric, Math.round(v))}`
  return (
    <div ref={ref} style={{ height: h }}>
      {w > 0 && (
        <svg width={w} height={h} className="k-bars block overflow-visible text-[11px]" role="img" aria-label={t`Past periods against the target`}>
          <line x1={0} x2={w} y1={h - B} y2={h - B} stroke="var(--border)" />
          {all.map((p, i) => {
            const x = i * (bw + gap)
            const open = p.state === 'current'
            const lab = label(p, i)
            const tip = tipAttrs({ title: periodName(p, goal.period), value: p.state === 'no_data' ? '' : value(p.value), note: periodNote(p, books) })
            if (p.state === 'no_data') {
              return (
                <g key={p.start} className="k-col">
                  <rect className="k-hit" x={x - gap / 2} y={0} width={bw + gap} height={h - B} {...tip} />
                  <rect className="k-nohit" x={x + 0.5} y={Y(target)} width={Math.max(0, bw - 1)} height={h - B - Y(target)} rx="3" fill="none" stroke="var(--muted-foreground)" strokeOpacity="0.4" strokeDasharray="3 3" />
                  <text x={x + bw / 2} y={h - 4} textAnchor="middle" fill="var(--muted-foreground)">{lab}</text>
                </g>
              )
            }
            const met = !!p.reached
            const y = Y(p.value)
            return (
              <g key={p.start} className="k-col">
                <rect className="k-hit" x={x - gap / 2} y={0} width={bw + gap} height={h - B} {...tip} />
                <rect className={cn('k-bar k-nohit', K.grow)} style={stagger(i, n)} x={x} y={Math.min(y, h - B - 3)} width={bw} height={Math.max(3, h - B - y)} rx="3" fill={met ? 'var(--success)' : NEUTRAL} fillOpacity={open ? 0.6 : 1} />
                {p.value > ymax && <text className="k-nohit" x={x + bw / 2} y={T + 12} textAnchor="middle" fill="var(--background)" fontWeight="700">{num(Math.round(p.value))}</text>}
                <text x={x + bw / 2} y={h - 4} textAnchor="middle" fill={open ? 'var(--foreground)' : 'var(--muted-foreground)'} fontWeight={open ? 600 : 400}>{lab}</text>
              </g>
            )
          })}
          <line className="k-nohit" x1={0} x2={w} y1={Y(target)} y2={Y(target)} stroke="var(--foreground)" strokeOpacity="0.75" strokeDasharray="5 4" />
        </svg>
      )}
    </div>
  )
}

/** The legend of PeriodBars. */
export function PeriodLegend({ books }: { books: boolean }) {
  return (
    <span className="text-[13px] text-muted-foreground">
      <span className="mr-1 inline-block h-[9px] w-[9px] rounded-[2px] bg-success" />{t`met`} · <span className="mx-1 inline-block h-[9px] w-[9px] rounded-[2px]" style={{ background: NEUTRAL }} />{t`missed`} · <span className="mx-1 inline-block h-[9px] w-[9px] rounded-[2px] border border-dashed border-muted-foreground/60" />{books ? t`no finish dates` : t`no reading yet`}
    </span>
  )
}

/** This week of a weekly goal, day by day: bars for the days so far, outlines for the days to come, the even share as a dashed line. */
export function WeekBars({ goal, history, h }: { goal: Goal; history: GoalHistory; h: number }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const so = history.current?.days ?? []
  const today = history.today
  const dates = Array.from({ length: 7 }, (_, i) => addDays(goal.period_start, i))
  const vals = new Map(so.map((d) => [d.date, d.value]))
  const share = goal.target / 7
  const ymax = Math.max(share * 2, ...so.map((d) => d.value)) * 1.05
  const T = 10, B = 40
  const gap = 14
  const bw = Math.min(44, (w - gap * 6) / 7)
  const x0 = 0
  const Y = (v: number) => T + (1 - v / ymax) * (h - T - B)
  const value = (v: number) => goal.metric === 'minutes' ? (() => { const m = num(Math.round(v)); return t`${m} min` })() : num(Math.round(v))
  return (
    <div ref={ref} style={{ height: h }}>
      {w > 0 && (
        <svg width={w} height={h} className="k-bars block overflow-visible text-[12px]" role="img" aria-label={t`This week by day`}>
          <line x1={0} x2={w} y1={h - B} y2={h - B} stroke="var(--border)" />
          {dates.map((d, i) => {
            const x = x0 + i * (bw + gap)
            const v = vals.get(d) ?? 0
            const future = d > today
            const isToday = d === today
            const y = Y(v)
            const tip = tipAttrs({ title: dayName(d), value: future ? t`Still to come` : `${value(v)}${goal.metric === 'pages' ? ` ${unitWord('pages', Math.round(v))}` : ''}`, note: isToday ? t`Today, so far` : undefined })
            return (
              <g key={d} className="k-col">
                <rect className="k-hit" x={x - gap / 2} y={0} width={bw + gap} height={h - B} {...tip} />
                {future
                  ? <rect className="k-nohit" x={x + 0.5} y={h - B - 10} width={bw - 1} height={10} rx="3" fill="none" stroke={FUTURE} />
                  : <rect className={cn('k-bar k-nohit', K.grow)} style={stagger(i, 7)} x={x} y={Math.min(y, h - B - 3)} width={bw} height={Math.max(3, h - B - y)} rx="3" fill={v > 0 ? 'var(--primary)' : MISS} stroke={isToday ? 'var(--foreground)' : 'none'} strokeOpacity="0.55" strokeWidth={isToday ? 1.5 : 0} />}
                <text x={x + bw / 2} y={h - B + 16} textAnchor="middle" fill={isToday ? 'var(--foreground)' : 'var(--muted-foreground)'} fontWeight={isToday ? 700 : 400}>{weekdayLetter(d)}</text>
                <text x={x + bw / 2} y={h - B + 32} textAnchor="middle" fill="var(--foreground)">{future ? '' : value(v)}</text>
              </g>
            )
          })}
          <line className="k-nohit" x1={0} x2={x0 + 7 * bw + 6 * gap} y1={Y(share)} y2={Y(share)} stroke="var(--foreground)" strokeOpacity="0.6" strokeDasharray="4 3" />
          <text className="k-nohit" x={x0 + 7 * bw + 6 * gap} y={Y(share) - 5} textAnchor="end" fill="var(--muted-foreground)">{(() => { const s = value(share); return t`${s} a day keeps pace` })()}</text>
        </svg>
      )}
    </div>
  )
}

/** The last weeks as a grid of days: a column per week, a row per weekday in the reader's order. */
export function WeekGrid({ goal, history, cell, gap }: { goal: Goal; history: GoalHistory; cell: number; gap: number }) {
  const weeks = history.weeks ?? []
  if (!weeks.length) return null
  const val = (d: HistoryDay) => {
    if (goal.metric === 'days') return d.value > 0 ? t`A reading day` : t`Not read`
    if (goal.metric === 'minutes') { const m = num(Math.round(d.value)); return t`${m} min` }
    const p = num(Math.round(d.value)); return t`${p} pages`
  }
  return (
    <div className="flex" style={{ gap }} role="img" aria-label={t`The last weeks day by day`}>
      <div className="flex flex-col pr-1" style={{ gap }}>
        {weeks[0].days.map((d) => <span key={d.date} className="text-right text-[10px] leading-none text-muted-foreground" style={{ height: cell, lineHeight: `${cell}px` }}>{weekdayLetter(d.date)}</span>)}
      </div>
      {weeks.map((wk, i) => (
        <div key={wk.start} className="relative flex flex-col" style={{ gap, width: cell }}>
          {wk.days.map((d, j) => {
            const m = gridMark(d)
            const note = d.state === 'no_data' ? t`Before your data starts` : d.state === 'before_goal' ? (d.reached ? t`Before the goal: reached` : t`Before the goal: missed`) : d.state === 'met' ? t`Reached` : d.state === 'missed' ? t`Missed` : d.state === 'today' ? t`Today, so far` : undefined
            return (
              <span key={d.date} className="k-dot block rounded-full" {...tipAttrs({ title: dayName(d.date), value: d.state === 'future' ? t`Still to come` : d.state === 'no_data' ? '' : val(d), note })}>
                <Dot m={m} size={cell} className={K.fade} style={{ ...stagger(i * 7 + j, weeks.length * 7), ...(m.none ? { opacity: 0.45 } : {}) }} />
              </span>
            )
          })}
          <span className="h-[12px] whitespace-nowrap text-[10px] leading-none text-muted-foreground">{i % 3 === 0 ? shortDate(wk.start) : ''}</span>
        </div>
      ))}
    </div>
  )
}

/** The legend of the large week (shown only in the Goals tab's detail). */
export function MarkLegend() {
  const items: [{ state: 'hit' | 'miss' | 'today' | 'future'; fill: number }, string][] = [
    [{ state: 'hit', fill: 1 }, t`reached`],
    [{ state: 'miss', fill: 0 }, t`missed`],
    [{ state: 'today', fill: 0.4 }, t`today, filling`],
    [{ state: 'future', fill: 0 }, t`still to come`],
  ]
  return (
    <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1.5 text-[12px] text-muted-foreground">
      {items.map(([m, l]) => <span key={l} className="inline-flex items-center gap-1.5"><Dot m={m} size={12} />{l}</span>)}
    </div>
  )
}

/** A finish-by goal's road to the date: the day it was set, today, the date. */
export function Timeline({ goal }: { goal: Goal }) {
  const start = goal.start_date
  const due = goal.due_date ?? goal.period_end
  const today = goal.finish ? addDays(due, -(goal.finish.days_left - 1)) : start
  const span = Math.max(1, dayCount(start, due))
  const at = Math.max(0, Math.min(1, dayCount(start, today) / span))
  return (
    <div className="pt-1">
      <div className="relative h-[6px] rounded-full" style={{ background: 'color-mix(in oklch, var(--foreground) 12%, var(--background))' }}>
        <div className="k-grow-x absolute inset-y-0 left-0 rounded-full bg-foreground/40" style={{ width: `${at * 100}%` }} />
        <span className="absolute -top-[5px] h-[16px] w-[2px] rounded-full bg-foreground" style={{ left: `calc(${(at * 100).toFixed(2)}% - 1px)` }} aria-hidden="true" />
      </div>
      <div className="relative mt-2 h-[18px] text-[12px] text-muted-foreground">
        {at > 0.22 && <span className="absolute left-0">{(() => { const d = mediumDate(start); return t`Set ${d}` })()}</span>}
        <span className="absolute whitespace-nowrap font-semibold text-foreground" style={at > 0.22 && at < 0.78 ? { left: `${at * 100}%`, transform: 'translateX(-50%)' } : at <= 0.22 ? { left: 0 } : { right: 0 }}>{t`today`}</span>
        {at < 0.78 && <span className="absolute right-0">{mediumDate(due)}</span>}
      </div>
    </div>
  )
}

/** A young year goal's months this year: each month's finishes against the monthly share of the
    target (dashed), and the running total to the target as a line on its own scale (right edge). */
export function YearMonths({ goal, months, h }: { goal: Goal; months: YearMonth[]; h: number }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const n = months.length
  const share = months[0]?.share ?? goal.target / 12
  const ymax = Math.max(share * 2, 1)
  const B = 18, T = 8, R = 26
  const gap = 8
  const bw = n ? (w - R - gap * (n - 1)) / n : 0
  const Y = (v: number) => T + (1 - Math.min(v, ymax) / ymax) * (h - T - B)
  const cmax = Math.max(goal.target, ...months.map((m) => m.cumulative ?? 0))
  const C = (v: number) => T + (1 - v / cmax) * (h - T - B)
  const pts = months.filter((m) => m.cumulative != null).map((m, i) => `${(i * (bw + gap) + bw).toFixed(1)},${C(m.cumulative!).toFixed(1)}`)
  const value = (v: number) => `${num(Math.round(v))} ${unitWord(goal.metric, Math.round(v))}`
  const note = (m: YearMonth) => m.state === 'future' ? t`Still to come` : m.state === 'no_data' ? t`No finish dates yet` : m.state === 'current' ? t`Still open` : m.reached ? t`At the monthly share` : t`Under the monthly share`
  return (
    <div ref={ref} style={{ height: h }}>
      {w > 0 && (
        <svg width={w} height={h} className="k-bars block overflow-visible text-[11px]" role="img" aria-label={t`This year by month against the monthly share`}>
          <line x1={0} x2={w - R} y1={h - B} y2={h - B} stroke="var(--border)" />
          {months.map((m, i) => {
            const x = i * (bw + gap)
            const lab = monthShort(m.start)
            const open = m.state === 'current'
            const tip = tipAttrs({ title: monthName(m.start, true), value: m.state === 'future' || m.state === 'no_data' ? '' : value(m.value), note: m.cumulative != null && m.state !== 'no_data' ? `${note(m)} · ${(() => { const c = num(m.cumulative!); return t`${c} so far this year` })()}` : note(m) })
            const labEl = <text x={x + bw / 2} y={h - 4} textAnchor="middle" fill={open ? 'var(--foreground)' : 'var(--muted-foreground)'} fontWeight={open ? 600 : 400}>{lab}</text>
            if (m.state === 'future' || m.state === 'no_data') {
              return (
                <g key={m.start} className="k-col">
                  <rect className="k-hit" x={x - gap / 2} y={0} width={bw + gap} height={h - B} {...tip} />
                  {m.state === 'no_data'
                    ? <rect className="k-nohit" x={x + 0.5} y={Y(share)} width={Math.max(0, bw - 1)} height={h - B - Y(share)} rx="3" fill="none" stroke="var(--muted-foreground)" strokeOpacity="0.4" strokeDasharray="3 3" />
                    : <rect className="k-nohit" x={x + 0.5} y={h - B - 8} width={Math.max(0, bw - 1)} height={8} rx="3" fill="none" stroke={FUTURE} />}
                  {labEl}
                </g>
              )
            }
            const y = Y(m.value)
            return (
              <g key={m.start} className="k-col">
                <rect className="k-hit" x={x - gap / 2} y={0} width={bw + gap} height={h - B} {...tip} />
                <rect className={cn('k-bar k-nohit', K.grow)} style={stagger(i, n)} x={x} y={Math.min(y, h - B - 3)} width={bw} height={Math.max(3, h - B - y)} rx="3" fill={m.reached ? 'var(--success)' : NEUTRAL} fillOpacity={open ? 0.6 : 1} />
                {m.value > ymax && <text className="k-nohit" x={x + bw / 2} y={T + 12} textAnchor="middle" fill="var(--background)" fontWeight="700">{num(Math.round(m.value))}</text>}
                {labEl}
              </g>
            )
          })}
          <line className="k-nohit" x1={0} x2={w - R} y1={Y(share)} y2={Y(share)} stroke="var(--foreground)" strokeOpacity="0.75" strokeDasharray="5 4" />
          {pts.length > 0 && <polyline className={cn('k-nohit', K.draw)} pathLength={1} points={pts.join(' ')} fill="none" stroke="var(--primary)" strokeWidth="2" strokeLinejoin="round" />}
          <line className="k-nohit" x1={w - R + 4} x2={w - R + 4} y1={C(goal.target)} y2={h - B} stroke="var(--primary)" strokeOpacity="0.35" />
          <text className="k-nohit" x={w} y={C(goal.target) + 4} textAnchor="end" fill="var(--primary)">{num(goal.target)}</text>
        </svg>
      )}
    </div>
  )
}

/** The legend of YearMonths. */
export function YearMonthsLegend() {
  return (
    <span className="text-[13px] text-muted-foreground">
      <span className="mr-1 inline-block h-[9px] w-[9px] rounded-[2px] bg-success" />{t`at the share`} · <span className="mx-1 inline-block h-[9px] w-[9px] rounded-[2px]" style={{ background: NEUTRAL }} />{t`under it`} · <span className="mx-1 inline-block h-[2px] w-[12px] rounded-full bg-primary align-middle" />{t`the year so far`}
    </span>
  )
}
