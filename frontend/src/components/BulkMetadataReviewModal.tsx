import { useEffect, useState } from 'react'
import { AlertCircle, BookOpen, Check, ChevronDown, ChevronUp, Loader2, Search, Sparkles, X } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { MetadataCandidate } from '@/lib/books'

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
      setError(e instanceof Error ? e.message : 'Fetch failed')
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

      // Title and author are always applied — candidate versions are cleaner than filenames
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
        // non-fatal — continue with rest
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
    setResults([])
    setRowState({})
    setError(null)
    setProgress(null)
    setDoneState(null)
    onClose()
  }

  if (!open) return null

  const matched = results.filter(r => r.best_match_index !== null).length
  const needsReview = results.filter(r => r.best_match_index === null && r.candidates.length > 0).length
  const noMatch = results.filter(r => r.candidates.length === 0).length

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={handleClose} />

      <div className="relative z-10 bg-background border border-border rounded-xl shadow-xl w-full max-w-3xl mx-4 max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border shrink-0">
          <div className="flex items-center gap-2 font-semibold text-sm">
            <Sparkles className="h-4 w-4 text-primary" />
            Fetch Metadata
            <span className="text-muted-foreground font-normal">— {bookIds.length} books</span>
          </div>
          <button onClick={handleClose} disabled={applying} className="text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Options bar */}
        {!loading && results.length > 0 && (
          <div className="px-6 py-2.5 border-b border-border bg-muted/30 flex items-center gap-4 shrink-0">
            <label className="flex items-center gap-2 text-xs cursor-pointer select-none">
              <input
                type="checkbox"
                checked={fillMissingOnly}
                onChange={e => setFillMissingOnly(e.target.checked)}
                className="rounded border-border"
              />
              <span className="text-muted-foreground">Fill missing fields only</span>
            </label>
            <span className="text-xs text-muted-foreground ml-auto">
              {matched} matched{needsReview > 0 ? ` · ${needsReview} needs review` : ''}{noMatch > 0 ? ` · ${noMatch} no match` : ''}
            </span>
          </div>
        )}

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {doneState && (
            <div className="flex flex-col items-center justify-center py-16 gap-4 text-center">
              <Check className="w-12 h-12 text-green-500" />
              <h2 className="text-lg font-semibold">Batch complete</h2>
              <p className="text-sm text-muted-foreground">
                Applied metadata to {doneState.applied} books.
                {doneState.uncertain.length > 0 && ` ${doneState.uncertain.length} had no confident match.`}
              </p>
              <div className="flex items-center gap-2">
                {doneState.uncertain.length > 0 && onReviewUncertain && (
                  <button
                    onClick={() => { onReviewUncertain(doneState.uncertain); handleClose() }}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all"
                  >
                    Review {doneState.uncertain.length} Uncertain
                  </button>
                )}
                <button
                  onClick={handleClose}
                  className="px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                >
                  Close
                </button>
              </div>
            </div>
          )}

          {!doneState && loading && (
            <div className="flex flex-col items-center justify-center py-16 gap-3">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">Searching {bookIds.length} books…</p>
            </div>
          )}

          {!doneState && error && (
            <div className="flex items-center gap-2 m-6 px-3 py-2 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          )}

          {!doneState && !loading && results.length > 0 && (
            <div className="divide-y divide-border">
              {results.map(result => {
                const state = rowState[result.book_id] ?? { approved: false, selectedIndex: null, expanded: false }
                const idx = state.selectedIndex ?? result.best_match_index
                const match = idx !== null ? result.candidates[idx] : null
                const hasMatch = match !== null

                return (
                  <div key={result.book_id} className={cn('px-6 py-3', state.approved && hasMatch ? '' : 'opacity-60')}>
                    <div className="flex items-start gap-3">
                      {/* Approve checkbox */}
                      <input
                        type="checkbox"
                        checked={state.approved && hasMatch}
                        disabled={!hasMatch}
                        onChange={() => toggleApproved(result.book_id)}
                        className="mt-1 rounded border-border shrink-0"
                      />

                      {/* Current book */}
                      <div className="w-8 shrink-0">
                        {result.book_cover_path ? (
                          <img
                            src={`${API}/api/books/${result.book_id}/cover`}
                            alt=""
                            className="w-8 h-12 object-cover rounded border border-border"
                          />
                        ) : (
                          <div className="w-8 h-12 rounded bg-muted border border-border flex items-center justify-center">
                            <BookOpen className="h-3 w-3 text-muted-foreground" />
                          </div>
                        )}
                      </div>

                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium leading-tight line-clamp-1">{result.book_title}</p>
                        {result.book_author && (
                          <p className="text-xs text-muted-foreground">{result.book_author}</p>
                        )}
                      </div>

                      {/* Arrow */}
                      <div className="text-muted-foreground/40 shrink-0 mt-2">→</div>

                      {/* Match preview */}
                      {hasMatch && match ? (
                        <div className="flex-1 min-w-0 flex items-start gap-2">
                          {match.cover_url && (
                            <img
                              src={match.cover_url}
                              alt=""
                              className="w-8 h-12 object-cover rounded border border-border shrink-0"
                              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                            />
                          )}
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-1.5 flex-wrap">
                              <p className="text-sm font-medium line-clamp-1">{match.title}</p>
                              <span className="shrink-0 text-[9px] px-1 py-0.5 rounded bg-muted text-muted-foreground border border-border">
                                {match.source === 'hardcover' ? 'Hardcover' : match.source === 'google_books' ? 'Google' : 'OpenLib'}
                              </span>
                            </div>
                            <div className="flex gap-2 flex-wrap mt-0.5">
                              {match.description && !result.book_description && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">+desc</span>
                              )}
                              {match.cover_url && !result.book_cover_path && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">+cover</span>
                              )}
                              {match.publisher && !result.book_publisher && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">+pub</span>
                              )}
                              {match.year && !result.book_year && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">+year</span>
                              )}
                              {match.series && !result.book_series && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">+series</span>
                              )}
                              {!match.description && !match.cover_url && !match.publisher && !match.year && !match.series && (
                                <span className="text-[10px] text-muted-foreground">no new fields</span>
                              )}
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="flex-1 flex items-center gap-1.5">
                          <span className="text-xs text-muted-foreground italic">No match found</span>
                          <button
                            onClick={() => { onManualSearch(result.book_id); handleClose() }}
                            className="text-xs text-primary hover:underline flex items-center gap-1"
                          >
                            <Search className="h-3 w-3" /> Search manually
                          </button>
                        </div>
                      )}

                      {/* Expand / manual search buttons */}
                      <div className="flex items-center gap-1 shrink-0">
                        {hasMatch && result.candidates.length > 1 && (
                          <button
                            onClick={() => toggleExpanded(result.book_id)}
                            className="p-1 text-muted-foreground hover:text-foreground transition-colors"
                            title="Pick different candidate"
                          >
                            {state.expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                          </button>
                        )}
                      </div>
                    </div>

                    {/* Candidate picker (expanded) */}
                    {state.expanded && result.candidates.length > 1 && (
                      <div className="mt-2 ml-11 space-y-1.5">
                        {result.candidates.map((c, i) => (
                          <button
                            key={i}
                            onClick={() => selectCandidate(result.book_id, i)}
                            className={cn(
                              'w-full text-left flex items-start gap-2 px-2.5 py-2 rounded-lg border text-xs transition-colors',
                              (state.selectedIndex ?? result.best_match_index) === i
                                ? 'border-primary/40 bg-primary/5'
                                : 'border-border bg-card hover:bg-muted'
                            )}
                          >
                            {c.cover_url && (
                              <img src={c.cover_url} alt="" className="w-6 h-9 object-cover rounded shrink-0"
                                onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
                            )}
                            <div className="min-w-0">
                              <span className="font-medium line-clamp-1">{c.title}</span>
                              {c.author && <span className="text-muted-foreground ml-1">· {c.author}</span>}
                              <span className="ml-1 text-[9px] px-1 py-0.5 rounded bg-muted text-muted-foreground border border-border">
                                {c.source === 'hardcover' ? 'Hardcover' : c.source === 'google_books' ? 'Google' : 'OpenLib'}
                              </span>
                              {c.year && <span className="text-muted-foreground ml-1">· {c.year}</span>}
                              {c.description && <p className="text-muted-foreground line-clamp-1 mt-0.5">{c.description}</p>}
                            </div>
                            {(state.selectedIndex ?? result.best_match_index) === i && (
                              <Check className="h-3.5 w-3.5 text-primary shrink-0 mt-0.5 ml-auto" />
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {!doneState && (
          <div className="px-6 py-4 border-t border-border flex items-center justify-between gap-3 shrink-0">
            {applying && progress ? (
              <span className="text-xs text-muted-foreground">
                Applying {progress.done}/{progress.total}…
              </span>
            ) : (
              <span className="text-xs text-muted-foreground">
                {!loading && results.length > 0 ? `${approvedCount()} of ${results.length} approved` : ''}
              </span>
            )}
            <div className="flex items-center gap-2">
              <button
                onClick={handleClose}
                disabled={applying}
                className="px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={applyAll}
                disabled={applying || loading || approvedCount() === 0}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
              >
                {applying
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  : <Check className="h-3.5 w-3.5" />}
                Apply {approvedCount() > 0 ? approvedCount() : ''} Approved
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
