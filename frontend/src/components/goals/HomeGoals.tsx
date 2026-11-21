// Home's reading goals: one row of at most four goals (two rows of two on the
// phone), the same cards as on Stats › Goals (a card opens it there), picked by
// what needs attention (homePick: behind first, reached last). With more goals,
// a quiet "See all" under the row opens the Goals tab. Home draws in one paint
// once its requests are in (the goals among them), so the cards never push what
// is below them.
import { Link, useNavigate } from 'react-router-dom'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import type { GoalsAnswer } from '@/lib/goals'
import { homePick } from '@/lib/goalsModel'
import { ONCE, useEntryRef } from '@/components/stats/kit/motion'
import { DESKTOP_MIN, num, useWidth } from '@/components/stats/kit/tokens'
import { GoalGrid } from './GoalCard'
import { TipLayer } from '@/components/stats/kit/TipLayer'

export function HomeGoals({ answer }: { answer: GoalsAnswer }) {
  const navigate = useNavigate()
  const [ref, width] = useWidth<HTMLDivElement>()
  const [e, attach] = useEntryRef('home/goals', ONCE)
  const shown = homePick(answer.goals)
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
      {width > 0 && total > shown.length && (
        <div className="mt-4 flex justify-end text-[15px] font-medium">
          <Link to="/stats?tab=goals" data-box="home-goals-all" className="text-primary hover:underline underline-offset-4">
            {(() => { const k = num(total); return <Trans>See all {k} goals</Trans> })()}
          </Link>
        </div>
      )}
    </section>
    </>
  )
}
