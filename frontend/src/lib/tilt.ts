import { useCallback, useRef, type MouseEvent } from 'react'

interface TiltOptions {
  /** How far the element rises while tilted, in px (default 3). */
  lift?: number
  /** Scale while tilted (default 1). */
  scale?: number
  /** Sets `--glare-x` / `--glare-y` (percent) on the tilting element, for a
   *  highlight that follows the pointer. */
  glare?: boolean
}

/** The one set of numbers every lifting cover uses (Library cards, Home,
 *  CoverLift): a few degrees toward the pointer, a 3 px lift. */
export const COVER_TILT = { degrees: 4, lift: 3, scale: 1 } as const

function stillPointer(): boolean {
  if (typeof window === 'undefined') return true
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches || !window.matchMedia('(hover: hover) and (pointer: fine)').matches
}

// Pressing a cover (.cover-lift:active, components/motion/motion.css) sets
// --cover-press; the transform always multiplies it in.
const PRESS = 'scale(var(--cover-press, 1))'
const REST = `rotateY(0deg) rotateX(0deg) translateY(0px) scale(1) ${PRESS}`

/**
 * The cover tilt the Library and Series cards have: the card follows the
 * pointer by a few degrees and lifts a few px, and settles back on leave. Put
 * `perspective` on the wrapper and spread `handlers` on it; give the tilting
 * element `ref` and `tiltStyle`. Does nothing under reduced motion or on a
 * touch screen (a tap would leave the cover tilted). Most call sites want
 * `<CoverLift>` (components/motion) instead.
 */
export function useTilt(enabled = true, degrees: number = COVER_TILT.degrees, { lift = COVER_TILT.lift, scale = COVER_TILT.scale, glare = false }: TiltOptions = {}) {
  const el = useRef<HTMLElement | null>(null)
  const settle = useRef<ReturnType<typeof setTimeout>>(undefined)
  const tilted = useRef(false)
  const setEl = useCallback((node: HTMLElement | null) => { el.current = node }, [])
  const onMouseMove = useCallback((e: MouseEvent<HTMLElement>) => {
    const node = el.current
    if (!enabled || !node || stillPointer()) return
    const rect = e.currentTarget.getBoundingClientRect()
    const fx = (e.clientX - rect.left) / rect.width
    const fy = (e.clientY - rect.top) / rect.height
    const x = fx * 2 - 1
    const y = fy * 2 - 1
    // A layer only for the cover under the pointer: hundreds of always-on
    // layers make a big grid slow to paint and to capture.
    clearTimeout(settle.current)
    if (node.style.willChange !== 'transform') node.style.willChange = 'transform'
    tilted.current = true
    node.style.transform = `rotateY(${x * degrees}deg) rotateX(${y * -degrees}deg) translateY(-${lift}px) scale(${scale}) ${PRESS}`
    if (glare) {
      node.style.setProperty('--glare-x', `${fx * 100}%`)
      node.style.setProperty('--glare-y', `${fy * 100}%`)
    }
  }, [enabled, degrees, lift, scale, glare])
  const onMouseLeave = useCallback(() => {
    const node = el.current
    if (!node || !tilted.current) return
    tilted.current = false
    node.style.transform = REST
    clearTimeout(settle.current)
    settle.current = setTimeout(() => { node.style.willChange = '' }, 240)
    if (glare) {
      node.style.setProperty('--glare-x', '50%')
      node.style.setProperty('--glare-y', '50%')
    }
  }, [glare])
  return {
    /** Spread on the wrapper: it gives the perspective and listens to the pointer. */
    wrapperProps: { style: { perspective: '600px' } as const, onMouseMove, onMouseLeave },
    /** Spread on the element that tilts. */
    tiltProps: { ref: setEl, style: { transform: PRESS, transition: 'transform 200ms ease-out', transformStyle: 'preserve-3d' } as const },
  }
}

/** The cover's box-shadow: the bloom, plus a ring for selection (accent) and
 *  keyboard focus (foreground, outside the selection ring when both). An
 *  inline box-shadow overrides Tailwind's ring utilities, so the rings are
 *  part of the same shadow list. */
export function coverRing(selected: boolean, focused: boolean): string {
  const gap = '0 0 0 2px var(--background)'
  if (selected && focused) return `${gap}, 0 0 0 4px var(--primary), 0 0 0 6px var(--foreground), var(--cover-bloom-fan)`
  if (selected) return `${gap}, 0 0 0 4px var(--primary), var(--cover-bloom-fan)`
  if (focused) return `${gap}, 0 0 0 4px var(--foreground), var(--cover-bloom-fan)`
  return 'var(--cover-bloom-fan)'
}
