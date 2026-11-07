import { useEffect, useState } from 'react'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { AlertCircle, ArrowRight, Check, ChevronDown, ChevronUp, Loader2, Search } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ModalShell } from '@/components/ModalShell'
import { Checkbox, Pill } from '@/components/page'
import type { MetadataCandidate } from '@/lib/books'
import { CoverImage } from './CoverImage'
import { coverSrc } from '@/lib/covers'

const API = import.meta.env.VITE_API_URL ?? ''

function authHeader(): Record<string, string> {
  const token = localStorage.getItem('tome_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

interface BookResult {
  book_id: number
  book_title: string
  book_author: string | null
  book_cover_path: string | null
  book_description: string | null
  book_publisher: string | null
  book_year: number | null
  book_series: string | null
  book_series_index: number | null
  candidates: MetadataCandidate[]
  best_match_index: number | null
}

interface RowState {
  approved: boolean
  selectedIndex: number | null  // null = use best_match_index
  expanded: boolean
}

interface Props {
  bookIds: number[]
  open: boolean
  onClose: () => void
  onApplied: () => void
  onManualSearch: (bookId: number) => void
  onReviewUncertain?: (bookIds: number[]) => void
}

export function BulkMetadataReviewModal({ bookIds, open, onClose, onApplied, onManualSearch, onReviewUncertain }: Props) {
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [results, setResults] = useState<BookResult[]>([])
  const [rowState, setRowState] = useState<Record<number, RowState>>({})
  const [fillMissingOnly, setFillMissingOnly] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [doneState, setDoneState] = useState<{ applied: number; uncertain: number[] } | null>(null)

  useEffect(() => {
    if (open && bookIds.length > 0) {
      // Reset on open (not close) so content survives the exit animation.
      setProgress(null)
      setDoneState(null)
      fetchAll()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  async function fetchAll() {
    setLoading(true)
    setError(null)
    setResults([])
    setRowState({})
    try {
      const data: BookResult[] = await api.post('/books/bulk-fetch-candidates', { book_ids: bookIds })
      setResults(data)
      const initial: Record<number, RowState> = {}
      for (const r of data) {
        const hasConfidentMatch = r.best_match_index !== null
        initial[r.book_id] = {
          approved: hasConfidentMatch,
          // When no confident match but candidates exist, pre-select the first one (unapproved) so the user can review it
          selectedIndex: !hasConfidentMatch && r.candidates.length > 0 ? 0 : null,
          expanded: false,
        }
      }
      setRowState(initial)
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Fetch failed`)
    } finally {
      setLoading(false)
    }
  }

  function toggleApproved(bookId: number) {
    setRowState(prev => ({
      ...prev,
      [bookId]: { ...prev[bookId], approved: !prev[bookId].approved },
    }))
  }

  function toggleExpanded(bookId: number) {
    setRowState(prev => ({
      ...prev,
      [bookId]: { ...prev[bookId], expanded: !prev[bookId].expanded },
    }))
  }

  function selectCandidate(bookId: number, index: number) {
    setRowState(prev => ({
      ...prev,
      [bookId]: { ...prev[bookId], selectedIndex: index, approved: true, expanded: false },
    }))
  }

  function approvedCount() {
    return results.filter(r => rowState[r.book_id]?.approved).length
  }

  async function applyAll() {
    const toApply = results.filter(r => rowState[r.book_id]?.approved)
    if (!toApply.length) return
    setApplying(true)
    setProgress({ done: 0, total: toApply.length })
    let applied = 0

    for (const result of toApply) {
      const state = rowState[result.book_id]
      const idx = state.selectedIndex ?? result.best_match_index
      if (idx === null) continue
      const candidate = result.candidates[idx]
      if (!candidate) continue

      const body: Record<string, unknown> = {}

      // Title and author are always applied; candidate versions are cleaner than filenames
      if (candidate.title) body.title = candidate.title
      if (candidate.author) body.author = candidate.author

      if (fillMissingOnly) {
        if (!result.book_description && candidate.description) body.description = candidate.description
        if (!result.book_publisher && candidate.publisher) body.publisher = candidate.publisher
        if (!result.book_year && candidate.year) body.year = candidate.year
        if (!result.book_series && candidate.series) body.series = candidate.series
        if (result.book_series_index == null && candidate.series_index != null) body.series_index = candidate.series_index
        if (!result.book_cover_path && candidate.cover_url) body.cover_url = candidate.cover_url
        if (candidate.tags?.length) body.tags = candidate.tags
      } else {
        if (candidate.description) body.description = candidate.description
        if (candidate.publisher) body.publisher = candidate.publisher
        if (candidate.year) body.year = candidate.year
        if (candidate.series) body.series = candidate.series
        if (candidate.series_index != null) body.series_index = candidate.series_index
        if (candidate.cover_url) body.cover_url = candidate.cover_url
        if (candidate.tags?.length) body.tags = candidate.tags
      }

      if (Object.keys(body).length === 0) {
        applied++
        setProgress({ done: applied, total: toApply.length })
        continue
      }

      try {
        await fetch(`${API}/api/books/${result.book_id}/apply-metadata`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeader() },
          body: JSON.stringify(body),
        })
      } catch {
        // non-fatal: continue with rest
      }
      applied++
      setProgress({ done: applied, total: toApply.length })
    }

    setApplying(false)
    setProgress(null)

    const uncertainIds = results
      .filter(r => !rowState[r.book_id]?.approved || (rowState[r.book_id]?.selectedIndex === null && r.best_match_index === null))
      .map(r => r.book_id)
    setDoneState({ applied: toApply.length, uncertain: uncertainIds })
    onApplied()  // trigger data reload in parent
  }

  function handleClose() {
    if (applying) return
    onClose()
  }

  const matched = results.filter(r => r.best_match_index !== null).length
  const needsReview = results.filter(r => r.best_match_index === null && r.candidates.length > 0).length
  const noMatch = results.filter(r => r.candidates.length === 0).length

  const sourceName = (src: string) =>
    /* eslint-disable lingui/no-unlocalized-strings -- provider names */
    src === 'hardcover' ? 'Hardcover' : src === 'google_books' ? 'Google Books' : src === 'anilist' ? 'AniList' : 'Open Library'
    /* eslint-enable lingui/no-unlocalized-strings */

  const footer = doneState ? (
    <>
      <Pill variant="quiet" onClick={handleClose}><Trans>Close</Trans></Pill>
      {doneState.uncertain.length > 0 && onReviewUncertain && (
        <Pill variant="primary" onClick={() => { onReviewUncertain(doneState.uncertain); handleClose() }}>
          {(() => { const n = doneState.uncertain.length; return <Trans>Review {n} uncertain</Trans> })()}
        </Pill>
      )}
    </>
  ) : (
    <>
      <span className="mr-auto text-[13px] text-muted-foreground">
        {applying && progress
          ? (() => { const done = progress.done; const total = progress.total; return <Trans>Applying {done}/{total}…</Trans> })()
          : !loading && results.length > 0 ? (() => { const ok = approvedCount(); const total = results.length; return t`${ok} of ${total} approved` })() : ''}
      </span>
      <Pill variant="quiet" onClick={handleClose} disabled={applying}><Trans>Cancel</Trans></Pill>
      <Pill variant="primary" onClick={applyAll} disabled={applying || loading || approvedCount() === 0}>
        {applying ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
        {(() => { const n = approvedCount(); return n > 0 ? <Trans>Apply {n} approved</Trans> : <Trans>Apply approved</Trans> })()}
      </Pill>
    </>
  )

  return (
    <ModalShell
      open={open}
      onClose={handleClose}
      hideClose={applying}
      closeOnBackdrop={!applying}
      title={<Trans>Fetch metadata</Trans>}
      eyebrow={(() => { const n = bookIds.length; return plural(n, { one: '# book', other: '# books' }) })()}
      size="lg"
      footer={footer}
    >
      {/* Options */}
      {!doneState && !loading && results.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border/70 pb-3">
          <Checkbox
            checked={fillMissingOnly}
            onChange={e => setFillMissingOnly(e.target.checked)}
            label={<Trans>Fill missing fields only</Trans>}
          />
          <span className="text-[13px] text-muted-foreground sm:ml-auto">
            {t`${matched} matched`}{needsReview > 0 ? t` · ${needsReview} needs review` : ''}{noMatch > 0 ? t` · ${noMatch} no match` : ''}
          </span>
        </div>
      )}

      {doneState && (
        <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
          <Check className="h-10 w-10 text-success" />
          <p className="font-display text-[20px] font-bold text-foreground"><Trans>Batch complete</Trans></p>
          <p className="text-[14px] text-muted-foreground">
            {(() => { const n = doneState.applied; return <Trans>Applied metadata to {n} books.</Trans> })()}
            {doneState.uncertain.length > 0 && (() => { const n = doneState.uncertain.length; return t` ${n} had no confident match.` })()}
          </p>
        </div>
      )}

      {!doneState && loading && (
        <div className="flex flex-col items-center justify-center gap-3 py-16">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          {(() => { const n = bookIds.length; return <p className="text-[14px] text-muted-foreground"><Trans>Searching {n} books…</Trans></p> })()}
        </div>
      )}

      {!doneState && error && (
        <p className="flex items-center gap-2 py-4 text-[14px] text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </p>
      )}

      {!doneState && !loading && results.length > 0 && (
        <ul>
          {results.map(result => {
            const state = rowState[result.book_id] ?? { approved: false, selectedIndex: null, expanded: false }
            const idx = state.selectedIndex ?? result.best_match_index
            const match = idx !== null ? result.candidates[idx] : null
            const hasMatch = match !== null
            const adds: string[] = []
            if (match) {
              if (match.description && !result.book_description) adds.push(t`description`)
              if (match.cover_url && !result.book_cover_path) adds.push(t`cover`)
              if (match.publisher && !result.book_publisher) adds.push(t`publisher`)
              if (match.year && !result.book_year) adds.push(t`year`)
              if (match.series && !result.book_series) adds.push(t`series`)
            }

            return (
              <li key={result.book_id} className="border-b border-border/70 py-3">
                <div className={cn('flex items-start gap-3', !(state.approved && hasMatch) && 'opacity-60')}>
                  {/* Approve checkbox */}
                  <Checkbox
                    checked={state.approved && hasMatch}
                    disabled={!hasMatch}
                    onChange={() => toggleApproved(result.book_id)}
                    aria-label={t`Approve`}
                    className="mt-3.5"
                  />

                  <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-start sm:gap-3">
                    {/* Current book */}
                    <div className="flex min-w-0 flex-1 items-start gap-3">
                      <div className="relative h-12 w-8 shrink-0 overflow-hidden rounded-[6px] bg-secondary">
                        <CoverImage
                          src={result.book_cover_path ? API + coverSrc(result.book_id, { w: 160 }) : null}
                          alt=""
                          iconClassName="h-3 w-3"
                        />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="line-clamp-2 text-[15px] font-medium leading-snug text-foreground">{result.book_title}</p>
                        {result.book_author && (
                          <p className="text-[13px] text-muted-foreground">{result.book_author}</p>
                        )}
                      </div>
                    </div>

                    <ArrowRight className="mt-4 hidden h-4 w-4 shrink-0 text-muted-foreground/50 sm:block" />

                    {/* Match preview */}
                    {hasMatch && match ? (
                      <div className="flex min-w-0 flex-1 items-start gap-3">
                        {match.cover_url ? (
                          <img
                            src={match.cover_url}
                            alt=""
                            className="h-12 w-8 shrink-0 rounded-[6px] object-cover"
                            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                          />
                        ) : <div className="h-12 w-8 shrink-0 rounded-[6px] bg-secondary" />}
                        <div className="min-w-0 flex-1">
                          <p className="text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">{sourceName(match.source)}</p>
                          <p className="line-clamp-2 text-[15px] font-medium leading-snug text-foreground">{match.title}</p>
                          <p className="text-[13px] text-primary">
                            {adds.length > 0
                              ? adds.map(a => `+ ${a}`).join('  ')
                              : <span className="text-muted-foreground"><Trans>no new fields</Trans></span>}
                          </p>
                        </div>
                      </div>
                    ) : (
                      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1 sm:pt-3">
                        <span className="text-[14px] text-muted-foreground"><Trans>No match found</Trans></span>
                        <button
                          type="button"
                          onClick={() => { onManualSearch(result.book_id); handleClose() }}
                          className="inline-flex items-center gap-1 text-[14px] text-primary hover:underline underline-offset-4"
                        >
                          <Search className="h-3.5 w-3.5" /> <Trans>Search manually</Trans>
                        </button>
                      </div>
                    )}
                  </div>

                  {/* Pick a different candidate */}
                  <div className="w-8 shrink-0">
                    {hasMatch && result.candidates.length > 1 && (
                      <Pill
                        variant="quiet"
                        icon
                        onClick={() => toggleExpanded(result.book_id)}
                        title={t`Pick different candidate`}
                        aria-label={t`Pick different candidate`}
                        aria-expanded={state.expanded}
                        className="mt-1.5 h-8 w-8"
                      >
                        {state.expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                      </Pill>
                    )}
                  </div>
                </div>

                {/* Candidate picker (expanded) */}
                {state.expanded && result.candidates.length > 1 && (
                  <ul className="ml-[30px] mt-2 border-t border-border/70">
                    {result.candidates.map((c, i) => {
                      const picked = (state.selectedIndex ?? result.best_match_index) === i
                      return (
                        <li key={i} className="border-b border-border/70 last:border-b-0">
                          <button
                            type="button"
                            onClick={() => selectCandidate(result.book_id, i)}
                            className={cn(
                              '-mx-2 flex w-[calc(100%+1rem)] items-start gap-3 rounded-lg px-2 py-2 text-left transition-colors hover:bg-secondary/70',
                              picked && 'bg-secondary/60',
                            )}
                          >
                            {c.cover_url ? (
                              <img src={c.cover_url} alt="" className="h-9 w-6 shrink-0 rounded-[4px] object-cover"
                                onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
                            ) : <div className="h-9 w-6 shrink-0 rounded-[4px] bg-secondary" />}
                            <div className="min-w-0 flex-1">
                              <p className="text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
                                {sourceName(c.source)}{c.year ? ` · ${c.year}` : ''}
                              </p>
                              <p className="line-clamp-1 text-[14px] font-medium text-foreground">
                                {c.title}
                                {c.author && <span className="font-normal text-muted-foreground"> · {c.author}</span>}
                              </p>
                              {c.description && <p className="mt-0.5 line-clamp-1 text-[13px] text-muted-foreground">{c.description}</p>}
                            </div>
                            {picked && <Check className="ml-auto mt-1 h-4 w-4 shrink-0 text-primary" />}
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </ModalShell>
  )
}
