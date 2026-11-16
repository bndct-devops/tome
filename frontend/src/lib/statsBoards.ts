// Which saved Stats boards are worth showing under the Boards tab.
//
// The designed Stats pages replace the default boards. A saved board that is
// still exactly a default board some release shipped carries nothing of the
// user's own, so the Boards tab leaves it out. It is only hidden: the saved
// dashboard is never rewritten or pruned by this rule. Anything that differs
// (moved, resized, removed, added, reconfigured or renamed) is shown, and when
// in doubt a board is shown.
//
// Kept free of React and of the tile catalog so the page can decide whether the
// Boards tab exists without loading the grid, and so it runs under plain Node
// for its tests (erasable TypeScript only: no enums, no parameter properties).

/** The parts of a saved board this module reads (a subset of the page's TabState). */
export type SavedTile = { id: string; defId: string; config?: Record<string, unknown> | null }
export type SavedLayoutItem = { i: string; x: number; y: number; w: number; h: number }
export type SavedBoard = { id?: string; label?: string; tiles?: SavedTile[] | null; layout?: SavedLayoutItem[] | null }

export type NormTile = { defId: string; x: number; y: number; w: number; h: number; config: Record<string, unknown> }
export type NormBoard = { label: string; tiles: NormTile[] }

// Config keys that change what a tile shows. `scopeLabel` is only the display
// name of `scope`; layout-item extras (minW/minH, moved, static, resizeHandles)
// are not user choices at all.
const CONFIG_KEYS = ['chartType', 'days', 'metric', 'series', 'scope', 'autoFit'] as const
// sanitizePersisted fills these in for every tile, so a missing key and the
// default value are the same thing.
const CONFIG_DEFAULTS: Record<string, unknown> = { chartType: 'bar', days: 0 }

function normaliseConfig(config: Record<string, unknown> | null | undefined): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...CONFIG_DEFAULTS, ...(config ?? {}) }
  const out: Record<string, unknown> = {}
  for (const k of CONFIG_KEYS) {
    const v = merged[k]
    if (v === undefined || v === null) continue
    if (k in CONFIG_DEFAULTS && CONFIG_DEFAULTS[k] === v) continue
    out[k] = v
  }
  return out
}

type Box = { x: number; y: number; w: number; h: number }
const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

/**
 * Vertical compaction as the grid draws it (react-grid-layout's compactItem):
 * items in (y, x) order, each moved up while nothing is in the way, then down
 * past anything it overlaps. Saved y values can carry gaps or overlaps that the
 * grid never shows (a layout saved while auto-fit tiles were drawn shorter, the
 * shipped Library board's two tiles on one row); compacting both sides makes
 * them compare on what is drawn.
 */
function compact(items: (Box & { k: number })[]): (Box & { k: number })[] {
  const sorted = [...items].sort((a, b) => a.y - b.y || a.x - b.x || a.k - b.k)
  const placed: (Box & { k: number })[] = []
  for (const it of sorted) {
    // Whole rows: a y saved under a fitted (fractional) height drops to its row.
    const cur = { ...it, y: Math.max(0, Math.floor(it.y)) }
    while (cur.y > 0 && !placed.some((p) => overlaps({ ...cur, y: cur.y - 1 }, p))) cur.y -= 1
    for (let hit = placed.find((p) => overlaps(cur, p)); hit; hit = placed.find((p) => overlaps(cur, p))) cur.y = hit.y + hit.h
    placed.push(cur)
  }
  return placed
}

/** A board reduced to what the user can see and change: its name, and per tile the widget, box and settings. */
export function normaliseBoard(board: SavedBoard): NormBoard {
  const tiles = board.tiles ?? []
  const layout = board.layout ?? []
  const items = tiles.map((t, k) => {
    const l = layout.find((x) => x.i === t.id)
    // A tile without a layout entry: the grid places it at the origin.
    return { k, x: l?.x ?? 0, y: l?.y ?? 0, w: l?.w ?? 1, h: l?.h ?? 1 }
  })
  const boxes = compact(items)
  const out = boxes.map((b) => ({ defId: tiles[b.k].defId, x: b.x, y: b.y, w: b.w, h: b.h, config: normaliseConfig(tiles[b.k].config) }))
  out.sort((a, b) => a.y - b.y || a.x - b.x || (a.defId < b.defId ? -1 : a.defId > b.defId ? 1 : 0))
  return { label: String(board.label ?? '').trim(), tiles: out }
}

const sameTiles = (a: NormTile[], b: NormTile[]) =>
  a.length === b.length && a.every((t, i) => JSON.stringify(t) === JSON.stringify(b[i]))

