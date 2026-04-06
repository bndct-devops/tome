import { useState, useRef, useEffect, useMemo } from 'react'
import { useSearchParams, useLocation, Link } from 'react-router-dom'
import * as LucideIcons from 'lucide-react'
import {
  BookOpen, Plus, Pencil, Trash2,
  ChevronLeft, ChevronRight, Bookmark, Library as LibraryIcon, Layers, Home, BarChart3,
  Settings, Shield, LogOut, ChevronsUpDown, Sun, Moon, Lock, X, BookPlus,
  type LucideIcon,
} from 'lucide-react'
import { api } from '@/lib/api'
import type { Library, SavedFilter } from '@/lib/books'
import { cn } from '@/lib/utils'
import { EntityModal } from '@/components/EntityModal'
import { useAuth } from '@/contexts/AuthContext'
import { applyTheme, getStoredTheme, THEMES } from '@/lib/theme'

const SIDEBAR_KEY = 'tome_sidebar'

function isLucideIcon(val: unknown): val is LucideIcon {
  return typeof val === 'object' && val !== null && 'render' in val
}

export const ICON_MAP: Record<string, LucideIcon> = Object.fromEntries(
  Object.entries(LucideIcons).filter(
    ([name, val]) => /^[A-Z]/.test(name) && isLucideIcon(val) && !name.endsWith('Icon')
  ) as [string, LucideIcon][]
)

export function getIcon(name?: string | null, className = 'w-3.5 h-3.5') {
  const Comp = ICON_MAP[name ?? ''] ?? LibraryIcon
  return <Comp className={className} />
}

const ALL_ICONS: [string, LucideIcon][] = Object.entries(LucideIcons).filter(
  ([name, val]) => /^[A-Z]/.test(name) && isLucideIcon(val) && !name.endsWith('Icon')
) as [string, LucideIcon][]

export function IconPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    setTimeout(() => searchRef.current?.focus(), 0)
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    const results = q ? ALL_ICONS.filter(([name]) => name.toLowerCase().includes(q)) : ALL_ICONS
    return results.slice(0, 96)
  }, [search])

  const Curr = (ICON_MAP[value] ?? LibraryIcon) as LucideIcon
  return (
    <div className="relative shrink-0" ref={ref}>
      <button
        type="button"
        onClick={() => { setOpen(o => !o); setSearch('') }}
        className="flex items-center justify-center w-7 h-7 rounded-md border border-border bg-muted hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors"
        title="Choose icon"
        aria-label="Choose icon"
      >
        <Curr className="w-3.5 h-3.5" />
      </button>
      {open && (
        <div className="absolute left-0 top-full mt-1 z-50 bg-card border border-border rounded-lg shadow-xl p-2 w-72">
          <input
            ref={searchRef}
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search icons…"
            className="w-full mb-2 px-2 py-1 text-xs rounded-md border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <div className="grid grid-cols-8 gap-1 max-h-[300px] overflow-y-auto">
            {filtered.map(([name, Comp]) => (
              <button
                key={name}
                type="button"
                onClick={() => { onChange(name); setOpen(false) }}
                className={cn(
                  'flex items-center justify-center w-7 h-7 rounded hover:bg-muted transition-colors',
                  value === name && 'bg-primary/15 text-primary'
                )}
                title={name}
              >
                <Comp className="w-3.5 h-3.5" />
              </button>
            ))}
          </div>
          {!search && <p className="text-[10px] text-muted-foreground text-center mt-1.5">Search to find more icons</p>}
        </div>
      )}
    </div>
  )
}

interface Props {
  libraries: Library[]
  savedFilters: SavedFilter[]
  activeTab: 'home' | 'books' | 'series'
  onLibrariesChange: () => void
  onSavedFiltersChange: () => void
  onOpenSeriesView: () => void
  onOpenHomeView: () => void
  mobileOpen: boolean
  onMobileClose: () => void
}

