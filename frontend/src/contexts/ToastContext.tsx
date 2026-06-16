import { createContext, useContext, useState, useCallback, useRef } from 'react'
import { Check, AlertCircle, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ToastAction {
  label: string
  onClick: () => void
}

interface Toast {
  id: number
  type: 'success' | 'error' | 'info'
  message: string
  exiting: boolean
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
const EXIT_DURATION_MS = 300

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const idRef = useRef(0)

  const dismiss = useCallback((id: number) => {
    // Mark as exiting first, then remove after animation
    setToasts(prev => prev.map(t => t.id === id ? { ...t, exiting: true } : t))
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id))
    }, EXIT_DURATION_MS)
  }, [])

  const addToast = useCallback((type: Toast['type'], message: string, action?: ToastAction) => {
    const id = ++idRef.current
    setToasts(prev => {
      const next = [...prev, { id, type, message, exiting: false, action }]
      // Cap at MAX_TOASTS — drop oldest
      return next.length > MAX_TOASTS ? next.slice(next.length - MAX_TOASTS) : next
    })
    const timer = setTimeout(() => dismiss(id), action ? AUTO_DISMISS_ACTION_MS : AUTO_DISMISS_MS)
    // Clean up timer if component unmounts (best-effort)
    return () => clearTimeout(timer)
  }, [dismiss])

  const toast: ToastMethods = {
    success: (msg, opts) => addToast('success', msg, opts?.action),
    error: (msg, opts) => addToast('error', msg, opts?.action),
    info: (msg, opts) => addToast('info', msg, opts?.action),
  }

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none w-80 max-w-[calc(100vw-2rem)]"
      >
        {toasts.map(t => (
          <div
            key={t.id}
            className={cn(
              'flex items-start gap-3 px-4 py-3 rounded-xl shadow-xl text-sm border bg-card pointer-events-auto',
              'border-l-[3px]',
              t.type === 'success' && 'border-l-success border-border',
              t.type === 'error' && 'border-l-destructive border-border',
              t.type === 'info' && 'border-l-info border-border',
              t.exiting
                ? 'animate-out fade-out duration-300'
                : 'animate-in slide-in-from-right-4 fade-in duration-200',
            )}
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
                className="shrink-0 mt-0.5 font-semibold text-primary hover:underline transition-colors"
              >
                {t.action.label}
              </button>
            )}
            <button
              onClick={() => dismiss(t.id)}
              className="shrink-0 mt-0.5 text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Dismiss"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
