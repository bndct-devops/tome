// The Recap section as a list: one row per earlier volume in the reader's
// window, each with its own stored recap (written from that volume's chapter
// summaries only), and the open book's live "Recap to here" as the last row.
//
// Nothing is written without a click. "Write it" goes through the same path
// as the page's other digests: when the volume still has unread chapters, the
// confirmation dialog asks first over CONFIRM_THRESHOLD chapters or over
// budget, the chapters are read, and the recap is asked for again after.
// "Write all" writes every missing or stale recap whose chapters are all read,
// one call each, after showing the count and a conservative cost; it never
// starts reading chapters (those volumes are left for "Write it"). The run
// goes on on the server: the list is polled while `write_all.running` is
// true (also when another tab started it), and the rows stay locked.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, Loader2, Pause, RotateCcw, Sparkles } from 'lucide-react'
import { Trans, useLingui } from '@lingui/react/macro'
import { plural } from '@lingui/core/macro'
import { DigestConfirmDialog } from '@/components/DigestConfirmDialog'
import { cn } from '@/lib/utils'
import {
  getRecapVolumes, requestVolumeRecap, isVolumeRecapPending, isAlreadyRunning, writeAllVolumeRecaps,
  startDigest, getDigestJob, cancelDigestJob,
  DIGEST_JOB_ACTIVE, CONFIRM_THRESHOLD,
  type DigestEstimate, type DigestJob, type RecapVolumes as RecapVolumesPayload,
  type VolumeRecap, type VolumeRecapPending, type VolumeRecapSkip,
} from '@/lib/ai'

const POLL_MS = 2000
const WRITE_ALL_POLL_MS = 3000
const POLL_FAILURES = 15

/** A "Write all" run being watched: how many it set out to write, and the
 *  rows that were already ready when watching began. */
interface WatchedRun {
  started: number
  readyBefore: Set<number>
}

function readyIds(volumes: VolumeRecap[] | null | undefined): Set<number> {
  return new Set((volumes ?? []).filter(v => v.status === 'ready').map(v => v.book_id))
}

function isActive(job: DigestJob | null): job is DigestJob {
  return job != null && DIGEST_JOB_ACTIVE.includes(job.status)
}

function paragraphs(text: string): string[] {
  return text.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean)
}

interface RecapVolumesProps {
  bookId: number
  /** One paragraph of recap text, glossary names linked. */
  renderParagraph: (text: string) => React.ReactNode
  /** The open book's row body: the live recap, or the no-position line. */
  current: React.ReactNode
  /** A volume's digest job ended (the glossary may have grown). */
  onDigestEnded: () => void
  /** Some recap text is on screen (the page loads the glossary for links). */
  onTextShown: () => void
}

