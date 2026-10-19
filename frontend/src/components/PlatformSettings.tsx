import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { AlertTriangle, ExternalLink, Loader2, RefreshCw, Unlink } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import { cn } from '@/lib/utils'
import { Pill } from '@/components/page'

export interface PlatformStatus {
  enabled: boolean
  linked: boolean
  handle: string | null
  timezone: string | null
  share_titles: boolean
  linked_at: string | null
  last_push_at: string | null
  last_push_error: string | null
  revoked: boolean
  pushing?: boolean
  platform_name: string
  platform_url: string
}

const POLL_MS = 2000
const POLL_LIMIT_MS = 5 * 60 * 1000

function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

function allZones(current: string | null): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
  let zones: string[] = []
  try {
    zones = intl.supportedValuesOf?.('timeZone') ?? []
  } catch {
    zones = []
  }
  const extra = [current, browserZone(), 'UTC'].filter((z): z is string => !!z && !zones.includes(z))
  return [...extra, ...zones]
}

/** "4 minutes ago" in the active locale, from a naive UTC timestamp. */
function ago(iso: string): string {
  const then = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').getTime()
  const secs = Math.round((then - Date.now()) / 1000)
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  const abs = Math.abs(secs)
  if (abs < 60) return rtf.format(secs, 'second')
  if (abs < 3600) return rtf.format(Math.round(secs / 60), 'minute')
  if (abs < 86400) return rtf.format(Math.round(secs / 3600), 'hour')
  return rtf.format(Math.round(secs / 86400), 'day')
}

/**
 * Settings card for the Nightlamp link (on unless TOME_PLATFORM_ENABLED=false). Linking opens a
 * one-time Nightlamp page in a new tab and polls until the user finished
 * there; afterwards Tome pushes daily reading totals on its own.
 */
