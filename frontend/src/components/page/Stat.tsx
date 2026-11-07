import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export type StatTone = 'foreground' | 'primary' | 'info' | 'success' | 'warning'

const TONE: Record<StatTone, string> = {
  foreground: 'text-foreground',
  primary: 'text-primary',
  info: 'text-info',
  success: 'text-success',
  warning: 'text-warning',
}

/**
 * One number without a box: a small label, the number in the display face,
 * an optional unit beside it and a line under it. Several in a row make a
 * StatStrip.
 */
export function Stat({ label, value, unit, foot, tone = 'foreground', size = 40, className }: {
  label: ReactNode
  value: ReactNode
  unit?: ReactNode
  foot?: ReactNode
  tone?: StatTone
  size?: 40 | 30
  className?: string
}) {
  return (
    <div className={className}>
      <div className="text-[14px] text-muted-foreground">{label}</div>
      <div className={cn('mt-1.5 flex items-baseline gap-1.5', TONE[tone])}>
        <span className={cn('font-display leading-none font-bold tabular-nums', size === 40 ? 'text-[40px]' : 'text-[30px]')}>{value}</span>
        {unit && <span className={cn('font-semibold', size === 40 ? 'text-[16px]' : 'text-[14px] text-muted-foreground')}>{unit}</span>}
      </div>
      {foot && <div className="text-[14px] text-muted-foreground mt-1.5">{foot}</div>}
    </div>
  )
}

/** A row of Stats on hairlines. `cols` is the count at desktop width; phones get two. */
export function StatStrip({ children, cols = 4, bordered = true, className }: { children: ReactNode; cols?: 3 | 4 | 5; bordered?: boolean; className?: string }) {
  const grid = { 3: 'lg:grid-cols-3', 4: 'lg:grid-cols-4', 5: 'lg:grid-cols-5' }[cols]
  return (
    <section className={cn('grid grid-cols-2 gap-8', grid, bordered && 'py-6 border-y border-border/70', className)}>
      {children}
    </section>
  )
}
