// The shared frame of the Settings and Admin pages: one header, a left
// section nav (a pill row on phones) and the content column. Both pages use
// these pieces so they read as one app even though they stay two pages.
import { useEffect, useRef, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { PageBody, PageHeader, SectionTitle } from '@/components/page'

export interface SectionNavItem {
  id: string
  label: string
}

/** Text field and select look for these pages. Width classes go next to it. */
// eslint-disable-next-line lingui/no-unlocalized-strings -- class names
export const FIELD_INPUT = 'h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring'
/** Label above a field. */
// eslint-disable-next-line lingui/no-unlocalized-strings -- class names
export const FIELD_LABEL = 'block text-[13px] text-muted-foreground mb-1.5'
/** Header row of a hairline table: 12px small caps. */
// eslint-disable-next-line lingui/no-unlocalized-strings -- class names
export const TABLE_HEAD = 'text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground'

/**
 * Left section nav at desktop width, a sideways pill row on phones. Items are
 * links when `hrefFor` is given (Settings jumps to anchors), buttons
 * otherwise (Admin switches panels).
 */
export function SectionNav({ items, active, onSelect, label, hrefFor }: {
  items: SectionNavItem[]
  active: string | null
  onSelect: (id: string, e: React.MouseEvent) => void
  label: string
  hrefFor?: (id: string) => string
}) {
  const pillRefs = useRef<Record<string, HTMLElement | null>>({})

  // Keep the active pill in view on phones.
  useEffect(() => {
    if (!active) return
    const pill = pillRefs.current[active]
    if (pill && pill.offsetParent !== null) pill.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' })
  }, [active])

  function item(s: SectionNavItem, className: string, ref?: (el: HTMLElement | null) => void) {
    const isActive = s.id === active
    const common = {
      onClick: (e: React.MouseEvent) => onSelect(s.id, e),
      'aria-current': isActive ? ('page' as const) : undefined,
      className,
    }
    return hrefFor
      ? <a key={s.id} ref={ref} href={hrefFor(s.id)} {...common}>{s.label}</a>
      : <button key={s.id} ref={ref} type="button" {...common}>{s.label}</button>
  }

  return (
    <>
      <nav aria-label={label} className="hidden lg:block sticky top-6 self-start">
        <div className="flex flex-col gap-0.5">
          {items.map(s => item(s, cn(
            'flex items-center h-9 px-3 rounded-lg text-[14px] text-left transition-colors',
            s.id === active
              ? 'bg-primary/10 text-foreground font-medium'
              : 'text-muted-foreground hover:text-foreground hover:bg-secondary',
          )))}
        </div>
      </nav>

      <nav aria-label={label} className="lg:hidden sticky top-0 z-10 -mx-5 sm:-mx-6 bg-background">
        <div className="flex gap-1.5 overflow-x-auto px-5 sm:px-6 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {items.map(s => item(s, cn(
            'shrink-0 whitespace-nowrap inline-flex items-center h-8 px-3.5 rounded-full text-[13px] transition-colors',
            s.id === active
              ? 'bg-primary/10 text-foreground font-medium'
              : 'bg-secondary text-muted-foreground hover:text-foreground',
          ), el => { pillRefs.current[s.id] = el }))}
        </div>
      </nav>
    </>
  )
}

/** Page frame shared by Settings and Admin. */
export function SectionLayout({ title, eyebrow, nav, children }: {
  title: ReactNode
  eyebrow?: ReactNode
  nav: ReactNode
  children: ReactNode
}) {
  return (
    <PageBody width="wide">
      <PageHeader eyebrow={eyebrow} title={title} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:gap-10 lg:grid-cols-[220px_minmax(0,1fr)]">
        {nav}
        <div className="min-w-0">{children}</div>
      </div>
    </PageBody>
  )
}

/**
 * One section of the content column: a 20px title with an optional action on
 * the right, the explanatory line under it, then the body.
 */
export function PanelSection({ id, title, description, action, children, className }: {
  id?: string
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  children?: ReactNode
  className?: string
}) {
  return (
    <section id={id} className={cn('scroll-mt-16 lg:scroll-mt-6', className)}>
      <SectionTitle size={20} action={action}>{title}</SectionTitle>
      {description && <p className="mt-2 text-[14px] text-muted-foreground max-w-[70ch]">{description}</p>}
      {children && <div className="mt-5">{children}</div>}
    </section>
  )
}
