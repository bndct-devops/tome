import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react'
import { AnimatePresence, m } from 'motion/react'
import { X } from 'lucide-react'
import { useLingui } from '@lingui/react/macro'
import { cn } from '@/lib/utils'
import { Pill } from '@/components/page'

// How many dialogs are open right now. Page-level keyboard shortcuts (the book
// page's Escape = go back, the library's j/k) check this so a key meant for a
// dialog never reaches the page behind it.
let openCount = 0
const listeners = new Set<() => void>()
function setOpen(delta: number) {
  openCount = Math.max(0, openCount + delta)
  listeners.forEach(fn => fn())
}
/** True while any ModalShell (or QuickLook, which registers too) is open. */
export function isModalOpen(): boolean {
  return openCount > 0
}
/** Register an open overlay for the lifetime of the calling effect. */
export function useRegisterOverlay(open: boolean) {
  useEffect(() => {
    if (!open) return
    setOpen(1)
    return () => setOpen(-1)
  }, [open])
}
/** Re-renders when a dialog opens or closes; for shortcut hooks. */
export function useAnyModalOpen(): boolean {
  const [v, setV] = useState(openCount > 0)
  useEffect(() => {
    const fn = () => setV(openCount > 0)
    listeners.add(fn)
    return () => { listeners.delete(fn) }
  }, [])
  return v
}

/** The left inset of the content panel: the app sidebar (open or collapsed to
 *  its rail) or the Settings/Admin section list. Dialogs centre over the
 *  content panel, not the viewport, so they sit where the page is. The
 *  sidebars carry `data-shell-sidebar`; on phones they are drawers, so 0. */
export function useShellInset(): number {
  const [inset, setInset] = useState(0)
  useLayoutEffect(() => {
    const find = () => document.querySelector<HTMLElement>('[data-shell-sidebar]')
    const measure = () => {
      const el = find()
      setInset(el && el.offsetParent !== null ? el.offsetWidth : 0)
    }
    measure()
    window.addEventListener('resize', measure)
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null
    const el = find()
    if (ro && el) ro.observe(el)
    return () => { window.removeEventListener('resize', measure); ro?.disconnect() }
  }, [])
  return inset
}

/** Calls `onClose` on Escape while `open`. Capture phase, so the page's own
 *  shortcuts never see the key. */
export function useEscape(open: boolean, onClose?: () => void) {
  useEffect(() => {
    if (!open || !onClose) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      e.preventDefault()
      e.stopPropagation()
      onClose()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open, onClose])
}

export type ModalSize = 'sm' | 'md' | 'lg' | 'xl'
const SIZE: Record<ModalSize, string> = {
  sm: 'max-w-[440px]',
  md: 'max-w-[600px]',
  lg: 'max-w-[760px]',
  xl: 'max-w-[960px]',
}

interface Props {
  open: boolean
  /** Close handler: Escape, the X and a backdrop click call it. Omit it for
      a confirm-gated flow that must be answered. */
  onClose?: () => void
  /** Legacy: sizing for the caller's own card. With `title` set, extra classes
      for the panel instead. */
  className?: string
  children: ReactNode
  /** The refreshed panel: pinned header with this title, scrolling body,
      optional footer. Without it the caller still brings its own card. */
  title?: ReactNode
  /** Small caps line above the title (a count, a book title, where you are). */
  eyebrow?: ReactNode
  /** One line under the title, muted. */
  subtitle?: ReactNode
  /** Controls at the right end of the header, before the close button. */
  headerActions?: ReactNode
  /** Pills for the footer, rendered right-aligned on a hairline. */
  footer?: ReactNode
  size?: ModalSize
  /** Hide the X (the footer carries the way out). */
  hideClose?: boolean
  /** Backdrop click closes (default: whenever onClose is set). */
  closeOnBackdrop?: boolean
  /** Padding and scroll live on the body; pass `p-0` for edge-to-edge lists. */
  bodyClassName?: string
  'aria-label'?: string
}

/** Shared dialog scaffold: full-viewport veil, panel centred over the content
    panel (not the viewport), pop in and out, Escape and backdrop close.
    Render it unconditionally and drive it via `open` so the exit animation
    runs. With `title` it draws the refreshed panel: 22px radius, a pinned
    header (eyebrow, display title, actions, X), a scrolling body and an
    optional footer. Without `title` it behaves as before and the caller's
    own markup is the card. */
export function ModalShell({
  open, onClose, className, children, title, eyebrow, subtitle, headerActions, footer,
  size = 'md', hideClose, closeOnBackdrop, bodyClassName, 'aria-label': ariaLabel,
}: Props) {
  const { t } = useLingui()
  const inset = useShellInset()
  useRegisterOverlay(open)
  useEscape(open, onClose)
  const backdropCloses = closeOnBackdrop ?? !!onClose
  const refreshed = title !== undefined

  return (
    <AnimatePresence>
      {open && (
        <m.div
          key="modal"
          className="fixed inset-0 z-50"
          initial="hidden"
          animate="visible"
          exit="hidden"
        >
          <m.div
            variants={{ hidden: { opacity: 0 }, visible: { opacity: 1 } }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 bg-black/50 backdrop-blur-sm"
            onClick={backdropCloses ? onClose : undefined}
          />
          <div
            className="fixed inset-0 flex items-center justify-center p-4 pointer-events-none"
            style={inset ? { left: inset } : undefined}
          >
            <m.div
              variants={{
                hidden: { opacity: 0, scale: 0.96, y: 8 },
                visible: { opacity: 1, scale: 1, y: 0 },
              }}
              transition={{ duration: 0.16, ease: [0.2, 0.8, 0.2, 1] }}
              role={refreshed ? 'dialog' : undefined}
              aria-modal={refreshed ? true : undefined}
              aria-label={ariaLabel}
              className={cn(
                'pointer-events-auto',
                refreshed && cn(
                  'relative flex w-full max-h-[min(88vh,920px)] flex-col overflow-hidden',
                  'rounded-[var(--radius-panel)] bg-card shadow-2xl shadow-black/40 ring-1 ring-panel-ring',
                  SIZE[size],
                ),
                className,
              )}
            >
              {refreshed ? (
                <>
                  <div className="flex items-start gap-3 px-6 pt-5 pb-3 sm:px-7">
                    <div className="min-w-0 flex-1">
                      {eyebrow && (
                        <div className="mb-1 truncate text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">{eyebrow}</div>
                      )}
                      <h2 className="font-display text-[22px] font-bold leading-tight text-foreground text-balance">{title}</h2>
                      {subtitle && <p className="mt-1 text-[14px] text-muted-foreground">{subtitle}</p>}
                    </div>
                    {headerActions && <div className="flex shrink-0 items-center gap-1.5">{headerActions}</div>}
                    {onClose && !hideClose && (
                      <Pill icon variant="quiet" onClick={onClose} aria-label={t`Close`} className="-mr-2 -mt-1 shrink-0">
                        <X className="h-4 w-4" />
                      </Pill>
                    )}
                  </div>
                  <div className={cn('min-h-0 flex-1 overflow-y-auto px-6 pb-6 sm:px-7', footer && 'pb-5', bodyClassName)}>
                    {children}
                  </div>
                  {footer && (
                    <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border/70 px-6 py-4 sm:px-7">
                      {footer}
                    </div>
                  )}
                </>
              ) : children}
            </m.div>
          </div>
        </m.div>
      )}
    </AnimatePresence>
  )
}
