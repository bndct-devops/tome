// Shared pieces of the designed Stats pages (look A2: Home's section titles,
// Stat figures and covers on hairlines). Every piece keeps its loaded box while
// loading, so a skeleton built from them reserves the final layout.
import type { CSSProperties, ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { coverSrc } from '@/lib/covers'
import { CoverImage } from '@/components/CoverImage'
import { SectionTitle } from '@/components/page'

/** Text that is only a placeholder while loading: it keeps its line box, drawn as a block. */
export function Ph({ on, children }: { on: boolean; children: ReactNode }) {
  return on ? <span aria-hidden="true" className="select-none rounded-[6px] bg-muted text-transparent">{children}</span> : <>{children}</>
}

/**
 * Below the last section whose size is known on a first visit: a quiet, generic
 * stand-in (a few soft lines fading out towards the bottom of the window), not a
 * picture of any section. The sections take its place when their answer lands; it
 * is the page's last element, so its going moves nothing that is drawn.
 */
export function Pending() {
  // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS value
  const fade = 'linear-gradient(to bottom, #000 0%, #000 25%, transparent 100%)'
  return (
    <div aria-hidden="true" data-pending="" className="pointer-events-none flex flex-col gap-4" style={{ height: '60vh', maskImage: fade, WebkitMaskImage: fade }}>
      <div className="h-5 w-[38%] rounded-[6px] bg-muted/70" />
      <div className="h-[88px] rounded-[12px] bg-muted/45" />
      <div className="h-4 w-[82%] rounded-[6px] bg-muted/45" />
      <div className="h-4 w-[64%] rounded-[6px] bg-muted/45" />
      <div className="h-[88px] rounded-[12px] bg-muted/35" />
    </div>
  )
}

/**
 * Home's progress track: 6 px, accent fill on a tinted track. Inside a section
 * with an entry (motion.ts) the fill grows from the start; `style` takes its
 * stagger() delay. `tip` (tipAttrs()) gives it a tooltip and a hover ring; in a
 * list marked .k-bars the other tracks dim while one is hovered.
 */
export function Track({ pct, className, style, tip }: { pct: number; className?: string; style?: CSSProperties; tip?: { 'data-tip': string } }) {
  return (
    <div className={cn('k-track k-bar h-[6px] flex-1 overflow-hidden rounded-full', className)} style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--background))', ...style }} {...tip}>
      <div className="k-grow-x h-full rounded-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
    </div>
  )
}

/** A cover in a fixed 2:3 box, as Home's rows draw them. `id` null draws the empty box. */
export function Cover({ id, h, className }: { id: number | null; h: number; className?: string }) {
  return (
    <span className={cn('relative block shrink-0 overflow-hidden rounded-[8px] bg-muted', className)} style={{ height: h, width: Math.round((h * 2) / 3) }}>
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
