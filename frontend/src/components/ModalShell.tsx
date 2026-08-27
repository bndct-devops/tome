import { type ReactNode } from 'react'
import { AnimatePresence, m } from 'motion/react'
import { cn } from '@/lib/utils'

interface Props {
  open: boolean
  /** Backdrop click handler. Omit to make the backdrop inert (confirm-gated flows). */
  onClose?: () => void
  /** Sizing/layout for the panel wrapper, e.g. "w-full max-w-md". The visual
      card (bg, border, rounding) stays in the caller's markup. */
  className?: string
  children: ReactNode
}

/** Shared animated modal scaffold: backdrop fade + panel pop, in and out.
    Render it unconditionally and drive it via `open` — an early
    `if (!open) return null` in the caller would skip the exit animation. */
export function ModalShell({ open, onClose, className, children }: Props) {
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
            onClick={onClose}
          />
          <div className="fixed inset-0 flex items-center justify-center p-4 pointer-events-none">
            <m.div
              variants={{
                hidden: { opacity: 0, scale: 0.96, y: 8 },
                visible: { opacity: 1, scale: 1, y: 0 },
              }}
              transition={{ duration: 0.16, ease: [0.2, 0.8, 0.2, 1] }}
              className={cn('pointer-events-auto', className)}
            >
              {children}
            </m.div>
          </div>
        </m.div>
      )}
    </AnimatePresence>
  )
}
