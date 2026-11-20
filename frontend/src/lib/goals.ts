/**
 * Reading goals: the server's answers (GET /api/goals and friends) and one
 * shared copy of the goals for every place that shows them (Stats' Goals tab
 * and Overview, Home, the Boards tile). One request per URL while it is in
 * flight (statsFetch); a write asks again and every place shows the new copy.
 * What a card says is decided in goalsModel.ts; the words are in
 * components/goals/text.ts.
 */
import { useEffect, useSyncExternalStore } from 'react'
import { api } from '@/lib/api'
import { dropStatsCache, statsFetch } from '@/components/stats/kit/useStatsGet'
import type { GoalKind, WeekMark, WeekStart } from '@/lib/goalsModel'

export type { GoalKind, WeekStart } from '@/lib/goalsModel'

export type GoalMetric = 'books' | 'minutes' | 'pages' | 'days' | 'volumes'
export type GoalPeriod = 'day' | 'week' | 'month' | 'year' | 'deadline'
export type GoalStatus = 'reached' | 'ahead' | 'behind' | 'on_pace' | 'open' | 'completed' | 'expired'

export interface FinishEstimate {
  scope: 'series' | 'book'
  series: string | null
  book_id: number | null
  title: string | null
  due_date: string
  volumes_total: number
  volumes_read: number
  volumes_left: number
  progress_volumes: number
  in_progress: { book_id: number; title: string; series_index: number | null; progress: number }[]
  next_book_id: number | null
  per_volume_seconds: number | null
  basis: string | null
  remaining_seconds: number | null
  days_left: number
  needed_seconds_per_day: number | null
  recent_seconds_per_day: number
  recent_days: number
}

/** A goal with its current period, as GET /api/goals sends it. */
export interface Goal {
  id: number
  kind: GoalKind
  metric: GoalMetric
  period: GoalPeriod
  target: number
  book_type_id: number | null
  book_type_label: string | null
  current: number
  pct: number
  expected: number | null
  name: string
  name_key: string
  name_params: { year?: number; month?: number; title?: string | null; due_date?: string | null; book_type_label?: string }
  unit: string
  status: GoalStatus
  reached: boolean
  to_go: number
  diff: number | null
  projection: number | null
  period_start: string
  period_end: string
  days_total: number
  days_passed: number
  days_left: number
  start_date: string
  created_at: string | null
  updated_at: string | null
  days_hit_this_week?: number
  year?: number
  week?: WeekMark[]
  reachable?: boolean
  recent_weeks?: { reached: number; of: number }
  // finish_by
  due_date?: string
  target_series?: string | null
  target_book_id?: number | null
  completed_at?: string | null
  expired?: boolean
  finish?: FinishEstimate
}

export interface GoalsAnswer {
  goals: Goal[]
  week_start: WeekStart
  today: string
}

export type PeriodState = 'met' | 'missed' | 'before_goal' | 'no_data' | 'current'
export interface HistoryPeriod {
  start: string
  end: string
  value: number
  target: number
  state: PeriodState
  reached: boolean | null
  seconds?: number
  bulk_day?: string | null
  bulk_count?: number
}
export interface HistoryDay {
  date: string
  value: number
  target: number
  seconds?: number
  state: 'met' | 'missed' | 'before_goal' | 'no_data' | 'today' | 'future'
  reached: boolean | null
}
export interface HistorySummary {
  periods?: number
  with_data: number
  reached: number
  met: number
  missed: number
  before_goal?: number
  usual: number | null
  best?: { start: string; value: number } | null
  best_run: number
  best_run_start: string | null
  best_run_end: string | null
  current_run: number
  current_run_includes_current: boolean
  data_from: string | null
  bulk?: { start: string; day: string; count: number; of: number }[]
  days?: number
  window_start?: string
  window_end?: string
  yesterday_value?: number
}
export interface GoalHistory {
  goal_id: number
  kind: GoalKind
  week_start: WeekStart
  today: string
  state: Goal
  periods: HistoryPeriod[]
  weeks: { start: string; days: HistoryDay[] }[] | null
  summary: HistorySummary | null
  current: (HistoryPeriod & { days: { date: string; value: number; cumulative: number }[]; expected: number | null; projection: number | null }) | null
}

export interface KindInfo {
  kind: GoalKind
  metric: GoalMetric
  period: GoalPeriod
  unit: string
  label: string
  presets: number[]
  min: number
  max: number | null
  book_type_scoped: boolean
  new: boolean
  existing: { goal_id: number; book_type_id: number | null; target_series: string | null; target_book_id: number | null }[]
}

export interface Chip { label?: 'steady' | 'keep_it_up' | 'stretch'; value: number; reached: number | null; of: number; books_per_month?: number | null; minutes_per_day?: number | null }
export interface KindSuggestion {
  kind: GoalKind
  unit: string
  over: 'days' | 'weeks' | 'months'
  count: number
  average: number | null
  median: number | null
  suggested: Chip[] | null
  presets: Chip[]
}
export interface Suggestions {
  today: string
  window: { from: string; to: string; days: number; weeks_from: string; weeks: number }
  per_book: { books: number; minutes: number | null; pages: number | null; median_minutes?: number | null }
  kinds: Partial<Record<GoalKind, KindSuggestion>>
}

