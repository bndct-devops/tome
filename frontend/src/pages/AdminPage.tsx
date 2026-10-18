import { useEffect, useState, useRef, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Plus, Pencil, Trash2, Shield, Check, X,
  ChevronDown, ChevronUp, Loader2,
  RefreshCw, FolderInput,
  BookOpen, Folder, Trash, LogIn,
  Activity, ChevronsUpDown, GitMerge,
  User, Eye, ExternalLink, Send, Search, Layers,
} from 'lucide-react'
import { DOCS, docsLink } from '@/lib/docs'
import { MetadataManager } from '@/components/MetadataManager'
import { AppShell } from '@/components/AppShell'
import { SectionLayout, SectionNav, SectionSidebar, PanelSection, TABLE_HEAD } from '@/components/SectionNav'
import { PageBody, Pill, Stat, StatStrip } from '@/components/page'
import { LibraryHealthTab } from '@/components/LibraryHealth'
import { CoverAudit } from '@/components/CoverAudit'
import { InstanceBackup } from '@/components/InstanceBackup'
import { WordCountTab } from '@/components/WordCount'
import { SeriesCoverageStrip } from '@/components/SeriesCoverageStrip'
import { useAuth, isAdmin } from '@/contexts/AuthContext'
import { Trans, useLingui, Plural } from '@lingui/react/macro'
import { t, plural, msg } from '@lingui/core/macro'
import type { MessageDescriptor } from '@lingui/core'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { IconPicker } from '@/components/Sidebar'
import { CoverImage } from '@/components/CoverImage'
import type { BookType } from '@/lib/books'
import { invalidateBookTypesCache } from '@/lib/bookTypes'
import { BookAnimation } from '@/components/BookAnimation'
import {
  adminListWishes, fulfillWish, dismissWish,
  type WishAdminOut,
} from '@/lib/wishlist'
import { coverSrc } from '@/lib/covers'

// ── Types ─────────────────────────────────────────────────────────────────