export function RecapVolumes({ bookId, renderParagraph, current, onDigestEnded, onTextShown }: RecapVolumesProps) {
  const { t, i18n } = useLingui()
  const [volumes, setVolumes] = useState<VolumeRecap[] | null>(null)
  const [canWrite, setCanWrite] = useState(false)
  const [writeAll, setWriteAll] = useState<RecapVolumesPayload['write_all'] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [run, setRun] = useState<WatchedRun | null>(null)
  const runRef = useRef(false)
  const [allSkipped, setAllSkipped] = useState<VolumeRecapSkip[]>([])
  const [allNotice, setAllNotice] = useState<string | null>(null)
  const [allError, setAllError] = useState<string | null>(null)

  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  const apply = useCallback((r: RecapVolumesPayload) => {
    setVolumes(r.volumes)
    setCanWrite(r.can_write)
    setWriteAll(r.write_all ?? null)
    setLoadError(null)
    const wa = r.write_all
    if (wa?.running) {
      // Running (started here or in another tab): watch it.
      runRef.current = true
      setRun(prev => prev ?? { started: wa.last?.started ?? 0, readyBefore: readyIds(r.volumes) })
    } else if (runRef.current) {
      // The watched run ended: show what it left alone.
      runRef.current = false
      setRun(null)
      setAllSkipped(wa?.last?.skipped ?? [])
    }
  }, [])

  const reload = useCallback(() => {
    getRecapVolumes(bookId)
      .then(apply)
      .catch(e => setLoadError(e instanceof Error ? e.message : t`Could not load the earlier volumes`))
  }, [bookId, apply, t])

  useEffect(() => { reload() }, [reload])

  const replace = useCallback((v: VolumeRecap) => {
    setVolumes(prev => prev?.map(x => (x.book_id === v.book_id ? v : x)) ?? prev)
    // A row was written on its own: the "Write all" figures moved.
    reload()
  }, [reload])

  const watching = run != null
  useEffect(() => {
    if (!watching) return
    // A failed poll (a timeout, a blip) is not the run ending: keep
    // polling, give up only after POLL_FAILURES in a row.
    let failures = 0
    const timer = window.setInterval(() => {
      getRecapVolumes(bookId)
        .then(r => {
          if (!alive.current) return
          failures = 0
          apply(r)
        })
        .catch(() => {
          if (!alive.current) return
          failures += 1
          if (failures < POLL_FAILURES) return
          runRef.current = false
          setRun(null)
        })
    }, WRITE_ALL_POLL_MS)
    return () => window.clearInterval(timer)
  }, [watching, bookId, apply])

  async function runWriteAll(count: number) {
    runRef.current = true
    setRun({ started: count, readyBefore: readyIds(volumes) })
    setAllSkipped([])
    setAllNotice(null)
    setAllError(null)
    try {
      const res = await writeAllVolumeRecaps(bookId)
      if (!alive.current) return
      setRun(prev => (prev ? { ...prev, started: res.started } : prev))
      apply(res.volumes)
    } catch (e) {
      if (!alive.current) return
      runRef.current = false
      setRun(null)
      if (isAlreadyRunning(e)) {
        setAllNotice(t`Already being written. Check back in a minute.`)
        reload()
      } else {
        setAllError(e instanceof Error ? e.message : t`Could not write the recaps`)
      }
    }
  }

  const money = (n: number) => {
    const fmt = (v: number) => new Intl.NumberFormat(i18n.locale, {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
    }).format(v)
    if (n > 0 && n < 0.01) { const min = fmt(0.01); return t`under ${min}` }
    return fmt(n)
  }

  const unreadCount = allSkipped.filter(s => s.reason === 'unread').length
  const budgetSkip = allSkipped.find(s => s.reason === 'budget')
  const errorCount = allSkipped.filter(s => s.reason === 'error').length
  const pendingCount = writeAll?.count ?? 0
  const cost = money(writeAll?.est_cost_usd ?? 0)
  const runTotal = run?.started ?? 0
  const turnedReady = run
    ? (volumes ?? []).filter(v => v.status === 'ready' && !run.readyBefore.has(v.book_id)).length
    : 0
  const runDone = Math.min(runTotal, Math.max(turnedReady, writeAll?.running ? (writeAll.last?.written.length ?? 0) : 0))
  const showOffer = canWrite && volumes != null && pendingCount > 0 && run == null
  const showHeader = showOffer || run != null || unreadCount > 0 || budgetSkip != null
    || errorCount > 0 || allNotice != null || allError != null

  return (
    <div className="rounded-lg border border-border bg-muted/40">
      {showHeader && (
        <div className="px-3 py-2 space-y-1.5 border-b border-border/60">
          {showOffer && (
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
              <p className="text-xs text-muted-foreground min-w-0">
                {plural(pendingCount, {
                  one: `# volume not written yet · about ${cost}`,
                  other: `# volumes not written yet · about ${cost}`,
                })}
              </p>
              <button
                type="button"
                onClick={() => void runWriteAll(pendingCount)}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted transition-colors shrink-0"
              >
                <Sparkles className="w-3 h-3" />
                <Trans>Write all</Trans>
              </button>
            </div>
          )}
          {run != null && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
              <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
              {runTotal > 0 ? t`Writing volume recaps… ${runDone} of ${runTotal}` : t`Writing volume recaps…`}
            </p>
          )}
          {unreadCount > 0 && (
            <p className="text-xs text-muted-foreground">
              {plural(unreadCount, {
                one: '# volume still needs its chapters read; use Write it on it.',
                other: '# volumes still need their chapters read; use Write it on them.',
              })}
            </p>
          )}
          {budgetSkip?.message && <p className="text-xs text-muted-foreground">{budgetSkip.message}</p>}
          {errorCount > 0 && (
            <p className="text-xs text-destructive">
              {plural(errorCount, {
                one: '# recap could not be written. Try Write it on it.',
                other: '# recaps could not be written. Try Write it on them.',
              })}
            </p>
          )}
          {allNotice && <p className="text-xs text-muted-foreground">{allNotice}</p>}
          {allError && <p className="text-xs text-destructive">{allError}</p>}
        </div>
      )}
      <ul className="divide-y divide-border/60">
        {volumes == null && !loadError && (
          <li className="px-3 py-2 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            <Trans>Loading the earlier volumes…</Trans>
          </li>
        )}
        {loadError && <li className="px-3 py-2 text-xs text-destructive">{loadError}</li>}
        {volumes?.map(v => (
          <VolumeRow
            key={v.book_id}
            bookId={bookId}
            vol={v}
            canWrite={canWrite}
            locked={run != null}
            renderParagraph={renderParagraph}
            onChange={replace}
            onReload={reload}
            onDigestEnded={onDigestEnded}
            onTextShown={onTextShown}
          />
        ))}
        <li className="px-3 py-2.5 space-y-2">
          <p className="text-sm font-medium text-foreground"><Trans>This book</Trans></p>
          {current}
        </li>
      </ul>
    </div>
  )
}

