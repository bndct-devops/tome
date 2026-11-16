// The Habits page's charts: plain SVG on whole pixels at a measured width, each
// in a box whose height is fixed before any data arrives. Shaping is in model.ts.
import { t } from '@lingui/core/macro'
import { formatDuration } from '@/lib/utils'
import { columns, type HourGrid } from '@/components/stats/overview/model'
import {
  RHYTHM_ROW, RHYTHM_ROW_PHONE, TL_PITCH, TL_PITCH_PHONE, TL_TRACK, TL_TRACK_PHONE, TL_ROWS, MONTH_ROW,
  monthsHeight, rhythmHeight, timelineHeight, type PaceDot, type TlRow,
} from './model'

const LV = ['var(--k-lv0)', 'var(--k-lv1)', 'var(--k-lv2)', 'var(--k-lv3)', 'var(--k-lv4)', 'var(--k-lv5)']
const GRID = 'color-mix(in oklch, var(--border) 70%, transparent)'
const TICK = 'color-mix(in oklch, var(--muted-foreground) 60%, transparent)'
const SVG = 'block fill-muted-foreground text-[11px] tabular-nums'
const hh = (h: number) => `${String(h % 24).padStart(2, '0')}:00`
const clock = (d: Date) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
/** "Tue 3": the weekday and the day of the month. */
const dayLabel = (iso: string) => {
  // eslint-disable-next-line lingui/no-unlocalized-strings -- ISO time suffix
  const d = new Date(iso + 'T00:00:00')
  return `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${d.getDate()}`
}
const fmtShort = (iso: string) => new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

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

// ── When you read: hour × weekday, weekday totals right, hour totals under ────
export function Rhythm({ W, grid, byCol, byDow, phone, loading }: { W: number; grid: HourGrid; byCol: number[]; byDow: number[]; phone: boolean; loading?: boolean }) {
  const ncol = phone ? 12 : 24
  const span = phone ? 2 : 1
  const labelW = phone ? 32 : 40
  const rightW = phone ? 58 : 208
  const rowH = phone ? RHYTHM_ROW_PHONE : RHYTHM_ROW
  const gap = 2
  const { cell, xs } = columns(labelW, W - labelW - rightW, ncol, gap)
  const heatR = xs[ncol - 1] + cell
  const rp = rowH + gap
  const heatH = 7 * rp - gap
  const histH = phone ? 48 : 64
  const base = heatH + 12 + histH
  const H = rhythmHeight(phone)
  if (loading) return <Placeholder W={W} H={H} x={labelW} h={heatH} />
  const days = [t`Mon`, t`Tue`, t`Wed`, t`Thu`, t`Fri`, t`Sat`, t`Sun`]
  const daysLong = [t`Mondays`, t`Tuesdays`, t`Wednesdays`, t`Thursdays`, t`Fridays`, t`Saturdays`, t`Sundays`]
  const maxDow = Math.max(1, ...byDow)
  const barX = heatR + 24
  const barMax = W - 64 - barX
  const maxH = Math.max(1, ...byCol)
  return (
    <svg className={SVG} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Reading time by hour and weekday`}>
      {grid.map((row, r) => {
        const y = r * rp
        const tot = formatDuration(byDow[r])
        const dayLong = daysLong[r]
        return (
          <g key={r}>
            <text x={0} y={y + rowH / 2 + 4}>{days[r]}</text>
            {row.map((c, k) => {
              const day = days[r]
              const range = `${hh(k * span)}–${hh(k * span + span)}`
              const dur = formatDuration(c.s)
              return (
                <rect key={k} x={xs[k]} y={y} width={cell} height={rowH} rx={3} fill={LV[c.lv]}>
                  <title>{t`${day} ${range}: ${dur}`}</title>
                </rect>
              )
            })}
            {!phone && byDow[r] > 0 && (
              <rect x={barX} y={y + (rowH - 12) / 2} width={Math.max(2, Math.round((byDow[r] / maxDow) * barMax))} height={12} rx={2} fill="var(--chart-accent)">
                <title>{`${dayLong}: ${tot}`}</title>
              </rect>
            )}
            {byDow[r] > 0 ? (
              <text x={W} y={y + rowH / 2 + 4} textAnchor="end" fill="var(--foreground)" fontSize={12} fontWeight={500}>{tot}</text>
            ) : (
              <text x={W} y={y + rowH / 2 + 4} textAnchor="end" opacity={0.5}>–</text>
            )}
          </g>
        )
      })}
      {byCol.map((s, k) => {
        const h = Math.round((s / maxH) * histH)
        if (h <= 0) return null
        const range = `${hh(k * span)}–${hh(k * span + span)}`
        const dur = formatDuration(s)
        return (
          <path key={k} d={roundedTop(xs[k], base - h, cell, h, Math.min(2, h))} fill="var(--chart-accent)">
            <title>{t`${range}, all days: ${dur}`}</title>
          </path>
        )
      })}
      <line x1={labelW} x2={heatR} y1={base + 0.5} y2={base + 0.5} stroke="var(--border)" shapeRendering="crispEdges" />
      {[0, 6, 12, 18, 24].map((hr) => {
        const k = hr / span
        const x = k >= ncol ? heatR : xs[k]
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

// ── Last 14 days: a 04:00 → 04:00 track per reading day ───────────────────────
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
  return (
    <svg className={SVG} style={{ fontSize }} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Sittings in the last 14 reading days, 04:00 to 04:00`}>
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
        return (
          <g key={r.date}>
            <text x={0} y={y + trackH / 2 + 4}>{dayLabel(r.date)}</text>
            <rect x={x0} y={y} width={tw} height={trackH} rx={3} fill={LV[0]} />
            {r.bars.map((b, k) => {
              const a = Math.round(xm(b.m0))
              const e = Math.max(a + 2, Math.round(xm(b.m1)))
              const title = b.title ?? t`Unknown book`
              const from = clock(b.started)
              const to = clock(new Date(b.started.getTime() + b.seconds * 1000))
              const dur = formatDuration(b.seconds)
              return (
                <rect key={k} x={a} y={y + 2} width={e - a} height={trackH - 4} rx={2} fill="var(--chart-accent)">
                  <title>{t`${title}: ${from} to ${to} (${dur})`}</title>
                </rect>
              )
            })}
            {r.seconds > 0 ? (
              <text x={W} y={y + trackH / 2 + 4} textAnchor="end" fill="var(--foreground)" fontWeight={500}>{formatDuration(r.seconds)}</text>
            ) : (
              <text x={W} y={y + trackH / 2 + 4} textAnchor="end" opacity={0.5}>–</text>
            )}
          </g>
        )
      })}
      {[6, 12, 18].map((h) => {
        const x = Math.round(xm(h * 60)) + 0.5
        return <line key={h} x1={x} x2={x} y1={yTrack(0)} y2={yb} stroke="var(--background)" shapeRendering="crispEdges" />
      })}
      {[0, 6, 12, 18, 24].map((h) => (
        <text key={h} x={Math.round(xm(h * 60))} y={yb + 14} textAnchor={h === 0 ? 'start' : h === 24 ? 'end' : 'middle'}>{(h + 4) % 24}</text>
      ))}
    </svg>
  )
}

