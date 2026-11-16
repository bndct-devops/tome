// Shapes the Library page's answers (/api/stats, /api/stats/library,
// /api/stats/backlog) into what the page draws. Bins, orders and sentences
// only: every figure is the server's own. Pure functions, no React.
import type { LibraryResponse, StatsResponse } from '@/components/stats/shared'

export type Series = StatsResponse['series_completion'][number]

/** The range the range-bound sections answer: in a sentence, as a chip, and as their entry signature (null while loading). */
export type Range = { phrase: string; label: string; sig: string | null }

// ── series: unfinished by percent (ties: more read first), completed by size ──
export function seriesModel(rows: Series[]) {
  const open = rows.filter((s) => s.read < s.total).sort((a, b) => b.pct - a.pct || b.read - a.read || a.series.localeCompare(b.series))
  const done = rows.filter((s) => s.total > 0 && s.read >= s.total).sort((a, b) => b.total - a.total || a.series.localeCompare(b.series))
  return { open, done }
}

// ── added to the library: monthly additions from the cumulative series ───────
export type AddedMonth = { month: string; byType: number[]; total: number }
export type AddedModel = {
  types: string[]
  months: AddedMonth[]
  /** Sum of the drawn months' additions. */
  added: number
  /** Index of a month broken off the scale (far above the rest), else -1. */
  outlier: number
  /** The value the plot's top stands for. */
  top: number
  step: number
}

const AXIS_STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000]
const stepFor = (v: number) => AXIS_STEPS.find((s) => v / s <= 4) ?? AXIS_STEPS[AXIS_STEPS.length - 1]

export function addedModel(growth: StatsResponse['library_growth']): AddedModel {
  const types = growth.length ? Object.keys(growth[0]).filter((k) => k !== 'month' && k !== 'total').sort((a, b) => a.localeCompare(b)) : []
  const val = (row: StatsResponse['library_growth'][number], k: string) => Number(row[k] ?? 0)
  // The first entry has nothing before it to difference against: it counts as
  // additions only when the library was empty before it (its total is all new).
  const diffs: AddedMonth[] = growth.map((row, i) => {
    const prev = i > 0 ? growth[i - 1] : null
    const byType = types.map((k) => Math.max(0, val(row, k) - (prev ? val(prev, k) : 0)))
    return { month: row.month, byType, total: byType.reduce((a, v) => a + v, 0) }
  })
  let start = diffs.findIndex((m) => m.total > 0)
  if (start === 0 && growth[0].total > 0) start = 1 // older than the window: no difference for its first month
  const months = start < 0 ? [] : diffs.slice(start)
  const totals = months.map((m) => m.total)
  const sorted = [...totals].sort((a, b) => b - a)
  const max = sorted[0] ?? 0
  const second = sorted[1] ?? 0
  // One month far above the rest (more than three times the next) is drawn
  // broken, so the other months keep a readable scale.
  const outlier = second > 0 && max > 3 * second ? totals.indexOf(max) : -1
  const capped = outlier >= 0 ? second * 1.3 : max
  const step = stepFor(Math.max(1, capped))
  const top = outlier >= 0 ? capped : Math.max(1, Math.ceil(max / step) * step)
  return { types, months, added: totals.reduce((a, v) => a + v, 0), outlier, top, step }
}

/** The first month with books in the library ("2026-03"), or null when the library is older than the window or empty. */
export function libraryStart(growth: StatsResponse['library_growth']): string | null {
  if (!growth.length || growth[0].total > 0) return null
  return growth.find((g) => g.total > 0)?.month ?? null
}

