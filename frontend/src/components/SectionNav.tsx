// The shared frame of the Settings and Admin pages: one header, a left
// section nav (a pill row on phones) and the content column. Both pages use
// these pieces so they read as one app even though they stay two pages.
import { useEffect, useRef, useSyncExternalStore, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { cn } from '@/lib/utils'
import { PageBody, PageHeader, SectionTitle } from '@/components/page'
import { TomeMark } from '@/components/TomeMark'

// Where a sidebar-placed SectionNav puts its desktop list. SectionSidebar
// (rendered by AppShell, a parent) registers the element; the nav subscribes,
// so it re-renders into the slot once that exists without a setState in an effect.
let slotEl: HTMLElement | null = null
const slotSubs = new Set<() => void>()
function registerSlot(el: HTMLElement | null) { slotEl = el; slotSubs.forEach(f => f()) }
function subscribeSlot(f: () => void) { slotSubs.add(f); return () => { slotSubs.delete(f) } }
const readSlot = () => slotEl

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
export function SectionNav({ items, active, onSelect, label, hrefFor, placement = 'column' }: {
  items: SectionNavItem[]
  active: string | null
  onSelect: (id: string, e: React.MouseEvent) => void
  label: string
  hrefFor?: (id: string) => string
  /** `sidebar`: the desktop list renders inside the page's SectionSidebar instead of a column. */
  placement?: 'column' | 'sidebar'
}) {
  const pillRow = useRef<HTMLDivElement>(null)
  const slot = useSyncExternalStore(subscribeSlot, readSlot, () => null)

  // Keep the active pill in view on phones.
  useEffect(() => {
    if (!active) return
    const pill = pillRow.current?.querySelector<HTMLElement>(`[data-section="${CSS.escape(active)}"]`)
    if (pill && pill.offsetParent !== null) pill.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' })
  }, [active])

  function item(s: SectionNavItem, className: string) {
    const isActive = s.id === active
    const common = {
      onClick: (e: React.MouseEvent) => onSelect(s.id, e),
      'aria-current': isActive ? ('page' as const) : undefined,
      'data-section': s.id,
      className,
    }
    return hrefFor
      ? <a key={s.id} href={hrefFor(s.id)} {...common}>{s.label}</a>
      : <button key={s.id} type="button" {...common}>{s.label}</button>
  }

  return (
    <>
      {placement === 'sidebar' ? (slot && createPortal(
        <nav aria-label={label} className="flex flex-col gap-0.5">
          {items.map(s => item(s, cn(
            'flex items-center h-9 px-3 rounded-lg text-[14px] text-left transition-colors',
            s.id === active
              ? 'bg-primary/10 text-foreground font-medium'
              : 'text-muted-foreground hover:text-foreground hover:bg-secondary',
          )))}
        </nav>,
        slot,
      )) : (
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
      )}

      <nav aria-label={label} className="lg:hidden sticky top-0 z-10 -mx-5 sm:-mx-6 bg-background">
        <div ref={pillRow} className="flex gap-1.5 overflow-x-auto px-5 sm:px-6 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {items.map(s => item(s, cn(
            'shrink-0 whitespace-nowrap inline-flex items-center h-8 px-3.5 rounded-full text-[13px] transition-colors',
            s.id === active
              ? 'bg-primary/10 text-foreground font-medium'
              : 'bg-secondary text-muted-foreground hover:text-foreground',
          )))}
        </div>
      </nav>
    </>
  )
}

/** Page frame shared by Settings and Admin. */
export function SectionLayout({ title, eyebrow, nav, sidebar = false, children }: {
  title: ReactNode
  eyebrow?: ReactNode
  nav: ReactNode
  /** The nav lives in the page's SectionSidebar on desktop; here it only adds the phone pill row. */
  sidebar?: boolean
  children: ReactNode
}) {
  return (
    <PageBody width={sidebar ? 'reading' : 'wide'} className={sidebar ? 'lg:max-w-[920px]' : undefined}>
      <PageHeader eyebrow={eyebrow} title={title} />
      <div className={cn('grid grid-cols-[minmax(0,1fr)] gap-4', !sidebar && 'lg:gap-10 lg:grid-cols-[220px_minmax(0,1fr)]')}>
        {nav}
        <div className="min-w-0">{children}</div>
      </div>
    </PageBody>
  )
}

/**
 * The sidebar a Settings or Admin page puts in place of the app's: the
 * wordmark, a way back, and the page's sections (filled by a SectionNav with
 * `placement="sidebar"`). It slides in from the left; the app sidebar is
 * simply gone while the page is open.
 */
export function SectionSidebar({ title }: { title: ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()
  // The first entry of a session has the key "default": there is nothing to go back to.
  const back = () => (location.key === 'default' ? navigate('/') : navigate(-1))
  return (
    <aside className="hidden md:flex w-64 shrink-0 flex-col bg-frame animate-[section-sidebar-in_240ms_var(--spring)]">
      <div className="group h-14 shrink-0 flex items-center gap-2 px-5 cursor-default">
        <TomeMark className="w-5 h-5 text-primary logo-bob" strokeWidth={7} />
        <span className="font-semibold text-[14px] text-foreground">Tome</span>
      </div>
      <div className="px-3">
        <button
          type="button"
          onClick={back}
          className="flex w-full items-center gap-2.5 h-9 px-3 rounded-lg text-[14px] text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          <Trans>Back</Trans>
        </button>
      </div>
      <div className="px-3 mt-5 mb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80 px-6">{title}</div>
      <div ref={registerSlot} className="flex-1 overflow-y-auto px-3 pb-4 overscroll-contain" />
    </aside>
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
