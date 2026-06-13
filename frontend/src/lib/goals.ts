/**
 * Reading goals — types, API calls, and per-kind display metadata.
 * All per-kind display logic derives from the GOAL_META map; its preset
 * chips are the curated on-ramp shown in the goal editor.
 */
import { api } from '@/lib/api'

export type GoalKind =
  | 'books_per_year'
  | 'books_per_month'
  | 'minutes_per_day'
  | 'minutes_per_week'
  | 'pages_per_day'
  | 'pages_per_week'

export type GoalMetric = 'books' | 'minutes' | 'pages'
export type GoalPeriod = 'day' | 'week' | 'month' | 'year'

/** Goal with computed progress, as returned by GET /api/goals. */
export interface Goal {
  id: number
  kind: string
  metric: GoalMetric
  period: GoalPeriod
  target: number
  book_type_id: number | null
  book_type_label: string | null
  current: number
  pct: number
  /** Target prorated to the elapsed window — month/year only, null otherwise. */
  expected: number | null
  /** Present only for daily-period goals */
  days_hit_this_week?: number
  /** Present for year-period goals */
  year?: number
}

interface GoalMeta {
  label: string          // "Books this year"
  caption: string        // ring caption below the number
  unit: string           // suffix shown in ring center bottom ('' | 'm' | 'p')
  inputUnit: string      // written-out unit for the input row (e.g. "books", "min", "pages")
  placeholder: number    // default placeholder for the numeric input
  presets: number[]      // quick-pick chips
  metric: GoalMetric
  period: GoalPeriod
}

const GOAL_META: Record<GoalKind, GoalMeta> = {
  books_per_year: {
    label: 'Books this year',
    caption: 'Year Challenge',
    unit: '',
    inputUnit: 'books',
    placeholder: 24,
    presets: [12, 24, 52],
    metric: 'books',
    period: 'year',
  },
  books_per_month: {
    label: 'Books this month',
    caption: 'Monthly goal',
    unit: '',
    inputUnit: 'books',
    placeholder: 2,
    presets: [1, 2, 4],
    metric: 'books',
    period: 'month',
  },
  minutes_per_day: {
    label: 'Minutes per day',
    caption: 'Daily minutes',
    unit: 'm',
    inputUnit: 'min',
    placeholder: 30,
    presets: [15, 30, 60],
    metric: 'minutes',
    period: 'day',
  },
  minutes_per_week: {
    label: 'Minutes per week',
    caption: 'Weekly minutes',
    unit: 'm',
    inputUnit: 'min',
    placeholder: 300,
    presets: [120, 300, 600],
    metric: 'minutes',
    period: 'week',
  },
  pages_per_day: {
    label: 'Pages per day',
    caption: 'Daily pages',
    unit: 'p',
    inputUnit: 'pages',
    placeholder: 50,
    presets: [20, 50, 100],
    metric: 'pages',
    period: 'day',
  },
  pages_per_week: {
    label: 'Pages per week',
    caption: 'Weekly pages',
    unit: 'p',
    inputUnit: 'pages',
    placeholder: 350,
    presets: [150, 350, 700],
    metric: 'pages',
    period: 'week',
  },
}

export function goalMeta(kind: string): GoalMeta {
  return GOAL_META[kind as GoalKind] ?? {
    label: kind,
    caption: kind,
    unit: '',
    inputUnit: '',
    placeholder: 10,
    presets: [],
    metric: 'books' as GoalMetric,
    period: 'year' as GoalPeriod,
  }
}

/** All 6 allowed kinds in display order */
export const ALLOWED_GOAL_KINDS: GoalKind[] = [
  'books_per_year',
  'books_per_month',
  'minutes_per_day',
  'minutes_per_week',
  'pages_per_day',
  'pages_per_week',
]

/** Tile/card title, e.g. "Books this year · Manga" */
export function goalTitle(goal: Goal): string {
  const base = goalMeta(goal.kind).label
  return goal.book_type_label ? `${base} · ${goal.book_type_label}` : base
}

/** Compute the subtext shown below a goal ring. Pace line for month/year. */
export function goalSubtext(goal: Goal): string {
  const { period } = goalMeta(goal.kind)
  const reached = goal.current >= goal.target

  if (period === 'day') {
    if (reached) return 'Goal reached today'
    return `${goal.days_hit_this_week ?? 0}/7 days this week`
  }
  if (period === 'week') {
    return reached ? 'Goal reached' : 'this week'
  }
  // month/year: pace against the prorated target
  if (reached) return 'Goal reached'
  if (goal.expected != null) {
    const diff = Math.round(goal.current - goal.expected)
    if (diff >= 1) return `${diff} ahead of pace`
    if (diff <= -1) return `${Math.abs(diff)} behind pace`
    return 'on pace'
  }
  const toGo = Math.max(goal.target - Math.floor(goal.current), 0)
  return `${toGo} to go`
}

// ── API ───────────────────────────────────────────────────────────────────────

const tz = () => new Date().getTimezoneOffset()

export function listGoals(): Promise<Goal[]> {
  return api.get<{ goals: Goal[] }>(`/goals?tz_offset=${tz()}`).then((r) => r.goals)
}

export function createGoal(kind: GoalKind, target: number, bookTypeId: number | null): Promise<Goal> {
  return api.post<Goal>(`/goals?tz_offset=${tz()}`, {
    kind,
    target,
    book_type_id: bookTypeId,
  })
}

export function updateGoal(id: number, target: number): Promise<Goal> {
  return api.put<Goal>(`/goals/${id}?tz_offset=${tz()}`, { target })
}

export function deleteGoal(id: number): Promise<void> {
  return api.delete<void>(`/goals/${id}`)
}
