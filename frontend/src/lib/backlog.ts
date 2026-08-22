// Backlog completion estimates (#187) — shared types + formatting for the
// book page cell, the series header line and the Stats "Backlog" tile.

export interface EstimatePace {
  wpm: number | null
  default_wpm: number
  minutes_per_day: number | null
  window_days: number
}

export type EstimateMethod = 'words' | 'default' | 'type_avg'

export interface BookEstimate {
  seconds: number | null
  days: number | null
  method: EstimateMethod | null
  pace: EstimatePace
}

export interface BacklogByType {
  label: string
  books: number
  seconds: number
  unestimated: number
  type_avg: number
  days: number | null
}

export interface BacklogSummary {
  books: number
  estimated: number
  unestimated: number
  seconds: number
  days: number | null
  by_type: BacklogByType[]
  pace: EstimatePace
}

export interface BacklogScope {
  id: string
  label: string
  group: 'Libraries' | 'Shelves' | null
}

/** "~45 min" / "~11 h" — coarse on purpose, these are estimates. */
export function formatEstimateHours(seconds: number): string {
  if (seconds < 3600) return `~${Math.max(1, Math.round(seconds / 60))} min`
  return `~${Math.round(seconds / 3600)} h`
}

/** "~13 days" / "~4 weeks" / "~3.7 months" at the user's daily pace. */
export function formatEstimateDays(days: number): string {
  const d = Math.round(days)
  if (d < 1) return 'under a day'
  if (d < 14) return `~${d} day${d === 1 ? '' : 's'}`
  if (d < 60) return `~${Math.round(d / 7)} weeks`
  return `~${(d / 30).toFixed(1).replace(/\.0$/, '')} months`
}

/** One-line explanation of where a figure came from, for tooltips. */
export function describePace(pace: EstimatePace, method: EstimateMethod | null): string {
  const parts: string[] = []
  if (method === 'words') parts.push(`Word count at your measured ${Math.round(pace.wpm ?? 0)} wpm`)
  else if (method === 'default') parts.push(`Word count at a default ${pace.default_wpm} wpm (no finished books to measure your pace yet)`)
  else if (method === 'type_avg') parts.push('Your average time per finished book of this type (no word count)')
  if (pace.minutes_per_day) parts.push(`days at ~${Math.round(pace.minutes_per_day)} min a day (last ${pace.window_days} days)`)
  else parts.push('no recent reading, so no day estimate')
  return parts.join(' · ')
}
