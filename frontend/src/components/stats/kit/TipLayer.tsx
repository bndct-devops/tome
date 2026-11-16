// The one tooltip of the Stats page (see tip.ts for how marks declare theirs).
// Mount once: <TipLayer /> (the Stats frame does). It renders a single element
// in a portal on <body> and never re-renders: listeners on the document find
// the mark under the pointer (or the focused chart's current mark), write the
// text and place the tip above the mark, below it when there is no room, kept
// inside the window. Look: the app's popover (InfoHint, the old ChartTooltip).
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { MARK, tipFor } from './tip'

const GAP = 8
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

    const hide = () => {
      if (hot) hot.removeAttribute('data-hot')
      hot = null
      tip.style.visibility = 'hidden'
    }
    const show = (el: Element, announce = false) => {
      if (el === hot) return
      const t = tipFor(el)
      if (!t) return hide()
      if (hot) hot.removeAttribute('data-hot')
      hot = el
      el.setAttribute('data-hot', '')
      titleEl.textContent = t.title
      valueEl.textContent = t.value ?? ''
      noteEl.textContent = t.note ?? ''
      valueEl.style.display = t.value ? '' : 'none'
      noteEl.style.display = t.note ? '' : 'none'
      const r = (t.anchor ?? el).getBoundingClientRect()
      const w = tip.offsetWidth, h = tip.offsetHeight
      const vw = document.documentElement.clientWidth, vh = window.innerHeight
      let y = r.top - h - GAP
      if (y < EDGE) y = Math.min(r.bottom + GAP, vh - h - EDGE)
      const x = Math.min(Math.max(EDGE, r.left + r.width / 2 - w / 2), vw - w - EDGE)
      tip.style.left = `${Math.round(x)}px`
      tip.style.top = `${Math.round(y)}px`
      tip.style.visibility = 'visible'
      if (announce) say.textContent = [t.title, t.value, t.note].filter(Boolean).join(', ')
    }
    const markAt = (target: EventTarget | null) => (target instanceof Element ? target.closest(MARK) : null)

    const onOver = (e: PointerEvent) => {
      if (e.pointerType === 'touch') return
      const m = markAt(e.target)
      if (m) show(m)
      else if (hot) {
        // A mark reached with the keyboard stays until focus leaves its chart.
        const f = document.activeElement
        if (!(f && f !== document.body && f.matches(':focus-visible') && f.contains(hot))) hide()
      }
    }
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY, t: e.timeStamp } }
    const onUp = (e: PointerEvent) => {
      if (e.pointerType === 'mouse' || !down) return
      const tap = Math.hypot(e.clientX - down.x, e.clientY - down.y) < 10 && e.timeStamp - down.t < 600
      down = null
      if (!tap) return
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
    const onBlur = () => hide()
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
    const onScroll = () => { if (hot) hide() }

    document.addEventListener('pointerover', onOver, { passive: true })
    document.addEventListener('pointerdown', onDown, { passive: true })
    document.addEventListener('pointerup', onUp, { passive: true })
    document.addEventListener('focusin', onFocus)
    document.addEventListener('focusout', onBlur)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, { capture: true, passive: true })
    window.addEventListener('blur', hide)
    return () => {
      hide()
      document.removeEventListener('pointerover', onOver)
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('pointerup', onUp)
      document.removeEventListener('focusin', onFocus)
      document.removeEventListener('focusout', onBlur)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, { capture: true })
      window.removeEventListener('blur', hide)
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
