import { useState, useEffect, useRef } from 'react'
import { useNavigate, Navigate } from 'react-router-dom'
import { LogIn, Smartphone, X, Clock, KeyRound } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { ThemePill } from '@/components/ThemeToggle'
import { Pill } from '@/components/page'
import { AuthError, AuthField, AuthFrame, AuthInput, AuthPanel, AuthPasswordInput, Spinner } from '@/components/auth/AuthPanel'
import { Trans, useLingui } from '@lingui/react/macro'
import { msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'

const API_BASE = '/api'

// Human-readable messages for the ?sso_error= codes the OIDC callback bounces back.
const SSO_ERRORS: Record<string, MessageDescriptor> = {
  misconfigured: msg`SSO is misconfigured on the server (redirect URL). Contact your admin.`,
  disabled: msg`SSO is not enabled.`,
  exchange: msg`SSO sign-in could not be completed. Please try again.`,
  claims: msg`SSO sign-in failed: no identity information was returned.`,
  not_allowed: msg`Your account is not permitted to sign in to Tome.`,
  no_account: msg`No Tome account is linked to this identity, and self-signup is disabled.`,
  inactive: msg`Your Tome account is disabled.`,
  oidc_error: msg`SSO sign-in failed. Please try again.`,
  callback: msg`SSO sign-in did not complete. Please try again.`,
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw Object.assign(new Error(text || res.statusText), { status: res.status })
  }
  return res.json() as Promise<T>
}

export function LoginPage() {
  const { t } = useLingui()
  const { user, login, refreshUser } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // SSO (OIDC) state
  const [sso, setSso] = useState<{ enabled: boolean; button_label: string }>({ enabled: false, button_label: 'Sign in with SSO' })

  useEffect(() => {
    apiFetch<{ enabled: boolean; button_label: string }>('/auth/oidc/config')
      .then(setSso)
      .catch(() => {})
    // Surface an error bounced back from the OIDC callback (?sso_error=…)
    const reason = new URLSearchParams(window.location.search).get('sso_error')
    if (reason) {
      setError(SSO_ERRORS[reason] ? i18n._(SSO_ERRORS[reason]) : t`SSO sign-in failed. Please try again.`)
      // Clean the query string so a refresh doesn't re-show it
      window.history.replaceState({}, '', window.location.pathname)
    }
  }, [])

  // Quick Connect state
  const [qcMode, setQcMode] = useState(false)
  const [qcCode, setQcCode] = useState<string | null>(null)
  const [qcSecondsLeft, setQcSecondsLeft] = useState(0)
  const [qcError, setQcError] = useState<string | null>(null)
  const [qcLoading, setQcLoading] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await login(username, password)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : t`Login failed`)
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
      const data = await apiFetch<{ code: string; poll_token: string; expires_at: string }>('/auth/quick-connect/initiate', {
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
          setQcError(t`Code expired. Try again.`)
        }
      }
      updateTimer()
      timerRef.current = setInterval(updateTimer, 1000)

      // Poll every 2 seconds
      pollRef.current = setInterval(async () => {
        try {
          const res = await apiFetch<{ status: string; access_token?: string; token_type?: string }>(
            '/auth/quick-connect/poll',
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ code: data.code, poll_token: data.poll_token }),
            }
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
            setQcError(t`Code expired. Try again.`)
          }
        }
      }, 2000)
    } catch {
      setQcError(t`Failed to generate code. Try again.`)
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

  // Redirect AFTER all hooks have run — an early return before later hooks
  // (e.g. when a stale session resolves user null→set while mounted) trips
  // React's "rendered fewer hooks than expected".
  if (user) return <Navigate to="/" replace />

  const fmtTime = (secs: number) => `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`

  return (
    <AuthFrame>
      <ThemePill className="fixed top-4 right-4" />

      {qcMode ? (
        <AuthPanel
          eyebrow={<Trans>Sign in with a code</Trans>}
          title={<Trans>Quick Connect</Trans>}
          subtitle={<Trans>Authorize this code from another device</Trans>}
          aside={
            <Pill variant="quiet" icon onClick={stopQc} aria-label={t`Cancel`} title={t`Cancel`}>
              <X className="w-4 h-4" />
            </Pill>
          }
        >
          {qcError ? (
            <div className="space-y-5">
              <AuthError>{qcError}</AuthError>
              <Pill variant="secondary" size="lg" className="w-full" onClick={() => { setQcError(null); setQcMode(false) }}>
                <Trans>Try again</Trans>
              </Pill>
            </div>
          ) : qcLoading ? (
            <div className="flex items-center justify-center py-8 text-primary">
              <Spinner className="w-6 h-6" />
            </div>
          ) : qcCode ? (
            <div className="space-y-6">
              <div className="rounded-xl bg-secondary py-6 flex flex-col items-center gap-2">
                <div className="tracking-[0.35em] pl-[0.35em] text-[36px] leading-none font-bold font-mono tabular-nums text-foreground select-all">
                  {qcCode}
                </div>
                <div className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
                  <Clock className="w-3.5 h-3.5" />
                  {(() => { const timeLeft = fmtTime(qcSecondsLeft); return <span><Trans>Expires in {timeLeft}</Trans></span> })()}
                </div>
              </div>

              <div className="text-[14px] text-muted-foreground leading-relaxed">
                <p className="text-[15px] font-medium text-foreground mb-1"><Trans>How to authorize</Trans></p>
                <p><Trans>On a device where you're already signed in, go to <span className="font-medium text-foreground">Settings &rarr; Security &rarr; Quick Connect</span> and enter this code.</Trans></p>
              </div>

              <div className="flex items-center gap-2 text-[14px] text-muted-foreground">
                <Spinner className="w-3.5 h-3.5 text-primary" />
                <Trans>Waiting for authorization…</Trans>
              </div>
            </div>
          ) : null}
        </AuthPanel>
      ) : (
        <AuthPanel title={<Trans>Welcome back</Trans>} subtitle={<Trans>Sign in to your library</Trans>}>
          <form onSubmit={handleSubmit} className="space-y-4">
            <AuthField label={<Trans>Username or email</Trans>}>
              <AuthInput
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                placeholder="you@example.com"
                required
                autoComplete="username"
                invalid={!!error}
              />
            </AuthField>

            <AuthField label={<Trans>Password</Trans>}>
              <AuthPasswordInput
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="••••••••"
                required
                autoComplete="current-password"
                invalid={!!error}
                shown={showPassword}
                onToggle={() => setShowPassword(s => !s)}
              />
            </AuthField>

            {error && <AuthError>{error}</AuthError>}

            <div className="pt-2">
              <Pill type="submit" variant="primary" size="lg" disabled={loading} className="w-full">
                {loading ? <Spinner /> : <><LogIn className="w-4 h-4" /><Trans>Sign in</Trans></>}
              </Pill>
            </div>
          </form>

          <div className="mt-2 flex flex-col gap-2">
            {sso.enabled && (
              <Pill variant="secondary" size="lg" className="w-full" onClick={() => { window.location.href = `${API_BASE}/auth/oidc/login` }}>
                <KeyRound className="w-4 h-4" />
                {sso.button_label}
              </Pill>
            )}
            <Pill variant="quiet" size="lg" className="w-full" onClick={startQuickConnect}>
              <Smartphone className="w-4 h-4" />
              <Trans>Quick Connect</Trans>
            </Pill>
          </div>
        </AuthPanel>
      )}

      <p className="text-center text-[13px] text-muted-foreground mt-6">
        <Trans>Tome · Your personal library</Trans>
      </p>
    </AuthFrame>
  )
}
