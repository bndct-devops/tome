// The Recap section as a list: one row per earlier volume in the reader's
// window, each with its own stored recap (written from that volume's chapter
// summaries only), and the open book's live "Recap to here" as the last row.
//
// Nothing is written without a click. "Write it" goes through the same path
// as the page's other digests: when the volume still has unread chapters, the
// confirmation dialog asks first over CONFIRM_THRESHOLD chapters or over
// budget, the chapters are read, and the recap is asked for again after.
// "Write all" is one background job: it reads the missing chapters of the
// read earlier volumes, merges duplicate glossary entries, then writes every
// missing or stale recap. It asks first (both costs and a total) when chapters
// need reading or the run would pass a limit, else starts at once. The job is
// polled by id; `write_all.job` on the list reconnects after a reload (also to
// a run started in another tab), and the rows stay locked while it runs.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, Loader2, Pause, RotateCcw, Sparkles } from 'lucide-react'
import { Trans, useLingui } from '@lingui/react/macro'
import { plural } from '@lingui/core/macro'
import { DigestConfirmDialog } from '@/components/DigestConfirmDialog'
import { cn } from '@/lib/utils'
import { Pill } from '@/components/page'
import {
  getRecapVolumes, requestVolumeRecap, isVolumeRecapPending, isAlreadyRunning, startWriteAll,
  startDigest, getDigestJob, cancelDigestJob, activeJobLine, isWriteAllJob,
  DIGEST_JOB_ACTIVE, CONFIRM_THRESHOLD,
  type DigestEstimate, type DigestJob, type RecapVolumes as RecapVolumesPayload,
  type VolumeRecap, type VolumeRecapPending, type WriteAll,
} from '@/lib/ai'

const POLL_MS = 2000
const POLL_FAILURES = 15

