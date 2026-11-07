import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '../lib/utils'
import { t } from '@lingui/core/macro'

const FADE = 40

function edgeMask(left: boolean, right: boolean): CSSProperties | undefined {
  if (!left && !right) return undefined
  // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS value
  const img = `linear-gradient(to right, ${left ? 'transparent' : '#000'} 0, #000 ${left ? FADE : 0}px, #000 calc(100% - ${right ? FADE : 0}px), ${right ? 'transparent' : '#000'} 100%)`
  return { maskImage: img, WebkitMaskImage: img }
}

/**
 * Horizontal scroll row with visible affordances: edge fades whenever content
 * continues past either edge, plus hover chevrons on pointer devices. The bare
 * `overflow-x-auto` rows on Home clipped the last card with nothing signalling
 * "there is more" (UX sweep finding).
 */
export function HScrollRow({ children, className, wrapClassName, controlsTop }: {
  children: ReactNode
  className?: string
  /** Kept for callers; the edge fade is a transparency mask now, so it melts
   *  into whatever surface the row sits on in every theme. */
  fadeFrom?: 'card' | 'background'
  /** Classes for the outer wrapper the fades/chevrons anchor to. Negative
   *  margins (-mx-*) belong HERE, not on className: on the scroll div they
   *  push its clip edge past the wrapper and a sliver of the next card shows
   *  beyond the fade. */
  wrapClassName?: string
  /** Tailwind top-* class anchoring the chevrons. Defaults to the row's
   *  vertical center — pass the cover's midline (e.g. 'top-24') for card rows
   *  where text below the artwork would otherwise drag the chevrons low. */
  controlsTop?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [canLeft, setCanLeft] = useState(false)
  const [canRight, setCanRight] = useState(false)

  const update = useCallback(() => {
    const el = ref.current
    if (!el) return
    setCanLeft(el.scrollLeft > 4)
    setCanRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 4)
  }, [])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    // Covers async children (cover images) growing the scroll width
    const mo = new MutationObserver(update)
    mo.observe(el, { childList: true, subtree: true })
    return () => { ro.disconnect(); mo.disconnect() }
  }, [update])

  const nudge = (dir: 1 | -1) => {
    const el = ref.current
    if (!el) return
    el.scrollBy({ left: dir * Math.round(el.clientWidth * 0.8), behavior: 'smooth' })
  }

  return (
    <div className={cn('group/hscroll relative min-w-0', wrapClassName)}>
      {/* Edge fades: a mask on the scroller itself, so the covers fade to
          transparent instead of under a painted band that only matched one
          surface (a lighter strip past the edge in light and amber). */}
      <div ref={ref} onScroll={update} className={cn('flex overflow-x-auto', className)} style={edgeMask(canLeft, canRight)}>
        {children}
      </div>
      {/* Chevrons stay visible whenever content is hidden — the gradient alone
          is invisible over dark artwork, and at widths where the cutoff lands
          between cards it sits over empty gap, so a hover-only affordance
          meant off-screen books gave no signal at all. */}
      {canLeft && (
        <button
          type="button"
          aria-label={t`Scroll left`}
          onClick={() => nudge(-1)}
          className={cn(
            'flex absolute left-1 -translate-y-1/2 w-8 h-8 items-center justify-center rounded-full border border-border bg-card/95 shadow-sm text-muted-foreground hover:text-foreground opacity-70 hover:opacity-100 group-hover/hscroll:opacity-100 focus-visible:opacity-100 transition-opacity',
            controlsTop ?? 'top-1/2'
          )}
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
      )}
      {canRight && (
        <button
          type="button"
          aria-label={t`Scroll right`}
          onClick={() => nudge(1)}
          className={cn(
            'flex absolute right-1 -translate-y-1/2 w-8 h-8 items-center justify-center rounded-full border border-border bg-card/95 shadow-sm text-muted-foreground hover:text-foreground opacity-70 hover:opacity-100 group-hover/hscroll:opacity-100 focus-visible:opacity-100 transition-opacity',
            controlsTop ?? 'top-1/2'
          )}
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      )}
    </div>
  )
}
