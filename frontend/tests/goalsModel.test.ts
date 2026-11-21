// Unit tests for the reading goals' pure rules (src/lib/goalsModel.ts).
//   npm run test:unit
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  addDays, booksAMonth, fitClauses, detail, dow, lead, markKind, minutesADay, monthGrid, parseTarget, shapeFits,
  stripMarks, tick, weekStartOf, weekdayOrder, type GoalLike, type WeekMark,
  boxesMeet, placeEndLabels, segHitsBox, homePick, HOME_MAX,
} from '../src/lib/goalsModel.ts'

const base: GoalLike = {
  kind: 'books_per_year', metric: 'books', period: 'year', target: 60, current: 58, pct: 96.7,
  expected: 53.1, status: 'ahead', reached: false, to_go: 2, diff: 5, projection: 65, days_left: 42,
}
const g = (over: Partial<GoalLike>): GoalLike => ({ ...base, ...over })

// The owner's week on Fri 2026-11-20 (API.md): Mon-Wed reached, Thu 18 seconds, today nothing yet.
const week: WeekMark[] = [
  { date: '2026-11-16', value: 68.7, reached: true, state: 'reached' },
  { date: '2026-11-17', value: 84.7, reached: true, state: 'reached' },
  { date: '2026-11-18', value: 128.5, reached: true, state: 'reached' },
  { date: '2026-11-19', value: 0.3, reached: false, state: 'missed' },
  { date: '2026-11-20', value: 12, reached: false, state: 'today' },
  { date: '2026-11-21', value: 0, reached: false, state: 'future' },
  { date: '2026-11-22', value: 0, reached: false, state: 'future' },
]

test('the status line: ahead and reached in success, behind in warning, the rest plain', () => {
  assert.deepEqual(lead(base), { lead: { k: 'ahead', n: 5 }, tone: 'success' })
  assert.deepEqual(lead(g({ diff: -2, status: 'behind' })), { lead: { k: 'behind', n: 2 }, tone: 'warning' })
  assert.deepEqual(lead(g({ diff: 0, status: 'on_pace' })), { lead: { k: 'on_pace' }, tone: 'plain' })
  assert.deepEqual(lead(g({ reached: true, status: 'reached' })), { lead: { k: 'reached' }, tone: 'success' })
  // A day goal not reached: what is left today, rounded up (29.4 min left reads 30).
  assert.deepEqual(lead(g({ kind: 'minutes_per_day', metric: 'minutes', period: 'day', diff: null, expected: null, to_go: 29.4, status: 'open' })),
    { lead: { k: 'today', n: 30, unit: 'minutes' }, tone: 'plain' })
  // Reading days: days to go and days left; out of reach when the server says behind.
  assert.deepEqual(lead(g({ kind: 'days_per_week', metric: 'days', period: 'week', to_go: 2, days_left: 3, status: 'on_pace' })).lead,
    { k: 'days', n: 2, left: 3, reachable: true })
  assert.equal((lead(g({ kind: 'days_per_week', metric: 'days', period: 'week', status: 'behind' })).lead as { reachable: boolean }).reachable, false)
  // Finish by: the server's status, completed in success, behind in warning, expired muted.
  assert.deepEqual(lead(g({ kind: 'finish_by', status: 'completed' })), { lead: { k: 'reached' }, tone: 'success' })
  assert.equal(lead(g({ kind: 'finish_by', status: 'behind' })).tone, 'warning')
  assert.equal(lead(g({ kind: 'finish_by', status: 'expired' })).tone, 'muted')
  assert.deepEqual(lead(g({ kind: 'finish_by', status: 'weird' })).lead, { k: 'finish', status: 'open' })
})

