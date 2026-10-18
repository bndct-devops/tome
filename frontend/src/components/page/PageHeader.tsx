import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * One page header for every page: an optional eyebrow (a date, a count, where
 * you are), the title in the display face, an optional one-line subtitle, and
 * a right-hand slot for the page's actions. Nothing else gets to invent a header.
 */
export function PageHeader({ eyebrow, title, subtitle, actions, className }: {
  eyebrow?: ReactNode
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <header className={cn('flex items-end justify-between gap-4 flex-wrap', className)}>
      <div className="min-w-0">
        {eyebrow && <div className="text-[13px] font-semibold uppercase tracking-wide text-muted-foreground">{eyebrow}</div>}
        <h1 className="font-display text-[34px] leading-none font-bold text-foreground mt-1">{title}</h1>
        {subtitle && <p className="mt-2 text-[15px] text-muted-foreground max-w-[70ch]">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </header>
  )
}
