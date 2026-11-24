// Marks that move from their old size and place to the new one when a section's
// data changes after its entry (motion.ts, phase 'update'): a range change reads
// as a change, not a restart. A FLIP on the marks: their final geometry is read
// after each commit (getBBox / offset sizes, which ignore transforms) and a
// transform takes each mark from where it was to where it is now.
//
//   const tw = useTween(entry, bars, W)          data identity: a tween; W: re-read only
//   <g ref={tw} className="k-bars">
//     <path className={K.grow} data-k={key} … />        bar: from its old height, width and place
//     <div className="k-grow-x" data-k={key} data-kt="barx" />   HTML fill: from its old width
//     <div data-k={key} data-kt="move" />               anything else: from its old place
//
// In a list laid out in columns, give each mark its column (data-k-col): a mark
// whose column changed does not slide across the other rows; it leaves from its
// old place and rises in at the new one, as a new key does.
//
// Keys say what stays the same thing (a day, a book). A key that is new grows
// in (bars) or rises from partial opacity; a key that went is drawn once more
// from a copy at its old place (inside the nearest [data-k-layer], else the
// root, which must be position:relative for HTML marks) that shrinks or fades
// away, so no frame is blank. Only transform and opacity move, and only on
// marks, never on a [data-box] element. Reduced motion, a section not yet seen,
// or finishEntries(): the end state at once.
import { useEffect, useLayoutEffect, useState } from 'react'
import { DUR, EASE, type Entry } from './motion'

type Kind = 'bar' | 'barx' | 'move'
type Box = { el: Element; kind: Kind; layer: Element; col: string | null; x: number; y: number; w: number; h: number }

// Bars start at most this far apart in all (a little order, no slow wave).
const SPREAD = 120
const LEAVE = DUR.update

function abs(el: HTMLElement) {
  let x = 0, y = 0
  for (let n: HTMLElement | null = el; n; n = n.offsetParent as HTMLElement | null) { x += n.offsetLeft; y += n.offsetTop }
  return { x, y }
}

function measure(root: Element | null): Map<string, Box> {
  const out = new Map<string, Box>()
  if (!root) return out
  for (const el of root.querySelectorAll('[data-k]')) {
    const key = el.getAttribute('data-k')!
    const kind = (el.getAttribute('data-kt') ?? 'bar') as Kind
    const layer = el.parentElement?.closest('[data-k-layer]') ?? root
    const col = el.getAttribute('data-k-col')
    if (el instanceof SVGGraphicsElement) {
      const b = el.getBBox()
      out.set(key, { el, kind, layer, col, x: b.x, y: b.y, w: b.width, h: b.height })
    } else if (el instanceof HTMLElement) {
      const a = abs(el), l = layer instanceof HTMLElement ? abs(layer) : { x: 0, y: 0 }
      out.set(key, { el, kind, layer, col, x: a.x - l.x, y: a.y - l.y, w: el.offsetWidth, h: el.offsetHeight })
    }
  }
  return out
}

/** The transform that draws mark `n` where `p` was. */
function from(p: Box, n: Box): string | null {
  const r = (v: number) => Math.round(v * 1000) / 1000
  if (n.kind === 'bar') {
    if (n.w <= 0 || n.h <= 0) return null
    const dx = p.x + p.w / 2 - (n.x + n.w / 2), dy = p.y + p.h - (n.y + n.h)
    const sx = p.w / n.w, sy = p.h / n.h
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(sx - 1) < 0.005 && Math.abs(sy - 1) < 0.005) return null
    return `translate(${r(dx)}px, ${r(dy)}px) scale(${r(sx)}, ${r(sy)})`
  }
  if (n.kind === 'barx') {
    // The fill grows from its start; its row moves it up or down, so only x and width count.
    if (n.w <= 0) return null
    const dx = p.x - n.x, sx = p.w / n.w
    if (Math.abs(dx) < 0.5 && Math.abs(sx - 1) < 0.005) return null
    return `translateX(${r(dx)}px) scaleX(${r(sx)})`
  }
  const dx = p.x - n.x, dy = p.y - n.y
  if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return null
  return `translate(${r(dx)}px, ${r(dy)}px)`
}

