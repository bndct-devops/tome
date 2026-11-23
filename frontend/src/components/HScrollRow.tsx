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
   *  vertical center - pass the cover's midline (e.g. 'top-24') for card rows
   *  where text below the artwork would otherwise drag the chevrons low. */
  controlsTop?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [canLeft, setCanLeft] = useState(false)
  const [canRight, setCanRight] = useState(false)

  const measure = useCallback(() => {
    const el = ref.current
    if (!el) return null
    // The right edge comes from the last child's layout box, not scrollWidth:
    // a child that is mid-animation (a transform) widens scrollWidth for a
    // moment, and nothing reports when it settles.
    const last = el.lastElementChild as HTMLElement | null
    const end = last
      ? last.offsetLeft - el.offsetLeft + last.offsetWidth + parseFloat(getComputedStyle(el).paddingRight || '0')
      : 0
    return { left: el.scrollLeft > 4, right: el.scrollLeft + el.clientWidth < end - 4 }
  }, [])

  // A chevron goes away at once but appears only if the overflow is still
  // there a moment later: an item that is leaving stays in the row for its
  // exit, and the row would flash a chevron for content that fits.
  const pending = useRef(0)
  const update = useCallback(() => {
    const now = measure()
    if (!now) return
    window.clearTimeout(pending.current)
    setCanLeft(v => v && now.left)
    setCanRight(v => v && now.right)
    if (now.left || now.right) {
      pending.current = window.setTimeout(() => {
        const later = measure()
        if (!later) return
        setCanLeft(later.left)
        setCanRight(later.right)
      }, 220)
    }
  }, [measure])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    // Reports once on observe, which is the first measurement.
    const ro = new ResizeObserver(update)
    ro.observe(el)
    // Covers async children (cover images) growing the scroll width
    const mo = new MutationObserver(update)
    mo.observe(el, { childList: true, subtree: true })
    return () => { ro.disconnect(); mo.disconnect(); window.clearTimeout(pending.current) }
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
      {/* Chevrons stay visible whenever content is hidden - the gradient alone
          is invisible over dark artwork, and at widths where the cutoff lands
          between cards it sits over empty gap, so a hover-only affordance
          meant off-screen books gave no signal at all. */}
      {canLeft && (
        <button
          type="button"
          aria-label={t`Scroll left`}
          onClick={() => nudge(-1)}
          className={cn(
            'flex absolute left-1 -translate-y-1/2 w-8 h-8 items-center justify-center rounded-full bg-card/95 shadow-sm ring-1 ring-panel-ring text-muted-foreground hover:text-foreground opacity-70 hover:opacity-100 group-hover/hscroll:opacity-100 focus-visible:opacity-100 transition-opacity',
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
            'flex absolute right-1 -translate-y-1/2 w-8 h-8 items-center justify-center rounded-full bg-card/95 shadow-sm ring-1 ring-panel-ring text-muted-foreground hover:text-foreground opacity-70 hover:opacity-100 group-hover/hscroll:opacity-100 focus-visible:opacity-100 transition-opacity',
            controlsTop ?? 'top-1/2'
          )}
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      )}
    </div>
  )
}
