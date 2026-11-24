import { useLayoutEffect, useRef, type RefObject } from 'react'
import { cancelFrame, frame, type Transition } from 'motion/react'

/** Travel of an indicator or a settling row: a short spring with a small overshoot. */
export const GLIDE: Transition = { type: 'spring', visualDuration: 0.3, bounce: 0.14 }

/**
 * An option's own fill, for options that host a gliding indicator: an empty
 * `<span aria-hidden className={GLIDE_FILL} />` in the option, which needs no
 * background of its own. It paints at the bottom of the row (the row must be
 * `isolate`), so the indicator, wherever it travels, is drawn over every
 * option's fill and under every label. A background on the option itself is
 * drawn over an indicator that comes from an option later in the row.
 */
export const GLIDE_FILL = 'pointer-events-none absolute inset-0 -z-10 rounded-[inherit] bg-secondary transition-colors duration-150 ease-(--spring)'

// While the indicator travels it covers options between the old choice and the
// new one, whose labels share its colour. For that time every label (each
// `[data-split]` element in the row) is painted in two colours split where the
// indicator's edges are: its `--split-in` colour over the indicator (the page's
// background colour by default), its `--split-out` colour elsewhere (the text
// colour by default), so each part of every label reads in every frame. Two rect
// reads per frame; the labels do not move in the row, so theirs are read once.
// It runs in motion's frame loop after the indicator is drawn for the frame, so
// the split never lags it. The labels' own colours change at once, with no
// transition: the split covers the travel, and when it ends the labels are
// already in their final colours.
function followIndicator(row: HTMLElement): () => void {
  const labels = Array.from(row.querySelectorAll<HTMLElement>('[data-split]'))
  // Offsets in the row (the row itself may move while the page changes under it).
  const at = (r: DOMRect, o: DOMRect) => ({ left: r.left - o.left, right: r.right - o.left, top: r.top - o.top, bottom: r.bottom - o.top, width: r.width, height: r.height })
  const origin = row.getBoundingClientRect()
  const boxes = labels.map(l => at(l.getBoundingClientRect(), origin))
  const host = row.querySelector('[data-glide]')?.parentElement
  const target = host && at(host.getBoundingClientRect(), origin)
  const start = performance.now()
  let still = 0
  // Icons in a label are not text (no background-clip): each takes one colour, by its middle.
  const icons = labels.map(l => Array.from(l.querySelectorAll<SVGElement>('svg')).map(svg => ({ svg, box: at(svg.getBoundingClientRect(), origin) })))
  const clear = () => labels.forEach((l, i) => {
    l.style.removeProperty('color'); l.style.removeProperty('background-image')
    l.style.removeProperty('background-clip'); l.style.removeProperty('-webkit-background-clip')
    l.style.removeProperty('background-repeat'); l.style.removeProperty('background-size'); l.style.removeProperty('background-position')
    icons[i].forEach(({ svg }) => svg.style.removeProperty('color'))
  })
  const step = () => {
    const el = row.querySelector('[data-glide]')
    if (!el || !target) { stop(); return }
    const ind = at(el.getBoundingClientRect(), row.getBoundingClientRect())
    labels.forEach((l, i) => {
      const box = boxes[i]
      // The indicator's rect in the label: its own colour inside (a band as high as the overlap),
      // the outside colour under it everywhere else. It may cover a label only in part, either way
      // (crossing from one row of a wrapped group to the next).
      const a = Math.max(0, Math.min(box.width, ind.left - box.left))
      const z = Math.max(0, Math.min(box.width, ind.right - box.left))
      const top = Math.max(0, Math.min(box.height, ind.top - box.top))
      const h = Math.max(0, Math.min(box.height, ind.bottom - box.top) - top)
      l.style.color = 'transparent'
      l.style.setProperty('-webkit-background-clip', 'text')
      l.style.backgroundClip = 'text'
      l.style.backgroundRepeat = 'no-repeat'
      l.style.backgroundImage = `linear-gradient(90deg, transparent ${a}px, var(--split-in, var(--background)) ${a}px, var(--split-in, var(--background)) ${z}px, transparent ${z}px), linear-gradient(var(--split-out, var(--foreground)), var(--split-out, var(--foreground)))`
      l.style.backgroundSize = `100% ${h}px, 100% 100%`
      l.style.backgroundPosition = `0 ${top}px, 0 0`
      icons[i].forEach(({ svg, box: b }) => {
        const mx = (b.left + b.right) / 2, my = (b.top + b.bottom) / 2
        const under = mx > ind.left && mx < ind.right && my > ind.top && my < ind.bottom
        svg.style.color = under ? 'var(--split-in, var(--background))' : 'var(--split-out, var(--foreground))'
      })
    })
    const landed = Math.abs(ind.left - target.left) < 0.5 && Math.abs(ind.width - target.width) < 0.5 && Math.abs(ind.top - target.top) < 0.5 && Math.abs(ind.height - target.height) < 0.5
    still = landed ? still + 1 : 0
    if ((still >= 2 && performance.now() - start > 80) || performance.now() - start > 900) stop()
  }
  const stop = () => { cancelFrame(step); clear() }
  frame.postRender(step, true)
  return stop
}

/**
 * Two-colour labels for a row whose chosen option hosts a `<Glide>` (PillGroup,
 * the goal dialog's target cards and date chips): call it with the row and the
 * chosen value; on every change of the value the labels split at the
 * indicator's edges until it lands. Mark each label `data-split`; give it
 * `--split-out` / `--split-in` colours when they are not the text and the page
 * background. The options' fills must sit under the indicator (see `GLIDE_FILL`).
 */
export function useIndicatorSplit(row: RefObject<HTMLElement | null>, value: unknown) {
  const shown = useRef(value)
  useLayoutEffect(() => {
    if (shown.current === value || !row.current) return
    shown.current = value
    return followIndicator(row.current)
  }, [row, value])
}
