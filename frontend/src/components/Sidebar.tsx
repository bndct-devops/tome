import { Fragment, createContext, useCallback, useContext, useId, useLayoutEffect, useState, useRef, useEffect, useMemo, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useSearchParams, useLocation, useNavigate, Link } from 'react-router-dom'
import {
  BookOpen, Plus, Pencil, Share2, Trash2,
  PanelLeftClose, PanelLeftOpen, Layers, Home, BarChart3,
  Settings, Shield, LogOut, ChevronsUpDown, Lock, X, BookPlus, ExternalLink,
  Sun, Moon, MoonStar, Flame, Coffee, Check, Sparkles, Users, Quote, BookMarked, CalendarDays, Loader2, GripVertical,
  type LucideIcon,
} from 'lucide-react'
import { api } from '@/lib/api'
import { ShareModal } from '@/components/ShareShelfModal'
import type { Library, SavedFilter } from '@/lib/books'
import { cn } from '@/lib/utils'
import { EntityModal } from '@/components/EntityModal'
import { ModalShell, useEscape } from '@/components/ModalShell'
import { Pill, Input, FormLabel, Checkbox, Glide, GlideGroup } from '@/components/page'
import { GLIDE } from '@/components/page/glideTransition'
import { useToast } from '@/contexts/ToastContext'
import { rememberOrder } from '@/lib/sidebarLists'
import { AnimatePresence, m } from 'motion/react'
import { EASE, Pop, RunningNumber, Unfold } from '@/components/motion'
import { TomeMark } from '@/components/TomeMark'
import { useAuth, isAdmin, isMember, type AuthUser } from '@/contexts/AuthContext'
import { getStoredTheme, switchTheme, type ThemeId } from '@/lib/theme'
import { DOCS, docsLink } from '@/lib/docs'
import { Trans, useLingui } from '@lingui/react/macro'
import { NamedIcon, loadAllIcons } from '@/lib/icons'
import { msg } from '@lingui/core/macro'
import type { MessageDescriptor } from '@lingui/core'

const SIDEBAR_KEY = 'tome_sidebar'

export function getIcon(name?: string | null, className = 'w-3.5 h-3.5') {
  return <NamedIcon icon={name} className={className} />
}

/** Lucide icon picker. `inline` (dialogs) opens the grid in the flow under
 *  the trigger, so it pushes the rows below down instead of covering them;
 *  the default is a popover (Admin). While the full icon set loads it holds
 *  its height with a quiet loading line. */
export function IconPicker({ value, onChange, inline = false }: { value: string; onChange: (v: string) => void; inline?: boolean }) {
  const { t } = useLingui()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    setTimeout(() => searchRef.current?.focus(), 0)
    if (inline) return
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open, inline])

  const [allIcons, setAllIcons] = useState<[string, LucideIcon][] | null>(null)
  useEffect(() => {
    if (open && !allIcons) loadAllIcons().then(m => setAllIcons(Object.entries(m))).catch(() => {})
  }, [open, allIcons])

  const filtered = useMemo(() => {
    if (!allIcons) return []
    const q = search.toLowerCase()
    const results = q ? allIcons.filter(([name]) => name.toLowerCase().includes(q)) : allIcons
    return results.slice(0, 96)
  }, [search, allIcons])

  const panel = (
    <>
      <input
        ref={searchRef}
        value={search}
        onChange={e => setSearch(e.target.value)}
        onKeyDown={e => { if (e.key === 'Escape' && inline) { e.stopPropagation(); e.nativeEvent.stopImmediatePropagation(); setOpen(false) } }}
        placeholder={t`Search icons…`}
        aria-label={t`Search icons`}
        className={inline ? 'w-full h-9 mb-2 rounded-lg bg-secondary px-3 text-[14px] placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring' : 'w-full h-9 mb-2 rounded-lg bg-secondary px-3 text-[14px] placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring'}
      />
      {!allIcons ? (
        <div className={cn('flex items-center justify-center gap-2 text-[13px] text-muted-foreground', inline ? 'h-[156px]' : 'h-[120px]')}>
          <Loader2 className="w-4 h-4 animate-spin" />
          <Trans>Loading icons</Trans>
        </div>
      ) : (
        <div className={cn('grid gap-1 overflow-y-auto', inline ? 'grid-cols-[repeat(auto-fill,minmax(32px,1fr))] h-[156px]' : 'grid-cols-8 max-h-[300px]')}>
          {filtered.map(([name, Comp]) => (
            <button
              key={name}
              type="button"
              onClick={() => { onChange(name); setOpen(false) }}
              className={cn(
                'flex items-center justify-center rounded-md hover:bg-secondary transition-colors',
                inline ? 'h-8' : 'w-7 h-7',
                value === name && 'bg-primary/15 text-primary'
              )}
              title={name}
              aria-label={name}
            >
              <Comp className={inline ? 'w-4 h-4' : 'w-3.5 h-3.5'} />
            </button>
          ))}
        </div>
      )}
      {!search && allIcons && <p className={cn('text-muted-foreground text-center mt-1.5', 'text-[12px]')}><Trans>Search to find more icons</Trans></p>}
    </>
  )

  if (inline) {
    return (
      <div ref={ref} className="min-w-0">
        <button
          type="button"
          onClick={() => { setOpen(o => !o); setSearch('') }}
          aria-expanded={open}
          className="inline-flex items-center gap-2 h-9 pl-2.5 pr-3.5 rounded-lg bg-secondary text-[14px] text-foreground hover:bg-surface-hover transition-colors touch-feedback"
        >
          <span className="grid place-items-center w-6 h-6 rounded-md bg-background/60"><NamedIcon icon={value} className="w-4 h-4" /></span>
          {open ? <Trans>Close icons</Trans> : <Trans>Change icon</Trans>}
        </button>
        <Unfold open={open}><div className="mt-2 rounded-xl bg-secondary/50 p-2.5">{panel}</div></Unfold>
      </div>
    )
  }

  return (
    <div className="relative shrink-0" ref={ref}>
      <button
        type="button"
        onClick={() => { setOpen(o => !o); setSearch('') }}
        className="flex items-center justify-center w-9 h-9 rounded-full bg-secondary hover:bg-surface-hover text-muted-foreground hover:text-foreground transition-colors touch-feedback"
        title={t`Choose icon`}
        aria-label={t`Choose icon`}
      >
        <NamedIcon icon={value} className="w-4 h-4" />
      </button>
      <Pop open={open} origin="top-left" className="absolute left-0 top-full mt-1 z-50 rounded-xl bg-card shadow-lg ring-1 ring-panel-ring p-2 w-72">
        {panel}
      </Pop>
    </div>
  )
}

interface Props {
  libraries: Library[]
  savedFilters: SavedFilter[]
  // 'none' = a non-dashboard page (Stats/Highlights/…) is open, so none of the
  // Home/All Books/Series items highlight - the active route item does instead.
  activeTab: 'home' | 'books' | 'series' | 'none'
  onLibrariesChange: () => void
  onSavedFiltersChange: () => void
  onOpenSeriesView: () => void
  onOpenHomeView: () => void
  mobileOpen: boolean
  onMobileClose: () => void
  /** A page that brings its own desktop sidebar (Settings, Admin) keeps only the phone drawer. */
  desktopHidden?: boolean
}

