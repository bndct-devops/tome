import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export type PillVariant = 'primary' | 'secondary' | 'quiet' | 'destructive'
export type PillSize = 'md' | 'lg'

const VARIANT: Record<PillVariant, string> = {
  primary: 'bg-primary text-primary-foreground hover:bg-primary/90',
  secondary: 'bg-secondary text-foreground hover:bg-muted',
  quiet: 'text-muted-foreground hover:text-foreground hover:bg-secondary',
  destructive: 'text-muted-foreground hover:text-destructive hover:bg-destructive/10',
}
const SIZE: Record<PillSize, string> = {
  md: 'h-9 px-4 text-[14px] gap-1.5',
  lg: 'h-11 px-5 text-[15px] gap-2',
}

/**
 * The app's button: a pill. `primary` is the one solid action in a view,
 * `secondary` the filled neutral, `quiet` text-only for header action rows.
 * Icon-only pills pass `icon` and an aria-label.
 */
export const Pill = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: PillVariant
  size?: PillSize
  icon?: boolean
}>(function Pill({ variant = 'secondary', size = 'md', icon = false, className, type = 'button', ...rest }, ref) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        'inline-flex items-center justify-center rounded-full font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 disabled:pointer-events-none touch-feedback',
        VARIANT[variant],
        SIZE[size],
        variant === 'primary' && 'font-semibold',
        icon && (size === 'lg' ? 'w-11 px-0' : 'w-9 px-0'),
        className,
      )}
      {...rest}
    />
  )
})
