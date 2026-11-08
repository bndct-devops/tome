// Axis tick helpers shared by the stats charts (kept out of shared.tsx so that
// file only exports components, which fast refresh needs).
import { formatDuration } from '@/lib/utils'

// ── Axis ticks ────────────────────────────────────────────────────────────────

/** Round-number duration ticks (seconds) from 0 to at least `maxSeconds`:
 *  5/15/30 min steps under 2h, whole hours above, at most ~5 ticks. */
export function niceDurationTicks(maxSeconds: number): number[] {
  const max = Math.max(maxSeconds, 1)
  const steps = [300, 900, 1800, 3600, 7200, 10800, 14400, 21600, 43200, 86400]
  const allowed = max < 7200 ? steps.filter((s) => s < 3600) : steps.filter((s) => s >= 3600)
  const step = allowed.find((s) => Math.ceil(max / s) <= 5) ?? allowed[allowed.length - 1]
  const n = Math.max(1, Math.ceil(max / step))
  return Array.from({ length: n + 1 }, (_, i) => i * step)
}

/** Axis label for a round duration: "2h", "30m", "1h 30m" (never "2h 0m"). */
export function axisDuration(seconds: number): string {
  if (seconds === 0) return '0'
  if (seconds % 3600 === 0) return `${seconds / 3600}h`
  return formatDuration(seconds)
}

/** Days since the epoch for an ISO date (YYYY-MM-DD), timezone-free. */
export function isoDay(iso: string): number {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return Math.round(Date.UTC(y, m - 1, d) / 86400000)
}

/** The ISO date (YYYY-MM-DD) for a day number from `isoDay`. */
export function dayIso(day: number): string {
  return new Date(day * 86400000).toISOString().slice(0, 10)
}

/** Even day-interval ticks between two ISO dates, anchored on the last day so
 *  "today" always gets a label. 2 days up to two weeks, 4 days up to ~6 weeks,
 *  weekly up to ~4 months, then every 4 weeks. Never repeats a date. */
export function evenDateTicks(first: string, last: string): string[] {
  const a = isoDay(first)
  const b = isoDay(last)
  const span = b - a
  const step = span <= 14 ? 2 : span <= 45 ? 4 : span <= 120 ? 7 : span <= 400 ? 28 : 91
  const out: string[] = []
  for (let d = b; d >= a; d -= step) out.unshift(dayIso(d))
  return out
}
