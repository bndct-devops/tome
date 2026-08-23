import { useCallback, useEffect, useRef, useState } from 'react'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { Link } from 'react-router-dom'
import { BookMarked, ExternalLink, Loader2, RefreshCw, AlertTriangle } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import { cn } from '@/lib/utils'

interface HardcoverStatus {
  linked: boolean
  username?: string | null
  token_status?: string | null
  sync_enabled?: boolean
  linked_at?: string | null
  last_synced_at?: string | null
  unmatched_count?: number
  matched_count?: number
  error_count?: number
  sync_running?: boolean
}

/**
 * Settings card: link a personal Hardcover account and push ratings + reading
 * progress to it (Want to Read syncs both ways). Linking is the opt-in; the toggle pauses without
 * unlinking. Match auditing/fixing lives on the dedicated /hardcover page —
 * this card only manages the connection. Renders nothing when the server has
 * the feature killed (404) — the parent section hides with us via onAvailable.
 */
export function HardcoverSync({ onAvailable }: { onAvailable?: (v: boolean) => void }) {
  const { toast } = useToast()
  const [status, setStatus] = useState<HardcoverStatus | null>(null)
  const [available, setAvailable] = useState(true)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const pollRef = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    try {
      const s = await api.get<HardcoverStatus>('/hardcover/status')
      setStatus(s)
      return s
    } catch (err) {
      // The kill switch answers 404 "Hardcover sync is disabled" — hide the
      // whole card. Other failures (network blip) keep it visible.
      if (err instanceof Error && /disabled/i.test(err.message)) {
        setAvailable(false)
        onAvailable?.(false)
      }
      return null
    }
  }, [onAvailable])

  useEffect(() => { void refresh() }, [refresh])

  // Poll while a manual sync runs so counts/last-synced update live.
  useEffect(() => {
    if (!status?.sync_running) return
    pollRef.current = window.setInterval(() => { void refresh() }, 4000)
    return () => { if (pollRef.current) window.clearInterval(pollRef.current) }
  }, [status?.sync_running, refresh])

  if (!available) return null

  async function link() {
    if (!token.trim()) return
    setBusy(true)
    try {
      const r = await api.post<{ username: string }>('/hardcover/link', { token: token.trim() })
      { const username = r.username; toast.success(t`Linked as @${username} — initial sync started`) }
      setToken('')
      await refresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Could not link Hardcover account`)
    } finally {
      setBusy(false)
    }
  }

  async function unlink() {
    setBusy(true)
    try {
      await api.delete('/hardcover/link')
      await refresh()
    } catch {
      toast.error(t`Could not unlink`)
    } finally {
      setBusy(false)
    }
  }

  async function toggleSync(next: boolean) {
    const prev = status
    setStatus(s => s ? { ...s, sync_enabled: next } : s) // optimistic
    try {
      await api.put('/hardcover/settings', { sync_enabled: next })
    } catch {
      setStatus(prev)
      toast.error(t`Could not update sync setting`)
    }
  }

  async function syncNow() {
    try {
      const r = await api.post<{ started: boolean }>('/hardcover/sync-now', {})
      if (r.started) {
        toast.info(t`Sync started — pushing ratings and progress, syncing Want to Read both ways`)
        setStatus(s => s ? { ...s, sync_running: true } : s)
      } else {
        toast.info(t`A sync is already running`)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Could not start sync`)
    }
  }

  const expired = status?.linked && status.token_status !== 'ok'

  return (
    <div className="mt-4 rounded-xl border border-border bg-card overflow-hidden">
      <div className="p-5 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <div className="p-1.5 rounded-lg bg-primary/10 mt-0.5 shrink-0">
              <BookMarked className="w-3.5 h-3.5 text-primary" />
            </div>
            <div>
              {/* eslint-disable-next-line lingui/no-unlocalized-strings -- product name */}
              <p className="text-sm font-medium text-foreground">Hardcover Sync</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                <Trans>Push your ratings and reading progress to your Hardcover profile — nothing
                is ever deleted there. Your Hardcover Want to Read shelf syncs both ways.
                Needs your personal API token (separate from the server's metadata token).</Trans>
              </p>
            </div>
          </div>
          <a
            href="https://hardcover.app/account/api"
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary transition-colors"
          >
            <Trans>Get token</Trans> <ExternalLink className="w-3 h-3" />
          </a>
        </div>

        {!status ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> <Trans>Loading…</Trans>
          </div>
        ) : !status.linked || expired ? (
          <div className="space-y-3">
            {expired && (
              <div className="flex items-center gap-2 rounded-lg bg-warning/10 border border-warning/20 px-3 py-2">
                <AlertTriangle className="w-3.5 h-3.5 text-warning shrink-0" />
                <p className="text-xs text-warning font-medium">
                  <Trans>Your Hardcover token expired (they reset every January 1). Paste a fresh one to resume syncing.</Trans>
                </p>
              </div>
            )}
            <div className="flex gap-2">
              <input
                type="password"
                value={token}
                onChange={e => setToken(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') void link() }}
                placeholder={t`Paste your Hardcover API token`}
                className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary focus:outline-none"
              />
              <button
                onClick={() => void link()}
                disabled={busy || !token.trim()}
                className="px-3 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity inline-flex items-center gap-2"
              >
                {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                {expired ? t`Re-link` : t`Link account`}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm text-foreground">
                {(() => { const username = status.username; return <Trans>Linked as <span className="font-medium">@{username}</span></Trans> })()}
                {status.last_synced_at && (() => { const when = new Date(status.last_synced_at + 'Z').toLocaleString(); return (
                  <span className="ml-2 text-xs text-muted-foreground">
                    <Trans>last synced {when}</Trans>
                  </span>
                ) })()}
              </p>
              <button
                onClick={() => void unlink()}
                disabled={busy}
                className="text-xs text-muted-foreground hover:text-destructive transition-colors"
              >
                <Trans>Unlink</Trans>
              </button>
            </div>

            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm text-foreground"><Trans>Sync automatically</Trans></p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  <Trans>Ratings push shortly after you change them; progress goes out in batches.</Trans>
                </p>
              </div>
              <button
                role="switch"
                aria-checked={status.sync_enabled}
                onClick={() => void toggleSync(!status.sync_enabled)}
                className={cn(
                  'relative w-9 h-5 rounded-full transition-colors shrink-0',
                  status.sync_enabled ? 'bg-primary' : 'bg-muted-foreground/30'
                )}
              >
                <span className={cn(
                  'absolute left-0 top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                  status.sync_enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
                )} />
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <button
                onClick={() => void syncNow()}
                disabled={!!status.sync_running}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-50"
              >
                <RefreshCw className={cn('w-3.5 h-3.5', status.sync_running && 'animate-spin')} />
                {status.sync_running ? t`Syncing…` : t`Sync now`}
              </button>
              {/* Match auditing/fixing lives on the dedicated page */}
              <Link to="/hardcover" className="text-xs text-primary hover:underline">
                {(() => { const n = status.matched_count ?? 0; return t`${n} synced` })()}
                {(status.unmatched_count ?? 0) > 0 && (() => { const n = status.unmatched_count ?? 0; return t` · ${n} not matched` })()}
                {(status.error_count ?? 0) > 0 && (() => { const n = status.error_count ?? 0; return t` · ${n} errors` })()}
                {' '}<Trans>— manage →</Trans>
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