export function Sidebar({ libraries, savedFilters, activeTab, onLibrariesChange, onSavedFiltersChange, onOpenSeriesView, onOpenHomeView, mobileOpen, onMobileClose, desktopHidden }: Props) {
  const { t } = useLingui()
  const [open, setOpen] = useState(() => localStorage.getItem(SIDEBAR_KEY) !== 'closed')
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { user, logout } = useAuth()

  // Generic filter modal (for saved filters only)
  const [modalOpen, setModalOpen] = useState(false)
  const [modalTitle, setModalTitle] = useState('')
  const [modalInitialName, setModalInitialName] = useState('')
  const [modalInitialIcon, setModalInitialIcon] = useState('')
  const [modalDefaultIcon, setModalDefaultIcon] = useState('Library')
  const [modalOnSave, setModalOnSave] = useState<(name: string, icon: string) => Promise<void>>(() => async () => {})

  // Library modal (with is_public)
  const [libModalOpen, setLibModalOpen] = useState(false)
  const [libModalTitle, setLibModalTitle] = useState('')
  const [libModalOnSave, setLibModalOnSave] = useState<(name: string, icon: string, isPublic: boolean) => Promise<void>>(() => async () => {})
  const [libModalInitialName, setLibModalInitialName] = useState('')
  const [libModalInitialIcon, setLibModalInitialIcon] = useState('Library')
  const [libModalInitialPublic, setLibModalInitialPublic] = useState(true)
  const [libModalLibraryId, setLibModalLibraryId] = useState<number | null>(null)
  const [libModalAssignedIds, setLibModalAssignedIds] = useState<number[]>([])

  const location = useLocation()

  // Close mobile drawer on navigation
  useEffect(() => {
    onMobileClose()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, location.search])

  // The phone drawer closes on Escape like any overlay.
  useEscape(mobileOpen, onMobileClose)

  // Lock body scroll when mobile drawer is open
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [mobileOpen])

  const [binderyCount, setBinderyCount] = useState(0)
  const prevCountRef = useRef(0)
  const [badgePulse, setBadgePulse] = useState(false)

  useEffect(() => {
    if (!isAdmin(user)) return
    const fetchCount = () => {
      api.get<{ count: number }>('/bindery/count')
        .then(d => setBinderyCount(d.count))
        .catch(() => {})
    }
    fetchCount()
    const interval = setInterval(fetchCount, 30_000)
    return () => clearInterval(interval)
  }, [user])

  useEffect(() => {
    if (binderyCount > prevCountRef.current) {
      setBadgePulse(true)
      setTimeout(() => setBadgePulse(false), 1000)
    }
    prevCountRef.current = binderyCount
  }, [binderyCount])

  const activeLibrary = searchParams.get('library_id') ? Number(searchParams.get('library_id')) : null
  const activeSavedFilter = searchParams.get('saved_filter') ? Number(searchParams.get('saved_filter')) : null
  const isHomeTab = activeTab === 'home'
  const isAllBooks = !activeLibrary && !activeSavedFilter && activeTab === 'books'
  const isSeriesTab = activeTab === 'series'

  // Collapse and expand: the width moves (200 ms) over content that keeps its
  // full-width layout, clipped by the edge, so no label reflows. Expanding
  // shows the list at once and the edge uncovers it; collapsing keeps the
  // list (its labels fading) until the edge has passed, then the rail.
  const [moving, setMoving] = useState(false)
  const moveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => clearTimeout(moveTimer.current), [])
  function toggleOpen() {
    const next = !open
    setOpen(next)
    localStorage.setItem(SIDEBAR_KEY, next ? 'open' : 'closed')
    clearTimeout(moveTimer.current)
    // eslint-disable-next-line lingui/no-unlocalized-strings -- media query
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setMoving(false); return }
    setMoving(true)
    moveTimer.current = setTimeout(() => setMoving(false), 220)
  }
  const listShown = open || moving
  const closing = !open && moving

  // Navigate to the dashboard (pathname '/') with these params. Using navigate
  // (not setSearchParams) means these work from a non-dashboard page too - the
  // sidebar is now shared by Stats/Highlights/… via AppShell. On the dashboard
  // itself the resulting URL is identical, so behaviour there is unchanged.
  function goToBooks(params: Record<string, string>) {
    navigate({ pathname: '/', search: '?' + new URLSearchParams(params).toString() })
  }
  function selectAllBooks() { goToBooks({ tab: 'books' }) }
  function selectLibrary(id: number) { goToBooks({ tab: 'books', library_id: String(id) }) }
  function selectSavedFilter(sf: SavedFilter) {
    const params: Record<string, string> = { tab: 'books', saved_filter: String(sf.id) }
    Object.entries(sf.params).forEach(([k, v]) => { if (v) params[k] = v })
    goToBooks(params)
  }

  const navItems = ([
    { id: 'home', group: 'main', label: t`Home`, icon: Home, onSelect: onOpenHomeView, active: isHomeTab },
    { id: 'books', group: 'main', label: t`All books`, icon: BookOpen, onSelect: selectAllBooks, active: isAllBooks },
    { id: 'series', group: 'main', label: t`Series`, icon: Layers, onSelect: onOpenSeriesView, active: isSeriesTab },
    { id: 'stats', group: 'reading', label: t`Stats`, icon: BarChart3, to: '/stats' },
    { id: 'calendar', group: 'reading', label: t`Calendar`, icon: CalendarDays, to: '/calendar' },
    { id: 'highlights', group: 'reading', label: t`Highlights`, icon: Quote, to: '/highlights' },
    { id: 'wishlist', group: 'reading', label: t`Wishlist`, icon: Sparkles, to: '/wishlist', visible: isMember },
    { id: 'bindery', group: 'manage', label: t`Bindery`, icon: BookPlus, to: '/bindery', visible: isAdmin,
      badge: { count: binderyCount, pulse: badgePulse } },
    { id: 'hardcover', group: 'manage', label: t`Hardcover`, icon: BookMarked, to: '/hardcover', visible: isMember },
  ] satisfies NavItem[]).filter(item => item.visible?.(user) ?? true)

  // A library can stay selected under Series (the tab keeps library_id); its
  // tint then gets its own indicator so the two never fight over one.
  const navActive = navItems.some(i => ('to' in i ? location.pathname === i.to : i.active))
  const listGlideId = navActive ? 'glide-list' : 'glide'

  // Three groups: what you use daily, your reading, and the things you manage.
  const groups: { id: NavGroup; label: string | null }[] = [
    { id: 'main', label: null },
    { id: 'reading', label: t`Reading` },
    { id: 'manage', label: t`Manage` },
  ]
  const renderGroups = (variant: NavVariant, onActivate?: () => void) => groups.map(g => {
    const items = navItems.filter(i => i.group === g.id)
    if (items.length === 0) return null
    return (
      <div key={g.id}>
        {g.label && <GroupLabel>{g.label}</GroupLabel>}
        <div className="space-y-0.5">{items.map(item => <NavEntry key={item.id} item={item} variant={variant} onActivate={onActivate} />)}</div>
      </div>
    )
  })

  function openCreateLibModal() {
    setLibModalTitle(t`New library`)
    setLibModalInitialName('')
    setLibModalInitialIcon('Library')
    setLibModalInitialPublic(true)
    setLibModalLibraryId(null)
    setLibModalAssignedIds([])
    setLibModalOnSave(() => async (name: string, icon: string, isPublic: boolean) => {
      await api.post('/libraries', { name, icon, is_public: isPublic })
      onLibrariesChange()
    })
    setLibModalOpen(true)
  }

  function openEditLibModal(lib: Library) {
    setLibModalTitle(t`Edit library`)
    setLibModalInitialName(lib.name)
    setLibModalInitialIcon(lib.icon ?? 'Library')
    setLibModalInitialPublic(lib.is_public ?? true)
    setLibModalLibraryId(lib.id)
    setLibModalAssignedIds(lib.assigned_user_ids ?? [])
    setLibModalOnSave(() => async (name: string, icon: string, isPublic: boolean) => {
      await api.put(`/libraries/${lib.id}`, { name, icon, is_public: isPublic })
      onLibrariesChange()
    })
    setLibModalOpen(true)
  }

  const [shareShelf, setShareShelf] = useState<SavedFilter | null>(null)

  // Drag to reorder. The server keeps one order per library for everyone, and
  // skips libraries the caller may not edit, so only someone who may edit every
  // library in the list gets to drag (admins, or a member seeing only their own).
  const { toast } = useToast()
  const canReorderLibs = libraries.length > 1 && libraries.every(l => l.can_edit)
  const orderFailed = t`Could not save the new order`
  const libOrder = useDragOrder(libraries, async ids => {
    await api.post('/libraries/reorder', { ids })
    rememberOrder(user?.id, 'libraries', ids)
    onLibrariesChange()
  }, () => toast.error(orderFailed))
  const shelfOrder = useDragOrder(savedFilters, async ids => {
    await api.post('/saved-filters/reorder', { ids })
    rememberOrder(user?.id, 'savedFilters', ids)
    onSavedFiltersChange()
  }, () => toast.error(orderFailed))

  function openEditFilterModal(sf: SavedFilter) {
    setModalTitle(t`Edit shelf`)
    setModalInitialName(sf.name)
    setModalInitialIcon(sf.icon ?? 'Bookmark')
    setModalDefaultIcon('Bookmark')
    setModalOnSave(() => async (name: string, icon: string) => {
      await api.put(`/saved-filters/${sf.id}`, { name, icon })
      onSavedFiltersChange()
    })
    setModalOpen(true)
  }

  // `glide`: the active tint travels between rows. Off in the phone drawer: it
  // slides on a transform, and any pick there closes it anyway. `drag`: rows
  // are draggable; in the drawer only while it is open, since rows that measure
  // while the drawer jumps into place (reduced motion) would fly in after it.
  const libraryAndShelfSections = (glide: boolean, drag: boolean) => (
    <>
      <Section
        title={t`Libraries`}
        onAdd={openCreateLibModal} addLabel={t`New library`}
      >
        <DragList ids={libOrder.ids} enabled={canReorderLibs && drag} onReorder={libOrder.set} onDrop={libOrder.save}>
        {() => libOrder.shown.map(lib => (
          <DragRow key={lib.id} id={lib.id} enabled={canReorderLibs && drag}>{(grip, lifted) => (
          <SidebarItem
            grip={grip}
            label={lib.name}
            iconName={lib.icon ?? 'Library'}
            count={lib.book_count}
            active={activeLibrary === lib.id}
            glideId={glide && !lifted ? listGlideId : undefined}
            isPrivate={lib.is_public === false}
            onClick={() => { selectLibrary(lib.id); onMobileClose() }}
            onEdit={lib.can_edit ? () => openEditLibModal(lib) : undefined}
            onDelete={lib.can_edit ? async () => {
              await api.delete(`/libraries/${lib.id}`)
              if (activeLibrary === lib.id) selectAllBooks()
              onLibrariesChange()
            } : undefined}
          />
          )}</DragRow>
        ))}
        </DragList>
      </Section>

      {savedFilters.length > 0 && (
        <Section
          title={t`Shelves`}
        >
          <DragList ids={shelfOrder.ids} enabled={drag} onReorder={shelfOrder.set} onDrop={shelfOrder.save}>
          {() => shelfOrder.shown.map(sf => (
            <DragRow key={sf.id} id={sf.id} enabled={drag}>{(grip, lifted) => (
            <SidebarItem
              grip={grip}
              label={sf.name}
              iconName={sf.icon ?? 'Bookmark'}
              active={activeSavedFilter === sf.id}
              glideId={glide && !lifted ? listGlideId : undefined}
              onClick={() => { selectSavedFilter(sf); onMobileClose() }}
              onEdit={() => openEditFilterModal(sf)}
              onShare={() => setShareShelf(sf)}
              onDelete={async () => {
                await api.delete(`/saved-filters/${sf.id}`)
                if (activeSavedFilter === sf.id) selectAllBooks()
                onSavedFiltersChange()
              }}
            />
            )}</DragRow>
          ))}
          </DragList>
        </Section>
      )}
    </>
  )


  return (
    <>
      {/* eslint-disable-next-line lingui/no-unlocalized-strings -- CSS, not copy */}
      <style>{`
        @keyframes sidebar-jiggle {
          0%, 100% { transform: rotate(0deg); }
          20% { transform: rotate(-8deg); }
          40% { transform: rotate(6deg); }
          60% { transform: rotate(-4deg); }
          80% { transform: rotate(2deg); }
        }
        .sidebar-item-icon { transition: transform 0.15s ease; }
        .group:hover .sidebar-item-icon { animation: sidebar-jiggle 0.4s ease-in-out; }
        @keyframes badge-ping {
          0% { transform: scale(1); }
          50% { transform: scale(1.3); }
          100% { transform: scale(1); }
        }
      `}</style>
      {/* Mounted while open; AnimatePresence lets each dialog play its exit. */}
      <AnimatePresence>
      {modalOpen && (
        <EntityModal
          key="entity"
          title={modalTitle}
          initialName={modalInitialName}
          initialIcon={modalInitialIcon}
          defaultIcon={modalDefaultIcon}
          onSave={modalOnSave}
          onClose={() => setModalOpen(false)}
        />
      )}
      {shareShelf && (
        <ShareModal
          key="share"
          title={shareShelf.name}
          endpoint={`/shelves/${shareShelf.id}/share`}
          noun="shelf"
          onClose={() => setShareShelf(null)}
        />
      )}
      {libModalOpen && (
        <LibraryModal
          key="library"
          title={libModalTitle}
          initialName={libModalInitialName}
          initialIcon={libModalInitialIcon}
          initialIsPublic={libModalInitialPublic}
          libraryId={libModalLibraryId}
          initialAssignedIds={libModalAssignedIds}
          currentUserId={user?.id ?? null}
          onSave={libModalOnSave}
          onChanged={onLibrariesChange}
          onClose={() => setLibModalOpen(false)}
        />
      )}
      </AnimatePresence>
      <aside className={cn(
        // Part of the frame: no line against the content panel, which is set
        // into the frame colour. The wordmark lives here, full height.
        'shrink-0 flex-col bg-frame transition-[width] duration-200 ease-[var(--spring)] motion-reduce:transition-none',
        desktopHidden ? 'hidden' : 'hidden md:flex',
        open ? 'w-64' : 'w-14',
        moving && 'overflow-hidden',
      )} data-shell-sidebar data-closing={closing || undefined}>
        {/* The mark sits at the rail's centre in both states, so it never moves. */}
        <div className="group h-14 shrink-0 flex items-center gap-2 cursor-default w-64 px-[18px]">
          <TomeMark className="w-5 h-5 shrink-0 text-primary logo-bob" strokeWidth={7} />
          {listShown && <span className="sb-label font-semibold text-[14px] text-foreground">Tome</span>}
        </div>
        {!listShown && (
          <GlideGroup>
          <m.div layoutScroll className="isolate flex flex-col items-center flex-1 overflow-y-auto py-2 space-y-0.5 overscroll-contain">
            {navItems.map(item => <NavEntry key={item.id} item={item} variant="rail" />)}
            {libraries.length > 0 && (
              <div className="w-6 h-px bg-border my-0.5" />
            )}
            {libOrder.shown.map(lib => (
              <RailTip key={lib.id} label={lib.name}>
              <button
                onClick={() => selectLibrary(lib.id)}
                aria-label={lib.name}
                className={cn(
                  'group relative flex items-center justify-center w-9 h-9 rounded-lg transition-all touch-feedback',
                  activeLibrary === lib.id
                    ? 'bg-primary/10 text-primary glide-host'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                )}
              >
                {activeLibrary === lib.id && <Glide key={listGlideId} behind className="bg-primary/10" radius={8} layoutId={listGlideId} />}
                <span className="w-4 h-4 flex items-center justify-center group-hover:animate-[wiggle_0.4s_ease-in-out]">
                  {getIcon(lib.icon ?? 'Library', 'w-4 h-4')}
                </span>
              </button>
              </RailTip>
            ))}
            {savedFilters.length > 0 && (
              <div className="w-6 h-px bg-border my-0.5" />
            )}
            {shelfOrder.shown.map(sf => (
              <RailTip key={sf.id} label={sf.name}>
              <button
                onClick={() => selectSavedFilter(sf)}
                aria-label={sf.name}
                className={cn(
                  'group relative flex items-center justify-center w-9 h-9 rounded-lg transition-all touch-feedback',
                  activeSavedFilter === sf.id
                    ? 'bg-primary/10 text-primary glide-host'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                )}
              >
                {activeSavedFilter === sf.id && <Glide key={listGlideId} behind className="bg-primary/10" radius={8} layoutId={listGlideId} />}
                <span className="w-4 h-4 flex items-center justify-center group-hover:animate-[wiggle_0.4s_ease-in-out]">
                  {getIcon(sf.icon ?? 'Bookmark', 'w-4 h-4')}
                </span>
              </button>
              </RailTip>
            ))}
          </m.div>
          </GlideGroup>
        )}

        {listShown && (
          <GlideGroup>
          <m.nav layoutScroll className="isolate flex-1 w-64 shrink-0 overflow-y-auto px-3 pt-1 pb-4 overscroll-contain">
            {renderGroups('sidebar')}
            {libraryAndShelfSections(true, true)}
          </m.nav>
          </GlideGroup>
        )}

        {/* User profile footer - single trigger, popover on click */}
        {listShown && (
          <UserMenu user={user} logout={logout} onCollapse={toggleOpen} />
        )}
        {!listShown && (
          <CollapsedUserMenu user={user} logout={logout} onExpand={toggleOpen} />
        )}
      </aside>

      {/* Mobile sidebar overlay */}
      <div className={cn('md:hidden', !mobileOpen && 'pointer-events-none')}>
          {/* Backdrop */}
          <div
            className={cn(
              'fixed inset-0 z-40 bg-black/50 transition-opacity duration-200',
              mobileOpen ? 'opacity-100' : 'opacity-0'
            )}
            onClick={onMobileClose}
          />
          {/* Drawer panel */}
          <div
            className={cn(
              'fixed inset-y-0 left-0 z-50 w-72 flex flex-col bg-frame safe-top',
              'transition-transform duration-[350ms] ease-[var(--spring)] motion-reduce:transition-none',
              mobileOpen ? 'translate-x-0' : '-translate-x-full'
            )}
          >
            {/* Header with close button */}
            <div className="flex items-center justify-between px-5 h-14 shrink-0">
              <div className="flex items-center gap-2 group cursor-default">
                <TomeMark className="w-5 h-5 text-primary logo-bob" strokeWidth={7} />
                <span className="font-semibold text-sm">Tome</span>
              </div>
              <button
                onClick={onMobileClose}
                className="flex items-center justify-center w-8 h-8 text-muted-foreground hover:text-foreground transition-colors rounded-lg hover:bg-muted"
                aria-label={t`Close navigation`}
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* One scroll area for the nav and the account block. A pinned
                footer took half a phone screen, leaving the nav a sliver that
                ended at the Libraries header with the lists scrolled out of view. */}
            <m.div layoutScroll className="flex-1 min-h-0 overflow-y-auto overscroll-contain">
            <nav className="px-3 py-3">
              {renderGroups('drawer', onMobileClose)}
              {libraryAndShelfSections(false, mobileOpen)}
            </nav>

            {/* Mobile user footer */}
            <div className="mt-4 border-t border-border/70">
              {/* User info */}
              <div className="flex items-center gap-3 px-5 py-3">
                <div className="flex w-9 h-9 items-center justify-center rounded-full bg-primary text-[13px] font-bold text-primary-foreground shrink-0">
                  {(user?.username ?? '?').slice(0, 1).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{user?.username}</p>
                  {isAdmin(user) && <p className="text-[11px] text-muted-foreground"><Trans>Admin</Trans></p>}
                </div>
              </div>
              {/* Actions */}
              <div className="px-2 pb-3 space-y-0.5">
                <MobileThemeRow />
                <Link
                  to="/settings"
                  onClick={onMobileClose}
                  className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                >
                  <Settings className="w-5 h-5 shrink-0" />
                  <Trans>Settings</Trans>
                </Link>
                {isAdmin(user) && (
                  <Link
                    to="/admin"
                    onClick={onMobileClose}
                    className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                  >
                    <Shield className="w-5 h-5 shrink-0" />
                    <Trans>Admin</Trans>
                  </Link>
                )}
                <a
                  href={docsLink(DOCS.home)}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={onMobileClose}
                  className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                >
                  <BookOpen className="w-5 h-5 shrink-0" />
                  <span className="flex-1"><Trans>Docs</Trans></span>
                  <ExternalLink className="w-3.5 h-3.5 shrink-0 opacity-60" />
                </a>
                <button
                  onClick={() => { logout(); onMobileClose() }}
                  className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm text-destructive/80 hover:text-destructive hover:bg-destructive/10 transition-colors"
                >
                  <LogOut className="w-5 h-5 shrink-0" />
                  <Trans>Log out</Trans>
                </button>
              </div>
            </div>
            </m.div>
          </div>
        </div>
    </>
  )
}


