// The per-book session list (BookDetailPage, the Stats Recent Sessions tile).
// Its own file, apart from the chart widgets, so the book page does not pull
// the chart library into the main bundle.
import { useEffect, useState } from 'react'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { Trash2, Loader2, ChevronDown, ArrowUpDown, Scissors, TriangleAlert } from 'lucide-react'
import { cn, formatDuration } from '@/lib/utils'
import { api } from '@/lib/api'
import { InfoHint } from '@/components/InfoHint'
import type { SessionEntry } from '@/components/stats/shared'

// Legend for the session log's labels and actions — rendered as an InfoHint
// beside whatever heads the list (the "Sessions (N)" header on BookDetailPage,
// the Recent Sessions tile title on Stats). Kept next to SessionLog so the
// explanation and the rows it explains stay in one file.
export function SessionLogHint() {
  return (
    <InfoHint wide>
      <span className="flex flex-col gap-1.5 text-left font-normal normal-case">
        <span><Trans>Each row is one reading session.</Trans></span>
        <span>
          <Trans><span className="font-medium text-foreground">Device name</span> (e.g. Kindle) — recorded
          live by the KOReader plugin. <span className="font-medium text-foreground">web</span> /{' '}
          <span className="font-medium text-foreground">manual</span> — the web reader, or logged by
          hand.</Trans>
        </span>
        <span>
          <Trans><span className="font-medium text-foreground">imported</span> — from KOReader&apos;s synced
          reading history. Delete only: KOReader already caps idle time, so there is nothing to trim.</Trans>
        </span>
        <span>
          <Trans><span className="font-medium text-foreground">not counted</span> — a live device session
          whose sitting is also described by imported history. Listed for completeness,
          excluded from the totals so nothing is counted twice.</Trans>
        </span>
        <span>
          <Trans>The triangle marks an implausibly long session. Scissors shorten a session, the bin
          deletes it.</Trans>
        </span>
      </span>
    </InfoHint>
  )
}

