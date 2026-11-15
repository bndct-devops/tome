import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * The content column of a page. Two widths only: `wide` for grids, charts and
 * dashboards; `reading` for pages made of rows and forms. Sections are
 * separated by the gap, never by their own margins.
 *
 * A `reading` page sits on the same left edge as a `wide` one (inside the wide
 * column) and is only narrower, so the title does not jump sideways between
 * pages. `align="center"` centres it instead, for pages without the app's
 * navigation (the public share page). `columnClassName` styles the narrow
 * column itself, `className` the outer frame.
 */
export function PageBody({ width = 'wide', align = 'start', children, className, columnClassName }: {
  width?: 'wide' | 'reading'
  align?: 'start' | 'center'
  children: ReactNode
  className?: string
  columnClassName?: string
}) {
  const frame = 'mx-auto px-5 sm:px-6 lg:px-10 pt-6 pb-16'
  if (width === 'wide' || align === 'center') {
    return (
      <div className={cn(frame, 'flex flex-col gap-7', width === 'wide' ? 'max-w-[1180px]' : 'max-w-[820px]', className, columnClassName)}>
        {children}
      </div>
    )
  }
  return (
    <div className={cn(frame, 'max-w-[1180px]', className)}>
      <div className={cn('flex flex-col gap-7 max-w-[740px]', columnClassName)}>
        {children}
      </div>
    </div>
  )
}
