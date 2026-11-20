// The words of the reading goals, in the app's language: a goal's one name
// (built from the server's name_key and name_params, so English reads exactly as
// the server's `name`), units, the status line and the quiet line under it.
// Numbers go through the Stats kit's num()/dec1() (the app language's format).
import { t, plural, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'
import type { Goal, GoalKind } from '@/lib/goals'
import type { Detail, Lead } from '@/lib/goalsModel'
import { num } from '@/components/stats/kit/tokens'
import { formatDuration } from '@/lib/utils'

const loc = () => i18n.locale || 'en'

/** A time a day, from minutes: under an hour "28 min", an hour or more the app's
    hours and minutes ("6h 28m", formatDuration). Every per-day need and recent pace. */
export function minutesText(min: number): string {
  const m = Math.max(0, Math.round(min))
  if (m < 60) { const n = num(m); return t`${n} min` }
  return formatDuration(m * 60)
}
/** The same from seconds a day; at least a minute ("needs about 1 min a day"). */
export const perDayText = (secs: number) => minutesText(Math.max(1, Math.round(secs / 60)))
const noon = (iso: string) => new Date(iso + 'T12:00:00')

/** "Dec 31" (with the year when it is not this year's). */
export function shortDate(iso: string, today?: string): string {
  const sameYear = !today || iso.slice(0, 4) === today.slice(0, 4)
  return noon(iso).toLocaleDateString(loc(), sameYear ? { month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric', year: 'numeric' })
}
/** "Nov 7, 2026". */
export const mediumDate = (iso: string) => noon(iso).toLocaleDateString(loc(), { month: 'short', day: 'numeric', year: 'numeric' })
/** "Thursday, Dec 31, 2026". */
export const longDate = (iso: string) => noon(iso).toLocaleDateString(loc(), { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric' })
/** "Friday, Nov 20". */
export const dayName = (iso: string) => noon(iso).toLocaleDateString(loc(), { weekday: 'long', month: 'short', day: 'numeric' })
/** "Thursday". */
export const weekdayLong = (iso: string) => noon(iso).toLocaleDateString(loc(), { weekday: 'long' })
/** "Mon". */
export const weekdayShort = (iso: string) => noon(iso).toLocaleDateString(loc(), { weekday: 'short' })
/** "M". */
export const weekdayLetter = (iso: string) => noon(iso).toLocaleDateString(loc(), { weekday: 'narrow' })
/** "November" (or "November 2026"). */
export const monthName = (iso: string, year = false) => noon(iso.slice(0, 7) + '-15').toLocaleDateString(loc(), year ? { month: 'long', year: 'numeric' } : { month: 'long' })
/** "Nov". */
export const monthShort = (iso: string) => noon(iso.slice(0, 7) + '-15').toLocaleDateString(loc(), { month: 'short' })

/** The goal's one name, everywhere ("Books in 2026", "Pages a week · Manga", "He Who Fights with Monsters by Dec 31"). */
export function goalName(g: Pick<Goal, 'kind' | 'name' | 'name_params' | 'period_start'>, today?: string): string {
  const p = g.name_params ?? {}
  let base: string
  switch (g.kind) {
    case 'books_per_year': { const y = p.year ?? Number(g.period_start.slice(0, 4)); base = t`Books in ${y}`; break }
    case 'books_per_month': { const m = monthName(g.period_start); base = t`Books in ${m}`; break }
    case 'minutes_per_day': base = t`Minutes a day`; break
    case 'minutes_per_week': base = t`Minutes a week`; break
    case 'pages_per_day': base = t`Pages a day`; break
    case 'pages_per_week': base = t`Pages a week`; break
    case 'days_per_week': base = t`Reading days a week`; break
    case 'finish_by': {
      const title = p.title ?? ''
      if (!p.due_date) { base = title || g.name; break }
      const due = shortDate(p.due_date, today)
      base = title ? t`${title} by ${due}` : t`Finish by ${due}`
      break
    }
    default: base = g.name
  }
  return p.book_type_label ? `${base} · ${p.book_type_label}` : base
}

/** The dialog's label of a kind ("Books a year") and its one-line note. */
export const KIND_LABEL: Record<GoalKind, MessageDescriptor> = {
  books_per_year: msg`Books a year`,
  books_per_month: msg`Books a month`,
  minutes_per_day: msg`Minutes a day`,
  minutes_per_week: msg`Minutes a week`,
  pages_per_day: msg`Pages a day`,
  pages_per_week: msg`Pages a week`,
  days_per_week: msg`Reading days a week`,
  finish_by: msg`Finish by a date`,
}
export const KIND_NOTE: Record<GoalKind, MessageDescriptor> = {
  books_per_year: msg`Books you finish this year`,
  books_per_month: msg`Books you finish each month`,
  minutes_per_day: msg`Reading time each day`,
  minutes_per_week: msg`Reading time each week`,
  pages_per_day: msg`Pages turned each day`,
  pages_per_week: msg`Pages turned each week`,
  days_per_week: msg`Days you read at all`,
  finish_by: msg`A book or a series, by a day you pick`,
}
export const kindLabel = (k: GoalKind) => i18n._(KIND_LABEL[k])
export const kindNote = (k: GoalKind) => i18n._(KIND_NOTE[k])

/** The unit after a figure ("/ 60 books", "/ 30 min"), for a count of `n`. */
export function unitWord(metric: string, n: number): string {
  switch (metric) {
    case 'books': return plural(n, { one: 'book', other: 'books' })
    case 'minutes': return t`min`
    case 'pages': return plural(n, { one: 'page', other: 'pages' })
    case 'days': return plural(n, { one: 'day', other: 'days' })
    case 'volumes': return plural(n, { one: 'volume', other: 'volumes' })
    default: return ''
  }
}

/** The dialog's unit beside the number field ("min a day", "books a year"). */
export function targetUnit(kind: GoalKind, n: number): string {
  switch (kind) {
    case 'books_per_year': return plural(n, { one: 'book a year', other: 'books a year' })
    case 'books_per_month': return plural(n, { one: 'book a month', other: 'books a month' })
    case 'minutes_per_day': return t`min a day`
    case 'minutes_per_week': return t`min a week`
    case 'pages_per_day': return plural(n, { one: 'page a day', other: 'pages a day' })
    case 'pages_per_week': return plural(n, { one: 'page a week', other: 'pages a week' })
    case 'days_per_week': return plural(n, { one: 'day a week', other: 'days a week' })
    default: return ''
  }
}

const daysLeft = (n: number) => plural(n, { one: '# day left', other: '# days left' })

/** The status line: its coloured lead and the plain rest ("5 ahead" + " of pace"). */
export function leadWords(l: Lead): { lead: string; rest: string } {
  switch (l.k) {
    case 'reached': return { lead: t`Reached`, rest: '' }
    case 'ahead': { const n = num(l.n); return { lead: t`${n} ahead`, rest: t` of pace` } }
    case 'behind': { const n = num(l.n); return { lead: t`${n} behind`, rest: t` pace` } }
    case 'on_pace': return { lead: t`On pace`, rest: '' }
    case 'today': {
      const n = num(l.n)
      const what = l.unit === 'minutes' ? t`${n} min to go` : plural(l.n, { one: '# page to go', other: '# pages to go' })
      return { lead: what, rest: t` today` }
    }
    case 'days': {
      const n = num(l.n)
      const left = daysLeft(l.left)
      return l.reachable ? { lead: t`${n} to go`, rest: ` · ${left}` } : { lead: t`Out of reach`, rest: t` this week` }
    }
    case 'finish':
      switch (l.status) {
        case 'completed': return { lead: t`Reached`, rest: '' }
        case 'expired': return { lead: t`The date has passed`, rest: '' }
        case 'on_pace': return { lead: t`On pace`, rest: '' }
        case 'behind': return { lead: t`Behind`, rest: t` at your recent pace` }
        default: return { lead: t`No estimate yet`, rest: '' }
      }
  }
}

/** The quiet line under the status, as clauses (a card drops trailing ones that do not fit its width). */
export function detailClauses(d: Detail): string[] {
  switch (d.k) {
    case 'togo': {
      const n = num(d.toGo)
      const out = [t`${n} to go`, daysLeft(d.left)]
      if (d.projection != null) { const p = num(d.projection); out.push(t`on course for ${p}`) }
      return out
    }
    case 'left': return [daysLeft(d.left)]
    case 'week_days': { const n = num(d.hit); return [t`${n} of 7 days this week`] }
    case 'recent_weeks': { const r = num(d.reached), o = num(d.of); return [d.of === 12 ? t`Reached in ${r} of the last 12 weeks` : t`Reached in ${r} of ${o} weeks so far`] }
    case 'finish': {
      const left = plural(d.left, { one: '# left', other: '# left' })
      if (d.minutes == null) return [left]
      const m = minutesText(d.minutes)
      return [left, t`needs about ${m} a day`]
    }
    case 'finish_need': { const m = minutesText(d.minutes), r = minutesText(d.recent); return [t`needs about ${m} a day`, t`you read ${r}`] }
    case 'finish_done': return [t`All read`]
    case 'none': return [t`No full week yet`]
  }
}

/** A shorter wording of a quiet line's first clause, for a card too narrow for it (null: none). */
export function detailShort(d: Detail): string | null {
  switch (d.k) {
    case 'recent_weeks': { const r = num(d.reached), o = num(d.of); return t`${r} of ${o} weeks reached` }
    case 'week_days': { const n = num(d.hit); return t`${n} of 7 days` }
    case 'finish_need': { const m = minutesText(d.minutes); return t`needs ${m} a day` }
    default: return null
  }
}

/** The quiet line in one string (tips, the Boards tile). */
export const detailWords = (d: Detail) => detailClauses(d).join(' · ')

/** A finish-by card's label: "Finish by Dec 31" (the goal's one name is the title plus the date). */
export function finishLabel(g: Pick<Goal, 'due_date' | 'period_end'>, today?: string): string {
  const due = shortDate(g.due_date ?? g.period_end, today)
  return t`Finish by ${due}`
}
