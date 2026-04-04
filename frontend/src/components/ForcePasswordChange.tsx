import { useState } from 'react'
import { KeyRound, Eye, EyeOff } from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'

export function ForcePasswordChange() {
  const { user, refreshUser } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showCurrent, setShowCurrent] = useState(false)
  const [showNext, setShowNext] = useState(false)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (next.length < 8) { setError('Password must be at least 8 characters.'); return }
    if (next !== confirm) { setError('Passwords do not match.'); return }
    setSaving(true)
    try {
      await api.put('/auth/me/password', { current_password: current, new_password: next })
      await refreshUser()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to update password.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background">
      <div className="w-full max-w-sm px-4">
        <div className="bg-card border border-border rounded-2xl shadow-2xl p-8">
          <div className="flex flex-col items-center mb-6">
            <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center mb-3">
              <KeyRound className="w-6 h-6 text-primary" />
            </div>
            <h1 className="text-lg font-semibold text-foreground">Set your password</h1>
            <p className="text-sm text-muted-foreground text-center mt-1">
              Hi <span className="font-medium text-foreground">{user?.username}</span> — your account was created by an admin.
              Please set a personal password before continuing.
            </p>
          </div>

          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <div className="relative">
              <input
                type={showCurrent ? 'text' : 'password'}
                placeholder="Temporary password"
                value={current}
                onChange={e => setCurrent(e.target.value)}
                required
                className="w-full px-3 py-2 pr-10 rounded-lg border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <button type="button" tabIndex={-1} onClick={() => setShowCurrent(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                {showCurrent ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>

            <div className="relative">
              <input
                type={showNext ? 'text' : 'password'}
                placeholder="New password"
                value={next}
                onChange={e => setNext(e.target.value)}
                required
                className="w-full px-3 py-2 pr-10 rounded-lg border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <button type="button" tabIndex={-1} onClick={() => setShowNext(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                {showNext ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>

            <input
              type="password"
              placeholder="Confirm new password"
              value={confirm}
              onChange={e => setConfirm(e.target.value)}
              required
              className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />

            {error && <p className="text-xs text-destructive">{error}</p>}

            <button
              type="submit"
              disabled={saving || !current || !next || !confirm}
              className="mt-1 w-full py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-all flex items-center justify-center gap-2"
            >
              {saving && <div className="w-4 h-4 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin" />}
              {saving ? 'Saving…' : 'Set password & continue'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
