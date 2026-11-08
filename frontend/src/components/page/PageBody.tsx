import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * The content column of a page. Two widths only: `wide` for grids, charts and
 * dashboards; `reading` for pages made of rows and forms. Sections are
 * separated by the gap, never by their own margins.
 */
export function PageBody({ width = 'wide', children, className }: { width?: 'wide' | 'reading'; children: ReactNode; className?: string }) {
  return (
    <div className={cn('mx-auto px-5 sm:px-6 lg:px-10 pt-6 pb-16 flex flex-col gap-7', width === 'wide' ? 'max-w-[1180px]' : 'max-w-[820px]', className)}>
      {children}
    </div>
  )
}
