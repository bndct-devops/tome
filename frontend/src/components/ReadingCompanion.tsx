// Reading companion on the book page: "Recap" and "Glossary", two collapsible
// sections in the Highlights & Notes style. Everything is cut at the reader's
// position (the server only reads the chapters before it), so nothing here can
// spoil what comes next. Hidden entirely unless the companion endpoint says
// it is available: AI on, a key, an EPUB with a chapter map, and a position.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Loader2, RotateCcw, Search, X } from 'lucide-react'
import { Trans, useLingui } from '@lingui/react/macro'
import { AiBadge } from '@/components/AiBadge'
import { cn } from '@/lib/utils'
import { getActiveLocale } from '@/lib/i18n'
import {
  useAiStatus, getCompanion, requestRecap, clearRecap, getGlossary, recallTerm,
  getPreviously, getDigestJob, cancelDigestJob, isRecapPending, DIGEST_JOB_ACTIVE,
  type Companion, type CompanionScope, type DigestJob, type Glossary, type GlossaryEntry,
  type GlossaryPlace, type Previously, type Recap, type RecallResult,
} from '@/lib/ai'

const POLL_MS = 2000

function isActive(job: DigestJob | null): job is DigestJob {
  return job != null && DIGEST_JOB_ACTIVE.includes(job.status)
}

