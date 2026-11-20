// Reading goals: what a card says and draws, decided from the server's answer
// (GET /api/goals, see the server's API notes). Kept free of React, Lingui and
// the app's path aliases so it runs under plain Node for its tests
// (tests/goalsModel.test.ts); the components turn these tokens into words.

export type GoalKind =
  | 'books_per_year' | 'books_per_month'
  | 'minutes_per_day' | 'minutes_per_week'
  | 'pages_per_day' | 'pages_per_week'
  | 'days_per_week' | 'finish_by'

export type WeekStart = 'monday' | 'sunday'

/** The eight kinds in the dialog's order (two columns: years and months, minutes, pages, the two new ones). */
export const KIND_ORDER: GoalKind[] = [
  'books_per_year', 'books_per_month',
  'minutes_per_day', 'minutes_per_week',
  'pages_per_day', 'pages_per_week',
  'days_per_week', 'finish_by',
]

export type WeekMark = { date: string; value: number; reached: boolean; state: 'reached' | 'missed' | 'today' | 'future'; seconds?: number }

/** The fields of a goal these rules read (a subset of the API's Goal). */
export type GoalLike = {
  kind: string
  metric: string
  period: string
  target: number
  current: number
  pct: number
  expected: number | null
  status: string
  reached: boolean
  to_go: number
  diff: number | null
  projection: number | null
  days_left: number
  days_hit_this_week?: number
  week?: WeekMark[]
  recent_weeks?: { reached: number; of: number }
  finish?: { volumes_left: number; needed_seconds_per_day: number | null; recent_seconds_per_day: number } | null
}

// ── The strip of the week ─────────────────────────────────────────────────────

/** A drawn mark: reached = filled success; missed = dim neutral; future = faint outline; today = a ring filling with `fill`. */
export type MarkState = 'hit' | 'miss' | 'today' | 'future'
export type Mark = { date: string; state: MarkState; fill: number; value: number }

/** The week's marks as the strip draws them; today's fill is its share of the target (reading days: 0 or 1). */
export function stripMarks(week: WeekMark[], target: number, metric: string): Mark[] {
  return week.map((m) => {
    const state: MarkState = m.state === 'reached' ? 'hit' : m.state === 'missed' ? 'miss' : m.state
    const fill = metric === 'days' ? (m.value > 0 ? 1 : 0) : target > 0 ? Math.max(0, Math.min(1, m.value / target)) : 0
    return { date: m.date, state, fill: state === 'today' ? fill : state === 'hit' ? 1 : 0, value: m.value }
  })
}

// ── Week start ────────────────────────────────────────────────────────────────

/** Day of week (0 = Sunday … 6 = Saturday) of a YYYY-MM-DD date. */
export function dow(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay()
}

/** The weekdays (0 = Sunday) in the reader's order. */
export function weekdayOrder(start: WeekStart): number[] {
  return start === 'sunday' ? [0, 1, 2, 3, 4, 5, 6] : [1, 2, 3, 4, 5, 6, 0]
}

/** ISO date `n` days after `iso`. */
export function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10)
}

/** First day of the reader's week holding `iso`. */
export function weekStartOf(iso: string, start: WeekStart): string {
  const first = start === 'sunday' ? 0 : 1
  return addDays(iso, -((dow(iso) - first + 7) % 7))
}

/** A month as rows of seven days in the reader's week order (null outside the month), for a day picker. */
export function monthGrid(year: number, month: number, start: WeekStart): (string | null)[][] {
  const first = `${year}-${String(month).padStart(2, '0')}-01`
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate()
  const lead = (dow(first) - (start === 'sunday' ? 0 : 1) + 7) % 7
  const cells: (string | null)[] = Array.from({ length: lead }, () => null)
  for (let i = 0; i < days; i++) cells.push(addDays(first, i))
  while (cells.length % 7) cells.push(null)
  const rows: (string | null)[][] = []
  for (let i = 0; i < cells.length; i += 7) rows.push(cells.slice(i, i + 7))
  return rows
}