test('what it takes: "on course for" only when the server sends a projection', () => {
  assert.deepEqual(detail(base), { k: 'togo', toGo: 2, left: 42, projection: 65 })
  assert.deepEqual(detail(g({ projection: null })), { k: 'togo', toGo: 2, left: 42, projection: null })
  assert.deepEqual(detail(g({ reached: true })), { k: 'left', left: 42 })
  assert.deepEqual(detail(g({ kind: 'minutes_per_day', period: 'day', days_hit_this_week: 3 })), { k: 'week_days', hit: 3 })
  assert.deepEqual(detail(g({ kind: 'days_per_week', recent_weeks: { reached: 12, of: 12 } })), { k: 'recent_weeks', reached: 12, of: 12 })
  assert.deepEqual(detail(g({ kind: 'days_per_week', recent_weeks: { reached: 0, of: 0 } })), { k: 'none' })
  const finish = { volumes_left: 2, needed_seconds_per_day: 1757, recent_seconds_per_day: 4231 }
  assert.deepEqual(detail(g({ kind: 'finish_by', status: 'on_pace', finish })), { k: 'finish', left: 2, minutes: 29 })
  assert.deepEqual(detail(g({ kind: 'finish_by', status: 'completed', finish })), { k: 'finish_done' })
  // Behind: what it needs a day against what the reader reads.
  assert.deepEqual(detail(g({ kind: 'finish_by', status: 'behind', finish: { volumes_left: 2, needed_seconds_per_day: 5700, recent_seconds_per_day: 4231 } })), { k: 'finish_need', minutes: 95, recent: 71, left: 2 })
  // Weekly goals never say "on course for": a week is too short to project.
  assert.deepEqual(detail(g({ kind: 'pages_per_week', period: 'week', projection: 2080 })), { k: 'togo', toGo: 2, left: 42, projection: null })
  assert.deepEqual(detail(g({ kind: 'books_per_month', period: 'month', projection: 3 })), { k: 'togo', toGo: 2, left: 42, projection: 3 })
})

test('the pace tick sits at expected / target, and not on a reached, day or finish-by goal', () => {
  assert.equal(tick(base), 53.1 / 60)
  assert.equal(tick(g({ reached: true })), null)
  assert.equal(tick(g({ expected: null })), null)
  assert.equal(tick(g({ kind: 'finish_by' })), null)
  assert.equal(tick(g({ expected: 80 })), 1)
  assert.equal(markKind(g({ period: 'day', week })), 'strip')
  assert.equal(markKind(g({ kind: 'days_per_week', period: 'week', week })), 'strip')
  assert.equal(markKind(g({ kind: 'pages_per_week', period: 'week' })), 'bar')
})

test('strip marks: reached filled, missed dim, future outline, today a ring filling with its share', () => {
  const m = stripMarks(week, 30, 'minutes')
  assert.deepEqual(m.map((x) => x.state), ['hit', 'hit', 'hit', 'miss', 'today', 'future', 'future'])
  assert.deepEqual(m.map((x) => x.fill), [1, 1, 1, 0, 12 / 30, 0, 0])
  // Today past the target: the ring is full, not more.
  assert.equal(stripMarks([{ ...week[4], value: 75 }], 30, 'minutes')[0].fill, 1)
  // Reading days: today is full once read at all.
  assert.equal(stripMarks([{ ...week[4], value: 1 }], 5, 'days')[0].fill, 1)
  assert.equal(stripMarks([{ ...week[4], value: 0 }], 5, 'days')[0].fill, 0)
})

test('week start: Monday or Sunday first, everywhere a week is laid out', () => {
  assert.equal(dow('2026-11-20'), 5)
  assert.deepEqual(weekdayOrder('monday'), [1, 2, 3, 4, 5, 6, 0])
  assert.deepEqual(weekdayOrder('sunday'), [0, 1, 2, 3, 4, 5, 6])
  assert.equal(weekStartOf('2026-11-20', 'monday'), '2026-11-16')
  assert.equal(weekStartOf('2026-11-20', 'sunday'), '2026-11-15')
  assert.equal(weekStartOf('2026-11-15', 'monday'), '2026-11-09') // a Sunday ends a Monday week
  assert.equal(weekStartOf('2026-11-15', 'sunday'), '2026-11-15')
  assert.equal(addDays('2026-12-31', 1), '2027-01-01')
  // November 2026 starts on a Sunday: no blanks before it on a Sunday week, six on a Monday week.
  const sun = monthGrid(2026, 11, 'sunday')
  assert.equal(sun[0][0], '2026-11-01')
  const mon = monthGrid(2026, 11, 'monday')
  assert.deepEqual(mon[0].slice(0, 7), [null, null, null, null, null, null, '2026-11-01'])
  assert.equal(mon.flat().filter(Boolean).length, 30)
  assert.ok(mon.every((r) => r.length === 7))
})