const ENTER: Record<Kind, Keyframe[]> = {
  bar: [{ transform: 'scale(1, 0)' }, { transform: 'none' }],
  barx: [{ transform: 'scaleX(0)' }, { transform: 'none' }],
  move: [{ opacity: 0.35, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }],
}
const EXIT: Record<Kind, Keyframe[]> = {
  bar: [{ transform: 'none' }, { transform: 'scale(1, 0)' }],
  barx: [{ transform: 'none' }, { transform: 'scaleX(0)' }],
  move: [{ opacity: 1 }, { opacity: 0 }],
}

/** A copy of a mark that went, at its old place, for its exit. */
function ghost(p: Box): Element | null {
  if (!p.layer.isConnected) return null
  const c = p.el.cloneNode(true) as Element
  for (const n of [c, ...c.querySelectorAll('*')]) {
    for (const a of ['data-k', 'data-box', 'data-tip', 'data-tip-i', 'id', 'href', 'tabindex']) n.removeAttribute(a)
  }
  c.setAttribute('aria-hidden', 'true')
  if (c instanceof HTMLElement) {
    Object.assign(c.style, { position: 'absolute', left: `${p.x}px`, top: `${p.y}px`, width: `${p.w}px`, height: `${p.h}px`, margin: '0', pointerEvents: 'none' })
  } else if (c instanceof SVGElement) c.style.pointerEvents = 'none'
  p.layer.appendChild(c)
  return c
}

/**
 * Tween the marks (elements with data-k) under the returned callback ref's element when
 * `data` changes after `entry` was seen. `layout` (a width) re-reads the geometry without a tween.
 */