// ── published: decades, or five-year periods when they fall into fewer than three ──
export type Period = { start: number; span: 5 | 10; count: number }
export function publishedModel(p: LibraryResponse['published']): { periods: Period[]; span: 5 | 10; withYear: number } {
  const years = p.years.filter((y) => y.count > 0)
  const withYear = years.reduce((a, y) => a + y.count, 0)
  if (!years.length) return { periods: [], span: 10, withYear }
  const decades = new Set(years.map((y) => Math.floor(y.year / 10)))
  let span: 5 | 10 = decades.size < 3 ? 5 : 10
  const bin = (y: number, s: number) => Math.floor(y / s) * s
  const range = (s: number) => (bin(years[years.length - 1].year, s) - bin(years[0].year, s)) / s + 1
  // Two decades far apart would give a long row of five-year periods: stay with decades then.
  if (span === 5 && range(5) > 6) span = 10
  const first = bin(Math.min(...years.map((y) => y.year)), span)
  const last = bin(Math.max(...years.map((y) => y.year)), span)
  const periods: Period[] = []
  for (let s = first; s <= last; s += span) periods.push({ start: s, span, count: 0 })
  for (const y of years) periods[(bin(y.year, span) - first) / span].count += y.count
  return { periods, span, withYear }
}

// ── by type: one row per type the library holds ───────────────────────────────
export type TypeRow = { type: string; owned: number; read: number; started: number; finished: number; finishPct: number | null; seconds: number; share: number }
export function typeRows(s: StatsResponse): TypeRow[] {
  const comp = new Map(s.completion_by_type.map((c) => [c.category, c]))
  const time = new Map(s.by_category.map((c) => [c.category, c.seconds]))
  const total = s.by_category.reduce((a, c) => a + c.seconds, 0)
  return s.tbr.by_type.map((t) => {
    const c = comp.get(t.type)
    const seconds = time.get(t.type) ?? 0
    return {
      type: t.type, owned: t.owned, read: t.read,
      started: c?.started ?? 0, finished: c?.finished ?? 0, finishPct: c && c.started > 0 ? c.pct : null,
      seconds, share: total > 0 ? seconds / total : 0,
    }
  })
}

// ── the shape of the last answer, so the skeleton reserves the same rows ─────
export type Shape = { series: number; done: boolean; types: number; authors: number; backlog: number; books: number }
const SHAPE_KEY = 'tome_stats_library_shape'
const DEFAULT_SHAPE: Shape = { series: 3, done: true, types: 4, authors: 2, backlog: 3, books: 10 }
export const SERIES_MAX = 6
export const AUTHORS_MAX = 6
export const BOOKS_SHOWN = 10

export function rememberedShape(): Shape {
  try {
    const raw = JSON.parse(localStorage.getItem(SHAPE_KEY) ?? 'null') as Partial<Shape> | null
    if (!raw || typeof raw !== 'object') return DEFAULT_SHAPE
    const n = (v: unknown, max: number, d: number) => (Number.isInteger(v) && (v as number) >= 0 && (v as number) <= max ? (v as number) : d)
    return {
      series: n(raw.series, SERIES_MAX, DEFAULT_SHAPE.series),
      done: typeof raw.done === 'boolean' ? raw.done : DEFAULT_SHAPE.done,
      types: n(raw.types, 20, DEFAULT_SHAPE.types),
      authors: n(raw.authors, AUTHORS_MAX, DEFAULT_SHAPE.authors),
      backlog: n(raw.backlog, 20, DEFAULT_SHAPE.backlog),
      books: n(raw.books, BOOKS_SHOWN, DEFAULT_SHAPE.books),
    }
  } catch {
    return DEFAULT_SHAPE
  }
}
export function rememberShape(part: Partial<Shape>) {
  try {
    localStorage.setItem(SHAPE_KEY, JSON.stringify({ ...rememberedShape(), ...part }))
  } catch {
    // storage unavailable: the skeleton keeps the default rows
  }
}

/** "Mar 2026" for "2026-03". */
export const fmtMonth = (ym: string) => new Date(ym + '-01T00:00:00').toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
export const fmtMonthShort = (ym: string) => new Date(ym + '-01T00:00:00').toLocaleDateString(undefined, { month: 'short' })

/** The type colours of the additions chart: the app's chart palette, mixed at 75 % like its stacks. */
const TYPE_VARS = ['--chart-accent', '--info', '--success', '--rating', '--muted-foreground', '--warning']
export const typeColor = (k: number) => `color-mix(in srgb, var(${TYPE_VARS[k % TYPE_VARS.length]}) ${k < TYPE_VARS.length ? 75 : 45}%, var(--background))`