interface UserData {
  id: number
  username: string
  email: string
  is_active: boolean
  is_admin: boolean
  created_at: string
  role: 'admin' | 'member' | 'guest'
  excluded_tags?: string[]
  download_limit?: number | null
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

// ── UserModal ─────────────────────────────────────────────────────────────

function UserModal({ user, onClose, onSaved }: {
  user: UserData | null
  onClose: () => void
  onSaved: (u: UserData) => void
}) {
  const { t } = useLingui()
  const { user: me } = useAuth()
  const [username, setUsername] = useState(user?.username ?? '')
  const [email, setEmail] = useState(user?.email ?? '')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<'admin' | 'member' | 'guest'>(user?.role ?? 'guest')
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
        ? await api.put<UserData>(`/users/${user.id}`, { username, email, ...(password ? { password } : {}), role, is_admin: role === 'admin', is_active: isActive })
        : await api.post<UserData>('/users', { username, email, password, role, is_admin: role === 'admin' })
      onSaved(saved)
    } catch (err) {
      setError(err instanceof Error ? err.message : t`Failed to save`)
    } finally {
      setSaving(false)
    }
  }

  const roles: { value: 'admin' | 'member' | 'guest'; label: string; Icon: typeof Shield }[] = [
    { value: 'guest', label: t`Guest`, Icon: Eye },
    { value: 'member', label: t`Member`, Icon: User },
    { value: 'admin', label: t`Admin`, Icon: Shield },
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
      <div className="bg-card rounded-[var(--radius-panel)] shadow-xl ring-1 ring-border/70 w-full max-w-md">
        <div className="flex items-center justify-between px-5 pt-5 pb-2">
          <h2 className="font-display text-[20px] font-bold leading-none">{user ? t`Edit User` : t`New User`}</h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-accent transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="px-5 pb-5 pt-2 flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-[13px] text-muted-foreground"><Trans>Username</Trans></label>
            <input value={username} onChange={e => setUsername(e.target.value)} required
              className="h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder="username" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[13px] text-muted-foreground"><Trans>Email</Trans></label>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)} required
              className="h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder="user@example.com" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[13px] text-muted-foreground">
              <Trans>Password</Trans> {user && <span className="text-muted-foreground/60"><Trans>(leave blank to keep)</Trans></span>}
            </label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} required={!user}
              className="h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder={user ? '••••••••' : t`password`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[13px] text-muted-foreground"><Trans>Role</Trans></label>
            <div className="flex gap-1 rounded-full bg-secondary p-1">
              {roles.map(({ value, label, Icon }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setRole(value)}
                  className={cn(
                    'flex-1 flex items-center justify-center gap-1.5 h-7 px-2 rounded-full text-[13px] font-medium transition-colors',
                    role === value
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  <Icon className="w-3.5 h-3.5" />
                  {label}
                </button>
              ))}
            </div>
          </div>
          {user && !isSelf && (
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <div onClick={() => setIsActive(v => !v)}
                className={cn('w-4 h-4 rounded border flex items-center justify-center transition-colors cursor-pointer',
                  isActive ? 'bg-primary border-primary' : 'border-border')}>
                {isActive && <Check className="w-3 h-3 text-primary-foreground" />}
              </div>
              <span className="text-sm"><Trans>Active</Trans></span>
            </label>
          )}
          {error && <p className="text-[13px] text-destructive">{error}</p>}
          <div className="flex justify-end gap-2 mt-1">
            <Pill type="button" onClick={onClose}
              variant="secondary"><Trans>Cancel</Trans></Pill>
            <Pill type="submit" disabled={saving}
              variant="primary">
              {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {user ? t`Save` : t`Create`}
            </Pill>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── UsersTab ──────────────────────────────────────────────────────────────

// Content restrictions (issue #190): hidden tags + daily download cap.
// Rendered in the expanded user row for non-admin accounts only.
function RestrictionEditor({ user, onSaved }: { user: UserData; onSaved: (u: UserData) => void }) {
  const { t } = useLingui()
  const [tags, setTags] = useState((user.excluded_tags ?? []).join(', '))
  const [limit, setLimit] = useState(user.download_limit == null ? '' : String(user.download_limit))
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setSaving(true)
    setSaved(false)
    setError(null)
    const trimmedLimit = limit.trim()
    const parsed = trimmedLimit === '' ? null : parseInt(trimmedLimit, 10)
    if (parsed !== null && (Number.isNaN(parsed) || parsed < 0)) {
      setError(t`Download limit must be a number of 0 or more`)
      setSaving(false)
      return
    }
    try {
      const updated = await api.put<UserData>(`/users/${user.id}/restrictions`, {
        excluded_tags: tags.split(',').map(s => s.trim()).filter(Boolean),
        download_limit: parsed,
      })
      onSaved(updated)
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Failed to save restrictions`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-4 flex flex-col gap-3 max-w-xl">
      <div className="flex flex-col gap-1">
        <label className="text-[13px] text-muted-foreground">
          <Trans>Hidden tags — books carrying any of these tags are invisible to this user (comma-separated)</Trans>
        </label>
        <input
          value={tags}
          onChange={(e) => { setTags(e.target.value); setSaved(false) }}
          placeholder={t`e.g. adult, mature`}
          className="h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        />
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <label className="text-[13px] text-muted-foreground shrink-0"><Trans>Downloads per day</Trans></label>
        <input
          value={limit}
          onChange={(e) => { setLimit(e.target.value); setSaved(false) }}
          inputMode="numeric"
          placeholder={t`unlimited`}
          className="w-28 h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        />
        <span className="text-[13px] text-muted-foreground"><Trans>blank = unlimited, 0 = downloads disabled</Trans></span>

      </div>
      <div className="flex items-center gap-2">
        <Pill
          onClick={save}
          disabled={saving}
          variant="primary"
        >
          {saving ? t`Saving…` : t`Save restrictions`}
        </Pill>
        {saved && <span className="text-[13px] text-success"><Trans>Saved</Trans></span>}
        {error && <span className="text-[13px] text-destructive">{error}</span>}
      </div>
    </div>
  )
}

function UsersTab() {
  const { t, i18n } = useLingui()
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
    api.get<UserData[]>('/users').then(setUsers).catch(() => setError(t`Failed to load users`)).finally(() => setLoading(false))
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
      setError(e instanceof Error ? e.message : t`Impersonation failed`)
    } finally { setImpersonating(null) }
  }

  async function handleDelete(userId: number) {
    setDeleting(userId)
    try {
      await api.delete(`/users/${userId}`)
      setUsers(prev => prev.filter(u => u.id !== userId))
      setDeleteConfirm(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Delete failed`)
    } finally { setDeleting(null) }
  }

  async function updateRole(userId: number, role: 'admin' | 'member' | 'guest') {
    setPermSaving(userId)
    try {
      const saved = await api.put<UserData>(`/users/${userId}`, { role, is_admin: role === 'admin' })
      setUsers(prev => prev.map(x => x.id === userId ? saved : x))
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Failed to save role`)
    } finally { setPermSaving(null) }
  }

  const roleOptions: { value: 'admin' | 'member' | 'guest'; label: string; Icon: typeof Shield }[] = [
    { value: 'guest', label: t`Guest`, Icon: Eye },
    { value: 'member', label: t`Member`, Icon: User },
    { value: 'admin', label: t`Admin`, Icon: Shield },
  ]

  return (
    <PanelSection
      title={<Trans>Users</Trans>}
      description={
        <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
          <Plural value={users.length} one="# user" other="# users" />
          <a
            href={docsLink(DOCS.usersAndRoles)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 hover:text-primary transition-colors"
          >
            <Trans>Roles guide</Trans> <ExternalLink className="w-3 h-3" />
          </a>
        </span>
      }
      action={
        <Pill variant="primary" onClick={() => { setModalUser(null); setModalOpen(true) }}>
          <Plus className="w-4 h-4" /> <Trans>New User</Trans>
        </Pill>
      }
    >
      {error && (
        <div className="mb-4 px-3 py-2 rounded-lg bg-destructive/10 text-[14px] text-destructive flex items-center justify-between gap-2">
          {error}
          <button onClick={() => setError(null)} className="shrink-0 hover:opacity-70 transition-opacity"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <BookAnimation variant="refresh" className="block w-10 h-10 text-primary" />
        </div>
      ) : (
        <div className="border-y border-border/70 divide-y divide-border/70">
          <div className={cn('hidden sm:flex items-center gap-3 py-2.5', TABLE_HEAD)}>
            <span className="flex-1"><Trans>User</Trans></span>
            <span className="w-24"><Trans>Added</Trans></span>
            <span className="w-[164px]" />
          </div>
          {users.map(u => (
            <div key={u.id}>
              <div className="flex items-center gap-3 py-3">
                <div className="flex items-center gap-3 flex-1 min-w-0">
                  <div className={cn('w-9 h-9 rounded-full flex items-center justify-center shrink-0 text-[13px] font-bold',
                    u.role === 'admin' ? 'bg-primary/10 text-primary' : 'bg-secondary text-muted-foreground')}>
                    {u.username[0].toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-[15px] font-medium text-foreground truncate">{u.username}</span>
                      {u.role === 'admin' && (
                        <span className="inline-flex items-center gap-0.5 text-[11px] font-medium px-2 py-0.5 rounded-full bg-primary/10 text-primary">
                          <Shield className="w-2.5 h-2.5" /> <Trans>Admin</Trans>
                        </span>
                      )}
                      {!u.is_active && (
                        <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-destructive/10 text-destructive"><Trans>Inactive</Trans></span>
                      )}
                      {u.id === me?.id && (
                        <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-secondary text-muted-foreground"><Trans>You</Trans></span>
                      )}
                    </div>
                    <p className="text-[13px] text-muted-foreground truncate">{u.email}</p>
                  </div>
                </div>
                <div className="hidden sm:block w-24 text-[13px] text-muted-foreground shrink-0 tabular-nums">
                  {new Date(u.created_at).toLocaleDateString(i18n.locale)}
                </div>
                <div className="flex items-center justify-end gap-0.5 shrink-0 sm:w-[164px]">
                  {u.id !== me?.id && deleteConfirm === u.id ? (
                    <>
                      <Pill variant="destructive" className="bg-destructive/10 text-destructive" onClick={() => handleDelete(u.id)} disabled={deleting === u.id}>
                        {deleting === u.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />} <Trans>Delete</Trans>
                      </Pill>
                      <Pill variant="quiet" icon aria-label={t`Cancel`} title={t`Cancel`} onClick={() => setDeleteConfirm(null)}>
                        <X className="w-4 h-4" />
                      </Pill>
                    </>
                  ) : (
                    <>
                      <Pill variant="quiet" icon aria-label={t`Role`} title={t`Role`} onClick={() => setExpandedId(expandedId === u.id ? null : u.id)}>
                        {expandedId === u.id ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                      </Pill>
                      <Pill variant="quiet" icon aria-label={t`Edit`} title={t`Edit`} onClick={() => { setModalUser(u); setModalOpen(true) }}>
                        <Pencil className="w-4 h-4" />
                      </Pill>
                      {u.id !== me?.id && (
                        <Pill variant="quiet" icon aria-label={t`Log in as this user`} title={t`Log in as this user`} className="hover:text-warning"
                          onClick={() => handleImpersonate(u.id)} disabled={impersonating === u.id}>
                          {impersonating === u.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4" />}
                        </Pill>
                      )}
                      {u.id !== me?.id && (
                        <Pill variant="destructive" icon aria-label={t`Delete`} title={t`Delete`} onClick={() => setDeleteConfirm(u.id)}>
                          <Trash2 className="w-4 h-4" />
                        </Pill>
                      )}
                    </>
                  )}
                </div>
              </div>
              {expandedId === u.id && (
                <div className="pb-4 sm:pl-12">
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="text-[13px] text-muted-foreground shrink-0"><Trans>Role</Trans></span>
                    <div className="flex gap-1 rounded-full bg-secondary p-1">
                      {roleOptions.map(({ value, label, Icon }) => (
                        <button
                          key={value}
                          onClick={() => updateRole(u.id, value)}
                          disabled={permSaving === u.id}
                          className={cn(
                            'flex items-center gap-1.5 h-7 px-3 rounded-full text-[13px] font-medium transition-colors',
                            u.role === value
                              ? 'bg-primary text-primary-foreground'
                              : 'text-muted-foreground hover:text-foreground'
                          )}
                        >
                          <Icon className="w-3.5 h-3.5" />
                          {label}
                        </button>
                      ))}
                    </div>
                    {permSaving === u.id && <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />}
                  </div>
                  {u.role !== 'admin' && (
                    <RestrictionEditor
                      user={u}
                      onSaved={(saved) => setUsers(prev => prev.map(x => x.id === saved.id ? saved : x))}
                    />
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {modalOpen && <UserModal user={modalUser} onClose={() => setModalOpen(false)} onSaved={handleSaved} />}
    </PanelSection>
  )
}

// ── ScannerTab ────────────────────────────────────────────────────────────

function ScannerTab() {
  const { t } = useLingui()
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
    <div className="max-w-md">
      <label className="block text-[13px] text-muted-foreground mb-1.5"><Trans>Assign type to new books</Trans></label>
      <select
        value={defaultTypeId}
        onChange={e => setDefaultTypeId(e.target.value ? Number(e.target.value) : '')}
        className="tome-select w-full h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
      >
        <option value="">{t`No type (staging / admin only)`}</option>
        {bookTypes.map(t => (
          <option key={t.id} value={t.id}>{t.label}</option>
        ))}
      </select>
      {!defaultTypeId && (
        <p className="text-[13px] text-warning mt-1.5">
          <Trans>Without a type, new books are only visible to admins until assigned.</Trans>
        </p>
      )}
    </div>
  )

  return (
    <>
      <PanelSection
        title={<Trans>Scan Library</Trans>}
        description={<Trans>Walk the library directory and add any new book files found.</Trans>}
      >
        {scanning ? (
          <div className="flex items-center gap-3 py-2">
            <BookAnimation variant="refresh" className="block w-10 h-10 text-primary" />
            <p className="text-[14px] text-muted-foreground"><Trans>Scanning…</Trans></p>
          </div>
        ) : (
          <div className="flex flex-col items-start gap-4">
            {typeSelector}
            <Pill variant="primary" onClick={handleScan} disabled={scanning || importing}>
              <RefreshCw className="w-4 h-4" />
              <Trans>Scan Now</Trans>
            </Pill>
          </div>
        )}
      </PanelSection>

      <PanelSection
        title={<Trans>Import from Incoming</Trans>}
        description={<Trans>Process files dropped into the <code className="text-[13px] bg-secondary px-1 rounded">incoming/</code> directory and move them into the library.</Trans>}
      >
        <div className="flex flex-col items-start gap-4">
          {typeSelector}
          <Pill variant="primary" onClick={handleImport} disabled={importing || scanning}>
            {importing ? <RefreshCw className="w-4 h-4 animate-spin" /> : <FolderInput className="w-4 h-4" />}
            {importing ? t`Importing…` : t`Import Now`}
          </Pill>
        </div>
      </PanelSection>

      {lastResult && (
        <div className="-mt-4 rounded-lg bg-success/10 px-4 py-3 text-[14px]">
          <p className="font-medium text-foreground mb-0.5">
            {lastResult.type === 'scan' ? t`Scan complete` : t`Import complete`}
          </p>
          <p className="text-muted-foreground text-[13px]">
            {(() => {
              const r = lastResult.result
              const added = r.added, skipped = r.skipped, dups = r.duplicates, errs = r.errors
              return t`${added} added · ${skipped} skipped` +
                (dups ? t` · ${dups} duplicates` : '') +
                (errs ? t` · ${errs} error(s)` : '')
            })()}
          </p>
        </div>
      )}
    </>
  )
}

// ── ServerTab ─────────────────────────────────────────────────────────────

function ServerTab() {
  const { t } = useLingui()
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)
  const [clearResult, setClearResult] = useState<number | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)

  useEffect(() => {
    api.get<AdminStats>('/admin/stats').then(setStats).catch(() => setError(t`Failed to load server stats`)).finally(() => setLoading(false))
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
      setError(e instanceof Error ? e.message : t`Failed to clear covers`)
    } finally { setClearing(false) }
  }

  if (loading) return (
    <div className="flex justify-center py-16">
      <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
    </div>
  )

  return (
    <>
      <PanelSection title={<Trans>Server</Trans>}>
        <StatStrip cols={4}>
          <Stat size={30} label={t`Books`} value={stats?.book_count ?? 0} />
          <Stat size={30} label={t`Users`} value={stats?.user_count ?? 0} />
          <Stat size={30} label={t`Database`} value={stats?.db_size_mb ?? 0} unit="MB" />
          <Stat size={30} label={t`Covers`} value={stats?.covers_size_mb ?? 0} unit="MB" />
        </StatStrip>
        {stats && (
          <p className="mt-3 text-[13px] text-muted-foreground">
            {/* eslint-disable-next-line lingui/no-unlocalized-strings -- version line */}
            {`Tome v${stats.tome_version} · Python ${stats.python_version}`}
          </p>
        )}
      </PanelSection>

      <PanelSection title={<Trans>Directories</Trans>}>
        <div className="border-y border-border/70 divide-y divide-border/70">
          {[
            { label: t`Library`, path: stats?.library_dir },
            { label: t`Data`, path: stats?.data_dir },
            { label: t`Incoming`, path: stats?.incoming_dir },
          ].map(({ label, path }) => (
            <div key={label} className="flex items-start gap-3 py-3">
              <Folder className="w-4 h-4 text-muted-foreground shrink-0 mt-0.5" />
              <div className="min-w-0">
                <p className="text-[13px] text-muted-foreground">{label}</p>
                <p className="text-[14px] text-foreground font-mono break-all">{path ?? '—'}</p>
              </div>
            </div>
          ))}
        </div>
      </PanelSection>

      <InstanceBackup />

      <section>
        <h2 className="font-display text-[20px] leading-none font-bold text-destructive"><Trans>Danger Zone</Trans></h2>
        <div className="mt-5 border-y border-destructive/30 py-4">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="min-w-0">
              <p className="text-[15px] font-medium text-foreground"><Trans>Clear cover cache</Trans></p>
              {(() => { const count = stats?.covers_count ?? 0; const mb = stats?.covers_size_mb ?? 0; return (
              <p className="text-[13px] text-muted-foreground mt-0.5">
                <Trans>Delete all {count} cached cover files ({mb} MB).
                Covers will be re-extracted on next access.</Trans>
              </p>
              ) })()}
              {clearResult != null && (
                <p className="text-[13px] text-success mt-1"><Trans>{clearResult} covers deleted.</Trans></p>
              )}
              {error && (
                <p className="text-[13px] text-destructive mt-1">{error}</p>
              )}
            </div>
            {confirmClear ? (
              <div className="flex items-center gap-1 shrink-0">
                <Pill variant="destructive" className="bg-destructive/10 text-destructive" onClick={handleClearCovers} disabled={clearing}>
                  {clearing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash className="w-3.5 h-3.5" />}
                  <Trans>Confirm</Trans>
                </Pill>
                <Pill variant="quiet" icon aria-label={t`Cancel`} title={t`Cancel`} onClick={() => setConfirmClear(false)}>
                  <X className="w-4 h-4" />
                </Pill>
              </div>
            ) : (
              <Pill variant="destructive" className="shrink-0 text-destructive" onClick={() => setConfirmClear(true)}>
                <Trash className="w-3.5 h-3.5" /> <Trans>Clear</Trans>
              </Pill>
            )}
          </div>
        </div>
      </section>
    </>
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
  const { t } = useLingui()
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
      setAddError(e instanceof Error ? e.message : t`Failed`)
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
      setEditError(e instanceof Error ? e.message : t`Failed`)
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
      setDeleteError(e instanceof Error ? e.message : t`Failed`)
    }
  }

  return (
    <PanelSection
      title={<Trans>Book Types</Trans>}
      action={
        <Pill
          onClick={() => { setShowAdd(a => !a); setAddError(null) }}
          variant="secondary"
        >
          <Plus className="w-3.5 h-3.5" /> <Trans>Add Type</Trans>
        </Pill>
      }
    >
    <div className="space-y-5">

      {/* Add form */}
      {showAdd && (
        <div className="rounded-[var(--radius-panel)] bg-card p-5 space-y-4">
          <h3 className="text-[15px] font-semibold text-foreground"><Trans>New Type</Trans></h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="text-[13px] text-muted-foreground mb-1.5 block">
<Trans>Label</Trans></label>
              <input
                value={addForm.label}
                onChange={e => setAddForm(f => ({ ...f, label: e.target.value }))}
                placeholder={t`e.g. Novel`}
                className="tome-select w-full h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>
            <div>
              <label className="text-[13px] text-muted-foreground mb-1.5 block">
<Trans>Color</Trans></label>
              <select
                value={addForm.color}
                onChange={e => setAddForm(f => ({ ...f, color: e.target.value as ColorOption }))}
                className="tome-select w-full h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {COLOR_OPTIONS.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[13px] text-muted-foreground mb-1.5 block">
<Trans>Icon</Trans></label>
              <IconPicker value={addForm.icon} onChange={v => setAddForm(f => ({ ...f, icon: v }))} />
            </div>
            <div>
              <label className="text-[13px] text-muted-foreground mb-1.5 block">
<Trans>Sort Order</Trans></label>
              <input
                type="number"
                value={addForm.sort_order}
                onChange={e => setAddForm(f => ({ ...f, sort_order: Number(e.target.value) }))}
                className="tome-select w-full h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>
          </div>
          {addError && <p className="text-[13px] text-destructive">{addError}</p>}
          <div className="flex items-center gap-2 justify-end">
            <Pill onClick={() => setShowAdd(false)} variant="quiet"><Trans>Cancel</Trans></Pill>
            <Pill
              onClick={handleAdd}
              disabled={addSaving || !addForm.label.trim()}
              variant="primary"
            >
              {addSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
              <Trans>Save</Trans>
            </Pill>
          </div>
        </div>
      )}

      {/* Types list */}
      {loading ? (
        <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
      ) : types.length === 0 ? (
        <p className="text-[14px] text-muted-foreground py-4"><Trans>No book types yet.</Trans></p>
      ) : (
        <div className="border-y border-border/70 divide-y divide-border/70">
          {types.map(bt => (
            <div key={bt.id}>
              {editId === bt.id ? (
                <div className="py-4 space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                      <label className="text-[13px] text-muted-foreground mb-1.5 block">
<Trans>Label</Trans></label>
                      <input
                        value={editForm.label}
                        onChange={e => setEditForm(f => ({ ...f, label: e.target.value }))}
                        className="tome-select w-full h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                      />
                    </div>
                    <div>
                      <label className="text-[13px] text-muted-foreground mb-1.5 block">
<Trans>Color</Trans></label>
                      <select
                        value={editForm.color}
                        onChange={e => setEditForm(f => ({ ...f, color: e.target.value as ColorOption }))}
                        className="tome-select w-full h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                      >
                        {COLOR_OPTIONS.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="text-[13px] text-muted-foreground mb-1.5 block">
<Trans>Icon</Trans></label>
                      <IconPicker value={editForm.icon} onChange={v => setEditForm(f => ({ ...f, icon: v }))} />
                    </div>
                    <div>
                      <label className="text-[13px] text-muted-foreground mb-1.5 block">
<Trans>Sort Order</Trans></label>
                      <input
                        type="number"
                        value={editForm.sort_order}
                        onChange={e => setEditForm(f => ({ ...f, sort_order: Number(e.target.value) }))}
                        className="tome-select w-full h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                      />
                    </div>
                  </div>
                  {editError && <p className="text-[13px] text-destructive">{editError}</p>}
                  <div className="flex items-center gap-2 justify-end">
                    <Pill onClick={() => setEditId(null)} variant="quiet"><Trans>Cancel</Trans></Pill>
                    <Pill
                      onClick={handleEdit}
                      disabled={editSaving || !editForm.label.trim()}
                      variant="primary"
                    >
                      {editSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                      <Trans>Save</Trans>
                    </Pill>
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-3 py-2.5">
                  <div className={cn('w-3 h-3 rounded-full shrink-0', COLOR_DOT[(bt.color ?? 'blue') as ColorOption] ?? 'bg-gray-400')} />
                  <span className="text-[15px] font-medium text-foreground flex-1">{bt.label}</span>
                  <span className="text-[13px] text-muted-foreground hidden sm:block">{bt.icon}</span>
                  <span className="text-[13px] text-muted-foreground hidden sm:block font-mono">{bt.slug}</span>
                  {deleteConfirmId === bt.id ? (
                    <div className="flex items-center gap-1.5 shrink-0">
                      {deleteError && <span className="text-[13px] text-destructive mr-1">{deleteError}</span>}
                      <Pill
                        onClick={() => handleDelete(bt.id)}
                        variant="destructive" className="bg-destructive/10 text-destructive"
                      >
                        <Trash2 className="w-3.5 h-3.5" /> <Trans>Confirm</Trans>
                      </Pill>
                      <Pill
                        variant="quiet" icon aria-label={t`Cancel`} title={t`Cancel`}
                        onClick={() => { setDeleteConfirmId(null); setDeleteError(null) }}
                      >
                        <X className="w-4 h-4" />
                      </Pill>
                    </div>
                  ) : (
                    <div className="flex items-center gap-0.5 shrink-0">
                      <Pill variant="quiet" icon aria-label={t`Edit`} title={t`Edit`} onClick={() => startEdit(bt)}>
                        <Pencil className="w-4 h-4" />
                      </Pill>
                      <Pill variant="destructive" icon aria-label={t`Delete`} title={t`Delete`} onClick={() => { setDeleteConfirmId(bt.id); setDeleteError(null) }}>
                        <Trash2 className="w-4 h-4" />
                      </Pill>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
    </PanelSection>
  )
}

// ── AuditTab ──────────────────────────────────────────────────────────────

const ACTION_CATEGORIES: { value: string; label: MessageDescriptor }[] = [
  { value: '', label: msg`All actions` },
  { value: 'auth', label: msg`Auth` },
  { value: 'books', label: msg`Books` },
  { value: 'users', label: msg`Users` },
  { value: 'libraries', label: msg`Libraries` },
]

/* eslint-disable lingui/no-unlocalized-strings -- Tailwind class map */
const ACTION_COLORS: Record<string, string> = {
  'auth.login': 'bg-success/10 text-success border-success/20',
  'auth.login_failed': 'bg-destructive/10 text-destructive border-destructive/20',
  'auth.logout': 'bg-muted text-muted-foreground border-border',
  'auth.password_changed': 'bg-info/10 text-info border-info/20',
  'auth.impersonated': 'bg-warning/10 text-warning border-warning/20',
  'books.downloaded': 'bg-primary/10 text-primary border-primary/20',
  'books.uploaded': 'bg-success/10 text-success border-success/20',
  'books.deleted': 'bg-destructive/10 text-destructive border-destructive/20',
  'books.metadata_edited': 'bg-info/10 text-info border-info/20',
  'books.bulk_metadata_edited': 'bg-info/10 text-info border-info/20',
  'books.metadata_applied': 'bg-info/10 text-info border-info/20',
  'users.created': 'bg-success/10 text-success border-success/20',
  'users.updated': 'bg-info/10 text-info border-info/20',
  'users.deleted': 'bg-destructive/10 text-destructive border-destructive/20',
  'libraries.created': 'bg-success/10 text-success border-success/20',
  'libraries.updated': 'bg-info/10 text-info border-info/20',
  'libraries.deleted': 'bg-destructive/10 text-destructive border-destructive/20',
}
/* eslint-enable lingui/no-unlocalized-strings */

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
  const { i18n } = useLingui()
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
    <PanelSection title={<Trans>Audit Log</Trans>}>
      <div className="flex items-end gap-3 flex-wrap mb-5">
        <div>
          <label className="block text-[13px] text-muted-foreground mb-1.5"><Trans>Action</Trans></label>
          <select value={filterAction} onChange={e => { setFilterAction(e.target.value); setPage(1) }}
            className="tome-select h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground focus:outline-none focus:ring-1 focus:ring-ring">
            {ACTION_CATEGORIES.map(c => <option key={c.value} value={c.value}>{i18n._(c.label)}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[13px] text-muted-foreground mb-1.5"><Trans>From</Trans></label>
          <input type="date" value={fromDate} onChange={e => { setFromDate(e.target.value); setPage(1) }}
            className="h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground focus:outline-none focus:ring-1 focus:ring-ring" />
        </div>
        <div>
          <label className="block text-[13px] text-muted-foreground mb-1.5"><Trans>To</Trans></label>
          <input type="date" value={toDate} onChange={e => { setToDate(e.target.value); setPage(1) }}
            className="h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground focus:outline-none focus:ring-1 focus:ring-ring" />
        </div>
      </div>

      {loading && items.length === 0 ? (
        <div className="flex justify-center py-16">
          <BookAnimation variant="refresh" className="block w-10 h-10 text-primary" />
        </div>
      ) : items.length === 0 ? (
        <div className="py-4 text-muted-foreground text-[14px]"><Trans>No audit entries yet.</Trans></div>
      ) : (
        <div className={cn('border-y border-border/70 divide-y divide-border/70 transition-opacity', loading && 'opacity-50')}>
          <div className={cn('hidden sm:flex items-center gap-3 py-2.5', TABLE_HEAD)}>
            <span className="w-7" />
            <span className="flex-1"><Trans>Action</Trans></span>
            <span className="w-28"><Trans>User</Trans></span>
            <span className="w-40"><Trans>Time</Trans></span>
            <span className="w-3.5" />
          </div>
          {items.map(entry => {
            // eslint-disable-next-line lingui/no-unlocalized-strings -- Tailwind classes
            const colorClass = ACTION_COLORS[entry.action] ?? 'bg-muted text-muted-foreground border-border'
            const isExpanded = expandedId === entry.id
            const parsed = entry.details ? (() => { try { return JSON.parse(entry.details) } catch { return null } })() : null
            return (
              <div key={entry.id}>
                <button className="w-full text-left flex items-center gap-3 py-2.5 hover:bg-secondary/50 transition-colors"
                  onClick={() => setExpandedId(isExpanded ? null : entry.id)}>
                  {/* User avatar */}
                  <div className="w-7 h-7 rounded-full bg-secondary flex items-center justify-center text-[12px] font-bold text-muted-foreground shrink-0">
                    {entry.username ? entry.username[0].toUpperCase() : '?'}
                  </div>
                  <div className="flex-1 min-w-0 flex items-center gap-2">
                    {/* Action badge */}
                    <span className={cn('text-[11px] font-mono px-1.5 py-0.5 rounded border shrink-0', colorClass)}>
                      {entry.action}
                    </span>
                    {/* Resource title */}
                    {entry.resource_title && (
                      <span className="text-[14px] text-foreground font-medium truncate min-w-0">
                        {entry.resource_title}
                      </span>
                    )}
                  </div>
                  <span className="w-28 text-[13px] text-muted-foreground hidden sm:block truncate">{entry.username ?? '—'}</span>
                  <span className="sm:w-40 text-[13px] text-muted-foreground tabular-nums shrink-0">{fmt(entry.created_at)}</span>
                  {isExpanded ? <ChevronUp className="w-3.5 h-3.5 text-muted-foreground shrink-0" /> : <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
                </button>
                {isExpanded && (
                  <div className="pb-3 pl-10 text-[13px] text-muted-foreground flex flex-col gap-1.5">
                    {entry.ip_address && <div><span className="font-medium text-foreground"><Trans>IP:</Trans></span> {entry.ip_address}</div>}
                    {entry.resource_type && entry.resource_id && (
                      <div><span className="font-medium text-foreground"><Trans>Resource:</Trans></span> {entry.resource_type} #{entry.resource_id}</div>
                    )}
                    {parsed && (
                      <div>
                        <span className="font-medium text-foreground"><Trans>Details:</Trans></span>
                        <pre className="mt-1.5 p-3 rounded-[var(--radius-panel)] bg-card text-[12px] overflow-x-auto">
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
        <div className="flex items-center justify-center gap-2 pt-5">
          <Pill variant="secondary" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>
            <Trans>Previous</Trans>
          </Pill>
          <span className="text-[13px] text-muted-foreground tabular-nums px-2">{page} / {totalPages}</span>
          <Pill variant="secondary" onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}>
            <Trans>Next</Trans>
          </Pill>
        </div>
      )}
    </PanelSection>
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
  if (mins < 1) return t`just now`
  if (mins < 60) return t`${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return t`${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 30) return t`${days}d ago`
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' })
}

const SYNC_STATUS_LABELS: Record<string, MessageDescriptor> = {
  unread: msg`unread`, reading: msg`reading`, read: msg`read`,
  shelved: msg`shelved`, want_to_read: msg`want to read`,
}

function StatusBadge({ status }: { status: SyncRecord['status'] }) {
  const { i18n } = useLingui()
  /* eslint-disable lingui/no-unlocalized-strings -- Tailwind classes */
  const cls =
    status === 'read'
      ? 'bg-success/10 text-success border-success/20'
      : status === 'reading'
        ? 'bg-info/10 text-info border-info/20'
        : 'bg-muted text-muted-foreground border-border'
  /* eslint-enable lingui/no-unlocalized-strings */
  return (
    <span className={cn('text-[10px] font-medium px-1.5 py-0.5 rounded border whitespace-nowrap', cls)}>
      {SYNC_STATUS_LABELS[status] ? i18n._(SYNC_STATUS_LABELS[status]) : status}
    </span>
  )
}

function SourceBadge({ source }: { source: SyncRecord['source'] }) {
  const { t } = useLingui()
  /* eslint-disable lingui/no-unlocalized-strings -- Tailwind classes */
  const cls =
    source === 'tomesync'
      ? 'bg-primary/10 text-primary border-primary/20'
      : 'bg-muted text-muted-foreground border-border'
  /* eslint-enable lingui/no-unlocalized-strings */
  return (
    <span className={cn('text-[10px] font-medium px-1.5 py-0.5 rounded border whitespace-nowrap', cls)}>
      {source === 'tomesync' ? 'TomeSync' : t`Web`}
    </span>
  )
}

function SyncStatusTab() {
  const { t } = useLingui()
  const [records, setRecords] = useState<SyncRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sortKey, setSortKey] = useState<SyncSortKey>('last_synced')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [deleting, setDeleting] = useState<string | null>(null)

  useEffect(() => {
    api.get<SyncRecord[]>('/admin/sync-status')
      .then(setRecords)
      .catch(() => setError(t`Failed to load sync status`))
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
        className={cn('px-2.5 first:pl-0 py-2.5 text-left whitespace-nowrap cursor-pointer select-none hover:text-foreground transition-colors', TABLE_HEAD, 'tracking-[0.04em]')}
        onClick={() => handleSort(col)}
      >
        <span className="flex items-center gap-0.5">
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
        <BookAnimation variant="refresh" className="block w-10 h-10 text-primary" />
      </div>
    )
  }

  if (error) {
    return (
      <PanelSection title={<Trans>Sync Status</Trans>}>
        <div className="px-3 py-2 rounded-lg bg-destructive/10 text-[14px] text-destructive">
          {error}
        </div>
      </PanelSection>
    )
  }

  return (
    <PanelSection
      title={<Trans>Sync Status</Trans>}
      action={
        <span className="flex items-center gap-1.5 text-[13px] font-normal text-muted-foreground">
          <Activity className="w-3.5 h-3.5" />
          <Plural value={records.length} one="# record" other="# records" />
        </span>
      }
    >
      {sorted.length === 0 ? (
        <div className="py-4 text-muted-foreground text-[14px]">
          <Trans>No reading activity yet. Records appear when users start reading books.</Trans>
        </div>
      ) : (
        <div className="border-y border-border/70">
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead className="border-b border-border/70">
                <tr>
                  <ColHeader label={t`Book`} col="book_title" />
                  <ColHeader label={t`User`} col="username" />
                  <ColHeader label={t`Status`} col="status" />
                  <ColHeader label={t`Progress`} col="progress_pct" />
                  <ColHeader label={t`Last Synced`} col="last_synced" />
                  <ColHeader label={t`Device`} col="device" />
                  <ColHeader label={t`Source`} col="source" />
                  <th className="pl-3 pr-0 py-2.5 w-10" />

                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {sorted.map((r, i) => {
                  const key = `${r.user_id}-${r.book_id}`
                  const isDeleting = deleting === key
                  return (
                  <tr key={`${key}-${i}`} className="hover:bg-secondary/50 transition-colors">
                    <td className="pl-0 pr-2.5 py-2.5 max-w-[200px]">
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
                    <td className="px-2.5 py-2.5 text-foreground font-medium whitespace-nowrap">{r.username}</td>
                    <td className="px-2.5 py-2.5"><StatusBadge status={r.status} /></td>
                    <td className="px-2.5 py-2.5 whitespace-nowrap">
                      {r.progress_pct != null
                        ? `${(r.progress_pct * 100).toFixed(1)}%`
                        : '—'}
                    </td>
                    <td className="px-2.5 py-2.5 whitespace-nowrap text-muted-foreground" title={r.last_synced ?? undefined}>
                      {relativeTime(r.last_synced)}
                    </td>
                    <td className="px-2.5 py-2.5 text-muted-foreground max-w-[140px] truncate" title={r.device ?? undefined}>
                      {r.device ?? '—'}
                    </td>
                    <td className="px-2.5 py-2.5"><SourceBadge source={r.source} /></td>
                    <td className="pl-3 pr-0 py-1.5 text-right">
                      <Pill
                        variant="destructive"
                        icon
                        onClick={() => handleDelete(r)}
                        disabled={isDeleting}
                        aria-label={t`Delete sync record`}
                        title={t`Delete sync record`}
                      >
                        {isDeleting
                          ? <Loader2 className="w-4 h-4 animate-spin" />
                          : <Trash2 className="w-4 h-4" />}
                      </Pill>
                    </td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </PanelSection>
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
  files: { id: number; format: string; file_size: number | null; path_exists: boolean }[]
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

const MATCH_REASON_LABEL: Record<DuplicateGroup['match_reason'], MessageDescriptor> = {
  content_hash: msg`Exact Match`,
  isbn: msg`Same ISBN`,
  same_series_volume: msg`Same Series Volume`,
  similar_title: msg`Similar Title`,
}

/* eslint-disable lingui/no-unlocalized-strings -- Tailwind class map */
const MATCH_REASON_STYLE: Record<DuplicateGroup['match_reason'], string> = {
  content_hash: 'bg-destructive/10 text-destructive border-destructive/20',
  isbn: 'bg-warning/10 text-warning border-warning/20',
  same_series_volume: 'bg-purple-500/10 text-purple-600 border-purple-500/20',
  similar_title: 'bg-info/10 text-info border-info/20',
}
/* eslint-enable lingui/no-unlocalized-strings */

function formatBytes(bytes: number | null): string {
  if (bytes == null) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

type GroupDecision = 'merge' | 'delete' | 'dismiss'

function DuplicatesTab() {
  const { t, i18n } = useLingui()
  const [groups, setGroups] = useState<DuplicateGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [keepIds, setKeepIds] = useState<Record<string, number>>({})
  const [decisions, setDecisions] = useState<Record<string, GroupDecision>>({})
  const [applying, setApplying] = useState(false)
  const [applyProgress, setApplyProgress] = useState<{ done: number; total: number } | null>(null)
  const [confirmApply, setConfirmApply] = useState(false)
  const [applyResult, setApplyResult] = useState<{ applied: number; failed: { label: string; error: string }[] } | null>(null)

  function fetchGroups() {
    setLoading(true)
    setError(null)
    setDecisions({})
    setConfirmApply(false)
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
      .catch(e => setError(e instanceof Error ? e.message : t`Failed to load duplicates`))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchGroups() }, [])

  function toggleDecision(groupId: string, action: GroupDecision) {
    setConfirmApply(false)
    setDecisions(prev => {
      const next = { ...prev }
      if (next[groupId] === action) delete next[groupId]
      else next[groupId] = action
      return next
    })
  }

  const decidedGroups = groups.filter(g => decisions[g.group_id])
  const mergeCount = decidedGroups.filter(g => decisions[g.group_id] === 'merge').length
  const dismissCount = decidedGroups.filter(g => decisions[g.group_id] === 'dismiss').length
  const deleteBookCount = decidedGroups
    .filter(g => decisions[g.group_id] === 'delete')
    .reduce((n, g) => n + g.books.filter(b => b.id !== keepIds[g.group_id]).length, 0)

  async function applyAll() {
    if (!decidedGroups.length) return
    setApplying(true)
    setApplyResult(null)
    setApplyProgress({ done: 0, total: decidedGroups.length })
    let applied = 0
    const failed: { label: string; error: string }[] = []
    for (let i = 0; i < decidedGroups.length; i++) {
      const group = decidedGroups[i]
      const action = decisions[group.group_id]
      const keepId = keepIds[group.group_id]
      const removeIds = group.books.map(b => b.id).filter(id => id !== keepId)
      const label = group.books.find(b => b.id === keepId)?.title ?? group.books[0]?.title ?? 'group'
      try {
        if (action === 'merge') {
          await api.post('/admin/duplicates/merge', { keep_id: keepId, remove_ids: removeIds })
        } else if (action === 'delete') {
          const res = await api.post<{ deleted: number[]; errors: { book_id: number; error: string }[] }>(
            '/books/bulk-delete',
            { book_ids: removeIds },
          )
          if (res.errors.length) {
            throw new Error(plural(res.errors.length, { one: '# book could not be deleted', other: '# books could not be deleted' }))
          }
        } else {
          await api.post('/admin/duplicates/dismiss', { book_ids: group.books.map(b => b.id) })
        }
        applied++
      } catch (e) {
        failed.push({ label, error: e instanceof Error ? e.message : t`Failed` })
      }
      setApplyProgress({ done: i + 1, total: decidedGroups.length })
    }
    setApplyResult({ applied, failed })
    setApplying(false)
    setApplyProgress(null)
    fetchGroups()
  }

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <BookAnimation variant="refresh" className="block w-10 h-10 text-primary" />
      </div>
    )
  }

  if (error) {
    return (
      <PanelSection title={<Trans>Duplicate Detection</Trans>}>
        <div className="px-3 py-2 rounded-lg bg-destructive/10 text-[14px] text-destructive">
          {error}
        </div>
      </PanelSection>
    )
  }

  return (
    <PanelSection
      title={<Trans>Duplicate Detection</Trans>}
      description={groups.length > 0 ? <>
        {plural(groups.length, { one: '# duplicate group found.', other: '# duplicate groups found.' })}{' '}
        <Trans>Pick which book to keep and an action per group — Merge, Delete Others, or Dismiss — then apply everything at once.</Trans>
      </> : undefined}
      action={
        <Pill
          onClick={fetchGroups}
          variant="secondary"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          <Trans>Refresh</Trans>
        </Pill>
      }
    >
    <div className="flex flex-col gap-6">
      {applyResult && (
        <div className={cn(
          'rounded-lg px-4 py-3 text-[13px] space-y-1',
          applyResult.failed.length
            ? 'bg-warning/10'
            : 'bg-success/10',
        )}>
          <p className={cn('font-medium', applyResult.failed.length ? 'text-warning' : 'text-success')}>
            <Plural value={applyResult.applied} one="Applied # group" other="Applied # groups" />
            {applyResult.failed.length > 0 && (() => { const n = applyResult.failed.length; return t`, ${n} failed` })()}
          </p>
          {applyResult.failed.length > 0 && (
            <ul className="space-y-0.5 text-muted-foreground">
              {applyResult.failed.map((f, i) => <li key={i}>{f.label}: {f.error}</li>)}
            </ul>
          )}
        </div>
      )}

      {groups.length === 0 ? (
        <div className="py-4 text-muted-foreground text-[14px]">
          <Trans>No duplicates found. Your library looks clean.</Trans>
        </div>
      ) : (
        <div className="flex flex-col border-t border-border/70">
          {groups.map(group => {
            const decision = decisions[group.group_id]
            const othersCount = group.books.filter(b => b.id !== keepIds[group.group_id]).length
            return (
              <div key={group.group_id} data-testid="duplicate-group" className="border-b border-border/70 py-5">
                {/* Group header */}
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <span className={cn(
                    'text-[12px] font-medium px-2 py-0.5 rounded border',
                    MATCH_REASON_STYLE[group.match_reason],
                  )}>
                    {i18n._(MATCH_REASON_LABEL[group.match_reason])}
                  </span>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <button
                      onClick={() => toggleDecision(group.group_id, 'dismiss')}
                      disabled={applying}
                      className={cn(
                        'flex items-center gap-1.5 h-9 px-4 text-[14px] font-medium rounded-full transition-colors disabled:opacity-50',
                        decision === 'dismiss'
                          ? 'bg-foreground text-background'
                          : 'bg-secondary text-foreground hover:bg-muted',
                      )}
                      title={t`Queue: not a duplicate — never show this group again`}
                    >
                      <X className="w-3.5 h-3.5" />
                      <Trans>Dismiss</Trans>
                    </button>
                    <button
                      onClick={() => toggleDecision(group.group_id, 'merge')}
                      disabled={applying || keepIds[group.group_id] == null}
                      className={cn(
                        'flex items-center gap-1.5 h-9 px-4 text-[14px] font-medium rounded-full transition-colors disabled:opacity-50',
                        decision === 'merge'
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-secondary text-foreground hover:bg-muted',
                      )}
                      title={t`Queue: fold the other copies into the kept book`}
                    >
                      <GitMerge className="w-3.5 h-3.5" />
                      <Trans>Merge</Trans>
                    </button>
                    <button
                      onClick={() => toggleDecision(group.group_id, 'delete')}
                      disabled={applying || keepIds[group.group_id] == null}
                      className={cn(
                        'flex items-center gap-1.5 h-9 px-4 text-[14px] font-medium rounded-full transition-colors disabled:opacity-50',
                        decision === 'delete'
                          ? 'bg-destructive text-destructive-foreground'
                          : 'text-muted-foreground hover:text-destructive hover:bg-destructive/10',
                      )}
                      title={t`Queue: keep the selected book and delete the others — their files are removed from disk`}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      {decision === 'delete' ? plural(othersCount, { one: 'Delete # other', other: 'Delete # others' }) : t`Delete Others`}
                    </button>
                  </div>
                </div>

                {/* Books */}
                <div className="flex flex-wrap gap-3 pt-4">
                  {group.books.map(book => {
                    const isKeep = keepIds[group.group_id] === book.id
                    return (
                      <label
                        key={book.id}
                        className={cn(
                          'flex flex-col gap-2 p-3 rounded-lg cursor-pointer transition-colors select-none w-full sm:w-60',
                          isKeep
                            ? 'bg-primary/10 ring-1 ring-primary/40'
                            : 'bg-card hover:bg-secondary',
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
                          <div className="relative w-10 h-14 rounded-[6px] bg-muted shrink-0 overflow-hidden" style={{ boxShadow: 'var(--cover-bloom-fan)' }}>
                            <CoverImage
                              src={book.cover_path ? coverSrc(book.id, { w: 160 }) : null}
                              alt=""
                              iconClassName="w-4 h-4"
                            />
                          </div>
                          {/* Meta */}
                          <div className="flex flex-col gap-0.5 min-w-0 flex-1">
                            <p className="text-[14px] font-medium text-foreground leading-snug line-clamp-2">{book.title}</p>
                            {book.subtitle && (
                              <p className="text-[12px] text-muted-foreground line-clamp-1">{book.subtitle}</p>
                            )}
                            {book.author && (
                              <p className="text-[12px] text-muted-foreground">{book.author}</p>
                            )}
                            {book.year && (
                              <p className="text-[12px] text-muted-foreground">{book.year}</p>
                            )}
                          </div>
                        </div>
                        {/* Files */}
                        {book.files.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {book.files.map(f => (
                              <span
                                key={f.id}
                                className={cn(
                                  'text-[10px] px-1.5 py-0.5 rounded border font-mono',
                                  f.path_exists
                                    ? 'bg-muted text-muted-foreground border-border'
                                    : 'bg-destructive/10 text-destructive border-destructive/20',
                                )}
                                title={f.path_exists ? undefined : t`File is missing from disk`}
                              >
                                {f.format.toUpperCase()} {formatBytes(f.file_size)}
                                {!f.path_exists && t` — MISSING`}
                              </span>
                            ))}
                          </div>
                        )}
                        {isKeep && (
                          <span className="text-[12px] font-medium text-primary"><Trans>Keep this one</Trans></span>
                        )}
                      </label>
                    )
                  })}
                </div>
              </div>
            )
          })}

          {(decidedGroups.length > 0 || applying) && (
            <div className="sticky bottom-16 sm:bottom-4 z-10 mt-5 flex flex-wrap items-center justify-between gap-3 px-4 py-3 rounded-[var(--radius-panel)] bg-card shadow-lg ring-1 ring-border/70">
              {/* bottom-16 below sm clears the fixed keyboard-shortcuts FAB */}
              <span className="text-[13px] text-muted-foreground min-w-0">
                {[
                  mergeCount > 0 ? plural(mergeCount, { one: 'merge # group', other: 'merge # groups' }) : null,
                  deleteBookCount > 0 ? plural(deleteBookCount, { one: 'delete # book (removes files)', other: 'delete # books (removes files)' }) : null,
                  dismissCount > 0 ? plural(dismissCount, { one: 'dismiss # group', other: 'dismiss # groups' }) : null,
                ].filter(Boolean).join(' · ')}
              </span>
              <div className="flex items-center gap-2 shrink-0 ml-auto">
                {confirmApply && !applying && (
                  <Pill
                    onClick={() => setConfirmApply(false)}
                    variant="secondary"
                  >
                    <Trans>Cancel</Trans>
                  </Pill>
                )}
                <Pill
                  onClick={() => { setDecisions({}); setConfirmApply(false) }}
                  disabled={applying}
                  variant="secondary"
                >
                  <Trans>Clear</Trans>
                </Pill>
                <button
                  onClick={() => confirmApply ? applyAll() : setConfirmApply(true)}
                  disabled={applying}
                  className={cn(
                    'flex items-center gap-1.5 h-9 px-4 text-[14px] font-semibold rounded-full transition-opacity disabled:opacity-50',
                    deleteBookCount > 0
                      ? 'bg-destructive text-destructive-foreground hover:opacity-90'
                      : 'bg-primary text-primary-foreground hover:opacity-90',
                  )}
                >
                  {applying && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {(() => {
                    const done = applyProgress?.done ?? 0
                    const total = applyProgress?.total ?? decidedGroups.length
                    const n = decidedGroups.length
                    return applying
                      ? t`Applying ${done}/${total}…`
                      : confirmApply
                      ? plural(n, { one: 'Confirm — apply # decision', other: 'Confirm — apply # decisions' })
                      : t`Apply All (${n})`
                  })()}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
    </PanelSection>
  )
}

// ── WishlistTab ────────────────────────────────────────────────────────────

type WishStatus = 'open' | 'fulfilled' | 'dismissed'

interface BookSearchResult {
  id: number
  title: string
  author: string | null
  series: string | null
  series_index: number | null
  cover_path: string | null
}

function wishAgeLabel(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const days = Math.floor(diff / 86_400_000)
  if (days === 0) return t`Today`
  if (days === 1) return t`Yesterday`
  if (days < 30) return t`${days}d ago`
  const months = Math.floor(days / 30)
  if (months < 12) return t`${months}mo ago`
  const years = Math.floor(months / 12)
  return t`${years}y ago`
}

function FulfillPicker({
  wish,
  onFulfill,
  onCancel,
}: {
  wish: WishAdminOut
  onFulfill: (bookId: number | null) => Promise<void>
  onCancel: () => void
}) {
  const { t } = useLingui()
  const isWholeSeries = !!wish.series && wish.series_index == null
  const [suggestedBooks, setSuggestedBooks] = useState<BookSearchResult[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<BookSearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [fulfilling, setFulfilling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Load suggested books if any
  useEffect(() => {
    if (!wish.suggested_book_ids || wish.suggested_book_ids.length === 0) return
    Promise.all(
      wish.suggested_book_ids.slice(0, 5).map(id =>
        api.get<BookSearchResult>(`/books/${id}`).catch(() => null)
      )
    ).then(results => {
      setSuggestedBooks(results.filter((b): b is BookSearchResult => b != null))
      if (results[0]) setSelectedId(results[0].id)
    })
  }, [wish.suggested_book_ids])

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) { setSearchResults([]); return }
    setSearching(true)
    try {
      const books = await api.get<BookSearchResult[]>(`/books?q=${encodeURIComponent(q)}&limit=10`)
      setSearchResults(books)
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }, [])

  function handleSearchChange(val: string) {
    setSearchQuery(val)
    setSelectedId(null)
    if (searchTimer.current) clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => doSearch(val), 350)
  }

  async function handleSubmit() {
    if (!selectedId) { setError(t`Select a book first.`); return }
    setFulfilling(true)
    setError(null)
    try {
      await onFulfill(selectedId)
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Fulfill failed`)
      setFulfilling(false)
    }
  }

  async function handleComplete() {
    setFulfilling(true)
    setError(null)
    try {
      await onFulfill(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Failed to complete series`)
      setFulfilling(false)
    }
  }

  const displayBooks = searchQuery.trim() ? searchResults : suggestedBooks

  // Whole-series wishes are standing wants — they are not fulfilled by a single
  // volume. Instead of the book picker, offer a deliberate "mark complete".
  if (isWholeSeries) {
    const n = wish.suggested_book_ids?.length ?? 0
    return (
      <div className="rounded-[var(--radius-panel)] bg-card p-4 space-y-3">
        <div className="flex items-center justify-between">
          {(() => { const title = wish.title; return <p className="text-[14px] font-semibold text-foreground"><Trans>Complete series: {title}</Trans></p> })()}
          <Pill variant="quiet" icon aria-label={t`Cancel`} title={t`Cancel`} onClick={onCancel}>
            <X className="w-4 h-4" />
          </Pill>
        </div>
        <p className="text-[13px] text-muted-foreground leading-relaxed">
          <Trans>This is a whole-series wish — it stays open as volumes arrive.</Trans>{' '}
          {n > 0
            ? plural(n, { one: '# matching volume currently in the library.', other: '# matching volumes currently in the library.' })
            : t`No matching volumes in the library yet.`}{' '}
          <Trans>Mark it complete when you consider the series fully available; the requester will be notified.</Trans>
        </p>
        {error && <p className="text-[13px] text-destructive">{error}</p>}
        <div className="flex items-center justify-end gap-2 pt-1">
          <Pill onClick={onCancel} variant="secondary">
            <Trans>Cancel</Trans>
          </Pill>
          <Pill
            onClick={handleComplete}
            disabled={fulfilling}
            variant="primary"
          >
            {fulfilling ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
            <Trans>Mark complete</Trans>
          </Pill>
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-[var(--radius-panel)] bg-card p-4 space-y-3">
      <div className="flex items-center justify-between">
        {(() => { const title = wish.title; return <p className="text-[14px] font-semibold text-foreground"><Trans>Fulfill: {title}</Trans></p> })()}
        <Pill variant="quiet" icon aria-label={t`Cancel`} title={t`Cancel`} onClick={onCancel}>
          <X className="w-4 h-4" />
        </Pill>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
        <input
          value={searchQuery}
          onChange={e => handleSearchChange(e.target.value)}
          placeholder={suggestedBooks.length > 0 ? t`Search for a different book…` : t`Search for a book in the library…`}
          className="w-full pl-8 pr-3 h-9 rounded-lg bg-secondary text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        />
        {searching && <Loader2 className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-muted-foreground animate-spin" />}
      </div>

      {/* Suggested / search results */}
      {!searchQuery && suggestedBooks.length > 0 && (
        <p className="text-[12px] text-muted-foreground"><Plural value={suggestedBooks.length}
 one="Suggested match:" other="Suggested matches:" /></p>
      )}
      {displayBooks.length > 0 && (
        <div className="space-y-1.5 max-h-48 overflow-y-auto">
          {displayBooks.map(book => (
            <button
              key={book.id}
              onClick={() => setSelectedId(book.id)}
              className={cn(
                'w-full flex items-center gap-3 p-2 rounded-lg text-left transition-colors',
                selectedId === book.id
                  ? 'bg-primary/10 ring-1 ring-primary/40'
                  : 'hover:bg-secondary'
              )}
            >
              <div className="w-7 h-10 rounded bg-muted shrink-0 overflow-hidden flex items-center justify-center">
                {book.cover_path ? (
                  <img src={coverSrc(book.id, { w: 160 })} alt="" className="w-full h-full object-cover" />
                ) : (
                  <BookOpen className="w-3 h-3 text-muted-foreground" />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-[14px] font-medium text-foreground truncate">{book.title}</p>
                {book.author && <p className="text-[12px] text-muted-foreground truncate">{book.author}</p>}
              </div>
            </button>
          ))}
        </div>
      )}

      {!searchQuery && suggestedBooks.length === 0 && !searching && (
        <p className="text-[13px] text-muted-foreground"><Trans>No suggested match yet — search for the book to link, or upload it first.</Trans></p>
      )}
      {searchQuery && !searching && searchResults.length === 0 && (
        <p className="text-[13px] text-muted-foreground"><Trans>No books found. Upload the book first, then fulfill.</Trans></p>
      )}

      {error && <p className="text-[13px] text-destructive">{error}</p>}

      <div className="flex items-center justify-end gap-2 pt-1">
        <Pill onClick={onCancel} variant="secondary">
          <Trans>Cancel</Trans>
        </Pill>
        <Pill
          onClick={handleSubmit}
          disabled={fulfilling || !selectedId}
          variant="primary"
        >
          {fulfilling && <Loader2 className="w-3 h-3 animate-spin" />}
          <Trans>Fulfill</Trans>
        </Pill>
      </div>
    </div>
  )
}

function WishlistTab() {
  const { t } = useLingui()
  const [statusFilter, setStatusFilter] = useState<WishStatus>('open')
  const [wishes, setWishes] = useState<WishAdminOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [fulfillingId, setFulfillingId] = useState<number | null>(null)
  const [dismissConfirmId, setDismissConfirmId] = useState<number | null>(null)
  const [dismissing, setDismissing] = useState<number | null>(null)
  const [actionError, setActionError] = useState<Record<number, string>>({})

  function load() {
    setLoading(true)
    setError(null)
    adminListWishes({ status: statusFilter })
      .then(setWishes)
      .catch(e => setError(e instanceof Error ? e.message : t`Failed to load wishlist`))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [statusFilter])

  async function handleFulfill(wishId: number, bookId: number | null) {
    try {
      await fulfillWish(wishId, bookId)
      setFulfillingId(null)
      load()
    } catch (e) {
      setActionError(prev => ({ ...prev, [wishId]: e instanceof Error ? e.message : t`Fulfill failed` }))
      throw e
    }
  }

  async function handleDismiss(wishId: number) {
    setDismissing(wishId)
    setActionError(prev => { const n = { ...prev }; delete n[wishId]; return n })
    try {
      await dismissWish(wishId)
      setDismissConfirmId(null)
      load()
    } catch (e) {
      setActionError(prev => ({ ...prev, [wishId]: e instanceof Error ? e.message : t`Dismiss failed` }))
    } finally {
      setDismissing(null)
    }
  }

  const STATUS_TABS: { id: WishStatus; label: string }[] = [
    { id: 'open', label: t`Open` },
    { id: 'fulfilled', label: t`Fulfilled` },
    { id: 'dismissed', label: t`Dismissed` },
  ]

  return (
    <PanelSection
      title={<Trans>Wishlist</Trans>}
      description={
        <a
          href={docsLink(DOCS.wishlist)}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 hover:text-primary transition-colors"
        >
          <Trans>Learn more</Trans> <ExternalLink className="w-3 h-3" />
        </a>
      }
    >
    <div className="space-y-5">
      <div className="flex items-center gap-1.5 flex-wrap">
        {STATUS_TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setStatusFilter(t.id)}
            className={cn(
              'inline-flex items-center h-8 px-3.5 rounded-full text-[13px] transition-colors',
              statusFilter === t.id
                ? 'bg-primary/10 text-foreground font-medium'
                : 'bg-secondary text-muted-foreground hover:text-foreground'
            )}
          >
            {t.label}
          </button>
        ))}
        <Pill variant="quiet" icon onClick={load} aria-label={t`Refresh`} title={t`Refresh`}>
          <RefreshCw className="w-4 h-4" />
        </Pill>
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg bg-destructive/10 text-[14px] text-destructive">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <BookAnimation variant="refresh" className="block w-10 h-10 text-primary" />
        </div>
      ) : wishes.length === 0 ? (
        <div className="py-4 text-muted-foreground text-[14px]">
          {statusFilter === 'open' ? <Trans>No open wishes.</Trans>
            : statusFilter === 'fulfilled' ? <Trans>No fulfilled wishes.</Trans>
            : <Trans>No dismissed wishes.</Trans>}
        </div>
      ) : (
        <div className="border-y border-border/70 divide-y divide-border/70">
          {wishes.map(wish => (
            <div key={wish.id}>
              <div className="flex items-start gap-4 py-3.5">
                {/* Cover */}
                <div className="w-10 h-[60px] rounded-[6px] bg-muted shrink-0 overflow-hidden flex items-center justify-center" style={{ boxShadow: 'var(--cover-bloom-fan)' }}>
                  {wish.cover_url ? (
                    <img
                      src={wish.cover_url}
                      alt=""
                      className="w-full h-full object-cover"
                      onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
                    />
                  ) : (
                    <BookOpen className="w-4 h-4 text-muted-foreground" />
                  )}
                </div>

                {/* Meta */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-[15px] font-medium text-foreground leading-snug line-clamp-2">{wish.title}</p>
                      {wish.author && <p className="text-[13px] text-muted-foreground truncate">{wish.author}</p>}
                      {wish.series && wish.series_index != null && (
                        <p className="text-[13px] text-muted-foreground/70">
                          {wish.series} #{wish.series_index}
                        </p>
                      )}
                      {wish.series && wish.series_index == null && (
                        <span className="mt-0.5 self-start inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-muted border border-border text-muted-foreground font-medium">
                          <Layers className="w-2.5 h-2.5" />
                          <Trans>Whole series</Trans>
                        </span>
                      )}
                      {wish.note && (
                        <p className="text-[13px] text-muted-foreground italic mt-0.5 line-clamp-1">{wish.note}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <span className="hidden sm:block text-[13px] text-muted-foreground whitespace-nowrap mr-1">
                        {wish.requester_username && (
                          <span className="mr-1 font-medium text-foreground">{wish.requester_username}</span>
                        )}
                        {wishAgeLabel(wish.created_at)}
                      </span>
                      {wish.status === 'open' && (
                        <>
                          {dismissConfirmId === wish.id ? (
                            <div className="flex items-center gap-1">
                              <Pill
                                onClick={() => handleDismiss(wish.id)}
                                disabled={dismissing === wish.id}
                                variant="destructive" className="bg-destructive/10 text-destructive"
                              >
                                {dismissing === wish.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                                <Trans>Confirm</Trans>
                              </Pill>
                              <Pill variant="quiet" icon aria-label={t`Cancel`} title={t`Cancel`} onClick={() => setDismissConfirmId(null)}>
                                <X className="w-4 h-4" />
                              </Pill>
                            </div>
                          ) : (
                            <Pill
                              onClick={() => { setDismissConfirmId(wish.id); setFulfillingId(null) }}
                              variant="secondary"
                              title={t`Dismiss wish`}
                            >
                              <X className="w-3 h-3" />
                              <Trans>Dismiss</Trans>
                            </Pill>
                          )}
                          <Pill
                            onClick={() => {
                              setFulfillingId(wish.id)
                              setDismissConfirmId(null)
                              setActionError(prev => { const n = { ...prev }; delete n[wish.id]; return n })
                            }}
                            variant="primary"
                          >
                            <Check className="w-3 h-3" />
                            {wish.series && wish.series_index == null ? t`Complete` : t`Fulfill`}
                          </Pill>
                        </>
                      )}
                      {wish.status === 'fulfilled' && wish.fulfilled_book_id && (
                        <Link
                          to={`/books/${wish.fulfilled_book_id}`}
                          className="text-[14px] text-primary hover:underline"
                        >
                          <Trans>View book</Trans>
                        </Link>
                      )}
                    </div>
                  </div>

                  {/* Whole-series wishes: coverage strip (volumes present, gaps shown) */}
                  {wish.status === 'open' && fulfillingId !== wish.id && wish.series && wish.series_index == null && wish.series_coverage && wish.series_coverage.length > 0 && (
                    <SeriesCoverageStrip coverage={wish.series_coverage} total={wish.series_total} />
                  )}
                  {/* Single-book wishes: suggested-match hint */}
                  {wish.suggested_book_ids && wish.suggested_book_ids.length > 0 && wish.status === 'open' && fulfillingId !== wish.id && !(wish.series && wish.series_index == null) && (
                    <div className="mt-1.5 flex items-center gap-1.5">
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-warning/10 text-warning border border-warning/20 font-medium">
                        <Plural value={wish.suggested_book_ids.length} one="# suggested match" other="# suggested matches" />
                      </span>
                    </div>
                  )}

                  {actionError[wish.id] && (
                    <p className="text-[13px] text-destructive mt-1">{actionError[wish.id]}</p>
                  )}
                </div>
              </div>

              {/* Fulfill picker (inline) */}
              {fulfillingId === wish.id && (
                <div className="pb-4 sm:pl-14">
                  <FulfillPicker
                    wish={wish}
                    onFulfill={(bookId) => handleFulfill(wish.id, bookId)}
                    onCancel={() => setFulfillingId(null)}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
    </PanelSection>
  )
}

// ── AdminPage ─────────────────────────────────────────────────────────────

type Tab = 'users' | 'scanner' | 'server' | 'types' | 'audit' | 'metadata' | 'library' | 'wordcount' | 'sync' | 'duplicates' | 'covers' | 'email' | 'wishlist'

export function AdminPage() {
  const { t } = useLingui()
  const { user } = useAuth()
  const [tab, setTab] = useState<Tab>('users')

  if (!isAdmin(user)) {
    return (
      <AppShell>
        <PageBody width="reading">
          <div className="flex flex-col items-center justify-center py-24 gap-3 text-center">
            <Shield className="w-10 h-10 text-muted-foreground" />
            <p className="text-[15px] text-muted-foreground"><Trans>Admin access required.</Trans></p>
            <Link to="/" className="text-[14px] text-primary hover:underline"><Trans>Go back</Trans></Link>
          </div>
        </PageBody>
      </AppShell>
    )
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: 'users', label: t`Users` },
    { id: 'scanner', label: t`Scanner` },
    { id: 'server', label: t`Server` },
    { id: 'types', label: t`Types` },
    { id: 'audit', label: t`Audit Log` },
    { id: 'metadata', label: t`Metadata` },
    { id: 'library', label: t`Library` },
    { id: 'wordcount', label: t`Word Counts` },
    { id: 'sync', label: t`Sync Status` },
    { id: 'duplicates', label: t`Duplicates` },
    { id: 'covers', label: t`Covers` },
    { id: 'email', label: t`Email` },
    { id: 'wishlist', label: t`Wishlist` },
  ]

  return (
    <AppShell sidebar={<SectionSidebar title={<Trans>Admin</Trans>} />}>
      <SectionLayout
        sidebar
        title={<Trans>Admin</Trans>}
        eyebrow={tabs.find(x => x.id === tab)?.label}
        nav={
          <SectionNav
            items={tabs}
            active={tab}
            onSelect={id => setTab(id as Tab)}
            label={t`Admin sections`}
            placement="sidebar"
          />
        }
      >
        <div className="flex flex-col gap-12">
          {tab === 'users' && <UsersTab />}
          {tab === 'scanner' && <ScannerTab />}
          {tab === 'server' && <ServerTab />}
          {tab === 'types' && <TypesTab />}
          {tab === 'audit' && <AuditTab />}
          {tab === 'metadata' && (
            <PanelSection title={<Trans>Metadata</Trans>}>
              <MetadataManager />
            </PanelSection>
          )}
          {tab === 'library' && (
            <PanelSection title={<Trans>Library health</Trans>}>
              <LibraryHealthTab />
            </PanelSection>
          )}
          {tab === 'wordcount' && (
            <PanelSection title={<Trans>Word counts</Trans>}>
              <WordCountTab />
            </PanelSection>
          )}
          {tab === 'sync' && <SyncStatusTab />}
          {tab === 'duplicates' && <DuplicatesTab />}
          {tab === 'covers' && (
            <PanelSection
              title={<Trans>Covers</Trans>}
              description={<Trans>Books with a missing or low-resolution cover.</Trans>}
            >
              <CoverAudit />
            </PanelSection>
          )}
          {tab === 'email' && <EmailTab />}
          {tab === 'wishlist' && <WishlistTab />}
        </div>
      </SectionLayout>
    </AppShell>
  )
}

// ── Email Tab ────────────────────────────────────────────────────────────────

interface SmtpStatusDetail {
  configured: boolean
  host: string | null
  port: number
  from_address: string | null
}

interface AdminDevice {
  id: number
  username: string
  device_name: string
  device_email: string
  created_at: string
}

interface SendHistoryEntry {
  id: number
  username: string | null
  book_title: string | null
  device_email: string | null
  device_name: string | null
  status: string | null
  format: string | null
  created_at: string
}

function EmailTab() {
  const { t, i18n } = useLingui()
  const [smtpStatus, setSmtpStatus] = useState<SmtpStatusDetail | null>(null)
  const [allDevices, setAllDevices] = useState<AdminDevice[]>([])
  const [history, setHistory] = useState<SendHistoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [testEmail, setTestEmail] = useState('')
  const [testSending, setTestSending] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null)

  useEffect(() => {
    Promise.all([
      api.get<SmtpStatusDetail>('/admin/smtp-status').catch(() => null),
      api.get<AdminDevice[]>('/admin/devices').catch(() => []),
      api.get<SendHistoryEntry[]>('/admin/send-history').catch(() => []),
    ]).then(([smtp, devices, hist]) => {
      setSmtpStatus(smtp)
      setAllDevices(devices)
      setHistory(hist)
      setLoading(false)
    })
  }, [])

  async function handleTestEmail(e: React.FormEvent) {
    e.preventDefault()
    if (!testEmail.trim()) return
    setTestSending(true)
    setTestResult(null)
    try {
      await api.post('/admin/smtp-test', { email: testEmail.trim() })
      setTestResult({ ok: true })
    } catch (err) {
      setTestResult({ ok: false, error: err instanceof Error ? err.message : t`Send failed` })
    } finally {
      setTestSending(false)
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <>
      {/* SMTP Status */}
      <PanelSection
        title={<Trans>SMTP Status</Trans>}
        action={
          <a href={docsLink(DOCS.sendToDevice)} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[14px] font-normal text-muted-foreground hover:text-primary transition-colors">
            <Trans>Learn more</Trans> <ExternalLink className="w-3 h-3" />
          </a>
        }
      >
        {smtpStatus?.configured ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-success" />
              <span className="text-xs font-medium text-success"><Trans>Configured</Trans></span>
            </div>
            <div className="border-y border-border/70 text-[14px] divide-y divide-border/70">
              <div className="flex items-center gap-3 py-2.5">
                <span className="text-muted-foreground w-20 shrink-0"><Trans>Host</Trans></span>
                <span className="font-mono text-foreground">{smtpStatus.host}</span>
              </div>
              <div className="flex items-center gap-3 py-2.5">
                <span className="text-muted-foreground w-20 shrink-0"><Trans>Port</Trans></span>
                <span className="font-mono text-foreground">{smtpStatus.port}</span>
              </div>
              <div className="flex items-center gap-3 py-2.5">
                <span className="text-muted-foreground w-20 shrink-0"><Trans>From</Trans></span>
                <span className="font-mono text-foreground">{smtpStatus.from_address || t`(not set)`}</span>
              </div>
            </div>

            {/* Test email */}
            <form onSubmit={handleTestEmail} className="flex items-end gap-2 max-w-md pt-2">
              <div className="flex-1">
                <label className="block text-xs font-medium text-muted-foreground mb-1"><Trans>Send test email</Trans></label>
                <input
                  type="email"
                  value={testEmail}
                  onChange={e => { setTestEmail(e.target.value); setTestResult(null) }}
                  placeholder="test@example.com"
                  className="w-full h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>
              <Pill
                type="submit"
                disabled={testSending || !testEmail.trim()}
                variant="primary" className="shrink-0"
              >
                {testSending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                {testSending ? t`Sending…` : t`Test`}
              </Pill>
            </form>
            {testResult?.ok && <p className="text-[13px] text-success"><Trans>Test email sent successfully.</Trans></p>}
            {testResult && !testResult.ok && <p className="text-[13px] text-destructive">{testResult.error}</p>}
          </div>
        ) : (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-warning" />
              <span className="text-xs font-medium text-warning dark:text-warning"><Trans>Not configured</Trans></span>
            </div>
            <p className="text-[13px] text-muted-foreground">
              <Trans>Set the following environment variables to enable Send to Device:</Trans>
            </p>
            <code className="block text-[13px] font-mono rounded-[var(--radius-panel)] bg-card px-4 py-3 text-muted-foreground whitespace-pre-wrap">TOME_SMTP_HOST{'\n'}TOME_SMTP_USER{'\n'}TOME_SMTP_PASSWORD</code>
          </div>
        )}
      </PanelSection>

      {/* All Devices */}
      <PanelSection
        title={<Trans>All Devices</Trans>}
        action={<span className="text-[14px] font-normal text-muted-foreground tabular-nums">{allDevices.length}</span>}
      >
        {allDevices.length > 0 ? (
          <div className="border-y border-border/70 text-[13px] divide-y divide-border/70">
            <div className="hidden sm:grid grid-cols-[8rem_1fr_1fr_7rem] py-2.5 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
              <span><Trans>User</Trans></span>
              <span><Trans>Device</Trans></span>
              <span><Trans>Email</Trans></span>
              <span><Trans>Added</Trans></span>
            </div>
            {allDevices.map(d => (
              <div key={d.id} className="flex sm:grid sm:grid-cols-[8rem_1fr_1fr_7rem] items-center gap-2 sm:gap-0 py-2.5">
                <span className="text-foreground font-medium truncate">{d.username}</span>
                <span className="text-foreground truncate">{d.device_name}</span>
                <span className="text-muted-foreground font-mono truncate">{d.device_email}</span>
                <span className="text-muted-foreground hidden sm:block">{new Date(d.created_at).toLocaleDateString(i18n.locale)}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[14px] text-muted-foreground"><Trans>No users have added devices yet.</Trans></p>
        )}
      </PanelSection>

      {/* Send History */}
      <PanelSection
        title={<Trans>Send History</Trans>}
        action={<span className="text-[14px] font-normal text-muted-foreground"><Trans>(last 100)</Trans></span>}
      >
        {history.length > 0 ? (
          <div className="border-y border-border/70 text-[13px] divide-y divide-border/70">
            <div className="hidden sm:grid grid-cols-[10rem_6rem_1fr_1fr_5rem] py-2.5 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
              <span><Trans>Time</Trans></span>
              <span><Trans>User</Trans></span>
              <span><Trans>Book</Trans></span>
              <span><Trans>Device</Trans></span>
              <span><Trans>Status</Trans></span>
            </div>
            {history.map(h => (
              <div key={h.id} className="flex sm:grid sm:grid-cols-[10rem_6rem_1fr_1fr_5rem] items-center gap-2 sm:gap-0 py-2.5">
                <span className="text-muted-foreground shrink-0">
                  {new Date(h.created_at).toLocaleString(i18n.locale, {
                    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                  })}
                </span>
                <span className="text-foreground font-medium truncate">{h.username}</span>
                <span className="text-foreground truncate">{h.book_title}</span>
                <span className="text-muted-foreground truncate">{h.device_name || h.device_email}</span>
                <span className={cn(
                  'text-xs font-medium',
                  h.status === 'ok' ? 'text-success' : 'text-destructive'
                )}>
                  {h.status === 'ok' ? t`Sent` : t`Failed`}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[14px] text-muted-foreground"><Trans>No books have been sent yet.</Trans></p>
        )}
      </PanelSection>
    </>
  )
}
