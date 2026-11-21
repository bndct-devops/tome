// Goal answers drawn to order: every kind and status, built around a given
// "today" (a monday week), for specs and pictures that route GET /api/goals.
// The fields are the ones the server sends (backend/services/goals.py
// goal_state / finish_goal_state); figures are made up but consistent.

type Json = Record<string, unknown>

function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10)
}
function mondayOf(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay()
  return addDays(iso, -((dow + 6) % 7))
}
const daysBetween = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000)

/** A week strip: `hits` says, per day before today, whether it was reached; today holds `todayValue`. */
function week(today: string, metric: string, target: number, hits: boolean[], todayValue: number) {
  const start = mondayOf(today)
  let k = 0
  return Array.from({ length: 7 }, (_, i) => {
    const date = addDays(start, i)
    if (date > today) return { date, value: 0, reached: false, state: 'future', seconds: 0 }
    if (date === today) return { date, value: todayValue, reached: metric === 'days' ? todayValue > 0 : todayValue >= target, state: 'today', seconds: todayValue * 60 }
    const hit = hits[k++ % hits.length]
    const value = metric === 'days' ? (hit ? 1 : 0) : hit ? target + 5 : Math.round(target / 3)
    return { date, value, reached: hit, state: hit ? 'reached' : 'missed', seconds: value * 60 }
  })
}

function base(id: number, kind: string, metric: string, period: string, unit: string, name: string, today: string, start: string, end: string): Json {
  const days_total = daysBetween(start, end) + 1
  const days_passed = daysBetween(start, today)
  return {
    id, kind, metric, period, unit, name, name_key: kind, name_params: {}, book_type_id: null, book_type_label: null,
    period_start: start, period_end: end, days_total, days_passed, days_left: daysBetween(today, end) + 1,
    start_date: addDays(today, -60), created_at: `${addDays(today, -60)}T10:00:00Z`, updated_at: `${addDays(today, -60)}T10:00:00Z`,
  }
}

/** A week, month or year count goal at `current` of `target`. */
function count(id: number, kind: string, today: string, target: number, current: number, extra: Json = {}): Json {
  const [metric, period] = ({
    books_per_year: ['books', 'year'], books_per_month: ['books', 'month'],
    minutes_per_week: ['minutes', 'week'], pages_per_week: ['pages', 'week'], days_per_week: ['days', 'week'],
  } as Record<string, [string, string]>)[kind]
  const y = Number(today.slice(0, 4)), mo = Number(today.slice(5, 7))
  const start = period === 'year' ? `${y}-01-01` : period === 'month' ? `${today.slice(0, 7)}-01` : mondayOf(today)
  const end = period === 'year' ? `${y}-12-31` : period === 'month' ? new Date(Date.UTC(y, mo, 0)).toISOString().slice(0, 10) : addDays(start, 6)
  const unit = { books: 'books', minutes: 'min', pages: 'pages', days: 'days' }[metric]!
  const monthName = new Date(Date.UTC(y, mo - 1, 1)).toLocaleString('en', { month: 'long', timeZone: 'UTC' })
  const name = { books_per_year: `Books in ${y}`, books_per_month: `Books in ${monthName}`, minutes_per_week: 'Minutes a week', pages_per_week: 'Pages a week', days_per_week: 'Reading days a week' }[kind]!
  const g = base(id, kind, metric, period, unit, name, today, start, end)
  const total = g.days_total as number, passed = g.days_passed as number
  const expected = Math.round(target * passed / total * 10) / 10
  const diff = Math.round(current - target * passed / total)
  const reached = current >= target
  const status = reached ? 'reached' : diff >= 1 ? 'ahead' : diff <= -1 ? 'behind' : 'on_pace'
  Object.assign(g, {
    target, current, pct: Math.round(current / target * 1000) / 10, expected, status, reached,
    to_go: Math.max(0, target - current), diff,
    projection: passed > 0 ? Math.floor(current * total / passed) : null,
    name_params: period === 'year' ? { year: y } : period === 'month' ? { year: y, month: mo } : {},
  }, period === 'year' ? { year: y } : {}, extra)
  if (kind === 'days_per_week') {
    Object.assign(g, { week: week(today, 'days', target, [true, false, true, false, false, true], 0), reachable: status !== 'behind', recent_weeks: { reached: 5, of: 12 } })
  }
  return g
}

