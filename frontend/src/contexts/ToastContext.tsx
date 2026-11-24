import { createContext, useContext, useState, useCallback, useMemo, useRef } from 'react'
import { Check, AlertCircle, Info, X } from 'lucide-react'
import { AnimatePresence, m } from 'motion/react'
import { MotionProvider } from '@/lib/motion'

interface ToastAction {
  label: string
  onClick: () => void
}

interface Toast {
  id: number
  type: 'success' | 'error' | 'info'
  message: string
  action?: ToastAction
}

interface ToastOptions {
  action?: ToastAction
}

interface ToastMethods {
  success: (message: string, opts?: ToastOptions) => void
  error: (message: string, opts?: ToastOptions) => void
  info: (message: string, opts?: ToastOptions) => void
}

interface ToastContextValue {
  toast: ToastMethods
}

const ToastContext = createContext<ToastContextValue | null>(null)

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}

const MAX_TOASTS = 5
const AUTO_DISMISS_MS = 4000
// Toasts with an action (e.g. Undo) linger a little longer so there's time to act.
const AUTO_DISMISS_ACTION_MS = 7000
// A toast slides in from the right; one leaving fades and lifts out of the
// stack at once (popLayout), and the others glide into its place.
const EASE = [0.2, 0.8, 0.2, 1] as const
const TOAST_MOTION = {
  layout: 'position',
  initial: { opacity: 0, x: 16 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, transition: { duration: 0.15, ease: EASE } },
  transition: { duration: 0.2, ease: EASE, layout: { duration: 0.24, ease: EASE } },
} as const

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const idRef = useRef(0)

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const addToast = useCallback((type: Toast['type'], message: string, action?: ToastAction) => {
    const id = ++idRef.current
    setToasts(prev => {
      const next = [...prev, { id, type, message, action }]
      // Cap at MAX_TOASTS - drop oldest
      return next.length > MAX_TOASTS ? next.slice(next.length - MAX_TOASTS) : next
    })
    const timer = setTimeout(() => dismiss(id), action ? AUTO_DISMISS_ACTION_MS : AUTO_DISMISS_MS)
    // Clean up timer if component unmounts (best-effort)
    return () => clearTimeout(timer)
  }, [dismiss])

  // Stable identity: pages list `toast` in effect and callback dependencies,
  // so a fresh object per render would re-run their loads after every toast.
  const value = useMemo<ToastContextValue>(() => ({
    toast: {
      success: (msg, opts) => addToast('success', msg, opts?.action),
      error: (msg, opts) => addToast('error', msg, opts?.action),
      info: (msg, opts) => addToast('info', msg, opts?.action),
    },
  }), [addToast])

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Outside the app's MotionProvider (App.tsx), so the stack brings its own. */}
      <MotionProvider>
      <div
        aria-live="polite"
        aria-atomic="false"
        className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none w-80 max-w-[calc(100vw-2rem)]"
      >
        <AnimatePresence initial={false} mode="popLayout">
        {toasts.map(t => (
          <m.div
            key={t.id}
            {...TOAST_MOTION}
            className="flex items-start gap-3 px-4 py-3 rounded-xl shadow-lg ring-1 ring-panel-ring text-[14px] bg-card pointer-events-auto"
          >
            <span className="shrink-0 mt-0.5">
              {t.type === 'success' && <Check className="w-4 h-4 text-success" />}
              {t.type === 'error' && <AlertCircle className="w-4 h-4 text-destructive" />}
              {t.type === 'info' && <Info className="w-4 h-4 text-info" />}
            </span>
            <span className="flex-1 text-foreground leading-snug">{t.message}</span>
            {t.action && (
              <button
                onClick={() => { t.action!.onClick(); dismiss(t.id) }}
                className="shrink-0 mt-0.5 font-semibold text-primary hover:underline transition-colors touch-feedback"
              >
                {t.action.label}
              </button>
            )}
            <button
              onClick={() => dismiss(t.id)}
              className="shrink-0 mt-0.5 text-muted-foreground hover:text-foreground transition-colors touch-feedback"
              aria-label="Dismiss"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </m.div>
        ))}
        </AnimatePresence>
      </div>
      </MotionProvider>
    </ToastContext.Provider>
  )
}