// ── What a card says ──────────────────────────────────────────────────────────

/** The coloured lead of a card's status line (the components word it). */
export type Lead =
  | { k: 'reached' }
  | { k: 'ahead'; n: number }
  | { k: 'behind'; n: number }
  | { k: 'on_pace' }
  /** A day goal not reached yet: what is left today. */
  | { k: 'today'; n: number; unit: string }
  /** Reading days a week: days still to read and days left. */
  | { k: 'days'; n: number; left: number; reachable: boolean }
  | { k: 'finish'; status: 'completed' | 'expired' | 'on_pace' | 'behind' | 'open' }

export type Tone = 'success' | 'warning' | 'plain' | 'muted'

/** The status line's lead and its colour: "ahead"/"reached" success, "behind" warning. */
export function lead(g: GoalLike): { lead: Lead; tone: Tone } {
  if (g.kind === 'finish_by') {
    // Finished: the reached state, as on every card. Behind (needs more a day than the
    // recent pace): the warning wording, as other cards' "behind".
    if (g.status === 'completed') return { lead: { k: 'reached' }, tone: 'success' }
    const status = (['expired', 'on_pace', 'behind'].includes(g.status) ? g.status : 'open') as Extract<Lead, { k: 'finish' }>['status']
    const tone: Tone = status === 'behind' ? 'warning' : status === 'expired' ? 'muted' : 'plain'
    return { lead: { k: 'finish', status }, tone }
  }
  if (g.reached) return { lead: { k: 'reached' }, tone: 'success' }
  if (g.kind === 'days_per_week') {
    return { lead: { k: 'days', n: Math.max(0, Math.round(g.to_go)), left: g.days_left, reachable: g.status !== 'behind' }, tone: 'plain' }
  }
  if (g.period === 'day') return { lead: { k: 'today', n: Math.max(0, Math.ceil(g.to_go)), unit: g.metric }, tone: 'plain' }
  if (g.diff != null && g.diff >= 1) return { lead: { k: 'ahead', n: g.diff }, tone: 'success' }
  if (g.diff != null && g.diff <= -1) return { lead: { k: 'behind', n: -g.diff }, tone: 'warning' }
  return { lead: { k: 'on_pace' }, tone: 'plain' }
}

/** The quiet line under it. */
export type Detail =
  /** Week, month, year goals: "4 to go · 11 days left · on course for 3" (the projection only when the server sends one). */
  | { k: 'togo'; toGo: number; left: number; projection: number | null }
  /** A reached week, month or year goal: how many days the period still has. */
  | { k: 'left'; left: number }
  /** Day goals: "3 of 7 days this week". */
  | { k: 'week_days'; hit: number }
  /** Reading days a week: "Reached in N of the last 12 weeks" (none: no complete week yet). */
  | { k: 'recent_weeks'; reached: number; of: number }
  /** Finish by: volumes left and the minutes a day it needs ("2 left · needs about 29 min a day"). */
  | { k: 'finish'; left: number; minutes: number | null }
  /** Finish by, behind: what it needs a day against what the reader reads ("needs about 95 min a day · you read 71"). */
  | { k: 'finish_need'; minutes: number; recent: number; left: number }
  /** Finish by, finished. */
  | { k: 'finish_done' }
  | { k: 'none' }

export function detail(g: GoalLike): Detail {
  if (g.kind === 'finish_by') {
    const need = g.finish?.needed_seconds_per_day
    const left = g.finish?.volumes_left ?? Math.max(0, g.to_go)
    if (g.status === 'completed') return { k: 'finish_done' }
    if (g.status === 'behind' && need != null && g.finish) return { k: 'finish_need', minutes: Math.max(1, Math.round(need / 60)), recent: Math.round(g.finish.recent_seconds_per_day / 60), left }
    return { k: 'finish', left, minutes: g.status === 'expired' || need == null ? null : Math.max(1, Math.round(need / 60)) }
  }
  if (g.kind === 'days_per_week') {
    const r = g.recent_weeks
    return r && r.of > 0 ? { k: 'recent_weeks', reached: r.reached, of: r.of } : { k: 'none' }
  }
  if (g.period === 'day') return { k: 'week_days', hit: g.days_hit_this_week ?? 0 }
  if (g.reached) return { k: 'left', left: g.days_left }
  // "On course for" is for month and year goals only: a week is too short to project.
  return { k: 'togo', toGo: g.to_go, left: g.days_left, projection: g.period === 'month' || g.period === 'year' ? g.projection : null }
}