type NavGroup = 'main' | 'reading' | 'manage'
type NavItem = {
  id: string
  group: NavGroup
  label: string
  icon: LucideIcon
  // Hides the entry from the nav; it does not restrict access.
  visible?: (user: AuthUser | null) => boolean
  badge?: { count: number; pulse: boolean }
} & ({ to: string } | { onSelect: () => void; active: boolean })

type NavVariant = 'rail' | 'sidebar' | 'drawer'

const NAV_VARIANTS: Record<NavVariant, { entryClassName: string; activeClassName: string; iconClassName: string; showLabel: boolean }> = {
  rail: {
    entryClassName: 'group relative flex items-center justify-center w-9 h-9 rounded-lg transition-all touch-feedback',
    activeClassName: 'bg-primary/10 text-primary',
    iconClassName: 'w-4 h-4',
    showLabel: false,
  },
  sidebar: {
    entryClassName: 'group flex items-center gap-2.5 w-full h-9 px-3 rounded-lg text-[14px] transition-colors touch-feedback',
    activeClassName: 'bg-primary/10 text-foreground font-medium [&>svg]:text-primary',
    iconClassName: 'w-4 h-4 shrink-0',
    showLabel: true,
  },
  drawer: {
    entryClassName: 'group flex items-center gap-2.5 w-full h-10 px-3 rounded-lg text-[15px] transition-colors touch-feedback',
    activeClassName: 'bg-primary/10 text-foreground font-medium [&>svg]:text-primary',
    iconClassName: 'w-4 h-4 shrink-0',
    showLabel: true,
  },
}

