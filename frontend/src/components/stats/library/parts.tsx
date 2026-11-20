// Small pieces the Library page's sections share.
import type { CSSProperties, ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { SectionTitle } from '@/components/page'
import { Heading } from '@/components/stats/kit/Kit'
import { K } from '@/components/stats/kit/motion'

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
      <div className="mb-3.5 flex h-[20px] items-center gap-2 overflow-hidden whitespace-nowrap text-[13px] leading-[18px] text-muted-foreground">{note ?? ' '}</div>
    </>
  )
}

/**
 * A thin 4 px track, as the mockup's rows and table cells draw them. Inside a
 * section with an entry (kit/motion.ts) the fill grows from its start; `style`
 * takes its stagger() delay. `tip` (tipAttrs()) gives it a tooltip and a hover
 * ring; the pointer finds it 8 px above and below the line (a band that adds
 * no height).
 */
export function Thin({ frac, className, style, tip }: { frac: number; className?: string; style?: CSSProperties; tip?: { 'data-tip': string } }) {
  return (
    <div className={cn('relative h-[4px] rounded-full', tip && "k-track k-bar before:absolute before:inset-x-0 before:-inset-y-2 before:content-['']", className)} style={style} {...tip}>
      <div className="h-full overflow-hidden rounded-full" style={{ background: 'var(--rail)' }}>
        <div className={cn('h-full rounded-full bg-primary', K.growX)} style={{ width: `${Math.min(100, Math.max(0, frac * 100))}%` }} />
      </div>
    </div>
  )
}

/**
 * The range a section follows, as a chip beside its heading: the Library page
 * is all time (its eyebrow says so) except the sections that carry this chip.
 * Tinted with the accent so it reads as the range control's own choice.
 */
export function RangeChip({ children }: { children: ReactNode }) {
  return (
    <span
      className="inline-flex h-[20px] shrink-0 items-center whitespace-nowrap rounded-full px-2.5 text-[12px] font-medium normal-case tracking-normal text-primary"
      style={{ background: 'color-mix(in oklch, var(--primary) 14%, transparent)' }}
    >
      {children}
    </span>
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