// ── Requests ──────────────────────────────────────────────────────────────────

// The api client adds the IANA zone next to tz_offset (DST-correct reading days).
const tz = () => `tz_offset=${new Date().getTimezoneOffset()}`
const GOALS_KEY = '/goals'

/** A goal's history; `rev` (the goals answer it goes with) asks again whenever the goals were asked again. */
export const historyUrl = (id: number, rev: number) => `/goals/${id}/history?${tz()}&r=${rev}`
export const kindsUrl = () => `/goals/kinds?${tz()}`
export const suggestionsUrl = () => `/goals/suggestions?${tz()}`
export const finishPreviewUrl = (due: string, target: { series: string } | { bookId: number }) =>
  `/goals/finish-preview?due=${due}&${'series' in target ? `series=${encodeURIComponent(target.series)}` : `book_id=${target.bookId}`}&${tz()}`

export type NewGoal =
  | { kind: Exclude<GoalKind, 'finish_by'>; target: number; book_type_id: number | null }
  | { kind: 'finish_by'; series?: string; book_id?: number; due_date: string }

export function createGoal(body: NewGoal): Promise<Goal> {
  return api.post<Goal>(`/goals?${tz()}`, body).then((g) => { goalsChanged(); return g })
}

export function updateGoal(id: number, body: { target?: number; due_date?: string }): Promise<Goal> {
  return api.put<Goal>(`/goals/${id}?${tz()}`, body).then((g) => { goalsChanged(); return g })
}

export function deleteGoal(id: number): Promise<void> {
  return api.delete<void>(`/goals/${id}`).then(() => { goalsChanged() })
}

export function restoreGoal(id: number): Promise<Goal> {
  return api.post<Goal>(`/goals/${id}/restore?${tz()}`, {}).then((g) => { goalsChanged(); return g })
}

/** Set the first day of the reader's week (goals follow it); every goal is asked for again. */
export function saveWeekStart(week_start: WeekStart): Promise<void> {
  return api.put('/auth/me', { week_start }).then(() => { goalsChanged() })
}

// ── One shared copy ───────────────────────────────────────────────────────────

const COUNT_KEY = 'tome_stats_goal_count'
const MAX_REMEMBERED = 24

/** How many goals the last answer in this browser had (what a skeleton reserves); null: never answered here. */
export function rememberedGoalCount(): number | null {
  try {
    const raw = localStorage.getItem(COUNT_KEY)
    if (raw == null) return null
    const n = Number(raw)
    return Number.isInteger(n) && n >= 0 && n <= MAX_REMEMBERED ? n : null
  } catch {
    return null
  }
}
function rememberCount(n: number) {
  try {
    localStorage.setItem(COUNT_KEY, String(Math.min(MAX_REMEMBERED, n)))
  } catch {
    // storage unavailable: the next skeleton waits for the answer
  }
}

type Snapshot = { data: GoalsAnswer | null; failed: boolean; rev: number }
let snap: Snapshot = { data: null, failed: false, rev: 0 }
const subs = new Set<() => void>()
const emit = (next: Partial<Snapshot>) => { snap = { ...snap, ...next }; subs.forEach((f) => f()) }
const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f) } }
const read = () => snap
// When the copy was last asked for: a page mounting within this asks nothing (two
// places on one page, or a tab switched back and forth, share one answer).
let askedAt = 0
const FRESH_MS = 30_000

function load(): Promise<GoalsAnswer> {
  askedAt = Date.now()
  const p = statsFetch<GoalsAnswer>(`${GOALS_KEY}?${tz()}`, GOALS_KEY)
  p.then((a) => { rememberCount(a.goals.length); emit({ data: a, failed: false, rev: snap.rev + 1 }) }, () => emit({ failed: true }))
  return p
}

/** Something changed the goals: ask again; every place keeps its copy on screen until the new one lands. */
export function goalsChanged() {
  dropStatsCache('/goals')
  load().catch(() => {})
}

/** The reader's goals once (the Boards tile). */
export function listGoals(): Promise<Goal[]> {
  return (snap.data && Date.now() - askedAt < FRESH_MS ? Promise.resolve(snap.data) : load()).then((a) => a.goals)
}

/**
 * The goals every place shows: null until the first answer. A mount asks again
 * when the copy is older than half a minute (Home and Stats show a fresh day),
 * keeping the old copy on screen until the answer lands. `settled` is called once
 * the copy a mount waited for is in (Home's first-paint gate).
 */
export function useGoals(settled?: () => void): { data: GoalsAnswer | null; failed: boolean; rev: number } {
  const s = useSyncExternalStore(subscribe, read, read)
  useEffect(() => {
    const p = snap.data && Date.now() - askedAt < FRESH_MS ? Promise.resolve(snap.data) : load()
    let live = true
    p.catch(() => null).finally(() => { if (live) settled?.() })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per mount
  }, [])
  return { data: s.data, failed: s.failed && !s.data, rev: s.rev }
}
