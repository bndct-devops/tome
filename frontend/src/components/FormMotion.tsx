/**
 * Small motion helpers for the form and table pages (Settings, Admin and the
 * panels they host), built on the kit in components/motion.
 *
 *   (useHeld, the hook behind Note, is in lib/useHeld.ts. For one block
 *   replaced by another in place, use Swap from components/page.)
 *   <Note value={err}>{v => <p>{v}</p>}</Note>
 *                        an Unfold for a message, an error or a reveal that
 *                        holds its last value while it folds away.
 *   <SameWidth active={busy ? 1 : 0}>{[idle, busy]}</SameWidth>
 *                        button labels that swap without resizing the button.
 *   <RowsSkeleton rows={4} />
 *                        a section's first load: hairline rows at the height
 *                        the rows will have, so nothing jumps when they land.
 */
import { type ReactNode } from 'react'
import { useHeld } from '@/lib/useHeld'
import { cn } from '@/lib/utils'
import { Unfold } from '@/components/motion'

export function Note<T>({ value, gap = 0, className, children }: {
  value: T | null | undefined | false
  gap?: number
  className?: string
  children: (v: T) => ReactNode
}) {
  const held = useHeld(value)
  return (
    <Unfold open={!!value} gap={gap} className={className}>
      {held !== null && children(held)}
    </Unfold>
  )
}

export function SameWidth({ active, className, children }: {
  active: number
  className?: string
  children: ReactNode[]
}) {
  return (
    <span className={cn('inline-grid', className)}>
      {children.map((c, i) => (
        <span
          key={i}
          aria-hidden={i !== active || undefined}
          className={cn('[grid-area:1/1] inline-flex items-center justify-center gap-1.5', i !== active && 'invisible')}
        >
          {c}
        </span>
      ))}
    </span>
  )
}

export function RowsSkeleton({ rows = 4, rowClassName = 'h-14', className }: {
  rows?: number
  rowClassName?: string
  className?: string
}) {
  return (
    <div aria-hidden className={cn('border-y border-border/70 divide-y divide-border/70', className)}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className={cn('flex items-center gap-3', rowClassName)}>
          <span className="h-3 rounded-full bg-secondary" style={{ width: `${40 - (i % 3) * 8}%` }} />
          <span className="ml-auto h-3 w-16 rounded-full bg-secondary/70" />
        </div>
      ))}
    </div>
  )
}
