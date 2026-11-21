// Text width in the app's font, for card lines that must stay on one line
// (goalsModel.fitClauses). One canvas for the page; widths are read again once
// the web fonts are in (useFontsReady re-renders the cards then).
import { useEffect, useState } from 'react'

let ctx: CanvasRenderingContext2D | null = null
let family = ''

export function textWidth(s: string, px: number, weight = 400): number {
  if (typeof document === 'undefined') return s.length * px * 0.55
  if (!ctx) ctx = document.createElement('canvas').getContext('2d')
  if (!family) family = getComputedStyle(document.body).fontFamily
  if (!ctx) return s.length * px * 0.55
  ctx.font = `${weight} ${px}px ${family}`
  return ctx.measureText(s).width
}

let ready = typeof document !== 'undefined' && document.fonts?.status === 'loaded'
/** False until the web fonts are loaded (a re-render then measures with them). */
export function useFontsReady(): boolean {
  const [r, setR] = useState(ready)
  useEffect(() => {
    if (r || typeof document === 'undefined' || !document.fonts) return
    let live = true
    document.fonts.ready.then(() => { ready = true; family = ''; if (live) setR(true) })
    return () => { live = false }
  }, [r])
  return r
}
