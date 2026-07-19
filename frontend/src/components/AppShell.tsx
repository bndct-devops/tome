import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth, isMember } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import { useSidebarLists } from '@/lib/sidebarLists'
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
  const { libraries, savedFilters, loadLibraries, loadSavedFilters } = useSidebarLists(user?.id)
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

  // Live preview under the box: the dashboard's search filters its grid as you
  // type, but on these pages Enter used to be the only feedback. A dropdown of
  // top matches keeps focus in the box (auto-navigating mid-typing would steal
  // keystrokes); Enter still opens the full filtered grid.
  const [searchPreview, setSearchPreview] = useState<
    { id: number; title: string; author: string | null }[]
  >([])
  useEffect(() => {
    const q = searchInput.trim()
    if (!q) {
      setSearchPreview([])
      return
    }
    const t = setTimeout(() => {
      api
        .get<{ id: number; title: string; author: string | null }[]>(
          `/books?q=${encodeURIComponent(q)}&limit=6`,
        )
        .then(r => setSearchPreview(Array.isArray(r) ? r : []))
        .catch(() => {})
    }, 250)
    return () => clearTimeout(t)
  }, [searchInput])

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

  // Cached across page mounts (see useSidebarLists) — this refetch only
  // freshens the lists in the background, it never blanks them.
  useEffect(() => { loadLibraries(); loadSavedFilters() }, [])

  // h-dvh, not h-screen: 100vh overshoots the visible area under mobile
  // browser toolbars, leaving the page itself pannable by the overhang.
  return (
    <div className="h-dvh bg-background flex flex-col overflow-hidden">
      <AppHeader
        onMenuClick={() => setMobileSidebarOpen(true)}
        search={
          <div className="relative flex-1 sm:max-w-md">
            <HeaderSearch
              value={searchInput}
              onChange={setSearchInput}
              onClear={() => setSearchInput('')}
              onSubmit={submitSearch}
              inputRef={searchInputRef}
            />
            {searchInput.trim() && searchPreview.length > 0 && (
              <div className="absolute inset-x-0 top-full z-50 mt-1.5 overflow-hidden rounded-lg border border-border bg-card shadow-xl">
                {searchPreview.map(b => (
                  <button
                    key={b.id}
                    onClick={() => { setSearchInput(''); navigate(`/books/${b.id}`) }}
                    className="flex w-full items-center gap-2.5 px-3 py-2 text-left hover:bg-muted"
                  >
                    <img
                      src={`/api/books/${b.id}/cover`}
                      alt=""
                      className="h-9 w-6 shrink-0 rounded-sm object-cover"
                    />
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-foreground">{b.title}</span>
                      {b.author && (
                        <span className="block truncate text-xs text-muted-foreground">{b.author}</span>
                      )}
                    </span>
                  </button>
                ))}
                <button
                  onClick={() => navigate(`/?tab=books&q=${encodeURIComponent(searchInput.trim())}`)}
                  className="w-full border-t border-border px-3 py-2 text-left text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  See all results for &quot;{searchInput.trim()}&quot;
                </button>
              </div>
            )}
          </div>
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
        {/* overflow-x-hidden: see DashboardPage's main — WebKit pans pre-transform
            layout overflow sideways; keep the app clamped to the viewport. */}
        <main className="flex-1 overflow-y-auto overflow-x-hidden min-w-0 overscroll-contain">{children}</main>
      </div>
    </div>
  )
}