function paragraphs(text: string): string[] {
  return text.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean)
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

  // The digest job either section is waiting on, polled every 2 s while active.
  const [job, setJob] = useState<DigestJob | null>(null)
  // The last job that ended, kept to say how it went (failures, a stop reason).
  const [endedJob, setEndedJob] = useState<DigestJob | null>(null)
  const recapWaitingRef = useRef(false)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  // phase: 'loading' for a recap the companion says is cached, 'writing' when
  // the server may have to write one (a click, or after the digest job).
  const loadRecap = useCallback(async (phase: 'loading' | 'writing', opts: { partial?: boolean } = {}) => {
    setRecapError(null)
    setRecapState(phase)
    try {
      const res = await requestRecap(bookId, opts)
      if (!alive.current) return
      if (isRecapPending(res)) {
        recapWaitingRef.current = true
        setJob(res.job)
        setRecapState('waiting')
      } else {
        recapWaitingRef.current = false
        setRecap(res)
        setRecapState('idle')
      }
    } catch (e) {
      if (!alive.current) return
      recapWaitingRef.current = false
      setRecapState('idle')
      setRecapError(e instanceof Error ? e.message : t`Could not write the recap`)
    }
  }, [bookId, t])

  const loadGlossary = useCallback(async (queue: boolean) => {
    setGlossaryError(null)
    setGlossaryLoading(true)
    try {
      const g = await getGlossary(bookId, { queue })
      if (!alive.current) return
      setGlossary(g)
      if (isActive(g.job)) setJob(prev => (isActive(prev) && prev.id === g.job!.id ? prev : g.job))
    } catch (e) {
      if (alive.current) setGlossaryError(e instanceof Error ? e.message : t`Could not load the glossary`)
    } finally {
      if (alive.current) setGlossaryLoading(false)
    }
  }, [bookId, t])

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
          if (DIGEST_JOB_ACTIVE.includes(j.status)) return
          setEndedJob(j)
          if (recapWaitingRef.current) {
            recapWaitingRef.current = false
            if (j.status === 'done') void loadRecap('writing', { partial: j.failed > 0 })
            else setRecapState('idle')
          }
          // Refresh the glossary (and its count in the heading) even while the
          // section is closed. queue=false only reads: it never queues work.
          void loadGlossary(false)
        })
        .catch(() => {
          if (!alive.current) return
          setJob(null)
          if (recapWaitingRef.current) { recapWaitingRef.current = false; setRecapState('idle') }
        })
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [jobId, loadRecap, loadGlossary])

  const filtered = useMemo(() => {
    const entries = glossary?.entries ?? []
    const q = query.trim().toLocaleLowerCase()
    if (!q) return entries
    return entries.filter(e =>
      e.name.toLocaleLowerCase().includes(q) || e.line.toLocaleLowerCase().includes(q))
  }, [glossary, query])

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

  const jobLine = (j: DigestJob) => {
    if (j.status === 'queued' || j.total === 0) return t`Getting ready to read the chapters…`
    const current = Math.min(j.done + 1, j.total)
    const total = j.total
    return t`Reading chapter ${current} of ${total}…`
  }

  async function stopJob() {
    if (!job) return
    try {
      const j = await cancelDigestJob(job.id)
      if (alive.current) setJob(j)
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
    await loadRecap('writing')
  }

  function startRecap() {
    setRecapOpen(true)
    setPreviously(null)
    void loadRecap('writing')
  }

  function toggleGlossary() {
    const next = !glossaryOpen
    setGlossaryOpen(next)
    if (next && !glossary && !glossaryLoading) void loadGlossary(true)
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

  // ── Recap section ──────────────────────────────────────────────────────────

  const progress = isActive(job) ? (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
      <span className="flex items-center gap-2">
        <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
        {jobLine(job)}
      </span>
      <button type="button" onClick={stopJob} className="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline">
        <Trans>Stop</Trans>
      </button>
    </div>
  ) : null

  const endedNote = endedJob && endedJob.status !== 'done' && endedJob.status !== 'cancelled' && endedJob.stop_reason
    ? <p className="text-xs text-muted-foreground">{endedJob.stop_reason}</p>
    : null

  const previouslyTitle = previously?.title ?? ''
  const recapFor = recap ? chapterLabel(recap.chapter_title, recap.chapter_idx) : ''
  const showPreviously = previously != null && recap == null && recapState === 'idle'
  // Nothing written yet: the section is one line and the button, open or not.
  const recapTeaser = recap == null && !showPreviously && recapState === 'idle'

  const recapButton = (
    <button
      type="button"
      onClick={startRecap}
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
          {endedNote}
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
              {progress ?? (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <Trans>Getting ready to read the chapters…</Trans>
                </p>
              )}
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
                  onClick={askAgain}
                  className="flex items-center gap-1 pt-2 text-xs font-medium text-primary hover:underline"
                >
                  <RotateCcw className="w-3 h-3" />
                  <Trans>Ask again</Trans>
                </button>
              </div>
            </>
          )}
          {recapState === 'idle' && recapError && <p className="text-xs text-destructive">{recapError}</p>}
          {recapState === 'idle' && endedNote}
        </div>
      ) : null}
    </div>
  ) : null

  // ── Glossary section ───────────────────────────────────────────────────────

  const glossaryCount = glossary?.count ?? companion.glossary_count
  const coverage = glossary?.coverage ?? companion.coverage
  const incomplete = coverage != null && coverage.digested < coverage.needed
  const digested = coverage?.digested ?? 0
  const needed = coverage?.needed ?? 0
  const lastFailed = endedJob?.failed ?? 0
  const scopeNotes = scopeLines(glossary?.scope ?? companion.scope)
  const term = query.trim()

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
            <ul className="max-h-[28rem] overflow-y-auto pr-1 divide-y divide-border/60 rounded-lg border border-border bg-muted/40">
              {filtered.map(e => {
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
                        <span className="rounded-full border border-border px-1.5 py-px text-[10px] uppercase tracking-wide text-muted-foreground shrink-0">
                          {kindLabel(e.kind)}
                        </span>
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
              })}
            </ul>
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
            <div className="space-y-1">
              {progress ?? (
                <p className="text-xs text-muted-foreground">
                  <Trans>{digested} of {needed} chapters before your position are read so far.</Trans>
                </p>
              )}
              {isActive(job)
                ? <p className="text-xs text-muted-foreground/70"><Trans>The glossary fills in as each chapter is read.</Trans></p>
                : lastFailed > 0
                  ? <p className="text-xs text-muted-foreground/70"><Trans>Some chapters could not be read, so names from them are missing.</Trans></p>
                  : null}
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
      {recapBlock}
      {glossaryBlock}
    </section>
  )
}

export default ReadingCompanion
