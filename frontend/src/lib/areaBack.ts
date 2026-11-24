// Where Back on a Settings or Admin page goes: the page before the user
// entered that area, however many sections they visited since. Kept from the
// router's history index (`idx` in history.state); AppHeader, which every app
// page renders, reports each location here. Its own module so AppHeader does
// not pull the Settings / Admin navigation into the entry bundle.
const AREA = /^\/(settings|admin)(\/|$)/
let areaStart: number | null = null
let lastIdx: number | null = null
const historyIdx = () => (window.history.state as { idx?: unknown } | null)?.idx

/** Records a location for the Settings / Admin Back button. */
export function noteLocation(pathname: string) {
  const idx = historyIdx()
  if (typeof idx !== 'number') return
  if (!AREA.test(pathname)) areaStart = null
  // Entered the area, came back to before its start, or arrived from a page
  // that reported nothing (so the entries in between are unknown).
  else if (areaStart == null || idx < areaStart || (lastIdx != null && idx > lastIdx + 1)) areaStart = idx
  lastIdx = idx
}

/** History steps from here to the page before the area, or null when unknown. */
export function leaveAreaDelta(): number | null {
  const idx = historyIdx()
  if (typeof idx === 'number' && areaStart != null && areaStart > 0 && idx >= areaStart) return areaStart - 1 - idx
  return null
}
