// Shaping of the Sessions tab: rows grouped by local calendar day, and the
// skeleton's remembered shape (group sizes of the last first page).
import type { SessionEntry } from '@/components/stats/shared'

export type Filter = 'counted' | 'not_counted' | 'all'
export type Sort = 'recent' | 'longest'
export const PAGE = 20

const pad = (n: number) => String(n).padStart(2, '0')
/** Local calendar day (no rollover) of a UTC ISO start, as the server's day_totals keys. */
export function localDay(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** Consecutive rows of one calendar day (rows come newest first). */
export function groupByDay(rows: SessionEntry[]): { day: string; rows: SessionEntry[] }[] {
  const out: { day: string; rows: SessionEntry[] }[] = []
  for (const s of rows) {
    const day = localDay(s.started_at)
    const last = out[out.length - 1]
    if (last && last.day === day) last.rows.push(s)
    else out.push({ day, rows: [s] })
  }
  return out
}

const SHAPE_KEY = 'tome_stats_sessions_shape'
const FALLBACK = [3, 1, 1, 1, 1, 2, 1, 1, 1, 1, 3, 1, 2, 1]

/** Group sizes the skeleton draws: the last first page's, else a typical one. */
export function rememberedShape(): number[] {
  try {
    const v = JSON.parse(localStorage.getItem(SHAPE_KEY) ?? 'null')
    if (Array.isArray(v) && v.length <= PAGE && v.every((n) => Number.isInteger(n) && n > 0) && v.reduce((a: number, n: number) => a + n, 0) <= PAGE) return v
  } catch {
    // storage unavailable or garbled - the typical shape
  }
  return FALLBACK
}
/** Whether this browser has a remembered shape (false on a first visit). */
export function shapeKnown(): boolean {
  try {
    return localStorage.getItem(SHAPE_KEY) != null
  } catch {
    return false
  }
}
export function rememberShape(groups: { rows: unknown[] }[]) {
  try {
    localStorage.setItem(SHAPE_KEY, JSON.stringify(groups.map((g) => g.rows.length)))
  } catch {
    // storage unavailable - the skeleton keeps the typical shape
  }
}