export function useTween(entry: Entry, data: unknown, layout: unknown = null) {
  const [state] = useState(() => ({
    root: null as Element | null,
    boxes: new Map<string, Box>(),
    data: undefined as unknown,
    anims: [] as Animation[],
    ghosts: [] as Element[],
  }))
  const [ref] = useState(() => (el: Element | null) => { state.root = el })
  const end = () => {
    for (const a of state.anims) a.finish()
    for (const g of state.ghosts) g.remove()
    state.anims = []
    state.ghosts = []
  }
  useLayoutEffect(() => {
    const prev = state.boxes
    const next = measure(state.root)
    state.boxes = next
    const changed = state.data !== undefined && state.data !== data
    state.data = data
    if (!changed || prev.size === 0) return
    // What moves: kept marks whose geometry changed, new marks, and marks that went
    // (one inside another that went leaves with it). A new answer equal to the
    // last (a re-render with a fresh array) moves nothing and leaves a running change alone.
    const keys = [...next.keys()]
    // Marks that changed column, and the marks inside them (they go with them).
    const crossedKeys = new Set(keys.filter((k) => { const p = prev.get(k); return !!p && p.col !== next.get(k)!.col }))
    const crossedEls = [...crossedKeys].map((k) => next.get(k)!.el)
    const inside = (el: Element) => crossedEls.some((c) => c !== el && c.contains(el))
    const moves = keys.map((k) => {
      const n = next.get(k)!
      const p = crossedKeys.has(k) ? undefined : prev.get(k)
      return { n, p, t: p && p.kind === n.kind ? from(p, n) : null }
    }).map((m, i) => ({ ...m, i })).filter((m) => !inside(m.n.el) && (!m.p || m.t))
    const allGone = [...prev].filter(([k]) => !next.has(k) || (crossedKeys.has(k) && !inside(next.get(k)!.el))).map(([, p]) => p)
    const gone = allGone.filter((p) => !allGone.some((o) => o !== p && o.el.contains(p.el)))
    if (moves.length === 0 && gone.length === 0) return
    end()
    if (!entry.update()) return
    const step = keys.length > 1 ? Math.min(6, SPREAD / (keys.length - 1)) : 0
    const opts = (i: number, dur: number): KeyframeAnimationOptions => ({ duration: dur, delay: Math.round(i * step), easing: EASE, fill: 'backwards' })
    for (const { n, t, i } of moves) {
      const frames = t ? [{ transform: t }, { transform: 'none' }] : ENTER[n.kind]
      // Moved marks travel together (a row's cells must not tear apart); bars keep a little order.
      state.anims.push(n.el.animate(frames, opts(n.kind === 'move' ? 0 : i, DUR.update)))
    }
    for (const p of gone) {
      const g = ghost(p)
      if (!g) continue
      state.ghosts.push(g)
      const a = g.animate(EXIT[p.kind], { duration: p.kind === 'move' ? DUR.fade / 2 : LEAVE, easing: EASE, fill: 'forwards' })
      a.onfinish = () => g.remove()
      a.oncancel = () => g.remove()
      state.anims.push(a)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- state and end are stable
  }, [entry, data, layout])
  // finishEntries() and the end of the change: everything at its end state.
  useEffect(() => entry.subscribe((p) => { if (p === 'done') end() }), [entry]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => end, []) // eslint-disable-line react-hooks/exhaustive-deps
  return ref
}

/**
 * A layer whose marks change colour or place all at once with the data (heat cells, a
 * year of days, a scatter with its axis): fill may not move, so on a change after the
 * section was seen, a copy of the old layer is drawn once more on top of the new one and
 * fades out (DUR.update) while the new layer is already opaque underneath. Nothing fades
 * from zero and no box moves.
 *
 *   const xf = useCrossFade(entry, loading ? null : sig, W)   sig: changes when the layer's look does
 *   <g ref={xf}>…cells…</g>                                   an SVG group (the copy goes next to it)
 *
 * `layout` (a width) takes a new copy without a change. Reduced motion, a section not yet
 * seen, or finishEntries(): the new layer at once.
 */
export function useCrossFade(entry: Entry, sig: string | null, layout: unknown = null) {
  const [state] = useState(() => ({
    el: null as Element | null,
    sig: null as string | null,
    copy: null as Element | null,
    ghost: null as Element | null,
    anim: null as Animation | null,
  }))
  const [ref] = useState(() => (el: Element | null) => { state.el = el })
  const end = () => {
    state.anim?.cancel()
    state.ghost?.remove()
    state.anim = null
    state.ghost = null
  }
  useLayoutEffect(() => {
    if (sig == null) return // loading: the last copy stays for the next answer
    const prev = state.sig, old = state.copy, el = state.el
    state.sig = sig
    state.copy = el ? quiet(el.cloneNode(true) as Element) : null
    if (prev == null || prev === sig || !old || !el?.parentNode) return
    if (!entry.update()) return
    end()
    el.after(old)
    state.ghost = old
    const a = old.animate([{ opacity: 1 }, { opacity: 0 }], { duration: DUR.update, easing: EASE, fill: 'forwards' })
    a.onfinish = () => { old.remove(); if (state.ghost === old) { state.ghost = null; state.anim = null } }
    state.anim = a
    // eslint-disable-next-line react-hooks/exhaustive-deps -- state and end are stable
  }, [entry, sig, layout])
  useEffect(() => entry.subscribe((p) => { if (p === 'done') end() }), [entry]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => end, []) // eslint-disable-line react-hooks/exhaustive-deps
  return ref
}

/** A copy for drawing only: no tips, keys, ids or pointer. */
function quiet(c: Element): Element {
  for (const n of [c, ...c.querySelectorAll('*')]) {
    for (const a of ['data-k', 'data-box', 'data-tip', 'data-tip-i', 'data-hot', 'data-dot', 'data-book', 'id', 'href', 'tabindex']) n.removeAttribute(a)
  }
  c.setAttribute('aria-hidden', 'true')
  if (c instanceof SVGElement || c instanceof HTMLElement) c.style.pointerEvents = 'none'
  return c
}
