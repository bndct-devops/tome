// The telemetry question and the report behind it.
//
// Three pieces: the hook that knows the consent state, the modal that shows
// the real report with the only "yes" button underneath it, and the one-time
// card on Home. Settings reuses the hook and the modal, so a grant from
// anywhere goes through the same look-first step. "No thanks" and the X both
// store a decline; the question is never asked again. Admin-only throughout.
import { useState } from 'react'
import { Trans, useLingui } from '@lingui/react/macro'
import { BarChart3, X } from 'lucide-react'
import { useTelemetry } from '@/lib/useTelemetry'
import { ModalShell } from '@/components/ModalShell'

export function TelemetryReportModal({ open, report, busy, onClose, onGrant }: {
  open: boolean; report: Record<string, unknown> | null; busy: boolean; onClose: () => void; onGrant: () => void
}) {
  const { t } = useLingui()
  return (
    <ModalShell open={open} onClose={onClose} className="w-full max-w-lg">
      <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-2xl">
        <div className="flex items-start justify-between gap-4 px-5 pt-5">
          <div>
            <h2 className="font-display text-lg text-foreground"><Trans>This is the report</Trans></h2>
            <p className="mt-1 text-sm text-muted-foreground">
              <Trans>Generated just now from your instance, by the same code that would send it. Once a month, to the Tome project, nothing else.</Trans>
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label={t`Close`}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-muted text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
        <pre className="mx-5 mt-4 max-h-80 overflow-auto rounded-xl bg-background p-4 text-xs leading-relaxed text-foreground">
          {JSON.stringify(report, null, 2)}
        </pre>
        <p className="px-5 pt-3 text-xs text-muted-foreground">
          <Trans>If a later version changes what is in here, sending pauses until you have seen the new report. You can stop at any time in Settings.</Trans>
        </p>
        <div className="flex flex-wrap items-center justify-end gap-2 px-5 pb-5 pt-4">
          <button type="button" onClick={onClose} disabled={busy}
            className="rounded-lg px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50">
            <Trans>Cancel</Trans>
          </button>
          <button type="button" onClick={onGrant} disabled={busy}
            className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50">
            <Trans>Send this report monthly</Trans>
          </button>
        </div>
      </div>
    </ModalShell>
  )
}

export function TelemetryConsentCard() {
  const { t } = useLingui()
  const { admin, data, busy, decide } = useTelemetry()
  const [open, setOpen] = useState(false)

  // Only the unanswered state asks. Granted, declined and env-off all stay quiet;
  // Settings shows where things stand.
  if (!admin || !data || data.consent.state !== 'unset') return null

  return (
    <>
      <div className="flex items-start gap-3 rounded-xl border border-primary/30 bg-primary/5 px-4 py-3 text-sm">
        <BarChart3 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-foreground"><Trans>Help count Tome installs?</Trans></p>
          <p className="mt-0.5 text-muted-foreground">
            <Trans>Once a month, Tome can send one small anonymous report: version, platform, bucketed counts, which features were used. Never a title, a name or a hostname. Off unless you say yes.</Trans>
          </p>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => setOpen(true)}
              className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90">
              <Trans>Show the report</Trans>
            </button>
            <button type="button" onClick={() => decide('declined')} disabled={busy}
              className="rounded-lg px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50">
              <Trans>No thanks</Trans>
            </button>
          </div>
        </div>
        <button type="button" onClick={() => decide('declined')} aria-label={t`No thanks`} disabled={busy}
          className="rounded p-0.5 text-muted-foreground transition hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>
      <TelemetryReportModal open={open} report={data.report} busy={busy} onClose={() => setOpen(false)}
        onGrant={async () => { if (await decide('granted')) setOpen(false) }} />
    </>
  )
}

// The state in words, for Settings. Every state reads as a sentence, not a
// switch position, and the only way to turn it on is the modal.
export function TelemetrySettings() {
  const { admin, data, busy, decide } = useTelemetry()
  const [open, setOpen] = useState(false)
  if (!admin || !data) return null
  const c = data.consent
  const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '')

  let line: React.ReactNode
  let action: React.ReactNode = null
  if (c.state === 'env_off') {
    line = <Trans>Off, disabled by TOME_TELEMETRY.</Trans>
  } else if (c.state === 'unset') {
    line = <Trans>Off, you have not opted in.</Trans>
    action = <button type="button" onClick={() => setOpen(true)} className={btn}><Trans>Show the report</Trans></button>
  } else if (c.state === 'declined') {
    const d = day(c.decided_at)
    line = <Trans>Off, declined on {d}.</Trans>
    action = <button type="button" onClick={() => setOpen(true)} className={btn}><Trans>Show the report</Trans></button>
  } else if (c.stale) {
    line = <Trans>Paused, the report changed in this version. Review it to resume.</Trans>
    action = <button type="button" onClick={() => setOpen(true)} className={btn}><Trans>Show the new report</Trans></button>
  } else {
    const since = day(c.decided_at), next = day(c.next_due), last = day(c.last_sent)
    line = c.last_sent
      ? <Trans>On since {since}. Last sent {last}, next {next}.</Trans>
      : <Trans>On since {since}. First report on its way.</Trans>
    action = <button type="button" onClick={() => decide('declined')} disabled={busy} className={btn}><Trans>Stop sending</Trans></button>
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
      <BarChart3 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="text-foreground"><Trans>Anonymous monthly report:</Trans> <span className="text-muted-foreground">{line}</span></span>
      <span className="ml-auto flex items-center gap-2">{action}</span>
      <TelemetryReportModal open={open} report={data.report} busy={busy} onClose={() => setOpen(false)}
        onGrant={async () => { if (await decide('granted')) setOpen(false) }} />
    </div>
  )
}

// eslint-disable-next-line lingui/no-unlocalized-strings -- class names
const btn = 'inline-flex items-center rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground transition-all hover:bg-muted hover:text-foreground disabled:opacity-50'
