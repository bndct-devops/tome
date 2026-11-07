import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * A section heading with an optional action on the right ("See all"). Two
 * sizes: 24 for short pages such as Home, 20 for long pages such as a book.
 */
export function SectionTitle({ children, action, size = 24, className }: {
  children: ReactNode
  action?: ReactNode
  size?: 24 | 20
  className?: string
}) {
  return (
    <div className={cn('flex items-baseline justify-between gap-4', className)}>
      <h2 className={cn('font-display font-bold text-foreground leading-none', size === 24 ? 'text-[24px]' : 'text-[20px]')}>{children}</h2>
      {action && <div className="text-[15px] font-medium shrink-0">{action}</div>}
    </div>
  )
}
