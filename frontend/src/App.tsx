import { useEffect, useState, lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Keyboard } from 'lucide-react'
import { AuthProvider, useAuth } from '@/contexts/AuthContext'
import { ToastProvider } from '@/contexts/ToastContext'
import { MotionProvider } from '@/lib/motion'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { ImpersonationBanner } from '@/components/ImpersonationBanner'
import { ForcePasswordChange } from '@/components/ForcePasswordChange'
import { KeyboardShortcutsModal } from '@/components/KeyboardShortcutsModal'
import { LoginPage } from '@/pages/LoginPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { BookDetailPage } from '@/pages/BookDetailPage'
import { api } from '@/lib/api'
import { applyTheme, getStoredTheme } from '@/lib/theme'

// Library and Home (and the book page they open) are in the main bundle; every
// other page is its own chunk, so the first load does not parse the stats
// charts, the admin tools or the QR scanner. The chunks are fetched in idle
// time once the app is up, so opening one of those pages does not wait.
// The reader is the exception: epub.js is large and only loads when reading.
const PAGE_LOADERS = {
  OidcCallbackPage: () => import('@/pages/OidcCallbackPage').then(m => ({ default: m.OidcCallbackPage })),
  SharePage: () => import('@/pages/SharePage').then(m => ({ default: m.SharePage })),
  SetupPage: () => import('@/pages/SetupPage').then(m => ({ default: m.SetupPage })),
  AdminPage: () => import('@/pages/AdminPage').then(m => ({ default: m.AdminPage })),
  SettingsPage: () => import('@/pages/SettingsPage').then(m => ({ default: m.SettingsPage })),
  StatsPage: () => import('@/pages/StatsPage').then(m => ({ default: m.StatsPage })),
  BinderyPage: () => import('@/pages/BinderyPage').then(m => ({ default: m.BinderyPage })),
  WishlistPage: () => import('@/pages/WishlistPage').then(m => ({ default: m.WishlistPage })),
  HardcoverPage: () => import('@/pages/HardcoverPage').then(m => ({ default: m.HardcoverPage })),
  HighlightsPage: () => import('@/pages/HighlightsPage').then(m => ({ default: m.HighlightsPage })),
  ReadingCalendarPage: () => import('@/pages/ReadingCalendarPage').then(m => ({ default: m.ReadingCalendarPage })),
}
const ReaderPage = lazy(() => import('@/pages/ReaderPage'))
const OidcCallbackPage = lazy(PAGE_LOADERS.OidcCallbackPage)
const SharePage = lazy(PAGE_LOADERS.SharePage)
const SetupPage = lazy(PAGE_LOADERS.SetupPage)
const AdminPage = lazy(PAGE_LOADERS.AdminPage)
const SettingsPage = lazy(PAGE_LOADERS.SettingsPage)
const StatsPage = lazy(PAGE_LOADERS.StatsPage)
const BinderyPage = lazy(PAGE_LOADERS.BinderyPage)
const WishlistPage = lazy(PAGE_LOADERS.WishlistPage)
const HardcoverPage = lazy(PAGE_LOADERS.HardcoverPage)
const HighlightsPage = lazy(PAGE_LOADERS.HighlightsPage)
const ReadingCalendarPage = lazy(PAGE_LOADERS.ReadingCalendarPage)

function prefetchPages() {
  const idle = window.requestIdleCallback ?? ((cb: () => void) => setTimeout(cb, 1500))
  idle(() => { for (const load of Object.values(PAGE_LOADERS)) load().catch(() => {}) })
}

// Apply saved theme immediately (avoid flash)
applyTheme(getStoredTheme())

const UNAUTHENTICATED_PATHS = ['/login', '/setup']

function AppRoutes() {
  const { user } = useAuth()
  const location = useLocation()
  const [setupNeeded, setSetupNeeded] = useState<boolean | null>(null)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)

  useEffect(() => { if (user) prefetchPages() }, [user])

  const isAuthenticatedPage = user != null && !UNAUTHENTICATED_PATHS.includes(location.pathname)

  useEffect(() => {
    api.get<{ setup_needed: boolean }>('/auth/setup-needed')
      .then(d => setSetupNeeded(d.setup_needed))
      .catch(() => setSetupNeeded(false))
  }, [user])

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (!isAuthenticatedPage) return
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.key === '?') {
        e.preventDefault()
        setShortcutsOpen(s => !s)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isAuthenticatedPage])

  if (setupNeeded === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  // Block entire app until password is changed
  if (user?.must_change_password) {
    return <ForcePasswordChange />
  }

  return (
    <>
      <Suspense fallback={null}>
      <Routes>
        <Route path="/setup" element={setupNeeded ? <SetupPage /> : <Navigate to="/login" replace />} />
        <Route path="/login" element={setupNeeded ? <Navigate to="/setup" replace /> : <LoginPage />} />
        <Route path="/auth/callback" element={<OidcCallbackPage />} />
        {/* Public share view — deliberately outside ProtectedRoute; the token
            is the capability. */}
        <Route path="/share/:token" element={<SharePage />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <DashboardPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/books/:id"
          element={
            <ProtectedRoute>
              <BookDetailPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/users"
          element={<Navigate to="/admin" replace />}
        />
        <Route
          path="/admin"
          element={
            <ProtectedRoute>
              <AdminPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/settings"
          element={
            <ProtectedRoute>
              <SettingsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/stats"
          element={
            <ProtectedRoute>
              <StatsPage />
            </ProtectedRoute>
          }
        />
        <Route path="/stats-lab" element={<Navigate to="/stats" replace />} />
        <Route
          path="/calendar"
          element={
            <ProtectedRoute>
              <ReadingCalendarPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/highlights"
          element={
            <ProtectedRoute>
              <HighlightsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/reader/:bookId"
          element={
            <ProtectedRoute>
              <Suspense fallback={<div className="flex items-center justify-center h-screen"><div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" /></div>}>
                <ReaderPage />
              </Suspense>
            </ProtectedRoute>
          }
        />
        <Route
          path="/bindery"
          element={
            <ProtectedRoute>
              <BinderyPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/wishlist"
          element={
            <ProtectedRoute>
              <WishlistPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/hardcover"
          element={
            <ProtectedRoute>
              <HardcoverPage />
            </ProtectedRoute>
          }
        />
        {/* Catch-all */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </Suspense>

      {/* Global keyboard shortcuts FAB — only on authenticated pages */}
      {isAuthenticatedPage && (
        <button
          onClick={() => setShortcutsOpen(s => !s)}
          title="Keyboard shortcuts (?)"
          className="fixed bottom-5 right-5 z-40 w-9 h-9 rounded-full bg-card border border-border shadow-md flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
        >
          <Keyboard className="w-4 h-4" />
        </button>
      )}

      <KeyboardShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
    </>
  )
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <MotionProvider>
            <AppRoutes />
            <ImpersonationBanner />
          </MotionProvider>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App

