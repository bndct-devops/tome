// The one tooltip of the Stats page (see tip.ts for how marks declare theirs).
// Mount once: <TipLayer /> (the Stats frame does). It renders a single element
// in a portal on <body> and never re-renders: listeners on the document find
// the mark under the pointer (or the focused chart's current mark), write the
// text and place the tip above the mark, below it when there is no room, kept
// inside the window. Look: the app's popover (InfoHint, the old ChartTooltip).
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { GUIDE, MARK, setTipLayer, tipFor, type Tip, type TipRect } from './tip'
import { DESKTOP_MIN } from './tokens'
import { EASE, reducedMotion } from './motion'

const GAP = 8
// The marks' chart: its gaps keep the tip.
const CHART = '[data-tips], .k-bars'
const EDGE = 8

export function TipLayer() {
  const box = useRef<HTMLDivElement>(null)
  const live = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const tip = box.current!
    const say = live.current!
    const [titleEl, valueEl, noteEl] = Array.from(tip.children) as HTMLElement[]
    let hot: Element | null = null
    let down: { x: number; y: number; t: number } | null = null
    // The Guide that owns the tip now, told when it goes.
    let owner: (() => void) | null = null
    const release = () => { const o = owner; owner = null; o?.() }

    const hide = () => {
      if (hot) hot.removeAttribute('data-hot')
      hot = null
      release()
      tip.style.visibility = 'hidden'
    }
    // A horizontal row (a bar row, a track): the tip goes beside the row's content,
    // to the right of the bar's end and its value label (and those of the rows it
    // spans), so it covers neither that row nor its neighbours; with no room there,
    // left of the whole set; for a track (its label sits at its end) and when neither
    // side has room, below the row at the bar's end, above when the window ends.
    // Null for anything else (columns, cells, covers: above the mark).
    const rowPlace = (el: Element, w: number, h: number, vw: number, vh: number) => {
      const track = el.classList.contains('k-track')
      const group = track ? el : el.closest('.k-col') ?? el
      const g = group.getBoundingClientRect()
      if (!(g.width > g.height * 3 && g.height <= 48)) return null
      // Where a row's content (bar, value label) ends; the hit area and screen-reader text do not count.
      const endOf = (row: Element) => {
        let e = -Infinity
        for (const n of Array.from(row.querySelectorAll('*'))) {
          if (n.childElementCount || n.classList.contains('sr-only') || n.classList.contains('k-hit')) continue
          const b = n.getBoundingClientRect()
          if (b.width >= 1 && b.height >= 1) e = Math.max(e, b.right)
        }
        return e
      }
      let end = endOf(group)
      if (!Number.isFinite(end)) end = g.left + g.width / 2
      const clampX = (x: number) => Math.min(Math.max(EDGE, x), vw - w - EDGE)
      if (!track) {
        // The tip is taller than a row: it also clears the content of the rows it spans.
        // Tried centred on the row, then hanging down from it, then standing up on it.
        const set = group.closest('.k-bars')
        const rows = set ? Array.from(set.querySelectorAll(MARK)).map((m) => m.closest('.k-col') ?? m) : []
        const fitY = (y: number) => Math.min(Math.max(EDGE, y), vh - h - EDGE)
        for (const y of [fitY(g.top + g.height / 2 - h / 2), fitY(g.top), fitY(g.bottom - h)]) {
          let x = end
          for (const row of rows) {
            const b = row.getBoundingClientRect()
            if (row !== group && b.bottom > y && b.top < y + h) x = Math.max(x, endOf(row))
          }
          if (x + 12 + w <= vw - EDGE) return { x: x + 12, y }
        }
        // No room on the right (a set at the window's edge): left of the whole set.
        const left = (set ?? group).getBoundingClientRect().left - 12 - w
        if (left >= EDGE) return { x: left, y: fitY(g.top + g.height / 2 - h / 2) }
      }
      if (vw < DESKTOP_MIN) {
        // Narrow window: the row spans the width, so the tip at the bar's end would
        // lie over the next rows' titles and bars. It goes above the whole row (its
        // title too), below it when the window ends above, never over the row itself.
        const set = el.closest('.k-bars')
        let row: Element = group
        while (set && row.parentElement && row.parentElement !== set && set.contains(row.parentElement)) row = row.parentElement
        const r = (set && set.contains(row) && row !== set ? row : group).getBoundingClientRect()
        let y = r.top - h - GAP
        if (y < EDGE) y = Math.min(r.bottom + GAP, vh - h - EDGE)
        return { x: clampX(end - 16), y }
      }
      let y = g.bottom + GAP
      if (y + h > vh - EDGE) y = Math.max(EDGE, g.top - h - GAP)
      return { x: clampX(end - 16), y }
    }
    const place = (t: Tip, r: TipRect, announce: boolean, el?: Element) => {
      titleEl.textContent = t.title
      valueEl.textContent = t.value ?? ''
      noteEl.textContent = t.note ?? ''
      valueEl.style.display = t.value ? '' : 'none'
      noteEl.style.display = t.note ? '' : 'none'
      const w = tip.offsetWidth, h = tip.offsetHeight
      const vw = document.documentElement.clientWidth, vh = window.innerHeight
      const row = el && !t.anchor ? rowPlace(el, w, h, vw, vh) : null
      let y = r.top - h - GAP
      if (y < EDGE) y = Math.min(r.bottom + GAP, vh - h - EDGE)
      let x = Math.min(Math.max(EDGE, (r.left + r.right) / 2 - w / 2), vw - w - EDGE)
      if (row) { x = row.x; y = row.y }
      tip.style.left = `${Math.round(x)}px`
      tip.style.top = `${Math.round(y)}px`
      // Shown from hidden: a short fade and grow (as the app's popovers). Between marks it
      // only moves: the one element stays, so nothing flickers.
      if (tip.style.visibility !== 'visible' && !reducedMotion()) {
        tip.animate([{ opacity: 0, transform: 'scale(0.96)' }, { opacity: 1, transform: 'none' }], { duration: 120, easing: EASE })
      }
      tip.style.visibility = 'visible'
      if (announce) say.textContent = [t.title, t.value, t.note].filter(Boolean).join(', ')
    }
    const show = (el: Element, announce = false) => {
      if (el === hot) return
      const t = tipFor(el)
      if (!t) return hide()
      if (hot) hot.removeAttribute('data-hot')
      release()
      hot = el
      el.setAttribute('data-hot', '')
      place(t, (t.anchor ?? el).getBoundingClientRect(), announce, el)
    }
    setTipLayer({
      showAt: (t, r, onHide, announce = false) => {
        if (hot) { hot.removeAttribute('data-hot'); hot = null }
        if (owner !== onHide) release()
        owner = onHide
        place(t, r, announce)
      },
      hide,
    })
    const markAt = (target: EventTarget | null) => (target instanceof Element ? target.closest(MARK) : null)

    const onOver = (e: PointerEvent) => {
      if (e.pointerType === 'touch') return
      const m = markAt(e.target)
      if (m) show(m)
      else if (owner && e.target instanceof Element && e.target.closest(GUIDE)) return
      // The gaps between a chart's marks (heat cells, bar rows) keep the tip: it goes
      // when the pointer leaves the chart, not at every 2 px gap (no flicker).
      else if (hot && e.target instanceof Node && hot.closest(CHART)?.contains(e.target)) return
      else if (hot || owner) {
        // A mark reached with the keyboard stays until focus leaves its chart.
        const f = document.activeElement
        if (!(f && f !== document.body && f.matches(':focus-visible') && hot && f.contains(hot))) hide()
      }
    }
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY, t: e.timeStamp } }
    const onUp = (e: PointerEvent) => {
      if (e.pointerType === 'mouse' || !down) return
      const tap = Math.hypot(e.clientX - down.x, e.clientY - down.y) < 10 && e.timeStamp - down.t < 600
      down = null
      if (!tap) return
      if (e.target instanceof Element && e.target.closest(GUIDE)) return // the Guide handles its own taps
      const m = markAt(e.target)
      if (m && m !== hot) show(m)
      else hide()
    }
    const onFocus = (e: FocusEvent) => {
      const el = e.target as Element
      if (!(el instanceof Element) || !el.matches(':focus-visible')) return
      if (el.matches(MARK)) show(el, true)
      else if (el.hasAttribute('data-tips')) {
        const first = hot && el.contains(hot) ? hot : el.querySelector(MARK)
        if (first) { hot = hot === first ? null : hot; show(first, true) }
      }
    }
    // Only focus leaving the tip's own mark or chart hides it. A tap's compatibility
    // mousedown moves focus off whatever was tapped before (a tab, a range button)
    // after the tap has opened a tip, and that blur is not about the tip. A Guide
    // hides its own tip on its svg's blur.
    const onBlur = (e: FocusEvent) => {
      if (hot && e.target instanceof Node && e.target.contains(hot)) hide()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { hide(); return }
      const host = document.activeElement
      if (!host || !host.hasAttribute('data-tips')) return
      const [x, y] = (host.getAttribute('data-tips-nav') ?? '1,0').split(',').map(Number)
      const step = { ArrowRight: x, ArrowLeft: -x, ArrowDown: y, ArrowUp: -y } as Record<string, number>
      const marks = Array.from(host.querySelectorAll(MARK))
      if (!marks.length) return
      const at = hot ? marks.indexOf(hot) : -1
      let next: number
      if (e.key === 'Home') next = 0
      else if (e.key === 'End') next = marks.length - 1
      else if (step[e.key]) next = at < 0 ? 0 : Math.min(marks.length - 1, Math.max(0, at + step[e.key]))
      else return
      e.preventDefault()
      show(marks[next], true)
    }
    const onScroll = () => { if (hot || owner) hide() }

    document.addEventListener('pointerover', onOver, { passive: true })
    document.addEventListener('pointerdown', onDown, { passive: true })
    document.addEventListener('pointerup', onUp, { passive: true })
    document.addEventListener('focusin', onFocus)
    document.addEventListener('focusout', onBlur)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, { capture: true, passive: true })
    window.addEventListener('blur', hide)
    // A resize moves the marks under a tip placed for the old layout.
    window.addEventListener('resize', hide)
    // The pointer left the window (no pointerover follows): nothing lingers.
    const onLeaveDoc = (e: PointerEvent) => { if (e.pointerType !== 'touch') hide() }
    document.documentElement.addEventListener('pointerleave', onLeaveDoc)
    return () => {
      document.documentElement.removeEventListener('pointerleave', onLeaveDoc)
      hide()
      setTipLayer(null)
      document.removeEventListener('pointerover', onOver)
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('pointerup', onUp)
      document.removeEventListener('focusin', onFocus)
      document.removeEventListener('focusout', onBlur)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, { capture: true })
      window.removeEventListener('blur', hide)
      window.removeEventListener('resize', hide)
    }
  }, [])

  return createPortal(
    <>
      <div
        ref={box}
        role="tooltip"
        className="pointer-events-none fixed left-0 top-0 z-50 max-w-[280px] rounded-lg bg-card px-3 py-2 text-[13px] leading-snug shadow-lg ring-1 ring-panel-ring"
        style={{ visibility: 'hidden' }}
      >
        <div className="font-medium text-foreground" />
        <div className="tabular-nums text-foreground" />
        <div className="text-muted-foreground" />
      </div>
      <div ref={live} className="sr-only" aria-live="polite" />
    </>,
    document.body,
  )
}
