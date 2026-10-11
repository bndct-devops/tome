// Settings > AI "Activity": what the reading companion's digest jobs did and
// cost. Running jobs first (with Stop), then the last 30 days grouped by book;
// a book expands to its jobs, a job to its model calls with their price.
// Admins see everyone's jobs with a user column and a user filter.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Trans, Plural, useLingui } from '@lingui/react/macro'
import { ChevronDown, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  aiFeatureLabel, cancelDigestJob, getAiJobCalls, getAiJobs,
  type AiJobBook, type AiJobCall, type AiJobSummary, type AiJobsList, type DigestJob, type DigestJobStatus,
} from '@/lib/ai'

const POLL_MS = 3000
const DAYS = 30

export function AiActivity({ isAdmin, onSettled }: {
  isAdmin: boolean
  /** Called when a running job ends, so the spend meter can refresh. */
  onSettled?: () => void
}) {
  const { t, i18n } = useLingui()
  const [userId, setUserId] = useState<number | null>(null)
  const [data, setData] = useState<AiJobsList | null>(null)
  const [failed, setFailed] = useState(false)
  const [openBooks, setOpenBooks] = useState<Set<number>>(() => new Set())
  const [openJobs, setOpenJobs] = useState<Set<string>>(() => new Set())
  const [calls, setCalls] = useState<Record<string, AiJobCall[] | 'loading' | 'error'>>({})
  const [stopping, setStopping] = useState<Set<string>>(() => new Set())
  // Every user seen in an unfiltered listing, for the admin's filter.
  const [knownUsers, setKnownUsers] = useState<Map<number, string>>(() => new Map())
  const runningCount = useRef(0)
  const settledRef = useRef(onSettled)
  useEffect(() => { settledRef.current = onSettled })

  const load = useCallback(async () => {
    try {
      const d = await getAiJobs(DAYS, isAdmin ? userId : null)
      setData(d)
      setFailed(false)
      if (runningCount.current > d.running.length) settledRef.current?.()
      runningCount.current = d.running.length
      if (isAdmin && userId == null) {
        setKnownUsers(prev => {
          const next = new Map(prev)
          for (const b of d.books) for (const j of b.jobs) if (j.username) next.set(j.user_id, j.username)
          for (const r of d.running) if (r.username) next.set(r.user_id, r.username)
          return next.size === prev.size ? prev : next
        })
      }
    } catch {
      setFailed(true)
    }
  }, [isAdmin, userId])

  useEffect(() => { void load() }, [load])

  const anyRunning = (data?.running.length ?? 0) > 0
  useEffect(() => {
    if (!anyRunning) return
    const timer = window.setInterval(() => { void load() }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [anyRunning, load])

  const money = (n: number | null | undefined) => new Intl.NumberFormat(i18n.locale, {
    style: 'currency', currency: 'USD', minimumFractionDigits: 2,
    maximumFractionDigits: n != null && n > 0 && n < 1 ? 3 : 2,
  }).format(n ?? 0)
  const num = (n: number) => n.toLocaleString(i18n.locale)
  const duration = (secs: number | null | undefined): string => {
    if (secs == null) return ''
    if (secs < 90) {
      return new Intl.NumberFormat(i18n.locale, { style: 'unit', unit: 'second', unitDisplay: 'short', maximumFractionDigits: 0 }).format(secs)
    }
    const minutes = secs / 60
    if (minutes < 90) {
      return new Intl.NumberFormat(i18n.locale, { style: 'unit', unit: 'minute', unitDisplay: 'short', maximumFractionDigits: 0 }).format(minutes)
    }
    return new Intl.NumberFormat(i18n.locale, { style: 'unit', unit: 'hour', unitDisplay: 'short', maximumFractionDigits: 1 }).format(minutes / 60)
  }
  const when = (iso: string | null) => iso
    ? new Date(iso).toLocaleString(i18n.locale, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
    : ''

  const statusLabel = (s: DigestJobStatus): string => {
    switch (s) {
      case 'queued': return t`Waiting`
      case 'running': return t`Running`
      case 'stopping': return t`Stopping`
      case 'done': return t`Done`
      case 'cancelled': return t`Stopped by you`
      case 'stopped': return t`Stopped`
      case 'error': return t`Failed`
      case 'interrupted': return t`Interrupted`
    }
  }
  const scopeLabel = (s: string) => (s === 'series' ? t`Whole series` : t`This book`)

  const runningLine = (j: DigestJob) => {
    const done = j.done
    const total = j.total
    if (j.status === 'stopping') return t`Stopping after the current chapters…`
    if (j.status === 'queued' || total === 0) return t`Getting ready to read the chapters…`
    return t`Reading chapters: ${done} of ${total}…`
  }

  function toggle<T>(set: Set<T>, value: T): Set<T> {
    const next = new Set(set)
    if (next.has(value)) next.delete(value)
    else next.add(value)
    return next
  }

  function toggleJob(id: string) {
    setOpenJobs(prev => toggle(prev, id))
    if (calls[id] == null || calls[id] === 'error') {
      setCalls(prev => ({ ...prev, [id]: 'loading' }))
      getAiJobCalls(id)
        .then(r => setCalls(prev => ({ ...prev, [id]: r.calls })))
        .catch(() => setCalls(prev => ({ ...prev, [id]: 'error' })))
    }
  }

  async function stop(id: string) {
    setStopping(prev => new Set(prev).add(id))
    try {
      await cancelDigestJob(id)
    } catch { /* the next poll shows the state */ }
    await load()
    setStopping(prev => { const next = new Set(prev); next.delete(id); return next })
  }

  const users = [...knownUsers.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  const empty = data != null && data.running.length === 0 && data.books.length === 0

  return (
    <div className="p-5 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-medium text-muted-foreground">
          <Trans>Activity</Trans>
          <span className="font-normal text-muted-foreground/70"> · <Trans>last 30 days</Trans></span>
        </p>
        {isAdmin && (users.length > 0 || userId != null) && (
          <select
            value={userId ?? ''}
            onChange={e => { setUserId(e.target.value ? Number(e.target.value) : null); setData(null) }}
            aria-label={t`Show activity for`}
            className="h-7 max-w-[10rem] rounded-md border border-border bg-background px-2 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
          >
            <option value="">{t`All users`}</option>
            {users.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        )}
      </div>

      {failed && !data ? (
        <p className="text-xs text-muted-foreground"><Trans>Could not load the activity.</Trans></p>
      ) : !data ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> <Trans>Loading…</Trans>
        </div>
      ) : empty ? (
        <p className="text-xs text-muted-foreground">
          <Trans>Nothing yet. When the recap or the glossary reads chapters of a book, the job shows up here with what it cost.</Trans>
        </p>
      ) : (
        <div className="space-y-3">
          {data.running.length > 0 && (
            <div className="rounded-lg border border-border divide-y divide-border text-xs" role="status">
              {data.running.map(j => {
                const pct = j.total > 0 ? Math.min(100, Math.round((j.done / j.total) * 100)) : 0
                return (
                  <div key={j.id} className="px-3 py-2.5 space-y-1.5">
                    <div className="flex items-center gap-3">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-foreground truncate">
                          {j.book_title ?? t`Deleted book`}
                          {isAdmin && j.username && <span className="text-muted-foreground"> · {j.username}</span>}
                        </p>
                        <p className="text-muted-foreground flex items-center gap-1.5 mt-0.5">
                          <Loader2 className="w-3 h-3 animate-spin shrink-0" />
                          <span className="truncate">{runningLine(j)}</span>
                        </p>
                      </div>
                      <span className="tabular-nums text-foreground shrink-0">{money(j.cost_usd)}</span>
                      {j.status !== 'stopping' && (
                        <button
                          type="button"
                          onClick={() => void stop(j.id)}
                          disabled={stopping.has(j.id)}
                          className="shrink-0 px-2.5 py-1 rounded-md border border-border text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
                        >
                          <Trans>Stop</Trans>
                        </button>
                      )}
                    </div>
                    <div className="h-1 rounded-full bg-muted overflow-hidden" aria-hidden>
                      <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {data.books.length > 0 && (
            <div className="rounded-lg border border-border overflow-hidden text-xs divide-y divide-border">
              {data.books.map(b => (
                <BookRow
                  key={b.book_id ?? "deleted"}
                  book={b}
                  open={openBooks.has(b.book_id)}
                  onToggle={() => setOpenBooks(prev => toggle(prev, b.book_id))}
                  isAdmin={isAdmin}
                  openJobs={openJobs}
                  onToggleJob={toggleJob}
                  calls={calls}
                  money={money}
                  num={num}
                  duration={duration}
                  when={when}
                  statusLabel={statusLabel}
                  scopeLabel={scopeLabel}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function BookRow({
  book, open, onToggle, isAdmin, openJobs, onToggleJob, calls, money, num, duration, when, statusLabel, scopeLabel,
}: {
  book: AiJobBook
  open: boolean
  onToggle: () => void
  isAdmin: boolean
  openJobs: Set<string>
  onToggleJob: (id: string) => void
  calls: Record<string, AiJobCall[] | 'loading' | 'error'>
  money: (n: number | null | undefined) => string
  num: (n: number) => string
  duration: (secs: number | null | undefined) => string
  when: (iso: string | null) => string
  statusLabel: (s: DigestJobStatus) => string
  scopeLabel: (s: string) => string
}) {
  const { t } = useLingui()
  const jobCalls = book.jobs.reduce((n, j) => n + (j.calls ?? 0), 0)
  const jobCost = book.jobs.reduce((n, j) => n + (j.cost_usd ?? 0), 0)
  const directCalls = Math.max(0, book.calls - jobCalls)
  const directCost = Math.max(0, book.cost_usd - jobCost)
  const idx = book.series_index
  const title = book.title ?? t`Deleted book`
  const chapters = book.chapters_digested

  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="w-full grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 items-center px-3 py-2.5 text-left hover:bg-muted/50 transition-colors"
      >
        <span className="flex items-center gap-1.5 min-w-0">
          <ChevronDown className={cn('w-3 h-3 shrink-0 text-muted-foreground/60 transition-transform', !open && '-rotate-90')} />
          <span className="text-sm text-foreground truncate">{title}</span>
        </span>
        <span className="tabular-nums text-sm text-foreground">{money(book.cost_usd)}</span>
        <span className="pl-[18px] text-muted-foreground truncate">
          {book.series && (idx != null
            ? <>{book.series} #{idx}{' · '}</>
            : <>{book.series}{' · '}</>)}
          <Plural value={chapters} one="# chapter read" other="# chapters read" />
        </span>
        <span className="text-right text-muted-foreground tabular-nums">
          <Plural value={book.calls} one="# call" other="# calls" />
        </span>
      </button>

      {open && (
        <div className="bg-muted/30 border-t border-border/60 divide-y divide-border/60">
          {book.jobs.map(j => (
            <JobRow
              key={j.id}
              job={j}
              open={openJobs.has(j.id)}
              onToggle={() => onToggleJob(j.id)}
              isAdmin={isAdmin}
              calls={calls[j.id]}
              money={money}
              num={num}
              duration={duration}
              when={when}
              statusLabel={statusLabel}
              scopeLabel={scopeLabel}
            />
          ))}
          {directCalls > 0 && (() => { const cost = money(directCost); return (
            <p className="px-3 py-2 pl-8 text-muted-foreground flex justify-between gap-3">
              <span><Plural value={directCalls} one="# other call (recap, Recall, Ask, fixes)" other="# other calls (recap, Recall, Ask, fixes)" /></span>
              <span className="tabular-nums text-foreground">{cost}</span>
            </p>
          ) })()}
        </div>
      )}
    </div>
  )
}

function JobRow({
  job, open, onToggle, isAdmin, calls, money, num, duration, when, statusLabel, scopeLabel,
}: {
  job: AiJobSummary
  open: boolean
  onToggle: () => void
  isAdmin: boolean
  calls: AiJobCall[] | 'loading' | 'error' | undefined
  money: (n: number | null | undefined) => string
  num: (n: number) => string
  duration: (secs: number | null | undefined) => string
  when: (iso: string | null) => string
  statusLabel: (s: DigestJobStatus) => string
  scopeLabel: (s: string) => string
}) {
  const { i18n } = useLingui()
  const clock = (iso: string | null) => iso
    ? new Date(iso).toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : ''
  const done = job.done
  const total = job.total
  const est = job.est_cost_usd != null ? money(job.est_cost_usd) : null
  const took = duration(job.actual_seconds)
  const estTime = job.est_seconds != null ? duration(job.est_seconds) : null

  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="w-full px-3 py-2 pl-8 text-left hover:bg-muted/50 transition-colors space-y-0.5"
      >
        <span className="flex items-center gap-2 min-w-0">
          <ChevronDown className={cn('w-3 h-3 shrink-0 text-muted-foreground/60 transition-transform', !open && '-rotate-90')} />
          <span className="text-foreground truncate">
            {when(job.created_at)} · {scopeLabel(job.scope)}
            {isAdmin && job.username && <span className="text-muted-foreground"> · {job.username}</span>}
          </span>
          <span className="ml-auto tabular-nums text-foreground shrink-0">{money(job.cost_usd)}</span>
        </span>
        <span className="flex flex-wrap gap-x-2 pl-5 text-muted-foreground">
          <span>{statusLabel(job.status)}</span>
          <span className="tabular-nums"><Trans>{done} of {total} chapters</Trans></span>
          {job.failed > 0 && (() => { const f = job.failed; return <span className="tabular-nums"><Trans>{f} could not be read</Trans></span> })()}
          {est && <span className="tabular-nums"><Trans>estimate {est}</Trans></span>}
          {took && (estTime
            ? <span className="tabular-nums"><Trans>took {took}, estimate {estTime}</Trans></span>
            : <span className="tabular-nums"><Trans>took {took}</Trans></span>)}
        </span>
        {job.stop_reason && <span className="block pl-5 text-muted-foreground/80">{job.stop_reason}</span>}
      </button>

      {open && (
        <div className="pl-12 pr-3 pb-2.5">
          {calls === 'loading' || calls == null ? (
            <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="w-3 h-3 animate-spin" /> <Trans>Loading…</Trans></p>
          ) : calls === 'error' ? (
            <p className="text-muted-foreground"><Trans>Could not load the calls.</Trans></p>
          ) : calls.length === 0 ? (
            <p className="text-muted-foreground"><Trans>No model calls.</Trans></p>
          ) : (
            <div className="rounded-md border border-border/60 divide-y divide-border/60 bg-background/60">
              <div className="hidden sm:grid grid-cols-[5.5rem_1fr_5rem_5rem_5rem_4rem_4.5rem] gap-2 px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                <span><Trans>Time</Trans></span>
                <span><Trans>Model</Trans></span>
                <span className="text-right"><Trans>Tokens in</Trans></span>
                <span className="text-right"><Trans>Cached</Trans></span>
                <span className="text-right"><Trans>Tokens out</Trans></span>
                <span className="text-right"><Trans>Took</Trans></span>
                <span className="text-right"><Trans>Cost</Trans></span>
              </div>
              {calls.map(c => {
                const tokensIn = num(c.input_tokens + c.cache_read_tokens + c.cache_write_tokens)
                const cached = num(c.cache_read_tokens)
                return (
                  <div key={c.id} className="grid grid-cols-[1fr_auto] sm:grid-cols-[5.5rem_1fr_5rem_5rem_5rem_4rem_4.5rem] gap-x-2 gap-y-0.5 px-2.5 py-1.5 tabular-nums">
                    <span className="text-muted-foreground">
                      {clock(c.created_at)}
                    </span>
                    <span className="text-right sm:hidden text-foreground">{money(c.cost_usd)}</span>
                    <span className="text-muted-foreground truncate col-span-2 sm:col-span-1 font-mono text-[11px]" title={aiFeatureLabel(c.feature)}>{c.model}</span>
                    <span className="text-muted-foreground sm:text-right col-span-2 sm:col-span-1">
                      <span className="sm:hidden"><Trans>in</Trans> </span>{tokensIn}
                      {c.cache_read_tokens > 0 && <span className="sm:hidden text-muted-foreground/60"> <Trans>({cached} cached)</Trans></span>}
                    </span>
                    <span className="text-muted-foreground text-right hidden sm:block">{cached}</span>
                    <span className="text-muted-foreground sm:text-right col-span-2 sm:col-span-1">
                      <span className="sm:hidden"><Trans>out</Trans> </span>{num(c.output_tokens)}
                    </span>
                    <span className="text-muted-foreground text-right hidden sm:block">{duration(c.duration_ms != null ? c.duration_ms / 1000 : null)}</span>
                    <span className="text-right hidden sm:block text-foreground">{money(c.cost_usd)}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default AiActivity
