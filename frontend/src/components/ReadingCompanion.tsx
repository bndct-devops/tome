// Reading companion on the book page: "Recap" and "Glossary", two collapsible
// sections in the Highlights & Notes style. Everything is cut at the reader's
// position (the server only reads the chapters before it), so nothing here can
// spoil what comes next. Hidden unless the companion endpoint says it is
// available (AI on, a key, an EPUB with a chapter map, and a position), or
// says `series_recaps` without a position: then only the Recap list of the
// earlier volumes shows.
//
// Nothing here queues work on its own. Chapters are read only after a click
// (Recap, "Read the missing chapters", Continue); over CONFIRM_THRESHOLD
// chapters the confirmation dialog asks first with an estimate. The page
// shows no prices beyond the Ask note (a fixed per-question range): the rest
// lives in that dialog and in Settings > AI.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Loader2, Pause, Play, RotateCcw, Search, X } from 'lucide-react'
import { Trans, useLingui } from '@lingui/react/macro'
import { plural } from '@lingui/core/macro'
import { AiBadge } from '@/components/AiBadge'
import { DigestConfirmDialog } from '@/components/DigestConfirmDialog'
import { RecapVolumes } from '@/components/RecapVolumes'
import { GlossaryEntryDetail } from '@/components/GlossaryEntry'
import { AskBox } from '@/components/AskBox'
import { cn } from '@/lib/utils'
import { getActiveLocale } from '@/lib/i18n'
import {
  useAiStatus, getCompanion, requestRecap, clearRecap, getGlossary, recallTerm,
  getDigestJob, cancelDigestJob, isRecapPending, getDigestEstimate, startDigest,
  DIGEST_JOB_ACTIVE, DIGEST_JOB_RESUMABLE, CONFIRM_THRESHOLD,
  type Companion, type CompanionScope, type DigestEstimate, type DigestJob, type DigestScope,
  type Glossary, type GlossaryEntry, type GlossaryPlace, type Recap,
  type RecapPending, type RecallResult, type SeriesPlace,
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

/** The names a query is matched against first, and the names recap text
 *  links to: the name plus its aliases. */
function entryNames(e: GlossaryEntry): string[] {
  return [e.name, ...(e.aliases ?? []).filter(a => a && a !== e.name)]
}

/** The row's second line: the stable identity when the server sends one,
 *  else today's line. */
function entryLine(e: GlossaryEntry): string {
  return e.identity || e.line
}

/** 2 when the query matches a name or alias, 1 when it only matches the
 *  row's line, 0 when it matches neither. `q` is trimmed and lower-cased. */
function matchRank(e: GlossaryEntry, q: string): number {
  if (entryNames(e).some(n => n.toLocaleLowerCase().includes(q))) return 2
  if (entryLine(e).toLocaleLowerCase().includes(q)) return 1
  return 0
}

/** A chapter title without a leading copy of its own number ("12. Sanguine
 *  Horror" under "ch 12" reads as "Sanguine Horror"). */
function bareChapterTitle(title: string | null, number: number): string | null {
  if (!title) return null
  const m = title.match(/^\s*(\d+)\s*[.:)-]?\s+(.+)$/)
  if (m && Number(m[1]) === number) return m[2].trim() || null
  return title.trim() || null
}

/** Glossary names by first character, longest first, for linkNames. */
type NameIndex = Map<string, { name: string; key: string }[]>

const WORD_CHAR = /[\p{L}\p{N}_]/u

function buildNameIndex(entries: GlossaryEntry[]): NameIndex {
  const seen = new Set<string>()
  const all: { name: string; key: string }[] = []
  for (const e of entries) {
    const key = `${e.kind}:${e.name}`
    for (const n of entryNames(e)) {
      const name = n.trim()
      if (name.length < 2 || seen.has(name)) continue
      seen.add(name)
      all.push({ name, key })
    }
  }
  all.sort((a, b) => b.name.length - a.name.length)
  const index: NameIndex = new Map()
  for (const item of all) {
    const list = index.get(item.name[0])
    if (list) list.push(item)
    else index.set(item.name[0], [item])
  }
  return index
}

/** One paragraph with every glossary name (as written, whole word, longest
 *  first) wrapped in a button that opens its entry. Pure: no HTML strings. */
