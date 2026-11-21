// Shapes the /api/stats answer into what the Overview draws. Everything here is
// computed from the response; nothing is invented. Pure functions, no React.
import type { StatsResponse } from '@/components/stats/shared'
import { rowOfJsDay } from '@/lib/weekStart'

export type Bar = { start: string; end: string; days: number; seconds: number; sessions: number; pages: number; current: boolean }
export type HourCell = { s: number; n: number; lv: number }
export type HourGrid = HourCell[][]
export type YearCell = { date: string; s: number; n: number; pages: number; lv: number; col: number; row: number }
export type YearModel = { cells: YearCell[]; cols: number; months: { col: number; label: string }[]; cuts: number[]; activeDays: number }

const DAY_MS = 86_400_000
export const addDays = (iso: string, n: number) => {
  const d = new Date(iso + 'T12:00:00Z')
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}
/** Monday = 0. */
/** A day's row in the reader's week (0 = the first day, Settings › Week starts on). */
export const dowOf = (iso: string) => rowOfJsDay(new Date(iso + 'T12:00:00Z').getUTCDay())
export const dayDiff = (a: string, b: string) => Math.round((new Date(b + 'T12:00:00Z').getTime() - new Date(a + 'T12:00:00Z').getTime()) / DAY_MS)
const isoLocal = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
/** The reading day of a moment: local time with the app's 4 h rollover (a 1 am session belongs to the evening before). */
export const readingDayOf = (ts: string | number | Date) => isoLocal(new Date(new Date(ts).getTime() - 4 * 3_600_000))
const monthShort = (iso: string) => new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short' })

// ── hero chart ────────────────────────────────────────────────────────────────
// Up to 31 days: one bar per day. Longer: one bar per 7 days counted back from
// today, so every bar but possibly the oldest holds a full week. The newest bar
// contains today, which is still running, so it is drawn lighter.
export function heroBars(daily: StatsResponse['daily']) {
  const total = (xs: StatsResponse['daily'], k: 'seconds' | 'sessions' | 'pages') => xs.reduce((a, d) => a + (d[k] ?? 0), 0)
  const n = daily.length
  const weekly = n > 31
  const bars: Bar[] = []
  if (!weekly) {
    daily.forEach((d, i) => bars.push({ start: d.date, end: d.date, days: 1, seconds: d.seconds, sessions: d.sessions ?? 0, pages: d.pages ?? 0, current: i === n - 1 }))
  } else {
    for (let end = n - 1; end >= 0; end -= 7) {
      const start = Math.max(0, end - 6)
      const slice = daily.slice(start, end + 1)
      bars.unshift({ start: daily[start].date, end: daily[end].date, days: slice.length, seconds: total(slice, 'seconds'), sessions: total(slice, 'sessions'), pages: total(slice, 'pages'), current: end === n - 1 })
    }
  }
  // The average per bar of what the chart shows (for All, `daily` is the last 365 days).
  const sum = daily.reduce((a, d) => a + d.seconds, 0)
  const avg = n === 0 ? 0 : sum / (weekly ? n / 7 : n)
  const monthStarts = daily.map((d, i) => ({ d, i })).filter(({ d }) => d.date.endsWith('-01')).map(({ d, i }) => ({ index: i, label: monthShort(d.date) }))
  return { bars, avg, monthStarts, nDays: Math.max(1, n), weekly }
}

// ── steps for the heat grids: the reader's own quantiles ─────────────────────
const quantile = (sorted: number[], q: number) => {
  const pos = (sorted.length - 1) * q
  const lo = Math.floor(pos), hi = Math.ceil(pos)
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo)
}
// Round a cut so the legend can print it: 5-minute steps under an hour, 15 above.
const roundCut = (s: number) => (s < 3600 ? Math.max(300, Math.round(s / 300) * 300) : Math.round(s / 900) * 900)
function steps(values: number[], round: boolean): number[] {
  const nz = values.filter((v) => v > 0).sort((a, b) => a - b)
  if (nz.length === 0) return []
  const cuts = [0.2, 0.4, 0.6, 0.8].map((q) => quantile(nz, q))
  return round ? cuts.map(roundCut) : cuts
}
const level = (v: number, cuts: number[]) => (v <= 0 ? 0 : 1 + cuts.filter((c) => v >= c).length)

// ── hour × weekday ────────────────────────────────────────────────────────────
export function hourModel(cells: StatsResponse['hour_dow_heatmap'], two: boolean) {
  const hd = Array.from({ length: 7 }, () => Array<number>(24).fill(0))
  const hn = Array.from({ length: 7 }, () => Array<number>(24).fill(0))
  // The server counts weekdays from Sunday = 0; rows here start on Monday.
  for (const c of cells) { hd[rowOfJsDay(c.dow)][c.hour] += c.seconds; hn[rowOfJsDay(c.dow)][c.hour] += c.sessions ?? 0 }
  const byHour = Array.from({ length: 24 }, (_, h) => hd.reduce((a, row) => a + row[h], 0))
  const byDow = hd.map((row) => row.reduce((a, v) => a + v, 0))
  const total = byHour.reduce((a, v) => a + v, 0)
  const pair = (m: number[][]) => (two ? m.map((row) => Array.from({ length: 12 }, (_, k) => row[2 * k] + row[2 * k + 1])) : m)
  const values = pair(hd)
  const counts = pair(hn)
  const cuts = steps(values.flat(), false)
  const grid: HourGrid = values.map((row, r) => row.map((s, k) => ({ s, n: counts[r][k], lv: level(s, cuts) })))
  const byCol = two ? Array.from({ length: 12 }, (_, k) => byHour[2 * k] + byHour[2 * k + 1]) : byHour
  const colN = counts[0].map((_, k) => counts.reduce((a, row) => a + row[k], 0))
  const peakHour = total > 0 ? byHour.indexOf(Math.max(...byHour)) : null
  const peakDow = total > 0 ? byDow.indexOf(Math.max(...byDow)) : null
  return { grid, byCol, colN, peakHour, peakDow }
}

