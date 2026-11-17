// Hover, focus and tap tooltips for the designed Stats pages. One tooltip for
// the whole page (TipLayer, mounted once by the Stats frame) reads what a mark
// says when the pointer reaches it; marks only carry attributes, so a pointer
// move renders nothing in React. The mark under the pointer gets data-hot, which
// motion.css styles (.k-hit columns wash, .k-cell outlines, .k-track rings; in a
// .k-bars set the hot .k-bar, or the bars of the hot .k-col, stay at full colour
// while the set's other bars dim).
//
// Two ways for a mark to say what it is:
//   1. Its own text, for a few marks or HTML elements (covers, tracks):
//        <Link to=… {...tipAttrs({ title: b.title, value: t`Finished ${day}` })}>
//   2. A chart-wide lookup, for many marks (formatted only when hovered):
//        const tips = useTips((i) => ({ title: fmtDay(cells[i].date), value: formatDuration(cells[i].s) }), { x: 7, y: 1 })
//        <svg {...tips} aria-label=…> … <rect data-tip-i={i} className="k-cell" /> … </svg>
//      The chart becomes one focus stop: arrow keys step through its marks in DOM
//      order, x marks per Left/Right and y per Up/Down (e.g. a week-column grid:
//      x 7, y 1; a row-major grid of n columns: x 1, y n). Home/End, Escape.
//      Return `anchor` to place the tip over another element than the mark (a bar
//      over its hit column).
// Tip text: `title` (medium), `value` (foreground), `note` (muted), as the old
// charts' tooltips were laid out. Touch: a tap on a mark shows its tip, a tap
// elsewhere or a scroll hides it; scrolling is never held. Keyboard focus on a
// mark with tipAttrs (a cover link) shows its tip too.
import { useId, useLayoutEffect } from 'react'

export type Tip = { title: string; value?: string; note?: string; anchor?: Element | null }
export type TipSource = (i: number, el: Element) => Tip | null

const sources = new Map<string, TipSource>()

/** Look up a tip for a mark element (used by TipLayer). */
export function tipFor(el: Element): Tip | null {
  const own = el.getAttribute('data-tip')
  if (own != null) {
    const [title, value, note] = own.split('\n')
    return { title, value: value || undefined, note: note || undefined }
  }
  const i = el.getAttribute('data-tip-i')
  const host = el.closest('[data-tips]')
  const src = host ? sources.get(host.getAttribute('data-tips') ?? '') : undefined
  return i != null && src ? src(Number(i), el) : null
}

// A tip that is not a mark's: a chart that tracks the pointer itself (Guide.tsx)
// asks the layer to show text at a place, and is told when the layer hides it
// (pointer elsewhere, scroll, Escape, focus gone) so it can clear its own marks.
export type TipRect = { left: number; right: number; top: number; bottom: number }
type Layer = { showAt: (t: Tip, r: TipRect, onHide: () => void, announce?: boolean) => void; hide: () => void }
let layer: Layer | null = null
export function setTipLayer(l: Layer | null) { layer = l }
export const tipLayer = {
  showAt: (t: Tip, r: TipRect, onHide: () => void, announce = false) => layer?.showAt(t, r, onHide, announce),
  hide: () => layer?.hide(),
}
/** Spread on the svg that holds a Guide (Guide.tsx): one focus stop. */
export const guideHost = { tabIndex: 0, 'data-guide-host': '' } as const
/** Marks the element whose own pointer handling owns the tip (a Guide's surface). */
export const GUIDE = '[data-guide]'

/** Selector of everything that has a tip. */
export const MARK = '[data-tip],[data-tip-i]'

/** Attributes of a mark that carries its own tip text. */
export function tipAttrs(t: Omit<Tip, 'anchor'>): { 'data-tip': string } {
  return { 'data-tip': [t.title, t.value ?? '', t.note ?? ''].join('\n') }
}

/** Attributes of a chart whose marks (data-tip-i) are looked up with `fn`; it becomes one focus stop. */
export function useTips(fn: TipSource, nav: { x?: number; y?: number } = {}) {
  const id = useId()
  useLayoutEffect(() => { sources.set(id, fn) })
  useLayoutEffect(() => () => { sources.delete(id) }, [id])
  return { 'data-tips': id, 'data-tips-nav': `${nav.x ?? 1},${nav.y ?? 0}`, tabIndex: 0 }
}