// ── Session length: five buckets, the count above each bar ────────────────────
export function Lengths({ W, H, counts, loading }: { W: number; H: number; counts: number[]; loading?: boolean }) {
  const valH = 18, labH = 22
  const plotH = H - valH - labH
  const base = valH + plotH
  if (loading) return <Placeholder W={W} H={H} y={valH} h={plotH} />
  const labels = [t`Under 15m`, t`15 to 30m`, t`30 to 60m`, t`1 to 2h`, t`Over 2h`]
  const slot = W / labels.length
  const bw = Math.round(Math.min(slot * 0.62, 56))
  const max = Math.max(1, ...counts)
  return (
    <svg className={SVG} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Sittings by length`}>
      {counts.map((n, i) => {
        const x = Math.round(slot * i + (slot - bw) / 2)
        const h = Math.round((n / max) * plotH)
        const label = labels[i]
        return (
          <g key={i}>
            {h > 0 && (
              <path d={roundedTop(x, base - h, bw, h, Math.min(3, h))} fill="var(--chart-accent)">
                <title>{t`${label}: ${n} sittings`}</title>
              </path>
            )}
            <text x={x + bw / 2} y={base - h - 5} textAnchor="middle" fill="var(--foreground)" fontWeight={500}>{n.toLocaleString()}</text>
            <text x={x + bw / 2} y={H - 4} textAnchor="middle">{label}</text>
          </g>
        )
      })}
      <line x1={0} x2={W} y1={base + 0.5} y2={base + 0.5} stroke="var(--border)" shapeRendering="crispEdges" />
    </svg>
  )
}

// ── Pace: a dot per reading day, the trailing 7-day mean as a line ────────────
export function Pace({ W, H, dots, nDays, months, loading }: { W: number; H: number; dots: PaceDot[]; nDays: number; months: { index: number; label: string }[]; loading?: boolean }) {
  const padL = 28, padB = 22, padT = 8
  if (loading) return <Placeholder W={W} H={H} x={padL} y={padT} h={H - padT - padB} />
  const plotW = W - padL, plotH = H - padT - padB
  const top = Math.ceil(Math.max(1, ...dots.map((d) => d.ppm)) + 0.25)
  const step = top > 12 ? Math.ceil(top / 6) : 1
  const y = (v: number) => padT + plotH - (v / top) * plotH
  const x = (i: number) => padL + 6 + (nDays > 1 ? i / (nDays - 1) : 0.5) * (plotW - 12)
  const by = Math.round(y(0)) + 0.5
  const lines: number[] = []
  for (let v = step; v <= top; v += step) lines.push(v)
  const path = dots.map((d, k) => `${k ? 'L' : 'M'}${x(d.i).toFixed(1)},${y(d.mean).toFixed(1)}`).join(' ')
  return (
    <svg className={SVG} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Pages per minute per reading day`}>
      {lines.map((v) => {
        const yy = Math.round(y(v)) + 0.5
        return (
          <g key={v}>
            <line x1={padL} x2={W} y1={yy} y2={yy} stroke={GRID} shapeRendering="crispEdges" />
            <text x={padL - 8} y={yy + 4} textAnchor="end">{v}</text>
          </g>
        )
      })}
      <line x1={padL} x2={W} y1={by} y2={by} stroke="var(--border)" shapeRendering="crispEdges" />
      <text x={padL - 8} y={by + 4} textAnchor="end">0</text>
      {months.map((m) => {
        const xx = Math.round(x(m.index)) + 0.5
        return (
          <g key={m.index}>
            <line x1={xx} x2={xx} y1={by} y2={by + 5} stroke={TICK} shapeRendering="crispEdges" />
            <text x={xx + 4} y={H - 5}>{m.label}</text>
          </g>
        )
      })}
      {dots.map((d) => {
        const day = fmtShort(d.date)
        const ppm = d.ppm.toFixed(1)
        const pages = d.pages.toLocaleString()
        const dur = formatDuration(d.seconds)
        return (
          <circle key={d.i} cx={x(d.i)} cy={y(d.ppm)} r={3} fill="var(--chart-accent)" fillOpacity={0.5}>
            <title>{t`${day}: ${ppm} pages a minute, ${pages} pages in ${dur}`}</title>
          </circle>
        )
      })}
      {dots.length > 1 && <path d={path} fill="none" stroke="var(--chart-accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />}
    </svg>
  )
}

