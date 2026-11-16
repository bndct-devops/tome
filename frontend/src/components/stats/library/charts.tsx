// The Library page's charts: small bar charts (book length, published, time to
// finish) and the monthly additions stacked by type. Plain SVG on whole pixels
// at a width the page measures before its first paint, like the Overview's.
// Each bar column is a hit area (k-hit) with a tip from a chart-wide lookup
// (kit/tip.ts: one focus stop, arrow keys between columns); bars grow from the
// base and the counts above them fade in when the section's entry plays.
import { useId, type ReactNode } from 'react'
import { t } from '@lingui/core/macro'
import { num } from '@/components/stats/kit/tokens'
import { K, stagger } from '@/components/stats/kit/motion'
import { useTips, type Tip } from '@/components/stats/kit/tip'
import type { AddedModel } from './model'
import { fmtMonth, fmtMonthShort, typeColor } from './model'

const GRID = 'color-mix(in oklch, var(--border) 70%, transparent)'
// Onest at 11 px: about 0.545 em per character.
const textW = (s: string, px = 11) => Math.ceil(s.length * px * 0.545)


const barPath = (x: number, yt: number, w: number, base: number, r = 4) => {
  const h = base - yt
  const rr = Math.max(0, Math.min(r, h, w / 2))
  // eslint-disable-next-line lingui/no-unlocalized-strings -- SVG path
  return `M${x},${base} V${yt + rr} Q${x},${yt} ${x + rr},${yt} H${x + w - rr} Q${x + w},${yt} ${x + w},${yt + rr} V${base} Z`
}

/** A chart with no data yet: a block in the chart's own box. */
export function ChartPlaceholder({ W, H, y = 0, h }: { W: number; H: number; y?: number; h?: number }) {
  return (
    <svg className="block" width={Math.max(1, W)} height={H} viewBox={`0 0 ${Math.max(1, W)} ${H}`} aria-hidden="true">
      <rect x={0} y={y} width={Math.max(1, W)} height={h ?? H - y} rx={8} fill="var(--muted)" />
    </svg>
  )
}

export type SmallBar = { label: string; value: number; tip: Omit<Tip, 'anchor'> }

// Quiet gridlines, the count above each bar, the label under it, no y labels.
export function SmallBars({ W, H, items, label }: { W: number; H: number; items: SmallBar[]; label: string }) {
  const padT = 18, padB = 22
  const plotH = H - padT - padB
  const max = Math.max(1, ...items.map((i) => i.value))
  const STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000]
  const step = STEPS.find((s) => max / s <= 4) ?? 1000
  const top = Math.max(max, Math.ceil(max / step) * step)
  const y = (v: number) => padT + plotH - (v / top) * plotH
  const base = Math.round(y(0))
  const slot = W / Math.max(1, items.length)
  const bw = Math.max(2, Math.round(Math.min(slot * 0.6, 56)))
  // Labels too wide for their slot: every k-th one.
  const widest = Math.max(0, ...items.map((i) => textW(i.label)))
  const every = Math.max(1, Math.ceil((widest + 8) / slot))
  const lines: number[] = []
  for (let v = step; v <= top; v += step) lines.push(v)
  const tips = useTips((i) => items[i]?.tip ?? null, { x: 1 })
  return (
    <svg {...tips} className="block fill-muted-foreground text-[11px] tabular-nums" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      {lines.map((v) => {
        const yy = Math.round(y(v)) + 0.5
        return <line key={v} x1={0} x2={W} y1={yy} y2={yy} stroke={GRID} shapeRendering="crispEdges" />
      })}
      <line x1={0} x2={W} y1={base + 0.5} y2={base + 0.5} stroke="var(--border)" shapeRendering="crispEdges" />
      <g className="k-bars">
      {items.map((it, i) => {
        const x = Math.round(slot * i + (slot - bw) / 2)
        const yt = Math.round(y(it.value))
        const cx = x + bw / 2
        return (
          <g key={i} className="k-col">
            <rect className="k-hit" data-tip-i={i} x={Math.round(slot * i)} y={0} width={Math.round(slot * (i + 1)) - Math.round(slot * i)} height={base} />
            {it.value > 0 && <path className={`${K.grow} k-bar k-nohit`} style={stagger(i, items.length)} d={barPath(x, yt, bw, base)} fill="var(--chart-accent)" />}
            <text className={`${K.fade} k-nohit`} style={stagger(i, items.length)} x={cx} y={yt - 6} textAnchor="middle" fill="var(--foreground)" fontWeight={600}>{num(it.value)}</text>
            {i % every === 0 && <text className="k-nohit" x={cx} y={H - 6} textAnchor="middle">{it.label}</text>}
          </g>
        )
      })}
      </g>
    </svg>
  )
}

