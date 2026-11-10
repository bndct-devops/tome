// The per-book session list (BookDetailPage, the Stats Recent Sessions tile).
// Its own file, apart from the chart widgets, so the book page does not pull
// the chart library into the main bundle.
import { useEffect, useState } from 'react'
import { Plural, Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { Trash2, Loader2, ChevronDown, ArrowUpDown, Scissors, TriangleAlert } from 'lucide-react'
import { cn, formatDuration } from '@/lib/utils'
import { api } from '@/lib/api'
import { InfoHint } from '@/components/InfoHint'
import { Input, Pill } from '@/components/page'
import type { SessionEntry } from '@/components/stats/shared'
import { AttributedPagesMark } from '@/components/stats/AttributedPagesMark'

// Legend for the session log's labels and actions, rendered as an InfoHint
// beside whatever heads the list (the "Sessions (N)" header on BookDetailPage,
// the Recent Sessions tile title on Stats). Kept next to SessionLog so the
// explanation and the rows it explains stay in one file.
export function SessionLogHint() {
  return (
    <InfoHint wide>
      <span className="flex flex-col gap-1.5 text-left font-normal normal-case">
        <span><Trans>Each row is one reading session.</Trans></span>
        <span>
          <Trans><span className="font-medium text-foreground">Device name</span> (e.g. Kindle): recorded
          live by the KOReader plugin. <span className="font-medium text-foreground">Web reader</span> /{' '}
          <span className="font-medium text-foreground">Manual</span>: the web reader, or logged by
          hand.</Trans>
        </span>
        <span>
          <Trans><span className="font-medium text-foreground">Imported</span>: from KOReader&apos;s synced
          reading history. Delete only: KOReader already caps idle time, so there is nothing to trim.</Trans>
        </span>
        <span>
          <Trans><span className="font-medium text-foreground">Not counted</span>: a live device session
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

// Paginated session log: same rows as the Stats page's "Recent Sessions" card,
// without the card frame. Self-contained: fetches, paginates, deletes and trims
// via the API, so it works as a dashboard tile without pushing state upward.
// With `bookId` it becomes the per-book drill-down inside the time table and on
// BookDetailPage (#150); `onChange` lets that host refresh its own aggregates
// after a trim or delete. `day` (a reading-day string from session_timeline)
// narrows it to sittings that started that day (the Activity-bar click-through).
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
    return <p className="py-8 text-center text-[14px] text-muted-foreground"><Trans>No sessions recorded.</Trans></p>
  }
  // On a book's own page the book column would repeat one title on every row.
  const showBook = bookId == null
  /* eslint-disable lingui/no-unlocalized-strings -- Tailwind classes */
  const cols = showBook
    ? 'sm:grid-cols-[minmax(0,1.4fr)_150px_96px_minmax(0,1fr)_72px]'
    : 'sm:grid-cols-[170px_110px_minmax(0,1fr)_72px]'
  /* eslint-enable lingui/no-unlocalized-strings */
  const when = (s: SessionEntry) =>
    s.started_at ? new Date(s.started_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-'
  const dur = (s: SessionEntry) => (s.duration_seconds != null ? formatDuration(s.duration_seconds) : '-')
  const pages = (s: SessionEntry) =>
    s.pages_turned != null && s.pages_turned > 0 ? (
      <span className="inline-flex items-center gap-1 whitespace-nowrap">
        <Plural value={s.pages_turned} one="# page" other="# pages" />
        {s.pages_attributed && <AttributedPagesMark />}
      </span>
    ) : null
  const deviceTitle = (s: SessionEntry) =>
    s.kind === 'imported'
      ? t`Imported from KOReader reading history`
      : !s.counted
        ? t`Not in totals: this book's imported KOReader history already covers this reading`
        : undefined
  const deviceNote = (s: SessionEntry) =>
    s.kind === 'imported' ? <Trans>Imported</Trans> : !s.counted ? <Trans>Not counted</Trans> : null

  return (
    <div className="flex flex-col">
      <div className="flex justify-end pb-2">
        <button
          type="button"
          onClick={() => setSort(sort === 'recent' ? 'longest' : 'recent')}
          className="inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-[13px] text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          title={t`Toggle sort order`}
        >
          <ArrowUpDown className="h-3.5 w-3.5" />
          {sort === 'recent' ? t`Newest first` : t`Longest first`}
        </button>
      </div>
      <div className={cn('hidden gap-3 border-b border-border/70 pb-2 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground sm:grid', cols)}>
        {showBook && <span><Trans>Book</Trans></span>}
        <span><Trans>Date</Trans></span>
        <span><Trans>Duration</Trans></span>
        <span><Trans>Device</Trans></span>
        <span />
      </div>
      {sessions.map((s) => (
        <div key={s.id} className="border-b border-border/70">
          <div className={cn('flex items-start gap-3 py-2.5 sm:grid sm:items-center', cols)}>
            {/* Phones: one block, the date (or book) on top, the rest under it. */}
            <div className="min-w-0 flex-1 sm:contents">
              {showBook && (
                <div className="truncate text-[15px] font-medium text-foreground">
                  {s.book_id ? (
                    <a href={`/books/${s.book_id}`} className="transition-colors hover:text-primary">{s.book_title}</a>
                  ) : (
                    <span className="text-muted-foreground">{s.book_title}</span>
                  )}
                </div>
              )}
              <div className={cn(showBook ? 'text-[14px] text-muted-foreground' : 'text-[15px] text-foreground')}>
                {when(s)}
              </div>
              <div className="text-[13px] text-muted-foreground sm:hidden">
                {dur(s)}
                {pages(s) && <>{' · '}{pages(s)}</>}
                {' · '}{sourceLabel(s.device)}
                {deviceNote(s) && <> · {deviceNote(s)}</>}
              </div>
              <div className="hidden text-[14px] text-muted-foreground sm:block">
                <div className="inline-flex items-center gap-1.5">
                  {dur(s)}
                  {s.suspect && (
                    <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-warning" aria-label={t`Unusually long session`} />
                  )}
                </div>
                {pages(s) && <div className="text-[13px] text-muted-foreground/70">{pages(s)}</div>}
              </div>
              <div className="hidden min-w-0 text-[14px] text-muted-foreground sm:block" title={deviceTitle(s)}>
                <span className="break-words">{sourceLabel(s.device)}</span>
                {deviceNote(s) && <span className="block text-[13px] text-muted-foreground/70">{deviceNote(s)}</span>}
              </div>
            </div>
            <div className="-my-1 flex shrink-0 items-center justify-end gap-0.5">
              {s.suspect && (
                <TriangleAlert className="mr-1 h-3.5 w-3.5 shrink-0 text-warning sm:hidden" aria-label={t`Unusually long session`} />
              )}
              {s.kind !== 'imported' && (
                <button
                  type="button"
                  onClick={() => (trimId === s.id ? setTrimId(null) : openTrim(s))}
                  className="grid h-8 w-8 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                  title={t`Trim session`}
                  aria-label={t`Trim session`}
                >
                  <Scissors className="h-3.5 w-3.5" />
                </button>
              )}
              <button
                type="button"
                onClick={() => deleteSession(s)}
                disabled={deleting.has(s.id)}
                className="grid h-8 w-8 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                title={s.kind === 'imported' ? t`Delete this imported sitting` : t`Delete session`}
                aria-label={s.kind === 'imported' ? t`Delete this imported sitting` : t`Delete session`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
          {trimId === s.id && (
            <div className="flex flex-wrap items-center gap-2 pb-3 text-[14px]">
              <span className="text-muted-foreground"><Trans>New duration</Trans></span>
              <InfoHint text={t`Trimming only shortens this session's length and end time. Page turns, progress and every other session stay untouched, and the previous duration is kept in the audit log. The suggestion re-prices this session's page turns at your usual reading pace.`} />
              <Input
                type="number"
                min={1}
                value={trimMinutes}
                onChange={(e) => setTrimMinutes(e.target.value)}
                aria-label={t`New duration in minutes`}
                className="w-20 text-right tabular-nums"
              />
              <span className="text-muted-foreground"><Trans>min</Trans></span>
              {s.suggested_seconds != null && (
                <button
                  type="button"
                  onClick={() => setTrimMinutes(String(Math.max(1, Math.round(s.suggested_seconds! / 60))))}
                  className="text-[13px] text-primary transition-colors hover:underline underline-offset-4"
                  title={t`Your page turns at your usual reading pace`}
                >
                  {(() => { const d = formatDuration(s.suggested_seconds); return t`Suggested: ${d}` })()}
                </button>
              )}
              <div className="ml-auto flex gap-1.5">
                <Pill variant="quiet" onClick={() => setTrimId(null)}><Trans>Cancel</Trans></Pill>
                <Pill variant="primary" onClick={() => applyTrim(s)} disabled={trimBusy}>
                  <Trans>Trim</Trans>
                </Pill>
              </div>
            </div>
          )}
        </div>
      ))}
      {loaded < total && (
        <div className="flex justify-center pt-3">
          <Pill variant="quiet" onClick={() => load(loaded, false)} disabled={loading}>
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <>
                <ChevronDown className="h-4 w-4" />
                {(() => { const left = total - loaded; return <span><Trans>Show more ({left} remaining)</Trans></span> })()}
              </>
            )}
          </Pill>
        </div>
      )}
    </div>
  )
}

// Friendly label for a session's device value, the same words the book
// page's "Where you read" legend uses.
function sourceLabel(device: string | null): string {
  if (!device) return '-'
  if (device === 'web-reader' || device === 'web') return t`Web reader`
  if (device === 'manual') return t`Manual`
  // eslint-disable-next-line lingui/no-unlocalized-strings -- product name
  if (device === 'koreader') return 'KOReader'
  return device
}
