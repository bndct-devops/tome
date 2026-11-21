// The first day of the reader's week (Settings › Language and region › Week starts on), for
// every web display laid out by weekday: the calendar, the stats grids and charts,
// the date pickers. Set from the signed-in user (AuthContext); Monday until known.
// Kept free of React and of path aliases so it runs under plain Node for its tests.

export type WeekStart = 'monday' | 'sunday'
let current: WeekStart = 'monday'

export function setWeekStart(ws: string | null | undefined) {
  current = ws === 'sunday' ? 'sunday' : 'monday'
}
export const getWeekStart = (): WeekStart => current

/** The JS day (0 = Sunday) the reader's week starts on. */
export const firstJsDay = () => (current === 'sunday' ? 0 : 1)

/** The row (0..6) of a JS day (0 = Sunday) in the reader's week. */
export const rowOfJsDay = (jsDay: number) => (((jsDay - firstJsDay()) % 7) + 7) % 7

/** Seven Monday-first items (labels, totals) put in the reader's order. */
export function inWeekOrder<T>(mondayFirst: readonly T[]): T[] {
  return current === 'sunday' ? [mondayFirst[6], ...mondayFirst.slice(0, 6)] : [...mondayFirst]
}