// ── the year ──────────────────────────────────────────────────────────────────
// The last 365 reading days, today included. If that starts mid-week, the partial
// first week is dropped so the grid starts on a full column (today's may be partial).
export function yearModel(heatmap: { date: string; seconds: number; sessions?: number; pages?: number }[]): YearModel {
  let year = heatmap.slice(-365)
  const activeDays = year.filter((d) => d.seconds > 0).length
  while (year.length && dowOf(year[0].date) !== 0) year = year.slice(1)
  if (!year.length) return { cells: [], cols: 0, months: [], cuts: [], activeDays }
  const cuts = steps(year.map((d) => d.seconds), true)
  const gridStart = year[0].date
  const cells = year.map((d) => {
    const off = dayDiff(gridStart, d.date)
    return { date: d.date, s: d.seconds, n: d.sessions ?? 0, pages: d.pages ?? 0, lv: level(d.seconds, cuts), col: Math.floor(off / 7), row: off % 7 }
  })
  const cols = Math.max(...cells.map((c) => c.col)) + 1
  const months = cells.filter((c) => c.date.endsWith('-01')).map((c) => ({ col: c.row === 0 ? c.col : c.col + 1, label: monthShort(c.date) }))
  return { cells, cols, months, cuts, activeDays }
}

/** The year as it will be before any answer: today's 365 reading days, all empty. */
export function emptyYear(today = readingDayOf(Date.now())) {
  return Array.from({ length: 365 }, (_, i) => ({ date: addDays(today, i - 364), seconds: 0 }))
}

// ── chart geometry shared by the charts and the boxes reserved for them ───────
/** Whole-pixel columns: a fixed cell, the gap varies by at most 1 px to fill the width. */
export function columns(x0: number, width: number, n: number, gap: number) {
  const pitch = (width + gap) / n
  const cell = Math.max(1, Math.floor(pitch) - gap)
  return { cell, xs: Array.from({ length: n }, (_, i) => Math.round(x0 + i * pitch)) }
}

/**
 * Heat tiles: n columns from x0 filling `width`, exactly `gap` apart (widths
 * differ by at most 1 px instead of the gaps), so a grid reads as one tiled
 * surface; bars under the grid use the same columns and end on the same pixel.
 * `cell` is the narrowest width (the side of a square tile).
 */
export function tiles(x0: number, width: number, n: number, gap: number) {
  const pitch = (width + gap) / n
  const xs: number[] = [], ws: number[] = []
  for (let i = 0; i < n; i++) {
    const a = Math.round(x0 + i * pitch)
    xs.push(a)
    ws.push(Math.max(1, Math.round(x0 + (i + 1) * pitch) - gap - a))
  }
  return { xs, ws, cell: Math.min(...ws), end: Math.round(x0 + width) }
}

/** Gap and corner of the hour-by-weekday tiles (Overview and Habits). */
export const HEAT_GAP = 2
export const heatRadius = (side: number) => (side >= 16 ? 3 : 2)

export function hourHeatHeight(W: number, two: boolean, histH: number) {
  const { cell } = tiles(34, W - 34, two ? 12 : 24, HEAT_GAP)
  return 7 * (cell + HEAT_GAP) - HEAT_GAP + 12 + histH + 20
}

export function yearHeight(W: number, n: number) {
  const { cell } = columns(22, W - 22, n, 3)
  return 20 + 7 * (cell + 3) - 3
}

// ── best day of the range (hero chart) ───────────────────────────────────────
/** The day with the most reading in the range (the latest of equals); all time: the all-time record. */
export function bestDay(stats: StatsResponse, allTime: boolean): { date: string; seconds: number } | null {
  if (allTime) {
    const r = stats.records
    return r.biggest_day_date && r.biggest_day_seconds > 0 ? { date: r.biggest_day_date, seconds: r.biggest_day_seconds } : null
  }
  let best: { date: string; seconds: number } | null = null
  for (const d of stats.daily) if (d.seconds > 0 && (!best || d.seconds >= best.seconds)) best = { date: d.date, seconds: d.seconds }
  return best
}

// ── the busiest month (a corner note of the hero chart at 1y and All) ───────
/** The month (YYYY-MM) with the most reading in `daily`, as the server picks it. */
export function busiestMonth(daily: StatsResponse['daily']): { month: string; seconds: number } | null {
  const months = new Map<string, number>()
  for (const d of daily) if (d.seconds > 0) months.set(d.date.slice(0, 7), (months.get(d.date.slice(0, 7)) ?? 0) + d.seconds)
  let best: { month: string; seconds: number } | null = null
  for (const [month, seconds] of months) if (!best || seconds > best.seconds) best = { month, seconds }
  return best
}
