// The shared frame of the Settings and Admin pages: one header, a left
// section nav (a pill row on phones) and the content column. Both pages use
// these pieces so they read as one app even though they stay two pages.
import { Fragment, useEffect, useRef, useSyncExternalStore, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { cn } from '@/lib/utils'
import { Glide, GlideGroup, PageBody, PageHeader, SectionTitle } from '@/components/page'
import { m } from 'motion/react'
import { TomeMark } from '@/components/TomeMark'
import { crossFade, isPlainClick, routeCommitted } from '@/lib/coverTransition'
import { leaveAreaDelta } from '@/lib/areaBack'

// Where a sidebar-placed SectionNav puts its desktop list. SectionSidebar
// (rendered by AppShell, a parent) registers the element; the nav subscribes,
// so it re-renders into the slot once that exists without a setState in an effect.
let slotEl: HTMLElement | null = null
const slotSubs = new Set<() => void>()
function registerSlot(el: HTMLElement | null) { slotEl = el; slotSubs.forEach(f => f()) }
function subscribeSlot(f: () => void) { slotSubs.add(f); return () => { slotSubs.delete(f) } }
const readSlot = () => slotEl

/** Leaves Settings / Admin for the page the user came from, else Home. */
function useLeaveArea() {
  const navigate = useNavigate()
  return () => {
    const delta = leaveAreaDelta()
    if (delta != null) navigate(delta)
    else navigate('/')
  }
}

export interface SectionNavItem {
  id: string
  label: string
  /** Small label above the first item of a run with this group. */
  group?: string
}

/** Each item with whether a group label goes above it. */
function withGroupStarts(items: SectionNavItem[]) {
  return items.map((s, i) => ({ s, groupStart: s.group != null && s.group !== items[i - 1]?.group }))
}

// A section switch cross-fades the page (crossFade). The phone pill row scrolls to
// the chosen pill as it starts, so a cross-fade of the row showed two copies of its
// labels, offset by the scroll. The row is left out of the fade: only the live row
// shows (its indicator glides and it scrolls as usual).
const PILLS_VT_CSS = '::view-transition-group(section-pills),::view-transition-new(section-pills){animation:none}::view-transition-old(section-pills){display:none}'

const GROUP_LABEL ='px-3 pt-1 pb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80'

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
 * links when `hrefFor` is given (each section has its own address), buttons
 * otherwise. Items with a `group` get a small group label above each run
 * (a thin divider between runs in the phone pill row).
 */
export function SectionNav({ items, active, onSelect, label, hrefFor, placement = 'column' }: {
  items: SectionNavItem[]
  active: string | null
  /** Button items (no `hrefFor`) only. With `hrefFor`, a plain click opens the
   *  section here, replacing the history entry so Back leaves the page in one step. */
  onSelect?: (id: string, e: React.MouseEvent) => void
  label: string
  hrefFor?: (id: string) => string
  /** `sidebar`: the desktop list renders inside the page's SectionSidebar instead of a column. */
  placement?: 'column' | 'sidebar'
}) {
  const pillRow = useRef<HTMLDivElement>(null)
  const slot = useSyncExternalStore(subscribeSlot, readSlot, () => null)
  const navigate = useNavigate()

  // A section is a place inside the page, not a step in the history: it
  // replaces the entry, with the same short cross-fade a page change gets.
  function open(id: string, e: React.MouseEvent) {
    if (!hrefFor) { onSelect?.(id, e); return }
    if (!isPlainClick(e)) return
    e.preventDefault()
    if (id !== active) {
      const to = hrefFor(id)
      // The fade ends at the router's commit, capped in crossFade.
      crossFade(() => {
        const landed = routeCommitted()
        navigate(to, { replace: true })
        return landed
      })
    }
    // A new section starts at its top, also when the old one was scrolled.
    const main = document.querySelector('main')
    if (main && main.scrollTop > 0) main.scrollTo({ top: 0 })
  }

  // Keep the active pill in view on phones. Scroll the row itself, sideways
  // only: scrollIntoView would also scroll <main> and cancel the section jump
  // that a tap on the pill just started.
  useEffect(() => {
    if (!active) return
    const row = pillRow.current
    const pill = row?.querySelector<HTMLElement>(`[data-section="${CSS.escape(active)}"]`)
    if (!row || !pill || pill.offsetParent === null) return
    const left = pill.offsetLeft - (row.clientWidth - pill.offsetWidth) / 2
    row.scrollTo({ left: Math.max(0, left), behavior: 'smooth' })
  }, [active])

  // The active tint is one indicator that glides between entries (Glide).
  // The pill row's other pills are filled, so there it rides over their fills
  // and under the labels instead of behind them.
  function item(s: SectionNavItem, className: string, radius: number, overFills = false) {
    const isActive = s.id === active
    const common = {
      onClick: (e: React.MouseEvent) => open(s.id, e),
      'aria-current': isActive ? ('page' as const) : undefined,
      'data-section': s.id,
      className: cn('relative', className, isActive && 'glide-host'),
    }
    const body = overFills
      ? <>{isActive && <Glide className="bg-primary/10" radius={radius} />}<span className="relative z-[1]">{s.label}</span></>
      : <>{isActive && <Glide behind className="bg-primary/10" radius={radius} />}{s.label}</>
    return hrefFor
      ? <a key={s.id} href={hrefFor(s.id)} {...common}>{body}</a>
      : <button key={s.id} type="button" {...common}>{body}</button>
  }

  return (
    <>
      {placement === 'sidebar' ? (slot && createPortal(
        <GlideGroup>
        <nav aria-label={label} className="isolate flex flex-col gap-0.5">
          {withGroupStarts(items).map(({ s, groupStart }, i) => (
            <Fragment key={s.id}>
              {groupStart && <div className={cn(GROUP_LABEL, i > 0 && 'mt-4')}>{s.group}</div>}
              {item(s, cn(
                'flex items-center h-9 px-3 rounded-lg text-[14px] text-left transition-colors',
                s.id === active
                  ? 'bg-primary/10 text-foreground font-medium'
                  : 'text-muted-foreground hover:text-foreground hover:bg-secondary',
              ), 8)}
            </Fragment>
          ))}
        </nav>
        </GlideGroup>,
        slot,
      )) : (
      <GlideGroup>
      <nav aria-label={label} className="hidden lg:block sticky top-6 self-start">
        <div className="isolate flex flex-col gap-0.5">
          {withGroupStarts(items).map(({ s, groupStart }, i) => (
            <Fragment key={s.id}>
              {groupStart && <div className={cn(GROUP_LABEL, i > 0 && 'mt-4')}>{s.group}</div>}
              {item(s, cn(
                'flex items-center h-9 px-3 rounded-lg text-[14px] text-left transition-colors',
                s.id === active
                  ? 'bg-primary/10 text-foreground font-medium'
                  : 'text-muted-foreground hover:text-foreground hover:bg-secondary',
              ), 8)}
            </Fragment>
          ))}
        </div>
      </nav>
      </GlideGroup>
      )}

      <GlideGroup>
      <style href="tome-section-pills-vt" precedence="default">{PILLS_VT_CSS}</style>
      <nav aria-label={label} className="lg:hidden sticky top-0 z-10 -mx-5 sm:-mx-6 bg-background" style={{ viewTransitionName: 'section-pills' }} data-section-pills>
        <m.div ref={pillRow} layoutScroll className="relative isolate flex gap-1.5 overflow-x-auto px-5 sm:px-6 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {withGroupStarts(items).map(({ s, groupStart }, i) => (
            <Fragment key={s.id}>
              {groupStart && i > 0 && <span aria-hidden className="shrink-0 self-center w-px h-4 mx-1 bg-border" />}
              {item(s, cn(
                'shrink-0 whitespace-nowrap inline-flex items-center h-8 px-3.5 rounded-full text-[13px] transition-colors',
                s.id === active
                  ? 'bg-primary/10 text-foreground font-medium'
                  : 'bg-secondary text-muted-foreground hover:text-foreground',
              ), 9999, true)}
            </Fragment>
          ))}
        </m.div>
      </nav>
      </GlideGroup>
    </>
  )
}

