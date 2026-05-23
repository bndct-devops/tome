// Generate hand-drawn-style SVG diagrams using roughjs (the same engine
// Excalidraw uses under the hood). Output goes to public/diagrams/{name}.svg.
//
// Diagrams use stroke="currentColor" so the docs site's theme controls colour
// — light, dark, and amber all just work.
//
// Usage:
//   node scripts/generate-diagrams.mjs
import roughPkg from 'roughjs/bundled/rough.cjs.js'
// roughjs bundled exports: { canvas, svg, generator, newSeed }
// `generator(config?)` returns a RoughGenerator instance (no DOM needed).
const roughGen = roughPkg.generator
import { writeFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'

const __dir = path.dirname(new URL(import.meta.url).pathname)
const OUT = path.resolve(__dir, '../public/diagrams')
mkdirSync(OUT, { recursive: true })

// ── tiny deterministic PRNG so jitter is stable across runs ──────────────────
function rng(seed) {
  let s = seed | 0
  return () => {
    s = (s * 1664525 + 1013904223) | 0
    return ((s >>> 0) / 4294967296)
  }
}

// ── helpers ──────────────────────────────────────────────────────────────────
const rough = roughGen()

function roughOpts({ accent = false, dashed = false, seed = 1 } = {}) {
  return {
    stroke: accent ? 'var(--accent-stroke)' : 'currentColor',
    strokeWidth: 1.6,
    roughness: 1.6,        // "cartoonist" — properly sketchy
    bowing: 1.2,
    fillStyle: 'solid',
    fill: accent ? 'var(--accent-stroke)' : 'none',
    seed,
    strokeLineDash: dashed ? [6, 4] : undefined,
  }
}

function drawableToSvg(drawable) {
  // RoughGenerator returns a drawable spec; we need to turn it into SVG path elements.
  // Each "op" in drawable.sets is either a path or fill operation.
  const parts = []
  for (const set of drawable.sets) {
    let d = ''
    for (const op of set.ops) {
      if (op.op === 'move') d += `M${op.data[0].toFixed(2)},${op.data[1].toFixed(2)} `
      else if (op.op === 'bcurveTo') d += `C${op.data.map(v => v.toFixed(2)).join(',')} `
      else if (op.op === 'lineTo') d += `L${op.data[0].toFixed(2)},${op.data[1].toFixed(2)} `
    }
    if (!d) continue
    if (set.type === 'path') {
      parts.push(`<path d="${d.trim()}" fill="none" stroke="${drawable.options.stroke}" stroke-width="${drawable.options.strokeWidth}" stroke-linecap="round" stroke-linejoin="round" />`)
    } else if (set.type === 'fillPath') {
      parts.push(`<path d="${d.trim()}" fill="${drawable.options.fill ?? drawable.options.stroke}" stroke="none" />`)
    } else if (set.type === 'fillSketch') {
      parts.push(`<path d="${d.trim()}" fill="none" stroke="${drawable.options.fill ?? drawable.options.stroke}" stroke-width="${(drawable.options.strokeWidth ?? 1) * 0.6}" stroke-linecap="round" />`)
    }
  }
  return parts.join('\n  ')
}

// ── shape builders ───────────────────────────────────────────────────────────
function roundedRectPath(x, y, w, h, r) {
  // SVG path for a rect with all four corners rounded at radius r.
  // Excalidraw-style: chunky radius (~14px) so corners read clearly.
  return `M${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} L${x + r},${y + h} Q${x},${y + h} ${x},${y + h - r} L${x},${y + r} Q${x},${y} ${x + r},${y} Z`
}

function box({ x, y, w, h, jitter = 0, seed, radius = 14 }) {
  const r = rng(seed * 7919)
  const jx = (r() - 0.5) * jitter
  const jy = (r() - 0.5) * jitter
  const jw = (r() - 0.5) * jitter
  const jh = (r() - 0.5) * jitter
  const d = roundedRectPath(x + jx, y + jy, w + jw, h + jh, radius)
  return drawableToSvg(rough.path(d, roughOpts({ seed })))
}

function diamond({ cx, cy, w, h, seed }) {
  const r = rng(seed * 7919)
  const j = (n) => n + (r() - 0.5) * 4
  const pts = [
    [j(cx), j(cy - h / 2)],
    [j(cx + w / 2), j(cy)],
    [j(cx), j(cy + h / 2)],
    [j(cx - w / 2), j(cy)],
  ]
  return drawableToSvg(rough.polygon(pts, roughOpts({ seed })))
}

function accentBox({ x, y, w, h, jitter = 0, seed, radius = 14 }) {
  const r = rng(seed * 7919)
  const jx = (r() - 0.5) * jitter
  const jy = (r() - 0.5) * jitter
  const opts = roughOpts({ accent: true, seed })
  const d = roundedRectPath(x + jx, y + jy, w, h, radius)
  return drawableToSvg(rough.path(d, { ...opts, fill: 'var(--accent-fill)', fillStyle: 'solid', stroke: 'var(--accent-stroke)' }))
}

// Trim a line's endpoints back from the boxes so arrowheads don't sit ON
// the box edge. Returns the inset start/end coords plus the unit vector.
function trimEnds(x1, y1, x2, y2, startTrim = 10, endTrim = 12) {
  const dx = x2 - x1, dy = y2 - y1
  const dist = Math.sqrt(dx * dx + dy * dy) || 1
  const ux = dx / dist, uy = dy / dist
  return {
    sx: x1 + ux * startTrim, sy: y1 + uy * startTrim,
    tx: x2 - ux * endTrim,   ty: y2 - uy * endTrim,
    ux, uy,
  }
}

function labelOnLine({ sx, sy, tx, ty, mx, my, label, offset = 22, side = 'above' }) {
  if (!label) return ''
  // Pick the perpendicular side that's visually `above` (smaller y) — works
  // regardless of which direction the arrow runs in screen space.
  const lineAngle = Math.atan2(ty - sy, tx - sx)
  const perpAngle = lineAngle - Math.PI / 2
  let pdx = Math.cos(perpAngle)
  let pdy = Math.sin(perpAngle)
  if ((side === 'above' && pdy > 0) || (side === 'below' && pdy < 0)) {
    pdx = -pdx
    pdy = -pdy
  }
  const lx = mx + pdx * offset
  const ly = my + pdy * offset
  return `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" font-family="var(--diagram-font, 'Caveat', 'Comic Sans MS', cursive)" font-size="17" fill="currentColor" text-anchor="middle" dominant-baseline="middle" opacity="0.78">${label}</text>`
}

function arrowHead(tipX, tipY, mx, my, seed) {
  const angle = Math.atan2(tipY - my, tipX - mx)
  const ah = 10, aw = 7
  const baseX = tipX - Math.cos(angle) * ah
  const baseY = tipY - Math.sin(angle) * ah
  const leftX = baseX + Math.cos(angle + Math.PI / 2) * (aw / 2)
  const leftY = baseY + Math.sin(angle + Math.PI / 2) * (aw / 2)
  const rightX = baseX - Math.cos(angle + Math.PI / 2) * (aw / 2)
  const rightY = baseY - Math.sin(angle + Math.PI / 2) * (aw / 2)
  return drawableToSvg(rough.polygon([[tipX, tipY], [leftX, leftY], [rightX, rightY]], { ...roughOpts({ seed }), fill: 'currentColor', fillStyle: 'solid' }))
}

function arrow({ x1, y1, x2, y2, seed, dashed = false, label, labelSide = 'above', labelOffset = 22 }) {
  const { sx, sy, tx, ty } = trimEnds(x1, y1, x2, y2)
  const r = rng(seed * 31)
  const mx = (sx + tx) / 2 + (r() - 0.5) * 6
  const my = (sy + ty) / 2 + (r() - 0.5) * 6
  const line = drawableToSvg(rough.curve([[sx, sy], [mx, my], [tx, ty]], roughOpts({ seed, dashed })))
  const head = arrowHead(tx, ty, mx, my, seed)
  const labelSvg = labelOnLine({ sx, sy, tx, ty, mx, my, label, offset: labelOffset, side: labelSide })
  return line + '\n  ' + head + '\n  ' + labelSvg
}

function bidiArrow({ x1, y1, x2, y2, seed, label, labelSide = 'above', labelOffset = 22 }) {
  const { sx, sy, tx, ty } = trimEnds(x1, y1, x2, y2)
  const r = rng(seed * 31)
  const mx = (sx + tx) / 2 + (r() - 0.5) * 5
  const my = (sy + ty) / 2 + (r() - 0.5) * 5
  const line = drawableToSvg(rough.curve([[sx, sy], [mx, my], [tx, ty]], roughOpts({ seed })))
  const head1 = arrowHead(tx, ty, mx, my, seed)
  const head2 = arrowHead(sx, sy, mx, my, seed + 1)
  const labelSvg = labelOnLine({ sx, sy, tx, ty, mx, my, label, offset: labelOffset, side: labelSide })
  return line + '\n  ' + head1 + '\n  ' + head2 + '\n  ' + labelSvg
}

function text({ x, y, content, size = 20, anchor = 'middle', accent = false, bold = false }) {
  const lines = content.split('\n')
  const lineHeight = size * 1.15
  return lines.map((ln, i) => {
    const yy = y + (i - (lines.length - 1) / 2) * lineHeight
    return `<text x="${x}" y="${yy.toFixed(1)}" font-family="var(--diagram-font, 'Caveat', 'Comic Sans MS', cursive)" font-size="${size}" font-weight="${bold ? '600' : '400'}" fill="${accent ? 'var(--accent-stroke)' : 'currentColor'}" text-anchor="${anchor}" dominant-baseline="middle">${ln}</text>`
  }).join('\n  ')
}

function wrapSvg({ width, height, body, padding = 16 }) {
  // The CSS vars below let the docs site control all colours via --accent.
  // Light/dark/amber inherit currentColor for strokes; the accent vars get
  // theme-specific values defined in global.css.
  return `<svg
  xmlns="http://www.w3.org/2000/svg"
  viewBox="-${padding} -${padding} ${width + padding * 2} ${height + padding * 2}"
  width="100%"
  preserveAspectRatio="xMidYMid meet"
  style="color: currentColor; --accent-stroke: var(--diagram-accent, #863bff); --accent-fill: var(--diagram-accent-fill, rgba(134, 59, 255, 0.12));"
  role="img">
  ${body}
</svg>`
}

// ── DIAGRAM 1 — sync flow (koreader) ─────────────────────────────────────────
function syncFlowDiagram() {
  // Layout:
  //   [KOReader+TomeSync]  ↕  [ Tome server ]  →  [ Dashboard ]
  //   [Web reader]         ↕
  const W = 880, H = 320
  const parts = []
  // KOReader box (top-left)
  parts.push(box({ x: 20, y: 30, w: 240, h: 70, seed: 11, jitter: 3 }))
  parts.push(text({ x: 140, y: 65, content: 'KOReader + TomeSync', size: 21, bold: true }))
  // Web reader (bottom-left)
  parts.push(box({ x: 20, y: 220, w: 240, h: 70, seed: 12, jitter: 3 }))
  parts.push(text({ x: 140, y: 255, content: 'Web reader in browser', size: 21, bold: true }))
  // Tome server (middle, accent)
  parts.push(accentBox({ x: 380, y: 125, w: 220, h: 80, seed: 13, jitter: 2 }))
  parts.push(text({ x: 490, y: 165, content: 'Tome server', size: 23, bold: true, accent: true }))
  // Dashboard (right)
  parts.push(box({ x: 720, y: 125, w: 140, h: 80, seed: 14, jitter: 3 }))
  parts.push(text({ x: 790, y: 165, content: 'Your\ndashboard', size: 19, bold: true }))
  // Connectors
  parts.push(bidiArrow({ x1: 260, y1: 65, x2: 380, y2: 145, seed: 21, label: 'sessions + position' }))
  parts.push(bidiArrow({ x1: 260, y1: 255, x2: 380, y2: 185, seed: 22, label: 'sessions + position' }))
  parts.push(arrow({ x1: 600, y1: 165, x2: 720, y2: 165, seed: 23, label: 'stats, series, library' }))
  return wrapSvg({ width: W, height: H, body: parts.join('\n  ') })
}

// ── DIAGRAM 2 — series detection ─────────────────────────────────────────────
function seriesDetectionDiagram() {
  const W = 900, H = 460
  const parts = []
  // Filename
  parts.push(box({ x: 30, y: 200, w: 160, h: 60, seed: 31, jitter: 3 }))
  parts.push(text({ x: 110, y: 230, content: 'filename.ext', size: 20, bold: true }))
  // Parser
  parts.push(box({ x: 270, y: 200, w: 130, h: 60, seed: 32, jitter: 2 }))
  parts.push(text({ x: 335, y: 230, content: 'Parser', size: 22, bold: true }))
  // Three outcomes — Series page (accent), No-series, Chapter grouping (accent)
  parts.push(accentBox({ x: 500, y: 70, w: 200, h: 60, seed: 33, jitter: 2 }))
  parts.push(text({ x: 600, y: 100, content: 'Series page', size: 21, bold: true, accent: true }))
  parts.push(box({ x: 500, y: 200, w: 200, h: 60, seed: 34, jitter: 3 }))
  parts.push(text({ x: 600, y: 230, content: 'No-series bucket', size: 19, bold: true }))
  parts.push(accentBox({ x: 500, y: 330, w: 200, h: 60, seed: 35, jitter: 2 }))
  parts.push(text({ x: 600, y: 360, content: 'Chapter grouping', size: 19, bold: true, accent: true }))
  // Arcs diamond
  parts.push(diamond({ cx: 800, cy: 100, w: 130, h: 80, seed: 36 }))
  parts.push(text({ x: 800, y: 100, content: 'Arcs\ndefined?', size: 17, bold: true }))
  // Arrows
  parts.push(arrow({ x1: 190, y1: 230, x2: 270, y2: 230, seed: 41 }))
  parts.push(arrow({ x1: 400, y1: 220, x2: 500, y2: 100, seed: 42, label: 'series + index' }))
  parts.push(arrow({ x1: 400, y1: 230, x2: 500, y2: 230, seed: 43, label: 'no series' }))
  parts.push(arrow({ x1: 400, y1: 240, x2: 500, y2: 360, seed: 44, label: 'chapter number' }))
  parts.push(arrow({ x1: 700, y1: 100, x2: 735, y2: 100, seed: 45 }))
  return wrapSvg({ width: W, height: H, body: parts.join('\n  ') })
}

// ── DIAGRAM 3 — visibility rules ─────────────────────────────────────────────
function visibilityDiagram() {
  const W = 880, H = 500
  const parts = []
  // Logged-in user (top)
  parts.push(box({ x: 350, y: 30, w: 200, h: 60, seed: 51, jitter: 3 }))
  parts.push(text({ x: 450, y: 60, content: 'Logged-in user', size: 21, bold: true }))
  // Role diamond
  parts.push(diamond({ cx: 450, cy: 175, w: 140, h: 80, seed: 52 }))
  parts.push(text({ x: 450, y: 175, content: 'Role?', size: 22, bold: true }))
  // Admin path (left, accent)
  parts.push(accentBox({ x: 30, y: 330, w: 220, h: 110, seed: 53, jitter: 2 }))
  parts.push(text({ x: 140, y: 385, content: 'Sees every book', size: 19, bold: true, accent: true }))
  // Member path (middle, accent)
  parts.push(accentBox({ x: 330, y: 330, w: 240, h: 130, seed: 54, jitter: 3 }))
  parts.push(text({ x: 450, y: 360, content: 'Books from admins', size: 16, bold: true, accent: true }))
  parts.push(text({ x: 450, y: 385, content: '+ books they uploaded', size: 16, bold: true, accent: true }))
  parts.push(text({ x: 450, y: 410, content: '+ books in assigned', size: 16, bold: true, accent: true }))
  parts.push(text({ x: 450, y: 432, content: 'libraries', size: 16, bold: true, accent: true }))
  // Guest path (right, accent)
  parts.push(accentBox({ x: 650, y: 330, w: 220, h: 110, seed: 55, jitter: 2 }))
  parts.push(text({ x: 760, y: 372, content: 'Books from admins', size: 16, bold: true, accent: true }))
  parts.push(text({ x: 760, y: 398, content: '+ books in public', size: 16, bold: true, accent: true }))
  parts.push(text({ x: 760, y: 422, content: 'libraries', size: 16, bold: true, accent: true }))
  // Arrows
  parts.push(arrow({ x1: 450, y1: 90, x2: 450, y2: 135, seed: 61 }))
  parts.push(arrow({ x1: 395, y1: 200, x2: 165, y2: 330, seed: 62, label: 'admin' }))
  parts.push(arrow({ x1: 450, y1: 215, x2: 450, y2: 330, seed: 63, label: 'member' }))
  parts.push(arrow({ x1: 505, y1: 200, x2: 740, y2: 330, seed: 64, label: 'guest' }))
  return wrapSvg({ width: W, height: H, body: parts.join('\n  ') })
}

// ── write ────────────────────────────────────────────────────────────────────
const diagrams = {
  'sync-flow': syncFlowDiagram(),
  'series-detection': seriesDetectionDiagram(),
  'visibility-rules': visibilityDiagram(),
}

for (const [name, svg] of Object.entries(diagrams)) {
  const file = path.join(OUT, `${name}.svg`)
  writeFileSync(file, svg)
  console.log(`  ✓ ${name}.svg`)
}
console.log(`Wrote ${Object.keys(diagrams).length} diagrams to ${OUT}`)
