/**
 * Pop: how a menu, popover or small floating panel opens and closes.
 *
 *   {open && <div className="absolute …" role="menu">…</div>}
 * becomes
 *   <Pop open={open} origin="top-right" className="absolute …" role="menu">…</Pop>
 *
 * Pop renders the positioned element itself (an `m.div` with your className,
 * style, ref, role, aria-* and data-* props), so absolute or fixed positioning
 * works as before. It owns the AnimatePresence: the panel grows from 0.96 and
 * comes 4 px from the side it hangs from (140 ms), opaque from its first frame
 * so what is behind it never shows through, and fades out as it leaves (100 ms). Focus handling, outside clicks and Escape stay with the
 * caller.
 *
 * `origin` is the corner or side the panel hangs from ("top", "top-right",
 * "bottom-left", "left" …), default "top". A responsive transform-origin can
 * be given with Tailwind classes instead (`origin-top-left sm:origin-top-right`);
 * the class wins over `origin`, which then only sets the offset's direction.
 *
 * A panel that would run past the left or right edge of the viewport (a menu
 * hung from a pill near a phone's edge) is moved back inside it, 8 px clear.
 * `fit={false}` leaves that to a caller that places the panel itself (measured
 * while hidden at 0,0, the fit would shift it for good).
 */
import { useCallback, type ComponentProps, type CSSProperties, type Ref } from 'react'
import { AnimatePresence, m } from 'motion/react'
import { cn } from '@/lib/utils'
import { EASE } from './presets'

type DivProps = Omit<ComponentProps<typeof m.div>, 'initial' | 'animate' | 'exit' | 'transition' | 'children'>

const EDGE = 8

export function Pop({ open, origin = 'top', fit: fitViewport = true, className, style, children, ref, ...rest }: DivProps & {
  open: boolean
  origin?: string
  fit?: boolean
  children?: React.ReactNode
}) {
  // Measured as it mounts, before the first paint: shift it inside the viewport.
  const fit = useCallback((node: HTMLDivElement | null) => {
    if (typeof ref === 'function') ref(node)
    else if (ref) (ref as { current: HTMLDivElement | null }).current = node
    if (!node || !fitViewport) return
    // The CSS translate property, not transform: motion owns transform.
    // Measured at rest: the entering scale would make it look narrower.
    node.style.translate = ''
    const t = node.style.transform
    node.style.transform = 'none'
    const r = node.getBoundingClientRect()
    node.style.transform = t
    const over = r.right - (window.innerWidth - EDGE)
    const under = EDGE - r.left
    const dx = over > 0 ? -Math.min(over, Math.max(0, r.left - EDGE)) : under > 0 ? under : 0
    // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS value
    if (dx) node.style.translate = `${Math.round(dx)}px 0`
  }, [ref, fitViewport])
  const words = origin.split(/[\s-]+/)
  const cssOrigin = words.join(' ')
  // Starts a few px toward the side it hangs from: down from a top edge, up
  // from a bottom one; sideways only for a panel hung from a left or right side.
  const y = words.includes('top') ? -4 : words.includes('bottom') ? 4 : 0
  const x = y !== 0 ? 0 : words.includes('left') ? -4 : words.includes('right') ? 4 : 0
  const from = { opacity: 1, scale: 0.96, x, y }
  return (
    <AnimatePresence>
      {open && (
        <m.div
          key="pop"
          initial={from}
          animate={{ opacity: 1, scale: 1, x: 0, y: 0, transition: { duration: 0.14, ease: EASE } }}
          exit={{ ...from, opacity: 0, transition: { duration: 0.1, ease: EASE } }}
          className={cn('origin-(--pop-origin)', className)}
          style={{ '--pop-origin': cssOrigin, ...style } as CSSProperties & DivProps['style']}
          ref={fit as Ref<HTMLDivElement>}
          {...rest}
        >
          {children}
        </m.div>
      )}
    </AnimatePresence>
  )
}
