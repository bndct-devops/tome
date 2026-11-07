// The pages outside the app shell (login, setup, forced password change, the
// SSO callback) share one look: the frame colour as the page ground and the
// form as a single inset panel, the same relationship the app's content panel
// has with its frame.
import { forwardRef, useState, type InputHTMLAttributes, type ReactNode } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { TomeMark } from '@/components/TomeMark'
import { cn } from '@/lib/utils'
import { useLingui } from '@lingui/react/macro'

/** Page ground: the frame colour, the panel centred on it. */
export function AuthFrame({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('min-h-screen flex flex-col items-center justify-center bg-frame px-4 py-10 safe-top', className)}>
      {children}
    </div>
  )
}

/** The wordmark: TomeMark + "Tome". */
export function Wordmark({ className }: { className?: string }) {
  return (
    <div className={cn('flex items-center gap-2 text-foreground', className)}>
      <TomeMark className="w-6 h-6 text-primary" strokeWidth={6} />
      {/* eslint-disable-next-line lingui/no-unlocalized-strings -- product name */}
      <span className="font-display text-[18px] font-bold leading-none tracking-tight">Tome</span>
    </div>
  )
}

/** The single form panel: wordmark on top, an optional small eyebrow line
 *  (a step, "Quick Connect"), the title in the display face, a subtitle. */
export function AuthPanel({ eyebrow, title, subtitle, aside, children }: {
  eyebrow?: ReactNode
  title: ReactNode
  subtitle?: ReactNode
  /** Right side of the wordmark row, e.g. a cancel button. */
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="w-full max-w-[420px] rounded-[var(--radius-panel)] bg-background p-6 sm:p-8">
      <div className="flex items-center justify-between gap-3 min-h-9">
        <Wordmark />
        {aside}
      </div>
      <div className="mt-8">
        {eyebrow && <div className="text-[13px] text-muted-foreground mb-1.5">{eyebrow}</div>}
        <h1 className="font-display text-[26px] leading-tight font-bold text-foreground">{title}</h1>
        {subtitle && <p className="mt-1.5 text-[15px] text-muted-foreground">{subtitle}</p>}
      </div>
      <div className="mt-7">{children}</div>
    </div>
  )
}

const INPUT = 'w-full h-11 rounded-lg bg-secondary px-3.5 text-[15px] text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-2 focus:ring-ring transition-shadow'

/** Label over a filled input. */
export function AuthField({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-[13px] text-muted-foreground mb-1.5">{label}</span>
      {children}
      {hint}
    </label>
  )
}

export const AuthInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }>(
  function AuthInput({ invalid, className, ...rest }, ref) {
    return <input ref={ref} className={cn(INPUT, invalid && 'ring-1 ring-destructive', className)} {...rest} />
  },
)

/** Password input with a show/hide toggle. Pass `shown`/`onToggle` to share
 *  one toggle between two fields; otherwise it keeps its own state. */
export function AuthPasswordInput({ invalid, shown, onToggle, toggleTabIndex, ...rest }: InputHTMLAttributes<HTMLInputElement> & {
  invalid?: boolean
  shown?: boolean
  onToggle?: () => void
  toggleTabIndex?: number
}) {
  const { t } = useLingui()
  const [own, setOwn] = useState(false)
  const visible = shown ?? own
  const toggle = onToggle ?? (() => setOwn(v => !v))
  return (
    <div className="relative">
      <input type={visible ? 'text' : 'password'} className={cn(INPUT, 'pr-11', invalid && 'ring-1 ring-destructive')} {...rest} />
      <button
        type="button"
        tabIndex={toggleTabIndex}
        onClick={toggle}
        aria-label={visible ? t`Hide password` : t`Show password`}
        className="absolute right-1.5 top-1/2 -translate-y-1/2 w-8 h-8 grid place-items-center rounded-full text-muted-foreground hover:text-foreground transition-colors"
      >
        {visible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
      </button>
    </div>
  )
}

/** An error as one line of text, no box. */
export function AuthError({ children }: { children: ReactNode }) {
  return <p role="alert" className="text-[14px] text-destructive">{children}</p>
}

/** Small spinner for pills and waiting lines. */
export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cn('inline-block w-4 h-4 border-2 rounded-full animate-spin', className)}
      style={{ borderColor: 'currentColor', borderTopColor: 'transparent' }}
    />
  )
}
