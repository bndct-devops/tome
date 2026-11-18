// The saved Stats boards (GET/PUT /api/stats/dashboard, localStorage
// `tome_stats_lab_v2`). Types and storage helpers only, so the Stats page can
// read the saved boards without loading the grid; the grid itself lives in
// BoardsView, loaded on demand.
import type { Layout } from 'react-grid-layout'
import type { ChartKind } from '@/components/stats/widgets/overview'

// Per-tile settings: chart style, timeframe and (for the Custom Stat tile) the
// metric. days = 0 follows the page range; days = N shows the last N days of the
// fetched window (sliced client-side, no extra fetches) - so two copies of one
// widget can show different timeframes.
export type TileConfig = { chartType: ChartKind; days: number; metric?: string; series?: string; autoFit?: boolean; scope?: string; scopeLabel?: string }
export type Tile = { id: string; defId: string; config: TileConfig }
// Each tab is its own board (own tiles + layout), independently customizable.
export type TabState = { id: string; label: string; tiles: Tile[]; layout: Layout }

// Side-padding setting. Percentage gutters (not max-width caps) so the three levels
// always differ proportionally, even on a narrow window. Phones always get px-4  - 
// a 16% gutter on a 390px screen would squeeze the tiles to ~265px.
export type PadWidth = 'none' | 'bit' | 'lot'
// The refreshed shell centres every page in a column, so the setting now picks
// the column's width: "A lot" is the standard 1180px dashboard column, "A bit"
// a wider one, "None" the full panel.
export const PAGE_W: Record<PadWidth, string> = {
  none: 'max-w-none',
  bit: 'max-w-[1480px]',
  lot: '',
}

// v2: defaults became the real-Stats-page replica - bumping the key discards
// boards saved under the old defaults so everyone starts from the replica.
export const LS_KEY = 'tome_stats_lab_v2'

// The saved blob. Its shape is unchanged since the dashboard shipped: older
// clients still read `activeTabId` (a board id) and `days` (the range preset).
// Only edits made under Boards write it.
// `showBoards` is the Settings switch that lists every board under a Boards tab
// (see lib/statsBoards); older clients ignore it.
export type Persisted = { tabs: TabState[]; activeTabId: string; pad: PadWidth; days: number; savedAt?: number; showBoards?: boolean }

/** The local copy of the saved boards, shape-checked only (no widget catalog here). */
export function loadLocalDashboard(): Persisted | null {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return null
    const p = JSON.parse(raw) as Persisted
    return p && Array.isArray(p.tabs) && p.tabs.length > 0 ? p : null
  } catch {
    return null
  }
}

// This browser's last known state of the "Show Boards" switch, so the Boards tab
// is there on the first frame; the server's blob is the truth and replaces it.
const BOARDS_ON_KEY = 'tome_stats_boards_on'

export function loadBoardsOn(): boolean {
  try {
    return localStorage.getItem(BOARDS_ON_KEY) === '1'
  } catch {
    return false
  }
}

export function saveBoardsOn(on: boolean) {
  try {
    if (on) localStorage.setItem(BOARDS_ON_KEY, '1')
    else localStorage.removeItem(BOARDS_ON_KEY)
  } catch {
    // storage unavailable - the tab follows the server answer instead
  }
}

export function saveLocalDashboard(p: Persisted) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(p))
  } catch {
    // storage unavailable - the server copy still holds the boards
  }
}
