import { useState, useEffect, useRef } from 'react'
import { useNavigate, Navigate } from 'react-router-dom'
import { BookOpen, LogIn, Eye, EyeOff, AlertCircle, Smartphone, X, Clock } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { ThemeToggle } from '@/components/ThemeToggle'
import { cn } from '@/lib/utils'

const API_BASE = '/api'

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw Object.assign(new Error(text || res.statusText), { status: res.status })
  }
  return res.json() as Promise<T>
}

export function LoginPage() {
  const { user, login, refreshUser } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Quick Connect state
  const [qcMode, setQcMode] = useState(false)
  const [qcCode, setQcCode] = useState<string | null>(null)
  const [qcSecondsLeft, setQcSecondsLeft] = useState(0)
  const [qcError, setQcError] = useState<string | null>(null)
  const [qcLoading, setQcLoading] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  if (user) return <Navigate to="/" replace />

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await login(username, password)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  function stopQc() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    setQcMode(false)
    setQcCode(null)
    setQcSecondsLeft(0)
    setQcError(null)
    setQcLoading(false)
  }

  async function startQuickConnect() {
    setQcMode(true)
    setQcError(null)
    setQcLoading(true)
    try {
      const data = await apiFetch<{ code: string; expires_at: string }>('/auth/quick-connect/initiate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      setQcCode(data.code)
      const expiry = new Date(data.expires_at + 'Z')
      // Countdown timer
      const updateTimer = () => {
        const secs = Math.max(0, Math.floor((expiry.getTime() - Date.now()) / 1000))
        setQcSecondsLeft(secs)
        if (secs <= 0) {
          if (timerRef.current) clearInterval(timerRef.current)
          if (pollRef.current) clearInterval(pollRef.current)
          setQcError('Code expired. Try again.')
        }
      }
      updateTimer()
      timerRef.current = setInterval(updateTimer, 1000)

      // Poll every 2 seconds
      pollRef.current = setInterval(async () => {
        try {
          const res = await apiFetch<{ status: string; access_token?: string; token_type?: string }>(
            `/auth/quick-connect/poll/${data.code}`
          )
          if (res.status === 'authorized' && res.access_token) {
            if (pollRef.current) clearInterval(pollRef.current)
            if (timerRef.current) clearInterval(timerRef.current)
            localStorage.setItem('tome_token', res.access_token)
            await refreshUser()
            navigate('/')
          }
        } catch (err: unknown) {
          const e = err as { status?: number }
          if (e.status === 410) {
            if (pollRef.current) clearInterval(pollRef.current)
            if (timerRef.current) clearInterval(timerRef.current)
            setQcError('Code expired. Try again.')
          }
        }
      }, 2000)
    } catch {
      setQcError('Failed to generate code. Try again.')
      setQcLoading(false)
      setQcMode(false)
    } finally {
      setQcLoading(false)
    }
  }

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  const fmtTime = (secs: number) => `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4 safe-top">
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
        <div className="flex items-center justify-center gap-3 mb-8 cursor-default logo-jiggle">
          <div className="p-2.5 rounded-xl bg-primary/10 ring-1 ring-primary/20 logo-icon">
            <BookOpen className="w-6 h-6 text-primary" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Tome</h1>
        </div>

        {/* Card — fade in on mount */}
        <div className="bg-card border border-border rounded-2xl p-6 shadow-lg shadow-black/5 animate-fade-in-up">

          {qcMode ? (
            /* Quick Connect view */
            <div className="space-y-5">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-medium text-foreground">Quick Connect</h2>
                  <p className="text-sm text-muted-foreground mt-0.5">
                    Authorize this code from another device
                  </p>
                </div>
                <button
                  onClick={stopQc}
                  className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                  title="Cancel"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              {qcError ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-destructive text-sm p-3 rounded-lg bg-destructive/10 border border-destructive/20">
                    <AlertCircle className="w-4 h-4 shrink-0" />
                    {qcError}
                  </div>
                  <button
                    onClick={() => { setQcError(null); setQcMode(false) }}
                    className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg border border-border text-sm font-medium text-foreground hover:bg-muted transition-all"
                  >
                    Try again
                  </button>
                </div>
              ) : qcLoading ? (
                <div className="flex items-center justify-center py-8">
                  <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                </div>
              ) : qcCode ? (
                <div className="space-y-4">
                  <div className="flex flex-col items-center py-4 gap-2">
                    <div className="tracking-[0.35em] text-4xl font-bold font-mono text-foreground select-all">
                      {qcCode}
                    </div>
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground mt-1">
                      <Clock className="w-3 h-3" />
                      <span>Expires in {fmtTime(qcSecondsLeft)}</span>
                    </div>
                  </div>

                  <div className="rounded-lg bg-muted/60 border border-border px-4 py-3 text-xs text-muted-foreground space-y-1">
                    <p className="font-medium text-foreground text-sm">How to authorize</p>
                    <p>On a device where you're already signed in, go to <span className="font-medium text-foreground">Settings &rarr; Security &rarr; Quick Connect</span> and enter this code.</p>
                  </div>

                  <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
                    <div className="w-3.5 h-3.5 border-2 border-primary/50 border-t-primary rounded-full animate-spin" />
                    Waiting for authorization…
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            /* Normal login view */
            <>
              <h2 className="text-lg font-medium text-foreground mb-1">Welcome back</h2>
              <p className="text-sm text-muted-foreground mb-6">Sign in to your library</p>

              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    Username or email
                  </label>
                  <input
                    type="text"
                    value={username}
                    onChange={e => setUsername(e.target.value)}
                    placeholder="you@example.com"
                    required
                    autoComplete="username"
                    className={cn(
                      'w-full px-3 py-2.5 rounded-lg text-sm bg-background border',
                      'text-foreground placeholder:text-muted-foreground/50',
                      'focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent focus:scale-[1.01]',
                      'transition-all duration-200',
                      error ? 'border-destructive' : 'border-input'
                    )}
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    Password
                  </label>
                  <div className="relative">
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={e => setPassword(e.target.value)}
                      placeholder="••••••••"
                      required
                      autoComplete="current-password"
                      className={cn(
                        'w-full px-3 py-2.5 pr-10 rounded-lg text-sm bg-background border',
                        'text-foreground placeholder:text-muted-foreground/50',
                        'focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent focus:scale-[1.01]',
                        'transition-all duration-200',
                        error ? 'border-destructive' : 'border-input'
                      )}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(s => !s)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
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
                      <LogIn className="w-4 h-4" />
                      Sign in
                    </>
                  )}
                </button>
              </form>

              {/* Quick Connect button */}
              <div className="mt-4 pt-4 border-t border-border">
                <button
                  onClick={startQuickConnect}
                  className={cn(
                    'w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg',
                    'border border-border text-sm font-medium text-muted-foreground',
                    'hover:text-foreground hover:bg-muted hover:-translate-y-0.5 hover:shadow-sm',
                    'transition-all duration-200'
                  )}
                >
                  <Smartphone className="w-4 h-4" />
                  Quick Connect
                </button>
              </div>
            </>
          )}
        </div>

        <p className="text-center text-xs text-muted-foreground mt-6">
          Tome · Your personal library
        </p>
      </div>
    </div>
  )
}
