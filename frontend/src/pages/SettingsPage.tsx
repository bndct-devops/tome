import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowLeft, Eye, EyeOff, Download, Check, RefreshCw, Loader2,
  Copy, Trash2, Plus, Key, Smartphone, CheckCircle, Info,
} from 'lucide-react'
import { ThemeToggle } from '@/components/ThemeToggle'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { applyTheme, getStoredTheme, THEMES, type ThemeId } from '@/lib/theme'
import { useAuth } from '@/contexts/AuthContext'

interface ApiKey {
  id: number
  label: string
  key_preview: string
  created_at: string
  last_used_at: string | null
}

interface OpdsPin {
  id: number
  label: string
  pin_preview: string
  created_at: string
  last_used_at: string | null
}

export function SettingsPage() {
  const { user, refreshUser } = useAuth()

  // ── Profile ───────────────────────────────────────────────────────────────
  const [profileUsername, setProfileUsername] = useState(user?.username ?? '')
  const [profileEmail, setProfileEmail] = useState(user?.email ?? '')
  const [profileSaving, setProfileSaving] = useState(false)
  const [profileError, setProfileError] = useState<string | null>(null)
  const [profileSuccess, setProfileSuccess] = useState(false)

  useEffect(() => {
    setProfileUsername(user?.username ?? '')
    setProfileEmail(user?.email ?? '')
  }, [user])

  const profileChanged = profileUsername !== (user?.username ?? '') || profileEmail !== (user?.email ?? '')

  async function handleProfileSubmit(e: React.FormEvent) {
    e.preventDefault()
    setProfileError(null)
    setProfileSuccess(false)
    setProfileSaving(true)
    try {
      await api.put('/auth/me', { username: profileUsername, email: profileEmail })
      await refreshUser()
      setProfileSuccess(true)
      setTimeout(() => setProfileSuccess(false), 3000)
    } catch (err) {
      setProfileError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setProfileSaving(false)
    }
  }

  // ── Password ──────────────────────────────────────────────────────────────
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPasswords, setShowPasswords] = useState(false)
  const [pwSaving, setPwSaving] = useState(false)
  const [pwError, setPwError] = useState<string | null>(null)
  const [pwSuccess, setPwSuccess] = useState(false)

  async function handlePasswordSubmit(e: React.FormEvent) {
    e.preventDefault()
    setPwError(null)
    setPwSuccess(false)
    if (newPassword !== confirmPassword) { setPwError('Passwords do not match'); return }
    if (newPassword.length < 8) { setPwError('Password must be at least 8 characters'); return }
    setPwSaving(true)
    try {
      await api.put('/auth/me/password', { current_password: currentPassword, new_password: newPassword })
      setPwSuccess(true)
      setTimeout(() => setPwSuccess(false), 4000)
      setCurrentPassword(''); setNewPassword(''); setConfirmPassword('')
    } catch (err: unknown) {
      setPwError(err instanceof Error ? err.message : 'Failed to change password')
    } finally {
      setPwSaving(false)
    }
  }

  // ── Quick Connect ─────────────────────────────────────────────────────────
  const [qcCode, setQcCode] = useState('')
  const [qcAuthorizing, setQcAuthorizing] = useState(false)
  const [qcError, setQcError] = useState<string | null>(null)
  const [qcSuccess, setQcSuccess] = useState(false)

  async function handleQcAuthorize(e: React.FormEvent) {
    e.preventDefault()
    setQcError(null)
    setQcSuccess(false)
    const code = qcCode.trim().toUpperCase()
    if (code.length !== 6) { setQcError('Code must be 6 characters'); return }
    setQcAuthorizing(true)
    try {
      await api.post('/auth/quick-connect/authorize', { code })
      setQcSuccess(true)
      setQcCode('')
      setTimeout(() => setQcSuccess(false), 5000)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to authorize'
      if (msg.includes('not found') || msg.includes('Not Found')) setQcError('Code not found. Check the code and try again.')
      else if (msg.includes('expired')) setQcError('Code has expired. Ask the other device to generate a new one.')
      else if (msg.includes('already authorized')) setQcError('Code was already authorized.')
      else setQcError(msg)
    } finally {
      setQcAuthorizing(false)
    }
  }

  // ── Theme ─────────────────────────────────────────────────────────────────
  const [activeTheme, setActiveTheme] = useState<ThemeId>(getStoredTheme)

  function handleThemeSelect(id: ThemeId) {
    applyTheme(id)
    setActiveTheme(id)
  }

  // ── KOSync ────────────────────────────────────────────────────────────────
  interface KOSyncStatus {
    linked: boolean
    synced_documents?: number
    last_sync?: number | null
    last_device?: string | null
  }
  const [kosyncStatus, setKosyncStatus] = useState<KOSyncStatus | null>(null)
  const [kosyncPassword, setKosyncPassword] = useState('')
  const [kosyncSaving, setKosyncSaving] = useState(false)
  const [kosyncError, setKosyncError] = useState<string | null>(null)
  const [kosyncSuccess, setKosyncSuccess] = useState(false)

  useEffect(() => {
    api.get<KOSyncStatus>('/auth/me/kosync').then(setKosyncStatus).catch(() => {})
  }, [])

  async function handleKosyncRegister(e: React.FormEvent) {
    e.preventDefault()
    setKosyncError(null)
    setKosyncSuccess(false)
    setKosyncSaving(true)
    try {
      await api.post('/auth/me/kosync', { password: kosyncPassword })
      setKosyncSuccess(true)
      setKosyncPassword('')
      const updated = await api.get<KOSyncStatus>('/auth/me/kosync')
      setKosyncStatus(updated)
    } catch (err) {
      setKosyncError(err instanceof Error ? err.message : 'Failed to register')
    } finally {
      setKosyncSaving(false)
    }
  }

  // ── API Keys ──────────────────────────────────────────────────────────────
  const [apiKeys, setApiKeys] = useState<ApiKey[]>([])
  const [newKeyResult, setNewKeyResult] = useState<string | null>(null)
  const [keyCreating, setKeyCreating] = useState(false)
  const [keyRevoking, setKeyRevoking] = useState<number | null>(null)
  const [pluginDownloading, setPluginDownloading] = useState(false)

  useEffect(() => {
    api.get<ApiKey[]>('/plugin/api-keys').then(setApiKeys).catch(() => {})
  }, [])

  async function handleCreateKey() {
    setKeyCreating(true)
    setNewKeyResult(null)
    try {
      const res = await api.post<{ id: number; label: string; key: string; created_at: string }>(
        '/plugin/api-keys', { label: 'KOReader Plugin' }
      )
      setNewKeyResult(res.key)
      setApiKeys(prev => [...prev, { id: res.id, label: res.label, key_preview: res.key.slice(0, 8) + '…', created_at: res.created_at, last_used_at: null }])
    } catch (err) {
      // ignore
    } finally {
      setKeyCreating(false)
    }
  }

  async function handleRevokeKey(id: number) {
    setKeyRevoking(id)
    try {
      await api.delete(`/plugin/api-keys/${id}`)
      setApiKeys(prev => prev.filter(k => k.id !== id))
      if (newKeyResult) setNewKeyResult(null)
    } finally {
      setKeyRevoking(null)
    }
  }

  async function handleDownloadPlugin() {
    setPluginDownloading(true)
    try {
      const token = localStorage.getItem('tome_token')
      const backendOrigin = window.location.port === '5173'
        ? window.location.origin.replace(':5173', ':8080')
        : window.location.origin
      const res = await fetch(`/api/plugin/koreader?server_url=${encodeURIComponent(backendOrigin)}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok) throw new Error('Download failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'tomesync.koplugin.zip'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      const keys = await api.get<ApiKey[]>('/plugin/api-keys')
      setApiKeys(keys)
    } finally {
      setPluginDownloading(false)
    }
  }

  // ── OPDS PINs ─────────────────────────────────────────────────────────────
  const [opdsPins, setOpdsPins] = useState<OpdsPin[]>([])
  const [newPinResult, setNewPinResult] = useState<string | null>(null)
  const [pinLabel, setPinLabel] = useState('KOReader')
  const [pinCreating, setPinCreating] = useState(false)
  const [pinRevoking, setPinRevoking] = useState<number | null>(null)

  useEffect(() => {
    api.get<OpdsPin[]>('/opds-pins').then(setOpdsPins).catch(() => {})
  }, [])

  async function handleCreatePin() {
    setPinCreating(true)
    setNewPinResult(null)
    try {
      const res = await api.post<{ id: number; label: string; pin: string; pin_preview: string }>(
        '/opds-pins', { label: pinLabel || 'KOReader' }
      )
      setNewPinResult(res.pin)
      setOpdsPins(prev => [...prev, {
        id: res.id,
        label: res.label,
        pin_preview: res.pin_preview,
        created_at: new Date().toISOString(),
        last_used_at: null,
      }])
    } catch {
      // ignore
    } finally {
      setPinCreating(false)
    }
  }

  async function handleRevokePin(id: number) {
    setPinRevoking(id)
    try {
      await api.delete(`/opds-pins/${id}`)
      setOpdsPins(prev => prev.filter(p => p.id !== id))
      if (newPinResult) setNewPinResult(null)
    } finally {
      setPinRevoking(null)
    }
  }

  // ── Export ────────────────────────────────────────────────────────────────
  const [exporting, setExporting] = useState<'json' | 'csv' | null>(null)

  async function handleExport(format: 'json' | 'csv') {
    setExporting(format)
    try {
      const token = localStorage.getItem('tome_token')
      const res = await fetch(`/api/books/export?format=${format}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok) throw new Error('Export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const date = new Date().toISOString().slice(0, 10)
      a.download = `tome-export-${date}.${format}`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } finally {
      setExporting(null)
    }
  }

  const origin = window.location.origin
  const opdsUrl = `${origin}/opds`
  const kosyncUrl = `${origin}/api/v1`

  return (
    <div className="min-h-screen bg-background flex flex-col">
      <header className="border-b border-border bg-card/50 backdrop-blur-sm sticky top-0 z-20">
        <div className="max-w-3xl mx-auto px-4 h-14 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <Link
              to="/"
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              Library
            </Link>
            <span className="text-border select-none">/</span>
            <span className="text-sm font-medium text-foreground">Settings</span>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 max-w-3xl mx-auto w-full px-4 py-10 space-y-10">

        {/* ── Account & Security ────────────────────────────────────────── */}
        <section>
          <SectionHeader title="Account & Security" />
          <div className="mt-4 rounded-xl border border-border bg-card divide-y divide-border overflow-hidden">

            {/* Profile */}
            <div className="p-5">
              <form onSubmit={handleProfileSubmit} className="flex flex-col sm:flex-row gap-3 items-start">
                <div className="flex-1 w-full grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-muted-foreground mb-1">Username</label>
                    <input
                      type="text"
                      value={profileUsername}
                      onChange={e => setProfileUsername(e.target.value)}
                      required
                      className="w-full text-sm bg-muted rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-muted-foreground mb-1">Email</label>
                    <input
                      type="email"
                      value={profileEmail}
                      onChange={e => setProfileEmail(e.target.value)}
                      required
                      className="w-full text-sm bg-muted rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                  </div>
                </div>
                <button
                  type="submit"
                  disabled={profileSaving || !profileChanged}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all disabled:opacity-40 sm:mt-5 shrink-0"
                >
                  {profileSaving && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                  {profileSaving ? 'Saving...' : 'Save changes'}
                </button>
              </form>
              {profileError && <p className="text-xs text-destructive mt-2">{profileError}</p>}
              {profileSuccess && <p className="text-xs text-green-500 mt-2">Profile updated</p>}
            </div>

            {/* Password */}
            <div className="p-5">
              <p className="text-sm font-medium text-foreground mb-3">Change Password</p>
              <form onSubmit={handlePasswordSubmit} className="space-y-3 max-w-sm">
                <PasswordField
                  label="Current password"
                  value={currentPassword}
                  onChange={setCurrentPassword}
                  show={showPasswords}
                  onToggleShow={() => setShowPasswords(v => !v)}
                  showToggle
                />
                <PasswordField label="New password" value={newPassword} onChange={setNewPassword} show={showPasswords} />
                <PasswordField
                  label="Confirm new password"
                  value={confirmPassword}
                  onChange={setConfirmPassword}
                  show={showPasswords}
                  error={!!(pwError && confirmPassword && newPassword !== confirmPassword)}
                />
                {pwError && <p className="text-xs text-destructive pt-0.5">{pwError}</p>}
                {pwSuccess && <p className="text-xs text-green-500 pt-0.5">Password updated successfully</p>}
                <div className="pt-1">
                  <button
                    type="submit"
                    disabled={pwSaving || !currentPassword || !newPassword || !confirmPassword}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all disabled:opacity-40"
                  >
                    {pwSaving && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                    {pwSaving ? 'Saving...' : 'Update password'}
                  </button>
                </div>
              </form>
            </div>

            {/* Quick Connect */}
            <div className="p-5">
              <div className="flex items-start gap-3 mb-3">
                <div className="p-1.5 rounded-lg bg-primary/10 mt-0.5 shrink-0">
                  <Smartphone className="w-3.5 h-3.5 text-primary" />
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">Quick Connect</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Sign in on a new device without entering your password. On the new device, tap "Quick Connect" on the login screen to get a 6-character code, then enter it here.
                  </p>
                </div>
              </div>
              <form onSubmit={handleQcAuthorize} className="flex items-end gap-2 max-w-xs">
                <div className="flex-1">
                  <label className="block text-xs font-medium text-muted-foreground mb-1">Code from new device</label>
                  <input
                    type="text"
                    value={qcCode}
                    onChange={e => setQcCode(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6))}
                    placeholder="ABC123"
                    maxLength={6}
                    spellCheck={false}
                    className="w-full h-9 rounded-md border border-border bg-background px-3 text-sm font-mono tracking-widest uppercase focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                </div>
                <button
                  type="submit"
                  disabled={qcAuthorizing || qcCode.trim().length !== 6}
                  className="flex items-center gap-1.5 h-9 px-3 rounded-md text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all disabled:opacity-40 shrink-0"
                >
                  {qcAuthorizing && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {qcAuthorizing ? 'Authorizing...' : 'Authorize'}
                </button>
              </form>
              {qcError && <p className="text-xs text-destructive mt-2">{qcError}</p>}
              {qcSuccess && (
                <div className="flex items-center gap-1.5 text-xs text-green-600 dark:text-green-400 mt-2">
                  <CheckCircle className="w-3.5 h-3.5" />
                  Device authorized — the new device is now signed in.
                </div>
              )}
            </div>

          </div>
        </section>

        {/* ── Appearance ───────────────────────────────────────────────── */}
        <section>
          <SectionHeader title="Appearance" />
          <div className="mt-4 grid grid-cols-4 sm:grid-cols-5 gap-2">
            {THEMES.map(theme => {
              const active = activeTheme === theme.id
              return (
                <button
                  key={theme.id}
                  onClick={() => handleThemeSelect(theme.id)}
                  className={cn(
                    'group relative rounded-lg overflow-hidden transition-all duration-150',
                    active
                      ? 'ring-2 ring-primary ring-offset-2 ring-offset-background shadow-md'
                      : 'ring-1 ring-border hover:ring-primary/40 hover:shadow-sm'
                  )}
                  title={theme.label}
                >
                  <div className="h-10 w-full flex items-end p-1.5 gap-1" style={{ background: theme.preview.bg }}>
                    <div className="flex-1 h-4 rounded opacity-90" style={{ background: theme.preview.card }} />
                    <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: theme.preview.primary }} />
                  </div>
                  <div className="px-1.5 py-1 flex items-center justify-between gap-1" style={{ background: theme.preview.card }}>
                    <span className="text-[10px] font-medium leading-tight truncate" style={{ color: theme.preview.text }}>
                      {theme.label}
                    </span>
                    {active && <Check className="w-2.5 h-2.5 shrink-0" style={{ color: theme.preview.primary }} />}
                  </div>
                </button>
              )
            })}
          </div>
        </section>

        {/* ── KOReader ─────────────────────────────────────────────────── */}
        <section>
          <SectionHeader title="KOReader" />
          <div className="mt-4 rounded-xl border border-border bg-card overflow-hidden divide-y divide-border">

            {/* OPDS Catalog */}
            <div className="p-6 space-y-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-0.5">OPDS Catalog</p>
                <p className="text-xs text-muted-foreground">
                  Browse and download your library from KOReader or any OPDS client.
                </p>
              </div>
              <ConnectBlock rows={[
                { label: 'URL', value: opdsUrl, copy: true },
                { label: 'Username', value: user?.username ?? '—', copy: true },
                { label: 'Password', value: 'your Tome password' },
              ]} />
              <p className="text-xs text-muted-foreground">
                In KOReader: Search &rarr; OPDS catalog &rarr; add catalog with the URL above.
              </p>

              {/* OPDS PINs — nested under OPDS */}
              <div className="space-y-2 pt-1">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                    <Key className="w-3 h-3" /> App-specific PINs
                  </p>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={pinLabel}
                      onChange={e => setPinLabel(e.target.value)}
                      placeholder="Label (e.g. KOReader)"
                      className="h-7 rounded-md border border-border bg-background px-2 text-xs focus:outline-none focus:ring-1 focus:ring-ring w-36"
                    />
                    <button
                      onClick={handleCreatePin}
                      disabled={pinCreating}
                      className="flex items-center gap-1 text-xs text-primary hover:opacity-80 transition-opacity disabled:opacity-50"
                    >
                      {pinCreating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                      Generate PIN
                    </button>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  Short app-specific passwords for OPDS — easier to type on an e-reader than your full password.
                </p>

                {newPinResult && (
                  <div className="rounded-lg bg-green-500/10 border border-green-500/20 p-3 space-y-1">
                    <p className="text-xs text-green-600 dark:text-green-400 font-medium">PIN created — copy it now, it won't be shown again.</p>
                    <div className="flex items-center gap-2">
                      <code className="text-xs font-mono text-foreground break-all flex-1">{newPinResult}</code>
                      <button
                        onClick={() => navigator.clipboard.writeText(newPinResult)}
                        className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground shrink-0"
                      >
                        <Copy className="w-3 h-3" />
                      </button>
                    </div>
                    <p className="text-xs text-muted-foreground">Use this as the OPDS password in KOReader (username stays the same).</p>
                  </div>
                )}

                {opdsPins.length > 0 ? (
                  <div className="rounded-lg border border-border overflow-hidden text-xs divide-y divide-border">
                    {opdsPins.map(p => (
                      <div key={p.id} className="flex items-center gap-3 px-3 py-2">
                        <span className="font-mono text-muted-foreground w-14 shrink-0">{p.pin_preview}</span>
                        <span className="text-foreground flex-1 truncate">{p.label}</span>
                        <span className="text-muted-foreground hidden sm:block shrink-0">
                          {p.last_used_at ? `used ${new Date(p.last_used_at).toLocaleDateString()}` : 'never used'}
                        </span>
                        <button
                          onClick={() => handleRevokePin(p.id)}
                          disabled={pinRevoking === p.id}
                          className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors"
                          title="Revoke"
                        >
                          {pinRevoking === p.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">No PINs yet. Generate one to use with OPDS clients.</p>
                )}
              </div>
            </div>

            {/* Progress Sync (KOSync) */}
            <div className="p-6 space-y-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-0.5">Progress Sync</p>
                  <p className="text-xs text-muted-foreground">
                    Sync reading position between KOReader and Tome.
                  </p>
                </div>
                {kosyncStatus?.linked && (
                  <span className="flex items-center gap-1 text-xs text-green-600 dark:text-green-400 shrink-0 mt-0.5">
                    <Check className="w-3 h-3" /> Linked
                    {kosyncStatus.synced_documents != null && (
                      <span className="text-muted-foreground ml-1">· {kosyncStatus.synced_documents} docs</span>
                    )}
                  </span>
                )}
              </div>

              {kosyncStatus?.last_sync && (
                <p className="text-xs text-muted-foreground">
                  Last sync: {new Date(kosyncStatus.last_sync * 1000).toLocaleString()}
                  {kosyncStatus.last_device && ` · ${kosyncStatus.last_device}`}
                </p>
              )}

              <form onSubmit={handleKosyncRegister} className="flex items-end gap-2 max-w-xs">
                <div className="flex-1">
                  <label className="block text-xs font-medium text-muted-foreground mb-1">
                    {kosyncStatus?.linked ? 'Update sync password' : 'Set sync password'}
                  </label>
                  <input
                    type="password"
                    value={kosyncPassword}
                    onChange={e => setKosyncPassword(e.target.value)}
                    placeholder="••••••••"
                    className="w-full h-9 rounded-md border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                </div>
                <button
                  type="submit"
                  disabled={kosyncSaving || !kosyncPassword}
                  className="flex items-center gap-1.5 h-9 px-3 rounded-md text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all disabled:opacity-40 shrink-0"
                >
                  {kosyncSaving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {kosyncSaving ? 'Saving...' : kosyncStatus?.linked ? 'Update' : 'Register'}
                </button>
              </form>
              {kosyncError && <p className="text-xs text-destructive">{kosyncError}</p>}
              {kosyncSuccess && <p className="text-xs text-green-500">KOSync registered successfully</p>}

              <ConnectBlock rows={[
                { label: 'URL', value: kosyncUrl, copy: true },
                { label: 'Username', value: user?.username ?? '—', copy: true },
                { label: 'Password', value: 'the sync password set above' },
              ]} />
              <p className="text-xs text-muted-foreground">
                In KOReader: Tools &rarr; Progress sync &rarr; Custom sync server.
              </p>
            </div>

            {/* TomeSync Plugin */}
            <div className="p-6 space-y-4">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-0.5">TomeSync Plugin</p>
                  <p className="text-xs text-muted-foreground">
                    Native KOReader plugin — tracks reading sessions and syncs by book ID. More reliable than KOSync.
                  </p>
                </div>
                <PluginVersion />
              </div>

              <button
                onClick={handleDownloadPlugin}
                disabled={pluginDownloading}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all disabled:opacity-50"
              >
                {pluginDownloading
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <Download className="w-3.5 h-3.5" />
                }
                {pluginDownloading ? 'Preparing...' : 'Download plugin ZIP'}
              </button>

              <SetupGuide />

              {/* API Keys */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                    <Key className="w-3 h-3" /> API Keys
                  </p>
                  <button
                    onClick={handleCreateKey}
                    disabled={keyCreating}
                    className="flex items-center gap-1 text-xs text-primary hover:opacity-80 transition-opacity disabled:opacity-50"
                  >
                    {keyCreating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                    New key
                  </button>
                </div>

                {newKeyResult && (
                  <div className="rounded-lg bg-green-500/10 border border-green-500/20 p-3 space-y-1">
                    <p className="text-xs text-green-600 dark:text-green-400 font-medium">Key created — copy it now, it won't be shown again.</p>
                    <div className="flex items-center gap-2">
                      <code className="text-xs font-mono text-foreground break-all flex-1">{newKeyResult}</code>
                      <button onClick={() => navigator.clipboard.writeText(newKeyResult)}
                        className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground shrink-0">
                        <Copy className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                )}

                {apiKeys.length > 0 ? (
                  <div className="rounded-lg border border-border overflow-hidden text-xs divide-y divide-border">
                    {apiKeys.map(k => (
                      <div key={k.id} className="flex items-center gap-3 px-3 py-2">
                        <span className="font-mono text-muted-foreground flex-1">{k.key_preview}</span>
                        <span className="text-muted-foreground hidden sm:block">
                          {k.last_used_at ? `used ${new Date(k.last_used_at).toLocaleDateString()}` : 'never used'}
                        </span>
                        <button
                          onClick={() => handleRevokeKey(k.id)}
                          disabled={keyRevoking === k.id}
                          className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors"
                          title="Revoke"
                        >
                          {keyRevoking === k.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">No API keys. Download the plugin to auto-create one.</p>
                )}
              </div>
            </div>

          </div>
        </section>

        {/* ── Export ───────────────────────────────────────────────────── */}
        <section>
          <SectionHeader title="Export" subtle />
          <div className="mt-3 rounded-xl border border-border/60 bg-card/50 p-5">
            <p className="text-xs text-muted-foreground mb-4">
              Download your entire library catalog — all titles, authors, series, tags and formats.
            </p>
            <div className="flex flex-wrap gap-2">
              <ExportButton format="json" label="JSON" exporting={exporting} onExport={handleExport} />
              <ExportButton format="csv" label="CSV" exporting={exporting} onExport={handleExport} />
            </div>
          </div>
        </section>

        {/* ── About ────────────────────────────────────────────────────── */}
        <section>
          <div className="flex items-center gap-2 text-xs text-muted-foreground/60">
            <Info className="w-3.5 h-3.5 shrink-0" />
            <span>Tome v0.1.0</span>
            <span>&middot;</span>
            <a
              href="https://github.com/bndct-devops/tome"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-muted-foreground transition-colors"
            >
              GitHub
            </a>
          </div>
        </section>

      </main>
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionHeader({ title, subtle = false }: { title: string; subtle?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <span className={cn(
        'text-xs font-semibold uppercase tracking-wider',
        subtle ? 'text-muted-foreground/50' : 'text-muted-foreground'
      )}>
        {title}
      </span>
      <div className={cn('flex-1 h-px', subtle ? 'bg-border/50' : 'bg-border')} />
    </div>
  )
}

function ConnectBlock({ rows }: { rows: { label: string; value: string; copy?: boolean }[] }) {
  const [copied, setCopied] = useState<string | null>(null)
  function copyValue(value: string) {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(value)
      setTimeout(() => setCopied(null), 1500)
    })
  }
  return (
    <div className="rounded-lg bg-muted/60 border border-border overflow-hidden text-xs">
      {rows.map(({ label, value, copy }, i) => (
        <div key={i} className={cn('flex items-center gap-3 px-3 py-2', i > 0 && 'border-t border-border/50')}>
          <span className="text-muted-foreground w-20 shrink-0">{label}</span>
          <span className={cn('flex-1 truncate', copy ? 'font-mono text-foreground' : 'text-muted-foreground')}>{value}</span>
          {copy && (
            <button
              onClick={() => copyValue(value)}
              className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground shrink-0"
              title="Copy"
            >
              {copied === value ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
            </button>
          )}
        </div>
      ))}
    </div>
  )
}

function PasswordField({
  label, value, onChange, show, onToggleShow, showToggle = false, error = false,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  show: boolean
  onToggleShow?: () => void
  showToggle?: boolean
  error?: boolean
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-muted-foreground mb-1">{label}</label>
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={e => onChange(e.target.value)}
          required
          className={cn(
            'w-full text-sm bg-muted rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring',
            error && 'ring-2 ring-destructive'
          )}
        />
        {showToggle && onToggleShow && (
          <button
            type="button"
            onClick={onToggleShow}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
            tabIndex={-1}
          >
            {show ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
          </button>
        )}
      </div>
    </div>
  )
}

function PluginVersion() {
  const [version, setVersion] = useState<string | null>(null)
  useEffect(() => {
    api.get<{ version: string }>('/plugin/version').then(r => setVersion(r.version)).catch(() => {})
  }, [])
  if (!version) return null
  return (
    <span className="text-[10px] font-mono text-muted-foreground bg-muted px-1.5 py-0.5 rounded shrink-0">
      v{version}
    </span>
  )
}

const SETUP_STEPS: { title: string; body: string; mono?: string }[] = [
  {
    title: 'Download the plugin',
    body: 'Click "Download plugin ZIP" above. An API key is automatically created and baked into the plugin — no manual configuration needed.',
  },
  {
    title: 'Copy to KOReader',
    body: "Unzip the file and copy via SSH (or USB). Remove the old plugin first to ensure a clean install.",
    mono: 'ssh root@<kindle-ip> "rm -rf /mnt/us/koreader/plugins/tomesync.koplugin" && scp -r tomesync.koplugin root@<kindle-ip>:/mnt/us/koreader/plugins/',
  },
  {
    title: 'Restart KOReader',
    body: 'In KOReader: Settings > Device > Restart KOReader. The plugin loads automatically.',
  },
  {
    title: 'Open a book downloaded via OPDS',
    body: 'Books downloaded through the OPDS catalog are automatically mapped to their Tome book ID. Open one and TomeSync will start tracking your session immediately.',
  },
  {
    title: "Verify it's working",
    body: 'In KOReader: main menu -> TomeSync -> "Test connection" to confirm the plugin can reach your Tome server.',
  },
  {
    title: 'Note on KOSync coexistence',
    body: 'TomeSync and KOSync can both be active. TomeSync tracks full reading sessions; KOSync only stores your last position.',
  },
]

function SetupGuide() {
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-lg border border-border overflow-hidden text-xs">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-3 py-2.5 text-left hover:bg-accent/50 transition-colors"
      >
        <span className="font-medium text-foreground">Setup instructions</span>
        <span className="text-muted-foreground text-[10px]">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <ol className="divide-y divide-border border-t border-border">
          {SETUP_STEPS.map((step, i) => (
            <li key={i} className="flex gap-3 px-3 py-3">
              <span className="w-5 h-5 rounded-full bg-primary/10 text-primary font-bold flex items-center justify-center shrink-0 text-[10px] mt-0.5">
                {i + 1}
              </span>
              <div className="space-y-1">
                <p className="font-medium text-foreground">{step.title}</p>
                <p className="text-muted-foreground leading-relaxed">{step.body}</p>
                {step.mono && (
                  <p className="font-mono text-muted-foreground/70">{step.mono}</p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

function ExportButton({ format, label, exporting, onExport }: {
  format: 'json' | 'csv'
  label: string
  exporting: 'json' | 'csv' | null
  onExport: (f: 'json' | 'csv') => void
}) {
  const busy = exporting === format
  return (
    <button
      onClick={() => onExport(format)}
      disabled={exporting !== null}
      className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-card hover:bg-muted transition-all disabled:opacity-50"
    >
      {busy
        ? <RefreshCw className="w-3 h-3 animate-spin" />
        : <Download className="w-3 h-3 text-muted-foreground" />
      }
      {busy ? 'Exporting...' : `Export ${label}`}
    </button>
  )
}
