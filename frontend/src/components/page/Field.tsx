import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** A label over a value, for detail grids. */
export function Field({ label, value, accent, className }: { label: ReactNode; value: ReactNode; accent?: boolean; className?: string }) {
  return (
    <div className={className}>
      <div className="text-[13px] text-muted-foreground">{label}</div>
      <div className={cn('mt-0.5 text-[15px]', accent ? 'text-primary' : 'text-foreground')}>{value}</div>
    </div>
  )
}
