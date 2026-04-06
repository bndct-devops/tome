import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Users, Plus, Pencil, Trash2, Shield, Check, X,
  ChevronDown, ChevronUp, Loader2, ArrowLeft, Settings,
  User, Eye,
} from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { ThemeToggle } from '@/components/ThemeToggle'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

interface UserData {
  id: number
  username: string
  email: string
  is_active: boolean
  is_admin: boolean
  created_at: string
  role: 'admin' | 'member' | 'guest'
}

interface UserModalProps {
  user: UserData | null
  onClose: () => void
  onSaved: (u: UserData) => void
}

function UserModal({ user, onClose, onSaved }: UserModalProps) {
  const [username, setUsername] = useState(user?.username ?? '')
  const [email, setEmail] = useState(user?.email ?? '')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<'admin' | 'member' | 'guest'>(user?.role ?? 'guest')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const roles: { value: 'admin' | 'member' | 'guest'; label: string; Icon: typeof Shield }[] = [
    { value: 'guest', label: 'Guest', Icon: Eye },
    { value: 'member', label: 'Member', Icon: User },
    { value: 'admin', label: 'Admin', Icon: Shield },
  ]

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      let saved: UserData
      if (user) {
        saved = await api.put<UserData>(`/users/${user.id}`, {
          username,
          email,
          ...(password ? { password } : {}),
          role,
          is_admin: role === 'admin',
        })
      } else {
        saved = await api.post<UserData>('/users', { username, email, password, role, is_admin: role === 'admin' })
      }
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
            <input
              value={username}
              onChange={e => setUsername(e.target.value)}
              required
              className="h-9 rounded-md border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder="username"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">Email</label>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
              className="h-9 rounded-md border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder="user@example.com"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">
              Password {user && <span className="text-muted-foreground/60">(leave blank to keep)</span>}
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required={!user}
              className="h-9 rounded-md border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder={user ? '••••••••' : 'password'}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">Role</label>
            <div className="flex rounded-lg border border-border overflow-hidden">
              {roles.map(({ value, label, Icon }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setRole(value)}
                  className={cn(
                    'flex-1 flex items-center justify-center gap-1.5 px-2 py-2 text-xs font-medium transition-colors',
                    role === value
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground hover:bg-muted/80'
                  )}
                >
                  <Icon className="w-3.5 h-3.5" />
                  {label}
                </button>
              ))}
            </div>
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
          <div className="flex justify-end gap-2 mt-1">
            <button type="button" onClick={onClose} className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-accent transition-colors">
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors flex items-center gap-1.5 disabled:opacity-50"
            >
              {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {user ? 'Save' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export function UsersPage() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<UserData[]>([])
  const [loading, setLoading] = useState(true)
  const [modalUser, setModalUser] = useState<UserData | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [permSaving, setPermSaving] = useState<number | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null)
  const [deleting, setDeleting] = useState<number | null>(null)

  useEffect(() => {
    api.get<UserData[]>('/users')
      .then(setUsers)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  function openCreate() {
    setModalUser(null)
    setModalOpen(true)
  }

  function openEdit(u: UserData) {
    setModalUser(u)
    setModalOpen(true)
  }

  function handleSaved(saved: UserData) {
    setUsers(prev => {
      const idx = prev.findIndex(u => u.id === saved.id)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = saved
        return next
      }
      return [...prev, saved]
    })
    setModalOpen(false)
  }

  async function handleDelete(userId: number) {
    setDeleting(userId)
    try {
      await api.delete(`/users/${userId}`)
      setUsers(prev => prev.filter(u => u.id !== userId))
      setDeleteConfirm(null)
    } catch {
      // ignore
    } finally {
      setDeleting(null)
    }
  }

  async function updateRole(userId: number, role: 'admin' | 'member' | 'guest') {
    setPermSaving(userId)
    try {
      const saved = await api.put<UserData>(`/users/${userId}`, { role, is_admin: role === 'admin' })
      setUsers(prev => prev.map(x => x.id === userId ? saved : x))
    } catch {
      // ignore
    } finally {
      setPermSaving(null)
    }
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur-sm safe-top">
        <div className="flex items-center justify-between px-4 h-14 max-w-5xl mx-auto">
          <div className="flex items-center gap-3">
            <Link to="/" className="p-2 rounded-lg hover:bg-accent transition-colors text-muted-foreground hover:text-foreground">
              <ArrowLeft className="w-4 h-4" />
            </Link>
            <div className="flex items-center gap-2">
              <Users className="w-4 h-4 text-muted-foreground" />
              <span className="text-sm font-semibold">User Management</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Link to="/settings" className="p-2 rounded-lg hover:bg-accent transition-colors text-muted-foreground hover:text-foreground" title="Settings">
              <Settings className="w-4 h-4" />
            </Link>
            <button
              onClick={openCreate}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              New User
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-6">
        {loading ? (
          <div className="flex justify-center py-24">
            <Loader2 className="w-6 h-6 animate-spin text-primary" />
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {users.map(u => (
              <div key={u.id} className="border border-border rounded-xl bg-card overflow-hidden">
                {/* User row */}
                <div className="flex items-center gap-3 px-4 py-3">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <div className={cn(
                      'w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-xs font-bold',
                      u.role === 'admin' ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'
                    )}>
                      {u.username[0].toUpperCase()}
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm font-medium truncate">{u.username}</span>
                        {u.role === 'admin' && (
                          <span className="flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                            <Shield className="w-2.5 h-2.5" /> Admin
                          </span>
                        )}
                        {!u.is_active && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-destructive/10 text-destructive border border-destructive/20">
                            Inactive
                          </span>
                        )}
                        {u.id === me?.id && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground border border-border">
                            You
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground truncate">{u.email}</p>
                    </div>
                  </div>
                  <div className="hidden sm:block text-xs text-muted-foreground shrink-0">
                    {new Date(u.created_at).toLocaleDateString()}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => setExpandedId(expandedId === u.id ? null : u.id)}
                      className="p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground hover:text-foreground"
                      title="Role"
                    >
                      {expandedId === u.id ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => openEdit(u)}
                      className="p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground hover:text-foreground"
                      title="Edit"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    {u.id !== me?.id && (
                      deleteConfirm === u.id ? (
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => handleDelete(u.id)}
                            disabled={deleting === u.id}
                            className="px-2 py-1 text-xs rounded bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors flex items-center gap-1"
                          >
                            {deleting === u.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                            Confirm
                          </button>
                          <button
                            onClick={() => setDeleteConfirm(null)}
                            className="p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setDeleteConfirm(u.id)}
                          className="p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground hover:text-destructive"
                          title="Delete"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )
                    )}
                  </div>
                </div>

                {/* Role panel */}
                {expandedId === u.id && (
                  <div className="border-t border-border px-4 py-3 bg-muted/30">
                    <div className="flex items-center gap-3">
                      <span className="text-xs text-muted-foreground shrink-0">Role</span>
                      <div className="flex rounded-lg border border-border overflow-hidden">
                        {([
                          { value: 'guest', label: 'Guest', Icon: Eye },
                          { value: 'member', label: 'Member', Icon: User },
                          { value: 'admin', label: 'Admin', Icon: Shield },
                        ] as { value: 'admin' | 'member' | 'guest'; label: string; Icon: typeof Shield }[]).map(({ value, label, Icon }) => (
                          <button
                            key={value}
                            onClick={() => updateRole(u.id, value)}
                            disabled={permSaving === u.id}
                            className={cn(
                              'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors',
                              u.role === value
                                ? 'bg-primary text-primary-foreground'
                                : 'bg-muted text-muted-foreground hover:bg-muted/80'
                            )}
                          >
                            <Icon className="w-3.5 h-3.5" />
                            {label}
                          </button>
                        ))}
                      </div>
                      {permSaving === u.id && <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </main>

      {modalOpen && (
        <UserModal
          user={modalUser}
          onClose={() => setModalOpen(false)}
          onSaved={handleSaved}
        />
      )}
    </div>
  )
}
