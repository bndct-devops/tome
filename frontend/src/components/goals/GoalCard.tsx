// The goal card, one look in every place (Stats' Goals tab, Home): the goal's
// one name, the figure against the target, the week's marks (day goals and
// reading days) or the bar with a tick where the reader should be today, the
// status line and what it takes. Cards sit on hairlines as the Stats pages'
// figures do. GoalGrid lays them out (four columns on a desktop, two on the
// phone) and reserves their remembered heights while the goals are on their way.
import type { CSSProperties, ReactNode } from 'react'
import { Check } from 'lucide-react'
import { t } from '@lingui/core/macro'
import { cn } from '@/lib/utils'
import type { Goal } from '@/lib/goals'
import { detail, lead, markKind, stripMarks, tick, type Mark } from '@/lib/goalsModel'
import { CountUp } from '@/components/stats/kit/CountUp'
import { stagger, type Entry } from '@/components/stats/kit/motion'
import { tipAttrs } from '@/components/stats/kit/tip'
import { HAIR, num } from '@/components/stats/kit/tokens'
import { detailWords, goalName, leadWords, unitWord, weekdayLetter } from './text'
import { useShape } from './shape'
import { gridCols, markTip } from './marks'

export const TRACK = 'color-mix(in oklch, var(--primary) 28%, var(--background))'
export const TRACK_OK = 'color-mix(in oklch, var(--success) 28%, var(--background))'
/** A missed day: a dim neutral filled dot. */
export const MISS = 'color-mix(in oklch, var(--foreground) 26%, var(--background))'
/** A day still to come: a thin faint outline. */
export const FUTURE = 'color-mix(in oklch, var(--foreground) 22%, var(--background))'

const toneClass = { success: 'text-success', warning: 'text-warning', plain: 'text-foreground', muted: 'text-muted-foreground' } as const

/** A mark of the week: reached filled success; missed dim neutral, no ring; to come a faint outline; today a ring whose arc fills with today's share. */
export function Dot({ m, size = 14, className, style }: { m: Pick<Mark, 'state' | 'fill'>; size?: number; className?: string; style?: CSSProperties }) {
  const box = { width: size, height: size, ...style }
  if (m.state === 'hit') return <span className={cn('block rounded-full bg-success', className)} style={box} />
  if (m.state === 'miss') return <span className={cn('block rounded-full', className)} style={{ ...box, background: MISS }} />
  if (m.state === 'future') return <span className={cn('block rounded-full', className)} style={{ ...box, boxShadow: `inset 0 0 0 1px ${FUTURE}` }} />
  const sw = size >= 20 ? 3 : 2
  const r = size / 2 - sw / 2, c = size / 2, full = 2 * Math.PI * r
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className={cn('block', className)} style={style} aria-hidden="true">
      <circle cx={c} cy={c} r={r} fill="none" stroke="var(--foreground)" strokeOpacity="0.55" strokeWidth={sw} />
      {m.fill > 0 && <circle cx={c} cy={c} r={r} fill="none" stroke="var(--success)" strokeWidth={sw} strokeDasharray={`${full * m.fill} ${full}`} transform={`rotate(-90 ${c} ${c})`} strokeLinecap="round" />}
    </svg>
  )
}

/** The week as seven marks with the weekday under each (today's in bold). */
export function DayStrip({ goal }: { goal: Goal }) {
  const marks = stripMarks(goal.week ?? [], goal.target, goal.metric)
  return (
    <div className="flex items-end gap-[9px]" role="img" aria-label={t`This week`}>
      {marks.map((m, i) => (
        <div key={m.date} className="k-dot flex w-[16px] flex-col items-center gap-[3px] rounded-[4px]" {...tipAttrs(markTip(goal, m))}>
          <Dot m={m} className="k-fade" style={stagger(i, 7)} />
          <span className={cn('text-[10px] leading-none', m.state === 'today' ? 'font-bold text-foreground' : 'text-muted-foreground')}>{weekdayLetter(m.date)}</span>
        </div>
      ))}
    </div>
  )
}

