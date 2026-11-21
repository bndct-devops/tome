// Shapes /api/stats and /api/stats/habits into what the Habits page draws.
// Pure functions, no React; nothing here is a figure the server computes itself.
import type { HabitsResponse, StatsResponse } from '@/components/stats/shared'
import { HEAT_GAP, tiles } from '@/components/stats/overview/model'
import { rowOfJsDay } from '@/lib/weekStart'

// ── fixed geometry (see the mockup's NOTES "Heights") ─────────────────────────
export const TL_PITCH = 38
export const TL_TRACK = 24
export const TL_PITCH_PHONE = 26
export const TL_TRACK_PHONE = 18
export const TL_ROWS = 14
export const timelineHeight = (phone: boolean) => TL_ROWS * (phone ? TL_PITCH_PHONE : TL_PITCH) + 16

/**
 * The hour grid's tiles: true squares, as wide as the hour columns that fill the
 * width, so the box's height follows the width (the skeleton computes the same).
 */
export function rhythmGeom(W: number, phone: boolean) {
  const ncol = phone ? 12 : 24
  const labelW = phone ? 32 : 40
  const rightW = phone ? 58 : 208
  const { xs, ws, cell, end } = tiles(labelW, W - labelW - rightW, ncol, HEAT_GAP)
  const rowH = cell
  const heatH = 7 * (rowH + HEAT_GAP) - HEAT_GAP
  const histH = phone ? 48 : 64
  return { ncol, labelW, rightW, xs, ws, end, rowH, heatH, histH, H: heatH + 12 + histH + 20 }
}
export const rhythmHeight = (W: number, phone: boolean) => rhythmGeom(W, phone).H

export const MONTH_ROW = 110
export const monthsHeight = (phone: boolean) => (phone ? 22 : 0) + MONTH_ROW + (phone ? 40 + 22 : 24) + MONTH_ROW + 22

// ── When you read: weekday totals beside the grid ─────────────────────────────
/** Seconds and sessions per weekday in the reader's week order (the server counts from Sunday = 0). */
export function byWeekday(cells: StatsResponse['hour_dow_heatmap']) {
  const s = Array<number>(7).fill(0)
  const n = Array<number>(7).fill(0)
  for (const c of cells) {
    s[rowOfJsDay(c.dow)] += c.seconds
    n[rowOfJsDay(c.dow)] += c.sessions
  }
  return { s, n }
}

// ── Last 14 days ──────────────────────────────────────────────────────────────
export type TlBar = { m0: number; m1: number; seconds: number; title: string | null; bookId: number | null; started: Date }
export type TlRow = { date: string; seconds: number; sittings: number; bars: TlBar[] }

/** One row per reading day; bars in minutes from the day's local 04:00, cut at the track's ends. */
export function timelineRows(days: HabitsResponse['last_14_days']): TlRow[] {
  return days.map((d) => {
    const start = new Date(d.date + 'T04:00:00').getTime()
    const bars: TlBar[] = []
    for (const s of d.sittings) {
      const t0 = new Date(s.started_at).getTime()
      const m0 = (t0 - start) / 60_000
      const m1 = m0 + s.seconds / 60
      const a = Math.max(0, m0), b = Math.min(1440, m1)
      if (m1 < 0 || m0 > 1440) continue
      bars.push({ m0: a, m1: Math.max(a, b), seconds: s.seconds, title: s.title, bookId: s.book_id, started: new Date(t0) })
    }
    return { date: d.date, seconds: d.seconds, sittings: d.sittings.length, bars }
  })
}

// ── Pace: pages a minute per reading day, a trailing 7-day mean ───────────────
export type PaceDot = { i: number; date: string; ppm: number; mean: number; pages: number; seconds: number }
export type PaceDay = { date: string; dot: PaceDot | null; mean: number | null }

/** Dots from daily pages / seconds (days with time but no pages skipped); the line is the
 *  time-weighted mean of the 7 calendar days ending on each dot. */
