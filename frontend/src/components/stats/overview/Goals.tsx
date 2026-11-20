// The way to a first goal on the Overview when there is none: a quiet line at
// the end of the page (where appearing moves nothing). The goals themselves are
// the compact row under the hero (components/goals/CompactRow.tsx) and the Goals tab.
import { useState } from 'react'
import { Plus } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { cn } from '@/lib/utils'
import type { Goal } from '@/lib/goals'
import { GoalDialog } from '@/components/goals/GoalDialog'

export function GoalsStart({ goals, remembered }: { goals: Goal[] | null; remembered: number }) {
  const [creating, setCreating] = useState(false)
  // Drawn in the skeleton too when the last answer had no goals, so it is there from the first frame.
  if (goals ? goals.length > 0 : remembered > 0) return null
  return (
    <div data-box="goals-start" className={cn('-mt-4', !goals && 'invisible')} aria-hidden={!goals || undefined}>
      <button
        type="button"
        disabled={!goals}
        onClick={() => setCreating(true)}
        className="inline-flex items-center gap-1.5 text-[15px] font-medium text-primary underline-offset-4 hover:underline"
      >
        <Plus className="h-4 w-4" aria-hidden="true" />
        <Trans>Set a reading goal</Trans>
      </button>
      <span className="ml-2 text-[14px] text-muted-foreground"><Trans>Progress counts automatically.</Trans></span>
      {creating && goals && <GoalDialog mode={{ kind: 'create' }} goals={goals} onClose={() => setCreating(false)} />}
    </div>
  )
}
