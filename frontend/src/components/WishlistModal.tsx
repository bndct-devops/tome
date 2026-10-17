import { useState, useEffect, useRef, useCallback } from 'react'
import { Trans, Plural } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { X, Search, Loader2, BookOpen, Layers, ChevronLeft } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ModalShell } from '@/components/ModalShell'
import { Pill } from '@/components/page'
import {
  searchWishCandidates,
  searchWishSeries,
  seriesSearchAvailable,
  createWish,
  type WishSearchResult,
  type WishSeriesResult,
} from '@/lib/wishlist'

interface Props {
  open: boolean
  onClose: () => void
  onCreated: () => void
}

type Mode = 'book' | 'series'

const INPUT = 'w-full h-10 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring'
const LABEL = 'block text-[13px] text-muted-foreground mb-1.5'
const RESULT_ROW = 'group w-full flex items-start gap-4 py-3 text-left'

function Thumb({ url, series = false }: { url: string | null; series?: boolean }) {
  return (
    <div className="w-11 h-16 rounded-[8px] bg-muted shrink-0 overflow-hidden flex items-center justify-center" style={{ boxShadow: 'var(--cover-bloom-fan)' }}>
      {url ? (
        <img src={url} alt="" className="w-full h-full object-cover" onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} />
      ) : series ? (
        <Layers className="w-4 h-4 text-muted-foreground" />
      ) : (
        <BookOpen className="w-4 h-4 text-muted-foreground" />
      )}
    </div>
  )
}

