// Shaping of the all-time ratings block for the Taste page: shelf and list
// orders, the scatter's lanes, the medians sentence and the time chart's dots,
// counts and stepped mean. Everything here is drawn from `stats.ratings`; no
// figure the server computes is recomputed.
import type { StatsResponse } from '@/components/stats/shared'

// lucide Star, the glyph the app's StarRating draws
export const STAR_PATH = 'M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z'

export type Ratings = StatsResponse['ratings']
export type RatedBook = Ratings['books'][number]

const ts = (b: RatedBook) => b.rated_at ?? ''

/** Highest rated: rating desc, then the most recently rated first. */
export function shelfOrder(books: RatedBook[]): RatedBook[] {
  return books.slice().sort((a, b) => b.rating - a.rating || ts(b).localeCompare(ts(a)))
}

/**
 * Lowest rated: the four lowest, rating asc then most recent first, from the
 * books below the top rating (so a book never sits on the shelf and in this
 * list at once, whatever the shelf's width).
 */
export function lowestFour(books: RatedBook[]): RatedBook[] {
  if (books.length === 0) return []
  const top = Math.max(...books.map((b) => b.rating))
  return books
    .filter((b) => b.rating < top)
    .sort((a, b) => a.rating - b.rating || ts(b).localeCompare(ts(a)))
    .slice(0, 4)
}

const median = (xs: number[]) => {
  const s = xs.slice().sort((a, b) => a - b)
  const n = s.length
  return n === 0 ? null : n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2
}

/**
 * Median reading time of the books rated exactly a whole star value, for the
 * two highest values with at least two such books that have time on record.
 * Half-star ratings and books never read in Tome (0 s) are left out.
 */
export function wholeStarMedians(books: RatedBook[]): { stars: number; n: number; seconds: number }[] {
  const out: { stars: number; n: number; seconds: number }[] = []
  for (let r = 5; r >= 1 && out.length < 2; r--) {
    const xs = books.filter((b) => b.rating === r && b.seconds > 0).map((b) => b.seconds)
    if (xs.length >= 2) out.push({ stars: r, n: xs.length, seconds: Math.round(median(xs)!) })
  }
  return out
}

/** A step for the hours axis giving at most five intervals. */
export function hourStep(maxHours: number): { step: number; top: number } {
  const STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500]
  const step = STEPS.find((s) => Math.ceil(maxHours / s) <= 4) ?? 1000
  return { step, top: Math.max(step, Math.ceil(maxHours / step) * step) }
}

export type ScatterDot = { cx: number; cy: number; half: boolean; book: RatedBook }

/**
 * Dots of the time-vs-rating chart. Whole stars sit in their lane, nudged up or
 * down to the nearest free spot (a deterministic beeswarm); half stars sit on
 * the line between two lanes, not nudged.
 */
export function scatterDots(books: RatedBook[], x: (s: number) => number, y: (r: number) => number, laneH: number, rad: number): ScatterDot[] {
  const lim = laneH / 2 - rad - 1
  const pts = books.map((b) => ({ b, cx: x(b.seconds), half: b.rating % 1 !== 0 })).sort((a, b) => a.cx - b.cx || a.b.book_id - b.b.book_id)
  const placed: ScatterDot[] = []
  for (const p of pts) {
    const yc = y(p.b.rating)
    let cy = yc
    if (!p.half) {
      const cands = [0]
      for (let k = 1; k * 3 <= lim; k++) cands.push(-k * 3, k * 3)
      const free = cands.find((off) => !placed.some((q) => (q.cx - p.cx) ** 2 + (q.cy - (yc + off)) ** 2 < (2 * rad - 0.5) ** 2))
      cy = yc + (free ?? cands[cands.length - 1])
    }
    placed.push({ cx: p.cx, cy, half: p.half, book: p.b })
  }
  return placed
}