// Default board names are stored in the language active when the board was built.
// Every translation the catalogs ever had for them (git log of src/locales).
const LABELS: Record<string, string[]> = {
  Overview: ['Overview', 'Vue d’ensemble'],
  Habits: ['Habits', 'Habitudes'],
  Library: ['Library', 'Bibliothèque'],
  Taste: ['Taste', 'Préférences'],
  Timeline: ['Timeline', 'Chronologie'],
}

export function matchesFingerprint(board: SavedBoard, fp: NormBoard): boolean {
  const n = normaliseBoard(board)
  const names = LABELS[fp.label] ?? [fp.label]
  return names.includes(n.label) && sameTiles(n.tiles, fp.tiles)
}

export function isShippedDefault(board: SavedBoard, fingerprints: NormBoard[] = DEFAULT_BOARD_FINGERPRINTS): boolean {
  return fingerprints.some((fp) => matchesFingerprint(board, fp))
}

/** Ids of the saved boards the Boards tab shows: those that are not a shipped default. */
export function customBoardIds(boards: SavedBoard[] | null | undefined): string[] {
  if (!Array.isArray(boards)) return []
  return boards.filter((b) => b && typeof b === 'object' && !isShippedDefault(b)).map((b) => String(b.id))
}

// ── Fingerprints ──────────────────────────────────────────────────────────────
// Every distinct default board shipped on main since the dashboard landed, from
// `node --experimental-strip-types scripts/stats-default-boards.mjs --ts`
// (it runs buildTabs() of each commit that touched StatsPage.tsx). Board sets
// that only differ in other boards share entries; each entry names the commit
// that first shipped it.
const T = (defId: string, x: number, y: number, w: number, h: number, config: Record<string, unknown> = {}): NormTile => ({ defId, x, y, w, h, config })