function linkNames(
  text: string, index: NameIndex, title: string, onPick: (key: string, name: string) => void,
): React.ReactNode[] {
  if (index.size === 0) return [text]
  const out: React.ReactNode[] = []
  let last = 0
  let i = 0
  while (i < text.length) {
    const candidates = index.get(text[i])
    if (!candidates || (i > 0 && WORD_CHAR.test(text[i - 1]))) { i++; continue }
    const hit = candidates.find(c => {
      if (!text.startsWith(c.name, i)) return false
      const end = i + c.name.length
      return end >= text.length || !WORD_CHAR.test(text[end])
    })
    if (!hit) { i++; continue }
    if (i > last) out.push(text.slice(last, i))
    out.push(
      <button
        key={i}
        type="button"
        onClick={() => onPick(hit.key, hit.name)}
        title={title}
        className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-2 hover:text-primary hover:decoration-primary"
      >
        {hit.name}
      </button>,
    )
    i += hit.name.length
    last = i
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function ReadingCompanion({ bookId }: { bookId: number }) {
  const { t } = useLingui()
  const { available: aiAvailable } = useAiStatus()
  const enabled = aiAvailable('reading_digest') && (aiAvailable('recap') || aiAvailable('recall'))

  const [companion, setCompanion] = useState<Companion | null>(null)

  // Recap
  const [recapOpen, setRecapOpen] = useState(false)
  const [recap, setRecap] = useState<Recap | null>(null)
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
  const glossaryRef = useRef<HTMLDivElement>(null)
  const glossaryTried = useRef(false)
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
    glossaryTried.current = true
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
        // The volume list is compact (rows collapsed): open the section.
        if (c.series_recaps) setRecapOpen(true)
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
        if (c.features.recap && c.recap_cached) {
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
    // Name matches first, then entries that only mention it; each group keeps
    // the usual order.
    const ranked = ordered.map(e => ({ e, rank: matchRank(e, q) })).filter(x => x.rank > 0)
    return [...ranked.filter(x => x.rank === 2), ...ranked.filter(x => x.rank === 1)].map(x => x.e)
  }, [ordered, query])

  const nameIndex = useMemo(() => buildNameIndex(glossary?.entries ?? []), [glossary])

  const hasPosition = companion != null && companion.available && companion.position != null
  const glossaryOn = hasPosition && companion != null && companion.features.glossary

  // Recap text is on screen: load the glossary once (read-only) so names in
  // it can link to their entries.
  const ensureGlossary = useCallback(() => {
    if (!glossaryOn || glossaryTried.current) return
    void loadGlossary()
  }, [glossaryOn, loadGlossary])

  useEffect(() => {
    if (recap != null) ensureGlossary()
  }, [recap, ensureGlossary])

  // A volume's chapters were read: the glossary may have grown.
  const onVolumeDigestEnded = useCallback(() => {
    if (glossaryOn) void loadGlossary()
  }, [glossaryOn, loadGlossary])

  if (!enabled || !companion) return null
  const seriesOnly = !companion.available && companion.reason === 'no_position' && companion.series_recaps === true
  if (!hasPosition && !seriesOnly) return null

  const { position, features } = companion
  const chapterLabel = (title: string | null, idx: number) => {
    const number = idx + 1
    return title || t`Chapter ${number}`
  }
  let headingLine: string | null = null
  if (hasPosition && position) {
    const when = relativeTime(position.updated_at)
    const fromLine = position.source === 'device'
      ? (when ? t`from your device ${when}` : t`from your device`)
      : (when ? t`from your web reader ${when}` : t`from your web reader`)
    if (position.finished) {
      // Marked read: the window is the whole book, no "up to" chapter.
      headingLine = t`Finished · ${fromLine}`
    } else {
      const pct = Math.round(position.pct * 100)
      const upTo = chapterLabel(position.chapter_title, position.chapter_idx)
      headingLine = t`Up to ${upTo} · ${pct}% · ${fromLine}`
    }
  }

  const linkTitle = t`Show in the glossary`
  function openEntry(key: string, name: string) {
    setGlossaryOpen(true)
    setQuery(name)
    setRecall(null)
    setExpanded(prev => new Set(prev).add(key))
    window.requestAnimationFrame(() => {
      glossaryRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }
  const renderParagraph = (p: string) => linkNames(p, glossaryOn ? nameIndex : new Map(), linkTitle, openEntry)

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

  // "vol 1, ch 12 · Sanguine Horror"; without a volume number "ch 12 · ...",
  // with the book title in front when it is another book.
  const placeLabel = (p: GlossaryPlace) => {
    const ch = p.chapter_idx + 1
    const title = bareChapterTitle(p.chapter_title, ch)
    const vol = p.series_index
    if (vol != null) return title ? t`vol ${vol}, ch ${ch} · ${title}` : t`vol ${vol}, ch ${ch}`
    const book = p.book_id !== bookId ? p.book_title : null
    if (book) return title ? t`${book}, ch ${ch} · ${title}` : t`${book}, ch ${ch}`
    return title ? t`ch ${ch} · ${title}` : t`ch ${ch}`
  }

  // "vol 1, ch 12" (or "ch 12"): the muted place after a fact or timeline line.
  const shortPlace = (p: SeriesPlace) => {
    const ch = p.chapter_idx + 1
    const vol = p.series_index
    return vol != null ? t`vol ${vol}, ch ${ch}` : t`ch ${ch}`
  }

  // An Ask quote's place, with the chapter title when there is one.
  const quotePlace = (p: SeriesPlace) => {
    const ch = p.chapter_idx + 1
    const title = bareChapterTitle(p.chapter_title ?? null, ch)
    const short = shortPlace(p)
    return title ? t`${short} · ${title}` : short
  }

  const samePlace = (a: GlossaryPlace, b: GlossaryPlace) =>
    a.book_id === b.book_id && a.chapter_idx === b.chapter_idx

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

  const recapFor = recap ? chapterLabel(recap.chapter_title, recap.chapter_idx) : ''
  const missingVolumes = recap?.missing_volumes ?? []
  const writtenWithout = missingVolumes.length > 0
    ? new Intl.ListFormat(getActiveLocale(), { style: 'long', type: 'unit' }).format(
        missingVolumes.map(v => {
          const idx = v.series_index
          const name = idx != null ? t`vol ${idx}` : (v.title || t`another volume`)
          const missing = v.missing
          const counts = plural(v.needed, {
            one: `${missing} of # chapter unread`,
            other: `${missing} of # chapters unread`,
          })
          return t`${name} (${counts})`
        }))
    : null
  // Nothing written yet: the section is one line and the button, open or not.
  const recapTeaser = recap == null && recapState === 'idle'
  // With earlier volumes the section is a list and this book is its last row.
  const volumeList = companion.series_recaps === true

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

  const teaser = (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="text-sm text-muted-foreground min-w-0">
          <Trans>The story so far, from the pages before your position. Nothing after it is read.</Trans>
        </p>
        {recapButton}
      </div>
      {recapError && <p className="text-xs text-destructive">{recapError}</p>}
    </div>
  )

  // Today's live recap: loading, waiting, writing, the text and its footer.
  const recapPanel = (boxed: boolean) => (
    <div className={cn(boxed ? 'rounded-lg border border-border bg-muted/40 px-4 py-3 space-y-3' : 'space-y-3')}>
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
            {recapState === 'idle' && recap && (
              <>
                {paragraphs(recap.text).map((p, i) => (
                  <p key={i} className="text-sm text-foreground leading-relaxed">{renderParagraph(p)}</p>
                ))}
                {recap.partial && (
                  <p className="text-xs text-muted-foreground/70">
                    <Trans>Some chapters could not be read, so this recap may skip parts.</Trans>
                  </p>
                )}
                {writtenWithout && (
                  <p className="text-xs text-muted-foreground/70">
                    <Trans>Written without: {writtenWithout}.</Trans>
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
  )

  const currentRow = !hasPosition ? (
    <p className="text-sm text-muted-foreground">
      <Trans>Open the book to get a recap up to your page.</Trans>
    </p>
  ) : recapTeaser ? teaser : recapPanel(false)

  const recapHeading = (
    <button
      type="button"
      onClick={() => setRecapOpen(o => !o)}
      aria-expanded={recapOpen}
      className="flex items-center gap-1.5 font-display text-base text-foreground mb-3 hover:text-primary transition-colors"
    >
      <Trans>Recap</Trans>
      <ChevronDown className={cn('w-3.5 h-3.5 transition-transform', !recapOpen && '-rotate-90')} />
    </button>
  )

  const recapBlock = volumeList ? (
    <div className="mt-4">
      {recapHeading}
      {recapOpen && (
        <RecapVolumes
          bookId={bookId}
          renderParagraph={renderParagraph}
          current={currentRow}
          onDigestEnded={onVolumeDigestEnded}
          onTextShown={ensureGlossary}
        />
      )}
    </div>
  ) : features.recap ? (
    <div className="mt-4">
      {recapHeading}
      {recapTeaser ? teaser : recapOpen ? recapPanel(true) : null}
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
    const lastSeen = e.last_seen && !samePlace(e.first_seen, e.last_seen) ? placeLabel(e.last_seen) : null
    const aliases = (e.aliases ?? []).filter(a => a && a !== e.name)
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
            {aliases.length > 0 && (
              <span className="text-xs text-muted-foreground/70 min-w-0 truncate">{aliases.join(', ')}</span>
            )}
            {showKind && (
              <span className="rounded-full border border-border px-1.5 py-px text-[10px] uppercase tracking-wide text-muted-foreground shrink-0">
                {kindLabel(e.kind)}
              </span>
            )}
            <ChevronDown className={cn('ml-auto w-3 h-3 shrink-0 self-center text-muted-foreground/50 transition-transform', !open && '-rotate-90')} />
          </span>
          <span className="block text-sm text-muted-foreground leading-snug mt-0.5">{entryLine(e)}</span>
        </button>
        {open && (
          <GlossaryEntryDetail bookId={bookId} entry={e} firstSeen={firstSeen} lastSeen={lastSeen} shortPlace={shortPlace} />
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

  const glossaryBlock = glossaryOn ? (
    <div className="mt-4 scroll-mt-4" ref={glossaryRef}>
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
          {glossary != null && (features.ask ?? false) && (
            <AskBox bookId={bookId} placeLabel={quotePlace} costHintUsd={companion.ask_cost_hint_usd} />
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
        {headingLine && <span className="min-w-0">{headingLine}</span>}
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
