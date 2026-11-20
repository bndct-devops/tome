import { useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { Pill } from '@/components/page'
import { ProgressRing } from '@/components/stats/ProgressRing'
import { num } from '@/components/stats/kit/tokens'
import type { Goal } from '@/lib/goals'
import { lead, detail } from '@/lib/goalsModel'
import { GoalDialog, type DialogMode } from '@/components/goals/GoalDialog'
import { useDeleteGoal } from '@/components/goals/actions'
import { detailWords, goalName, leadWords, unitWord } from '@/components/goals/text'
import { cn } from '@/lib/utils'

// The Boards "Reading goals" tile (the older grid): every goal as a ring with its
// one name, figures in the app language and its status, managed in place (edit,
// delete with undo, new goal). The body scrolls inside the tile, so nothing is
// cut off at the tile's default height whatever the number of goals.

function GoalRing({ goal }: { goal: Goal }) {
  return (
    <ProgressRing pct={goal.pct} size={48} stroke={5}>
      <div className="flex flex-col items-center leading-none">
        <span className={cn('text-sm font-bold tabular-nums', goal.reached ? 'text-success' : 'text-foreground')}>{num(Math.round(goal.current))}</span>
        <span className="text-[9px] tabular-nums text-muted-foreground">/{num(goal.target)}</span>
      </div>
    </ProgressRing>
  )
}

export function GoalWidgetBody({ goals, onChanged }: { goals: Goal[] | null; onChanged: () => void }) {
  const [dialog, setDialog] = useState<DialogMode | null>(null)
  const del = useDeleteGoal()
  void onChanged // the shared goals copy (lib/goals) updates every place after a write

  if (goals === null) {
    return <div className="flex h-full items-center justify-center text-[14px] text-muted-foreground"><Trans>Loading…</Trans></div>
  }

  const modal = dialog && <GoalDialog mode={dialog} goals={goals} onClose={() => setDialog(null)} />
  if (goals.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <p className="text-[14px] text-muted-foreground"><Trans>Set a target and watch the ring fill as you read.</Trans></p>
        <Pill onClick={() => setDialog({ kind: 'create' })}><Trans>Set a goal</Trans></Pill>
        {modal}
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto overscroll-contain pr-0.5 [scrollbar-width:thin]">
      <div className="grid items-stretch gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(12.5rem, 1fr))' }}>
        {goals.map((goal) => {
          const name = goalName(goal)
          const l = leadWords(lead(goal).lead)
          const sub = `${l.lead}${l.rest}`
          const unit = unitWord(goal.metric, goal.target)
          return (
            <div key={goal.id} className="group/goal relative flex items-center gap-3 rounded-lg bg-secondary/50 p-2.5" title={`${name}\n${num(Math.round(goal.current))} / ${num(goal.target)} ${unit}\n${detailWords(detail(goal))}`}>
              <GoalRing goal={goal} />
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <p className="truncate text-[14px] font-medium leading-tight text-foreground">{name}</p>
                <p className={cn('truncate text-[13px] leading-tight', goal.reached ? 'text-success' : 'text-muted-foreground')}>{sub}</p>
              </div>
              <div className="absolute right-1 top-1 flex items-center gap-0.5 rounded-full bg-card/90 opacity-0 transition-opacity focus-within:opacity-100 group-hover/goal:opacity-100 [@media(hover:none)]:opacity-100">
                <button type="button" onClick={() => setDialog({ kind: 'edit', goal })} title={t`Edit goal`} aria-label={t`Edit goal`} className="inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground">
                  <Pencil className="h-3 w-3" />
                </button>
                <button type="button" onClick={() => del(goal)} title={t`Delete this goal`} aria-label={t`Delete this goal`} className="inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive">
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            </div>
          )
        })}
        <button
          type="button"
          onClick={() => setDialog({ kind: 'create' })}
          className="flex min-h-[4.25rem] items-center justify-center gap-1.5 rounded-lg bg-secondary/50 text-[13px] text-muted-foreground transition hover:bg-secondary hover:text-foreground"
        >
          <Plus className="h-3.5 w-3.5" />
          <Trans>New goal</Trans>
        </button>
      </div>
      {modal}
    </div>
  )
}
