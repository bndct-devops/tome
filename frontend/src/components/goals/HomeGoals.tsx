// Home's reading goals: one row of at most four goals (two rows of two on the
// phone), the same cards as on Stats › Goals (a card opens it there), picked by
// what needs attention (homePick: behind first, reached last). Under the row, one
// quiet line at the start: "New goal" (the dialog, right here on Home) and, with
// more goals than the row shows, "See all N goals" (the Goals tab). A goal just
// set here always gets a place in the row. Without goals, the same spot holds
// "Set a reading goal" (HomeGoalStart). Home draws in one paint once its requests
// are in (the goals among them), so neither the cards nor the line push what is
// below them.
import { useState } from 'react'
import { AnimatePresence } from 'motion/react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import type { Goal, GoalsAnswer } from '@/lib/goals'
import { HOME_MAX, homePick } from '@/lib/goalsModel'
import { ONCE, useEntryRef } from '@/components/stats/kit/motion'
import { DESKTOP_MIN, num, useWidth } from '@/components/stats/kit/tokens'
import { GoalGrid } from './GoalCard'
import { GoalDialog } from './GoalDialog'
import { TipLayer } from '@/components/stats/kit/TipLayer'

// A text link in the app's primary colour, its tap area grown to 44 px without moving the line.
const LINK = 'inline-flex items-center gap-1.5 -my-3 py-3 text-[15px] font-medium text-primary underline-offset-4 hover:underline focus:outline-none focus-visible:underline focus-visible:ring-2 focus-visible:ring-ring rounded-md'

export function HomeGoals({ answer }: { answer: GoalsAnswer }) {
  const navigate = useNavigate()
  const [ref, width] = useWidth<HTMLDivElement>()
  const [e, attach] = useEntryRef('home/goals', ONCE)
  const [creating, setCreating] = useState(false)
  const [fresh, setFresh] = useState<number | null>(null)
  let shown = homePick(answer.goals)
  const just = fresh != null && !shown.some((g) => g.id === fresh) ? answer.goals.find((g) => g.id === fresh) : undefined
  if (just) shown = [...shown.slice(0, HOME_MAX - 1), just]
  const total = answer.goals.length
  return (
    <>
    {/* Home has no Stats frame: the cards' tips need their own layer. */}
    <TipLayer />
    <section ref={(el: HTMLElement | null) => { ref.current = el as HTMLDivElement | null; attach(el) }} aria-label={t`Reading goals`} data-box="home-goals" className="pt-1">
      {width > 0 && (
        <GoalGrid
          goals={shown}
          count={shown.length}
          desktop={width >= DESKTOP_MIN}
          entry={e}
          shapeKey="home"
          today={answer.today}
          label={t`Reading goals`}
          pick={(g) => navigate(`/stats?tab=goals&goal=${g.id}&kind=${g.kind}`)}
        />
      )}
      {width > 0 && (
        <div data-box="home-goals-more" className="mt-4 flex flex-wrap items-center gap-x-5">
          <button type="button" onClick={() => setCreating(true)} className={LINK}>
            <Plus className="h-4 w-4" aria-hidden="true" /><Trans>New goal</Trans>
          </button>
          {total > shown.length && (
            <Link to="/stats?tab=goals" data-box="home-goals-all" className={LINK}>
              {(() => { const k = num(total); return <Trans>See all {k} goals</Trans> })()}
            </Link>
          )}
        </div>
      )}
      <AnimatePresence>{creating && <GoalDialog key="new" mode={{ kind: 'create' }} goals={answer.goals} onClose={() => setCreating(false)} onSaved={(g: Goal) => setFresh(g.id)} />}</AnimatePresence>
    </section>
    </>
  )
}

/** No goals yet: one quiet line under Home's figures, the dialog right here. */
export function HomeGoalStart({ goals }: { goals: Goal[] }) {
  const [creating, setCreating] = useState(false)
  return (
    <div data-box="home-goal-start" className="flex flex-wrap items-center gap-x-2">
      <button type="button" onClick={() => setCreating(true)} className={LINK}>
        <Plus className="h-4 w-4" aria-hidden="true" /><Trans>Set a reading goal</Trans>
      </button>
      <span className="text-[14px] text-muted-foreground"><Trans>Progress counts automatically.</Trans></span>
      <AnimatePresence>{creating && <GoalDialog key="new" mode={{ kind: 'create' }} goals={goals} onClose={() => setCreating(false)} />}</AnimatePresence>
    </div>
  )
}