function NavEntry({ item, variant, onActivate }: { item: NavItem; variant: NavVariant; onActivate?: () => void }) {
  const { pathname } = useLocation()
  const style = NAV_VARIANTS[variant]
  const { icon: Icon, label, badge } = item
  const active = 'to' in item ? pathname === item.to : item.active
  const glide = variant !== 'drawer'
  const className = cn(style.entryClassName, 'relative', active ? cn(style.activeClassName, glide && 'glide-host') : 'text-muted-foreground hover:text-foreground hover:bg-muted')
  const labelProps = style.showLabel ? {} : { 'aria-label': label }
  const content = (
    <>
      {active && glide && <Glide behind className="bg-primary/10" radius={8} />}
      <Icon className={cn(style.iconClassName, 'group-hover:animate-[wiggle_0.4s_ease-in-out]')} />
      {style.showLabel && <span className="sb-label truncate">{label}</span>}
      <AnimatePresence initial={false}>
        {badge && badge.count > 0 && <NavBadge key="badge" variant={variant} {...badge} />}
      </AnimatePresence>
    </>
  )
  const entry = 'to' in item
    ? <Link to={item.to} onClick={onActivate} className={className} {...labelProps}>{content}</Link>
    : <button onClick={() => { item.onSelect(); onActivate?.() }} className={className} {...labelProps}>{content}</button>
  return style.showLabel ? entry : <RailTip label={label}>{entry}</RailTip>
}

