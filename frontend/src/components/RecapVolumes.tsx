// The Recap section as a list: one row per earlier volume in the reader's
// window, each with its own stored recap (written from that volume's chapter
// summaries only), and the open book's live "Recap to here" as the last row.
//
// Nothing is written without a click. "Write it" goes through the same path
// as the page's other digests: when the volume still has unread chapters, the
// confirmation dialog asks first over CONFIRM_THRESHOLD chapters or over
// budget, the chapters are read, and the recap is asked for again after.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, Loader2, Pause, RotateCcw } from 'lucide-react'
import { Trans, useLingui } from '@lingui/react/macro'
import { plural } from '@lingui/core/macro'
import { DigestConfirmDialog } from '@/components/DigestConfirmDialog'
import { cn } from '@/lib/utils'
import {
  getRecapVolumes, requestVolumeRecap, isVolumeRecapPending, isAlreadyRunning,
  startDigest, getDigestJob, cancelDigestJob,
  DIGEST_JOB_ACTIVE, CONFIRM_THRESHOLD,
  type DigestEstimate, type DigestJob, type VolumeRecap, type VolumeRecapPending,
} from '@/lib/ai'

const POLL_MS = 2000

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
  const { t } = useLingui()
  const [volumes, setVolumes] = useState<VolumeRecap[] | null>(null)
  const [canWrite, setCanWrite] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const reload = useCallback(() => {
    getRecapVolumes(bookId)
      .then(r => { setVolumes(r.volumes); setCanWrite(r.can_write); setLoadError(null) })
      .catch(e => setLoadError(e instanceof Error ? e.message : t`Could not load the earlier volumes`))
  }, [bookId, t])

  useEffect(() => { reload() }, [reload])

  const replace = useCallback((v: VolumeRecap) => {
    setVolumes(prev => prev?.map(x => (x.book_id === v.book_id ? v : x)) ?? prev)
  }, [])

  return (
    <div className="rounded-lg border border-border bg-muted/40">
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
  renderParagraph: (text: string) => React.ReactNode
  onChange: (v: VolumeRecap) => void
  onReload: () => void
  onDigestEnded: () => void
  onTextShown: () => void
}

function VolumeRow({ bookId, vol, canWrite, renderParagraph, onChange, onReload, onDigestEnded, onTextShown }: VolumeRowProps) {
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
    const timer = window.setInterval(() => {
      getDigestJob(jobId)
        .then(j => {
          if (!alive.current) return
          if (isActive(j)) setJob(j)
          else ended(j)
        })
        .catch(() => {
          if (!alive.current) return
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
        className="w-full text-left px-3 py-2 hover:bg-muted/60 transition-colors"
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
        <div className="px-3 pb-3 -mt-0.5 space-y-3">
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
              <button type="button" onClick={() => void writeIt()} className={buttonClassName}>
                <Trans>Write it</Trans>
              </button>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 pt-1 border-t border-border/60">
                <p className="text-xs text-muted-foreground/70 pt-2">
                  <Trans>Written for the whole volume · cached, free to reopen</Trans>
                </p>
                {vol.status === 'partial' ? (
                  <button type="button" onClick={() => void writeIt({ force: true })} className={cn(linkButtonClassName, 'pt-2')}>
                    <RotateCcw className="w-3 h-3" />
                    <Trans>Write again</Trans>
                  </button>
                ) : (
                  <button type="button" onClick={() => void writeIt({ again: true })} className={cn(linkButtonClassName, 'pt-2')}>
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
