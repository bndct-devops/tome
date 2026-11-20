// A vertical guide for time-axis charts: the whole plot is the hit target, a line
// follows the pointer and snaps to the nearest slot (a day, a rating date), the
// slot's dots light up (data-hot on the elements with its data-dot keys), a ring
// marks the slot's point on the chart's line, and the page's one tip (TipLayer)
// shows the slot's text above the plot.
//   <svg {...guideHost} …>   (guideHost from tip.ts)  …dots with data-dot={key}…
//     <Guide x0={…} x1={…} y0={…} y1={…} slots={slots} onOpen={…} />  (last child, on top)
// Nothing here renders on a pointer move: the line, the ring and the hot dots are
// written straight to the DOM. Keyboard: the svg is one focus stop, Left/Right step
// slot by slot, Home/End, Enter opens. Touch: a tap or a sideways drag moves the
// guide; vertical drags scroll the page (touch-action: pan-y), never held.
import { useEffect, useLayoutEffect, useRef } from 'react'
import { tipLayer, type Tip } from './tip'

export type GuideSlot = {
  /** x of the slot in the svg's own coordinates. */
  x: number
  tip: () => Tip
  /** data-dot keys of the marks that belong to the slot. */
  dots?: string[]
  /** y of the slot's point on the chart's line, if it has one. */
  lineY?: number | null
}

export function Guide({ x0, x1, y0, y1, slots, onOpen, label }: {
  x0: number; x1: number; y0: number; y1: number
  slots: GuideSlot[]
  /** Enter, a click, or a second tap on the same slot. */
  onOpen?: (i: number) => void
  label?: string
}) {
  const hit = useRef<SVGRectElement>(null)
  const line = useRef<SVGLineElement>(null)
  const ring = useRef<SVGCircleElement>(null)
  const live = useRef({ slots, onOpen })
  useLayoutEffect(() => { live.current = { slots, onOpen } })

  useEffect(() => {
    const rect = hit.current!, ln = line.current!, rg = ring.current!
    const svg = rect.ownerSVGElement!
    let at = -1
    let lit: Element[] = []
    let touch: { id: number; at: number; was: number } | null = null

    const clear = () => {
      at = -1
      ln.style.visibility = 'hidden'
      rg.style.visibility = 'hidden'
      for (const el of lit) el.removeAttribute('data-hot')
      lit = []
    }
    const set = (i: number, announce = false) => {
      const ss = live.current.slots
      if (!ss.length) return
      i = Math.max(0, Math.min(ss.length - 1, i))
      const s = ss[i]
      if (i !== at) {
        at = i
        ln.setAttribute('transform', `translate(${s.x.toFixed(1)},0)`)
        ln.style.visibility = 'visible'
        if (s.lineY != null) { rg.setAttribute('transform', `translate(${s.x.toFixed(1)},${s.lineY.toFixed(1)})`); rg.style.visibility = 'visible' }
        else rg.style.visibility = 'hidden'
        for (const el of lit) el.removeAttribute('data-hot')
        lit = (s.dots ?? []).flatMap((k) => Array.from(svg.querySelectorAll(`[data-dot="${k}"]`)))
        for (const el of lit) el.setAttribute('data-hot', '')
      }
      const b = svg.getBoundingClientRect()
      const x = b.left + s.x, top = b.top + y0
      tipLayer.showAt(s.tip(), { left: x, right: x, top, bottom: top }, clear, announce)
    }
    const nearest = (clientX: number) => {
      const ss = live.current.slots
      const x = clientX - svg.getBoundingClientRect().left
      let lo = 0, hi = ss.length - 1
      while (lo < hi) { const m = (lo + hi) >> 1; if (ss[m].x < x) lo = m + 1; else hi = m }
      return lo > 0 && x - ss[lo - 1].x < ss[lo].x - x ? lo - 1 : lo
    }
    const off = () => { if (at >= 0) tipLayer.hide(); clear() }

    const onMove = (e: PointerEvent) => {
      if (e.pointerType === 'touch') { if (touch && touch.id === e.pointerId) { touch.at = nearest(e.clientX); set(touch.at) } return }
      set(nearest(e.clientX))
    }
    const onLeave = (e: PointerEvent) => { if (e.pointerType !== 'touch') off() }
    const onDown = (e: PointerEvent) => {
      if (e.pointerType !== 'touch') return
      touch = { id: e.pointerId, at: nearest(e.clientX), was: at }
      set(touch.at)
    }
    const onUp = (e: PointerEvent) => {
      if (e.pointerType !== 'touch' || !touch) return
      // A second tap on the slot that was already showing opens it.
      if (touch.was === touch.at) live.current.onOpen?.(touch.at)
      touch = null
    }
    const onCancel = () => { touch = null; off() } // the page took the drag as a scroll
    const onClick = (e: MouseEvent) => {
      if ((e as PointerEvent).pointerType === 'touch') return
      if (at >= 0) live.current.onOpen?.(at)
    }
    const onKey = (e: KeyboardEvent) => {
      const n = live.current.slots.length
      if (!n) return
      let next: number
      if (e.key === 'ArrowRight') next = at < 0 ? n - 1 : at + 1
      else if (e.key === 'ArrowLeft') next = at < 0 ? n - 1 : at - 1
      else if (e.key === 'Home') next = 0
      else if (e.key === 'End') next = n - 1
      else if (e.key === 'Enter') { if (at >= 0) live.current.onOpen?.(at); return }
      else if (e.key === 'Escape') { off(); return }
      else return
      e.preventDefault()
      set(next, true)
    }
    const onFocus = () => { if (svg.matches(':focus-visible')) set(at >= 0 ? at : live.current.slots.length - 1, true) }
    const onBlur = () => off()

    rect.addEventListener('pointermove', onMove, { passive: true })
    rect.addEventListener('pointerleave', onLeave, { passive: true })
    rect.addEventListener('pointerdown', onDown, { passive: true })
    rect.addEventListener('pointerup', onUp, { passive: true })
    rect.addEventListener('pointercancel', onCancel, { passive: true })
    rect.addEventListener('click', onClick)
    svg.addEventListener('keydown', onKey)
    svg.addEventListener('focus', onFocus)
    svg.addEventListener('blur', onBlur)
    return () => {
      off()
      rect.removeEventListener('pointermove', onMove)
      rect.removeEventListener('pointerleave', onLeave)
      rect.removeEventListener('pointerdown', onDown)
      rect.removeEventListener('pointerup', onUp)
      rect.removeEventListener('pointercancel', onCancel)
      rect.removeEventListener('click', onClick)
      svg.removeEventListener('keydown', onKey)
      svg.removeEventListener('focus', onFocus)
      svg.removeEventListener('blur', onBlur)
    }
  }, [y0])

  return (
    <g data-guide="" aria-label={label}>
      <line ref={line} className="k-guide" x1={0} x2={0} y1={y0} y2={y1} style={{ visibility: 'hidden' }} />
      <circle ref={ring} className="k-guide-ring" cx={0} cy={0} r={4} style={{ visibility: 'hidden' }} />
      <rect ref={hit} className="k-guide-hit" x={x0} y={y0} width={Math.max(0, x1 - x0)} height={Math.max(0, y1 - y0)} />
    </g>
  )
}
