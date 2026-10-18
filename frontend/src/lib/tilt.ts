import { useCallback, useRef, type MouseEvent } from 'react'

/**
 * The cover tilt the Library and Series cards have: the card follows the
 * pointer by a few degrees and lifts 3px, and settles back on leave. Put
 * `perspective` on the wrapper and spread `handlers` on it; give the tilting
 * element `ref` and `tiltStyle`.
 */
export function useTilt(enabled = true, degrees = 4) {
  const el = useRef<HTMLElement | null>(null)
  const setEl = useCallback((node: HTMLElement | null) => { el.current = node }, [])
  const onMouseMove = useCallback((e: MouseEvent<HTMLElement>) => {
    const node = el.current
    if (!enabled || !node) return
    const rect = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * 2 - 1
    const y = ((e.clientY - rect.top) / rect.height) * 2 - 1
    node.style.transform = `rotateY(${x * degrees}deg) rotateX(${y * -degrees}deg) translateY(-3px)`
  }, [enabled, degrees])
  const onMouseLeave = useCallback(() => {
    const node = el.current
    if (node) node.style.transform = 'rotateY(0deg) rotateX(0deg) translateY(0px)'
  }, [])
  return {
    /** Spread on the wrapper: it gives the perspective and listens to the pointer. */
    wrapperProps: { style: { perspective: '600px' } as const, onMouseMove, onMouseLeave },
    /** Spread on the element that tilts. */
    tiltProps: { ref: setEl, style: { transition: 'transform 180ms ease-out', transformStyle: 'preserve-3d' } as const },
  }
}