interface VolumeRowProps {
  bookId: number
  vol: VolumeRecap
  canWrite: boolean
  /** "Write all" is running: the row's own write buttons are disabled. */
  locked: boolean
  renderParagraph: (text: string) => React.ReactNode
  onChange: (v: VolumeRecap) => void
  onReload: () => void
  onDigestEnded: () => void
  onTextShown: () => void
}

function VolumeRow({ bookId, vol, canWrite, locked, renderParagraph, onChange, onReload, onDigestEnded, onTextShown }: VolumeRowProps) {
  const { t } = useLingui()
  const [open, setOpen] = useState(false)
  const [phase, setPhase] = useState<'idle' | 'writing' | 'waiting'>('idle')
  const [job, setJob] = useState<DigestJob | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [stopped, setStopped] = useState<DigestJob | null>(null)
  const [confirm, setConfirm] = useState<DigestEstimate | null>(null)
  const [confirmBusy, setConfirmBusy] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)

  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  const volId = vol.book_id
  const hasText = vol.text != null && vol.text.trim() !== ''

  useEffect(() => {
    if (open && hasText) onTextShown()
  }, [open, hasText, onTextShown])

  /** POST the volume recap. Returns the 202 body when chapters are unread,
   *  except right after a digest job (then it says so instead of looping).
   *  `again` rewrites a stored recap in place; `force` rewrites a partial one. */
  const write = useCallback(async (
    opts: { force?: boolean; again?: boolean } = {}, afterDigest = false,
  ): Promise<VolumeRecapPending | null> => {
    setError(null)
    setNotice(null)
    setPhase('writing')
    try {
      const res = await requestVolumeRecap(bookId, volId, opts)
      if (!alive.current) return null
      if (isVolumeRecapPending(res)) {
        if (afterDigest) {
          setPhase('idle')
          setError(t`Some chapters of this volume are still unread.`)
          onReload()
          return null
        }
        return res
      }
      setPhase('idle')
      setOpen(true)
      onChange(res)
    } catch (e) {
      if (!alive.current) return null
      setPhase('idle')
      if (isAlreadyRunning(e)) setNotice(t`Already being written. Check back in a minute.`)
      else setError(e instanceof Error ? e.message : t`Could not write the recap`)
    }
    return null
  }, [bookId, volId, onChange, onReload, t])

  // A job for this volume ended: write the recap when every chapter was read.
  const ended = useCallback((j: DigestJob) => {
    setJob(null)
    onDigestEnded()
    if (j.status === 'done') {
      setStopped(null)
      void write({}, true)
    } else {
      setPhase('idle')
      setStopped(j)
      onReload()
    }
  }, [onDigestEnded, onReload, write])

  const jobId = isActive(job) ? job.id : null
  useEffect(() => {
    if (!jobId) return
    // A failed poll (a timeout, a blip) is not the job ending: keep
    // polling, give up only after POLL_FAILURES in a row.
    let failures = 0
    const timer = window.setInterval(() => {
      getDigestJob(jobId)
        .then(j => {
          if (!alive.current) return
          failures = 0
          if (isActive(j)) setJob(j)
          else ended(j)
        })
        .catch(() => {
          if (!alive.current) return
          failures += 1
          if (failures < POLL_FAILURES) return
          setJob(null)
          setPhase('idle')
        })
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [jobId, ended])

  /** Start reading this volume's unread chapters. Returns an error message,
   *  or null when it started (or nothing was left to read). */
  async function begin(): Promise<string | null> {
    try {
      const res = await startDigest(volId, 'book')
      if (!alive.current) return null
      setStopped(null)
      if (isActive(res.job)) {
        setJob(res.job)
        setPhase('waiting')
      } else {
        void write({}, true)
      }
      return null
    } catch (e) {
      return e instanceof Error ? e.message : t`Could not start reading the chapters`
    }
  }

  /** Start at once when the work is small, else ask with the estimate. */
  function decide(est: DigestEstimate) {
    if (est.chapters > 0 && est.chapters <= CONFIRM_THRESHOLD && !est.over_budget) {
      setPhase('waiting')
      void begin().then(err => {
        if (err && alive.current) { setPhase('idle'); setError(err) }
      })
      return
    }
    setPhase('idle')
    setConfirmError(null)
    setConfirm(est)
  }

  async function confirmStart() {
    setConfirmBusy(true)
    setConfirmError(null)
    const err = await begin()
    if (!alive.current) return
    setConfirmBusy(false)
    if (err) { setConfirmError(err); return }
    setConfirm(null)
  }

  async function writeIt(opts: { force?: boolean; again?: boolean } = {}) {
    setOpen(true)
    const pending = await write(opts)
    if (pending && alive.current) decide(pending.estimate)
  }

  async function stop() {
    if (!job) return
    try {
      const j = await cancelDigestJob(job.id)
      if (!alive.current) return
      if (isActive(j)) setJob(j)
      else ended(j)
    } catch { /* the poll picks up the final state */ }
  }

  const idx = vol.series_index
  const title = vol.title
  const label = idx != null ? t`vol ${idx} · ${title}` : title
  const unread = vol.missing_chapters
  const statusWord = vol.status === 'missing'
    ? t`not written yet`
    : vol.status === 'partial'
      ? plural(unread, { one: '# chapter unread', other: '# chapters unread' })
      : null
  const busy = phase !== 'idle' || isActive(job)

  const linkButtonClassName = 'flex items-center gap-1 text-xs font-medium text-primary hover:underline disabled:opacity-60'
  const buttonClassName = 'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted transition-colors disabled:opacity-60 shrink-0'

  let progress: React.ReactNode = null
  if (phase === 'writing') {
    progress = (
      <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
        <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
        <Trans>Writing the recap. This can take up to a minute.</Trans>
      </p>
    )
  } else if (phase === 'waiting') {
    const done = job?.done ?? 0
    const total = job?.total ?? 0
    const text = job?.status === 'stopping'
      ? t`Stopping after the current chapters…`
      : job == null || job.status === 'queued' || total === 0
        ? t`Getting ready to read the chapters…`
        : t`Reading chapters: ${done} of ${total}…`
    progress = (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground" role="status">
        <span className="flex items-center gap-2 min-w-0">
          <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
          {text}
        </span>
        {job != null && job.status !== 'stopping' && (
          <button type="button" onClick={() => void stop()} className="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline">
            <Trans>Stop</Trans>
          </button>
        )}
      </div>
    )
  }

  let stoppedLine: React.ReactNode = null
  if (stopped && phase === 'idle') {
    const done = stopped.done
    const total = stopped.total
    const text = stopped.stop_reason
      ? stopped.stop_reason
      : stopped.status === 'interrupted'
        ? t`Interrupted by a server restart after ${done} of ${total} chapters.`
        : t`Stopped after ${done} of ${total} chapters.`
    stoppedLine = (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Pause className="w-3 h-3 shrink-0" />
        <span className="min-w-0">{text}</span>
      </p>
    )
  }

  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        className={cn('w-full text-left px-3 py-2 transition-colors', !open && 'hover:bg-muted/60')}
      >
        <span className="flex items-baseline gap-2 min-w-0">
          <span className="text-sm font-medium text-foreground min-w-0 break-words">{label}</span>
          <span className="ml-auto flex items-center gap-2 shrink-0 self-center">
            {busy && <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />}
            {statusWord && <span className="text-xs text-muted-foreground">{statusWord}</span>}
            <ChevronDown className={cn('w-3 h-3 text-muted-foreground/50 transition-transform', !open && '-rotate-90')} />
          </span>
        </span>
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-3">
          {hasText && vol.text != null && phase !== 'writing' && (
            <>
              {paragraphs(vol.text).map((p, i) => (
                <p key={i} className="text-sm text-foreground leading-relaxed">{renderParagraph(p)}</p>
              ))}
              {vol.status === 'partial' && (
                <p className="text-xs text-muted-foreground/70">
                  {plural(unread, {
                    one: 'Written with # chapter unread, so it may skip parts.',
                    other: 'Written with # chapters unread, so it may skip parts.',
                  })}
                </p>
              )}
            </>
          )}
          {!hasText && phase === 'idle' && (
            <p className="text-sm text-muted-foreground">
              {unread > 0 && canWrite
                ? plural(unread, {
                    one: 'Not written yet. # chapter needs to be read first.',
                    other: 'Not written yet. # chapters need to be read first.',
                  })
                : <Trans>Not written yet.</Trans>}
            </p>
          )}
          {progress}
          {stoppedLine}
          {notice && <p className="text-xs text-muted-foreground">{notice}</p>}
          {error && <p className="text-xs text-destructive">{error}</p>}
          {canWrite && !busy && (
            !hasText ? (
              <button type="button" onClick={() => void writeIt()} disabled={locked} className={buttonClassName}>
                <Trans>Write it</Trans>
              </button>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 pt-1 border-t border-border/60">
                <p className="text-xs text-muted-foreground/70 pt-2">
                  <Trans>Written for the whole volume · cached, free to reopen</Trans>
                </p>
                {vol.status === 'partial' ? (
                  <button type="button" onClick={() => void writeIt({ force: true })} disabled={locked} className={cn(linkButtonClassName, 'pt-2')}>
                    <RotateCcw className="w-3 h-3" />
                    <Trans>Write again</Trans>
                  </button>
                ) : (
                  <button type="button" onClick={() => void writeIt({ again: true })} disabled={locked} className={cn(linkButtonClassName, 'pt-2')}>
                    <RotateCcw className="w-3 h-3" />
                    <Trans>Ask again</Trans>
                  </button>
                )}
              </div>
            )
          )}
        </div>
      )}
      <DigestConfirmDialog
        open={confirm != null}
        book={confirm}
        series={null}
        scope="book"
        busy={confirmBusy}
        error={confirmError}
        onCancel={() => setConfirm(null)}
        onStart={() => void confirmStart()}
      />
    </li>
  )
}

export default RecapVolumes