export function paceModel(daily: StatsResponse['daily']) {
  const dots: PaceDot[] = []
  daily.forEach((d, i) => {
    if (d.pages <= 0 || d.seconds <= 0) return
    const win = daily.slice(Math.max(0, i - 6), i + 1).filter((w) => w.pages > 0 && w.seconds > 0)
    const pg = win.reduce((a, w) => a + w.pages, 0)
    const sec = win.reduce((a, w) => a + w.seconds, 0)
    dots.push({ i, date: d.date, ppm: d.pages / (d.seconds / 60), mean: pg / (sec / 60), pages: d.pages, seconds: d.seconds })
  })
  const weighted = (arr: PaceDot[]) => {
    const sec = arr.reduce((a, p) => a + p.seconds, 0)
    return sec > 0 ? arr.reduce((a, p) => a + p.pages, 0) / (sec / 60) : 0
  }
  const recent = dots.slice(-7)
  // Every day of the range for the chart's guide: its date, its dot (a reading day)
  // and the same trailing 7-day mean the line is drawn from (null: no reading in the window).
  const byDay = new Map(dots.map((p) => [p.i, p]))
  const days: PaceDay[] = daily.map((d, i) => {
    const dot = byDay.get(i) ?? null
    if (dot) return { date: d.date, dot, mean: dot.mean }
    const win = daily.slice(Math.max(0, i - 6), i + 1).filter((w) => w.pages > 0 && w.seconds > 0)
    const sec = win.reduce((a, w) => a + w.seconds, 0)
    return { date: d.date, dot: null, mean: sec > 0 ? win.reduce((a, w) => a + w.pages, 0) / (sec / 60) : null }
  })
  return { dots, days, nDays: daily.length, recent: weighted(recent), all: weighted(dots), recentCount: recent.length }
}

/** Axis ticks of the pace chart: month starts, or for a range with fewer than two
 *  of them, about five evenly spaced days ("Nov 3"). */
export function paceTicks(daily: { date: string }[]) {
  const months = monthStarts(daily)
  if (months.length >= 2 || daily.length < 2) return months
  const every = Math.max(1, Math.ceil(daily.length / 5))
  const out: { index: number; label: string }[] = []
  for (let i = 0; i < daily.length; i += every) {
    out.push({ index: i, label: new Date(daily[i].date + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) })
  }
  return out
}

/** Month starts in `daily` as chart indices. */
export function monthStarts(daily: { date: string }[]) {
  return daily
    .map((d, i) => ({ d, i }))
    .filter(({ d }) => d.date.endsWith('-01'))
    .map(({ d, i }) => ({ index: i, label: new Date(d.date + 'T00:00:00').toLocaleDateString(undefined, { month: 'short' }) }))
}

// ── Sessions ──────────────────────────────────────────────────────────────────
export const BUCKETS = ['under_15m', '15_to_30m', '30_to_60m', '1_to_2h', 'over_2h'] as const
export function bucketCounts(h: HabitsResponse | null) {
  return BUCKETS.map((b) => h?.session_lengths.find((x) => x.bucket === b)?.count ?? 0)
}

// ── Pace by format ────────────────────────────────────────────────────────────
export type FormatShare = { format: string; ppm: number; pages: number; seconds: number; sessions: number; pct: number }

/** Formats with time, longest first, each with its share of the summed format time in whole
 *  percent (largest remainder, so the shown shares add up to 100). The server joins every file
 *  of a book, so a book kept in two formats puts its sessions under both: its time then counts
 *  in each format's share, and the shares are of that sum, not of the clock. */
export function formatShares(rows: StatsResponse['pace_by_format']): FormatShare[] {
  const fs = rows.filter((f) => f.seconds > 0).sort((a, b) => b.seconds - a.seconds)
  const total = fs.reduce((a, f) => a + f.seconds, 0)
  if (total <= 0) return []
  const raw = fs.map((f) => (f.seconds / total) * 100)
  const pct = raw.map(Math.floor)
  let left = 100 - pct.reduce((a, p) => a + p, 0)
  raw.map((r, i) => ({ i, rem: r - Math.floor(r) })).sort((a, b) => b.rem - a.rem).forEach(({ i }) => { if (left > 0) { pct[i]++; left-- } })
  return fs.map((f, i) => ({ format: f.format.toUpperCase(), ppm: f.pages_per_min, pages: f.pages, seconds: f.seconds, sessions: f.sessions, pct: pct[i] }))
}

/** The change from the range's pace to the recent one in whole percent, computed from the two
 *  figures as the Pace sentence prints them (one decimal), so a reader's own sum agrees;
 *  null when the range figure is 0.0. */
export function paceChange(recentShown: string, allShown: string): number | null {
  const r = Number(recentShown), a = Number(allShown)
  if (!(a > 0)) return null
  return Math.round(((r - a) / a) * 100)
}
