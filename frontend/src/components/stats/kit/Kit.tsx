// Shared pieces of the designed Stats pages (look A2: Home's section titles,
// Stat figures and covers on hairlines). Every piece keeps its loaded box while
// loading, so a skeleton built from them reserves the final layout.
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { coverSrc } from '@/lib/covers'
import { CoverImage } from '@/components/CoverImage'
import { SectionTitle } from '@/components/page'

/** Text that is only a placeholder while loading: it keeps its line box, drawn as a block. */
export function Ph({ on, children }: { on: boolean; children: ReactNode }) {
  return on ? <span aria-hidden="true" className="select-none rounded-[6px] bg-muted text-transparent">{children}</span> : <>{children}</>
}

/** Home's progress track: 6 px, accent fill on a tinted track. */
export function Track({ pct, className }: { pct: number; className?: string }) {
  return (
    <div className={cn('h-[6px] flex-1 overflow-hidden rounded-full', className)} style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--background))' }}>
      <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
    </div>
  )
}

/** A cover in a fixed 2:3 box, as Home's rows draw them. `id` null draws the empty box. */
export function Cover({ id, h, title, className }: { id: number | null; h: number; title?: string; className?: string }) {
  return (
    <span className={cn('relative block shrink-0 overflow-hidden rounded-[8px] bg-muted', className)} style={{ height: h, width: Math.round((h * 2) / 3) }} title={title}>
      {id != null && <CoverImage src={coverSrc(id, { w: h > 60 ? 400 : 160 })} alt="" iconClassName="w-5 h-5" />}
    </span>
  )
}

/** Section heading: Home's SectionTitle (24/700), a quiet note beside it or, with `right`, at the right edge. */
export function Heading({ children, note, right, className }: { children: ReactNode; note?: ReactNode; right?: boolean; className?: string }) {
  return (
    <SectionTitle
      className={cn('mb-5', right ? '' : 'justify-start gap-3.5', className)}
      action={note ? <span className="text-[14px] font-normal text-muted-foreground">{note}</span> : undefined}
    >
      {children}
    </SectionTitle>
  )
}
