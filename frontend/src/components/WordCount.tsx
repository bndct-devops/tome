import { useCallback, useEffect, useRef, useState } from 'react'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import {
  Loader2, Check, X, FileWarning, Play, Ban, RotateCcw,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  getWordCountStatus, getWordCountPreflight, startWordCount, cancelWordCount, dismissWordCount,
  type WordCountState, type WordCountPreflight,
} from '@/lib/wordcount'
import { Pill, Stat, StatStrip } from '@/components/page'

const POLL_MS = 1000

function formatBytes(n: number): string {
  // eslint-disable-next-line lingui/no-unlocalized-strings -- unit
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

function formatDuration(secs: number | null): string {
  if (secs == null) return '-'
  const s = Math.max(0, Math.round(secs))
  if (s < 60) return t`${s}s`
  const m = Math.floor(s / 60)
  const rem = s % 60
  if (m < 60) return t`${m}m ${rem}s`
  const h = Math.floor(m / 60)
  const mm = m % 60
  return t`${h}h ${mm}m`
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
      .catch(() => { if (alive) setError(t`Failed to load word-count status`) })
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
      setError(e instanceof Error ? e.message : t`Failed to start`)
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
  // The pending total mixes two jobs: EPUB word counts and PDF/CBZ/CBR page
  // counts. Show them apart so "EPUBs 11, counted 11, to count 30" never happens.
  const pendingEpubs = preflight?.pending_epubs ?? pending
  const pendingPages = preflight?.pending_page_counts ?? 0
  const startLabel = pending === 0
    ? t`Everything is counted`
    : pendingPages === 0
      ? plural(pendingEpubs, { one: 'Count words in # EPUB', other: 'Count words in # EPUBs' })
      : pendingEpubs === 0
        ? plural(pendingPages, { one: 'Count pages in # file', other: 'Count pages in # files' })
        : plural(pending, { one: 'Count # file', other: 'Count # files' })
  const pct = state && state.total_bytes > 0
    ? Math.min(100, (state.done_bytes / state.total_bytes) * 100)
    : 0

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <p className="min-w-0 flex-1 basis-[320px] text-[14px] text-muted-foreground leading-relaxed max-w-[70ch]">
          <Trans>Parse each EPUB to store its word count. New uploads get this automatically; this
          backfills books added before the feature. PDF, CBZ and CBR files get their page
          count instead. It only reads the files and writes one number per book; nothing on
          disk is modified. Word counts power words-read and reading-speed stats.</Trans>
        </p>
        {!running && !terminal && (
          <Pill
            onClick={handleStart}
            disabled={busy || pending === 0}
            variant="primary"
            className="shrink-0"
            title={preflight && pending > 0 ? (() => { const size = formatBytes(preflight.pending_bytes); return t`${size} to read` })() : undefined}>
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            {startLabel}
          </Pill>
        )}
        {running && state && (
          <Pill onClick={handleCancel} disabled={busy} variant="destructive" className="shrink-0">
            <Ban className="w-3.5 h-3.5" /> <Trans>Stop</Trans>
          </Pill>
        )}
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg bg-destructive/10 text-[14px] text-destructive flex items-center justify-between gap-2">
          {error}
          <button onClick={() => setError(null)} aria-label={t`Dismiss`} className="shrink-0 hover:opacity-70"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {/* Idle: pre-flight + start */}
      {!running && !terminal && (
        <>
          <StatStrip cols={3}>
            <Stat size={30} label={t`EPUBs with word counts`} value={preflight ? formatCount(preflight.already_counted) : '-'}
              foot={preflight ? (() => { const n = formatCount(preflight.epub_total); return t`of ${n} EPUBs` })() : undefined} />
            <Stat size={30} label={t`EPUBs to count`} value={preflight ? formatCount(pendingEpubs) : '-'} tone={pendingEpubs > 0 ? 'primary' : 'foreground'} />
            <Stat size={30} label={t`Page counts to read`} value={preflight ? formatCount(pendingPages) : '-'} tone={pendingPages > 0 ? 'primary' : 'foreground'}
              foot={t`PDF, CBZ and CBR`} />
          </StatStrip>
        </>
      )}

      {/* Running: progress */}
      {running && state && (
        <>
          <div>
            <div className="flex items-center justify-between gap-3 mb-2">
              <span className="text-[15px] font-medium flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin text-primary" />
                {(() => { const done = formatCount(state.done_files); const total = formatCount(state.total_files); return <Trans>Counting… {done} / {total}</Trans> })()}
              </span>
              <span className="text-[14px] font-bold tabular-nums">{pct.toFixed(1)}%</span>
            </div>
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--background))' }}>
              <div className="h-full bg-primary rounded-full transition-all duration-500" style={{ width: `${pct}%` }} />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-x-3 mt-2 text-[13px] text-muted-foreground">
              <span>{formatBytes(state.done_bytes)} / {formatBytes(state.total_bytes)}</span>
              {(() => { const el = formatDuration(state.elapsed_seconds); const eta = formatDuration(state.eta_seconds); return <span><Trans>Elapsed {el} · ~{eta} left</Trans></span> })()}
            </div>
            <div className="mt-1 text-[13px] text-muted-foreground truncate">
              <span className="text-foreground/70"><Trans>Current:</Trans></span>{' '}
              <span className="font-mono">{state.current_file?.split('/').pop() ?? '…'}</span>
            </div>
          </div>
          <StatStrip cols={3}>
            <Stat size={30} label={t`Counted`} value={formatCount(state.counted)} tone="primary" />
            <Stat size={30} label={t`Words found`} value={formatCount(state.words_total)} />
            <Stat size={30} label={t`Failed`} value={formatCount(state.failed)} tone={state.failed > 0 ? 'warning' : 'foreground'} />
          </StatStrip>
        </>
      )}

      {/* Terminal: end summary */}
      {terminal && state && (
        <>
          <div className={cn('flex flex-wrap items-center gap-2',
            state.status === 'error' ? 'text-destructive' : state.status === 'cancelled' ? 'text-warning' : 'text-success')}>
            {state.status === 'done' && <Check className="w-4 h-4" />}
            {state.status === 'cancelled' && <Ban className="w-4 h-4" />}
            {state.status === 'error' && <FileWarning className="w-4 h-4" />}
            <span className="text-[15px] font-semibold">
              {state.status === 'done' && t`Word count complete`}
              {state.status === 'cancelled' && t`Word count stopped`}
              {state.status === 'error' && t`Word count failed`}
            </span>
            {(() => { const dur = formatDuration(state.elapsed_seconds); return <span className="ml-auto text-[13px] text-muted-foreground"><Trans>Took {dur}</Trans></span> })()}
          </div>
          <StatStrip cols={3}>
            <Stat size={30} label={t`Counted`} value={formatCount(state.counted)} tone="primary" />
            <Stat size={30} label={t`Words found`} value={formatCount(state.words_total)} />
            <Stat size={30} label={t`Failed`} value={formatCount(state.failed)} tone={state.failed > 0 ? 'warning' : 'foreground'} />
          </StatStrip>
          {state.error && (
            <p className="text-[13px] text-destructive">{state.error}</p>
          )}
          {state.issues.length > 0 && (
            <div>
              <p className="text-[13px] font-medium text-muted-foreground mb-1.5 flex items-center gap-1.5">
                <FileWarning className="w-3.5 h-3.5" /> {(() => { const n = state.issues.length; return <Trans>Could not parse ({n})</Trans> })()}
              </p>
              <div className="max-h-64 overflow-y-auto border-y border-border/70 divide-y divide-border/70">
                {state.issues.map((it, i) => (
                  <div key={i} className="text-[13px] flex items-center gap-2 py-1.5">
                    <span className="font-mono truncate" title={it.path}>{it.path.split('/').pop()}</span>
                    {it.reason && <span className="text-muted-foreground truncate ml-auto">{it.reason}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Pill onClick={handleDismiss} disabled={busy}
              variant="primary">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />} <Trans>Done</Trans>
            </Pill>
            {state.failed > 0 && (
              <Pill onClick={handleStart} disabled={busy}
                variant="secondary">
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
                {(() => { const n = formatCount(state.failed); return <Trans>Retry {n} failed</Trans> })()}
              </Pill>
            )}
          </div>
        </>
      )}
    </div>
  )
}