/**
 * The name of an icon-only rail entry, beside it on hover and on keyboard
 * focus. Rendered into <body> at a fixed position: the rail scrolls, so
 * anything positioned inside it would be clipped.
 */
function RailTip({ label, children }: { label: string; children: ReactNode }) {
  // The place is kept after hiding, so the tip fades out where it stood.
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)
  const [shown, setShown] = useState(false)
  function show(el: Element | null) {
    if (!el) return
    const r = el.getBoundingClientRect()
    setAt({ x: r.right + 8, y: r.top + r.height / 2 })
    setShown(true)
  }
  const hide = () => setShown(false)
  return (
    <span
      className="contents"
      onMouseEnter={e => show(e.currentTarget.firstElementChild)}
      onMouseLeave={hide}
      // Keyboard focus only: a click focuses the entry too, and the tip
      // would then stay up after the pointer has left.
      onFocus={e => { if (e.target.matches(':focus-visible')) show(e.target) }}
      onBlur={hide}
      onPointerDown={hide}
    >
      {children}
      {at && createPortal(
        <Pop
          open={shown}
          origin="left"
          aria-hidden
          className="pointer-events-none fixed z-[60] -translate-y-1/2 whitespace-nowrap rounded-lg bg-card px-2.5 py-1 text-[13px] text-foreground shadow-lg ring-1 ring-panel-ring"
          style={{ left: at.x, top: at.y }}
        >
          {label}
        </Pop>,
        document.body,
      )}
    </span>
  )
}

// Grows in when the count goes above zero and shrinks away at zero (160 ms);
// a rising count runs to its new value with a short pulse.
const BADGE_IN = { initial: { scale: 0 }, animate: { scale: 1 }, exit: { scale: 0 }, transition: { duration: 0.16, ease: EASE } } as const

function NavBadge({ variant, count, pulse }: { variant: NavVariant; count: number; pulse: boolean }) {
  const pulseClassName = pulse && 'animate-[badge-ping_0.3s_ease-in-out] motion-reduce:animate-none'
  // The rail is icon-only, too narrow for a number.
  if (variant === 'rail') {
    return (
      <m.span {...BADGE_IN} className="absolute top-1 right-1 w-1.5 h-1.5">
        {/* An <i>, so the link holds one span: the rail badge. */}
        <i className={cn('block w-full h-full rounded-full bg-primary', pulseClassName)} />
      </m.span>
    )
  }
  return (
    <m.span {...BADGE_IN} className="ml-auto grid">
      <span className={cn('min-w-5 h-5 px-1.5 grid place-items-center rounded-full bg-primary text-primary-foreground text-[12px] font-semibold tabular-nums', pulseClassName)}>
        <RunningNumber value={count} />
      </span>
    </m.span>
  )
}


const THEME_OPTIONS: { id: ThemeId; icon: typeof Sun; label: MessageDescriptor }[] = [
  { id: 'light', icon: Sun, label: msg`Light` },
  { id: 'dark', icon: Moon, label: msg`Dark` },
  { id: 'black', icon: MoonStar, label: msg`Black` },
  { id: 'amber', icon: Flame, label: msg`Amber` },
  { id: 'ember', icon: Coffee, label: msg`Ember` },
]

function ThemeMenuItems({ itemClass }: { itemClass: string }) {
  const { i18n } = useLingui()
  const [current, setCurrent] = useState(getStoredTheme)
  return (
    <>
      {THEME_OPTIONS.map(({ id, icon: Icon, label }) => (
        <Fragment key={id}>
          {/* hairline between the neutral core and the warm pair */}
          {id === 'amber' && <div className="h-px bg-border my-1" />}
          <button
            onClick={() => { switchTheme(id); setCurrent(id) }}
            className={itemClass}
          >
            <Icon className="w-4 h-4 shrink-0" />
            {i18n._(label)}
            {current === id && <Check className="w-3.5 h-3.5 shrink-0 text-primary ml-auto" />}
          </button>
        </Fragment>
      ))}
    </>
  )
}