export function WishlistModal({ open, onClose, onCreated }: Props) {
  const [searchMode, setSearchMode] = useState<Mode>('book')
  const [seriesAvailable, setSeriesAvailable] = useState(false)
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<WishSearchResult[]>([])
  const [seriesResults, setSeriesResults] = useState<WishSeriesResult[]>([])
  const [searching, setSearching] = useState(false)
  const [selected, setSelected] = useState<WishSearchResult | null>(null)
  const [selectedSeries, setSelectedSeries] = useState<WishSeriesResult | null>(null)
  const [manualMode, setManualMode] = useState(false)

  // Manual / free-text fields
  const [manualTitle, setManualTitle] = useState('')
  const [manualAuthor, setManualAuthor] = useState('')
  const [manualSeries, setManualSeries] = useState('')
  const [manualNote, setManualNote] = useState('')

  const [wishWholeSeries, setWishWholeSeries] = useState(false)
  const [seriesNameDraft, setSeriesNameDraft] = useState('')

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const searchRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    inputRef.current?.focus()
    // Series search is Hardcover-only — only offer the Series tab when available.
    seriesSearchAvailable().then(r => setSeriesAvailable(!!r.available)).catch(() => {})
  }, [open])

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') handleClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const doSearch = useCallback(async (q: string, mode: Mode) => {
    if (!q.trim()) {
      setCandidates([])
      setSeriesResults([])
      return
    }
    setSearching(true)
    try {
      if (mode === 'series') {
        setSeriesResults(await searchWishSeries(q.trim()))
      } else {
        setCandidates(await searchWishCandidates(q.trim()))
      }
    } catch {
      setCandidates([])
      setSeriesResults([])
    } finally {
      setSearching(false)
    }
  }, [])

  function handleQueryChange(val: string) {
    setQuery(val)
    setSelected(null)
    setSelectedSeries(null)
    setError(null)
    if (searchRef.current) clearTimeout(searchRef.current)
    const mode = searchMode
    searchRef.current = setTimeout(() => { doSearch(val, mode) }, 350)
  }

  function switchMode(mode: Mode) {
    if (mode === searchMode) return
    setSearchMode(mode)
    setQuery('')
    setCandidates([])
    setSeriesResults([])
    setSelected(null)
    setSelectedSeries(null)
    setWishWholeSeries(false)
    setManualMode(false)
    setError(null)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  function handleClose() {
    onClose()
  }

  function deriveSeriesName(c: WishSearchResult): string {
    if (c.series) return c.series
    const t = c.title
    // strip a subtitle after a colon: "Mistborn: The Final Empire" -> "Mistborn"
    const colon = t.indexOf(':')
    if (colon > 0) return t.slice(0, colon).trim()
    // strip trailing volume markers: "Foo Vol. 3", "Foo #3", "Foo, Book 2"
    return t.replace(/[\s,]*(vol\.?|volume|book|#)\s*\d+.*$/i, '').trim() || t
  }

  function selectCandidate(c: WishSearchResult) {
    setSelected(c)
    setWishWholeSeries(false)
    setSeriesNameDraft(deriveSeriesName(c))
    setManualMode(false)
    setError(null)
  }

  async function handleSubmit() {
    setError(null)
    if (!manualMode && !selected && !selectedSeries) {
      setError(t`Please pick a result or use the manual form.`)
      return
    }
    if (manualMode && !manualTitle.trim()) {
      setError(t`Title is required.`)
      return
    }

    setSubmitting(true)
    try {
      if (manualMode) {
        await createWish({
          title: manualTitle.trim(),
          author: manualAuthor.trim() || null,
          series: manualSeries.trim() || null,
          note: manualNote.trim() || null,
        })
      } else if (selectedSeries) {
        // Canonical whole-series wish from a Hardcover series result.
        await createWish({
          title: selectedSeries.name,
          series: selectedSeries.name,
          author: selectedSeries.author,
          series_index: null,
          cover_url: selectedSeries.cover_url,
          source: selectedSeries.source,
          source_id: selectedSeries.source_id,
          external_series_id: selectedSeries.source_id,
          series_total: selectedSeries.total,
        })
      } else if (wishWholeSeries) {
        if (!seriesNameDraft.trim()) {
          setError(t`Series name is required.`)
          setSubmitting(false)
          return
        }
        await createWish({
          title: seriesNameDraft.trim(),
          author: selected!.author,
          series: seriesNameDraft.trim(),
          series_index: null,
          cover_url: selected!.cover_url,
          source: null,
          source_id: null,
          isbn: null,
        })
      } else {
        await createWish({
          title: selected!.title,
          author: selected!.author,
          series: selected!.series,
          series_index: selected!.series_index,
          cover_url: selected!.cover_url,
          source: selected!.source,
          source_id: selected!.source_id,
          isbn: selected!.isbn,
        })
      }
      onCreated()
      handleClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Failed to add wish`)
    } finally {
      setSubmitting(false)
    }
  }

  /* eslint-disable lingui/no-unlocalized-strings -- provider names */
  const SOURCE_LABEL: Record<string, string> = {
    hardcover: 'Hardcover',
    google_books: 'Google Books',
    open_library: 'OpenLibrary',
    manual: 'Manual',
  }
  /* eslint-enable lingui/no-unlocalized-strings */

  const inSearchState = !manualMode && !selected && !selectedSeries

  return (
    <ModalShell open={open} onClose={handleClose} className="w-full max-w-lg">
      <div className="bg-card text-foreground rounded-[var(--radius-panel)] shadow-xl shadow-accent-soft flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 pl-6 pr-4 pt-5 pb-3 shrink-0">
          <h2 className="font-display text-[20px] font-bold leading-none"><Trans>Add to Wishlist</Trans></h2>
          <Pill variant="quiet" icon onClick={handleClose} aria-label={t`Close`}>
            <X className="w-4 h-4" />
          </Pill>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 pt-2 pb-5 space-y-4">
          {inSearchState && (
            <>
              {/* Book / Series mode: Series only when Hardcover is configured */}
              {seriesAvailable && (
                <div className="flex items-center gap-1.5">
                  <Pill
                    variant={searchMode === 'book' ? 'secondary' : 'quiet'}
                    onClick={() => switchMode('book')}
                    aria-pressed={searchMode === 'book'}
                  >
                    <BookOpen className="w-4 h-4" />
                    <Trans>Book</Trans>
                  </Pill>
                  <Pill
                    variant={searchMode === 'series' ? 'secondary' : 'quiet'}
                    onClick={() => switchMode('series')}
                    aria-pressed={searchMode === 'series'}
                  >
                    <Layers className="w-4 h-4" />
                    <Trans>Series</Trans>
                  </Pill>
                </div>
              )}

              {/* Search box */}
              <div className="relative">
                <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={e => handleQueryChange(e.target.value)}
                  placeholder={searchMode === 'series' ? t`Search for a series…` : t`Search for a book…`}
                  className={cn(INPUT, 'pl-10 pr-9')}
                />
                {searching && (
                  <Loader2 className="absolute right-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground animate-spin" />
                )}
              </div>

              {/* Book candidates */}
              {searchMode === 'book' && candidates.length > 0 && (
                <div>
                  {candidates.slice(0, 5).map((c, i) => (
                    <button
                      key={`${c.source}-${c.source_id}-${i}`}
                      onClick={() => selectCandidate(c)}
                      className={cn(RESULT_ROW, i > 0 && 'border-t border-border/70')}
                    >
                      <Thumb url={c.cover_url} />
                      <div className="flex-1 min-w-0">
                        <p className="text-[15px] font-semibold text-foreground line-clamp-2 leading-snug group-hover:text-primary transition-colors">{c.title}</p>
                        {c.author && <p className="mt-0.5 text-[14px] text-muted-foreground truncate">{c.author}</p>}
                        {c.series && (
                          <p className="text-[13px] text-muted-foreground truncate">
                            {c.series}{c.series_index != null ? ` #${c.series_index}` : ''}
                          </p>
                        )}
                        <span className="mt-1.5 inline-flex items-center h-6 px-2.5 rounded-full bg-secondary text-[12px] font-medium text-muted-foreground">
                          {SOURCE_LABEL[c.source] ?? c.source}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              )}

              {/* Series results */}
              {searchMode === 'series' && seriesResults.length > 0 && (
                <div>
                  {seriesResults.map((s, i) => (
                    <button
                      key={`${s.source}-${s.source_id}`}
                      onClick={() => { setSelectedSeries(s); setError(null) }}
                      className={cn(RESULT_ROW, i > 0 && 'border-t border-border/70')}
                    >
                      <Thumb url={s.cover_url} series />
                      <div className="flex-1 min-w-0">
                        <p className="text-[15px] font-semibold text-foreground line-clamp-2 leading-snug group-hover:text-primary transition-colors">{s.name}</p>
                        {s.author && <p className="mt-0.5 text-[14px] text-muted-foreground truncate">{s.author}</p>}
                        {s.total != null && (
                          <p className="text-[13px] text-muted-foreground"><Plural value={s.total} one="# volume" other="# volumes" /></p>
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              )}

              {query && !searching && (
                (searchMode === 'book' ? candidates.length === 0 : seriesResults.length === 0) && (
                  <p className="text-[14px] text-muted-foreground text-center py-4"><Trans>No results found.</Trans></p>
                )
              )}

              {/* Manual fallback (book mode only) */}
              {searchMode === 'book' && (
                <Pill
                  variant="quiet"
                  onClick={() => { setManualMode(true); setSelected(null); setWishWholeSeries(false); setError(null) }}
                  className="-ml-4"
                >
                  <Trans>Can't find it? Add manually</Trans>
                </Pill>
              )}
            </>
          )}

          {manualMode && (
            <div className="space-y-3.5">
              <div>
                <label className={LABEL}>
                  <Trans>Title <span className="text-destructive">*</span></Trans>
                </label>
                <input
                  value={manualTitle}
                  onChange={e => { setManualTitle(e.target.value); setError(null) }}
                  placeholder={t`Book title`}
                  autoFocus
                  className={INPUT}
                />
              </div>
              <div>
                <label className={LABEL}><Trans>Author</Trans></label>
                <input
                  value={manualAuthor}
                  onChange={e => setManualAuthor(e.target.value)}
                  placeholder={t`Author name`}
                  className={INPUT}
                />
              </div>
              <div>
                <label className={LABEL}><Trans>Series</Trans></label>
                <input
                  value={manualSeries}
                  onChange={e => setManualSeries(e.target.value)}
                  placeholder={t`Series name. Fill this alone to wish for the whole series.`}
                  className={INPUT}
                />
              </div>
              <div>
                <label className={LABEL}><Trans>Note</Trans></label>
                <textarea
                  value={manualNote}
                  onChange={e => setManualNote(e.target.value)}
                  placeholder={t`Optional note (e.g. 'the new one')`}
                  rows={2}
                  className="w-full rounded-lg bg-secondary px-3 py-2 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring resize-none"
                />
              </div>
              <Pill
                variant="quiet"
                onClick={() => { setManualMode(false); setError(null) }}
                className="-ml-4"
              >
                <ChevronLeft className="w-4 h-4" />
                <Trans>Back to search</Trans>
              </Pill>
            </div>
          )}

          {/* Book confirm view */}
          {!manualMode && selected && (
            <>
              <Pill
                variant="quiet"
                onClick={() => { setSelected(null); setWishWholeSeries(false); setError(null); inputRef.current?.focus() }}
                className="-ml-4"
              >
                <ChevronLeft className="w-4 h-4" />
                <Trans>Choose a different book</Trans>
              </Pill>

              <div className="rounded-[14px] bg-secondary p-3 flex items-start gap-4">
                <Thumb url={selected.cover_url} />
                <div className="flex-1 min-w-0">
                  <p className="text-[15px] font-semibold text-foreground leading-snug">{selected.title}</p>
                  {selected.author && <p className="mt-0.5 text-[14px] text-muted-foreground">{selected.author}</p>}
                  {selected.series && (
                    <p className="text-[13px] text-muted-foreground">
                      {selected.series}{selected.series_index != null ? ` #${selected.series_index}` : ''}
                    </p>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-1.5">
                <Pill
                  variant={!wishWholeSeries ? 'secondary' : 'quiet'}
                  onClick={() => setWishWholeSeries(false)}
                  aria-pressed={!wishWholeSeries}
                >
                  <Trans>This volume</Trans>
                </Pill>
                <Pill
                  variant={wishWholeSeries ? 'secondary' : 'quiet'}
                  onClick={() => {
                    setWishWholeSeries(true)
                    setSeriesNameDraft(deriveSeriesName(selected))
                  }}
                  aria-pressed={wishWholeSeries}
                >
                  <Layers className="w-4 h-4" />
                  <Trans>Whole series</Trans>
                </Pill>
              </div>

              {wishWholeSeries && (
                <div>
                  <label className={LABEL}><Trans>Series name</Trans></label>
                  <input
                    value={seriesNameDraft}
                    onChange={e => { setSeriesNameDraft(e.target.value); setError(null) }}
                    placeholder={t`Series name`}
                    className={INPUT}
                  />
                  <p className="mt-1.5 text-[13px] text-muted-foreground"><Trans>For precise series tracking, use the <strong>Series</strong> tab instead.</Trans></p>
                </div>
              )}
            </>
          )}

          {/* Series confirm view */}
          {!manualMode && selectedSeries && (
            <>
              <Pill
                variant="quiet"
                onClick={() => { setSelectedSeries(null); setError(null); inputRef.current?.focus() }}
                className="-ml-4"
              >
                <ChevronLeft className="w-4 h-4" />
                <Trans>Choose a different series</Trans>
              </Pill>

              <div className="rounded-[14px] bg-secondary p-3 flex items-start gap-4">
                <Thumb url={selectedSeries.cover_url} series />
                <div className="flex-1 min-w-0">
                  <p className="text-[15px] font-semibold text-foreground leading-snug">{selectedSeries.name}</p>
                  {selectedSeries.author && <p className="mt-0.5 text-[14px] text-muted-foreground">{selectedSeries.author}</p>}
                  {selectedSeries.total != null && (
                    <p className="text-[13px] text-muted-foreground">{(() => { const n = selectedSeries.total; return <Trans>{n} volumes</Trans> })()}</p>
                  )}
                </div>
              </div>
              <p className="text-[13px] text-muted-foreground">
                <Trans>This wish covers the whole series and stays open as new volumes arrive.</Trans>
              </p>
            </>
          )}

          {error && (
            <p className="text-[13px] text-destructive">{error}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-border/70 shrink-0">
          <Pill variant="quiet" onClick={handleClose}>
            <Trans>Cancel</Trans>
          </Pill>
          <Pill
            variant="primary"
            onClick={handleSubmit}
            disabled={
              submitting
              || (manualMode && !manualTitle.trim())
              || (!manualMode && !selected && !selectedSeries)
              || (!manualMode && !!selected && wishWholeSeries && !seriesNameDraft.trim())
            }
          >
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            <Trans>Add to Wishlist</Trans>
          </Pill>
        </div>
      </div>
    </ModalShell>
  )
}
