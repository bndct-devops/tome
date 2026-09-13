// "Connect a phone": issue a pre-authorized Quick Connect code and show it as a
// QR for the Tome app. The code is single-use, lives five minutes, and is
// cancelled the moment this modal closes — a screenshot of the QR is worth
// nothing once the window is gone. On a phone browser the same payload is
// offered as an "Open in the app" link instead of a scan.
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import QRCode from 'qrcode'
import { CheckCircle, Loader2, RefreshCw, Smartphone, X } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ModalShell } from '@/components/ModalShell'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

interface Issued { code: string; poll_token: string; expires_at: string }

/** Deep link the app understands. Server = the origin this page is served
    from, which is the address the phone can reach too. */
function payloadFor(issued: Issued): string {
  const params = new URLSearchParams({
    server: window.location.origin,
    code: issued.code,
    token: issued.poll_token,
  })
  return `tome://connect?${params.toString()}`
}

/** Backend timestamps are naive UTC; make sure the browser reads them as such. */
function expiryMs(iso: string): number {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z').getTime()
}

export function ConnectPhoneModal({ onClose }: { onClose: () => void }) {
  const [issued, setIssued] = useState<Issued | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [secondsLeft, setSecondsLeft] = useState(0)
  const [connected, setConnected] = useState(false)
  const live = useRef<Issued | null>(null)

  const cancel = useCallback(async (code: string) => {
    try { await api.delete(`/auth/quick-connect/${code}`) } catch { /* already consumed or expired */ }
  }, [])

  // Issue a fresh code, replacing (and cancelling) any live one. State is only
  // touched after the awaits, so this is safe to call from the mount effect.
  const issue = useCallback(async () => {
    const previous = live.current
    live.current = null
    if (previous) void cancel(previous.code)
    try {
      const data = await api.post<Issued>('/auth/quick-connect/issue')
      const png = await QRCode.toDataURL(payloadFor(data), {
        margin: 1,
        width: 512,
        errorCorrectionLevel: 'M',
        color: { dark: '#000000', light: '#ffffff' },
      })
      live.current = data
      setIssued(data)
      setQr(png)
      setError(null)
      setConnected(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Could not create a code`)
    }
  }, [cancel])

  /** "New code" / "Try again": show the spinner, then reissue. */
  const reissue = () => {
    setQr(null)
    setError(null)
    setConnected(false)
    void issue()
  }

  // Kick off the first code. Deferred to a microtask so the effect body itself
  // stays free of state updates (react-hooks/set-state-in-effect).
  useEffect(() => { void Promise.resolve().then(issue) }, [issue])

  // Closing the modal (unmount) invalidates whatever code is still live.
  useEffect(() => () => {
    const current = live.current
    live.current = null
    if (current) void cancel(current.code)
  }, [cancel])

  // Countdown, and watch for the phone taking the code: the status endpoint
  // answers 404 once it is consumed. Before expiry that can only mean success.
  useEffect(() => {
    if (!issued || connected) return
    const expires = expiryMs(issued.expires_at)
    const tick = () => setSecondsLeft(Math.max(0, Math.round((expires - Date.now()) / 1000)))
    tick()
    const clock = setInterval(tick, 1000)
    const watch = setInterval(async () => {
      if (Date.now() >= expires - 2000) return
      try {
        await api.get(`/auth/quick-connect/${issued.code}`)
      } catch {
        live.current = null
        setConnected(true)
      }
    }, 2000)
    return () => { clearInterval(clock); clearInterval(watch) }
  }, [issued, connected])

  const expired = issued !== null && !connected && secondsLeft === 0
  const minutes = Math.floor(secondsLeft / 60)
  const seconds = String(secondsLeft % 60).padStart(2, '0')
  const onPhone = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent)

  return createPortal(
    <ModalShell open onClose={onClose} className="w-full max-w-sm">
      <div className="rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center gap-2 border-b border-border px-5 py-3.5">
          <Smartphone className="h-4 w-4 text-primary" />
          <h2 className="font-display text-base text-foreground"><Trans>Connect a phone</Trans></h2>
          <button
            onClick={onClose}
            aria-label={t`Close`}
            className="ml-auto rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-5 py-4">
          <p className="text-xs leading-relaxed text-muted-foreground">
            <Trans>Scan this with the Tome app to sign the phone in as you. The code works once, stops after five minutes, and dies the moment you close this window.</Trans>
          </p>

          {connected ? (
            <div className="flex flex-col items-center gap-2 py-8 text-sm text-success">
              <CheckCircle className="h-8 w-8" />
              <Trans>Phone connected</Trans>
            </div>
          ) : error ? (
            <div className="py-6 text-center">
              <p className="text-xs text-destructive">{error}</p>
              <button onClick={reissue} className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted">
                <RefreshCw className="h-3.5 w-3.5" /> <Trans>Try again</Trans>
              </button>
            </div>
          ) : !qr || !issued ? (
            <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> <Trans>Loading…</Trans>
            </div>
          ) : (
            <div className="mt-4 flex flex-col items-center gap-3">
              <img
                src={qr}
                alt=""
                className={cn('h-56 w-56 rounded-lg bg-white p-2 transition-all', expired && 'opacity-20 blur-[2px]')}
              />
              {expired ? (
                <p className="text-sm text-muted-foreground"><Trans>This code has expired.</Trans></p>
              ) : (
                <p className="font-mono text-sm tracking-widest text-foreground">
                  {issued.code}
                  <span className="ml-3 tracking-normal tabular-nums text-muted-foreground">{minutes}:{seconds}</span>
                </p>
              )}
              {onPhone && !expired && (
                <a
                  href={payloadFor(issued)}
                  className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90"
                >
                  <Smartphone className="h-3.5 w-3.5" /> <Trans>Open in the Tome app</Trans>
                </a>
              )}
              <button onClick={reissue} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground">
                <RefreshCw className="h-3.5 w-3.5" /> <Trans>New code</Trans>
              </button>
            </div>
          )}
        </div>
      </div>
    </ModalShell>,
    document.body,
  )
}