// Paginated session log — same rows as the Stats page's "Recent Sessions" card,
// without the card frame. Self-contained: fetches, paginates, deletes and trims
// via the API, so it works as a dashboard tile without pushing state upward.
// With `bookId` it becomes the per-book drill-down inside the time table and on
// BookDetailPage (#150); `onChange` lets that host refresh its own aggregates
// after a trim or delete. `day` (a reading-day string from session_timeline)
// narrows it to sittings that started that day — the Activity-bar click-through.
export function SessionLog({ bookId, day, onChange }: { bookId?: number; day?: string; onChange?: () => void } = {}) {
  const [sessions, setSessions] = useState<SessionEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loaded, setLoaded] = useState(0)
  const [loading, setLoading] = useState(false)
  const [deleting, setDeleting] = useState<Set<number | string>>(new Set())
  const [sort, setSort] = useState<'recent' | 'longest'>('recent')
  const [trimId, setTrimId] = useState<number | string | null>(null)
  const [trimMinutes, setTrimMinutes] = useState('')
  const [trimBusy, setTrimBusy] = useState(false)

  const load = (offset: number, replace: boolean) => {
    setLoading(true)
    const bookParam = bookId != null ? `&book_id=${bookId}` : ''
    // Reading-day bucketing needs the client timezone; sent always so `day`
    // filtering and the server's day labels agree.
    const dayParam = day ? `&day=${day}` : ''
    const tzParam = `&tz_offset=${new Date().getTimezoneOffset()}`
    api.get<{ total: number; sessions: SessionEntry[] }>(`/stats/sessions?offset=${offset}&limit=20&sort=${sort}${bookParam}${dayParam}${tzParam}`)
      .then((res) => {
        setSessions((prev) => (replace ? res.sessions : [...prev, ...res.sessions]))
        setTotal(res.total)
        setLoaded(offset + res.sessions.length)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    load(0, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sort, bookId, day])

  const openTrim = (s: SessionEntry) => {
    setTrimId(s.id)
    const preFill = s.suggested_seconds ?? s.duration_seconds ?? 60
    setTrimMinutes(String(Math.max(1, Math.round(preFill / 60))))
  }

  const applyTrim = (s: SessionEntry) => {
    const seconds = Math.round(Number(trimMinutes) * 60)
    if (!Number.isFinite(seconds) || seconds < 60) return
    setTrimBusy(true)
    api.patch(`/stats/sessions/${s.id}`, { duration_seconds: seconds })
      .then(() => {
        setSessions((prev) =>
          prev.map((row) =>
            row.id === s.id
              ? { ...row, duration_seconds: seconds, suspect: false, suggested_seconds: null }
              : row,
          ),
        )
        setTrimId(null)
        onChange?.()
      })
      .catch(() => {})
      .finally(() => setTrimBusy(false))
  }

  const deleteSession = (s: SessionEntry) => {
    setDeleting((prev) => new Set(prev).add(s.id))
    const req =
      s.kind === 'imported'
        ? api.delete(`/stats/imported-sessions?book_id=${s.book_id}&start=${s.range_start}&end=${s.range_end}`)
        : api.delete(`/stats/sessions/${s.id}`)
    req
      .then(() => {
        setSessions((prev) => prev.filter((row) => row.id !== s.id))
        setTotal((prev) => prev - 1)
        setLoaded((prev) => prev - 1)
        onChange?.()
      })
      .catch(() => {})
      .finally(() => setDeleting((prev) => { const n = new Set(prev); n.delete(s.id); return n }))
  }

  if (sessions.length === 0 && !loading) {
    return <p className="text-sm text-muted-foreground text-center py-8"><Trans>No sessions recorded.</Trans></p>
  }
  return (
    <div className="flex flex-col gap-0">
      <div className="flex justify-end pb-1">
        <button
          onClick={() => setSort(sort === 'recent' ? 'longest' : 'recent')}
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
          title={t`Toggle sort order`}
        >
          <ArrowUpDown className="w-3 h-3" />
          {sort === 'recent' ? t`Newest first` : t`Longest first`}
        </button>
      </div>
      <div className="hidden sm:grid grid-cols-[1fr_120px_90px_80px_60px] gap-2 px-2 pb-2 text-[11px] font-medium text-muted-foreground border-b border-border">
        <span><Trans>Book</Trans></span>
        <span><Trans>Date</Trans></span>
        <span><Trans>Duration</Trans></span>
        <span><Trans>Device</Trans></span>
        <span />
      </div>
      {sessions.map((s, idx) => (
        <div key={s.id}>
          <div
            className={cn(
              'grid grid-cols-1 sm:grid-cols-[1fr_120px_90px_80px_60px] gap-1 sm:gap-2 px-2 py-2 items-center hover:bg-accent/30 transition-colors text-xs rounded',
              idx % 2 === 0 ? 'bg-muted/20' : ''
            )}
          >
            <div className="font-medium text-foreground truncate">
              {s.book_id ? (
                <a href={`/books/${s.book_id}`} className="hover:text-primary transition-colors">{s.book_title}</a>
              ) : (
                <span className="text-muted-foreground">{s.book_title}</span>
              )}
            </div>
            <div className="text-muted-foreground">
              {s.started_at ? new Date(s.started_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '--'}
            </div>
            <div className="text-muted-foreground inline-flex items-center gap-1">
              {s.duration_seconds != null ? formatDuration(s.duration_seconds) : '--'}
              {s.suspect && (
                <TriangleAlert
                  className="w-3 h-3 text-warning shrink-0"
                  aria-label={t`Unusually long session`}
                />
              )}
            </div>
            <div
              className="text-muted-foreground truncate"
              title={
                s.kind === 'imported'
                  ? 'Imported from KOReader reading history'
                  : !s.counted
                    ? 'Not in totals — this book’s imported KOReader history already covers this reading'
                    : undefined
              }
            >
              {s.device || '--'}
              {s.kind === 'imported' ? (
                <span className="block text-[10px] text-muted-foreground/70">imported</span>
              ) : !s.counted ? (
                <span className="block text-[10px] text-muted-foreground/70"><Trans>not counted</Trans></span>
              ) : null}
            </div>
            <div className="flex justify-end gap-0.5">
              {s.kind !== 'imported' && (
                <button
                  onClick={() => (trimId === s.id ? setTrimId(null) : openTrim(s))}
                  className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                  title={t`Trim session`}
                >
                  <Scissors className="w-3.5 h-3.5" />
                </button>
              )}
              <button
                onClick={() => deleteSession(s)}
                disabled={deleting.has(s.id)}
                className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors disabled:opacity-40"
                title={s.kind === 'imported' ? t`Delete this imported sitting` : t`Delete session`}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
          {trimId === s.id && (
            <div className="flex flex-wrap items-center gap-2 rounded bg-muted/40 px-2 py-2 text-xs">
              <span className="text-muted-foreground"><Trans>New duration</Trans></span>
              <InfoHint text={t`Trimming only shortens this session's length and end time — page turns, progress and every other session stay untouched, and the previous duration is kept in the audit log. The suggestion re-prices this session's page turns at your usual reading pace.`} />
              <input
                type="number"
                min={1}
                value={trimMinutes}
                onChange={(e) => setTrimMinutes(e.target.value)}
                className="w-16 rounded border border-border bg-background px-1.5 py-0.5 text-right tabular-nums"
              />
              <span className="text-muted-foreground"><Trans>min</Trans></span>
              {s.suggested_seconds != null && (
                <button
                  onClick={() => setTrimMinutes(String(Math.max(1, Math.round(s.suggested_seconds! / 60))))}
                  className="text-primary hover:text-primary/80 transition-colors"
                  title={t`Your page turns at your usual reading pace`}
                >
                  {(() => { const dur = formatDuration(s.suggested_seconds); return t`Suggested: ${dur}` })()}
                </button>
              )}
              <div className="ml-auto flex gap-1.5">
                <button
                  onClick={() => applyTrim(s)}
                  disabled={trimBusy}
                  className="rounded bg-primary px-2 py-0.5 font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
                >
                  <Trans>Trim</Trans>
                </button>
                <button
                  onClick={() => setTrimId(null)}
                  className="rounded border border-border px-2 py-0.5 text-muted-foreground hover:text-foreground transition-colors"
                >
                  <Trans>Cancel</Trans>
                </button>
              </div>
            </div>
          )}
        </div>
      ))}
      {loaded < total && (
        <button
          onClick={() => load(loaded, false)}
          disabled={loading}
          className="flex items-center justify-center gap-1.5 py-3 text-xs text-primary hover:text-primary/80 transition-colors disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <>
              <ChevronDown className="w-3.5 h-3.5" />
              {(() => { const left = total - loaded; return <span><Trans>Show more ({left} remaining)</Trans></span> })()}
            </>
          )}
        </button>
      )}
    </div>
  )
}