export type TimePoint = { date: string; rating: number; title: string; id: number; mean: number | null }
export type TimeGroup = { date: string; rating: number; titles: string[]; ids: number[] }

/**
 * Every rating with a time, in the order it was given (full timestamps; the
 * server's `trend` has dates only), with the mean of the last five from the
 * fifth rating on. The date is the server's own (`trend` takes rated_at[:10]).
 */
export function timeline(books: RatedBook[]): { points: TimePoint[]; groups: TimeGroup[] } {
  const points: TimePoint[] = books
    .filter((b) => b.rated_at)
    .slice()
    .sort((a, b) => ts(a).localeCompare(ts(b)) || a.book_id - b.book_id)
    .map((b) => ({ date: ts(b).slice(0, 10), rating: b.rating, title: b.title, id: b.book_id, mean: null }))
  points.forEach((p, i) => {
    if (i >= 4) p.mean = points.slice(i - 4, i + 1).reduce((a, q) => a + q.rating, 0) / 5
  })
  const groups: TimeGroup[] = []
  for (const p of points) {
    let g = groups.find((q) => q.date === p.date && q.rating === p.rating)
    if (!g) groups.push((g = { date: p.date, rating: p.rating, titles: [], ids: [] }))
    g.titles.push(p.title)
    g.ids.push(p.id)
  }
  return { points, groups }
}

/** Day number of a YYYY-MM-DD (UTC noon, so DST never shifts it). */
export const dayNum = (iso: string) => new Date(iso + 'T12:00:00Z').getTime() / 86400000

type Box = { x0: number; x1: number; y0: number; y1: number }
const hits = (bs: Box[], b: Box) => bs.some((q) => b.x0 < q.x1 && b.x1 > q.x0 && b.y0 < q.y1 && b.y1 > q.y0)

/**
 * Places each merged dot's count ("×15") where it crosses no dot, no stretch of
 * the mean line and no other count; falls back to the left of the dot.
 */
export function placeCounts(
  groups: { cx: number; cy: number; n: number }[],
  meanPath: { x: number; y: number }[],
  rad: number,
  bounds: { x0: number; x1: number; y0: number; y1: number },
): ({ x: number; y: number; label: string } | null)[] {
  const boxes: Box[] = groups.map((g) => ({ x0: g.cx - rad, x1: g.cx + rad, y0: g.cy - rad, y1: g.cy + rad }))
  for (let k = 1; k < meanPath.length; k++) {
    const a = meanPath[k - 1], b = meanPath[k]
    boxes.push({ x0: a.x, x1: b.x, y0: a.y - 1.5, y1: a.y + 1.5 })
    boxes.push({ x0: b.x - 1.5, x1: b.x + 1.5, y0: Math.min(a.y, b.y), y1: Math.max(a.y, b.y) })
  }
  return groups.map((g) => {
    if (g.n < 2) return null
    const label = `×${g.n}`
    const tw = label.length * 6.5 + 2
    const { cx, cy } = g
    const cands: [number, number][] = [
      [cx - rad - 4 - tw, cy - 6], [cx + rad + 4, cy - 6], [cx - tw / 2, cy - rad - 15], [cx - tw / 2, cy + rad + 3],
      [cx - tw - 2, cy - rad - 15], [cx + 2, cy - rad - 15], [cx - tw - 2, cy + rad + 3], [cx + 2, cy + rad + 3],
    ]
    const fits = ([lx, ly]: [number, number]) => lx >= bounds.x0 && lx + tw <= bounds.x1 && ly >= bounds.y0 && ly + 12 <= bounds.y1
    const free = cands.find((c) => fits(c) && !hits(boxes, { x0: c[0], x1: c[0] + tw, y0: c[1], y1: c[1] + 12 })) ?? cands.find(fits) ?? cands[0]
    boxes.push({ x0: free[0], x1: free[0] + tw, y0: free[1], y1: free[1] + 12 })
    return { x: free[0], y: free[1] + 10, label }
  })
}
