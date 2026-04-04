import { createContext, useContext, useState, useCallback, useRef } from 'react'
import { Check, AlertCircle, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface Toast {
  id: number
  type: 'success' | 'error' | 'info'
  message: string
  exiting: boolean
}

interface ToastMethods {
  success: (message: string) => void
  error: (message: string) => void
  info: (message: string) => void
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

  const addToast = useCallback((type: Toast['type'], message: string) => {
    const id = ++idRef.current
    setToasts(prev => {
      const next = [...prev, { id, type, message, exiting: false }]
      // Cap at MAX_TOASTS — drop oldest
      return next.length > MAX_TOASTS ? next.slice(next.length - MAX_TOASTS) : next
    })
    const timer = setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
    // Clean up timer if component unmounts (best-effort)
    return () => clearTimeout(timer)
  }, [dismiss])

  const toast: ToastMethods = {
    success: (msg) => addToast('success', msg),
    error: (msg) => addToast('error', msg),
    info: (msg) => addToast('info', msg),
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
              t.type === 'success' && 'border-l-green-500 border-border',
              t.type === 'error' && 'border-l-destructive border-border',
              t.type === 'info' && 'border-l-blue-500 border-border',
              t.exiting
                ? 'animate-out fade-out duration-300'
                : 'animate-in slide-in-from-right-4 fade-in duration-200',
            )}
          >
            <span className="shrink-0 mt-0.5">
              {t.type === 'success' && <Check className="w-4 h-4 text-green-500" />}
              {t.type === 'error' && <AlertCircle className="w-4 h-4 text-destructive" />}
              {t.type === 'info' && <Info className="w-4 h-4 text-blue-500" />}
            </span>
            <span className="flex-1 text-foreground leading-snug">{t.message}</span>
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
