import { useState } from 'react'
import { useNavigate, Navigate } from 'react-router-dom'
import { BookOpen, UserPlus, AlertCircle, CheckCircle2, Smartphone, BarChart3 } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Pill } from '@/components/page'
import { AuthError, AuthField, AuthFrame, AuthInput, AuthPanel, AuthPasswordInput, Spinner } from '@/components/auth/AuthPanel'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Trans, useLingui } from '@lingui/react/macro'

export function SetupPage() {
  const { t } = useLingui()
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ username: '', email: '', password: '', confirm: '' })
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  if (user) return <Navigate to="/" replace />

  function set(field: string, value: string) {
    setForm(f => ({ ...f, [field]: value }))
    setError(null)
  }

  const passwordsMatch = form.password === form.confirm && form.confirm.length > 0
  const passwordStrong = form.password.length >= 8

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!passwordsMatch) { setError(t`Passwords do not match`); return }
    if (!passwordStrong) { setError(t`Password must be at least 8 characters`); return }

    setLoading(true)
    setError(null)
    try {
      await api.post('/auth/setup', {
        username: form.username,
        email: form.email,
        password: form.password,
      })
      await login(form.username, form.password)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : t`Setup failed`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthFrame>
      <ThemeToggle className="fixed top-4 right-4" />

      <AuthPanel
        eyebrow={<Trans>Step 1 of 2</Trans>}
        title={<Trans>Welcome to Tome</Trans>}
        subtitle={<Trans>Create your admin account to get started</Trans>}
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          <AuthField label={<Trans>Username</Trans>}>
            <AuthInput
              type="text"
              value={form.username}
              onChange={e => set('username', e.target.value)}
              placeholder={t`librarian`}
              required
              autoComplete="username"
            />
          </AuthField>

          <AuthField label={<Trans>Email</Trans>}>
            <AuthInput
              type="email"
              value={form.email}
              onChange={e => set('email', e.target.value)}
              placeholder="you@example.com"
              required
              autoComplete="email"
            />
          </AuthField>

          <AuthField
            label={<Trans>Password</Trans>}
            hint={form.password.length > 0 && (
              <span className={cn('mt-1.5 text-[13px] flex items-center gap-1', passwordStrong ? 'text-success' : 'text-muted-foreground')}>
                {passwordStrong ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
                {passwordStrong ? t`Strong enough` : t`Minimum 8 characters`}
              </span>
            )}
          >
            <AuthPasswordInput
              value={form.password}
              onChange={e => set('password', e.target.value)}
              placeholder="••••••••"
              required
              autoComplete="new-password"
              shown={showPassword}
              onToggle={() => setShowPassword(s => !s)}
            />
          </AuthField>

          <AuthField label={<Trans>Confirm password</Trans>}>
            <AuthInput
              type={showPassword ? 'text' : 'password'}
              value={form.confirm}
              onChange={e => set('confirm', e.target.value)}
              placeholder="••••••••"
              required
              autoComplete="new-password"
              invalid={form.confirm.length > 0 && !passwordsMatch}
              className={cn(form.confirm.length > 0 && passwordsMatch && 'ring-1 ring-success/50')}
            />
          </AuthField>

          {error && <AuthError>{error}</AuthError>}

          <div className="pt-2">
            <Pill type="submit" variant="primary" size="lg" disabled={loading} className="w-full">
              {loading ? <Spinner /> : <><UserPlus className="w-4 h-4" /><Trans>Create admin account</Trans></>}
            </Pill>
          </div>
        </form>

        <p className="mt-6 text-[13px] text-muted-foreground">
          <Trans>Next: Your library</Trans>
        </p>
      </AuthPanel>

      {/* What Tome does, setup page only */}
      <div className="mt-6 flex items-center justify-center gap-6 text-[13px] text-muted-foreground">
        <span className="flex items-center gap-1.5"><BookOpen className="w-3.5 h-3.5" /><Trans>Your Library</Trans></span>
        <span className="flex items-center gap-1.5"><Smartphone className="w-3.5 h-3.5" /><Trans>KOReader Sync</Trans></span>
        <span className="flex items-center gap-1.5"><BarChart3 className="w-3.5 h-3.5" /><Trans>Reading Stats</Trans></span>
      </div>

      <p className="text-center text-[13px] text-muted-foreground mt-4">
        <Trans>Tome · Your personal library</Trans>
      </p>
    </AuthFrame>
  )
}
