import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '@/lib/api'

interface Permissions {
  can_upload: boolean
  can_download: boolean
  can_edit_metadata: boolean
  can_delete_books: boolean
  can_manage_libraries: boolean
  can_manage_tags: boolean
  can_manage_series: boolean
  can_manage_users: boolean
  can_approve_bindery: boolean
  can_view_stats: boolean
  can_use_opds: boolean
  can_use_kosync: boolean
  can_share: boolean
  can_bulk_operations: boolean
}

export interface AuthUser {
  id: number
  username: string
  email: string
  is_active: boolean
  is_admin: boolean
  must_change_password: boolean
  created_at: string
  permissions: Permissions | null
  role: 'admin' | 'member' | 'guest'
}

export function isAdmin(user: AuthUser | null): boolean {
  return user?.is_admin || user?.role === 'admin' || false
}

export function isMember(user: AuthUser | null): boolean {
  return isAdmin(user) || user?.role === 'member' || false
}

export function isGuest(user: AuthUser | null): boolean {
  return user?.role === 'guest' || false
}

interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  isImpersonating: boolean
  impersonatedUsername: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  refreshUser: () => Promise<void>
  impersonate: (userId: number) => Promise<void>
  exitImpersonation: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

const ADMIN_TOKEN_KEY = 'tome_admin_token'
const IMPERSONATED_USERNAME_KEY = 'tome_impersonated_username'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [isImpersonating, setIsImpersonating] = useState(false)
  const [impersonatedUsername, setImpersonatedUsername] = useState<string | null>(null)

  useEffect(() => {
    const token = localStorage.getItem('tome_token')
    if (!token) {
      setLoading(false)
      return
    }
    // Restore impersonation state on page reload
    const adminToken = sessionStorage.getItem(ADMIN_TOKEN_KEY)
    const savedUsername = sessionStorage.getItem(IMPERSONATED_USERNAME_KEY)
    if (adminToken && savedUsername) {
      setIsImpersonating(true)
      setImpersonatedUsername(savedUsername)
    }
    api.get<AuthUser>('/auth/me')
      .then(setUser)
      .catch(() => localStorage.removeItem('tome_token'))
      .finally(() => setLoading(false))
  }, [])

  async function login(username: string, password: string) {
    const data = await api.post<{ access_token: string }>('/auth/login', { username, password })
    localStorage.setItem('tome_token', data.access_token)
    const me = await api.get<AuthUser>('/auth/me')
    setUser(me)
  }

  function logout() {
    localStorage.removeItem('tome_token')
    sessionStorage.removeItem(ADMIN_TOKEN_KEY)
    sessionStorage.removeItem(IMPERSONATED_USERNAME_KEY)
    setUser(null)
    setIsImpersonating(false)
    setImpersonatedUsername(null)
  }

  async function refreshUser() {
    const me = await api.get<AuthUser>('/auth/me')
    setUser(me)
  }

  async function impersonate(userId: number) {
    const data = await api.post<{ access_token: string; username: string }>(`/users/${userId}/impersonate`, {})
    // Stash admin token so we can restore it later
    const adminToken = localStorage.getItem('tome_token')!
    sessionStorage.setItem(ADMIN_TOKEN_KEY, adminToken)
    sessionStorage.setItem(IMPERSONATED_USERNAME_KEY, data.username)
    localStorage.setItem('tome_token', data.access_token)
    const me = await api.get<AuthUser>('/auth/me')
    setUser(me)
    setIsImpersonating(true)
    setImpersonatedUsername(data.username)
  }

  function exitImpersonation() {
    const adminToken = sessionStorage.getItem(ADMIN_TOKEN_KEY)
    if (!adminToken) return
    localStorage.setItem('tome_token', adminToken)
    sessionStorage.removeItem(ADMIN_TOKEN_KEY)
    sessionStorage.removeItem(IMPERSONATED_USERNAME_KEY)
    setIsImpersonating(false)
    setImpersonatedUsername(null)
    // Reload user as admin
    api.get<AuthUser>('/auth/me').then(setUser).catch(() => logout())
  }

  return (
    <AuthContext.Provider value={{
      user, loading,
      isImpersonating, impersonatedUsername,
      login, logout, refreshUser,
      impersonate, exitImpersonation,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