/** The bar: progress on the accent track (success when reached), with a tick where the reader should be today. */
export function PaceBar({ goal, thin, today }: { goal: Goal; thin?: boolean; today?: string }) {
  const at = tick(goal)
  const pct = Math.round(goal.pct)
  const name = goalName(goal, today)
  const pace = goal.expected != null ? (() => { const e = num(Math.round(goal.expected!)); const u = unitWord(goal.metric, Math.round(goal.expected!)); return t`Pace today: ${e} ${u}` })() : undefined
  return (
    <div className="k-track relative flex h-[14px] items-center rounded-full" {...tipAttrs({ title: name, value: t`${pct}% of the goal`, note: at != null ? pace : undefined })}>
      <div className="flex-1 overflow-hidden rounded-full" style={{ height: thin ? 4 : 6, background: goal.reached ? TRACK_OK : TRACK }}>
        <div className={cn('k-grow-x h-full rounded-full', goal.reached ? 'bg-success' : 'bg-primary')} style={{ width: `${Math.min(100, Math.max(0, goal.pct))}%` }} />
      </div>
      {at != null && (
        <span className="absolute top-[1px] h-[12px] w-[2px] rounded-full bg-foreground" style={{ left: `calc(${(at * 100).toFixed(2)}% - 1px)` }} aria-hidden="true" />
      )}
    </div>
  )
}

/** The status line: a coloured lead ("5 ahead", "Reached") and the plain rest. */
export function LeadLine({ goal, className }: { goal: Goal; className?: string }) {
  const l = lead(goal)
  const w = leadWords(l.lead)
  return (
    <span className={className}>
      {l.lead.k === 'reached' || (l.lead.k === 'finish' && l.lead.status === 'completed')
        // The check sits inside the line's own height (an inline-flex box taller than the line would push it down).
        ? <span className="font-semibold text-success"><Check className="mr-1 inline-block h-[0.95em] w-[0.95em] align-[-0.12em]" strokeWidth={2.5} aria-hidden="true" />{w.lead}</span>
        : <span className={cn('font-semibold', toneClass[l.tone])}>{w.lead}</span>}
      {w.rest && <span className="text-foreground">{w.rest}</span>}
    </span>
  )
}

/** The figure against the target ("58 / 60 books"); the unit wraps under the figure where the cell is narrow. */
function Figure({ goal, entry, desktop }: { goal: Goal; entry: Entry; desktop: boolean }) {
  const cur = Math.round(goal.current)
  const target = num(goal.target)
  return (
    <div className={cn('flex flex-wrap items-baseline gap-x-1.5 gap-y-1 font-display font-bold tabular-nums leading-none', goal.reached ? 'text-success' : 'text-foreground')} style={{ fontSize: desktop ? 30 : 26 }}>
      <CountUp entry={entry} value={cur} format={num} />
      <span className="whitespace-nowrap font-sans text-[15px] font-semibold tracking-normal text-muted-foreground">/ {target} {unitWord(goal.metric, goal.target)}</span>
    </div>
  )
}

export function GoalCard({ goal, entry, desktop, today }: { goal: Goal; entry: Entry; desktop: boolean; today?: string }) {
  const name = goalName(goal, today)
  return (
    <div className="min-w-0 text-left">
      <div className={cn('truncate text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px] leading-snug')} {...tipAttrs({ title: name })}>{name}</div>
      <div className="mt-1.5"><Figure goal={goal} entry={entry} desktop={desktop} /></div>
      <div className="mt-3 flex h-[30px] items-start">
        {markKind(goal) === 'strip' ? <DayStrip goal={goal} /> : <div className="w-full pt-2"><PaceBar goal={goal} today={today} /></div>}
      </div>
      <div className={cn('mt-1 leading-[1.4]', desktop ? 'text-[14px]' : 'text-[13px]')}><LeadLine goal={goal} /></div>
      <div className={cn('mt-0.5 leading-[1.4] text-muted-foreground', desktop ? 'text-[13px]' : 'text-[12px]')}>{detailWords(detail(goal))}</div>
    </div>
  )
}

