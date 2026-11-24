import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { CheckCircle } from 'lucide-react'
import { Trans, useLingui } from '@lingui/react/macro'
import { useAuth } from '@/contexts/AuthContext'
import { Pill } from '@/components/page'
import { AnimatePresence, m } from 'motion/react'
import { Unfold, stepSwap } from '@/components/motion'
import { AuthError, AuthField, AuthFrame, AuthInput, AuthPanel, Spinner } from '@/components/auth/AuthPanel'
import { forgetReaderCode, normaliseReaderCode, rememberReaderCode } from '@/lib/afterLogin'

type Step = 'checking' | 'confirm' | 'enter' | 'linking' | 'linked'


/**
 * Where the QR code on a CrossPoint reader leads: the signed-in user checks
 * the code against the reader's screen and confirms. The reader collects its
 * key by itself a few seconds later.
 */
export function LinkReaderPage() {
  const { t } = useLingui()
  const { user, loading, logout } = useAuth()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const code = normaliseReaderCode(params.get('code'))
  const [typed, setTyped] = useState('')
  const [step, setStep] = useState<Step>(code ? 'checking' : 'enter')
  const [error, setError] = useState<string | null>(null)
  // The last message stays in the box while it folds away.
  const [shownError, setShownError] = useState(error)
  if (error && error !== shownError) setShownError(error)
  // Bumped to ask again about the code already in the address.
  const [attempt, setAttempt] = useState(0)
  // The stored sign-in was refused (expired): treat as signed out.
  const [expired, setExpired] = useState(false)
  // The outgoing confirm panel stays clickable while it fades: one approve at a time.
  const confirmingRef = useRef(false)

  async function call(path: string, init?: RequestInit): Promise<{ ok: boolean; detail: string }> {
    const token = localStorage.getItem('tome_token')
    const res = await fetch(`/api/v1/crosspoint/device/${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    })
    if (res.ok) return { ok: true, detail: '' }
    if (res.status === 401) { setExpired(true); return { ok: false, detail: '' } }
    const body = await res.json().catch(() => ({}))
    return { ok: false, detail: typeof body.detail === 'string' ? body.detail : t`Something went wrong. Try again.` }
  }

  // Signed in and here: the code remembered across the sign-in has done its job.
  useEffect(() => { if (user && !expired) forgetReaderCode() }, [user, expired])

  // A code in the address: is a reader waiting under it?
  useEffect(() => {
    if (!user || !code) return
    let alive = true
    setStep('checking')
    setError(null)
    call(code).then(r => {
      if (!alive) return
      if (r.ok) setStep('confirm')
      else { setError(r.detail); setStep('enter') }
    }).catch(() => { if (alive) { setError(t`Something went wrong. Try again.`); setStep('enter') } })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id, code, attempt])

  async function confirm() {
    if (confirmingRef.current) return
    confirmingRef.current = true
    setStep('linking')
    setError(null)
    try {
      const r = await call('approve', { method: 'POST', body: JSON.stringify({ code }) })
      // A success stays: the guard is only lifted for another try after a failure.
      if (r.ok) { setStep('linked'); return }
      setError(r.detail)
      setStep('enter')
    } catch {
      setError(t`Something went wrong. Try again.`)
      setStep('enter')
    }
    confirmingRef.current = false
  }

  if (loading) {
    return <AuthFrame><div className="appear-late text-primary"><Spinner className="w-6 h-6" /></div></AuthFrame>
  }

  if (!user || expired) {
    if (code) rememberReaderCode(code)
    return (
      <AuthFrame>
        <AuthPanel
          eyebrow={<Trans>Connect a reader</Trans>}
          title={<Trans>Sign in first</Trans>}
          subtitle={<Trans>Sign in to Tome on this device. You come back here afterwards.</Trans>}
        >
          <Pill variant="primary" size="lg" className="w-full" onClick={() => { if (expired) logout(); navigate('/login') }}><Trans>Sign in</Trans></Pill>
        </AuthPanel>
      </AuthFrame>
    )
  }

  const name = user.username
  const panel = step === 'checking' || step === 'linking' ? 'wait' : step

  return (
    <AuthFrame>
      <AnimatePresence mode="wait" initial={false}>
      <m.div key={panel} {...stepSwap} className="w-full flex justify-center">
      {step === 'linked' ? (
        <AuthPanel
          eyebrow={<Trans>Connect a reader</Trans>}
          title={<Trans>Reader connected</Trans>}
          subtitle={<Trans>The reader finishes by itself within a few seconds. You can close this page.</Trans>}
        >
          <div className="space-y-5">
            <div className="flex items-center gap-2 text-[15px] text-success">
              <CheckCircle className="w-5 h-5" /> <Trans>Linked to {name}</Trans>
            </div>
            <Pill variant="secondary" size="lg" className="w-full" onClick={() => navigate('/settings/xteink')}><Trans>Open reader settings</Trans></Pill>
          </div>
        </AuthPanel>
      ) : step === 'checking' || step === 'linking' ? (
        <AuthPanel eyebrow={<Trans>Connect a reader</Trans>} title={<Trans>One moment</Trans>}>
          <div className="flex items-center justify-center py-8 text-primary"><Spinner className="w-6 h-6" /></div>
        </AuthPanel>
      ) : step === 'confirm' ? (
        <AuthPanel
          eyebrow={<Trans>Connect a reader</Trans>}
          title={<Trans>Is this the code on your reader?</Trans>}
          subtitle={<Trans>Connecting lets that reader send its reading sessions to your account, {name}.</Trans>}
        >
          <div className="space-y-6">
            <div className="rounded-xl bg-secondary py-6 flex flex-col items-center">
              <div className="tracking-[0.35em] pl-[0.35em] text-[36px] leading-none font-bold font-mono text-foreground select-all">
                {code}
              </div>
            </div>
            <p className="text-[13px] text-muted-foreground">
              <Trans>Only confirm if you started this on your own reader and the code matches.</Trans>
            </p>
            <div className="flex flex-col gap-2">
              <Pill variant="primary" size="lg" className="w-full" onClick={confirm}><Trans>Connect reader</Trans></Pill>
              <Pill variant="secondary" size="lg" className="w-full" onClick={() => navigate('/')}><Trans>Cancel</Trans></Pill>
            </div>
          </div>
        </AuthPanel>
      ) : (
        <AuthPanel
          eyebrow={<Trans>Connect a reader</Trans>}
          title={<Trans>Enter the code</Trans>}
          subtitle={<Trans>Open Plugins &rarr; Tome on the reader and choose Sign in. It shows a six-character code.</Trans>}
        >
          <form
            className="space-y-5"
            onSubmit={e => {
              e.preventDefault()
              const next = normaliseReaderCode(typed)
              if (next.length !== 6) return
              if (next === code) setAttempt(n => n + 1)
              else setParams({ code: next })
            }}
          >
            <Unfold open={!!error} gap={20}><AuthError>{shownError}</AuthError></Unfold>
            <AuthField label={<Trans>Code on the reader</Trans>}>
              <AuthInput
                value={typed}
                onChange={e => setTyped(e.target.value.toUpperCase())}
                maxLength={8}
                autoCapitalize="characters"
                autoComplete="off"
                spellCheck={false}
                placeholder="ABC234"
                className="font-mono tracking-[0.25em] uppercase"
                invalid={!!error}
              />
            </AuthField>
            <Pill variant="primary" size="lg" className="w-full" type="submit" disabled={normaliseReaderCode(typed).length !== 6}>
              <Trans>Continue</Trans>
            </Pill>
          </form>
        </AuthPanel>
      )}
      </m.div>
      </AnimatePresence>
    </AuthFrame>
  )
}