export function Sidebar({ libraries, savedFilters, activeTab, onLibrariesChange, onSavedFiltersChange, onOpenSeriesView, onOpenHomeView, mobileOpen, onMobileClose }: Props) {
  const [open, setOpen] = useState(() => localStorage.getItem(SIDEBAR_KEY) !== 'closed')
  const [searchParams, setSearchParams] = useSearchParams()
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

  const location = useLocation()

  // Close mobile drawer on navigation
  useEffect(() => {
    onMobileClose()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, location.search])

  // Lock body scroll when mobile drawer is open
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [mobileOpen])

  const [binderyCount, setBinderyCount] = useState(0)
  const prevCountRef = useRef(0)
  const [badgePulse, setBadgePulse] = useState(false)

  useEffect(() => {
    if (!user?.is_admin && !user?.permissions?.can_approve_bindery) return
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

  function toggleOpen() {
    const next = !open
    setOpen(next)
    localStorage.setItem(SIDEBAR_KEY, next ? 'open' : 'closed')
  }

  function selectAllBooks() { setSearchParams({ tab: 'books' }) }
  function selectLibrary(id: number) { setSearchParams({ tab: 'books', library_id: String(id) }) }
  function selectSavedFilter(sf: SavedFilter) {
    const params: Record<string, string> = { tab: 'books', saved_filter: String(sf.id) }
    Object.entries(sf.params).forEach(([k, v]) => { if (v) params[k] = v })
    setSearchParams(params)
  }

  function openCreateLibModal() {
    setLibModalTitle('New Library')
    setLibModalInitialName('')
    setLibModalInitialIcon('Library')
    setLibModalInitialPublic(true)
    setLibModalOnSave(() => async (name: string, icon: string, isPublic: boolean) => {
      await api.post('/libraries', { name, icon, is_public: isPublic })
      onLibrariesChange()
    })
    setLibModalOpen(true)
  }

  function openEditLibModal(lib: Library) {
    setLibModalTitle('Edit Library')
    setLibModalInitialName(lib.name)
    setLibModalInitialIcon(lib.icon ?? 'Library')
    setLibModalInitialPublic(lib.is_public ?? true)
    setLibModalOnSave(() => async (name: string, icon: string, isPublic: boolean) => {
      await api.put(`/libraries/${lib.id}`, { name, icon, is_public: isPublic })
      onLibrariesChange()
    })
    setLibModalOpen(true)
  }

  function openEditFilterModal(sf: SavedFilter) {
    setModalTitle('Edit Saved Filter')
    setModalInitialName(sf.name)
    setModalInitialIcon(sf.icon ?? 'Bookmark')
    setModalDefaultIcon('Bookmark')
    setModalOnSave(() => async (name: string, icon: string) => {
      await api.put(`/saved-filters/${sf.id}`, { name, icon })
      onSavedFiltersChange()
    })
    setModalOpen(true)
  }


  return (
    <>
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
      {modalOpen && (
        <EntityModal
          title={modalTitle}
          initialName={modalInitialName}
          initialIcon={modalInitialIcon}
          defaultIcon={modalDefaultIcon}
          onSave={modalOnSave}
          onClose={() => setModalOpen(false)}
        />
      )}
      {libModalOpen && (
        <LibraryModal
          title={libModalTitle}
          initialName={libModalInitialName}
          initialIcon={libModalInitialIcon}
          initialIsPublic={libModalInitialPublic}
          onSave={libModalOnSave}
          onClose={() => setLibModalOpen(false)}
        />
      )}
      <aside className={cn(
        'hidden md:flex shrink-0 flex-col border-r border-border bg-card/30 transition-all duration-200',
        open ? 'w-52' : 'w-10'
      )}>
        <button
          onClick={toggleOpen}
          className="flex items-center justify-center h-10 w-10 shrink-0 text-muted-foreground hover:text-foreground transition-colors self-end"
          title={open ? 'Collapse sidebar' : 'Expand sidebar'}
        >
          {open ? <ChevronLeft className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>

        {open && (
          <nav className="flex-1 overflow-y-auto px-2 pb-4 space-y-4 overscroll-contain">
            <div>
              <button
                onClick={onOpenHomeView}
                className={cn(
                  'group flex items-center gap-2 w-full px-2 py-1.5 rounded-lg text-sm transition-all',
                  isHomeTab
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                )}
              >
                <Home className="w-4 h-4 shrink-0 group-hover:animate-[wiggle_0.4s_ease-in-out]" />
                <span className="truncate">Home</span>
              </button>
              <button
                onClick={selectAllBooks}
                className={cn(
                  'group flex items-center gap-2 w-full px-2 py-1.5 rounded-lg text-sm transition-all',
                  isAllBooks
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                )}
              >
                <BookOpen className="w-4 h-4 shrink-0 group-hover:animate-[wiggle_0.4s_ease-in-out]" />
                <span className="truncate">All Books</span>
              </button>
              <button
                onClick={onOpenSeriesView}
                className={cn(
                  'group flex items-center gap-2 w-full px-2 py-1.5 rounded-lg text-sm transition-all',
                  isSeriesTab
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                )}
              >
                <Layers className="w-4 h-4 shrink-0 group-hover:animate-[wiggle_0.4s_ease-in-out]" />
                <span className="truncate">Series</span>
              </button>
              <Link
                to="/stats"
                className={cn(
                  'group flex items-center gap-2 w-full px-2 py-1.5 rounded-lg text-sm transition-all',
                  location.pathname === '/stats'
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                )}
              >
                <BarChart3 className="w-4 h-4 shrink-0 group-hover:animate-[wiggle_0.4s_ease-in-out]" />
                <span className="truncate">Stats</span>
              </Link>
              {(user?.is_admin || user?.permissions?.can_approve_bindery) && (
                <Link
                  to="/bindery"
                  className={cn(
                    'group flex items-center gap-2 w-full px-2 py-1.5 rounded-lg text-sm transition-all',
                    location.pathname === '/bindery'
                      ? 'bg-primary/10 text-primary font-medium'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  )}
                >
                  <BookPlus className="w-4 h-4 shrink-0 group-hover:animate-[wiggle_0.4s_ease-in-out]" />
                  <span className="truncate">Bindery</span>
                  {binderyCount > 0 && (
                    <span
                      className="ml-auto text-[10px] font-medium bg-primary/15 text-primary px-1.5 py-0.5 rounded-full"
                      style={{ animation: badgePulse ? 'badge-ping 0.4s ease-in-out' : 'none' }}
                    >
                      {binderyCount}
                    </span>
                  )}
                </Link>
              )}
            </div>

            <Section
              title="Libraries"
              icon={<LibraryIcon className="w-3 h-3" />}
              onAdd={openCreateLibModal}
            >
              {libraries.map(lib => (
                <SidebarItem
                  key={lib.id}
                  label={lib.name}
                  iconName={lib.icon ?? 'Library'}
                  count={lib.book_count}
                  active={activeLibrary === lib.id}
                  isPrivate={lib.is_public === false}
                  onClick={() => selectLibrary(lib.id)}
                  onEdit={() => openEditLibModal(lib)}
                  onDelete={async () => {
                    await api.delete(`/libraries/${lib.id}`)
                    if (activeLibrary === lib.id) selectAllBooks()
                    onLibrariesChange()
                  }}
                />
              ))}
            </Section>

            <Section
              title="Saved Filters"
              icon={<Bookmark className="w-3 h-3" />}
            >
              {savedFilters.map(sf => (
                <SidebarItem
                  key={sf.id}
                  label={sf.name}
                  iconName={sf.icon ?? 'Bookmark'}
                  active={activeSavedFilter === sf.id}
                  onClick={() => selectSavedFilter(sf)}
                  onEdit={() => openEditFilterModal(sf)}
                  onDelete={async () => {
                    await api.delete(`/saved-filters/${sf.id}`)
                    if (activeSavedFilter === sf.id) selectAllBooks()
                    onSavedFiltersChange()
                  }}
                />
              ))}
            </Section>
          </nav>
        )}

        {/* User profile footer — single trigger, popover on click */}
        {open && (
          <UserMenu user={user} logout={logout} />
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
              'fixed inset-y-0 left-0 z-50 w-72 flex flex-col border-r border-border bg-card safe-top',
              mobileOpen ? 'translate-x-0' : '-translate-x-full'
            )}
            style={{ transition: 'transform 0.35s var(--spring)' }}
          >
            {/* Header with close button */}
            <div className="flex items-center justify-between px-3 h-14 border-b border-border shrink-0">
              <div className="flex items-center gap-2">
                <BookOpen className="w-5 h-5 text-primary" />
                <span className="font-semibold text-sm">Tome</span>
              </div>
              <button
                onClick={onMobileClose}
                className="flex items-center justify-center w-8 h-8 text-muted-foreground hover:text-foreground transition-colors rounded-lg hover:bg-muted"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <nav className="flex-1 overflow-y-auto px-2 py-4 space-y-4 overscroll-contain">
              <div>
                <button
                  onClick={() => { onOpenHomeView(); onMobileClose() }}
                  className={cn(
                    'group flex items-center gap-2 w-full px-2 py-2.5 rounded-lg text-sm transition-all',
                    isHomeTab
                      ? 'bg-primary/10 text-primary font-medium'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  )}
                >
                  <Home className="w-5 h-5 shrink-0 group-hover:animate-[wiggle_0.4s_ease-in-out]" />
                  <span className="truncate">Home</span>
                </button>
                <button
                  onClick={() => { selectAllBooks(); onMobileClose() }}
                  className={cn(
                    'group flex items-center gap-2 w-full px-2 py-2.5 rounded-lg text-sm transition-all',
                    isAllBooks
                      ? 'bg-primary/10 text-primary font-medium'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  )}
                >
                  <BookOpen className="w-5 h-5 shrink-0 group-hover:animate-[wiggle_0.4s_ease-in-out]" />
                  <span className="truncate">All Books</span>
                </button>
                <button
                  onClick={() => { onOpenSeriesView(); onMobileClose() }}
                  className={cn(
                    'group flex items-center gap-2 w-full px-2 py-2.5 rounded-lg text-sm transition-all',
                    isSeriesTab
                      ? 'bg-primary/10 text-primary font-medium'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  )}
                >
                  <Layers className="w-5 h-5 shrink-0 group-hover:animate-[wiggle_0.4s_ease-in-out]" />
                  <span className="truncate">Series</span>
                </button>
                <Link
                  to="/stats"
                  onClick={onMobileClose}
                  className={cn(
                    'group flex items-center gap-2 w-full px-2 py-2.5 rounded-lg text-sm transition-all',
                    location.pathname === '/stats'
                      ? 'bg-primary/10 text-primary font-medium'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  )}
                >
                  <BarChart3 className="w-5 h-5 shrink-0 group-hover:animate-[wiggle_0.4s_ease-in-out]" />
                  <span className="truncate">Stats</span>
                </Link>
              </div>

              <Section
                title="Libraries"
                icon={<LibraryIcon className="w-3 h-3" />}
                onAdd={openCreateLibModal}
              >
                {libraries.map(lib => (
                  <SidebarItem
                    key={lib.id}
                    label={lib.name}
                    iconName={lib.icon ?? 'Library'}
                    count={lib.book_count}
                    active={activeLibrary === lib.id}
                    isPrivate={lib.is_public === false}
                    onClick={() => selectLibrary(lib.id)}
                    onEdit={() => openEditLibModal(lib)}
                    onDelete={async () => {
                      await api.delete(`/libraries/${lib.id}`)
                      if (activeLibrary === lib.id) selectAllBooks()
                      onLibrariesChange()
                    }}
                  />
                ))}
              </Section>

              <Section
                title="Saved Filters"
                icon={<Bookmark className="w-3 h-3" />}
              >
                {savedFilters.map(sf => (
                  <SidebarItem
                    key={sf.id}
                    label={sf.name}
                    iconName={sf.icon ?? 'Bookmark'}
                    active={activeSavedFilter === sf.id}
                    onClick={() => selectSavedFilter(sf)}
                    onEdit={() => openEditFilterModal(sf)}
                    onDelete={async () => {
                      await api.delete(`/saved-filters/${sf.id}`)
                      if (activeSavedFilter === sf.id) selectAllBooks()
                      onSavedFiltersChange()
                    }}
                  />
                ))}
              </Section>
            </nav>

            {/* Mobile user footer */}
            <div className="shrink-0 border-t border-border">
              {/* User info */}
              <div className="flex items-center gap-3 px-4 py-3">
                <div className="flex w-9 h-9 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary shrink-0 ring-2 ring-primary/20">
                  {(user?.username ?? '?').slice(0, 2).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{user?.username}</p>
                  {user?.is_admin && <p className="text-[11px] text-muted-foreground">Admin</p>}
                </div>
              </div>
              {/* Actions */}
              <div className="px-2 pb-3 space-y-0.5">
                <MobileThemeToggle />
                <Link
                  to="/settings"
                  onClick={onMobileClose}
                  className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                >
                  <Settings className="w-5 h-5 shrink-0" />
                  Settings
                </Link>
                {user?.is_admin && (
                  <Link
                    to="/admin"
                    onClick={onMobileClose}
                    className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                  >
                    <Shield className="w-5 h-5 shrink-0" />
                    Admin
                  </Link>
                )}
                <button
                  onClick={() => { logout(); onMobileClose() }}
                  className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm text-destructive/80 hover:text-destructive hover:bg-destructive/10 transition-colors"
                >
                  <LogOut className="w-5 h-5 shrink-0" />
                  Log out
                </button>
              </div>
            </div>
          </div>
        </div>
    </>
  )
}


function MobileThemeToggle() {
  const [isDark, setIsDark] = useState(() => {
    const t = THEMES.find(t => t.id === getStoredTheme())
    return t?.dark ?? false
  })

  function toggle() {
    const next = isDark ? 'light' : 'dark'
    applyTheme(next)
    setIsDark(!isDark)
  }

  return (
    <button
      onClick={toggle}
      className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
    >
      {isDark ? <Sun className="w-5 h-5 shrink-0" /> : <Moon className="w-5 h-5 shrink-0" />}
      {isDark ? 'Light mode' : 'Dark mode'}
    </button>
  )
}

function UserMenu({ user, logout }: { user: { username: string; is_admin?: boolean } | null; logout: () => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const [isDark, setIsDark] = useState(() => {
    const t = THEMES.find(t => t.id === getStoredTheme())
    return t?.dark ?? false
  })

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  function toggleTheme() {
    const next = isDark ? 'light' : 'dark'
    applyTheme(next)
    setIsDark(!isDark)
  }

  const menuItem = 'flex items-center gap-2.5 w-full px-3 py-1.5 text-[13px] rounded-md transition-colors hover:bg-accent text-foreground/80 hover:text-foreground'
  const destructive = menuItem + ' text-destructive/80 hover:text-destructive hover:bg-destructive/10'

  return (
    <div ref={ref} className="relative shrink-0 border-t border-border px-2 py-2.5">
      <div className="flex items-center gap-1">
        {/* Always-visible theme toggle */}
        <button
          onClick={toggleTheme}
          title={isDark ? 'Switch to light' : 'Switch to dark'}
          className="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-accent/60 transition-colors text-muted-foreground hover:text-foreground shrink-0"
        >
          {isDark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </button>

        {/* Trigger button */}
        <button
          onClick={() => setOpen(o => !o)}
          className="flex-1 flex items-center gap-2 px-2 py-1.5 rounded-lg text-[13px] hover:bg-accent/60 transition-colors min-w-0"
        >
          <div className="flex w-6 h-6 items-center justify-center rounded-full bg-primary/10 text-[10px] font-bold text-primary shrink-0 ring-2 ring-primary/20">
            {(user?.username ?? '?').slice(0, 2).toUpperCase()}
          </div>
          <span className="truncate font-medium text-foreground/80 flex-1 text-left">{user?.username}</span>
          <ChevronsUpDown className="w-3 h-3 text-muted-foreground/50 shrink-0" />
        </button>
      </div>

      {open && (
        <div className="absolute bottom-full left-2 right-2 mb-1 bg-card border border-border rounded-lg shadow-lg py-1.5 z-50">
          <Link to="/settings" onClick={() => setOpen(false)} className={menuItem}>
            <Settings className="w-4 h-4 shrink-0" />
            Settings
          </Link>
          {user?.is_admin && (
            <Link to="/admin" onClick={() => setOpen(false)} className={menuItem}>
              <Shield className="w-4 h-4 shrink-0" />
              Admin
            </Link>
          )}
          <div className="my-1 h-px bg-border mx-2" />
          <button onClick={logout} className={destructive}>
            <LogOut className="w-4 h-4 shrink-0" />
            Log out
          </button>
        </div>
      )}
    </div>
  )
}

function Section({ title, icon, onAdd, onTitleClick, children }: {
  title: string
  icon: React.ReactNode
  onAdd?: () => void
  onTitleClick?: () => void
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="flex items-center justify-between px-2 mb-1">
        <button
          onClick={onTitleClick}
          className={cn(
            'flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground transition-colors',
            onTitleClick && 'hover:text-foreground cursor-pointer'
          )}
        >
          {icon} {title}
        </button>
        {onAdd && (
          <button
            onClick={onAdd}
            className="text-muted-foreground hover:text-foreground transition-colors"
            title={`New ${title.toLowerCase().replace(/s$/, '')}`}
            aria-label={`New ${title.toLowerCase().replace(/s$/, '')}`}
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  )
}

function SidebarItem({ label, iconName, count, active, isPrivate, onClick, onEdit, onDelete }: {
  label: string
  iconName: string
  count?: number
  active: boolean
  isPrivate?: boolean
  onClick: () => void
  onEdit?: () => void
  onDelete?: () => Promise<void>
}) {
  return (
    <div
      className={cn(
        'group flex items-center gap-2 w-full px-2 py-1.5 rounded-lg text-sm transition-all cursor-pointer',
        active
          ? 'bg-primary/10 text-primary font-medium'
          : 'text-muted-foreground hover:text-foreground hover:bg-muted'
      )}
      onClick={onClick}
    >
      <span className="shrink-0 sidebar-item-icon">{getIcon(iconName)}</span>
      <span className="truncate flex-1">{label}</span>
      {isPrivate && (
        <span title="Private library" className="shrink-0 flex items-center group-hover:hidden group-focus-within:hidden">
          <Lock className="w-3 h-3 text-muted-foreground/50" />
        </span>
      )}
      {count != null && !isPrivate && (
        <span className="text-[10px] text-muted-foreground shrink-0 group-hover:hidden group-focus-within:hidden">{count}</span>
      )}
      <div className="hidden group-hover:flex group-focus-within:flex items-center gap-0.5 shrink-0">
        {onEdit && (
          <button
            onClick={e => { e.stopPropagation(); onEdit() }}
            className="p-0.5 rounded hover:text-foreground transition-colors"
            title="Edit"
            aria-label="Edit"
          >
            <Pencil className="w-3 h-3" />
          </button>
        )}
        {onDelete && (
          <button
            onClick={async e => { e.stopPropagation(); await onDelete() }}
            className="p-0.5 rounded hover:text-destructive transition-colors"
            title="Delete"
            aria-label="Delete"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        )}
      </div>
    </div>
  )
}

function LibraryModal({ title, initialName, initialIcon, initialIsPublic, onSave, onClose }: {
  title: string
  initialName?: string
  initialIcon?: string
  initialIsPublic?: boolean
  onSave: (name: string, icon: string, isPublic: boolean) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState(initialName ?? '')
  const [icon, setIcon] = useState(initialIcon ?? 'Library')
  const [isPublic, setIsPublic] = useState(initialIsPublic ?? true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { inputRef.current?.focus() }, [])
  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  async function handleSave() {
    if (!name.trim() || saving) return
    setSaving(true)
    setError('')
    try {
      await onSave(name.trim(), icon, isPublic)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center"
      onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-card text-foreground rounded-xl shadow-xl max-w-sm w-full mx-4 p-6 space-y-4">
        <h2 className="text-base font-semibold">{title}</h2>
        <input
          ref={inputRef}
          value={name}
          onChange={e => { setName(e.target.value); setError('') }}
          onKeyDown={e => { if (e.key === 'Enter') handleSave() }}
          placeholder="Name…"
          className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm focus:outline-none focus:ring-1 focus:ring-ring"
        />
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">Icon</span>
          <IconPicker value={icon} onChange={setIcon} />
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground flex-1">Public library</span>
          <button
            type="button"
            onClick={() => setIsPublic(v => !v)}
            className={cn(
              'relative w-10 h-6 rounded-full transition-colors flex-shrink-0',
              isPublic ? 'bg-primary' : 'bg-muted-foreground/30'
            )}
          >
            <span className={cn(
              'absolute top-1 left-1 w-4 h-4 rounded-full bg-white shadow transition-transform',
              isPublic ? 'translate-x-4' : 'translate-x-0'
            )} />
          </button>
        </div>
        {!isPublic && (
          <p className="text-xs text-muted-foreground flex items-center gap-1">
            <Lock className="w-3 h-3" /> Private — only assigned users can access
          </p>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !name.trim()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
