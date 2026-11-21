// Unit tests for the "is this saved board a shipped default?" rule.
//   npm run test:unit
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_BOARD_FINGERPRINTS,
  boardsTabVisible,
  customBoardIds,
  isShippedDefault,
  listedBoards,
  normaliseBoard,
  readShowBoards,
  withShowBoards,
  type NormBoard,
  type SavedBoard,
} from '../src/lib/statsBoards.ts'

// A fingerprint turned back into a board as the page saves it: tile ids equal to
// the widget id, layout items with the minW/minH the page bakes in, full config.
function saved(fp: NormBoard, id = fp.label.toLowerCase()): SavedBoard & { tiles: NonNullable<SavedBoard['tiles']>; layout: NonNullable<SavedBoard['layout']> } {
  return {
    id,
    label: fp.label,
    tiles: fp.tiles.map((t) => ({ id: t.defId, defId: t.defId, config: { chartType: 'bar', days: 0, ...t.config } })),
    layout: fp.tiles.map((t) => ({ i: t.defId, x: t.x, y: t.y, w: t.w, h: t.h, minW: 2, minH: 1 })),
  }
}

const OVERVIEW_NOW = DEFAULT_BOARD_FINGERPRINTS.filter((fp) => fp.label === 'Overview').at(-1)!

test('every historical default board matches', () => {
  assert.ok(DEFAULT_BOARD_FINGERPRINTS.length >= 5)
  for (const fp of DEFAULT_BOARD_FINGERPRINTS) assert.equal(isShippedDefault(saved(fp)), true, fp.label)
})

test('fields the grid adds and generated tile ids do not count', () => {
  const b = saved(OVERVIEW_NOW)
  b.layout = b.layout.map((l) => ({ ...l, moved: false, static: false }))
  b.tiles = b.tiles.map((t) => ({ ...t, id: `${t.defId}--mi1k2x05` }))
  b.layout = b.layout.map((l) => ({ ...l, i: `${l.i}--mi1k2x05` }))
  assert.equal(isShippedDefault(b), true)
})

test('config written out in full equals config left to the defaults', () => {
  const b = saved(OVERVIEW_NOW)
  b.tiles = b.tiles.map((t) => ({ ...t, config: Object.fromEntries(Object.entries(t.config ?? {}).filter(([k, v]) => !(k === 'chartType' && v === 'bar') && !(k === 'days' && v === 0))) }))
  assert.equal(isShippedDefault(b), true)
})

test('a layout saved with gaps or overlaps that the grid compacts still matches', () => {
  // The shipped Library board of 9a818a9 put two tiles on the same rows; a board
  // saved while auto-fit tiles were drawn shorter has everything below moved up.
  const b = saved(OVERVIEW_NOW)
  b.layout = b.layout.map((l) => (l.y >= 6 ? { ...l, y: l.y - 3 } : l))
  assert.equal(isShippedDefault(b), true)
  const gap = saved(OVERVIEW_NOW)
  gap.layout = gap.layout.map((l) => (l.y >= 9 ? { ...l, y: l.y + 4 } : l))
  assert.equal(isShippedDefault(gap), true)
})

test('a translated default name matches', () => {
  const b = saved(OVERVIEW_NOW)
  b.label = 'Vue d’ensemble'
  assert.equal(isShippedDefault(b), true)
})

test('one tile moved does not match', () => {
  const b = saved(OVERVIEW_NOW)
  // daily and top-books swap sides
  b.layout = b.layout.map((l) => (l.i === 'daily' ? { ...l, x: 6 } : l.i === 'top-books' ? { ...l, x: 0 } : l))
  assert.equal(isShippedDefault(b), false)
  const down = saved(OVERVIEW_NOW)
  // activity grid moved below the finished chart
  down.layout = down.layout.map((l) => (l.i === 'activity-365' ? { ...l, y: 12 } : l))
  assert.equal(isShippedDefault(down), false)
})

test('one tile resized does not match', () => {
  const b = saved(OVERVIEW_NOW)
  b.layout = b.layout.map((l) => (l.i === 'currently-reading' ? { ...l, h: 2 } : l))
  assert.equal(isShippedDefault(b), false)
  const wide = saved(OVERVIEW_NOW)
  wide.layout = wide.layout.map((l) => (l.i === 'daily' ? { ...l, w: 5 } : l))
  assert.equal(isShippedDefault(wide), false)
})

test('one tile removed does not match', () => {
  const b = saved(OVERVIEW_NOW)
  b.tiles = b.tiles.filter((t) => t.defId !== 'session-log')
  b.layout = b.layout.filter((l) => l.i !== 'session-log')
  assert.equal(isShippedDefault(b), false)
})

test('one tile added does not match', () => {
  const b = saved(OVERVIEW_NOW)
  b.tiles.push({ id: 'goal--x1', defId: 'goal', config: { chartType: 'bar', days: 0 } })
  b.layout.push({ i: 'goal--x1', x: 0, y: 30, w: 6, h: 2 })
  assert.equal(isShippedDefault(b), false)
})

test('one tile config changed does not match', () => {
  for (const change of [{ chartType: 'line' }, { days: 14 }, { autoFit: false }]) {
    const b = saved(OVERVIEW_NOW)
    b.tiles = b.tiles.map((t) => (t.defId === (('autoFit' in change) ? 'currently-reading' : 'daily') ? { ...t, config: { ...t.config, ...change } } : t))
    assert.equal(isShippedDefault(b), false, JSON.stringify(change))
  }
  const lib = DEFAULT_BOARD_FINGERPRINTS.filter((fp) => fp.label === 'Library').at(-1)!
  const b = saved(lib)
  b.tiles = b.tiles.map((t) => (t.defId === 'backlog-estimate' ? { ...t, config: { ...t.config, scope: 'unread', scopeLabel: 'All unread' } } : t))
  assert.equal(isShippedDefault(b), false)
})

