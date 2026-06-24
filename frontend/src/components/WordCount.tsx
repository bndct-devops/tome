import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlignLeft, Loader2, Check, X, FileWarning, Play, Ban, RotateCcw,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  getWordCountStatus, getWordCountPreflight, startWordCount, cancelWordCount, dismissWordCount,
  type WordCountState, type WordCountPreflight,
} from '@/lib/wordcount'

const POLL_MS = 1000

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

function formatCount(n: number): string {
  return n.toLocaleString()
}

const TERMINAL = new Set(['done', 'cancelled', 'error'])

export function WordCountTab() {
  const [preflight, setPreflight] = useState<WordCountPreflight | null>(null)
  const [state, setState] = useState<WordCountState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  const poll = useCallback(async () => {
    try {
      const s = await getWordCountStatus()
      setState(s)
      if (TERMINAL.has(s.status) || s.status === 'idle') {
        stopPolling()
        getWordCountPreflight().then(setPreflight).catch(() => {})
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
    Promise.all([getWordCountPreflight(), getWordCountStatus()])
      .then(([pf, s]) => {
        if (!alive) return
        setPreflight(pf)
        setState(s)
        if (s.status === 'running') startPolling()
      })
      .catch(() => { if (alive) setError('Failed to load word-count status') })
    return () => { alive = false; stopPolling() }
  }, [startPolling, stopPolling])

  async function handleStart() {
    setBusy(true)
    setError(null)
    try {
      const s = await startWordCount()
      setState(s)
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
      const s = await cancelWordCount()
      setState(s)
    } catch { /* ignore */ } finally { setBusy(false) }
  }

  async function handleDismiss() {
    setBusy(true)
    try {
      const s = await dismissWordCount()
      setState(s)
      getWordCountPreflight().then(setPreflight).catch(() => {})
    } catch { /* ignore */ } finally { setBusy(false) }
  }

  const running = state?.status === 'running'
  const terminal = state ? TERMINAL.has(state.status) : false
  const pending = preflight?.pending ?? 0
  const pct = state && state.total_bytes > 0
    ? Math.min(100, (state.done_bytes / state.total_bytes) * 100)
    : 0

  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0">
          <AlignLeft className="w-4 h-4" />
        </div>
        <div>
          <h2 className="text-sm font-semibold">Word counts</h2>
          <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
            Parse each EPUB to store its word count. New uploads get this automatically — this
            backfills books added before the feature. It only reads the files and writes one
            number per book; nothing on disk is modified. Word counts power words-read and
            reading-speed stats. PDF and CBZ are skipped.
          </p>
        </div>
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive flex items-center justify-between gap-2">
          {error}
          <button onClick={() => setError(null)} className="shrink-0 hover:opacity-70"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {/* ── Idle: pre-flight + start ───────────────────────────────────────── */}
      {!running && !terminal && (
        <div className="border border-border rounded-xl bg-card overflow-hidden">
          <div className="px-4 py-3 border-b border-border grid grid-cols-3 gap-2 text-center">
            <Stat label="EPUBs" value={preflight ? formatCount(preflight.epub_total) : '—'} />
            <Stat label="Already counted" value={preflight ? formatCount(preflight.already_counted) : '—'} />
            <Stat label="To count" value={preflight ? formatCount(pending) : '—'} accent={pending > 0}
              sub={preflight ? formatBytes(preflight.pending_bytes) : undefined} />
          </div>
          <div className="px-4 py-3">
            <button
              onClick={handleStart}
              disabled={busy || pending === 0}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              {pending === 0 ? 'Every EPUB is counted' : `Count ${formatCount(pending)} EPUB${pending !== 1 ? 's' : ''}`}
            </button>
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
                Counting… {formatCount(state.done_files)} / {formatCount(state.total_files)}
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
              <Stat label="Counted" value={formatCount(state.counted)} accent />
              <Stat label="Words found" value={formatCount(state.words_total)} />
              <Stat label="Failed" value={formatCount(state.failed)} danger={state.failed > 0} />
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
            {state.status === 'error' && <FileWarning className="w-4 h-4" />}
            <span className="text-sm font-semibold">
              {state.status === 'done' && 'Word count complete'}
              {state.status === 'cancelled' && 'Word count stopped'}
              {state.status === 'error' && 'Word count failed'}
            </span>
            <span className="ml-auto text-xs text-muted-foreground">Took {formatDuration(state.elapsed_seconds)}</span>
          </div>
          <div className="px-4 py-3 grid grid-cols-3 gap-2 text-center border-b border-border">
            <Stat label="Counted" value={formatCount(state.counted)} accent />
            <Stat label="Words found" value={formatCount(state.words_total)} />
            <Stat label="Failed" value={formatCount(state.failed)} danger={state.failed > 0} />
          </div>
          {state.error && (
            <div className="px-4 py-2 text-xs text-destructive border-b border-border">{state.error}</div>
          )}
          {state.issues.length > 0 && (
            <div className="px-4 py-3 border-b border-border">
              <p className="text-xs font-medium text-muted-foreground mb-1.5 flex items-center gap-1.5">
                <FileWarning className="w-3.5 h-3.5" /> Could not parse ({state.issues.length})
              </p>
              <div className="max-h-64 overflow-y-auto flex flex-col gap-1">
                {state.issues.map((it, i) => (
                  <div key={i} className="text-[11px] flex items-center gap-2 py-1 border-b border-border/40 last:border-0">
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
            {state.failed > 0 && (
              <button onClick={handleStart} disabled={busy}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border border-border hover:bg-accent transition-colors disabled:opacity-50">
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
                Retry {formatCount(state.failed)} failed
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
