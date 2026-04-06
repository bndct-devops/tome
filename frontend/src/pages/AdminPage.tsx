import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Users, Plus, Pencil, Trash2, Shield, Check, X,
  ChevronDown, ChevronUp, Loader2, ArrowLeft,
  RefreshCw, FolderInput, HardDrive, Database,
  BookOpen, Folder, Trash, Tag, LogIn,
  Activity, ChevronsUpDown, Copy, GitMerge,
} from 'lucide-react'
import { MetadataManager } from '@/components/MetadataManager'
import { LibraryHealthTab } from '@/components/LibraryHealth'
import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { IconPicker } from '@/components/Sidebar'
import type { BookType } from '@/lib/books'
import { invalidateBookTypesCache } from '@/lib/bookTypes'

// ── Types ─────────────────────────────────────────────────────────────────

interface PermissionsData {
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

interface UserData {
  id: number
  username: string
  email: string
  is_active: boolean
  is_admin: boolean
  created_at: string
  permissions: PermissionsData | null
}

interface AdminStats {
  book_count: number
  user_count: number
  db_size_mb: number
  covers_count: number
  covers_size_mb: number
  library_dir: string
  data_dir: string
  incoming_dir: string
  tome_version: string
  python_version: string
}

interface ScanResult {
  added: number
  skipped: number
  duplicates?: number
  errors?: number
}

const PERMISSION_LABELS: Record<keyof PermissionsData, string> = {
  can_upload: 'Upload books',
  can_download: 'Download books',
  can_edit_metadata: 'Edit metadata',
  can_delete_books: 'Delete books',
  can_manage_libraries: 'Manage libraries',
  can_manage_tags: 'Manage tags',
  can_manage_series: 'Manage series',
  can_manage_users: 'Manage users',
  can_approve_bindery: 'Approve Bindery',
  can_view_stats: 'View stats',
  can_use_opds: 'Use OPDS',
  can_use_kosync: 'Use KOSync',
  can_share: 'Share books',
  can_bulk_operations: 'Bulk operations',
}

// ── UserModal ─────────────────────────────────────────────────────────────

function UserModal({ user, onClose, onSaved }: {
  user: UserData | null
  onClose: () => void
  onSaved: (u: UserData) => void
}) {
  const { user: me } = useAuth()
  const [username, setUsername] = useState(user?.username ?? '')
  const [email, setEmail] = useState(user?.email ?? '')
  const [password, setPassword] = useState('')
  const [isAdmin, setIsAdmin] = useState(user?.is_admin ?? false)
  const [isActive, setIsActive] = useState(user?.is_active ?? true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isSelf = user?.id === me?.id

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      const saved = user
        ? await api.put<UserData>(`/users/${user.id}`, { username, email, ...(password ? { password } : {}), is_admin: isAdmin, is_active: isActive })
        : await api.post<UserData>('/users', { username, email, password, is_admin: isAdmin })
      onSaved(saved)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="bg-card border border-border rounded-xl shadow-xl w-full max-w-md">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h2 className="text-sm font-semibold">{user ? 'Edit User' : 'New User'}</h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-accent transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="p-4 flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">Username</label>
            <input value={username} onChange={e => setUsername(e.target.value)} required
              className="h-9 rounded-md border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder="username" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">Email</label>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)} required
              className="h-9 rounded-md border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder="user@example.com" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">
              Password {user && <span className="text-muted-foreground/60">(leave blank to keep)</span>}
            </label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} required={!user}
              className="h-9 rounded-md border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder={user ? '••••••••' : 'password'} />
          </div>
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <div onClick={() => setIsAdmin(v => !v)}
              className={cn('w-4 h-4 rounded border flex items-center justify-center transition-colors cursor-pointer',
                isAdmin ? 'bg-primary border-primary' : 'border-border')}>
              {isAdmin && <Check className="w-3 h-3 text-primary-foreground" />}
            </div>
            <span className="text-sm">Admin</span>
          </label>
          {user && !isSelf && (
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <div onClick={() => setIsActive(v => !v)}
                className={cn('w-4 h-4 rounded border flex items-center justify-center transition-colors cursor-pointer',
                  isActive ? 'bg-primary border-primary' : 'border-border')}>
                {isActive && <Check className="w-3 h-3 text-primary-foreground" />}
              </div>
              <span className="text-sm">Active</span>
            </label>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
          <div className="flex justify-end gap-2 mt-1">
            <button type="button" onClick={onClose}
              className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-accent transition-colors">Cancel</button>
            <button type="submit" disabled={saving}
              className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors flex items-center gap-1.5 disabled:opacity-50">
              {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {user ? 'Save' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── UsersTab ──────────────────────────────────────────────────────────────

function UsersTab() {
  const { user: me, impersonate } = useAuth()
  const navigate = useNavigate()
  const [users, setUsers] = useState<UserData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [modalUser, setModalUser] = useState<UserData | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [permSaving, setPermSaving] = useState<number | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null)
  const [deleting, setDeleting] = useState<number | null>(null)
  const [impersonating, setImpersonating] = useState<number | null>(null)

  useEffect(() => {
    api.get<UserData[]>('/users').then(setUsers).catch(() => setError('Failed to load users')).finally(() => setLoading(false))
  }, [])

  function handleSaved(saved: UserData) {
    setUsers(prev => {
      const idx = prev.findIndex(u => u.id === saved.id)
      if (idx >= 0) { const next = [...prev]; next[idx] = saved; return next }
      return [...prev, saved]
    })
    setModalOpen(false)
  }

  async function handleImpersonate(userId: number) {
    setImpersonating(userId)
    try {
      await impersonate(userId)
      navigate('/')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Impersonation failed')
    } finally { setImpersonating(null) }
  }

  async function handleDelete(userId: number) {
    setDeleting(userId)
    try {
      await api.delete(`/users/${userId}`)
      setUsers(prev => prev.filter(u => u.id !== userId))
      setDeleteConfirm(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally { setDeleting(null) }
  }

  async function togglePermission(userId: number, key: keyof PermissionsData, current: boolean) {
    const u = users.find(x => x.id === userId)
    if (!u?.permissions) return
    setPermSaving(userId)
    try {
      const saved = await api.put<UserData>(`/users/${userId}/permissions`, { ...u.permissions, [key]: !current })
      setUsers(prev => prev.map(x => x.id === userId ? saved : x))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save permissions')
    } finally { setPermSaving(null) }
  }

  return (
    <div>
      {error && (
        <div className="mb-4 px-3 py-2 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive flex items-center justify-between gap-2">
          {error}
          <button onClick={() => setError(null)} className="shrink-0 hover:opacity-70 transition-opacity"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm text-muted-foreground">{users.length} user{users.length !== 1 ? 's' : ''}</p>
        <button onClick={() => { setModalUser(null); setModalOpen(true) }}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
          <Plus className="w-3.5 h-3.5" /> New User
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin text-primary" />
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {users.map(u => (
            <div key={u.id} className="border border-border rounded-xl bg-card overflow-hidden">
              <div className="flex items-center gap-3 px-4 py-3">
                <div className="flex items-center gap-2 flex-1 min-w-0">
                  <div className={cn('w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-xs font-bold',
                    u.is_admin ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground')}>
                    {u.username[0].toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-medium truncate">{u.username}</span>
                      {u.is_admin && (
                        <span className="flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                          <Shield className="w-2.5 h-2.5" /> Admin
                        </span>
                      )}
                      {!u.is_active && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-destructive/10 text-destructive border border-destructive/20">Inactive</span>
                      )}
                      {u.id === me?.id && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground border border-border">You</span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground truncate">{u.email}</p>
                  </div>
                </div>
                <div className="hidden sm:block text-xs text-muted-foreground shrink-0">
                  {new Date(u.created_at).toLocaleDateString()}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button onClick={() => setExpandedId(expandedId === u.id ? null : u.id)}
                    className="p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground hover:text-foreground" title="Permissions">
                    {expandedId === u.id ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                  </button>
                  <button onClick={() => { setModalUser(u); setModalOpen(true) }}
                    className="p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground hover:text-foreground" title="Edit">
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                  {u.id !== me?.id && (
                    <button onClick={() => handleImpersonate(u.id)} disabled={impersonating === u.id}
                      className="p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground hover:text-amber-500" title="Log in as this user">
                      {impersonating === u.id
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <LogIn className="w-3.5 h-3.5" />}
                    </button>
                  )}
                  {u.id !== me?.id && (
                    deleteConfirm === u.id ? (
                      <div className="flex items-center gap-1">
                        <button onClick={() => handleDelete(u.id)} disabled={deleting === u.id}
                          className="px-2 py-1 text-xs rounded bg-destructive text-destructive-foreground hover:bg-destructive/90 flex items-center gap-1">
                          {deleting === u.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />} Confirm
                        </button>
                        <button onClick={() => setDeleteConfirm(null)}
                          className="p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground">
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ) : (
                      <button onClick={() => setDeleteConfirm(u.id)}
                        className="p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground hover:text-destructive" title="Delete">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )
                  )}
                </div>
              </div>
              {expandedId === u.id && (
                <div className="border-t border-border px-4 py-3 bg-muted/30">
                  {u.is_admin ? (
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Shield className="w-3.5 h-3.5 text-primary" /> Admin users have all permissions.
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                      {(Object.keys(PERMISSION_LABELS) as (keyof PermissionsData)[]).map(key => {
                        const val = u.permissions?.[key] ?? false
                        return (
                          <button key={key} onClick={() => togglePermission(u.id, key, val)} disabled={permSaving === u.id}
                            className={cn('flex items-center gap-1.5 px-2 py-1.5 rounded-lg border text-xs transition-colors text-left',
                              val ? 'bg-primary/10 border-primary/20 text-primary'
                                : 'bg-background border-border text-muted-foreground hover:bg-accent hover:text-foreground')}>
                            <div className={cn('w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0',
                              val ? 'bg-primary border-primary' : 'border-border')}>
                              {val && <Check className="w-2.5 h-2.5 text-primary-foreground" />}
                            </div>
                            {PERMISSION_LABELS[key]}
                          </button>
                        )
                      })}
                      {permSaving === u.id && (
                        <div className="flex items-center gap-1 text-xs text-muted-foreground">
                          <Loader2 className="w-3 h-3 animate-spin" /> Saving...
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {modalOpen && <UserModal user={modalUser} onClose={() => setModalOpen(false)} onSaved={handleSaved} />}
    </div>
  )
}

// ── ScannerTab ────────────────────────────────────────────────────────────

function ScannerTab() {
  const [scanning, setScanning] = useState(false)
  const [importing, setImporting] = useState(false)
  const [defaultTypeId, setDefaultTypeId] = useState<number | ''>('')
  const [bookTypes, setBookTypes] = useState<BookType[]>([])
  const [lastResult, setLastResult] = useState<{ type: 'scan' | 'import'; result: ScanResult } | null>(null)

  useEffect(() => {
    api.get<BookType[]>('/book-types').then(setBookTypes).catch(() => {})
  }, [])

  async function handleScan() {
    setScanning(true)
    setLastResult(null)
    try {
      const r = await api.post<ScanResult>('/books/scan', { default_type_id: defaultTypeId || null })
      setLastResult({ type: 'scan', result: r })
    } catch { /* ignore */ } finally { setScanning(false) }
  }

  async function handleImport() {
    setImporting(true)
    setLastResult(null)
    try {
      const r = await api.post<ScanResult>('/books/import', { default_type_id: defaultTypeId || null })
      setLastResult({ type: 'import', result: r })
    } catch { /* ignore */ } finally { setImporting(false) }
  }

  const typeSelector = (
    <div className="mt-3">
      <label className="block text-xs text-muted-foreground mb-1">Assign type to new books</label>
      <select
        value={defaultTypeId}
        onChange={e => setDefaultTypeId(e.target.value ? Number(e.target.value) : '')}
        className="w-full text-sm bg-background border border-border rounded-lg px-3 py-1.5 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
      >
        <option value="">No type (staging / admin only)</option>
        {bookTypes.map(t => (
          <option key={t.id} value={t.id}>{t.label}</option>
        ))}
      </select>
      {!defaultTypeId && (
        <p className="text-xs text-amber-500 mt-1">
          Without a type, new books are only visible to admins until assigned.
        </p>
      )}
    </div>
  )

  return (
    <div className="flex flex-col gap-4 max-w-xl">
      <div className="border border-border rounded-xl bg-card overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-semibold">Scan Library</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Walk the library directory and add any new book files found.
          </p>
        </div>
        <div className="px-4 py-3">
          {typeSelector}
          <button onClick={handleScan} disabled={scanning || importing}
            className="mt-3 flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm hover:bg-primary/90 transition-colors disabled:opacity-50">
            {scanning ? <RefreshCw className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            {scanning ? 'Scanning…' : 'Scan Now'}
          </button>
        </div>
      </div>

      <div className="border border-border rounded-xl bg-card overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-semibold">Import from Incoming</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Process files dropped into the <code className="text-[11px] bg-muted px-1 rounded">incoming/</code> directory and move them into the library.
          </p>
        </div>
        <div className="px-4 py-3">
          {typeSelector}
          <button onClick={handleImport} disabled={importing || scanning}
            className="mt-3 flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm hover:bg-primary/90 transition-colors disabled:opacity-50">
            {importing ? <RefreshCw className="w-4 h-4 animate-spin" /> : <FolderInput className="w-4 h-4" />}
            {importing ? 'Importing…' : 'Import Now'}
          </button>
        </div>
      </div>

      {lastResult && (
        <div className="border border-green-500/30 bg-green-500/5 rounded-xl px-4 py-3 text-sm">
          <p className="font-medium text-foreground mb-1">
            {lastResult.type === 'scan' ? 'Scan' : 'Import'} complete
          </p>
          <p className="text-muted-foreground text-xs">
            {lastResult.result.added} added · {lastResult.result.skipped} skipped
            {lastResult.result.duplicates ? ` · ${lastResult.result.duplicates} duplicates` : ''}
            {lastResult.result.errors ? ` · ${lastResult.result.errors} error(s)` : ''}
          </p>
        </div>
      )}
    </div>
  )
}

// ── ServerTab ─────────────────────────────────────────────────────────────

function ServerTab() {
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)
  const [clearResult, setClearResult] = useState<number | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)

  useEffect(() => {
    api.get<AdminStats>('/admin/stats').then(setStats).catch(() => setError('Failed to load server stats')).finally(() => setLoading(false))
  }, [])

  async function handleClearCovers() {
    setClearing(true)
    setError(null)
    try {
      const r = await api.delete<{ deleted: number }>('/admin/covers-cache')
      setClearResult(r.deleted)
      setStats(prev => prev ? { ...prev, covers_count: 0, covers_size_mb: 0 } : prev)
      setConfirmClear(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to clear covers')
    } finally { setClearing(false) }
  }

  if (loading) return (
    <div className="flex justify-center py-16">
      <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
    </div>
  )

  return (
    <div className="flex flex-col gap-4 max-w-xl">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { icon: BookOpen, label: 'Books', value: stats?.book_count ?? 0 },
          { icon: Users, label: 'Users', value: stats?.user_count ?? 0 },
          { icon: Database, label: 'Database', value: `${stats?.db_size_mb ?? 0} MB` },
          { icon: HardDrive, label: 'Covers', value: `${stats?.covers_size_mb ?? 0} MB` },
        ].map(({ icon: Icon, label, value }) => (
          <div key={label} className="border border-border rounded-xl bg-card px-4 py-3">
            <div className="flex items-center gap-1.5 text-muted-foreground mb-1">
              <Icon className="w-3.5 h-3.5" />
              <span className="text-xs">{label}</span>
            </div>
            <p className="text-lg font-semibold text-foreground">{value}</p>
          </div>
        ))}
      </div>

      {/* Paths */}
      <div className="border border-border rounded-xl bg-card overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-semibold">Directories</h3>
        </div>
        <div className="divide-y divide-border">
          {[
            { label: 'Library', path: stats?.library_dir },
            { label: 'Data', path: stats?.data_dir },
            { label: 'Incoming', path: stats?.incoming_dir },
          ].map(({ label, path }) => (
            <div key={label} className="flex items-start gap-3 px-4 py-2.5">
              <Folder className="w-4 h-4 text-muted-foreground shrink-0 mt-0.5" />
              <div className="min-w-0">
                <p className="text-xs font-medium text-muted-foreground">{label}</p>
                <p className="text-xs text-foreground font-mono break-all">{path ?? '—'}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Danger zone */}
      <div className="border border-destructive/30 rounded-xl bg-card overflow-hidden">
        <div className="px-4 py-3 border-b border-destructive/20">
          <h3 className="text-sm font-semibold text-destructive">Danger Zone</h3>
        </div>
        <div className="px-4 py-3">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-medium">Clear cover cache</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Delete all {stats?.covers_count ?? 0} cached cover files ({stats?.covers_size_mb ?? 0} MB).
                Covers will be re-extracted on next access.
              </p>
              {clearResult != null && (
                <p className="text-xs text-green-600 mt-1">{clearResult} covers deleted.</p>
              )}
              {error && (
                <p className="text-xs text-destructive mt-1">{error}</p>
              )}
            </div>
            {confirmClear ? (
              <div className="flex items-center gap-1.5 shrink-0">
                <button onClick={handleClearCovers} disabled={clearing}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors disabled:opacity-50">
                  {clearing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash className="w-3.5 h-3.5" />}
                  Confirm
                </button>
                <button onClick={() => setConfirmClear(false)}
                  className="p-1.5 rounded-md hover:bg-accent text-muted-foreground transition-colors">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ) : (
              <button onClick={() => setConfirmClear(true)}
                className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-destructive/40 text-destructive hover:bg-destructive/10 transition-colors">
                <Trash className="w-3.5 h-3.5" /> Clear
              </button>
            )}
          </div>
        </div>
      </div>

      {stats && (
        <p className="text-xs text-muted-foreground text-center pt-1">
          Tome v{stats.tome_version} · Python {stats.python_version}
        </p>
      )}
    </div>
  )
}

// ── TypesTab ──────────────────────────────────────────────────────────────

const COLOR_OPTIONS = ['blue', 'pink', 'orange', 'purple', 'red', 'green', 'yellow', 'teal'] as const
type ColorOption = typeof COLOR_OPTIONS[number]

const COLOR_DOT: Record<ColorOption, string> = {
  blue: 'bg-blue-500',
  pink: 'bg-pink-500',
  orange: 'bg-orange-500',
  purple: 'bg-purple-500',
  red: 'bg-red-500',
  green: 'bg-green-500',
  yellow: 'bg-yellow-500',
  teal: 'bg-teal-500',
}

interface TypeFormState {
  label: string
  icon: string
  color: ColorOption
  sort_order: number
}

function defaultForm(): TypeFormState {
  return { label: '', icon: 'Tag', color: 'blue', sort_order: 0 }
}

function TypesTab() {
  const [types, setTypes] = useState<BookType[]>([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [addForm, setAddForm] = useState<TypeFormState>(defaultForm())
  const [addSaving, setAddSaving] = useState(false)
  const [addError, setAddError] = useState<string | null>(null)
  const [editId, setEditId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState<TypeFormState>(defaultForm())
  const [editSaving, setEditSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  function load() {
    setLoading(true)
    api.get<BookType[]>('/book-types')
      .then(setTypes)
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function handleAdd() {
    if (!addForm.label.trim()) return
    setAddSaving(true)
    setAddError(null)
    try {
      await api.post('/book-types', addForm)
      invalidateBookTypesCache()
      load()
      setAddForm(defaultForm())
      setShowAdd(false)
    } catch (e) {
      setAddError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setAddSaving(false)
    }
  }

  function startEdit(bt: BookType) {
    setEditId(bt.id)
    setEditForm({ label: bt.label, icon: bt.icon ?? 'Tag', color: (bt.color ?? 'blue') as ColorOption, sort_order: bt.sort_order })
    setEditError(null)
  }

  async function handleEdit() {
    if (!editId || !editForm.label.trim()) return
    setEditSaving(true)
    setEditError(null)
    try {
      await api.put(`/book-types/${editId}`, editForm)
      invalidateBookTypesCache()
      load()
      setEditId(null)
    } catch (e) {
      setEditError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setEditSaving(false)
    }
  }

  async function handleDelete(id: number) {
    setDeleteError(null)
    try {
      await api.delete(`/book-types/${id}`)
      invalidateBookTypesCache()
      load()
      setDeleteConfirmId(null)
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : 'Failed')
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold flex items-center gap-2">
          <Tag className="w-4 h-4 text-muted-foreground" /> Book Types
        </h2>
        <button
          onClick={() => { setShowAdd(a => !a); setAddError(null) }}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-card hover:bg-muted transition-all"
        >
          <Plus className="w-3.5 h-3.5" /> Add Type
        </button>
      </div>

      {/* Add form */}
      {showAdd && (
        <div className="border border-border rounded-xl bg-card p-4 space-y-3">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">New Type</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Label</label>
              <input
                value={addForm.label}
                onChange={e => setAddForm(f => ({ ...f, label: e.target.value }))}
                placeholder="e.g. Novel"
                className="w-full px-3 py-1.5 text-sm rounded-lg border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Color</label>
              <select
                value={addForm.color}
                onChange={e => setAddForm(f => ({ ...f, color: e.target.value as ColorOption }))}
                className="w-full px-3 py-1.5 text-sm rounded-lg border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {COLOR_OPTIONS.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Icon</label>
              <IconPicker value={addForm.icon} onChange={v => setAddForm(f => ({ ...f, icon: v }))} />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Sort Order</label>
              <input
                type="number"
                value={addForm.sort_order}
                onChange={e => setAddForm(f => ({ ...f, sort_order: Number(e.target.value) }))}
                className="w-full px-3 py-1.5 text-sm rounded-lg border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>
          </div>
          {addError && <p className="text-xs text-destructive">{addError}</p>}
          <div className="flex items-center gap-2 justify-end">
            <button onClick={() => setShowAdd(false)} className="px-3 py-1.5 rounded-lg text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors">Cancel</button>
            <button
              onClick={handleAdd}
              disabled={addSaving || !addForm.label.trim()}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
            >
              {addSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
              Save
            </button>
          </div>
        </div>
      )}

      {/* Types list */}
      {loading ? (
        <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
      ) : types.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-8">No book types yet.</p>
      ) : (
        <div className="border border-border rounded-xl bg-card overflow-hidden divide-y divide-border">
          {types.map(bt => (
            <div key={bt.id}>
              {editId === bt.id ? (
                <div className="p-4 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">Label</label>
                      <input
                        value={editForm.label}
                        onChange={e => setEditForm(f => ({ ...f, label: e.target.value }))}
                        className="w-full px-3 py-1.5 text-sm rounded-lg border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">Color</label>
                      <select
                        value={editForm.color}
                        onChange={e => setEditForm(f => ({ ...f, color: e.target.value as ColorOption }))}
                        className="w-full px-3 py-1.5 text-sm rounded-lg border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring"
                      >
                        {COLOR_OPTIONS.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">Icon</label>
                      <IconPicker value={editForm.icon} onChange={v => setEditForm(f => ({ ...f, icon: v }))} />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">Sort Order</label>
                      <input
                        type="number"
                        value={editForm.sort_order}
                        onChange={e => setEditForm(f => ({ ...f, sort_order: Number(e.target.value) }))}
                        className="w-full px-3 py-1.5 text-sm rounded-lg border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring"
                      />
                    </div>
                  </div>
                  {editError && <p className="text-xs text-destructive">{editError}</p>}
                  <div className="flex items-center gap-2 justify-end">
                    <button onClick={() => setEditId(null)} className="px-3 py-1.5 rounded-lg text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors">Cancel</button>
                    <button
                      onClick={handleEdit}
                      disabled={editSaving || !editForm.label.trim()}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
                    >
                      {editSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                      Save
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-3 px-4 py-3">
                  <div className={cn('w-3 h-3 rounded-full shrink-0', COLOR_DOT[(bt.color ?? 'blue') as ColorOption] ?? 'bg-gray-400')} />
                  <span className="text-sm font-medium flex-1">{bt.label}</span>
                  <span className="text-xs text-muted-foreground hidden sm:block">{bt.icon}</span>
                  <span className="text-xs text-muted-foreground hidden sm:block font-mono">{bt.slug}</span>
                  {deleteConfirmId === bt.id ? (
                    <div className="flex items-center gap-1.5 shrink-0">
                      {deleteError && <span className="text-xs text-destructive mr-1">{deleteError}</span>}
                      <button
                        onClick={() => handleDelete(bt.id)}
                        className="flex items-center gap-1 px-2 py-1 text-xs rounded-md bg-destructive text-destructive-foreground hover:opacity-90 transition-colors"
                      >
                        <Trash2 className="w-3 h-3" /> Confirm
                      </button>
                      <button
                        onClick={() => { setDeleteConfirmId(null); setDeleteError(null) }}
                        className="p-1 rounded hover:bg-muted transition-colors text-muted-foreground"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        onClick={() => startEdit(bt)}
                        className="p-1.5 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                        title="Edit"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => { setDeleteConfirmId(bt.id); setDeleteError(null) }}
                        className="p-1.5 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-destructive"
                        title="Delete"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── AuditTab ──────────────────────────────────────────────────────────────

const ACTION_CATEGORIES = [
  { value: '', label: 'All actions' },
  { value: 'auth', label: 'Auth' },
  { value: 'books', label: 'Books' },
  { value: 'users', label: 'Users' },
  { value: 'libraries', label: 'Libraries' },
]

const ACTION_COLORS: Record<string, string> = {
  'auth.login': 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20',
  'auth.login_failed': 'bg-destructive/10 text-destructive border-destructive/20',
  'auth.logout': 'bg-muted text-muted-foreground border-border',
  'auth.password_changed': 'bg-blue-500/10 text-blue-600 border-blue-500/20',
  'auth.impersonated': 'bg-amber-500/10 text-amber-600 border-amber-500/20',
  'books.downloaded': 'bg-violet-500/10 text-violet-600 border-violet-500/20',
  'books.uploaded': 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20',
  'books.deleted': 'bg-destructive/10 text-destructive border-destructive/20',
  'books.metadata_edited': 'bg-blue-500/10 text-blue-600 border-blue-500/20',
  'books.bulk_metadata_edited': 'bg-blue-500/10 text-blue-600 border-blue-500/20',
  'users.created': 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20',
  'users.updated': 'bg-blue-500/10 text-blue-600 border-blue-500/20',
  'users.deleted': 'bg-destructive/10 text-destructive border-destructive/20',
  'libraries.created': 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20',
  'libraries.updated': 'bg-blue-500/10 text-blue-600 border-blue-500/20',
  'libraries.deleted': 'bg-destructive/10 text-destructive border-destructive/20',
}

interface AuditEntry {
  id: number
  user_id: number | null
  username: string | null
  action: string
  resource_type: string | null
  resource_id: number | null
  resource_title: string | null
  details: string | null
  ip_address: string | null
  created_at: string
}

function AuditTab() {
  const [items, setItems] = useState<AuditEntry[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [filterAction, setFilterAction] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const perPage = 50

  useEffect(() => {
    setLoading(true)
    const params = new URLSearchParams({ page: String(page), per_page: String(perPage) })
    if (filterAction) params.set('action', filterAction)
    if (fromDate) params.set('from_date', fromDate)
    if (toDate) params.set('to_date', toDate)
    api.get<{ total: number; items: AuditEntry[] }>(`/admin/audit-logs?${params}`)
      .then(d => { setItems(d.items); setTotal(d.total) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [page, filterAction, fromDate, toDate])

  function fmt(iso: string) {
    const d = new Date(iso)
    return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' })
  }

  const totalPages = Math.ceil(total / perPage)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Audit Log</h2>
        <div className="flex items-center gap-2 flex-wrap">
          <select value={filterAction} onChange={e => { setFilterAction(e.target.value); setPage(1) }}
            className="text-xs border border-border rounded-lg px-2 py-1.5 bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-primary">
            {ACTION_CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
          <input type="date" value={fromDate} onChange={e => { setFromDate(e.target.value); setPage(1) }}
            className="text-xs border border-border rounded-lg px-2 py-1.5 bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-primary" />
          <input type="date" value={toDate} onChange={e => { setToDate(e.target.value); setPage(1) }}
            className="text-xs border border-border rounded-lg px-2 py-1.5 bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-primary" />
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin text-primary" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground text-sm">No audit entries yet.</div>
      ) : (
        <div className="flex flex-col gap-1">
          {items.map(entry => {
            const colorClass = ACTION_COLORS[entry.action] ?? 'bg-muted text-muted-foreground border-border'
            const isExpanded = expandedId === entry.id
            const parsed = entry.details ? (() => { try { return JSON.parse(entry.details) } catch { return null } })() : null
            return (
              <div key={entry.id} className="border border-border rounded-xl bg-card overflow-hidden">
                <button className="w-full text-left flex items-center gap-3 px-4 py-3 hover:bg-accent/50 transition-colors"
                  onClick={() => setExpandedId(isExpanded ? null : entry.id)}>
                  {/* User avatar */}
                  <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center text-xs font-bold text-muted-foreground shrink-0">
                    {entry.username ? entry.username[0].toUpperCase() : '?'}
                  </div>
                  {/* Action badge */}
                  <span className={cn('text-[10px] font-mono px-1.5 py-0.5 rounded border shrink-0', colorClass)}>
                    {entry.action}
                  </span>
                  {/* Resource title */}
                  {entry.resource_title && (
                    <span className="text-xs text-foreground font-medium truncate flex-1 min-w-0">
                      {entry.resource_title}
                    </span>
                  )}
                  <div className="ml-auto flex items-center gap-3 shrink-0">
                    <span className="text-xs text-muted-foreground hidden sm:block">{entry.username ?? '—'}</span>
                    <span className="text-xs text-muted-foreground">{fmt(entry.created_at)}</span>
                    {isExpanded ? <ChevronUp className="w-3.5 h-3.5 text-muted-foreground" /> : <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />}
                  </div>
                </button>
                {isExpanded && (
                  <div className="px-4 pb-3 pt-0 border-t border-border bg-muted/30 text-xs text-muted-foreground flex flex-col gap-1.5">
                    {entry.ip_address && <div><span className="font-medium text-foreground">IP:</span> {entry.ip_address}</div>}
                    {entry.resource_type && entry.resource_id && (
                      <div><span className="font-medium text-foreground">Resource:</span> {entry.resource_type} #{entry.resource_id}</div>
                    )}
                    {parsed && (
                      <div>
                        <span className="font-medium text-foreground">Details:</span>
                        <pre className="mt-1 p-2 rounded bg-background text-[10px] overflow-x-auto border border-border">
                          {JSON.stringify(parsed, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-2">
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
            className="px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent disabled:opacity-40 transition-colors">
            Previous
          </button>
          <span className="text-xs text-muted-foreground">{page} / {totalPages}</span>
          <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
            className="px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent disabled:opacity-40 transition-colors">
            Next
          </button>
        </div>
      )}
    </div>
  )
}

// ── SyncStatusTab ─────────────────────────────────────────────────────────

interface SyncRecord {
  book_id: number
  book_title: string
  book_author: string | null
  book_series: string | null
  book_series_index: number | null
  user_id: number
  username: string
  status: 'unread' | 'reading' | 'read'
  progress_pct: number | null
  last_synced: string | null
  device: string | null
  source: 'tomesync' | 'web'
}

type SyncSortKey = 'book_title' | 'username' | 'status' | 'progress_pct' | 'last_synced' | 'device' | 'source'

function relativeTime(iso: string | null): string {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 30) return `${days}d ago`
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' })
}

function StatusBadge({ status }: { status: SyncRecord['status'] }) {
  const cls =
    status === 'read'
      ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20'
      : status === 'reading'
        ? 'bg-blue-500/10 text-blue-600 border-blue-500/20'
        : 'bg-muted text-muted-foreground border-border'
  return (
    <span className={cn('text-[10px] font-medium px-1.5 py-0.5 rounded border whitespace-nowrap', cls)}>
      {status}
    </span>
  )
}

function SourceBadge({ source }: { source: SyncRecord['source'] }) {
  const cls =
    source === 'tomesync'
      ? 'bg-violet-500/10 text-violet-600 border-violet-500/20'
      : 'bg-muted text-muted-foreground border-border'
  return (
    <span className={cn('text-[10px] font-medium px-1.5 py-0.5 rounded border whitespace-nowrap', cls)}>
      {source === 'tomesync' ? 'TomeSync' : 'Web'}
    </span>
  )
}

function SyncStatusTab() {
  const [records, setRecords] = useState<SyncRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sortKey, setSortKey] = useState<SyncSortKey>('last_synced')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [deleting, setDeleting] = useState<string | null>(null)

  useEffect(() => {
    api.get<SyncRecord[]>('/admin/sync-status')
      .then(setRecords)
      .catch(() => setError('Failed to load sync status'))
      .finally(() => setLoading(false))
  }, [])

  async function handleDelete(r: SyncRecord) {
    const key = `${r.user_id}-${r.book_id}`
    setDeleting(key)
    try {
      await api.delete(`/admin/sync-status/${r.user_id}/${r.book_id}`)
      setRecords(prev => prev.filter(x => !(x.user_id === r.user_id && x.book_id === r.book_id)))
    } catch {
      // silently ignore — user stays in list
    } finally {
      setDeleting(null)
    }
  }

  function handleSort(key: SyncSortKey) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(key === 'last_synced' ? 'desc' : 'asc')
    }
  }

  const sorted = [...records].sort((a, b) => {
    let av: string | number | null = null
    let bv: string | number | null = null
    if (sortKey === 'book_title') { av = a.book_title; bv = b.book_title }
    else if (sortKey === 'username') { av = a.username; bv = b.username }
    else if (sortKey === 'status') { av = a.status; bv = b.status }
    else if (sortKey === 'progress_pct') { av = a.progress_pct ?? -1; bv = b.progress_pct ?? -1 }
    else if (sortKey === 'last_synced') { av = a.last_synced ?? ''; bv = b.last_synced ?? '' }
    else if (sortKey === 'device') { av = a.device ?? ''; bv = b.device ?? '' }
    else if (sortKey === 'source') { av = a.source; bv = b.source }

    if (av === null || av === undefined) av = ''
    if (bv === null || bv === undefined) bv = ''
    const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true })
    return sortDir === 'asc' ? cmp : -cmp
  })

  function ColHeader({ label, col }: { label: string; col: SyncSortKey }) {
    const active = sortKey === col
    return (
      <th
        className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground cursor-pointer select-none hover:text-foreground transition-colors"
        onClick={() => handleSort(col)}
      >
        <span className="flex items-center gap-1">
          {label}
          {active ? (
            sortDir === 'asc'
              ? <ChevronUp className="w-3 h-3" />
              : <ChevronDown className="w-3 h-3" />
          ) : (
            <ChevronsUpDown className="w-3 h-3 opacity-30" />
          )}
        </span>
      </th>
    )
  }

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="px-3 py-2 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive">
        {error}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Sync Status</h2>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Activity className="w-3.5 h-3.5" />
          <span>{records.length} record{records.length !== 1 ? 's' : ''}</span>
        </div>
      </div>

      {sorted.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground text-sm">
          No reading activity yet. Records appear when users start reading books.
        </div>
      ) : (
        <div className="border border-border rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="border-b border-border bg-muted/40">
                <tr>
                  <ColHeader label="Book" col="book_title" />
                  <ColHeader label="User" col="username" />
                  <ColHeader label="Status" col="status" />
                  <ColHeader label="Progress" col="progress_pct" />
                  <ColHeader label="Last Synced" col="last_synced" />
                  <ColHeader label="Device" col="device" />
                  <ColHeader label="Source" col="source" />
                  <th className="px-3 py-2.5 w-8" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {sorted.map((r, i) => {
                  const key = `${r.user_id}-${r.book_id}`
                  const isDeleting = deleting === key
                  return (
                  <tr key={`${key}-${i}`} className="bg-card hover:bg-accent/40 transition-colors">
                    <td className="px-3 py-2.5 max-w-[220px]">
                      <div className="font-medium text-foreground truncate" title={r.book_title}>{r.book_title}</div>
                      {r.book_author && (
                        <div className="text-muted-foreground truncate" title={r.book_author}>{r.book_author}</div>
                      )}
                      {r.book_series && (
                        <div className="text-muted-foreground/70 truncate">
                          {r.book_series}{r.book_series_index != null ? ` #${r.book_series_index}` : ''}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-foreground font-medium whitespace-nowrap">{r.username}</td>
                    <td className="px-3 py-2.5"><StatusBadge status={r.status} /></td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      {r.progress_pct != null
                        ? `${(r.progress_pct * 100).toFixed(1)}%`
                        : '—'}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-muted-foreground" title={r.last_synced ?? undefined}>
                      {relativeTime(r.last_synced)}
                    </td>
                    <td className="px-3 py-2.5 text-muted-foreground max-w-[140px] truncate" title={r.device ?? undefined}>
                      {r.device ?? '—'}
                    </td>
                    <td className="px-3 py-2.5"><SourceBadge source={r.source} /></td>
                    <td className="px-3 py-2.5">
                      <button
                        onClick={() => handleDelete(r)}
                        disabled={isDeleting}
                        className="p-1 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-40"
                        title="Delete sync record"
                      >
                        {isDeleting
                          ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          : <Trash2 className="w-3.5 h-3.5" />}
                      </button>
                    </td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ── DuplicatesTab ─────────────────────────────────────────────────────────

interface DuplicateBookOut {
  id: number
  title: string
  subtitle: string | null
  author: string | null
  isbn: string | null
  cover_path: string | null
  series: string | null
  year: number | null
  files: { id: number; format: string; file_size: number | null }[]
  tags: string[]
  library_ids: number[]
}

interface DuplicateGroup {
  group_id: string
  match_reason: 'content_hash' | 'isbn' | 'same_series_volume' | 'similar_title'
  books: DuplicateBookOut[]
}

interface DuplicatesResponse {
  groups: DuplicateGroup[]
}

const MATCH_REASON_LABEL: Record<DuplicateGroup['match_reason'], string> = {
  content_hash: 'Exact Match',
  isbn: 'Same ISBN',
  same_series_volume: 'Same Series Volume',
  similar_title: 'Similar Title',
}

const MATCH_REASON_STYLE: Record<DuplicateGroup['match_reason'], string> = {
  content_hash: 'bg-destructive/10 text-destructive border-destructive/20',
  isbn: 'bg-amber-500/10 text-amber-600 border-amber-500/20',
  same_series_volume: 'bg-purple-500/10 text-purple-600 border-purple-500/20',
  similar_title: 'bg-blue-500/10 text-blue-600 border-blue-500/20',
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function DuplicatesTab() {
  const [groups, setGroups] = useState<DuplicateGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [keepIds, setKeepIds] = useState<Record<string, number>>({})
  const [merging, setMerging] = useState<string | null>(null)
  const [dismissing, setDismissing] = useState<string | null>(null)
  const [actionError, setActionError] = useState<Record<string, string>>({})

  function fetchGroups() {
    setLoading(true)
    setError(null)
    api.get<DuplicatesResponse>('/admin/duplicates')
      .then(d => {
        setGroups(d.groups)
        // Default keep selection: first book in each group
        const defaults: Record<string, number> = {}
        for (const g of d.groups) {
          if (g.books.length > 0) {
            defaults[g.group_id] = g.books[0].id
          }
        }
        setKeepIds(defaults)
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load duplicates'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchGroups() }, [])

  async function handleMerge(group: DuplicateGroup) {
    const keepId = keepIds[group.group_id]
    if (keepId == null) return
    const removeIds = group.books.map(b => b.id).filter(id => id !== keepId)
    setMerging(group.group_id)
    setActionError(prev => { const n = { ...prev }; delete n[group.group_id]; return n })
    try {
      await api.post('/admin/duplicates/merge', { keep_id: keepId, remove_ids: removeIds })
      fetchGroups()
    } catch (e) {
      setActionError(prev => ({ ...prev, [group.group_id]: e instanceof Error ? e.message : 'Merge failed' }))
    } finally {
      setMerging(null)
    }
  }

  async function handleDismiss(group: DuplicateGroup) {
    setDismissing(group.group_id)
    setActionError(prev => { const n = { ...prev }; delete n[group.group_id]; return n })
    try {
      await api.post('/admin/duplicates/dismiss', { book_ids: group.books.map(b => b.id) })
      fetchGroups()
    } catch (e) {
      setActionError(prev => ({ ...prev, [group.group_id]: e instanceof Error ? e.message : 'Dismiss failed' }))
    } finally {
      setDismissing(null)
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="px-3 py-2 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive">
        {error}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Copy className="w-4 h-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">Duplicate Detection</h2>
        </div>
        <button
          onClick={fetchGroups}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-border hover:bg-accent transition-colors text-muted-foreground"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </button>
      </div>

      {groups.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground text-sm">
          No duplicates found. Your library looks clean.
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <p className="text-xs text-muted-foreground">
            {groups.length} duplicate group{groups.length !== 1 ? 's' : ''} found. Select which book to keep, then merge or dismiss each group.
          </p>
          {groups.map(group => {
            const isMerging = merging === group.group_id
            const isDismissing = dismissing === group.group_id
            const busy = isMerging || isDismissing
            const err = actionError[group.group_id]
            return (
              <div key={group.group_id} className="border border-border rounded-xl bg-card overflow-hidden">
                {/* Group header */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-muted/30">
                  <span className={cn(
                    'text-[10px] font-medium px-2 py-0.5 rounded border',
                    MATCH_REASON_STYLE[group.match_reason],
                  )}>
                    {MATCH_REASON_LABEL[group.match_reason]}
                  </span>
                  <div className="flex items-center gap-2">
                    {err && <span className="text-xs text-destructive">{err}</span>}
                    <button
                      onClick={() => handleDismiss(group)}
                      disabled={busy}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-border hover:bg-accent transition-colors text-muted-foreground disabled:opacity-50"
                      title="Dismiss this group — it will not appear again"
                    >
                      {isDismissing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <X className="w-3.5 h-3.5" />}
                      Dismiss
                    </button>
                    <button
                      onClick={() => handleMerge(group)}
                      disabled={busy || keepIds[group.group_id] == null}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
                    >
                      {isMerging ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <GitMerge className="w-3.5 h-3.5" />}
                      Merge
                    </button>
                  </div>
                </div>

                {/* Books */}
                <div className="flex flex-wrap gap-4 px-4 py-4">
                  {group.books.map(book => {
                    const isKeep = keepIds[group.group_id] === book.id
                    return (
                      <label
                        key={book.id}
                        className={cn(
                          'flex flex-col gap-2 p-3 rounded-lg border cursor-pointer transition-all select-none w-full sm:w-56',
                          isKeep
                            ? 'border-primary bg-primary/5 ring-1 ring-primary/30'
                            : 'border-border bg-background hover:border-primary/50',
                        )}
                      >
                        <div className="flex items-start gap-3">
                          {/* Radio */}
                          <input
                            type="radio"
                            name={`keep-${group.group_id}`}
                            value={book.id}
                            checked={isKeep}
                            onChange={() => setKeepIds(prev => ({ ...prev, [group.group_id]: book.id }))}
                            className="mt-0.5 shrink-0 accent-primary"
                          />
                          {/* Cover */}
                          <div className="w-10 h-14 rounded bg-muted shrink-0 overflow-hidden">
                            {book.cover_path ? (
                              <img
                                src={`/api/books/${book.id}/cover`}
                                alt=""
                                className="w-full h-full object-cover"
                              />
                            ) : (
                              <div className="w-full h-full flex items-center justify-center">
                                <BookOpen className="w-4 h-4 text-muted-foreground/40" />
                              </div>
                            )}
                          </div>
                          {/* Meta */}
                          <div className="flex flex-col gap-0.5 min-w-0 flex-1">
                            <p className="text-xs font-medium text-foreground leading-snug line-clamp-2">{book.title}</p>
                            {book.subtitle && (
                              <p className="text-[10px] text-muted-foreground line-clamp-1">{book.subtitle}</p>
                            )}
                            {book.author && (
                              <p className="text-[10px] text-muted-foreground">{book.author}</p>
                            )}
                            {book.year && (
                              <p className="text-[10px] text-muted-foreground">{book.year}</p>
                            )}
                          </div>
                        </div>
                        {/* Files */}
                        {book.files.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {book.files.map(f => (
                              <span
                                key={f.id}
                                className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground border border-border font-mono"
                              >
                                {f.format.toUpperCase()} {formatBytes(f.file_size)}
                              </span>
                            ))}
                          </div>
                        )}
                        {isKeep && (
                          <span className="text-[10px] font-medium text-primary">Keep this one</span>
                        )}
                      </label>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── AdminPage ─────────────────────────────────────────────────────────────

type Tab = 'users' | 'scanner' | 'server' | 'types' | 'audit' | 'metadata' | 'library' | 'sync' | 'duplicates'

export function AdminPage() {
  const { user } = useAuth()
  const [tab, setTab] = useState<Tab>('users')

  if (!user?.is_admin) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-3">
        <Shield className="w-10 h-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Admin access required.</p>
        <Link to="/" className="text-sm text-primary hover:underline">Go back</Link>
      </div>
    )
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: 'users', label: 'Users' },
    { id: 'scanner', label: 'Scanner' },
    { id: 'server', label: 'Server' },
    { id: 'types', label: 'Types' },
    { id: 'audit', label: 'Audit Log' },
    { id: 'metadata', label: 'Metadata' },
    { id: 'library', label: 'Library' },
    { id: 'sync', label: 'Sync Status' },
    { id: 'duplicates', label: 'Duplicates' },
  ]

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur-sm safe-top">
        <div className={cn('flex items-center px-4 h-14 mx-auto', (tab === 'metadata' || tab === 'library') ? 'max-w-full' : 'max-w-4xl')}>
          <div className="flex items-center gap-3">
            <Link to="/" className="p-2 rounded-lg hover:bg-accent transition-colors text-muted-foreground hover:text-foreground">
              <ArrowLeft className="w-4 h-4" />
            </Link>
            <div className="flex items-center gap-2">
              <Shield className="w-4 h-4 text-muted-foreground" />
              <span className="text-sm font-semibold">Admin</span>
            </div>
          </div>
        </div>
        <div className="overflow-x-auto border-t border-border/50">
          <div className={cn('flex items-center gap-1 px-4 py-1.5 mx-auto', (tab === 'metadata' || tab === 'library') ? 'max-w-full' : 'max-w-4xl')}>
            {tabs.map(t => (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={cn(
                  'shrink-0 px-3 py-1.5 rounded-md text-xs font-medium transition-all whitespace-nowrap',
                  tab === t.id ? 'bg-muted text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                )}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className={cn('mx-auto px-4 py-6', tab === 'metadata' ? 'max-w-full' : 'max-w-4xl')}>
        {tab === 'users' && <UsersTab />}
        {tab === 'scanner' && <ScannerTab />}
        {tab === 'server' && <ServerTab />}
        {tab === 'types' && <TypesTab />}
        {tab === 'audit' && <AuditTab />}
        {tab === 'metadata' && <MetadataManager />}
        {tab === 'library' && <LibraryHealthTab />}
        {tab === 'sync' && <SyncStatusTab />}
        {tab === 'duplicates' && <DuplicatesTab />}
      </main>
    </div>
  )
}
