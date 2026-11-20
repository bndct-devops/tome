// What the Goals tab's detail says in its three figures, per kind, and which
// detail a goal gets (kept apart from Detail.tsx: components only there).
import { t, plural } from '@lingui/core/macro'
import { formatDuration } from '@/lib/utils'
import type { Goal, GoalHistory } from '@/lib/goals'
import { addDays } from '@/lib/goalsModel'
import { num } from '@/components/stats/kit/tokens'
import { mediumDate, monthName, shortDate, unitWord, weekdayLong } from './text'

export type DetailKind = 'days' | 'week' | 'period' | 'finish'
export function detailKind(g: Pick<Goal, 'kind' | 'period'>): DetailKind {
  if (g.kind === 'finish_by') return 'finish'
  if (g.period === 'day' || g.kind === 'days_per_week') return 'days'
  if (g.period === 'week') return 'week'
  return 'period'
}

export type Fig = { label: string; value: number; text?: string; unit: string; foot: string }

export function daysFigures(goal: Goal, h: GoalHistory): Fig[] {
  const s = h.summary
  if (!s) return []
  const span = s.best_run_start && s.best_run_end ? `${shortDate(s.best_run_start, h.today)} to ${shortDate(s.best_run_end, h.today)}` : null
  const from = s.data_from ? mediumDate(s.data_from) : null
  if (goal.kind === 'days_per_week') {
    const n = goal.target
    const of = s.with_data
    return [
      { label: t`Reached`, value: s.reached, unit: plural(of, { one: 'of # week', other: 'of the last # weeks' }), foot: plural(n, { one: 'A reading day or more in a week', other: '# reading days or more in a week' }) },
      { label: t`Best run`, value: s.best_run, unit: plural(s.best_run, { one: 'week', other: 'weeks in a row' }), foot: span && from ? t`${span}, since your data starts (${from})` : t`No run yet` },
      { label: t`Current run`, value: s.current_run, unit: plural(s.current_run, { one: 'week', other: 'weeks' }), foot: s.current_run_includes_current ? t`This week already counts` : s.current_run ? t`Up to last week` : t`Last week missed` },
    ]
  }
  const unitLine = goal.metric === 'minutes' ? (() => { const n = num(goal.target); return t`${n} min or more on a reading day` })() : (() => { const n = num(goal.target); return t`${n} pages or more on a reading day` })()
  const yesterday = addDays(h.today, -1)
  const yv = s.yesterday_value ?? 0
  const yday = weekdayLong(yesterday)
  const hadYesterday = goal.metric === 'minutes'
    ? (yv > 0 && yv < 1 ? (() => { const sec = num(Math.round(yv * 60)); return t`${yday} had ${sec} seconds` })() : (() => { const m = num(Math.round(yv)); return t`${yday} had ${m} min` })())
    : (() => { const p = num(Math.round(yv)); return t`${yday} had ${p} pages` })()
  const runFrom = s.current_run > 0 ? addDays(h.today, -(s.current_run - (s.current_run_includes_current ? 1 : 0))) : null
  return [
    { label: t`Reached`, value: s.reached, unit: (() => { const n = num(s.days ?? 90); return t`of the last ${n} days` })(), foot: unitLine },
    { label: t`Best run`, value: s.best_run, unit: plural(s.best_run, { one: 'day', other: 'days in a row' }), foot: span && from ? t`${span}, since your data starts (${from})` : t`No run yet` },
    { label: t`Current run`, value: s.current_run, unit: plural(s.current_run, { one: 'day', other: 'days' }), foot: s.current_run > 0 && runFrom ? (() => { const d = shortDate(runFrom, h.today); return t`since ${d}` })() : hadYesterday },
  ]
}

function periodWord(period: string, n: number) {
  return period === 'week' ? plural(n, { one: 'week', other: 'weeks' }) : period === 'month' ? plural(n, { one: 'month', other: 'months' }) : plural(n, { one: 'year', other: 'years' })
}

export function periodFigures(goal: Goal, h: GoalHistory): Fig[] {
  const s = h.summary
  if (!s) return []
  const books = goal.metric === 'books'
  const of = s.with_data
  const usual = s.usual
  const from = s.data_from
  const metFoot = from
    ? (books ? (() => { const m = goal.period === 'year' ? from.slice(0, 4) : monthName(from, true); return t`since ${m}, your first finish date` })() : (() => { const d = mediumDate(from); return t`since your data starts (${d})` })())
    : t`No data yet`
  const usualUnit = goal.metric === 'minutes' ? t`min` : unitWord(goal.metric, Math.round(usual ?? 0))
  const usualLabel = goal.period === 'week' ? t`Usual week` : goal.period === 'month' ? t`Usual month` : t`Usual year`
  const usualFoot = (() => { const k = num(of); return t`median of the ${k} with data` })()
  const last = h.periods.length ? h.periods[h.periods.length - 1] : null
  const lastName = last ? (goal.period === 'month' ? monthName(last.start) : goal.period === 'year' ? last.start.slice(0, 4) : t`Last week`) : ''
  const curName = goal.period === 'month' ? monthName(goal.period_start) : goal.period === 'year' ? goal.period_start.slice(0, 4) : t`this week`
  const runFoot = s.current_run_includes_current
    ? (() => { const c = curName; return t`${c} already reached` })()
    : last && last.reached
      ? (() => { const l = lastName, c = curName; return t`${l} met, ${c} still open` })()
      : last ? (() => { const l = lastName; return t`${l} missed` })() : ''
  return [
    { label: t`Met`, value: s.reached, text: of > 0 ? (() => { const a = num(s.reached), b = num(of); return t`${a} of ${b}` })() : '0', unit: periodWord(goal.period, of), foot: metFoot },
    { label: usualLabel, value: Math.round(usual ?? 0), text: usual == null ? '–' : undefined, unit: usualUnit, foot: books && goal.period === 'month' ? (() => { const k = num(of); return t`median of the ${k} months with finish dates` })() : usualFoot },
    { label: t`Current run`, value: s.current_run, unit: periodWord(goal.period, s.current_run), foot: runFoot },
  ]
}

export function finishFigures(goal: Goal, today: string): Fig[] {
  const f = goal.finish
  if (!f) return []
  const due = shortDate(goal.due_date ?? goal.period_end, today)
  const left = f.days_left
  return [
    { label: t`Time left`, value: 0, text: f.remaining_seconds == null ? '–' : formatDuration(f.remaining_seconds), unit: '', foot: plural(f.volumes_left, { one: '# volume to go', other: '# volumes to go' }) },
    { label: t`Needs`, value: f.needed_seconds_per_day == null ? 0 : Math.max(1, Math.round(f.needed_seconds_per_day / 60)), text: f.needed_seconds_per_day == null ? '–' : undefined, unit: t`min a day`, foot: (() => { const n = num(left); return t`for the ${n} days to ${due}` })() },
    { label: t`Recent pace`, value: Math.round(f.recent_seconds_per_day / 60), unit: t`min a day`, foot: (() => { const n = num(f.recent_days); return t`over the last ${n} days` })() },
  ]
}
