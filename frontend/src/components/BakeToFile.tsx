import { useCallback, useEffect, useRef, useState } from 'react'
import {
  HardDriveDownload, AlertTriangle, Loader2, Check, X,
  FileWarning, Play, Ban, RotateCcw, CircleSlash,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  getBakeStatus, getBakePreflight, startBake, cancelBake, dismissBake,
  type BakeState, type BakePreflight,
} from '@/lib/bake'

const POLL_MS = 1500

function formatBytes(n: number): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

function formatDuration(secs: number | null): string {
  if (secs == null) return '—'
  const s = Math.max(0, Math.round(secs))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rem = s % 60
  if (m < 60) return `${m}m ${rem}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

const TERMINAL = new Set(['done', 'cancelled', 'error'])

export function BakeToFileTab() {
  const [preflight, setPreflight] = useState<BakePreflight | null>(null)
  const [state, setState] = useState<BakeState | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  const poll = useCallback(async () => {
    try {
      const s = await getBakeStatus()
      setState(s)
      if (TERMINAL.has(s.status) || s.status === 'idle') {
        stopPolling()
        // refresh the pending/already-current counts after a run
        getBakePreflight().then(setPreflight).catch(() => {})
      }
    } catch { /* keep last state */ }
  }, [stopPolling])

  const startPolling = useCallback(() => {
    stopPolling()
    pollRef.current = setInterval(poll, POLL_MS)
  }, [poll, stopPolling])

  // On mount: load preflight + current status; reconnect to a running job.
  useEffect(() => {
    let alive = true
    Promise.all([getBakePreflight(), getBakeStatus()])
      .then(([pf, s]) => {
        if (!alive) return
        setPreflight(pf)
        setState(s)
        if (s.status === 'running') startPolling()
      })
      .catch(() => { if (alive) setError('Failed to load bake status') })
    return () => { alive = false; stopPolling() }
  }, [startPolling, stopPolling])

  async function handleStart() {
    setBusy(true)
    setError(null)
    try {
      const s = await startBake()
      setState(s)
      setConfirming(false)
      startPolling()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start')
    } finally {
      setBusy(false)
    }
  }

  async function handleCancel() {
    setBusy(true)
    try {
      const s = await cancelBake()
      setState(s)
    } catch { /* ignore */ } finally { setBusy(false) }
  }

  async function handleDismiss() {
    setBusy(true)
    try {
      const s = await dismissBake()
      setState(s)
      getBakePreflight().then(setPreflight).catch(() => {})
    } catch { /* ignore */ } finally { setBusy(false) }
  }

  const running = state?.status === 'running'
  const terminal = state ? TERMINAL.has(state.status) : false
  const writable = preflight?.library_writable ?? state?.library_writable ?? true
  const enabled = preflight?.enabled ?? state?.enabled ?? true
  const pending = preflight?.pending ?? 0
  const pct = state && state.total_bytes > 0
    ? Math.min(100, (state.done_bytes / state.total_bytes) * 100)
    : 0

  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0">
          <HardDriveDownload className="w-4 h-4" />
        </div>
        <div>
          <h2 className="text-sm font-semibold">Bake metadata to files</h2>
          <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
            Write Tome's metadata (title, author, series, description, cover, tags) directly
            into the source files on disk. Useful when files are read outside Tome — Syncthing,
            a Calibre library on the same folder, or direct NAS browsing.
          </p>
        </div>
      </div>

      {/* Caveat box */}
      <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-xs text-foreground/90 flex gap-2.5">
        <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
        <div className="leading-relaxed">
          This rewrites the actual library files and recomputes their content hash.
          It <span className="font-semibold">cannot be undone.</span> EPUB and CBZ get full
          metadata + cover; PDF gets title/author/subject. Files already current are skipped.
        </div>
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive flex items-center justify-between gap-2">
          {error}
          <button onClick={() => setError(null)} className="shrink-0 hover:opacity-70"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {!enabled && (
        <div className="px-3 py-2 rounded-lg bg-muted border border-border text-sm text-muted-foreground flex items-center gap-2">
          <CircleSlash className="w-4 h-4 shrink-0" />
          In-file baking is disabled on this server (<code className="text-[11px] bg-background px-1 rounded">TOME_ALLOW_INFILE_BAKE=false</code>).
        </div>
      )}
      {enabled && !writable && (
        <div className="px-3 py-2 rounded-lg bg-muted border border-border text-sm text-muted-foreground flex items-center gap-2">
          <CircleSlash className="w-4 h-4 shrink-0" />
          The library directory is mounted read-only — files cannot be modified.
        </div>
      )}

      {/* ── Idle: pre-flight + start ───────────────────────────────────────── */}
      {!running && !terminal && (
        <div className="border border-border rounded-xl bg-card overflow-hidden">
          <div className="px-4 py-3 border-b border-border grid grid-cols-3 gap-2 text-center">
            <Stat label="Bakeable files" value={preflight?.bakeable_total ?? '—'} />
            <Stat label="Already current" value={preflight?.already_current ?? '—'} />
            <Stat label="To bake" value={pending} accent={pending > 0} sub={preflight ? formatBytes(preflight.pending_bytes) : undefined} />
          </div>
          <div className="px-4 py-3">
            {confirming ? (
              <div className="flex flex-col gap-3">
                <p className="text-sm">
                  Rewrite <span className="font-semibold">{pending}</span> file{pending !== 1 ? 's' : ''} on disk
                  {preflight ? <> (<span className="font-semibold">{formatBytes(preflight.pending_bytes)}</span>)</> : null}?
                  This cannot be undone.
                </p>
                <div className="flex items-center gap-2">
                  <button onClick={handleStart} disabled={busy}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-amber-600 text-white hover:bg-amber-600/90 transition-colors disabled:opacity-50">
                    {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                    Yes, bake {pending} file{pending !== 1 ? 's' : ''}
                  </button>
                  <button onClick={() => setConfirming(false)} disabled={busy}
                    className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-accent transition-colors">
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setConfirming(true)}
                disabled={!enabled || !writable || pending === 0}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
                <Play className="w-4 h-4" />
                {pending === 0 ? 'Everything is up to date' : `Write metadata to ${pending} file${pending !== 1 ? 's' : ''}`}
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── Running: progress ──────────────────────────────────────────────── */}
      {running && state && (
        <div className="border border-border rounded-xl bg-card overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin text-primary" />
                Baking… {state.done_files} / {state.total_files} files
              </span>
              <span className="text-xs text-muted-foreground tabular-nums">{pct.toFixed(1)}%</span>
            </div>
            <div className="h-2 rounded-full bg-muted overflow-hidden">
              <div className="h-full bg-primary transition-all duration-500" style={{ width: `${pct}%` }} />
            </div>
            <div className="flex items-center justify-between mt-2 text-xs text-muted-foreground">
              <span>{formatBytes(state.done_bytes)} / {formatBytes(state.total_bytes)}</span>
              <span>Elapsed {formatDuration(state.elapsed_seconds)} · ~{formatDuration(state.eta_seconds)} left</span>
            </div>
          </div>
          <div className="px-4 py-3 flex flex-col gap-3">
            <div className="text-xs text-muted-foreground truncate">
              <span className="text-foreground/70">Current:</span>{' '}
              <span className="font-mono">{state.current_file?.split('/').pop() ?? '…'}</span>
            </div>
            <div className="grid grid-cols-3 gap-2 text-center">
              <Stat label="Baked" value={state.baked} accent />
              <Stat label="Skipped" value={state.skipped} />
              <Stat label="Failed" value={state.failed} danger={state.failed > 0} />
            </div>
            <button onClick={handleCancel} disabled={busy}
              className="self-start flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-destructive/40 text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-50">
              <Ban className="w-3.5 h-3.5" /> Stop
            </button>
          </div>
        </div>
      )}

      {/* ── Terminal: end summary ──────────────────────────────────────────── */}
      {terminal && state && (
        <div className="border border-border rounded-xl bg-card overflow-hidden">
          <div className={cn('px-4 py-3 border-b border-border flex items-center gap-2',
            state.status === 'error' ? 'text-destructive' : state.status === 'cancelled' ? 'text-amber-600' : 'text-emerald-600')}>
            {state.status === 'done' && <Check className="w-4 h-4" />}
            {state.status === 'cancelled' && <Ban className="w-4 h-4" />}
            {state.status === 'error' && <AlertTriangle className="w-4 h-4" />}
            <span className="text-sm font-semibold">
              {state.status === 'done' && 'Bake complete'}
              {state.status === 'cancelled' && 'Bake stopped'}
              {state.status === 'error' && 'Bake failed'}
            </span>
            <span className="ml-auto text-xs text-muted-foreground">Took {formatDuration(state.elapsed_seconds)}</span>
          </div>
          <div className="px-4 py-3 grid grid-cols-3 gap-2 text-center border-b border-border">
            <Stat label="Baked" value={state.baked} accent />
            <Stat label="Skipped" value={state.skipped} />
            <Stat label="Failed" value={state.failed} danger={state.failed > 0} />
          </div>
          {state.error && (
            <div className="px-4 py-2 text-xs text-destructive border-b border-border">{state.error}</div>
          )}
          {state.issues.length > 0 && (
            <div className="px-4 py-3 border-b border-border">
              <p className="text-xs font-medium text-muted-foreground mb-1.5 flex items-center gap-1.5">
                <FileWarning className="w-3.5 h-3.5" /> Files not baked ({state.issues.length})
              </p>
              <div className="max-h-64 overflow-y-auto flex flex-col gap-1">
                {state.issues.map((it, i) => (
                  <div key={i} className="text-[11px] flex items-center gap-2 py-1 border-b border-border/40 last:border-0">
                    <span className={cn('px-1.5 py-0.5 rounded border shrink-0 font-medium',
                      it.status === 'failed'
                        ? 'bg-destructive/10 text-destructive border-destructive/20'
                        : 'bg-muted text-muted-foreground border-border')}>
                      {it.status}
                    </span>
                    <span className="font-mono truncate" title={it.path}>{it.path.split('/').pop()}</span>
                    {it.reason && <span className="text-muted-foreground/70 truncate ml-auto">{it.reason}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="px-4 py-3 flex items-center gap-2">
            <button onClick={handleDismiss} disabled={busy}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />} Done
            </button>
            {pending > 0 && (
              <button onClick={handleDismiss} disabled={busy}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border border-border hover:bg-accent transition-colors disabled:opacity-50">
                <RotateCcw className="w-3.5 h-3.5" /> Back ({pending} still pending)
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, sub, accent, danger }: {
  label: string; value: number | string; sub?: string; accent?: boolean; danger?: boolean
}) {
  return (
    <div>
      <p className={cn('text-lg font-semibold tabular-nums',
        danger ? 'text-destructive' : accent ? 'text-primary' : 'text-foreground')}>
        {value}
      </p>
      <p className="text-[11px] text-muted-foreground">{label}</p>
      {sub && <p className="text-[10px] text-muted-foreground/70">{sub}</p>}
    </div>
  )
}
