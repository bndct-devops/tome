import { useState } from 'react'
import { useNavigate, Navigate } from 'react-router-dom'
import { BookOpen, UserPlus, Eye, EyeOff, AlertCircle, CheckCircle2, Smartphone, BarChart3 } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { ThemeToggle } from '@/components/ThemeToggle'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

export function SetupPage() {
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
    if (!passwordsMatch) { setError('Passwords do not match'); return }
    if (!passwordStrong) { setError('Password must be at least 8 characters'); return }

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
      setError(err instanceof Error ? err.message : 'Setup failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <style>{`
        @keyframes fade-in-up {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes jiggle {
          0%, 100% { transform: rotate(0deg); }
          20% { transform: rotate(-12deg); }
          40% { transform: rotate(10deg); }
          60% { transform: rotate(-6deg); }
          80% { transform: rotate(4deg); }
        }
        .animate-fade-in-up { animation: fade-in-up 0.4s ease-out both; }
        .logo-jiggle:hover .logo-icon { animation: jiggle 0.5s ease-in-out; }
      `}</style>

      <ThemeToggle className="fixed top-4 right-4" />

      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex items-center justify-center gap-3 mb-6 cursor-default logo-jiggle">
          <div className="p-2.5 rounded-xl bg-primary/10 ring-1 ring-primary/20 logo-icon">
            <BookOpen className="w-6 h-6 text-primary" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Tome</h1>
        </div>

        {/* Step indicator */}
        <div className="flex items-center justify-center gap-2 mb-5">
          <div className="flex items-center gap-1.5">
            <div className="w-5 h-5 rounded-full bg-primary flex items-center justify-center">
              <span className="text-[10px] font-semibold text-primary-foreground">1</span>
            </div>
            <span className="text-xs font-medium text-foreground">Create admin account</span>
          </div>
          <div className="w-8 h-px bg-border mx-1" />
          <div className="flex items-center gap-1.5 opacity-40">
            <div className="w-5 h-5 rounded-full border border-border flex items-center justify-center">
              <span className="text-[10px] text-muted-foreground">2</span>
            </div>
            <span className="text-xs text-muted-foreground">Your library</span>
          </div>
        </div>

        {/* Card — fade in on mount */}
        <div className="bg-card border border-border rounded-2xl p-6 shadow-lg shadow-black/5 animate-fade-in-up">
          <h2 className="text-lg font-medium text-foreground mb-1">Welcome to Tome</h2>
          <p className="text-sm text-muted-foreground mb-6">Create your admin account to get started</p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Username
              </label>
              <input
                type="text"
                value={form.username}
                onChange={e => set('username', e.target.value)}
                placeholder="librarian"
                required
                autoComplete="username"
                className="w-full px-3 py-2.5 rounded-lg text-sm bg-background border border-input text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent focus:scale-[1.01] transition-all duration-200"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Email
              </label>
              <input
                type="email"
                value={form.email}
                onChange={e => set('email', e.target.value)}
                placeholder="you@example.com"
                required
                autoComplete="email"
                className="w-full px-3 py-2.5 rounded-lg text-sm bg-background border border-input text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent focus:scale-[1.01] transition-all duration-200"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Password
              </label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={form.password}
                  onChange={e => set('password', e.target.value)}
                  placeholder="••••••••"
                  required
                  autoComplete="new-password"
                  className="w-full px-3 py-2.5 pr-10 rounded-lg text-sm bg-background border border-input text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent focus:scale-[1.01] transition-all duration-200"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(s => !s)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              {form.password.length > 0 && (
                <p className={cn('text-xs flex items-center gap-1', passwordStrong ? 'text-green-500' : 'text-muted-foreground')}>
                  {passwordStrong ? <CheckCircle2 className="w-3 h-3" /> : <AlertCircle className="w-3 h-3" />}
                  {passwordStrong ? 'Strong enough' : 'Minimum 8 characters'}
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Confirm password
              </label>
              <input
                type={showPassword ? 'text' : 'password'}
                value={form.confirm}
                onChange={e => set('confirm', e.target.value)}
                placeholder="••••••••"
                required
                autoComplete="new-password"
                className={cn(
                  'w-full px-3 py-2.5 rounded-lg text-sm bg-background border text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent focus:scale-[1.01] transition-all duration-200',
                  form.confirm.length > 0
                    ? passwordsMatch ? 'border-green-500/50' : 'border-destructive'
                    : 'border-input'
                )}
              />
            </div>

            {error && (
              <div className="flex items-center gap-2 text-destructive text-sm p-3 rounded-lg bg-destructive/10 border border-destructive/20">
                <AlertCircle className="w-4 h-4 shrink-0" />
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className={cn(
                'w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg',
                'bg-primary text-primary-foreground text-sm font-medium',
                'hover:opacity-90 hover:-translate-y-0.5 hover:shadow-md hover:shadow-primary/20',
                'active:translate-y-0 active:shadow-none',
                'transition-all duration-200',
                'disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none'
              )}
            >
              {loading ? (
                <div className="w-4 h-4 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin" />
              ) : (
                <>
                  <UserPlus className="w-4 h-4" />
                  Create admin account
                </>
              )}
            </button>
          </form>
        </div>

        {/* Feature highlights — setup page only */}
        <div className="mt-5 flex items-center justify-center gap-6">
          <div className="flex flex-col items-center gap-1.5">
            <BookOpen className="w-3.5 h-3.5 text-muted-foreground/60" />
            <span className="text-xs text-muted-foreground/60">Your Library</span>
          </div>
          <div className="w-px h-6 bg-border/60" />
          <div className="flex flex-col items-center gap-1.5">
            <Smartphone className="w-3.5 h-3.5 text-muted-foreground/60" />
            <span className="text-xs text-muted-foreground/60">KOReader Sync</span>
          </div>
          <div className="w-px h-6 bg-border/60" />
          <div className="flex flex-col items-center gap-1.5">
            <BarChart3 className="w-3.5 h-3.5 text-muted-foreground/60" />
            <span className="text-xs text-muted-foreground/60">Reading Stats</span>
          </div>
        </div>

        <p className="text-center text-xs text-muted-foreground mt-4">
          Tome · Your personal library
        </p>
      </div>
    </div>
  )
}
