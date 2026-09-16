import { useEffect, useRef, useState } from 'react'
import rough from 'roughjs'

const FONT_URL = 'https://fonts.googleapis.com/css2?family=Caveat:wght@600&display=swap'
const FONT_FAMILY = "'Caveat', cursive"

export function SyncFlowDiagram() {
  const svgRef = useRef<SVGSVGElement>(null)
  const [ready, setReady] = useState(false)

  // Preload font before rendering anything
  useEffect(() => {
    const link = document.createElement('link')
    link.rel = 'stylesheet'
    link.href = FONT_URL
    document.head.appendChild(link)

    const check = () => {
      document.fonts.load('600 24px Caveat').then(fonts => {
        if (fonts.length > 0) {
          setReady(true)
        } else {
          setTimeout(check, 100)
        }
      })
    }
    check()
  }, [])

  useEffect(() => {
    const svg = svgRef.current
    if (!svg || !ready) return

    while (svg.lastChild) svg.removeChild(svg.lastChild)

    const rc = rough.svg(svg)
    const base = { stroke: '#d4d4d4', strokeWidth: 1.3, roughness: 0.7, bowing: 0.5 }
    const dashed = { ...base, strokeLineDash: [9, 6], strokeWidth: 1.5 }

    //  viewBox: 760 x 130
    //
    //  [KOReader+TomeSync]  ──╮                        ╭──→ [Dashboard]
    //                         ├─→ ┊Tome Server┊ ──────╯
    //  [Web reader]         ──╯

    // Source boxes (left)
    const sX = 20, sW = 185, sH = 32, sR = 10
    const koY = 20
    const webY = 78

    // Server box (center, dashed) — same height as the span of sources
    const svX = 330, svW = 130, svH = 70, svY = 22, svR = 6

    // Dashboard box (right)
    const dX = 590, dW = 145, dH = 38, dY = 42, dR = 10

    // Draw boxes with rounded corners
    const rrect = (x: number, y: number, w: number, h: number, r: number, o: object) => {
      const d = [
        `M${x + r},${y}`, `L${x + w - r},${y}`,
        `Q${x + w},${y},${x + w},${y + r}`, `L${x + w},${y + h - r}`,
        `Q${x + w},${y + h},${x + w - r},${y + h}`, `L${x + r},${y + h}`,
        `Q${x},${y + h},${x},${y + h - r}`, `L${x},${y + r}`,
        `Q${x},${y},${x + r},${y}`, 'Z'
      ].join(' ')
      return rc.path(d, o)
    }

    svg.appendChild(rrect(sX, koY, sW, sH, sR, base))
    svg.appendChild(rrect(sX, webY, sW, sH, sR, base))
    svg.appendChild(rrect(svX, svY, svW, svH, svR, dashed))
    svg.appendChild(rrect(dX, dY, dW, dH, dR, base))

    // Arrows — KOReader → Server top entry
    const a1s = { x: sX + sW + 2, y: koY + sH / 2 }
    const a1e = { x: svX - 2, y: svY + svH * 0.32 }
    const a1c = { x: (a1s.x + a1e.x) / 2, y: (a1s.y + a1e.y) / 2 - 8 }
    svg.appendChild(rc.curve([[a1s.x, a1s.y], [a1c.x, a1c.y], [a1e.x, a1e.y]], base))
    arrow(svg, a1c.x, a1c.y, a1e.x, a1e.y)

    // Arrows — Web reader → Server bottom entry
    const a2s = { x: sX + sW + 2, y: webY + sH / 2 }
    const a2e = { x: svX - 2, y: svY + svH * 0.68 }
    const a2c = { x: (a2s.x + a2e.x) / 2, y: (a2s.y + a2e.y) / 2 + 8 }
    svg.appendChild(rc.curve([[a2s.x, a2s.y], [a2c.x, a2c.y], [a2e.x, a2e.y]], base))
    arrow(svg, a2c.x, a2c.y, a2e.x, a2e.y)

    // Arrow — Server → Dashboard (straight)
    const a3s = { x: svX + svW + 2, y: svY + svH / 2 }
    const a3e = { x: dX - 2, y: dY + dH / 2 }
    svg.appendChild(rc.line(a3s.x, a3s.y, a3e.x, a3e.y, base))
    arrow(svg, a3s.x, a3s.y, a3e.x, a3e.y)

    // Labels
    txt(svg, sX + sW / 2, koY + sH / 2 + 2, 'KOReader + TomeSync', 14.5)
    txt(svg, sX + sW / 2, webY + sH / 2 + 2, 'Web reader', 14.5)
    txt(svg, svX + svW / 2, svY + svH / 2 - 7, 'Tome', 22)
    txt(svg, svX + svW / 2, svY + svH / 2 + 15, 'Server', 22)
    txt(svg, dX + dW / 2, dY + dH / 2 + 2, 'Dashboard', 16)

    // Edge labels — clearly above the arrows
    txt(svg, (a1s.x + a1e.x) / 2, Math.min(a1s.y, a1e.y) - 14, 'sessions and positions', 12)
    txt(svg, (a3s.x + a3e.x) / 2, a3s.y - 14, 'stats, pages, libraries, etc', 12)

  }, [ready])

  return (
    <svg
      ref={svgRef}
      viewBox="0 0 760 130"
      style={{ width: '100%', height: 'auto', display: 'block' }}
    />
  )
}

function arrow(svg: SVGSVGElement, fx: number, fy: number, tx: number, ty: number) {
  const a = Math.atan2(ty - fy, tx - fx)
  const l = 8, s = 0.4
  const d = `M${tx - l * Math.cos(a - s)},${ty - l * Math.sin(a - s)} L${tx},${ty} L${tx - l * Math.cos(a + s)},${ty - l * Math.sin(a + s)}`
  const el = document.createElementNS('http://www.w3.org/2000/svg', 'path')
  el.setAttribute('d', d)
  el.setAttribute('stroke', '#d4d4d4')
  el.setAttribute('stroke-width', '1.3')
  el.setAttribute('fill', 'none')
  el.setAttribute('stroke-linecap', 'round')
  el.setAttribute('stroke-linejoin', 'round')
  svg.appendChild(el)
}

function txt(svg: SVGSVGElement, x: number, y: number, t: string, sz: number) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', 'text')
  el.setAttribute('x', String(x))
  el.setAttribute('y', String(y))
  el.setAttribute('text-anchor', 'middle')
  el.setAttribute('dominant-baseline', 'central')
  el.setAttribute('fill', '#d4d4d4')
  el.setAttribute('font-family', FONT_FAMILY)
  el.setAttribute('font-size', String(sz))
  el.setAttribute('font-weight', '600')
  el.textContent = t
  svg.appendChild(el)
}
