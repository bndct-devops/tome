// Deleting a goal: gone at once everywhere, with an Undo in a short toast (the
// server keeps it restorable for 30 minutes; the toast itself is the usual one).
import { useCallback } from 'react'
import { t } from '@lingui/core/macro'
import { useToast } from '@/contexts/ToastContext'
import { deleteGoal, restoreGoal, type Goal } from '@/lib/goals'
import { goalName } from './text'

export function useDeleteGoal() {
  const { toast } = useToast()
  return useCallback((g: Goal) => {
    const name = goalName(g)
    deleteGoal(g.id)
      .then(() => {
        toast.success(t`Deleted ${name}`, {
          action: {
            label: t`Undo`,
            onClick: () => {
              restoreGoal(g.id).catch((e: Error & { status?: number }) => {
                toast.error(e.status === 409 ? t`An equal goal exists now, so this one cannot come back.` : t`This goal can no longer be restored.`)
              })
            },
          },
        })
      })
      .catch(() => toast.error(t`Could not delete the goal.`))
  }, [toast])
}

const SEL_KEY = 'tome_stats_goal_sel'

/** The goal selected on the Goals tab in this browser (id and kind, so a skeleton can draw its detail). */
export function savedSelection(): { id: number; kind: string } | null {
  try {
    const v = JSON.parse(localStorage.getItem(SEL_KEY) ?? 'null') as { id?: unknown; kind?: unknown } | null
    return v && typeof v.id === 'number' && typeof v.kind === 'string' ? { id: v.id, kind: v.kind } : null
  } catch {
    return null
  }
}
export function saveSelection(g: { id: number; kind: string }) {
  try {
    localStorage.setItem(SEL_KEY, JSON.stringify({ id: g.id, kind: g.kind }))
  } catch {
    // storage unavailable: the tab opens on the first goal
  }
}