// Monthly additions stacked by type. A month far above the rest is drawn to the
// top of the plot, cut across by a zigzag gap (a real gap: the bar is masked, so
// the ground shows through) with its count and "bar shortened" above it; its
// segments keep their shares inside the drawn height, so its mix stays readable.
// Its tip gives the real count and says the bar is not to scale.
export function AddedChart({ W, H, m }: { W: number; H: number; m: AddedModel }) {
  const padL = 30, padT = 26, padB = 24
  const plotW = W - padL, plotH = H - padT - padB
  const y = (v: number) => padT + plotH - (v / m.top) * plotH
  const base = Math.round(y(0))
  const n = Math.max(1, m.months.length)
  const slot = plotW / n
  const bw = Math.max(2, Math.round(Math.min(slot * 0.5, 48)))
  const every = Math.max(1, Math.ceil(30 / slot))
  const lines: number[] = []
  for (let v = m.step; v <= m.top; v += m.step) lines.push(v)
  const cutId = `k-cut-${useId().replace(/[^\w-]/g, '')}`
  const tips = useTips((i) => {
    const mo = m.months[i]
    if (!mo) return null
    const when = fmtMonth(mo.month)
    const total = num(mo.total)
    const parts = mo.byType.map((v, k) => ({ v, k })).filter((s) => s.v > 0).sort((a, b) => b.v - a.v).map((s) => { const ty = m.types[s.k], n = num(s.v); return `${ty} ${n}` })
    const note = i === m.outlier ? t`The bar is not to scale: it is cut short so the other months stay readable.` : undefined
    return { title: when, value: mo.total === 1 ? t`1 book added` : t`${total} books added`, note: [parts.join(' · '), note].filter(Boolean).join('. ') || undefined }
  }, { x: 1 })
  return (
    <svg {...tips} className="block fill-muted-foreground text-[11px] tabular-nums" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t`Books added per month`}>
      {lines.map((v) => {
        const yy = Math.round(y(v)) + 0.5
        return (
          <g key={v}>
            <line x1={padL} x2={W} y1={yy} y2={yy} stroke={GRID} shapeRendering="crispEdges" />
            <text x={padL - 8} y={yy + 4} textAnchor="end">{num(v)}</text>
          </g>
        )
      })}
      <line x1={padL} x2={W} y1={base + 0.5} y2={base + 0.5} stroke="var(--border)" shapeRendering="crispEdges" />
      <text x={padL - 8} y={base + 4} textAnchor="end">0</text>
      <g className="k-bars">
      {m.months.map((mo, i) => {
        const x = Math.round(padL + slot * i + (slot - bw) / 2)
        const broken = i === m.outlier
        const scale = broken ? plotH / mo.total : plotH / m.top
        const segs = mo.byType.map((v, k) => ({ v, k })).filter((s) => s.v > 0)
        let yb = base
        const parts = segs.map((s, j) => {
          const last = j === segs.length - 1
          const yt = last ? (broken ? padT : Math.round(base - mo.total * scale)) : Math.round(yb - s.v * scale)
          const el = last ? (
            <path key={s.k} d={barPath(x, yt, bw, yb)} style={{ fill: typeColor(s.k) }} />
          ) : (
            <rect key={s.k} x={x} y={yt} width={bw} height={Math.max(0, yb - yt)} style={{ fill: typeColor(s.k) }} />
          )
          yb = yt
          return el
        })
        const cx = x + bw / 2
        let top: ReactNode = null
        let cut: ReactNode = null
        if (broken) {
          const total = num(mo.total)
          const short = t({ message: 'bar shortened', comment: 'Beside the count above a bar drawn shorter than its value (an axis break)' })
          const half = textW(`${total} ${short}`) / 2
          const lx = Math.min(W - half, Math.max(half, cx))
          // The gap: a zigzag band 6 px tall across the bar, 18 px under its top, cut out by a mask.
          const gy = padT + 18, tooth = Math.max(3, Math.min(6, bw / 4))
          const edge = (y0: number) => {
            const pts: string[] = []
            for (let k = 0, px = x - 1; px <= x + bw + 1 + tooth; k++, px += tooth) pts.push(`${Math.min(px, x + bw + 1)},${y0 + (k % 2 ? 3 : 0)}`)
            return pts
          }
          const upper = edge(gy), lower = edge(gy + 6).reverse()
          cut = (
            <mask id={cutId} maskUnits="userSpaceOnUse" x={0} y={0} width={W} height={H}>
              <rect x={0} y={0} width={W} height={H} fill="white" />
              <polygon points={[...upper, ...lower].join(' ')} fill="black" />
            </mask>
          )
          top = (
            <text className={`${K.fade} k-nohit`} style={stagger(i, n)} x={lx} y={padT - 9} textAnchor="middle" fill="var(--foreground)" fontWeight={600}>
              {total} <tspan className="fill-muted-foreground" fontWeight={400}>{short}</tspan>
            </text>
          )
        } else if (mo.total > 0) {
          top = <text className={`${K.fade} k-nohit`} style={stagger(i, n)} x={cx} y={Math.round(base - mo.total * scale) - 6} textAnchor="middle" fill="var(--foreground)" fontWeight={600}>{num(mo.total)}</text>
        }
        // January shows its year, so a chart across a new year reads in order.
        const lab = mo.month.endsWith('-01') && i > 0 ? mo.month.slice(0, 4) : fmtMonthShort(mo.month)
        return (
          <g key={mo.month} className="k-col">
            <rect className="k-hit" data-tip-i={i} x={Math.round(padL + slot * i)} y={0} width={Math.round(padL + slot * (i + 1)) - Math.round(padL + slot * i)} height={base} />
            {cut}
            {parts.length > 0 && <g className={`${K.grow} k-bar k-nohit`} style={stagger(i, n)} mask={broken ? `url(#${cutId})` : undefined}>{parts}</g>}
            {top}
            {i % every === 0 && <text className="k-nohit" x={cx} y={H - 6} textAnchor="middle">{lab}</text>}
          </g>
        )
      })}
      </g>
    </svg>
  )
}