export function PlatformSettings({ initial }: { initial: PlatformStatus }) {
  const { toast } = useToast()
  const [status, setStatus] = useState<PlatformStatus>(initial)
  const [busy, setBusy] = useState(false)
  const [waiting, setWaiting] = useState<{ url: string; blocked: boolean } | null>(null)
  const [expired, setExpired] = useState(false)
  const [confirmUnlink, setConfirmUnlink] = useState(false)
  const pollRef = useRef<number | null>(null)
  const name = status.platform_name

  const refresh = useCallback(async () => {
    try {
      const s = await api.get<PlatformStatus>('/platform/status')
      setStatus(s)
      return s
    } catch {
      return null
    }
  }, [])

  const stopPolling = useCallback(() => {
    if (pollRef.current) window.clearInterval(pollRef.current)
    pollRef.current = null
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  // While a push runs (after linking, resync), refresh until it is done.
  useEffect(() => {
    if (!status.pushing) return
    const id = window.setInterval(() => { void refresh() }, 2000)
    return () => window.clearInterval(id)
  }, [status.pushing, refresh])

  const zones = useMemo(() => allZones(status.timezone), [status.timezone])

  async function startLink(path: '/platform/link' | '/platform/open') {
    // Open the tab inside the click so the browser allows it; the address
    // follows once Tome has it.
    const tab = window.open('', '_blank')
    setBusy(true)
    setExpired(false)
    try {
      const body = path === '/platform/link' ? { timezone: browserZone() } : {}
      const r = await api.post<{ url: string; request_id: string }>(path, body)
      if (tab) {
        tab.opener = null
        tab.location.href = r.url
      }
      setWaiting({ url: r.url, blocked: !tab })
      const started = Date.now()
      stopPolling()
      pollRef.current = window.setInterval(async () => {
        if (Date.now() - started > POLL_LIMIT_MS) {
          stopPolling()
          setWaiting(null)
          setExpired(true)
          return
        }
        try {
          const p = await api.get<{ status: string; handle?: string }>(`/platform/link/${encodeURIComponent(r.request_id)}`)
          if (p.status === 'done') {
            stopPolling()
            setWaiting(null)
            await refresh()
          } else if (p.status === 'expired') {
            stopPolling()
            setWaiting(null)
            setExpired(true)
          }
        } catch {
          // A failed poll is retried on the next tick.
        }
      }, POLL_MS)
    } catch (err) {
      tab?.close()
      toast.error(err instanceof Error ? err.message : t`Could not reach ${name}`)
    } finally {
      setBusy(false)
    }
  }

  function cancelWaiting() {
    stopPolling()
    setWaiting(null)
  }

  async function patch(body: { share_titles?: boolean; timezone?: string }) {
    const prev = status
    setStatus(s => ({ ...s, ...body }))
    try {
      const s = await api.patch<PlatformStatus>('/platform/settings', body)
      setStatus({ ...s, pushing: true })
    } catch (err) {
      setStatus(prev)
      toast.error(err instanceof Error ? err.message : t`Could not save the setting`)
    }
  }

  async function resync() {
    try {
      await api.post('/platform/resync', {})
      setStatus(s => ({ ...s, pushing: true }))
      toast.info(t`Sending all your reading history again`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Could not start the resync`)
    }
  }

  async function unlink() {
    setBusy(true)
    try {
      await api.post('/platform/unlink', {})
      setConfirmUnlink(false)
      await refresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Could not unlink`)
    } finally {
      setBusy(false)
    }
  }

  const waitingBlock = waiting && (
    <div className="flex flex-wrap items-center gap-3 text-[13px] text-muted-foreground">
      <Loader2 className="w-3.5 h-3.5 animate-spin" />
      {waiting.blocked ? (
        <span>
          <Trans>Your browser blocked the new tab.</Trans>{' '}
          <a href={waiting.url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
            <Trans>Open {name}</Trans>
          </a>
        </span>
      ) : (
        <span><Trans>Waiting for the new tab…</Trans></span>
      )}
      <button onClick={cancelWaiting} className="text-[13px] text-muted-foreground hover:text-foreground transition-colors">
        <Trans>Cancel</Trans>
      </button>
    </div>
  )

  if (!status.linked) {
    return (
      <div className="space-y-4">
        <p className="text-[13px] text-muted-foreground">
          <Trans>{name} shows your reading hours, streaks and finished books on a page you can
          share, with boards across Tome servers. Only daily totals leave this server; titles
          and covers only if you choose.</Trans>
        </p>
        {status.revoked && (
          <div className="flex items-center gap-2 rounded-lg bg-warning/10 border border-warning/20 px-3 py-2">
            <AlertTriangle className="w-3.5 h-3.5 text-warning shrink-0" />
            <p className="text-xs text-warning font-medium">
              <Trans>Unlinked on {name}. Link again to continue.</Trans>
            </p>
          </div>
        )}
        {expired && (
          <p className="text-[13px] text-warning"><Trans>The link was not used in time. Try again.</Trans></p>
        )}
        {waiting ? waitingBlock : (
          <Pill variant="primary" onClick={() => void startLink('/platform/link')} disabled={busy}>
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ExternalLink className="w-3.5 h-3.5" />}
            <Trans>Link with {name}</Trans>
          </Pill>
        )}
      </div>
    )
  }

  const handle = status.handle ?? ''
  const profileUrl = `${status.platform_url}/@${handle}`

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-foreground">
          <Trans>Linked as</Trans>{' '}
          <a href={profileUrl} target="_blank" rel="noopener noreferrer" className="font-medium text-primary hover:underline">
            @{handle}
          </a>
        </p>
        {waiting ? waitingBlock : (
          <Pill variant="secondary" onClick={() => void startLink('/platform/open')} disabled={busy}>
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ExternalLink className="w-3.5 h-3.5" />}
            <Trans>Open {name}</Trans>
          </Pill>
        )}
      </div>
      {expired && (
        <p className="text-[13px] text-warning"><Trans>The link was not used in time. Try again.</Trans></p>
      )}

      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-foreground"><Trans>Share titles and covers</Trans></p>
          <p className="text-[13px] text-muted-foreground mt-0.5">
            <Trans>Off: only daily minutes, sittings and how many books you finished leave this server.
            On: also the titles, authors, ISBNs and a small cover image of the books you finish and the
            one you are reading. {name} prefers covers from public catalogues and uses yours only when it
            finds none. Covers are visible to anyone who can see your page.</Trans>
          </p>
        </div>
        <button
          role="switch"
          aria-checked={status.share_titles}
          aria-label={t`Share titles and covers`}
          onClick={() => void patch({ share_titles: !status.share_titles })}
          className={cn(
            'relative w-9 h-5 rounded-full transition-colors shrink-0',
            status.share_titles ? 'bg-primary' : 'bg-muted-foreground/30'
          )}
        >
          <span className={cn(
            'absolute left-0 top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
            status.share_titles ? 'translate-x-[18px]' : 'translate-x-0.5'
          )} />
        </button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-foreground"><Trans>Time zone</Trans></p>
          <p className="text-[13px] text-muted-foreground mt-0.5">
            <Trans>Your days end at midnight here. Changing it sends all days again.</Trans>
          </p>
        </div>
        <select
          value={status.timezone ?? 'UTC'}
          onChange={e => void patch({ timezone: e.target.value })}
          aria-label={t`Time zone`}
          className="h-9 max-w-full rounded-lg bg-secondary px-3 text-[14px] text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        >
          {zones.map(z => <option key={z} value={z}>{z}</option>)}
        </select>
      </div>

      <div className="space-y-1">
        {status.last_push_error ? (
          <p className="flex items-center gap-2 text-[13px] text-warning">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            <span>{(() => { const error = status.last_push_error; return <Trans>Last push failed: {error}</Trans> })()}</span>
          </p>
        ) : status.pushing ? (
          <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> <Trans>Sending…</Trans>
          </p>
        ) : status.last_push_at ? (
          <p className="text-[13px] text-muted-foreground">
            {(() => { const when = ago(status.last_push_at); return <Trans>Last push: {when}</Trans> })()}
          </p>
        ) : (
          <p className="text-[13px] text-muted-foreground"><Trans>Nothing sent yet.</Trans></p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Pill variant="quiet" onClick={() => void resync()} disabled={!!status.pushing}>
          <RefreshCw className={cn('w-3.5 h-3.5', status.pushing && 'animate-spin')} />
          <Trans>Resync everything</Trans>
        </Pill>
        {confirmUnlink ? (
          <span className="inline-flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground">
            <Trans>Stop sending to {name}?</Trans>
            <Pill variant="destructive" onClick={() => void unlink()} disabled={busy}>
              <Trans>Unlink</Trans>
            </Pill>
            <Pill variant="quiet" onClick={() => setConfirmUnlink(false)}>
              <Trans>Keep</Trans>
            </Pill>
          </span>
        ) : (
          <Pill variant="quiet" onClick={() => setConfirmUnlink(true)}>
            <Unlink className="w-3.5 h-3.5" />
            <Trans>Unlink</Trans>
          </Pill>
        )}
      </div>
      <p className="text-[12px] text-muted-foreground -mt-2">
        <Trans>Resync sends all your reading history again. Unlinking keeps
        your {name} page; link again from here to continue.</Trans>
      </p>
    </div>
  )
}
