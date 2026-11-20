// Home's reading goals: every goal as the same card as on Stats › Goals (a card
// opens it there). Home draws in one paint once its requests are in (the goals
// among them), so the cards never push what is below them.
import { useNavigate } from 'react-router-dom'
import { t } from '@lingui/core/macro'
import type { GoalsAnswer } from '@/lib/goals'
import { ONCE, useEntryRef } from '@/components/stats/kit/motion'
import { DESKTOP_MIN, useWidth } from '@/components/stats/kit/tokens'
import { GoalGrid } from './GoalCard'

export function HomeGoals({ answer }: { answer: GoalsAnswer }) {
  const navigate = useNavigate()
  const [ref, width] = useWidth<HTMLDivElement>()
  const [e, attach] = useEntryRef('home/goals', ONCE)
  return (
    <section ref={(el: HTMLElement | null) => { ref.current = el as HTMLDivElement | null; attach(el) }} aria-label={t`Reading goals`} data-box="home-goals" className="pt-3">
      {width > 0 && (
        <GoalGrid
          goals={answer.goals}
          count={answer.goals.length}
          desktop={width >= DESKTOP_MIN}
          entry={e}
          shapeKey="home"
          today={answer.today}
          label={t`Reading goals`}
          pick={(g) => navigate(`/stats?tab=goals&goal=${g.id}&kind=${g.kind}`)}
        />
      )}
    </section>
  )
}
