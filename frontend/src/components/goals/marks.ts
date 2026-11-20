// Marks and their tips, shared by the cards and the detail (no components here).
import { t } from '@lingui/core/macro'
import type { Goal, HistoryDay } from '@/lib/goals'
import type { Mark } from '@/lib/goalsModel'
import { num } from '@/components/stats/kit/tokens'
import { dayName } from './text'

/** Columns of a goal grid: four on a desktop, two on the phone. */
export const gridCols = (desktop: boolean) => (desktop ? 4 : 2)

/** What a mark's tip says. */
export function markTip(g: Pick<Goal, 'metric' | 'target'>, m: Mark): { title: string; value: string; note?: string } {
  const title = dayName(m.date)
  if (m.state === 'future') return { title, value: t`Still to come` }
  let value: string
  if (g.metric === 'days') value = m.value > 0 ? t`A reading day` : m.state === 'today' ? t`Nothing read yet` : t`Not read`
  else if (g.metric === 'minutes') { const v = num(Math.round(m.value)); value = t`${v} min` }
  else { const v = num(Math.round(m.value)); value = t`${v} pages` }
  const note = m.state === 'hit' ? t`Reached` : m.state === 'miss' ? t`Missed` : m.fill >= 1 ? t`Reached today` : t`Today, so far`
  return { title, value, note }
}

/** A day of the grid as a mark. */
export function gridMark(d: HistoryDay): { state: 'hit' | 'miss' | 'today' | 'future'; fill: number; none?: boolean } {
  if (d.state === 'future') return { state: 'future', fill: 0 }
  if (d.state === 'today') return { state: 'today', fill: d.target > 0 ? Math.min(1, d.value / d.target) : 0 }
  if (d.state === 'no_data') return { state: 'future', fill: 0, none: true }
  return { state: d.reached ? 'hit' : 'miss', fill: d.reached ? 1 : 0 }
}

