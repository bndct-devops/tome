// "Finish by…" in a series' or a book's header actions: the small goal dialog
// for that title (the day and what it takes). When the title already has a
// finish-by goal the pill names its day ("Finish by Dec 31", in the primary
// colour; otherwise it is as quiet as its neighbours) and opens that goal to edit; when everything there is read, and no goal is open, there is
// nothing to offer. Until the goals are in the pill keeps its place unseen, so
// it never appears beside the other actions and moves them.
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CalendarCheck, CalendarClock } from 'lucide-react'
import { t } from '@lingui/core/macro'
import { cn } from '@/lib/utils'
import { Pill } from '@/components/page'
import { useToast } from '@/contexts/ToastContext'
import { useGoals, type Goal } from '@/lib/goals'
import { GoalDialog, type DialogMode } from './GoalDialog'
import type { FinishTarget } from './pickerModel'
import { shortDate } from './text'

export function FinishByPill({ target, done, pending, iconOnPhone }: {
  target: FinishTarget
  /** Everything there is read already. */
  done: boolean
  /** Whether it is all read is not known yet: the pill waits unseen. */
  pending?: boolean
  /** The label only from `sm` up (the book page's header shows icons on the phone). */
  iconOnPhone?: boolean
}) {
  const { data, failed } = useGoals()
  const { toast } = useToast()
  const navigate = useNavigate()
  const [dialog, setDialog] = useState<DialogMode | null>(null)
  const goals: Goal[] | null = data?.goals ?? (failed ? [] : null)
  const mine = goals?.find((g) => g.kind === 'finish_by' && ('series' in target ? g.target_series === target.series : !g.target_series && g.target_book_id === target.bookId))
  const open = mine && mine.status !== 'completed' ? mine : null
  if (done && !open) return null
  const ready = !!goals && !pending

  const day = open ? shortDate(open.due_date ?? open.period_end, data?.today) : ''
  const label = open ? t`Finish by ${day}` : t`Finish by…`
  const hint = open ? t`Your goal for this. Change its day` : 'series' in target ? t`Set a goal to finish this series by a day you pick` : t`Set a goal to finish this book by a day you pick`
  return (
    <>
      <Pill
        variant="quiet"
        onClick={() => setDialog(open ? { kind: 'edit', goal: open } : { kind: 'finish', target })}
        disabled={!ready}
        aria-hidden={!ready || undefined}
        tabIndex={ready ? undefined : -1}
        aria-label={label}
        title={hint}
        className={cn(open && 'text-primary hover:text-primary', !ready && 'invisible')}
      >
        {open ? <CalendarCheck className="h-4 w-4" /> : <CalendarClock className="h-4 w-4" />}
        {/* On the phone a goal's pill says only its day, in the width "Finish by…" took unseen while
            the goals were on their way: the actions after it (the row starts at the left there) stay put. */}
        {open && !iconOnPhone && (
          <span className="grid justify-items-center sm:hidden">
            <span className="invisible col-start-1 row-start-1" aria-hidden="true">{t`Finish by…`}</span>
            <span className="col-start-1 row-start-1">{day}</span>
          </span>
        )}
        <span className={cn((iconOnPhone || open) && 'hidden sm:inline')}>{label}</span>
      </Pill>
      {dialog && goals && (
        <GoalDialog
          mode={dialog}
          goals={goals}
          onClose={() => setDialog(null)}
          onSaved={(g) => {
            if (dialog.kind === 'finish') toast.success(t`Goal set. It shows on Home and in Stats.`, { action: { label: t`See goal`, onClick: () => navigate(`/stats?tab=goals&goal=${g.id}&kind=${g.kind}`) } })
          }}
        />
      )}
    </>
  )
}
