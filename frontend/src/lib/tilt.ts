import { useCallback, useRef, type MouseEvent } from 'react'

interface TiltOptions {
  /** How far the element rises while tilted, in px (default 4). */
  lift?: number
  /** Scale while tilted (default 1.02). */
  scale?: number
  /** Sets `--glare-x` / `--glare-y` (percent) on the tilting element, for a
   *  highlight that follows the pointer. */
  glare?: boolean
}

function reducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * The cover tilt the Library and Series cards have: the card follows the
 * pointer by a few degrees and lifts a few px, and settles back on leave. Put
 * `perspective` on the wrapper and spread `handlers` on it; give the tilting
 * element `ref` and `tiltStyle`. Does nothing under reduced motion.
 */
export function useTilt(enabled = true, degrees = 6, { lift = 4, scale = 1.02, glare = false }: TiltOptions = {}) {
  const el = useRef<HTMLElement | null>(null)
  const setEl = useCallback((node: HTMLElement | null) => { el.current = node }, [])
  const onMouseMove = useCallback((e: MouseEvent<HTMLElement>) => {
    const node = el.current
    if (!enabled || !node || reducedMotion()) return
    const rect = e.currentTarget.getBoundingClientRect()
    const fx = (e.clientX - rect.left) / rect.width
    const fy = (e.clientY - rect.top) / rect.height
    const x = fx * 2 - 1
    const y = fy * 2 - 1
    node.style.transform = `rotateY(${x * degrees}deg) rotateX(${y * -degrees}deg) translateY(-${lift}px) scale(${scale})`
    if (glare) {
      node.style.setProperty('--glare-x', `${fx * 100}%`)
      node.style.setProperty('--glare-y', `${fy * 100}%`)
    }
  }, [enabled, degrees, lift, scale, glare])
  const onMouseLeave = useCallback(() => {
    const node = el.current
    if (!node) return
    node.style.transform = 'rotateY(0deg) rotateX(0deg) translateY(0px) scale(1)'
    if (glare) {
      node.style.setProperty('--glare-x', '50%')
      node.style.setProperty('--glare-y', '50%')
    }
  }, [glare])
  return {
    /** Spread on the wrapper: it gives the perspective and listens to the pointer. */
    wrapperProps: { style: { perspective: '600px' } as const, onMouseMove, onMouseLeave },
    /** Spread on the element that tilts. */
    tiltProps: { ref: setEl, style: { transition: 'transform 200ms ease-out', transformStyle: 'preserve-3d', willChange: 'transform' } as const },
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