function isActive(job: DigestJob | null | undefined): job is DigestJob {
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
  const [writeAll, setWriteAll] = useState<WriteAll | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  // The "Write all" job being followed, and how the last one ended.
  const [allJob, setAllJob] = useState<DigestJob | null>(null)
  const [allEnded, setAllEnded] = useState<DigestJob | null>(null)
  const [allStarting, setAllStarting] = useState(false)
  const [allNotice, setAllNotice] = useState<string | null>(null)
  const [allError, setAllError] = useState<string | null>(null)
  const [confirmAll, setConfirmAll] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)

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
    // A Write all run going on (started here before a reload, or in another
    // tab). Other active jobs for the book (reading, merging) are left to the
    // companion's own progress line.
    const running = r.write_all?.job
    if (isActive(running) && isWriteAllJob(running)) {
      setAllJob(prev => (isActive(prev) && prev.id === running.id ? prev : running))
    } else {
      // How the newest Write all run ended, when nothing newer has run since.
      const last = r.write_all?.last
      if (!isActive(running) && last && !isActive(last) && isWriteAllJob(last)) {
        setAllEnded(prev => prev ?? last)
      }
    }
  }, [])

  const reload = useCallback(() => {
    getRecapVolumes(bookId)
      .then(r => { if (alive.current) apply(r) })
      .catch(e => {
        if (alive.current) setLoadError(e instanceof Error ? e.message : t`Could not load the earlier volumes`)
      })
  }, [bookId, apply, t])

  useEffect(() => { reload() }, [reload])

  const replace = useCallback((v: VolumeRecap) => {
    setVolumes(prev => prev?.map(x => (x.book_id === v.book_id ? v : x)) ?? prev)
    // A row was written on its own: the "Write all" figures moved.
    reload()
  }, [reload])

  const allEndedRun = useCallback((j: DigestJob) => {
    setAllJob(null)
    setAllEnded(j)
    onDigestEnded()
    reload()
  }, [onDigestEnded, reload])

  const allJobId = isActive(allJob) ? allJob.id : null
  useEffect(() => {
    if (!allJobId) return
    // A failed poll (a timeout, a blip) is not the run ending: keep
    // polling, give up only after POLL_FAILURES in a row.
    let failures = 0
    const timer = window.setInterval(() => {
      getDigestJob(allJobId)
        .then(j => {
          if (!alive.current) return
          failures = 0
          if (isActive(j)) setAllJob(j)
          else allEndedRun(j)
        })
        .catch(() => {
          if (!alive.current) return
          failures += 1
          if (failures < POLL_FAILURES) return
          setAllJob(null)
          reload()
        })
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [allJobId, allEndedRun, reload])

  /** POST write-all. Returns an error message, or null when it started. */
  async function beginAll(): Promise<string | null> {
    setAllNotice(null)
    setAllError(null)
    setAllEnded(null)
    try {
      const res = await startWriteAll(bookId)
      if (!alive.current) return null
      if (isActive(res.job)) setAllJob(res.job)
      else allEndedRun(res.job)
      return null
    } catch (e) {
      if (!alive.current) return null
      if (isAlreadyRunning(e)) {
        setAllNotice(t`Something is already running for this book. Try again when it is done.`)
        reload()
        return null
      }
      return e instanceof Error ? e.message : t`Could not write the recaps`
    }
  }

  const readingChapters = writeAll?.reading?.chapters ?? 0
  const remaining = writeAll?.remaining_budget_usd ?? null
  // Merging duplicate entries counts only when there is something to merge.
  const mergeCost = (writeAll?.merging?.pairs ?? 0) > 0 ? writeAll?.merging?.est_cost_usd ?? 0 : 0
  const totalCost = (writeAll?.est_cost_usd ?? 0) + (writeAll?.reading?.est_cost_usd ?? 0) + mergeCost
  const overBudget = remaining != null && totalCost > remaining

  async function clickWriteAll() {
    if (readingChapters > 0 || overBudget) {
      setConfirmError(null)
      setConfirmAll(true)
      return
    }
    setAllStarting(true)
    const err = await beginAll()
    if (!alive.current) return
    setAllStarting(false)
    if (err) setAllError(err)
  }

  async function confirmStartAll() {
    setAllStarting(true)
    setConfirmError(null)
    const err = await beginAll()
    if (!alive.current) return
    setAllStarting(false)
    if (err) { setConfirmError(err); return }
    setConfirmAll(false)
  }

  async function stopAll() {
    if (!allJob) return
    try {
      const j = await cancelDigestJob(allJob.id)
      if (!alive.current) return
      if (isActive(j)) setAllJob(j)
      else allEndedRun(j)
    } catch { /* the poll picks up the final state */ }
  }

  const money = (n: number) => {
    const fmt = (v: number) => new Intl.NumberFormat(i18n.locale, {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
    }).format(v)
    if (n > 0 && n < 0.01) { const min = fmt(0.01); return t`under ${min}` }
    return fmt(n)
  }

  const running = isActive(allJob)
  const pendingCount = writeAll?.count ?? 0
  const cost = money(writeAll?.est_cost_usd ?? 0)
  const showOffer = canWrite && volumes != null && pendingCount > 0 && !running
  let offerText = ''
  if (readingChapters > 0 && writeAll) {
    const chapters = plural(readingChapters, { one: '# chapter', other: '# chapters' })
    const readCost = money(writeAll.reading.est_cost_usd)
    const recaps = plural(pendingCount, {
      one: `then # recap (about ${cost})`,
      other: `then # recaps (about ${cost})`,
    })
    offerText = plural(writeAll.reading.volumes, {
      one: `# volume still needs reading (${chapters}, about ${readCost}) · ${recaps}`,
      other: `# volumes still need reading (${chapters}, about ${readCost}) · ${recaps}`,
    })
  } else {
    // One figure: the recaps plus any merging.
    const allCost = money((writeAll?.est_cost_usd ?? 0) + mergeCost)
    offerText = plural(pendingCount, {
      one: `# volume not written yet · about ${allCost}`,
      other: `# volumes not written yet · about ${allCost}`,
    })
  }
  const endedLine = allEnded && !running
    ? allEnded.stop_reason
      ?? (allEnded.status === 'interrupted'
        ? t`Interrupted by a server restart. Write all again to go on.`
        : allEnded.status === 'cancelled'
          ? t`Stopped. What was done so far is kept.`
          : null)
    : null
  // What the last run's recap step could not do (from its follow-up).
  const recapsStep = allEnded && !running
    ? allEnded.followups?.find(f => f.name === 'volume_recaps')
    : undefined
  const errorCount = recapsStep?.failed?.length ?? 0
  const unreadCount = recapsStep?.skipped?.filter(x => x.reason === 'unread').length ?? 0
  const showHeader = showOffer || running || endedLine != null || errorCount > 0 || unreadCount > 0
    || allNotice != null || allError != null

  return (
    <div>
      {showHeader && (
        <div className="space-y-2 pb-3">
          {showOffer && (
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
              <p className="text-[13px] text-muted-foreground min-w-0">{offerText}</p>
              <Pill onClick={() => void clickWriteAll()} disabled={allStarting} className="shrink-0">
                {allStarting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                <Trans>Write all</Trans>
              </Pill>
            </div>
          )}
          {running && allJob && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[14px] text-muted-foreground" role="status">
              <span className="flex items-center gap-2 min-w-0">
                <Loader2 className="h-4 w-4 animate-spin shrink-0" />
                {activeJobLine(allJob)}
              </span>
              {allJob.status !== 'stopping' && (
                <Pill variant="quiet" className="-my-2" onClick={() => void stopAll()}>
                  <Trans>Stop</Trans>
                </Pill>
              )}
            </div>
          )}
          {endedLine && (
            <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
              <Pause className="h-3.5 w-3.5 shrink-0" />
              <span className="min-w-0">{endedLine}</span>
            </p>
          )}
          {errorCount > 0 && (
            <p className="text-[13px] text-destructive">
              {plural(errorCount, {
                one: '# recap could not be written. Try Write it on it.',
                other: '# recaps could not be written. Try Write it on them.',
              })}
            </p>
          )}
          {unreadCount > 0 && (
            <p className="text-[13px] text-muted-foreground">
              {plural(unreadCount, {
                one: '# volume still needs its chapters read; use Write it on it.',
                other: '# volumes still need their chapters read; use Write it on them.',
              })}
            </p>
          )}
          {allNotice && <p className="text-[13px] text-muted-foreground">{allNotice}</p>}
          {allError && <p className="text-[13px] text-destructive">{allError}</p>}
        </div>
      )}
      <ul className="divide-y divide-border/70">
        {volumes == null && !loadError && (
          <li className="flex items-center gap-2 py-3 text-[14px] text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <Trans>Loading the earlier volumes…</Trans>
          </li>
        )}
        {loadError && <li className="py-3 text-[13px] text-destructive">{loadError}</li>}
        {volumes?.map(v => (
          <VolumeRow
            key={v.book_id}
            bookId={bookId}
            vol={v}
            canWrite={canWrite}
            locked={running}
            renderParagraph={renderParagraph}
            onChange={replace}
            onReload={reload}
            onDigestEnded={onDigestEnded}
            onTextShown={onTextShown}
          />
        ))}
        <li className="space-y-3 py-3">
          <p className="text-[15px] font-semibold text-foreground"><Trans>This book</Trans></p>
          {current}
        </li>
      </ul>
      {writeAll && (
        <DigestConfirmDialog
          open={confirmAll}
          book={null}
          series={null}
          writeAll={{
            reading: writeAll.reading,
            count: writeAll.count,
            est_cost_usd: writeAll.est_cost_usd,
            merging: writeAll.merging ?? null,
            remaining_budget_usd: remaining,
          }}
          busy={allStarting}
          error={confirmError}
          onCancel={() => setConfirmAll(false)}
          onStart={() => void confirmStartAll()}
        />
      )}
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

  let progress: React.ReactNode = null
  if (phase === 'writing') {
    progress = (
      <p className="flex items-center gap-2 text-[14px] text-muted-foreground" role="status">
        <Loader2 className="h-4 w-4 animate-spin shrink-0" />
        <Trans>Writing the recap. This can take up to a minute.</Trans>
      </p>
    )
  } else if (phase === 'waiting') {
    const text = job == null ? t`Getting ready to read the chapters…` : activeJobLine(job)
    progress = (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[14px] text-muted-foreground" role="status">
        <span className="flex items-center gap-2 min-w-0">
          <Loader2 className="h-4 w-4 animate-spin shrink-0" />
          {text}
        </span>
        {job != null && job.status !== 'stopping' && (
          <Pill variant="quiet" className="-my-2" onClick={() => void stop()}>
            <Trans>Stop</Trans>
          </Pill>
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
      <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
        <Pause className="h-3.5 w-3.5 shrink-0" />
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
        className="group flex w-full items-center gap-3 py-3 text-left"
      >
        <span className="min-w-0 flex-1 break-words text-[15px] font-semibold text-foreground transition-colors group-hover:text-primary">{label}</span>
        <span className="flex shrink-0 items-center gap-2">
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
          {statusWord && <span className="text-[13px] text-muted-foreground">{statusWord}</span>}
          <ChevronDown className={cn('h-4 w-4 text-muted-foreground transition-transform', open && 'rotate-180')} />
        </span>
      </button>
      {open && (
        <div className="max-w-[70ch] space-y-3 pb-4">
          {hasText && vol.text != null && phase !== 'writing' && (
            <>
              {paragraphs(vol.text).map((p, i) => (
                <p key={i} className="text-[15px] leading-relaxed text-foreground/85">{renderParagraph(p)}</p>
              ))}
              {vol.status === 'partial' && (
                <p className="text-[13px] text-muted-foreground">
                  {plural(unread, {
                    one: 'Written with # chapter unread, so it may skip parts.',
                    other: 'Written with # chapters unread, so it may skip parts.',
                  })}
                </p>
              )}
            </>
          )}
          {!hasText && phase === 'idle' && (
            <p className="text-[14px] text-muted-foreground">
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
          {notice && <p className="text-[13px] text-muted-foreground">{notice}</p>}
          {error && <p className="text-[13px] text-destructive">{error}</p>}
          {canWrite && !busy && (
            !hasText ? (
              <Pill onClick={() => void writeIt()} disabled={locked}>
                <Trans>Write it</Trans>
              </Pill>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-border/70 pt-2">
                <p className="text-[13px] text-muted-foreground">
                  <Trans>Written for the whole volume · cached, free to reopen</Trans>
                </p>
                {vol.status === 'partial' ? (
                  <Pill variant="quiet" className="-mr-3" onClick={() => void writeIt({ force: true })} disabled={locked}>
                    <RotateCcw className="h-4 w-4" />
                    <Trans>Write again</Trans>
                  </Pill>
                ) : (
                  <Pill variant="quiet" className="-mr-3" onClick={() => void writeIt({ again: true })} disabled={locked}>
                    <RotateCcw className="h-4 w-4" />
                    <Trans>Ask again</Trans>
                  </Pill>
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
