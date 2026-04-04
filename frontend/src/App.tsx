import { useEffect, useState, lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Keyboard } from 'lucide-react'
import { AuthProvider, useAuth } from '@/contexts/AuthContext'
import { ToastProvider } from '@/contexts/ToastContext'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { ImpersonationBanner } from '@/components/ImpersonationBanner'
import { ForcePasswordChange } from '@/components/ForcePasswordChange'
import { KeyboardShortcutsModal } from '@/components/KeyboardShortcutsModal'
import { LoginPage } from '@/pages/LoginPage'
import { SetupPage } from '@/pages/SetupPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { BookDetailPage } from '@/pages/BookDetailPage'
import { AdminPage } from '@/pages/AdminPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { StatsPage } from '@/pages/StatsPage'
import { BinderyPage } from '@/pages/BinderyPage'
import { api } from '@/lib/api'
import { applyTheme, getStoredTheme } from '@/lib/theme'

// Lazy-load the reader — epub.js is large
const ReaderPage = lazy(() => import('@/pages/ReaderPage'))

// Apply saved theme immediately (avoid flash)
applyTheme(getStoredTheme())

const UNAUTHENTICATED_PATHS = ['/login', '/setup']

function AppRoutes() {
  const { user } = useAuth()
  const location = useLocation()
  const [setupNeeded, setSetupNeeded] = useState<boolean | null>(null)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)

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
      <Routes>
        <Route path="/setup" element={setupNeeded ? <SetupPage /> : <Navigate to="/login" replace />} />
        <Route path="/login" element={setupNeeded ? <Navigate to="/setup" replace /> : <LoginPage />} />
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
        {/* Catch-all */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>

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
          <AppRoutes />
          <ImpersonationBanner />
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App

