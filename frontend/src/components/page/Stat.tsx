import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { RunningNumber } from '@/components/motion'

export type StatTone = 'foreground' | 'primary' | 'info' | 'success' | 'warning'

const TONE: Record<StatTone, string> = {
  foreground: 'text-foreground',
  primary: 'text-primary',
  info: 'text-info',
  success: 'text-success',
  warning: 'text-warning',
}

/** A value's own decimals on the way to it (12.5 runs as 11.8, 12.1, 12.5). */
function asPrinted(value: number): ((n: number) => string) | undefined {
  if (Number.isInteger(value)) return undefined
  const places = String(value).split('.')[1]?.length ?? 0
  return n => n.toFixed(places)
}

/**
 * One number without a box: a small label, the number in the display face,
 * an optional unit beside it and a line under it. Several in a row make a
 * StatStrip.
 */
export function Stat({ label, value, format, measure = '', unit, foot, tone = 'foreground', size = 40, className }: {
  label: ReactNode
  /** A number runs from the value shown to a new one (RunningNumber). */
  value: ReactNode
  /** Text for a numeric value (default: as the number prints, with its decimals). */
  format?: (n: number) => string
  /** What the number counts when that can change (hours, then minutes): a new
   *  measure shows its value at once instead of running from the old one. */
  measure?: string
  unit?: ReactNode
  foot?: ReactNode
  tone?: StatTone
  size?: 40 | 30
  className?: string
}) {
  return (
    <div className={className}>
      <div className="text-[14px] text-muted-foreground">{label}</div>
      <div className={cn('mt-1.5 flex items-baseline', unit === '%' ? 'gap-0' : 'gap-1.5', TONE[tone])}>
        <span className={cn('font-display leading-none font-bold', size === 40 ? 'text-[40px]' : 'text-[30px]')}>
          {typeof value === 'number' ? <RunningNumber key={measure} value={value} format={format ?? asPrinted(value)} /> : value}
        </span>
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