/** Where the pace tick sits on the bar (0..1), or null: no tick for day goals, finish-by, or a reached goal. */
export function tick(g: GoalLike): number | null {
  if (g.reached || g.expected == null || g.target <= 0 || g.kind === 'finish_by') return null
  return Math.max(0, Math.min(1, g.expected / g.target))
}

/** What the card draws in its mark slot. */
export function markKind(g: GoalLike): 'strip' | 'bar' {
  return g.week && (g.period === 'day' || g.kind === 'days_per_week') ? 'strip' : 'bar'
}

/** A figure as goals show it: minutes and pages whole, books and volumes whole. */
export function shown(n: number): number {
  return Math.round(n)
}

// ── The Overview's one row and Home ───────────────────────────────────────────

/** The Overview's compact row shows at most this many goals (one row of four; two rows of two on the phone). */
export const COMPACT_MAX = 4

// ── Remembered shape (skeletons) ──────────────────────────────────────────────

/** The heights of a goal grid's rows as last drawn, per place and layout, so a second visit's skeleton reserves them. */
export type GridShape = { n: number; cols: number; rows: number[] }

export function shapeFits(s: GridShape | null, n: number, cols: number): s is GridShape {
  return !!s && s.n === n && s.cols === cols && s.rows.length === Math.ceil(n / cols) && s.rows.every((h) => h > 0 && h < 1000)
}

// ── Dialog: the target's consequence line ────────────────────────────────────

/** Books a month at `value` (minutes or pages a day / a week), from the reader's minutes or pages per finished book. */
export function booksAMonth(value: number, per: 'day' | 'week', perBook: number | null): number | null {
  if (!perBook || !(value > 0)) return null
  return ((per === 'week' ? value / 7 : value) * 30.44) / perBook
}

/** Minutes a day that `books` a month or a year take at the reader's median minutes per finished book. */
export function minutesADay(books: number, per: 'month' | 'year', medianMinutes: number | null): number | null {
  if (!medianMinutes || !(books > 0)) return null
  return (books * medianMinutes) / (per === 'month' ? 30.44 : 365)
}

/** A target typed in the dialog: a whole number within the kind's bounds, or why not. */
export function parseTarget(raw: string, min: number, max: number | null): { ok: true; value: number } | { ok: false; why: 'empty' | 'whole' | 'range' } {
  const t = raw.trim()
  if (!t) return { ok: false, why: 'empty' }
  // Digit grouping as any app language writes it ("2,000", "2 000", "2.000") is accepted.
  const s = /^\d{1,3}([,.'\s ]\d{3})+$/.test(t) ? t.replace(/\D/g, '') : t
  if (!/^\d+$/.test(s)) return { ok: false, why: 'whole' }
  const v = Number(s)
  if (v < min || (max != null && v > max)) return { ok: false, why: 'range' }
  return { ok: true, value: v }
}

// ── One line, never wrapped ───────────────────────────────────────────────────

/**
 * The clauses of a card line that fit `width` px: the first clause always, then
 * each following one while the whole line still fits; the rest are dropped (the
 * last clause goes first: on course for, then days left). `measure` gives a
 * string's width in the line's font.
 */
export function fitClauses(clauses: string[], width: number, measure: (s: string) => number, sep = ' · '): string[] {
  if (!clauses.length || width <= 0) return clauses.slice(0, 1)
  const out = [clauses[0]]
  for (let i = 1; i < clauses.length; i++) {
    if (measure([...out, clauses[i]].join(sep)) > width) break
    out.push(clauses[i])
  }
  return out
}