// ── The last 12 months: reading hours over books finished ─────────────────────
type Month = { label: string; seconds: number; books: number }
export function Months({ W, months, phone, loading }: { W: number; months: Month[]; phone: boolean; loading?: boolean }) {
  const labelW = phone ? 0 : 132
  const rowGap = phone ? 40 : 24, valH = 18
  const H = monthsHeight(phone)
  const top1 = phone ? 22 : 0
  const base1 = top1 + MONTH_ROW
  const top2 = base1 + rowGap + (phone ? 22 : 0)
  const base2 = top2 + MONTH_ROW
  const rowLabels = [t`Reading hours`, t`Books finished`]
  if (loading) {
    return (
      <svg className="block" width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
        <rect x={labelW} y={top1} width={W - labelW} height={MONTH_ROW} rx={8} fill="var(--muted)" />
        <rect x={labelW} y={top2} width={W - labelW} height={MONTH_ROW} rx={8} fill="var(--muted)" />
      </svg>
    )
  }
  const n = Math.max(1, months.length)
  const slot = (W - labelW) / n
  const bw = Math.round(Math.min(slot * 0.5, 40))
  const xs = months.map((_, i) => Math.round(labelW + slot * i + (slot - bw) / 2))
  const last = months.length - 1
  const row = (key: 'hours' | 'books', base: number) => {
    const val = (m: Month) => (key === 'hours' ? m.seconds : m.books)
    const lab = (m: Month) => (key === 'hours' ? formatDuration(m.seconds) : m.books.toLocaleString())
    const max = Math.max(1, ...months.map(val))
    const fill = key === 'hours' ? 'var(--chart-accent)' : 'var(--success)'
    const rowLabel = rowLabels[key === 'hours' ? 0 : 1]
    return (
      <g key={key}>
        <text x={0} y={phone ? base - MONTH_ROW - 8 : base - 2} fontSize={13}>{rowLabel}</text>
        {months.map((m, i) => {
          const v = val(m)
          const h = Math.round((v / max) * (MONTH_ROW - valH))
          const label = lab(m)
          const mLabel = m.label
          const tip = key === 'hours' ? `${mLabel}: ${label}` : t`${mLabel}: ${label} finished`
          const show = !phone || key === 'books' || (last - i) % 2 === 0
          let text = null
          if (show && phone && key === 'hours') {
            const tw = Math.ceil(label.length * 11 * 0.55)
            const cx = xs[i] + bw / 2
            const edge = cx + tw / 2 > W
            text = <text x={edge ? W : cx} y={base - MONTH_ROW + 11} textAnchor={edge ? 'end' : 'middle'} fill="var(--foreground)" fontWeight={500}>{label}</text>
          } else if (show) {
            text = <text x={xs[i] + bw / 2} y={base - h - 5} textAnchor="middle" fill="var(--foreground)" fontWeight={500}>{label}</text>
          }
          return (
            <g key={i}>
              {h > 0 && (
                <path d={roundedTop(xs[i], base - h, bw, h, Math.min(3, h))} fill={fill} fillOpacity={i === last ? 0.45 : 1}>
                  <title>{tip}</title>
                </path>
              )}
              {text}
            </g>
          )
        })}
        <line x1={labelW} x2={W} y1={base + 0.5} y2={base + 0.5} stroke="var(--border)" shapeRendering="crispEdges" />
      </g>
    )
  }
  return (
    <svg className={SVG} width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Reading hours and books finished per month`}>
      {row('hours', base1)}
      {row('books', base2)}
      {months.map((m, i) => <text key={i} x={xs[i] + bw / 2} y={H - 4} textAnchor="middle">{m.label}</text>)}
    </svg>
  )
}
