// The Overview's reading goals: one compact row (up to four goals: name, figure,
// a thin bar, the status) and the way to the Goals tab. The skeleton draws as
// many cells as the last answer had in this browser; with nothing remembered the
// Overview holds its Pending placeholder until the goals are in (Overview.tsx).
import { ChevronRight } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { cn } from '@/lib/utils'
import { PRESS } from '@/components/page'
import type { Goal } from '@/lib/goals'
import { COMPACT_MAX } from '@/lib/goalsModel'
import { Heading, Ph } from '@/components/stats/kit/Kit'
import { ONCE, useEntryRef } from '@/components/stats/kit/motion'
import { tipAttrs } from '@/components/stats/kit/tip'
import { HAIR, num } from '@/components/stats/kit/tokens'
import { LeadLine, PaceBar } from './GoalCard'
import { goalName } from './text'

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text, drawn as blocks */
const PH = { label: 'Books in November', fig: '0,000 / 0,000', lead: '00 ahead of pace' }
/* eslint-enable lingui/no-unlocalized-strings */

export function CompactRow({ goals, count, today, desktop, open }: {
  goals: Goal[] | null
  /** Cells the skeleton draws (the remembered count). */
  count: number
  today?: string
  desktop: boolean
  /** Open the Goals tab, on a goal or on the first. */
  open: (goalId?: number) => void
}) {
  const [, attach] = useEntryRef('overview/goals', goals ? ONCE : null)
  const total = goals ? goals.length : count
  const n = Math.min(COMPACT_MAX, total)
  if (n === 0) return null
  const cells: (Goal | null)[] = goals ? goals.slice(0, COMPACT_MAX) : Array.from({ length: n }, () => null)
  const more = total > COMPACT_MAX
  return (
    <section ref={attach} data-box="goals" aria-busy={!goals}>
      <Heading
        right
        note={
          <button
            type="button"
            disabled={!goals}
            onClick={() => open()}
            className="inline-flex items-center gap-0.5 text-[15px] font-medium text-primary underline-offset-4 hover:underline disabled:opacity-0"
          >
            {more ? (() => { const k = num(total); return <Trans>All {k} goals</Trans> })() : <Trans>All goals</Trans>}
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </button>
        }
      >
        <Trans>Reading goals</Trans>
      </Heading>
      <div data-box="goal-cells" className={cn('k-bars grid', desktop ? 'grid-cols-4' : 'grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-y-4')}>
        {cells.map((g, i) => {
          const name = g ? goalName(g, today) : PH.label
          const fig = g ? `${num(Math.round(g.current))} / ${num(g.target)}${g.metric === 'minutes' ? ` ${t`min`}` : ''}` : PH.fig
          const inner = (
            <>
              <div className={cn(desktop ? 'flex items-baseline justify-between gap-3' : 'flex flex-col gap-0.5')}>
                <span className={cn('min-w-0 truncate text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px] leading-snug')} {...(g ? tipAttrs({ title: name }) : {})}><Ph on={!g}>{name}</Ph></span>
                <span className={cn('shrink-0 whitespace-nowrap text-[15px] font-semibold tabular-nums', g?.reached ? 'text-success' : 'text-foreground')}><Ph on={!g}>{fig}</Ph></span>
              </div>
              <div className="mt-2">{g ? <PaceBar goal={g} thin today={today} /> : <div className="flex h-[14px] items-center"><div className="h-[4px] w-full rounded-full bg-muted/70" /></div>}</div>
              <div className="mt-1 truncate text-[13px] leading-[1.4]">{g ? <LeadLine goal={g} /> : <Ph on>{PH.lead}</Ph>}</div>
            </>
          )
          return (
            <div
              key={g ? g.id : i}
              data-box={`goal-${i}`}
              className={cn('min-w-0', desktop ? cn('px-6', i === 0 && 'pl-0', i > 0 && cn('border-l', HAIR)) : cn(i % 2 === 0 ? 'pr-3' : cn('border-l pl-4', HAIR)))}
            >
              {g ? (
                <button type="button" onClick={() => open(g.id)} className={cn('block w-full rounded-[8px] text-left touch-feedback focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4 focus-visible:ring-offset-background', PRESS)}>
                  {inner}
                </button>
              ) : <div aria-hidden="true">{inner}</div>}
            </div>
          )
        })}
      </div>
    </section>
  )
}
