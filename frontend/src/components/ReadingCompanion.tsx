// Reading companion on the book page: "Recap" and "Glossary", two collapsible
// sections in the Highlights & Notes style. Everything is cut at the reader's
// position (the server only reads the chapters before it), so nothing here can
// spoil what comes next. Hidden entirely unless the companion endpoint says
// it is available: AI on, a key, an EPUB with a chapter map, and a position.
//
// Nothing here queues work on its own. Chapters are read only after a click
// (Recap, "Read the missing chapters", Continue); over CONFIRM_THRESHOLD
// chapters the confirmation dialog asks first with an estimate. The page
// never shows money: prices live in that dialog and in Settings > AI.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Loader2, Pause, Play, RotateCcw, Search, X } from 'lucide-react'
import { Trans, useLingui } from '@lingui/react/macro'
import { AiBadge } from '@/components/AiBadge'
import { DigestConfirmDialog } from '@/components/DigestConfirmDialog'
import { cn } from '@/lib/utils'
import { getActiveLocale } from '@/lib/i18n'
import {
  useAiStatus, getCompanion, requestRecap, clearRecap, getGlossary, recallTerm,
  getPreviously, getDigestJob, cancelDigestJob, isRecapPending, getDigestEstimate, startDigest,
  DIGEST_JOB_ACTIVE, DIGEST_JOB_RESUMABLE, CONFIRM_THRESHOLD,
  type Companion, type CompanionScope, type DigestEstimate, type DigestJob, type DigestScope,
  type Glossary, type GlossaryEntry, type GlossaryPlace, type Previously, type Recap,
  type RecapPending, type RecallResult,
} from '@/lib/ai'

const POLL_MS = 2000

type Origin = 'recap' | 'glossary' | 'continue'

function isActive(job: DigestJob | null): job is DigestJob {
  return job != null && DIGEST_JOB_ACTIVE.includes(job.status)
}

function paragraphs(text: string): string[] {
  return text.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean)
}

// "Recap to here" was clicked and the recap waits on a digest job. The job id
// is kept in localStorage so the waiting state survives a reload. Set only once
// a job has actually started (never while the confirmation dialog is open), and
// acted on after a reload only for that same job; cleared when the recap is
// written or the job ends any other way.
const waitKey = (bookId: number) => `tome_recap_wait:${bookId}`

function setRecapWait(bookId: number, jobId: string | null) {
  try {
    if (jobId) localStorage.setItem(waitKey(bookId), jobId)
    else localStorage.removeItem(waitKey(bookId))
  } catch { /* storage blocked: the wait just does not survive a reload */ }
}

function recapWaitJob(bookId: number): string | null {
  try { return localStorage.getItem(waitKey(bookId)) } catch { return null }
}

/** "2 hours ago" in the active locale. */
function relativeTime(iso: string | null): string | null {
  if (!iso) return null
  const ms = new Date(iso).getTime()
  if (Number.isNaN(ms)) return null
  const secs = Math.round((ms - Date.now()) / 1000)
  const fmt = new Intl.RelativeTimeFormat(getActiveLocale(), { numeric: 'auto' })
  const abs = Math.abs(secs)
  if (abs < 60) return fmt.format(0, 'second')
  if (abs < 3600) return fmt.format(Math.round(secs / 60), 'minute')
  if (abs < 86400) return fmt.format(Math.round(secs / 3600), 'hour')
  if (abs < 86400 * 30) return fmt.format(Math.round(secs / 86400), 'day')
  if (abs < 86400 * 365) return fmt.format(Math.round(secs / (86400 * 30)), 'month')
  return fmt.format(Math.round(secs / (86400 * 365)), 'year')
}

/** People first (most-updated first, then first seen), then places, then
 *  terms, both by first seen. The API sends first-seen order. */
function orderEntries(entries: GlossaryEntry[]): GlossaryEntry[] {
  const indexed = entries.map((e, i) => ({ e, i }))
  const people = indexed
    .filter(x => x.e.kind === 'person')
    .sort((a, b) => (b.e.chapters ?? 1) - (a.e.chapters ?? 1) || a.i - b.i)
  const places = indexed.filter(x => x.e.kind === 'place')
  const terms = indexed.filter(x => x.e.kind !== 'person' && x.e.kind !== 'place')
  return [...people, ...places, ...terms].map(x => x.e)
}