/** A card's skeleton: the same lines as blocks. */
function CardSkeleton({ desktop }: { desktop: boolean }) {
  return (
    <div aria-hidden="true" className="min-w-0">
      <div className={cn('h-[18px] w-[55%] rounded-[6px] bg-muted', !desktop && 'h-[17px]')} />
      <div className="mt-1.5 h-[30px] w-[62%] rounded-[8px] bg-muted" />
      <div className="mt-3 flex h-[30px] items-start pt-2"><div className="h-[6px] w-full rounded-full bg-muted/70" /></div>
      <div className="mt-1 h-[16px] w-[48%] rounded-[6px] bg-muted/70" />
      <div className="mt-1.5 h-[14px] w-[72%] rounded-[6px] bg-muted/50" />
    </div>
  )
}

/**
 * The cards on hairlines (four columns on a desktop, two on the phone). `goals`
 * null draws `count` skeleton cells at the remembered row heights (`shapeKey`
 * names where the grid is: the caller only draws a skeleton when shapeKnown()).
 * `pick` makes each card a button (Home: open the Goals tab; the Goals tab:
 * select it, `selected` underlined).
 */
export function GoalGrid({ goals, count, desktop, entry, shapeKey, pick, selected, today, label }: {
  goals: Goal[] | null
  count: number
  desktop: boolean
  entry: Entry
  shapeKey: string
  pick?: (g: Goal) => void
  selected?: number | null
  today?: string
  label: string
}) {
  const cols = gridCols(desktop)
  const n = goals ? goals.length : count
  const rows = Math.ceil(n / cols)
  const shape = useShape(`${shapeKey}:${cols}:${n}`)
  const cells: (Goal | null)[] = goals ?? Array.from({ length: n }, () => null)
  return (
    <div className={cn('grid', desktop ? 'grid-cols-4' : 'grid-cols-[minmax(0,1fr)_minmax(0,1fr)]')} role="list" aria-label={label}>
      {cells.map((g, i) => {
        const col = i % cols
        const row = Math.floor(i / cols)
        const lastRow = row === rows - 1
        const sel = !!g && selected === g.id
        const rowH = !g ? shape.h(`row${row}`) : undefined
        const cell: ReactNode = g ? <GoalCard goal={g} entry={entry} desktop={desktop} today={today} /> : <CardSkeleton desktop={desktop} />
        return (
          <div
            key={g ? g.id : `s${i}`}
            role="listitem"
            data-box={`goal-${i}`}
            ref={g && col === 0 ? shape.measure(`row${row}`) : undefined}
            style={rowH ? { height: rowH } : undefined}
            className={cn(
              'relative min-w-0',
              desktop
                ? cn('px-6 py-0.5', col === 0 && 'pl-0', col > 0 && cn('border-l', HAIR), row > 0 && cn('border-t pt-5', HAIR), !lastRow && 'pb-5')
                : cn('py-4', col === 0 ? 'pr-3' : cn('border-l pl-4', HAIR), row === 0 && 'pt-0', !lastRow ? cn('border-b', HAIR) : 'pb-0'),
            )}
          >
            {g && pick ? (
              <button
                type="button"
                onClick={() => pick(g)}
                aria-pressed={selected !== undefined ? sel : undefined}
                className="group/card block w-full rounded-[10px] text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4 focus-visible:ring-offset-background"
              >
                {cell}
              </button>
            ) : cell}
            {sel && (
              <span
                className={cn('absolute h-[3px] rounded-full bg-primary', lastRow ? '-bottom-[14px]' : 'bottom-[6px]', desktop && col > 0 ? 'left-6 right-6' : 'left-0 right-6', !desktop && col > 0 && 'left-4 right-0')}
                aria-hidden="true"
              />
            )}
          </div>
        )
      })}
    </div>
  )
}