function MobileThemeRow() {
  const { t, i18n } = useLingui()
  const [current, setCurrent] = useState(getStoredTheme)
  return (
    <div className="flex items-center gap-1 px-3 py-1.5">
      <span className="flex-1 text-sm text-muted-foreground"><Trans>Theme</Trans></span>
      {THEME_OPTIONS.map(({ id, icon: Icon, label }) => {
        const themeName = i18n._(label)
        return (
        <button
          key={id}
          onClick={() => { switchTheme(id); setCurrent(id) }}
          title={themeName}
          aria-label={t`${themeName} theme`}
          className={cn(
            'flex items-center justify-center w-9 h-9 rounded-lg transition-colors touch-feedback',
            current === id
              ? 'bg-primary/10 text-primary'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted'
          )}
        >
          <Icon className="w-4 h-4" />
        </button>
        )
      })}
    </div>
  )
}

function CollapsedUserMenu({ user, logout, onExpand }: { user: { username: string; is_admin?: boolean; role?: string } | null; logout: () => void; onExpand: () => void }) {
  const { t } = useLingui()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])
  const close = useCallback(() => setOpen(false), [])
  useEscape(open, close)

  const menuItem = 'flex items-center gap-2.5 w-full h-8 px-3 text-[14px] rounded-lg transition-colors hover:bg-secondary text-foreground/85 hover:text-foreground touch-feedback'
  const destructive = menuItem + ' text-destructive/80 hover:text-destructive hover:bg-destructive/10'

  return (
    <div ref={ref} className="relative shrink-0 flex flex-col items-center gap-1 py-3">
      <RailTip label={t`Expand sidebar`}>
      <button
        onClick={onExpand}
        aria-label={t`Expand sidebar`}
        className="flex items-center justify-center w-9 h-8 rounded-lg text-muted-foreground/60 hover:text-foreground hover:bg-secondary transition-colors touch-feedback"
      >
        <PanelLeftOpen className="w-4 h-4" />
      </button>
      </RailTip>
      <RailTip label={user?.username ?? t`User menu`}>
      <button
        onClick={() => setOpen(o => !o)}
        aria-label={user?.username ?? t`User menu`}
        className="flex items-center justify-center w-10 h-10 rounded-lg hover:bg-secondary transition-colors touch-feedback"
      >
        <div className="flex w-8 h-8 items-center justify-center rounded-full bg-primary text-[12px] font-bold text-primary-foreground">
          {(user?.username ?? '?').slice(0, 1).toUpperCase()}
        </div>
      </button>
      </RailTip>
      <Pop open={open} origin="bottom-left" className="absolute bottom-full left-1 mb-1 rounded-xl bg-card shadow-lg ring-1 ring-panel-ring p-1.5 z-50 w-48">
          <Link to="/settings" onClick={() => setOpen(false)} className={menuItem}>
            <Settings className="w-4 h-4 shrink-0" />
            <Trans>Settings</Trans>
          </Link>
          {(user?.is_admin || user?.role === 'admin') && (
            <Link to="/admin" onClick={() => setOpen(false)} className={menuItem}>
              <Shield className="w-4 h-4 shrink-0" />
              <Trans>Admin</Trans>
            </Link>
          )}
          <a href={docsLink(DOCS.home)} target="_blank" rel="noopener noreferrer" onClick={() => setOpen(false)} className={menuItem}>
            <BookOpen className="w-4 h-4 shrink-0" />
            <span className="flex-1"><Trans>Docs</Trans></span>
            <ExternalLink className="w-3 h-3 shrink-0 opacity-60" />
          </a>
          <div className="my-1 h-px bg-border/70 mx-2" />
          <ThemeMenuItems itemClass={menuItem} />
          <div className="my-1 h-px bg-border/70 mx-2" />
          <button onClick={logout} className={destructive}>
            <LogOut className="w-4 h-4 shrink-0" />
            <Trans>Log out</Trans>
          </button>
      </Pop>
    </div>
  )
}

function UserMenu({ user, logout, onCollapse }: { user: { username: string; is_admin?: boolean; role?: string } | null; logout: () => void; onCollapse: () => void }) {
  const { t } = useLingui()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])
  const close = useCallback(() => setOpen(false), [])
  useEscape(open, close)

  const menuItem = 'flex items-center gap-2.5 w-full h-8 px-3 text-[14px] rounded-lg transition-colors hover:bg-secondary text-foreground/85 hover:text-foreground touch-feedback'
  const destructive = menuItem + ' text-destructive/80 hover:text-destructive hover:bg-destructive/10'

  return (
    <div ref={ref} className="relative shrink-0 w-64 px-3 py-3 flex items-center gap-1">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-3 flex-1 min-w-0 px-1 py-1 rounded-lg hover:bg-secondary transition-colors text-left touch-feedback"
      >
        <div className="flex w-8 h-8 items-center justify-center rounded-full bg-primary text-[12px] font-bold text-primary-foreground shrink-0">
          {(user?.username ?? '?').slice(0, 1).toUpperCase()}
        </div>
        <span className="sb-label min-w-0 flex-1">
          <span className="block truncate text-[14px] font-medium text-foreground">{user?.username}</span>
          {(user?.is_admin || user?.role === 'admin') && <span className="block text-[11px] leading-none text-muted-foreground"><Trans>Admin</Trans></span>}
        </span>
        <ChevronsUpDown className="w-3.5 h-3.5 text-muted-foreground/50 shrink-0" />
      </button>
      <button
        onClick={onCollapse}
        title={t`Collapse sidebar`}
        aria-label={t`Collapse sidebar`}
        className="flex items-center justify-center w-8 h-8 rounded-lg shrink-0 text-muted-foreground/60 hover:text-foreground hover:bg-secondary transition-colors touch-feedback"
      >
        <PanelLeftClose className="w-4 h-4" />
      </button>

      <Pop open={open} origin="bottom" className="absolute bottom-full left-3 right-3 mb-1 rounded-xl bg-card shadow-lg ring-1 ring-panel-ring p-1.5 z-50">
          <Link to="/settings" onClick={() => setOpen(false)} className={menuItem}>
            <Settings className="w-4 h-4 shrink-0" />
            <Trans>Settings</Trans>
          </Link>
          {(user?.is_admin || user?.role === 'admin') && (
            <Link to="/admin" onClick={() => setOpen(false)} className={menuItem}>
              <Shield className="w-4 h-4 shrink-0" />
              <Trans>Admin</Trans>
            </Link>
          )}
          <a href={docsLink(DOCS.home)} target="_blank" rel="noopener noreferrer" onClick={() => setOpen(false)} className={menuItem}>
            <BookOpen className="w-4 h-4 shrink-0" />
            <span className="flex-1"><Trans>Docs</Trans></span>
            <ExternalLink className="w-3 h-3 shrink-0 opacity-60" />
          </a>
          <div className="my-1 h-px bg-border/70 mx-2" />
          <ThemeMenuItems itemClass={menuItem} />
          <div className="my-1 h-px bg-border/70 mx-2" />
          <button onClick={logout} className={destructive}>
            <LogOut className="w-4 h-4 shrink-0" />
            <Trans>Log out</Trans>
          </button>
      </Pop>
    </div>
  )
}

/** A group label in the nav: small caps, no icon. */
function GroupLabel({ children }: { children: React.ReactNode }) {
  return <div className="sb-label px-3 mt-5 mb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">{children}</div>
}