export function ReadingCompanion({ bookId }: { bookId: number }) {
  const { t } = useLingui()
  const { available: aiAvailable } = useAiStatus()
  const enabled = aiAvailable('reading_digest') && (aiAvailable('recap') || aiAvailable('recall'))

  const [companion, setCompanion] = useState<Companion | null>(null)

  // Recap
  const [recapOpen, setRecapOpen] = useState(false)
  const [recap, setRecap] = useState<Recap | null>(null)
  const [previously, setPreviously] = useState<Previously | null>(null)
  const [recapState, setRecapState] = useState<'idle' | 'loading' | 'waiting' | 'writing'>('idle')
  const [recapError, setRecapError] = useState<string | null>(null)

  // Glossary
  const [glossaryOpen, setGlossaryOpen] = useState(false)
  const [glossary, setGlossary] = useState<Glossary | null>(null)
  const [glossaryLoading, setGlossaryLoading] = useState(false)
  const [glossaryError, setGlossaryError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [recall, setRecall] = useState<RecallResult | null>(null)
  const [recalling, setRecalling] = useState(false)

  // The digest job, polled every 2 s while active (queued, running, stopping).
  const [job, setJob] = useState<DigestJob | null>(null)
  // The last job that ended, to say how it went and to offer Continue.
  const [endedJob, setEndedJob] = useState<DigestJob | null>(null)
  // Starting a job: the confirmation dialog, and errors outside it.
  const [confirm, setConfirm] = useState<{
    book: DigestEstimate; series: DigestEstimate | null; origin: Origin; scope?: DigestScope
  } | null>(null)
  const [confirmBusy, setConfirmBusy] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [digestError, setDigestError] = useState<string | null>(null)

  const recapWaitingRef = useRef(false)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  // phase: 'loading' for a recap the companion says is cached, 'writing' when
  // the server may have to write one. Returns the 202 body when chapters are
  // missing (nothing is queued by the server; the caller decides).
  const loadRecap = useCallback(async (
    phase: 'loading' | 'writing', opts: { partial?: boolean } = {},
  ): Promise<RecapPending | null> => {
    setRecapError(null)
    setRecapState(phase)
    try {
      const res = await requestRecap(bookId, opts)
      if (!alive.current) return null
      if (isRecapPending(res)) return res
      recapWaitingRef.current = false
      setRecapWait(bookId, null)
      setRecap(res)
      setRecapState('idle')
    } catch (e) {
      if (!alive.current) return null
      recapWaitingRef.current = false
      setRecapWait(bookId, null)
      setRecapState('idle')
      setRecapError(e instanceof Error ? e.message : t`Could not write the recap`)
    }
    return null
  }, [bookId, t])

  // Read-only: the glossary GET never queues work.
  const loadGlossary = useCallback(async () => {
    setGlossaryError(null)
    setGlossaryLoading(true)
    try {
      const g = await getGlossary(bookId)
      if (!alive.current) return
      setGlossary(g)
      if (isActive(g.job)) setJob(prev => (isActive(prev) && prev.id === g.job!.id ? prev : g.job))
    } catch (e) {
      if (alive.current) setGlossaryError(e instanceof Error ? e.message : t`Could not load the glossary`)
    } finally {
      if (alive.current) setGlossaryLoading(false)
    }
  }, [bookId, t])

  // A job ended: finish the recap that waited on it, refresh the glossary.
  const afterJob = useCallback((j: DigestJob) => {
    setEndedJob(j)
    if (recapWaitingRef.current) {
      recapWaitingRef.current = false
      // partial: write from what exists even when some chapters could not be
      // read or were left out (a book-only job leaves earlier volumes out).
      if (j.status === 'done') void loadRecap('writing', { partial: true })
      else { setRecapWait(bookId, null); setRecapState('idle') }
    }
    // Refresh even while the section is closed (its count is in the heading).
    void loadGlossary()
  }, [bookId, loadRecap, loadGlossary])

  // Companion state. Never queues work; a cached recap is free to load.
  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    getCompanion(bookId)
      .then(c => {
        if (cancelled) return
        setCompanion(c)
        if (!c.available) return
        if (isActive(c.job)) setJob(c.job)
        else if (c.last_job) setEndedJob(c.last_job)
        const waitingFor = recapWaitJob(bookId)
        if (waitingFor) {
          // "Recap to here" started a job before a reload. Only that same job
          // counts; anything else is stale and dropped.
          if (c.features.recap && isActive(c.job) && c.job.id === waitingFor) {
            recapWaitingRef.current = true
            setRecapOpen(true)
            setRecapState('waiting')
            return
          }
          if (c.features.recap && c.last_job?.id === waitingFor
            && c.last_job.status === 'done' && !c.recap_cached) {
            setRecapOpen(true)
            void loadRecap('writing', { partial: true }).then(pending => {
              // Still chapters missing: do not start anything without a click.
              if (pending && alive.current) setRecapState('idle')
            })
            setRecapWait(bookId, null)
            return
          }
          setRecapWait(bookId, null)
        }
        if (c.features.recap && c.previously) {
          getPreviously(bookId)
            .then(p => { if (!cancelled) { setPreviously(p); setRecapOpen(true) } })
            .catch(() => {})
        } else if (c.features.recap && c.recap_cached) {
          setRecapOpen(true)
          void loadRecap('loading')
        }
      })
      .catch(() => { if (!cancelled) setCompanion(null) })
    return () => { cancelled = true }
  }, [bookId, enabled, loadRecap])

  // Poll the job; when it ends, pick up what it made.
  const jobId = isActive(job) ? job.id : null
  useEffect(() => {
    if (!jobId) return
    const timer = window.setInterval(() => {
      getDigestJob(jobId)
        .then(j => {
          if (!alive.current) return
          setJob(j)
          if (!isActive(j)) afterJob(j)
        })
        .catch(() => {
          if (!alive.current) return
          setJob(null)
          if (recapWaitingRef.current) {
            recapWaitingRef.current = false
            setRecapWait(bookId, null)
            setRecapState('idle')
          }
        })
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [jobId, afterJob, bookId])

  const ordered = useMemo(() => orderEntries(glossary?.entries ?? []), [glossary])
  const filtered = useMemo(() => {
    const q = query.trim().toLocaleLowerCase()
    if (!q) return ordered
    return ordered.filter(e =>
      e.name.toLocaleLowerCase().includes(q) || e.line.toLocaleLowerCase().includes(q))
  }, [ordered, query])

  if (!enabled || !companion || !companion.available || !companion.position) return null

  const { position, features } = companion
  const pct = Math.round(position.pct * 100)
  const chapterLabel = (title: string | null, idx: number) => {
    const number = idx + 1
    return title || t`Chapter ${number}`
  }
  const upTo = chapterLabel(position.chapter_title, position.chapter_idx)
  const when = relativeTime(position.updated_at)
  const fromLine = position.source === 'device'
    ? (when ? t`from your device ${when}` : t`from your device`)
    : (when ? t`from your web reader ${when}` : t`from your web reader`)

  // ── starting work ──────────────────────────────────────────────────────────

  function waitOn(j: DigestJob) {
    recapWaitingRef.current = true
    setRecapWait(bookId, j.id)
    setEndedJob(null)
    setJob(j)
    setRecapState('waiting')
  }

  /** POST digest. Returns an error message, or null when it started (or
   *  nothing was missing). */
  async function begin(scope: DigestScope, origin: Origin): Promise<string | null> {
    setDigestError(null)
    try {
      const res = await startDigest(bookId, scope)
      if (!alive.current) return null
      if (isActive(res.job)) {
        if (origin === 'recap') waitOn(res.job)
        else { setEndedJob(null); setJob(res.job) }
      } else {
        // Nothing left to read in that scope.
        setEndedJob(null)
        if (origin === 'recap') void loadRecap('writing', { partial: true })
        else void loadGlossary()
      }
      return null
    } catch (e) {
      return e instanceof Error ? e.message : t`Could not start reading the chapters`
    }
  }

  function failStart(origin: Origin, message: string) {
    if (origin === 'recap') {
      recapWaitingRef.current = false
      setRecapWait(bookId, null)
      setRecapState('idle')
      setRecapError(message)
    } else {
      setDigestError(message)
    }
  }

  /** Start at once when the work is small, else ask with the estimate.
   *  `scope` picks the scope to start or preselect (Continue keeps the scope
   *  of the job it continues); without it the widest scope is used. */
  function decide(
    est: { book: DigestEstimate; series: DigestEstimate | null }, origin: Origin, scope?: DigestScope,
  ) {
    const chosen = scope === 'book' ? est.book : est.series ?? est.book
    if (chosen.chapters > 0 && chosen.chapters <= CONFIRM_THRESHOLD && !chosen.over_budget) {
      void begin(chosen.scope, origin).then(err => { if (err && alive.current) failStart(origin, err) })
      return
    }
    if (origin === 'recap') setRecapState('idle')
    setConfirmError(null)
    setConfirm({ book: est.book, series: est.series, origin, scope: chosen.scope })
  }

  async function confirmStart(scope: DigestScope) {
    if (!confirm) return
    setConfirmBusy(true)
    setConfirmError(null)
    const err = await begin(scope, confirm.origin)
    if (!alive.current) return
    setConfirmBusy(false)
    if (err) { setConfirmError(err); return }
    setConfirm(null)
  }

  function cancelConfirm() {
    if (confirm?.origin === 'recap') {
      recapWaitingRef.current = false
      setRecapWait(bookId, null)
      setRecapState('idle')
    }
    setConfirm(null)
  }

  async function startRecap() {
    setRecapOpen(true)
    setPreviously(null)
    const pending = await loadRecap('writing')
    if (!pending || !alive.current) return
    if (isActive(pending.job)) { waitOn(pending.job); return }
    decide(pending.estimate, 'recap')
  }

  async function readMissing() {
    setDigestError(null)
    setStarting(true)
    try {
      const est = await getDigestEstimate(bookId)
      if (alive.current) decide(est, 'glossary')
    } catch (e) {
      if (alive.current) setDigestError(e instanceof Error ? e.message : t`Could not start reading the chapters`)
    } finally {
      if (alive.current) setStarting(false)
    }
  }

  /** Same path as a fresh start: the estimate first, and the dialog when the
   *  rest of the job is big or over budget. */
  async function continueJob() {
    if (!endedJob) return
    const scope = endedJob.scope ?? 'book'
    setDigestError(null)
    setStarting(true)
    try {
      const est = await getDigestEstimate(bookId)
      if (alive.current) decide(est, 'continue', scope)
    } catch (e) {
      if (alive.current) setDigestError(e instanceof Error ? e.message : t`Could not start reading the chapters`)
    } finally {
      if (alive.current) setStarting(false)
    }
  }

  async function stopJob() {
    if (!job) return
    try {
      const j = await cancelDigestJob(job.id)
      if (!alive.current) return
      setJob(j)
      if (!isActive(j)) afterJob(j)
    } catch { /* the poll picks up the final state */ }
  }

  async function askAgain() {
    setRecapError(null)
    setRecapState('writing')
    try {
      await clearRecap(bookId)
    } catch { /* nothing cached is fine */ }
    setRecap(null)
    // Not partial: "Ask again" retries the chapters that could not be read
    // (clearing the recap clears their placeholders on the server).
    const pending = await loadRecap('writing')
    if (!pending || !alive.current) return
    if (isActive(pending.job)) { waitOn(pending.job); return }
    decide(pending.estimate, 'recap')
  }

  function toggleGlossary() {
    const next = !glossaryOpen
    setGlossaryOpen(next)
    if (next && !glossary && !glossaryLoading) void loadGlossary()
  }

  function toggleEntry(key: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  async function runRecall() {
    const term = query.trim()
    // One live call per term: a second Enter while looking, or after the
    // answer is shown, sends nothing (recall resets when the query changes).
    if (!term || recalling || recall != null) return
    setGlossaryError(null)
    setRecalling(true)
    setRecall(null)
    try {
      const r = await recallTerm(bookId, term)
      if (alive.current) setRecall(r)
    } catch (e) {
      if (alive.current) setGlossaryError(e instanceof Error ? e.message : t`Could not look that up`)
    } finally {
      if (alive.current) setRecalling(false)
    }
  }

  const kindLabel = (kind: GlossaryEntry['kind']) =>
    kind === 'person' ? t`Person` : kind === 'place' ? t`Place` : t`Term`

  const placeLabel = (p: GlossaryPlace) => {
    const chapter = chapterLabel(p.chapter_title, p.chapter_idx)
    const volume = p.book_title
    return volume ? t`${volume}, ${chapter}` : chapter
  }

  function scopeLines(scope: CompanionScope | null): string[] {
    if (!scope) return []
    return scope.excluded_volumes.map(v => {
      const idx = v.series_index
      const name = idx != null ? t`Volume ${idx}` : v.title
      if (v.reason === 'not_read') return t`${name} is not marked read, so it is left out.`
      if (v.reason === 'no_chapters') return t`${name} has no chapter map yet, so it is left out. The word-count job in Admin adds one.`
      return t`${name} is not an EPUB, so it is left out.`
    })
  }

  const coverage = glossary?.coverage ?? companion.coverage
  const incomplete = coverage != null && coverage.digested < coverage.needed
  const resumable = !isActive(job) && endedJob != null && incomplete
    && DIGEST_JOB_RESUMABLE.includes(endedJob.status)

  // ── the one progress line, under the heading ──────────────────────────────

  const buttonClassName = 'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted transition-colors disabled:opacity-60 shrink-0'

  let progressLine: React.ReactNode = null
  if (isActive(job)) {
    const done = job.done
    const total = job.total
    const text = job.status === 'stopping'
      ? t`Stopping after the current chapters…`
      : job.status === 'queued' || total === 0
        ? t`Getting ready to read the chapters…`
        : t`Reading chapters: ${done} of ${total}…`
    progressLine = (
      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground" role="status">
        <span className="flex items-center gap-2 min-w-0">
          <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
          {text}
        </span>
        {job.status !== 'stopping' && (
          <button type="button" onClick={stopJob} className="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline">
            <Trans>Stop</Trans>
          </button>
        )}
      </div>
    )
  } else if (resumable && endedJob) {
    const done = endedJob.done
    const total = endedJob.total
    const text = endedJob.status === 'stopped' && endedJob.stop_reason
      ? endedJob.stop_reason
      : endedJob.status === 'interrupted'
        ? t`Interrupted by a server restart after ${done} of ${total} chapters.`
        : t`Stopped after ${done} of ${total} chapters.`
    progressLine = (
      <div className="mt-3 space-y-1">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <p className="flex items-center gap-2 text-sm text-muted-foreground min-w-0">
            <Pause className="w-3.5 h-3.5 shrink-0" />
            <span className="min-w-0">{text}</span>
          </p>
          <button type="button" onClick={continueJob} disabled={starting} className={buttonClassName}>
            {starting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
            <Trans>Continue</Trans>
          </button>
        </div>
        {digestError && <p className="text-xs text-destructive">{digestError}</p>}
      </div>
    )
  }

  const endedNote = endedJob && endedJob.status === 'error' && endedJob.stop_reason
    ? <p className="text-xs text-muted-foreground">{endedJob.stop_reason}</p>
    : null

  // ── Recap section ──────────────────────────────────────────────────────────

  const previouslyTitle = previously?.title ?? ''
  const recapFor = recap ? chapterLabel(recap.chapter_title, recap.chapter_idx) : ''
  const showPreviously = previously != null && recap == null && recapState === 'idle'
  // Nothing written yet: the section is one line and the button, open or not.
  const recapTeaser = recap == null && !showPreviously && recapState === 'idle'

  const recapButton = (
    <button
      type="button"
      onClick={() => void startRecap()}
      title={t`Sends the summaries of the chapters before your position and the current chapter up to it to Anthropic`}
      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted hover:-translate-y-0.5 transition-all duration-200 shrink-0"
    >
      <Trans>Recap to here</Trans>
    </button>
  )

  const recapBlock = features.recap ? (
    <div className="mt-4">
      <button
        type="button"
        onClick={() => setRecapOpen(o => !o)}
        aria-expanded={recapOpen}
        className="flex items-center gap-1.5 font-display text-base text-foreground mb-3 hover:text-primary transition-colors"
      >
        <Trans>Recap</Trans>
        <ChevronDown className={cn('w-3.5 h-3.5 transition-transform', !recapOpen && '-rotate-90')} />
      </button>
      {recapTeaser ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <p className="text-sm text-muted-foreground min-w-0">
              <Trans>The story so far, from the pages before your position. Nothing after it is read.</Trans>
            </p>
            {recapButton}
          </div>
          {recapError && <p className="text-xs text-destructive">{recapError}</p>}
        </div>
      ) : recapOpen ? (
        <div className="rounded-lg border border-border bg-muted/40 px-4 py-3 space-y-3">
          {recapState === 'loading' && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              <Trans>Loading the recap…</Trans>
            </p>
          )}
          {recapState === 'waiting' && (
            <>
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <Trans>Waiting for the chapters to be read. The recap is written right after.</Trans>
              </p>
              <p className="text-xs text-muted-foreground/70">
                <Trans>Each chapter is read once. Later recaps and the glossary reuse it.</Trans>
              </p>
            </>
          )}
          {recapState === 'writing' && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              <Trans>Writing the recap. This can take up to a minute.</Trans>
            </p>
          )}
          {recapState === 'idle' && showPreviously && previously && (
            <>
              <p className="text-sm font-medium text-foreground">
                <Trans>Previously on {previouslyTitle}</Trans>
              </p>
              {paragraphs(previously.text).map((p, i) => (
                <p key={i} className="text-sm text-foreground leading-relaxed">{p}</p>
              ))}
              {previously.partial && (
                <p className="text-xs text-muted-foreground/70">
                  <Trans>Some chapters of that volume could not be read, so this may skip parts.</Trans>
                </p>
              )}
            </>
          )}
          {recapState === 'idle' && recap && (
            <>
              {paragraphs(recap.text).map((p, i) => (
                <p key={i} className="text-sm text-foreground leading-relaxed">{p}</p>
              ))}
              {recap.partial && (
                <p className="text-xs text-muted-foreground/70">
                  <Trans>Some chapters could not be read, so this recap may skip parts.</Trans>
                </p>
              )}
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 pt-1 border-t border-border/60">
                <p className="text-xs text-muted-foreground/70 pt-2">
                  {recap.whole_book
                    ? <Trans>Written for the whole book · cached, free to reopen</Trans>
                    : <Trans>Written for {recapFor} · cached, free to reopen</Trans>}
                </p>
                <button
                  type="button"
                  onClick={() => void askAgain()}
                  className="flex items-center gap-1 pt-2 text-xs font-medium text-primary hover:underline"
                >
                  <RotateCcw className="w-3 h-3" />
                  <Trans>Ask again</Trans>
                </button>
              </div>
            </>
          )}
          {recapState === 'idle' && recapError && <p className="text-xs text-destructive">{recapError}</p>}
        </div>
      ) : null}
    </div>
  ) : null

  // ── Glossary section ───────────────────────────────────────────────────────

  const glossaryCount = glossary?.count ?? companion.glossary_count
  const digested = coverage?.digested ?? 0
  const needed = coverage?.needed ?? 0
  const lastFailed = endedJob?.failed ?? 0
  const scopeNotes = scopeLines(glossary?.scope ?? companion.scope)
  const term = query.trim()
  // Kind headings only on the unfiltered list.
  const grouped = !term

  const entryRow = (e: GlossaryEntry, showKind: boolean) => {
    const key = `${e.kind}:${e.name}`
    const open = expanded.has(key)
    const firstSeen = placeLabel(e.first_seen)
    return (
      <li key={key}>
        <button
          type="button"
          onClick={() => toggleEntry(key)}
          aria-expanded={open}
          className="w-full text-left px-3 py-2 hover:bg-muted/60 transition-colors"
        >
          <span className="flex items-baseline gap-2 min-w-0">
            <span className="text-sm font-medium text-foreground min-w-0 break-words">{e.name}</span>
            {showKind && (
              <span className="rounded-full border border-border px-1.5 py-px text-[10px] uppercase tracking-wide text-muted-foreground shrink-0">
                {kindLabel(e.kind)}
              </span>
            )}
            <ChevronDown className={cn('ml-auto w-3 h-3 shrink-0 self-center text-muted-foreground/50 transition-transform', !open && '-rotate-90')} />
          </span>
          <span className="block text-sm text-muted-foreground leading-snug mt-0.5">{e.line}</span>
        </button>
        {open && (
          <div className="px-3 pb-2.5 -mt-0.5 space-y-1.5">
            {e.more && <p className="text-sm text-foreground leading-relaxed">{e.more}</p>}
            {e.evidence && (
              <p className="text-xs text-muted-foreground italic border-l-2 border-primary/40 pl-2.5">
                {e.evidence}
              </p>
            )}
            <p className="text-xs text-muted-foreground/70">
              <Trans>First seen: {firstSeen}</Trans>
            </p>
          </div>
        )}
      </li>
    )
  }

  const groups: { kind: GlossaryEntry['kind']; label: string; entries: GlossaryEntry[] }[] = grouped
    ? [
        { kind: 'person' as const, label: t`People`, entries: filtered.filter(e => e.kind === 'person') },
        { kind: 'place' as const, label: t`Places`, entries: filtered.filter(e => e.kind === 'place') },
        { kind: 'term' as const, label: t`Terms`, entries: filtered.filter(e => e.kind !== 'person' && e.kind !== 'place') },
      ].filter(g => g.entries.length > 0)
    : []

  const glossaryBlock = features.glossary ? (
    <div className="mt-4">
      <button
        type="button"
        onClick={toggleGlossary}
        aria-expanded={glossaryOpen}
        className="flex items-center gap-1.5 font-display text-base text-foreground mb-3 hover:text-primary transition-colors"
      >
        <Trans>Glossary</Trans>
        <span className="text-muted-foreground/50 font-normal text-sm">({glossaryCount})</span>
        <ChevronDown className={cn('w-3.5 h-3.5 transition-transform', !glossaryOpen && '-rotate-90')} />
      </button>
      {glossaryOpen && (
        <div className="space-y-3">
          {glossary == null && glossaryLoading && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              <Trans>Loading the glossary…</Trans>
            </p>
          )}
          {glossary != null && (glossary.entries.length > 0 || features.recall) && (
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground/60" />
              <input
                type="text"
                value={query}
                onChange={e => { setQuery(e.target.value); setRecall(null) }}
                onKeyDown={e => { if (e.key === 'Enter' && filtered.length === 0 && features.recall && !recalling) void runRecall() }}
                placeholder={t`Search names, places, terms`}
                aria-label={t`Search the glossary`}
                className="w-full rounded-lg border border-border bg-background pl-8 pr-8 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => { setQuery(''); setRecall(null) }}
                  aria-label={t`Clear search`}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 text-muted-foreground/60 hover:text-foreground"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          )}
          {glossary != null && glossary.entries.length === 0 && !incomplete && !term && (
            <p className="text-sm text-muted-foreground">
              <Trans>Nobody and nothing worth noting yet. Names show up here once the chapters before your position mention them.</Trans>
            </p>
          )}
          {glossary != null && filtered.length > 0 && (
            <div className="max-h-[28rem] overflow-y-auto rounded-lg border border-border bg-muted/40">
              {grouped ? groups.map(g => (
                <section key={g.kind} aria-label={g.label}>
                  <h4 className="sticky top-0 z-[1] px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground bg-muted border-b border-border/60">
                    {g.label} <span className="font-normal text-muted-foreground/60">{g.entries.length}</span>
                  </h4>
                  <ul className="divide-y divide-border/60">
                    {g.entries.map(e => entryRow(e, false))}
                  </ul>
                </section>
              )) : (
                <ul className="divide-y divide-border/60">
                  {filtered.map(e => entryRow(e, true))}
                </ul>
              )}
            </div>
          )}
          {glossary != null && term && filtered.length === 0 && (
            <div className="rounded-lg border border-border bg-muted/40 px-3 py-2.5 space-y-2">
              {recall == null ? (
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <p className="text-sm text-muted-foreground min-w-0 break-words">
                    <Trans>No glossary entry for "{term}".</Trans>
                  </p>
                  {features.recall && (
                    <button
                      type="button"
                      onClick={runRecall}
                      disabled={recalling}
                      title={t`Checks the glossary and the current chapter up to your position; sends that chapter to Anthropic when needed`}
                      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted transition-colors disabled:opacity-60 min-w-0 max-w-full"
                    >
                      {recalling && <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />}
                      <span className="truncate">
                        {recalling ? <Trans>Looking…</Trans> : <Trans>Recall "{term}"</Trans>}
                      </span>
                    </button>
                  )}
                </div>
              ) : recall.found && recall.entry ? (
                <div className="space-y-1">
                  <p className="flex items-baseline gap-2 min-w-0">
                    <span className="text-sm font-medium text-foreground min-w-0 break-words">{recall.entry.name}</span>
                    <span className="rounded-full border border-border px-1.5 py-px text-[10px] uppercase tracking-wide text-muted-foreground">
                      {kindLabel(recall.entry.kind)}
                    </span>
                  </p>
                  <p className="text-sm text-muted-foreground leading-snug">{recall.entry.line}</p>
                  {recall.entry.more && <p className="text-sm text-foreground leading-relaxed">{recall.entry.more}</p>}
                  {recall.entry.evidence && (
                    <p className="text-xs text-muted-foreground italic border-l-2 border-primary/40 pl-2.5">{recall.entry.evidence}</p>
                  )}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground"><Trans>Nothing about that before this page.</Trans></p>
              )}
            </div>
          )}
          {glossaryError && <p className="text-xs text-destructive">{glossaryError}</p>}
          {incomplete && coverage && (
            <div className="space-y-1.5">
              {isActive(job) ? (
                <p className="text-xs text-muted-foreground/70"><Trans>The glossary fills in as each chapter is read.</Trans></p>
              ) : (
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <p className="text-xs text-muted-foreground min-w-0">
                    <Trans>{digested} of {needed} chapters before your position are read so far.</Trans>
                  </p>
                  {!resumable && (
                    <button type="button" onClick={() => void readMissing()} disabled={starting} className={buttonClassName}>
                      {starting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                      <Trans>Read the missing chapters</Trans>
                    </button>
                  )}
                </div>
              )}
              {!isActive(job) && lastFailed > 0 && (
                <p className="text-xs text-muted-foreground/70"><Trans>Some chapters could not be read, so names from them are missing.</Trans></p>
              )}
              {!isActive(job) && !resumable && digestError && <p className="text-xs text-destructive">{digestError}</p>}
              {!isActive(job) && endedNote}
            </div>
          )}
          {scopeNotes.length > 0 && (
            <div className="space-y-0.5">
              {scopeNotes.map(line => <p key={line} className="text-xs text-muted-foreground/70">{line}</p>)}
            </div>
          )}
        </div>
      )}
    </div>
  ) : null

  return (
    <section className="mt-6" aria-label={t`Reading companion`}>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span className="min-w-0">
          <Trans>Up to {upTo} · {pct}% · {fromLine}</Trans>
        </span>
        <AiBadge />
      </p>
      <p className="mt-1 text-xs text-muted-foreground/70">
        <Trans>Only the pages before your position are sent. For well-known books the model may still know more than it is given.</Trans>
      </p>
      {progressLine}
      {recapBlock}
      {glossaryBlock}
      <DigestConfirmDialog
        open={confirm != null}
        book={confirm?.book ?? null}
        series={confirm?.series ?? null}
        scope={confirm?.scope}
        busy={confirmBusy}
        error={confirmError}
        onCancel={cancelConfirm}
        onStart={scope => void confirmStart(scope)}
      />
    </section>
  )
}

export default ReadingCompanion