/** A day goal (minutes or pages a day) with today's value. */
function day(id: number, kind: 'minutes_per_day' | 'pages_per_day', today: string, target: number, current: number, hits: boolean[]): Json {
  const metric = kind === 'minutes_per_day' ? 'minutes' : 'pages'
  const g = base(id, kind, metric, 'day', metric === 'minutes' ? 'min' : 'pages', kind === 'minutes_per_day' ? 'Minutes a day' : 'Pages a day', today, today, today)
  const w = week(today, metric, target, hits, current)
  const reached = current >= target
  return Object.assign(g, {
    target, current, pct: Math.round(current / target * 1000) / 10, expected: null, status: reached ? 'reached' : 'open', reached,
    to_go: Math.max(0, target - current), diff: null, projection: null, week: w,
    days_hit_this_week: w.filter((m) => m.reached).length,
  })
}

/** Finish a series by `due` (days from today). */
function finish(id: number, today: string, title: string, status: 'on_pace' | 'behind' | 'open' | 'expired' | 'completed', dueIn: number, total: number, read: number): Json {
  const due = addDays(today, dueIn)
  const start = addDays(today, -45)
  const short = new Date(due + 'T12:00:00Z').toLocaleString('en', { month: 'short', day: 'numeric', timeZone: 'UTC' })
  const g = base(id, 'finish_by', 'volumes', 'deadline', 'volumes', `${title} by ${short}`, today, start, due)
  const left = total - read
  const need = status === 'completed' ? 0 : status === 'open' || status === 'expired' ? null : status === 'behind' ? 95 * 60 : 29 * 60
  const recent = status === 'behind' ? 71 * 60 : 40 * 60
  return Object.assign(g, {
    name_params: { title, due_date: due }, target: total, current: read, pct: Math.round(read / total * 1000) / 10, expected: null,
    status, reached: status === 'completed', to_go: left, diff: null, projection: null,
    days_left: Math.max(0, dueIn), due_date: due, target_series: title, target_book_id: null,
    completed_at: status === 'completed' ? `${today}T09:00:00Z` : null, expired: status === 'expired',
    finish: {
      scope: 'series', series: title, book_id: null, title, due_date: due, volumes_total: total, volumes_read: read, volumes_left: left,
      progress_volumes: read, in_progress: [], next_book_id: null, per_volume_seconds: 6 * 3600, basis: 'series',
      remaining_seconds: left * 6 * 3600, days_left: Math.max(0, dueIn), needed_seconds_per_day: need, recent_seconds_per_day: recent, recent_days: 30,
    },
  })
}

export const LONG_SERIES = 'He Who Fights with Monsters'

/** The goal sets the specs and pictures use, by count (1, 3, 4, 6, 8): together every kind and every status. */
export function goalSet(n: 1 | 3 | 4 | 6 | 8, today: string): Json[] {
  switch (n) {
    case 1: return [finish(1, today, LONG_SERIES, 'on_pace', 40, 12, 7)]
    case 3: return [
      count(1, 'books_per_year', today, 30, 27),
      count(2, 'minutes_per_week', today, 600, 90),
      finish(3, today, 'Done Saga', 'completed', 30, 2, 2),
    ]
    case 4: return [
      count(1, 'books_per_year', today, 40, 60),
      day(2, 'minutes_per_day', today, 30, 45, [true, true, false, true, true]),
      finish(3, today, 'Old Series', 'expired', -3, 6, 4),
      count(4, 'days_per_week', today, 4, 2),
    ]
    case 6: return [
      count(1, 'books_per_year', today, 40, 1, { book_type_id: 4, book_type_label: 'Light Novels', name: `Books in ${today.slice(0, 4)} · Light Novels`, name_params: { year: Number(today.slice(0, 4)), book_type_label: 'Light Novels' } }),
      day(2, 'minutes_per_day', today, 30, 12, [true, false, true, true, false]),
      count(3, 'days_per_week', today, 3, 5),
      finish(4, today, LONG_SERIES, 'behind', 40, 12, 7),
      count(5, 'pages_per_week', today, 400, 380),
      count(6, 'books_per_month', today, 2, 3),
    ]
    case 8: return [
      count(1, 'books_per_year', today, 40, 1, { book_type_id: 4, book_type_label: 'Light Novels', name: `Books in ${today.slice(0, 4)} · Light Novels`, name_params: { year: Number(today.slice(0, 4)), book_type_label: 'Light Novels' } }),
      count(2, 'books_per_month', today, 4, 3),
      day(3, 'minutes_per_day', today, 30, 12, [true, false, true, true, false]),
      count(4, 'minutes_per_week', today, 300, 255),
      day(5, 'pages_per_day', today, 20, 25, [true, true, true, false, true]),
      count(6, 'pages_per_week', today, 400, 410),
      count(7, 'days_per_week', today, 5, 1),
      finish(8, today, LONG_SERIES, 'on_pace', 40, 12, 7),
    ]
  }
}

/** The answer GET /api/goals sends for a set. */
export function goalsAnswer(n: 1 | 3 | 4 | 6 | 8, today: string) {
  return { goals: goalSet(n, today), week_start: 'monday', today }
}