test('a renamed board does not match', () => {
  const b = saved(OVERVIEW_NOW)
  b.label = 'My overview'
  assert.equal(isShippedDefault(b), false)
  const swapped = saved(OVERVIEW_NOW)
  swapped.label = 'Habits'
  assert.equal(isShippedDefault(swapped), false)
})

test('customBoardIds keeps only boards that differ, and tolerates junk', () => {
  const overview = saved(OVERVIEW_NOW)
  const mine = { ...saved(OVERVIEW_NOW, 'tab--abc'), label: 'Mine' }
  assert.deepEqual(customBoardIds([overview, mine]), ['tab--abc'])
  assert.deepEqual(customBoardIds(null), [])
  assert.deepEqual(customBoardIds([{ id: 'empty', label: 'New board', tiles: [], layout: [] }]), ['empty'])
})

test('normalisation reads only the boxes and settings', () => {
  const n = normaliseBoard(saved(OVERVIEW_NOW))
  assert.deepEqual(Object.keys(n.tiles[0]).sort(), ['config', 'defId', 'h', 'w', 'x', 'y'])
})

// ── The "Show Boards" switch ──────────────────────────────────────────────────

test('Boards tab: never set follows the customised boards, set on always shows it, set off never does', () => {
  // Never set (null): exactly as before the switch.
  assert.equal(boardsTabVisible(null, []), false)
  assert.equal(boardsTabVisible(null, ['tab--abc']), true)
  // Set on.
  assert.equal(boardsTabVisible(true, []), true)
  assert.equal(boardsTabVisible(true, ['tab--abc']), true)
  // Set off: hidden even with boards of the reader's own.
  assert.equal(boardsTabVisible(false, []), false)
  assert.equal(boardsTabVisible(false, ['tab--abc']), false)
  assert.equal(boardsTabVisible(false, ['overview', 'habits', 'library']), false)
})

test('listing: set on lists every board; never set or set off lists the shown boards in saved order', () => {
  const boards = [{ id: 'overview' }, { id: 'tab--abc' }, { id: 'habits' }, { id: 'tab--def' }]
  for (const flag of [null, false] as const) {
    assert.deepEqual(listedBoards(boards, ['tab--def', 'tab--abc'], flag).map((b) => b.id), ['tab--abc', 'tab--def'], String(flag))
    assert.deepEqual(listedBoards(boards, [], flag), [], String(flag))
  }
  assert.deepEqual(listedBoards(boards, ['tab--abc'], true).map((b) => b.id), ['overview', 'tab--abc', 'habits', 'tab--def'])
  assert.deepEqual(listedBoards(boards, [], true).map((b) => b.id), ['overview', 'tab--abc', 'habits', 'tab--def'])
  assert.deepEqual(listedBoards([], [], true), [])
})

test('the switch is read as set only when the blob holds a boolean', () => {
  assert.equal(readShowBoards({ showBoards: true }), true)
  assert.equal(readShowBoards({ showBoards: false }), false)
  assert.equal(readShowBoards({ showBoards: false, tabs: [] }), false)
  for (const blob of [null, undefined, {}, { showBoards: 'true' }, { showBoards: 1 }, { showBoards: 0 }, { showBoards: null }, [], 'x']) {
    assert.equal(readShowBoards(blob), null, JSON.stringify(blob))
  }
})

test('setting the switch changes only that key, always to an explicit value', () => {
  const blob = { tabs: [saved(OVERVIEW_NOW)], activeTabId: 'overview', pad: 'lot', days: 30, savedAt: 1700000000000 }
  const before = JSON.stringify(blob)
  for (const value of [true, false]) {
    const set = withShowBoards(blob, value)
    assert.equal(set.showBoards, value)
    assert.equal(JSON.stringify(blob), before, 'the input is not mutated')
    const { showBoards: _drop, ...rest } = set
    void _drop
    assert.equal(JSON.stringify(rest), before)
  }
  // Off then on: the boards are exactly as they were.
  const back = withShowBoards(withShowBoards(blob, false), true)
  assert.deepEqual(back, { ...blob, showBoards: true })
  // No saved blob yet (a new account): the flag alone.
  assert.deepEqual(withShowBoards(null, true), { showBoards: true })
  assert.deepEqual(withShowBoards(null, false), { showBoards: false })
})

test('a blob holding only the flag has no saved boards', () => {
  for (const value of [true, false]) {
    const only = withShowBoards(null, value) as { tabs?: SavedBoard[] }
    assert.deepEqual(customBoardIds(only.tabs), [])
    // The checks the Stats page and older clients make before using a saved blob.
    assert.equal(Array.isArray(only.tabs) && only.tabs.length > 0, false)
  }
  // Set off, then on again with nothing saved: every board listed is a default built from the code.
  assert.equal(boardsTabVisible(readShowBoards(withShowBoards(withShowBoards(null, false), true)), []), true)
})

test('the classification ignores the switch', () => {
  const overview = saved(OVERVIEW_NOW)
  const mine = { ...saved(OVERVIEW_NOW, 'tab--abc'), label: 'Mine' }
  const off = { tabs: [overview, mine] }
  const on = withShowBoards(off, true) as typeof off
  assert.deepEqual(customBoardIds(on.tabs), customBoardIds(off.tabs))
  assert.deepEqual(customBoardIds(on.tabs), ['tab--abc'])
  assert.equal(isShippedDefault({ ...overview, showBoards: true } as SavedBoard), true)
})