function Section({ title, onAdd, addLabel, onTitleClick, children }: {
  title: string
  onAdd?: () => void
  /** Accessible label for the add button, e.g. "New library". */
  addLabel?: string
  onTitleClick?: () => void
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="sb-label flex items-center justify-between px-3 mt-5 mb-1">
        <button
          onClick={onTitleClick}
          className={cn(
            'text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80 transition-colors',
            onTitleClick && 'hover:text-foreground cursor-pointer touch-feedback'
          )}
        >
          {title}
        </button>
        {onAdd && (
          <button
            onClick={onAdd}
            className="w-5 h-5 grid place-items-center rounded text-muted-foreground/70 hover:text-foreground hover:bg-secondary transition-colors touch-feedback"
            title={addLabel}
            aria-label={addLabel}
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  )
}

function SidebarItem({ grip, label, iconName, count, active, glideId, isPrivate, onClick, onEdit, onShare, onDelete }: {
  grip?: ReactNode
  label: string
  glideId?: string
  iconName: string
  count?: number
  active: boolean
  isPrivate?: boolean
  onClick: () => void
  onEdit?: () => void
  onShare?: () => void
  onDelete?: () => Promise<void>
}) {
  const { t } = useLingui()
  return (
    <div
      className={cn(
        'group relative flex items-center gap-2.5 w-full h-9 px-3 rounded-lg text-[14px] transition-colors cursor-pointer touch-feedback',
        active
          ? cn('bg-primary/10 text-foreground font-medium [&_svg]:text-primary', glideId && 'glide-host')
          : 'text-muted-foreground hover:text-foreground hover:bg-muted'
      )}
      onClick={onClick}
    >
      {/* Keyed by its id: a row whose indicator switches ids must mount a new one, not rename it. */}
      {active && glideId && <Glide key={glideId} behind className="bg-primary/10" radius={8} layoutId={glideId} />}
      {grip}
      <span className="shrink-0 sidebar-item-icon w-4 h-4 grid place-items-center">{getIcon(iconName, 'w-4 h-4')}</span>
      <span className="sb-label truncate flex-1">{label}</span>
      {isPrivate && (
        <span title={t`Private library`} className="shrink-0 flex items-center group-hover:hidden group-focus-within:hidden">
          <Lock className="w-3 h-3 text-muted-foreground/50" />
        </span>
      )}
      {/* A ghost "0" on an empty library was pure noise - no count reads cleaner. */}
      {count != null && count > 0 && !isPrivate && (
        <span className="sb-label text-[12px] tabular-nums shrink-0 text-muted-foreground/70 group-hover:hidden group-focus-within:hidden">
          <RunningNumber value={count} />
        </span>
      )}
      <div className="hidden group-hover:flex group-focus-within:flex items-center gap-0.5 shrink-0">
        {onEdit && (
          <button
            onClick={e => { e.stopPropagation(); onEdit() }}
            className="p-0.5 rounded hover:text-foreground transition-colors touch-feedback"
            title={t`Edit`}
            aria-label={t`Edit`}
          >
            <Pencil className="w-3 h-3" />
          </button>
        )}
        {onShare && (
          <button
            onClick={e => { e.stopPropagation(); onShare() }}
            className="p-0.5 rounded hover:text-foreground transition-colors touch-feedback"
            title={t`Share`}
            aria-label={t`Share`}
          >
            <Share2 className="w-3 h-3" />
          </button>
        )}
        {onDelete && (
          <button
            onClick={async e => { e.stopPropagation(); await onDelete() }}
            className="p-0.5 rounded hover:text-destructive transition-colors touch-feedback"
            title={t`Delete`}
            aria-label={t`Delete`}
          >
            <Trash2 className="w-3 h-3" />
          </button>
        )}
      </div>
    </div>
  )
}

type ShareUser = { id: number; username: string; role: string }

function LibraryModal({ title, initialName, initialIcon, initialIsPublic, libraryId, initialAssignedIds, currentUserId, onSave, onChanged, onClose }: {
  title: string
  initialName?: string
  initialIcon?: string
  initialIsPublic?: boolean
  libraryId?: number | null
  initialAssignedIds?: number[]
  currentUserId?: number | null
  onSave: (name: string, icon: string, isPublic: boolean) => Promise<void>
  onChanged?: () => void
  onClose: () => void
}) {
  const { t } = useLingui()
  const [name, setName] = useState(initialName ?? '')
  const [icon, setIcon] = useState(initialIcon ?? 'Library')
  const [isPublic, setIsPublic] = useState(initialIsPublic ?? true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // Share-with-users state (only meaningful when editing an existing, private library)
  const [users, setUsers] = useState<ShareUser[]>([])
  const [assignedIds, setAssignedIds] = useState<Set<number>>(new Set(initialAssignedIds ?? []))
  const [busyUserId, setBusyUserId] = useState<number | null>(null)
  const canShare = libraryId != null && !isPublic

  useEffect(() => {
    if (libraryId == null) return
    api.get<ShareUser[]>('/users/list')
      .then(list => setUsers(list.filter(u => u.id !== currentUserId)))
      .catch(() => {})
  }, [libraryId, currentUserId])

  async function toggleUser(userId: number) {
    if (libraryId == null || busyUserId != null) return
    setBusyUserId(userId)
    const isAssigned = assignedIds.has(userId)
    try {
      if (isAssigned) {
        await api.delete(`/libraries/${libraryId}/users/${userId}`)
        setAssignedIds(prev => { const next = new Set(prev); next.delete(userId); return next })
      } else {
        await api.post(`/libraries/${libraryId}/users`, { user_id: userId })
        setAssignedIds(prev => new Set(prev).add(userId))
      }
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Failed to update sharing`)
    } finally {
      setBusyUserId(null)
    }
  }

  useEffect(() => { inputRef.current?.focus() }, [])
  const nameId = useId()

  async function handleSave() {
    if (!name.trim() || saving) return
    setSaving(true)
    setError('')
    try {
      await onSave(name.trim(), icon, isPublic)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Failed to save`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <ModalShell
      open
      onClose={onClose}
      size="sm"
      title={title}
      footer={
        <>
          <Pill variant="quiet" onClick={onClose}><Trans>Cancel</Trans></Pill>
          <Pill variant="primary" onClick={handleSave} disabled={saving || !name.trim()}>
            {saving && <Loader2 className="w-4 h-4 animate-spin" />}
            <Trans>Save</Trans>
          </Pill>
        </>
      }
    >
      <div className="flex flex-col gap-4 pt-1">
        <div>
          <FormLabel htmlFor={nameId}><Trans>Name</Trans></FormLabel>
          <Input
            id={nameId}
            ref={inputRef}
            value={name}
            onChange={e => { setName(e.target.value); setError('') }}
            onKeyDown={e => { if (e.key === 'Enter') handleSave() }}
            invalid={!!error}
          />
        </div>
        <div>
          <FormLabel><Trans>Icon</Trans></FormLabel>
          <IconPicker value={icon} onChange={setIcon} inline />
        </div>
        <Checkbox
          checked={isPublic}
          onChange={e => setIsPublic(e.target.checked)}
          label={<Trans>Public library</Trans>}
          description={isPublic
            ? <Trans>Everyone on this server can see its books.</Trans>
            : <span className="inline-flex items-center gap-1"><Lock className="w-3 h-3" /><Trans>Private. Only assigned users can access it.</Trans></span>}
        />
        {canShare && (
          <div>
            <FormLabel className="flex items-center gap-1.5"><Users className="w-3.5 h-3.5" /><Trans>Share with users</Trans></FormLabel>
            {users.length === 0 ? (
              <p className="text-[13px] text-muted-foreground"><Trans>No other users to share with.</Trans></p>
            ) : (
              <div className="max-h-48 overflow-y-auto">
                {users.map(u => (
                  <div key={u.id} className="flex items-center py-2.5 border-b border-border/70 last:border-0">
                    <Checkbox
                      checked={assignedIds.has(u.id)}
                      onChange={() => toggleUser(u.id)}
                      disabled={busyUserId === u.id}
                      label={u.username}
                      className="flex-1 min-w-0"
                    />
                    {busyUserId === u.id && <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {!isPublic && libraryId == null && (
          <p className="text-[13px] text-muted-foreground"><Trans>Save the library first, then reopen it to share with users.</Trans></p>
        )}
        {error && <p className="text-[13px] text-destructive">{error}</p>}
      </div>
    </ModalShell>
  )
}

/**
 * Live order for a dragged list. `shown` follows the drag; on drop the new
 * order is saved, and a failed save puts the server's order back.
 */
function useDragOrder<T extends { id: number }>(items: T[], save: (ids: number[]) => Promise<void>, onFail: () => void) {
  const [order, setOrder] = useState<number[] | null>(null)
  const ids = items.map(i => i.id)
  const live = order != null && order.length === ids.length && order.every(id => ids.includes(id)) ? order : null
  const shown = live ? live.map(id => items.find(i => i.id === id)!) : items
  const latest = useRef<number[] | null>(null)
  return {
    ids: live ?? ids,
    shown,
    set: (next: number[]) => { latest.current = next; setOrder(next) },
    save: () => {
      const next = latest.current
      if (!next || next.length !== ids.length || !next.every(id => ids.includes(id))) return
      if (next.every((id, i) => id === ids[i])) return
      save(next).catch(() => { latest.current = null; setOrder(null); onFail() })
    },
  }
}

type DragCtx = { ids: number[]; onReorder: (ids: number[]) => void; onDrop: () => void }
const DragDropContext = createContext<DragCtx>({ ids: [], onReorder: () => {}, onDrop: () => {} })

/** A sidebar list whose rows can be dragged into a new order. */
function DragList({ ids, enabled, onReorder, onDrop, children }: {
  ids: number[]
  enabled: boolean
  onReorder: (ids: number[]) => void
  onDrop: () => void
  children: () => ReactNode
}) {
  if (!enabled) return <>{children()}</>
  return (
    <DragDropContext.Provider value={{ ids, onReorder, onDrop }}>
      {/* relative: the rows' offsetTop is measured against this box. */}
      <div className="relative space-y-0.5" data-drag-list>{children()}</div>
    </DragDropContext.Provider>
  )
}

/**
 * One draggable row. A mouse drags the whole row once it moves 3 px, so a
 * plain click still navigates; touch drags only from the grip, so the drawer
 * still scrolls under a finger. The dragged row follows the pointer on a
 * transform with a slight lift; the others step aside (layout="position",
 * measured only when the order changes) when its centre crosses their middle;
 * on release it settles into its slot with the GLIDE spring.
 */
function DragRow({ id, enabled, children }: { id: number; enabled: boolean; children: (grip: ReactNode, lifted: boolean) => ReactNode }) {
  const { t } = useLingui()
  const { ids, onReorder, onDrop } = useContext(DragDropContext)
  const ref = useRef<HTMLDivElement>(null)
  const dragged = useRef(false)
  // Pointer offset from the row's slot while dragging; null at rest.
  const [ty, setTy] = useState<number | null>(null)
  const want = useRef(0)
  const order = ids.join(',')

  // After a reorder moves this row in the DOM, re-aim it at the pointer.
  useLayoutEffect(() => {
    const el = ref.current
    if (ty == null || !el) return
    setTy(want.current - el.offsetTop)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order])

  if (!enabled) return <>{children(null, false)}</>

  function begin(e: React.PointerEvent, threshold: number) {
    const el = ref.current
    const list = el?.closest<HTMLElement>('[data-drag-list]')
    if (!el || !list) return
    const startY = e.clientY
    let active = false
    let grab = 0
    let current = ids
    const move = (ev: PointerEvent) => {
      if (!active) {
        if (Math.abs(ev.clientY - startY) < threshold) return
        active = true
        dragged.current = true
        grab = startY - el.getBoundingClientRect().top
        document.body.style.userSelect = 'none'
      }
      ev.preventDefault()
      const box = list.getBoundingClientRect()
      const h = el.offsetHeight
      const top = Math.max(-6, Math.min(box.height - h + 6, ev.clientY - box.top - grab))
      want.current = top
      const mid = top + h / 2
      const rows = Array.from(list.children).filter((c): c is HTMLElement => c !== el && c instanceof HTMLElement)
      const target = rows.filter(r => r.offsetTop + r.offsetHeight / 2 < mid).length
      const others = current.filter(x => x !== id)
      const next = [...others.slice(0, target), id, ...others.slice(target)]
      if (next.some((x, i) => x !== current[i])) { current = next; onReorder(next) }
      setTy(top - el.offsetTop)
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
      if (!active) return
      document.body.style.userSelect = ''
      setTy(null)
      onDrop()
      // The click that follows this pointerup must not navigate.
      setTimeout(() => { dragged.current = false }, 0)
    }
    window.addEventListener('pointermove', move, { passive: false })
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
  }

  const lifted = ty != null
  const grip = (
    <>
      {/* Mouse: a hint in the left padding on hover. */}
      <span aria-hidden className="pointer-events-none absolute left-0 inset-y-0 w-3 grid place-items-center opacity-0 group-hover:opacity-50 transition-opacity pointer-coarse:hidden">
        <GripVertical className="w-3 h-3" />
      </span>
      {/* Touch: a real handle at the end of the row. */}
      <span
        aria-hidden
        title={t`Drag to reorder`}
        onPointerDown={e => { e.stopPropagation(); begin(e, 3) }}
        onClick={e => e.stopPropagation()}
        className="hidden pointer-coarse:grid order-last -mr-2 w-8 self-stretch place-items-center text-muted-foreground/60 touch-none"
      >
        <GripVertical className="w-4 h-4" />
      </span>
    </>
  )
  return (
    <m.div
      ref={ref}
      layout={lifted ? false : 'position'}
      layoutDependency={order}
      animate={{ y: ty ?? 0, scale: lifted ? 1.03 : 1 }}
      transition={lifted ? { duration: 0 } : GLIDE}
      onPointerDown={e => { if (e.pointerType === 'mouse' && e.button === 0) begin(e, 3) }}
      onClickCapture={e => { if (dragged.current) { e.stopPropagation(); e.preventDefault() } }}
      style={{ borderRadius: 8, position: 'relative', zIndex: lifted ? 3 : undefined }}
      className={cn('transition-[background-color,box-shadow] duration-150', lifted && 'bg-card text-foreground shadow-[0_14px_28px_-10px_rgb(0_0_0/0.55)]')}
    >
      {children(grip, lifted)}
    </m.div>
  )
}
