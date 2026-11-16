// Every default Stats board set that was ever on main, for the "is this saved board
// untouched?" fingerprints in src/lib/statsBoards.ts.
//
// For each commit since the dashboard landed (7137bf9, #44) that touched
// StatsPage.tsx, the file is transpiled and its module-level code run with every
// import stubbed, then `buildTabs()` is called: that is exactly the board set a new
// user got from that build. Boards are normalised as the matcher normalises them,
// and distinct sets are printed with the first and last commit that shipped them.
//
//   node --experimental-strip-types scripts/stats-default-boards.mjs          # table
//   node --experimental-strip-types scripts/stats-default-boards.mjs --json   # raw
//   node --experimental-strip-types scripts/stats-default-boards.mjs --ts     # the table in statsBoards.ts
import { execFileSync } from 'node:child_process'
import vm from 'node:vm'
import ts from 'typescript'
import { normaliseBoard } from '../src/lib/statsBoards.ts'

const FILE = 'frontend/src/pages/StatsPage.tsx'
const git = (...a) => execFileSync('git', a, { encoding: 'utf8', maxBuffer: 64 << 20 })
const commits = git('log', '--reverse', '--format=%h %ad', '--date=short', '7137bf9^..HEAD', '--', FILE)
  .trim().split('\n').map((l) => { const [hash, date] = l.split(' '); return { hash, date } })

// A callable proxy stands in for every import: tagged templates (msg`…`, t`…`)
// return their text, i18n._(x) returns x, anything else returns the proxy again.
function stub() {
  const fn = function (...args) {
    const [first] = args
    if (Array.isArray(first) && first.raw) return first.reduce((s, part, i) => s + part + (i < args.length - 1 ? String(args[i + 1]) : ''), '')
    if (typeof first === 'string') return first
    if (first && typeof first === 'object' && typeof first.message === 'string') return first.message
    return proxy
  }
  const proxy = new Proxy(fn, {
    get: (_t, k) => (k === Symbol.toPrimitive ? () => '' : k === '__esModule' ? true : proxy),
  })
  return proxy
}

function defaultsAt(hash) {
  const src = git('show', `${hash}:${FILE}`) + '\n;module.exports.__boards = buildTabs();\n'
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  }).outputText
  const module = { exports: {} }
  // Constants a default tile config reads from another module, by value at that commit.
  const real = (spec) => {
    if (!spec.endsWith('/BacklogEstimate')) return stub()
    const m = git('show', `${hash}:frontend/src/components/stats/BacklogEstimate.tsx`).match(/DEFAULT_BACKLOG_SCOPE\s*=\s*'([^']+)'/)
    return new Proxy({ DEFAULT_BACKLOG_SCOPE: m?.[1] }, { get: (o, k) => (k in o ? o[k] : stub()) })
  }
  const ctx = vm.createContext({ module, exports: module.exports, require: real, React: stub(), console, Date, Math, JSON, Object, Array, Map, Set, String, Number })
  vm.runInContext(js, ctx, { filename: `${hash}.js` })
  return JSON.parse(JSON.stringify(module.exports.__boards))
}

const sets = []
for (const c of commits) {
  let boards
  try { boards = defaultsAt(c.hash) } catch (e) { console.error(`${c.hash}: ${e.message}`); continue }
  const norm = boards.map((b) => normaliseBoard(b))
  const key = JSON.stringify(norm)
  const last = sets[sets.length - 1]
  if (last && last.key === key) { last.until = c; continue }
  const seen = sets.find((s) => s.key === key)
  if (seen) { seen.again = [...(seen.again ?? []), c.hash]; continue }
  sets.push({ key, from: c, until: c, norm })
}

if (process.argv.includes('--ts')) {
  // One entry per distinct board, with the commit that first shipped it.
  const boards = []
  for (const s of sets) for (const b of s.norm) {
    const k = JSON.stringify(b)
    if (!boards.some((x) => x.k === k)) boards.push({ k, b, from: s.from })
  }
  const tile = (t) => `T('${t.defId}', ${t.x}, ${t.y}, ${t.w}, ${t.h}${Object.keys(t.config).length ? `, ${JSON.stringify(t.config).replace(/"([a-zA-Z]+)":/g, '$1: ').replace(/"/g, "'").replace(/,/g, ', ')}` : ''})`
  for (const { b, from } of boards) {
    console.log(`  // ${b.label}, first shipped in ${from.hash} (${from.date})`)
    console.log(`  { label: '${b.label}', tiles: [\n    ${b.tiles.map(tile).join(',\n    ')},\n  ] },`)
  }
} else if (process.argv.includes('--json')) {
  console.log(JSON.stringify(sets.map((s) => ({ from: s.from.hash, until: s.until.hash, boards: s.norm })), null, 1))
} else {
  for (const s of sets) {
    console.log(`${s.from.hash} (${s.from.date}) .. ${s.until.hash} (${s.until.date})${s.again ? ` again at ${s.again.join(', ')}` : ''}`)
    for (const b of s.norm) console.log(`  ${b.label}: ${b.tiles.map((t) => `${t.defId}@${t.x},${t.y},${t.w}x${t.h}${Object.keys(t.config).length ? JSON.stringify(t.config) : ''}`).join(' ')}`)
  }
}
