import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'

/**
 * Lands the OIDC redirect. The backend appends the freshly minted Tome JWT to
 * the URL *fragment* (#token=…) — never the query string, so it can't leak into
 * server/proxy logs. We pull it out, store it via the auth context, scrub the
 * fragment, and continue to the app.
 */
export function OidcCallbackPage() {
  const { loginWithToken } = useAuth()
  const navigate = useNavigate()
  const ran = useRef(false)

  useEffect(() => {
    if (ran.current) return // guard StrictMode double-invoke
    ran.current = true

    const hash = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : ''
    const token = new URLSearchParams(hash).get('token')
    // Scrub the token from the address bar immediately.
    window.history.replaceState({}, '', window.location.pathname)

    if (!token) {
      navigate('/login?sso_error=callback', { replace: true })
      return
    }
    loginWithToken(token)
      .then(() => navigate('/', { replace: true }))
      .catch(() => navigate('/login?sso_error=callback', { replace: true }))
  }, [loginWithToken, navigate])

  return (
    <div className="min-h-screen flex items-center justify-center bg-background text-muted-foreground">
      <div className="flex items-center gap-3 text-sm">
        <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        Signing you in…
      </div>
    </div>
  )
}
