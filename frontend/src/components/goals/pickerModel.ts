// The goal dialog's picker values and the date chips (no components here).
import { t } from '@lingui/core/macro'
import { monthName } from './text'

export type FinishTarget = { series: string; cover: number | null; read: number; total: number } | { bookId: number; title: string; cover: number | null }

export const targetKey = (x: FinishTarget) => ('series' in x ? `s:${x.series}` : `b:${x.bookId}`)

/** "End of November", "End of the year": the last day of this month and of this year, from `today`. */
export function dateChips(today: string): { id: string; label: string; date: string }[] {
  const y = Number(today.slice(0, 4)), m = Number(today.slice(5, 7))
  const monthEnd = new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10)
  const yearEnd = `${y}-12-31`
  const month = monthName(today)
  const out = [{ id: 'month', label: t`End of ${month}`, date: monthEnd }]
  if (yearEnd !== monthEnd) out.push({ id: 'year', label: t`End of the year`, date: yearEnd })
  return out
}