test('the dialog: a typed target, and what it means', () => {
  assert.deepEqual(parseTarget('60', 1, null), { ok: true, value: 60 })
  assert.deepEqual(parseTarget(' 2,000 ', 1, null), { ok: true, value: 2000 })
  assert.deepEqual(parseTarget('2 000', 1, null), { ok: true, value: 2000 })
  assert.deepEqual(parseTarget('1.5', 1, null), { ok: false, why: 'whole' })
  assert.deepEqual(parseTarget('', 1, null), { ok: false, why: 'empty' })
  assert.deepEqual(parseTarget('0', 1, null), { ok: false, why: 'range' })
  assert.deepEqual(parseTarget('8', 1, 7), { ok: false, why: 'range' })
  assert.deepEqual(parseTarget('-3', 1, 7), { ok: false, why: 'whole' })
  // API.md: 60 min a day at 496.4 min per book is about 3.7 books a month.
  assert.equal(Number(booksAMonth(60, 'day', 496.4)!.toFixed(1)), 3.7)
  assert.equal(Number(booksAMonth(420, 'week', 496.4)!.toFixed(1)), 3.7)
  assert.equal(booksAMonth(60, 'day', null), null)
  assert.equal(Number(minutesADay(6, 'month', 496.4)!.toFixed(0)), 98)
  assert.equal(Number(minutesADay(60, 'year', 496.4)!.toFixed(0)), 82)
})

test('a remembered grid shape fits only the same count and columns', () => {
  const s = { n: 6, cols: 4, rows: [180, 160] }
  assert.ok(shapeFits(s, 6, 4))
  assert.ok(!shapeFits(s, 5, 4))
  assert.ok(!shapeFits(s, 6, 2))
  assert.ok(!shapeFits(null, 6, 4))
})

test('a card line keeps its first clause and drops trailing ones that would wrap', () => {
  const m = (s: string) => s.length * 6
  const c = ['2 to go', '42 days left', 'on course for 65']
  assert.deepEqual(fitClauses(c, 1000, m), c)
  assert.deepEqual(fitClauses(c, m('2 to go · 42 days left'), m), ['2 to go', '42 days left'])
  assert.deepEqual(fitClauses(c, 10, m), ['2 to go'])
  assert.deepEqual(fitClauses([], 100, m), [])
})

// ── Burn-up end labels ────────────────────────────────────────────────────────

const plot = { x0: 30, y0: 0, x1: 500, y1: 128 }
const textBox = (p: { x: number; y: number }, w: number) => ({ x0: p.x - w - 2, x1: p.x + 2, y0: p.y - 11, y1: p.y + 5 })

test('a segment meets a box when it crosses it, not when it passes by', () => {
  const b = { x0: 10, y0: 10, x1: 20, y1: 20 }
  assert.equal(segHitsBox({ x0: 0, y0: 15, x1: 30, y1: 15 }, b), true)
  assert.equal(segHitsBox({ x0: 0, y0: 0, x1: 30, y1: 30 }, b), true)
  assert.equal(segHitsBox({ x0: 0, y0: 25, x1: 30, y1: 25 }, b), false)
  assert.equal(segHitsBox({ x0: 12, y0: 12, x1: 14, y1: 14 }, b), true)
  assert.equal(segHitsBox({ x0: 0, y0: 30, x1: 30, y1: 21 }, b), false)
})

