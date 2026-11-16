// Small pieces the Library page's sections share.
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { SectionTitle } from '@/components/page'
import { Heading } from '@/components/stats/kit/Kit'

/**
 * A section heading. On the phone a long note goes on its own line under the
 * title (as the Overview's "The year"), so it never runs off the edge; the
 * height of either form is fixed, so the choice does not move anything.
 */
export function Head({ children, note, stack }: { children: ReactNode; note?: ReactNode; stack?: boolean }) {
  if (!stack) return <Heading note={note}>{children}</Heading>
  return (
    <>
      <SectionTitle className="mb-1.5">{children}</SectionTitle>
      <div className="mb-3.5 truncate text-[13px] leading-[18px] text-muted-foreground">{note ?? ' '}</div>
    </>
  )
}

/** A thin 4 px track, as the mockup's rows and table cells draw them. */
export function Thin({ frac, className }: { frac: number; className?: string }) {
  return (
    <div className={cn('h-[4px] overflow-hidden rounded-full', className)} style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--background))' }}>
      <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, frac * 100))}%` }} />
    </div>
  )
}

/** A message inside a box reserved for data (empty states, failures). */
export function Note({ h, children, center }: { h: number; children: ReactNode; center?: boolean }) {
  return (
    <div className={cn('flex text-[15px] text-muted-foreground', center ? 'items-center justify-center rounded-[12px] bg-secondary/50 px-6 text-center text-[14px]' : 'items-center')} style={{ height: h }}>
      <p>{children}</p>
    </div>
  )
}
