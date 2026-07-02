import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth, isMember } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { api } from '@/lib/api'
import type { Library, SavedFilter } from '@/lib/books'
import { Sidebar } from '@/components/Sidebar'
import { AppHeader, HeaderSearch } from '@/components/AppHeader'
import { UploadModal } from '@/components/UploadModal'

/**
 * The shared application shell — the same top navbar (Tome wordmark) + persistent
 * Sidebar (docked on desktop, drawer on mobile) that the dashboard uses, so the
 * standalone pages (Stats, Highlights, Wishlist, Bindery) get the *real* nav
 * instead of a stripped-down clone. The page renders its own content as children;
 * `actions` slots page-specific controls into the navbar's right side, and
 * `onUploaded` lets a page refresh itself after an Upload from this navbar
 * (e.g. the Bindery reloading its inbox).
 *
 * Home / All Books / Series in the sidebar navigate back to the dashboard; the
 * active section item (Stats/Highlights/…) highlights itself by route.
 */
export function AppShell({
  children,
  actions,
  onUploaded,
}: {
  children: ReactNode
  actions?: ReactNode
  onUploaded?: () => void
}) {
  const navigate = useNavigate()
  const { user } = useAuth()
  const { toast } = useToast()
  const [libraries, setLibraries] = useState<Library[]>([])
  const [savedFilters, setSavedFilters] = useState<SavedFilter[]>([])
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [searchInput, setSearchInput] = useState('')
  const searchInputRef = useRef<HTMLInputElement>(null)

  // Global library search from any page: Enter jumps to the dashboard's book
  // results (it never live-yanks you off the current page mid-type).
  function submitSearch(e: React.FormEvent) {
    e.preventDefault()
    const q = searchInput.trim()
    navigate(q ? `/?tab=books&q=${encodeURIComponent(q)}` : '/?tab=books')
  }

  // "/" focuses the search — same shortcut the dashboard's box advertises.
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault()
        searchInputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  const loadLibraries = () => { api.get<Library[]>('/libraries').then(setLibraries).catch(() => {}) }
  const loadSavedFilters = () => { api.get<SavedFilter[]>('/saved-filters').then(setSavedFilters).catch(() => {}) }
  useEffect(() => { loadLibraries(); loadSavedFilters() }, [])

  return (
    <div className="h-screen bg-background flex flex-col overflow-hidden">
      <AppHeader
        onMenuClick={() => setMobileSidebarOpen(true)}
        search={
          <HeaderSearch
            value={searchInput}
            onChange={setSearchInput}
            onClear={() => setSearchInput('')}
            onSubmit={submitSearch}
            inputRef={searchInputRef}
          />
        }
        actions={actions}
        onUploadClick={isMember(user) ? () => setUploadOpen(true) : undefined}
      />

      <UploadModal
        isOpen={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onDone={() => onUploaded?.()}
        onWishMatches={(wishIds) => {
          const n = wishIds.length
          toast.info(`This upload satisfies ${n} wish${n !== 1 ? 'es' : ''} — review in Admin > Wishlist`)
        }}
      />

      <div className="flex flex-1 overflow-hidden">
        <Sidebar
          libraries={libraries}
          savedFilters={savedFilters}
          activeTab="none"
          onLibrariesChange={loadLibraries}
          onSavedFiltersChange={loadSavedFilters}
          onOpenSeriesView={() => navigate('/?tab=series')}
          onOpenHomeView={() => navigate('/')}
          mobileOpen={mobileSidebarOpen}
          onMobileClose={() => setMobileSidebarOpen(false)}
        />
        <main className="flex-1 overflow-y-auto min-w-0">{children}</main>
      </div>
    </div>
  )
}