/** Page frame shared by Settings and Admin. */
export function SectionLayout({ title, eyebrow, nav, sidebar = false, wide = false, children }: {
  title: ReactNode
  eyebrow?: ReactNode
  nav: ReactNode
  /** The nav lives in the page's SectionSidebar on desktop; here it only adds the phone pill row. */
  sidebar?: boolean
  /** A wide table (Admin > Metadata) takes the full content width. */
  wide?: boolean
  children: ReactNode
}) {
  return (
    <PageBody width={sidebar && !wide ? 'reading' : 'wide'} columnClassName={sidebar && !wide ? 'lg:max-w-[840px]' : undefined}>
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
  const back = useLeaveArea()
  return (
    <aside className="hidden md:flex w-64 shrink-0 flex-col bg-frame animate-[section-sidebar-in_240ms_var(--spring)]" data-shell-sidebar>
      <div className="group h-14 shrink-0 flex items-center gap-2 px-5 cursor-default">
        <TomeMark className="w-5 h-5 text-primary logo-bob" strokeWidth={7} />
        <span className="font-semibold text-[14px] text-foreground">Tome</span>
      </div>
      <div className="px-3">
        <button
          type="button"
          onClick={back}
          className="flex w-full items-center gap-2.5 h-9 px-3 rounded-lg text-[14px] text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors touch-feedback"
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
  /** Leave out when the page header already names the section (Admin tabs):
      the description and action then share one row. */
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  children?: ReactNode
  className?: string
}) {
  return (
    <section id={id} className={cn('scroll-mt-16 lg:scroll-mt-6', className)}>
      {title != null ? (
        <>
          <SectionTitle size={20} action={action}>{title}</SectionTitle>
          {description && <p className="mt-2 text-[14px] text-muted-foreground max-w-[70ch]">{description}</p>}
        </>
      ) : (description || action) ? (
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          {description && <div className="min-w-0 flex-1 text-[14px] text-muted-foreground max-w-[70ch]">{description}</div>}
          {action && <div className="ml-auto shrink-0">{action}</div>}
        </div>
      ) : null}
      {children && <div className={cn((title != null || description || action) && 'mt-5')}>{children}</div>}
    </section>
  )
}