export const DEFAULT_BOARD_FINGERPRINTS: NormBoard[] = [
  // Overview, first shipped in 7137bf9 (2026-06-11)
  { label: 'Overview', tiles: [
    T('stat-time', 0, 0, 2, 1),
    T('stat-sessions', 2, 0, 2, 1),
    T('stat-finished', 4, 0, 2, 1),
    T('stat-streak', 6, 0, 2, 1),
    T('stat-pages', 8, 0, 2, 1),
    T('stat-completion', 10, 0, 2, 1),
    T('currently-reading', 0, 1, 12, 5),
    T('daily', 0, 6, 6, 3),
    T('top-books', 6, 6, 6, 3),
    T('activity-365', 0, 9, 12, 2),
    T('books-finished', 0, 11, 12, 2, {chartType: 'area'}),
    T('session-log', 0, 13, 12, 7),
  ] },
  // Habits, first shipped in 7137bf9 (2026-06-11)
  { label: 'Habits', tiles: [
    T('hour-dow', 0, 0, 12, 2),
    T('session-timeline', 0, 2, 12, 3),
    T('reading-pace', 0, 5, 6, 3),
    T('pace-by-format', 6, 5, 6, 3),
    T('speed-trend', 0, 8, 12, 2),
    T('estimates', 0, 10, 12, 3),
    T('period-comparison', 0, 13, 12, 1),
    T('monthly-comparison', 0, 14, 12, 3),
  ] },
  // Library, first shipped in 7137bf9 (2026-06-11)
  { label: 'Library', tiles: [
    T('year-in-review', 0, 0, 12, 2),
    T('series-completion', 0, 2, 12, 4),
    T('author-affinity', 0, 6, 12, 3),
    T('completion-by-type', 0, 9, 12, 2),
    T('category-breakdown', 0, 11, 12, 2),
    T('genre-over-time', 0, 13, 12, 3, {chartType: 'area'}),
    T('library-growth', 0, 16, 12, 3, {chartType: 'area'}),
    T('per-book-table', 0, 19, 12, 6),
  ] },
  // Overview, first shipped in 7e5a9e1 (2026-06-13)
  { label: 'Overview', tiles: [
    T('stat-time', 0, 0, 2, 1),
    T('stat-sessions', 2, 0, 2, 1),
    T('stat-finished', 4, 0, 2, 1),
    T('stat-streak', 6, 0, 2, 1),
    T('stat-pages', 8, 0, 2, 1),
    T('stat-completion', 10, 0, 2, 1),
    T('currently-reading', 0, 1, 12, 5),
    T('daily', 0, 6, 6, 3),
    T('top-books', 6, 6, 6, 3),
    T('activity-365', 0, 9, 12, 2),
    T('books-finished', 0, 11, 12, 2, {chartType: 'area'}),
    T('session-log', 0, 13, 12, 7),
    T('goal', 0, 20, 6, 3),
  ] },
  // Taste, first shipped in 5c3869a (2026-06-24)
  { label: 'Taste', tiles: [
    T('rating-distribution', 0, 0, 6, 2),
    T('taste-by-genre', 6, 0, 6, 2),
    T('rating-vs-time', 0, 2, 6, 2),
    T('rating-trend', 6, 2, 6, 2),
    T('top-rated', 0, 4, 6, 3),
    T('best-rated-series', 6, 4, 6, 3),
  ] },
  // Overview, first shipped in df43c6b (2026-06-24)
  { label: 'Overview', tiles: [
    T('stat-time', 0, 0, 2, 1),
    T('stat-sessions', 2, 0, 2, 1),
    T('stat-finished', 4, 0, 2, 1),
    T('stat-streak', 6, 0, 2, 1),
    T('stat-pages', 8, 0, 2, 1),
    T('stat-completion', 10, 0, 2, 1),
    T('currently-reading', 0, 1, 12, 5),
    T('daily', 0, 6, 6, 3),
    T('top-books', 6, 6, 6, 3),
    T('activity-365', 0, 9, 12, 2),
    T('books-finished', 0, 11, 12, 2, {chartType: 'area'}),
    T('session-log', 0, 13, 12, 7),
    T('goal', 0, 20, 6, 3),
    T('lifetime-totals', 0, 23, 12, 2),
  ] },
  // Habits, first shipped in df43c6b (2026-06-24)
  { label: 'Habits', tiles: [
    T('hour-dow', 0, 0, 12, 2),
    T('session-timeline', 0, 2, 12, 3),
    T('reading-pace', 0, 5, 6, 3),
    T('pace-by-format', 6, 5, 6, 3),
    T('speed-trend', 0, 8, 12, 2),
    T('estimates', 0, 10, 12, 3),
    T('period-comparison', 0, 13, 12, 1),
    T('monthly-comparison', 0, 14, 12, 3),
    T('reading-clock', 0, 17, 4, 3),
  ] },
  // Library, first shipped in df43c6b (2026-06-24)
  { label: 'Library', tiles: [
    T('year-in-review', 0, 0, 12, 2),
    T('series-completion', 0, 2, 12, 4),
    T('author-affinity', 0, 6, 12, 3),
    T('completion-by-type', 0, 9, 12, 2),
    T('category-breakdown', 0, 11, 12, 2),
    T('genre-over-time', 0, 13, 12, 3, {chartType: 'area'}),
    T('library-growth', 0, 16, 12, 3, {chartType: 'area'}),
    T('per-book-table', 0, 19, 12, 6),
    T('library-completion', 0, 25, 4, 3),
    T('personal-records', 4, 25, 4, 3),
    T('reading-by-language', 8, 25, 4, 2),
  ] },
  // Overview, first shipped in 3f3ad85 (2026-06-24)
  { label: 'Overview', tiles: [
    T('stat-time', 0, 0, 2, 1),
    T('stat-sessions', 2, 0, 2, 1),
    T('stat-finished', 4, 0, 2, 1),
    T('stat-streak', 6, 0, 2, 1),
    T('stat-pages', 8, 0, 2, 1),
    T('stat-completion', 10, 0, 2, 1),
    T('currently-reading', 0, 1, 12, 5, {autoFit: true}),
    T('daily', 0, 6, 6, 3),
    T('top-books', 6, 6, 6, 3),
    T('activity-365', 0, 9, 12, 2),
    T('books-finished', 0, 11, 12, 2, {chartType: 'area'}),
    T('session-log', 0, 13, 12, 7),
    T('goal', 0, 20, 6, 3),
  ] },
  // Habits, first shipped in 3f3ad85 (2026-06-24)
  { label: 'Habits', tiles: [
    T('hour-dow', 0, 0, 12, 2),
    T('session-timeline', 0, 2, 12, 3),
    T('reading-pace', 0, 5, 6, 3),
    T('pace-by-format', 6, 5, 6, 3),
    T('speed-trend', 0, 8, 12, 2),
    T('estimates', 0, 10, 12, 3, {autoFit: true}),
    T('period-comparison', 0, 13, 12, 1),
    T('monthly-comparison', 0, 14, 12, 3),
  ] },
  // Library, first shipped in 3f3ad85 (2026-06-24)
  { label: 'Library', tiles: [
    T('year-in-review', 0, 0, 12, 2),
    T('series-completion', 0, 2, 12, 4, {autoFit: true}),
    T('author-affinity', 0, 6, 12, 3),
    T('completion-by-type', 0, 9, 12, 2),
    T('category-breakdown', 0, 11, 12, 2),
    T('genre-over-time', 0, 13, 12, 3, {chartType: 'area'}),
    T('library-growth', 0, 16, 12, 3, {chartType: 'area'}),
    T('per-book-table', 0, 19, 12, 6),
  ] },
  // Overview, first shipped in 18260d5 (2026-06-24)
  { label: 'Overview', tiles: [
    T('stat-time', 0, 0, 2, 1),
    T('stat-sessions', 2, 0, 2, 1),
    T('stat-finished', 4, 0, 2, 1),
    T('stat-streak', 6, 0, 2, 1),
    T('stat-pages', 8, 0, 2, 1),
    T('stat-completion', 10, 0, 2, 1),
    T('currently-reading', 0, 1, 12, 5, {autoFit: true}),
    T('daily', 0, 6, 6, 3),
    T('top-books', 6, 6, 6, 3),
    T('activity-365', 0, 9, 12, 2),
    T('books-finished', 0, 11, 12, 2, {chartType: 'area'}),
    T('session-log', 0, 13, 12, 7),
  ] },
  // Timeline, first shipped in 9f62e70 (2026-07-06)
  { label: 'Timeline', tiles: [
    T('reading-timeline', 0, 0, 12, 7),
  ] },
  // Library, first shipped in 9a818a9 (2026-08-22)
  { label: 'Library', tiles: [
    T('year-in-review', 0, 0, 12, 2),
    T('series-completion', 0, 2, 12, 4, {autoFit: true}),
    T('backlog-estimate', 0, 6, 12, 2, {scope: 'want', autoFit: true}),
    T('author-affinity', 0, 8, 12, 3),
    T('completion-by-type', 0, 11, 12, 2),
    T('category-breakdown', 0, 13, 12, 2),
    T('genre-over-time', 0, 15, 12, 3, {chartType: 'area'}),
    T('library-growth', 0, 18, 12, 3, {chartType: 'area'}),
    T('per-book-table', 0, 21, 12, 6),
  ] },
  // Overview, first shipped in 4d580e3 (2026-11-08)
  { label: 'Overview', tiles: [
    T('stat-time', 0, 0, 2, 1),
    T('stat-sessions', 2, 0, 2, 1),
    T('stat-finished', 4, 0, 2, 1),
    T('stat-streak', 6, 0, 2, 1),
    T('stat-pages', 8, 0, 2, 1),
    T('stat-completion', 10, 0, 2, 1),
    T('currently-reading', 0, 1, 12, 5, {autoFit: true}),
    T('daily', 0, 6, 6, 3),
    T('top-books', 6, 6, 6, 3, {autoFit: true}),
    T('activity-365', 0, 9, 12, 2),
    T('books-finished', 0, 11, 12, 2, {chartType: 'area', autoFit: true}),
    T('session-log', 0, 13, 12, 7),
  ] },
  // Habits, first shipped in 4d580e3 (2026-11-08)
  { label: 'Habits', tiles: [
    T('hour-dow', 0, 0, 12, 2),
    T('session-timeline', 0, 2, 12, 3),
    T('reading-pace', 0, 5, 6, 3),
    T('pace-by-format', 6, 5, 6, 3, {autoFit: true}),
    T('speed-trend', 0, 8, 12, 2),
    T('estimates', 0, 10, 12, 3, {autoFit: true}),
    T('period-comparison', 0, 13, 12, 1),
    T('monthly-comparison', 0, 14, 12, 3),
  ] },
  // Library, first shipped in 4d580e3 (2026-11-08)
  { label: 'Library', tiles: [
    T('year-in-review', 0, 0, 12, 2, {autoFit: true}),
    T('series-completion', 0, 2, 12, 4, {autoFit: true}),
    T('backlog-estimate', 0, 6, 12, 2, {scope: 'want', autoFit: true}),
    T('author-affinity', 0, 8, 12, 3, {autoFit: true}),
    T('completion-by-type', 0, 11, 12, 2, {autoFit: true}),
    T('category-breakdown', 0, 13, 12, 2),
    T('genre-over-time', 0, 15, 12, 3, {chartType: 'area'}),
    T('library-growth', 0, 18, 12, 3, {chartType: 'area'}),
    T('per-book-table', 0, 21, 12, 6, {autoFit: true}),
  ] },
  // Taste, first shipped in 4d580e3 (2026-11-08)
  { label: 'Taste', tiles: [
    T('rating-distribution', 0, 0, 6, 2),
    T('taste-by-genre', 6, 0, 6, 2),
    T('rating-vs-time', 0, 2, 6, 2),
    T('rating-trend', 6, 2, 6, 2),
    T('top-rated', 0, 4, 6, 3, {autoFit: true}),
    T('best-rated-series', 6, 4, 6, 3, {autoFit: true}),
  ] },
]