test('end labels: the projection under the pace (Books in November) sits clear of every line', () => {
  // pace 0 -> 6 over the month, projection from today (x 330, 2) to the end (3); max 8.
  const Y = (v: number) => 18 + (1 - v / 8) * 110
  const pace = { x0: 30, y0: Y(0), x1: 490, y1: Y(6) }
  const proj = { x0: 330, y0: Y(2), x1: 490, y1: Y(3) }
  const reader = { x0: 30, y0: Y(2), x1: 330, y1: Y(2) }
  const today = { x0: 330, y0: 12, x1: 330, y1: 128 }
  const segs = [pace, proj, reader, today]
  const labels = [
    { id: 'pace', ex: 490, ey: Y(6), w: 62, prefer: 'above' as const },
    { id: 'proj', ex: 490, ey: Y(3), w: 92, prefer: 'below' as const },
  ]
  const placed = placeEndLabels(labels, { segs, boxes: [] }, plot)
  assert.ok(placed)
  for (const p of placed!) {
    const b = textBox(p, labels.find((l) => l.id === p.id)!.w)
    for (const s of segs) assert.equal(segHitsBox(s, b), false, `${p.id} touches a line`)
  }
  const [a, b] = placed!.map((p) => textBox(p, 92))
  assert.equal(boxesMeet(a, b), false)
})

test('end labels: projection and pace ending close together go one above, one below', () => {
  const pace = { x0: 30, y0: 120, x1: 490, y1: 40 }
  const proj = { x0: 380, y0: 52, x1: 490, y1: 37 }
  const labels = [
    { id: 'pace', ex: 490, ey: 40, w: 62, prefer: 'below' as const },
    { id: 'proj', ex: 490, ey: 37, w: 92, prefer: 'above' as const },
  ]
  const placed = placeEndLabels(labels, { segs: [pace, proj], boxes: [] }, plot)
  assert.ok(placed)
  const by = Object.fromEntries(placed!.map((p) => [p.id, p.y]))
  assert.ok(by.proj < 37 && by.pace > 40, JSON.stringify(by))
  assert.equal(boxesMeet(textBox({ x: 486, y: by.pace }, 62), textBox({ x: 486, y: by.proj }, 92)), false)
})

test('end labels: no room right of a late "today" marker means the legend (null)', () => {
  // Nov 20 in a year: today at 88% of the width, 55 px before the end; labels 60-90 px wide.
  const today = { x0: 435, y0: 12, x1: 435, y1: 128 }
  const labels = [
    { id: 'pace', ex: 490, ey: 50, w: 62, prefer: 'below' as const },
    { id: 'proj', ex: 490, ey: 30, w: 92, prefer: 'above' as const },
  ]
  assert.equal(placeEndLabels(labels, { segs: [today], boxes: [] }, plot), null)
})

test('end labels: a point in the way is avoided too', () => {
  const labels = [{ id: 'pace', ex: 490, ey: 60, w: 62, prefer: 'above' as const }]
  const dot = { x0: 440, y0: 40, x1: 450, y1: 56 }
  const placed = placeEndLabels(labels, { segs: [], boxes: [dot] }, plot)
  assert.ok(placed)
  assert.equal(boxesMeet(textBox(placed![0], 62), dot), false)
})

test('Home picks four goals by what needs attention: behind and expired, then open and on pace by the nearest end, then ahead, reached last', () => {
  const h = (id: number, status: string, period_end: string) => ({ id, status, period_end })
  const goals = [
    h(1, 'reached', '2026-11-22'), h(2, 'ahead', '2026-12-31'), h(3, 'on_pace', '2026-12-31'), h(4, 'open', '2026-11-21'),
    h(5, 'behind', '2026-12-31'), h(6, 'completed', '2026-12-28'), h(7, 'expired', '2026-11-18'), h(8, 'on_pace', '2026-11-30'),
  ]
  assert.equal(HOME_MAX, 4)
  assert.deepEqual(homePick(goals).map((g) => g.id), [5, 7, 4, 8])
  assert.deepEqual(homePick(goals, 8).map((g) => g.id), [5, 7, 4, 8, 3, 2, 1, 6])
  // The same answer in any order picks the same cards; equal ends fall back to the id.
  assert.deepEqual(homePick([...goals].reverse(), 8).map((g) => g.id), [5, 7, 4, 8, 3, 2, 1, 6])
  assert.deepEqual(homePick([h(9, 'open', '2026-11-21'), h(2, 'open', '2026-11-21')]).map((g) => g.id), [2, 9])
  // Fewer than four: all of them; an unknown status counts as not yet reached.
  assert.deepEqual(homePick([h(1, 'reached', '2026-11-22'), h(2, 'later', '2026-12-01')]).map((g) => g.id), [2, 1])
  assert.equal(homePick([]).length, 0)
})
