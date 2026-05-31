import { useState, useEffect, useRef, useCallback } from 'react'
import { X, Search, Loader2, BookOpen, Layers, ChevronLeft } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  searchWishCandidates,
  searchWishSeries,
  seriesSearchAvailable,
  createWish,
  type WishSearchResult,
  type WishSeriesResult,
} from '@/lib/wishlist'

interface Props {
  onClose: () => void
  onCreated: () => void
}

type Mode = 'book' | 'series'

function SeriesThumb({ url }: { url: string | null }) {
  return (
    <div className="w-10 h-14 rounded bg-muted shrink-0 overflow-hidden flex items-center justify-center">
      {url ? (
        <img src={url} alt="" className="w-full h-full object-cover" onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} />
      ) : (
        <Layers className="w-4 h-4 text-muted-foreground" />
      )}
    </div>
  )
}

export function WishlistModal({ onClose, onCreated }: Props) {
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
  const [visible, setVisible] = useState(false)

  const searchRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    requestAnimationFrame(() => setVisible(true))
    inputRef.current?.focus()
    // Series search is Hardcover-only — only offer the Series tab when available.
    seriesSearchAvailable().then(r => setSeriesAvailable(!!r.available)).catch(() => {})
  }, [])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') handleClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
    setVisible(false)
    setTimeout(onClose, 200)
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
      setError('Please pick a result or use the manual form.')
      return
    }
    if (manualMode && !manualTitle.trim()) {
      setError('Title is required.')
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
          setError('Series name is required.')
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
      setError(e instanceof Error ? e.message : 'Failed to add wish')
    } finally {
      setSubmitting(false)
    }
  }

  const SOURCE_LABEL: Record<string, string> = {
    hardcover: 'Hardcover',
    google_books: 'Google Books',
    open_library: 'OpenLibrary',
    manual: 'Manual',
  }

  const inSearchState = !manualMode && !selected && !selectedSeries

  return (
    <div
      className={cn(
        'fixed inset-0 z-50 flex items-center justify-center p-4 transition-all duration-200',
        visible ? 'bg-black/40 backdrop-blur-sm' : 'bg-black/0 backdrop-blur-0'
      )}
      onMouseDown={e => { if (e.target === e.currentTarget) handleClose() }}
    >
      <div className={cn(
        'bg-card text-foreground rounded-2xl shadow-xl shadow-accent-soft w-full max-w-lg flex flex-col max-h-[85vh] transition-all duration-200',
        visible ? 'opacity-100 scale-100' : 'opacity-0 scale-95'
      )}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <h2 className="text-base font-semibold">Add to Wishlist</h2>
          <button
            onClick={handleClose}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {inSearchState && (
            <>
              {/* Book / Series mode — Series tab only when Hardcover is configured */}
              {seriesAvailable && (
              <div className="flex rounded-lg overflow-hidden border border-border text-xs font-medium">
                <button
                  type="button"
                  onClick={() => switchMode('book')}
                  className={cn(
                    'flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 transition-colors',
                    searchMode === 'book' ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:text-foreground'
                  )}
                >
                  <BookOpen className="w-3 h-3" />
                  Book
                </button>
                <button
                  type="button"
                  onClick={() => switchMode('series')}
                  className={cn(
                    'flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 transition-colors border-l border-border',
                    searchMode === 'series' ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:text-foreground'
                  )}
                >
                  <Layers className="w-3 h-3" />
                  Series
                </button>
              </div>
              )}

              {/* Search box */}
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={e => handleQueryChange(e.target.value)}
                  placeholder={searchMode === 'series' ? 'Search for a series…' : 'Search for a book…'}
                  className="w-full h-9 pl-9 pr-4 rounded-lg bg-muted border border-border text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                />
                {searching && (
                  <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground animate-spin" />
                )}
              </div>

              {/* Book candidates */}
              {searchMode === 'book' && candidates.length > 0 && (
                <div className="space-y-2">
                  {candidates.slice(0, 5).map((c, i) => (
                    <button
                      key={`${c.source}-${c.source_id}-${i}`}
                      onClick={() => selectCandidate(c)}
                      className="w-full flex items-start gap-3 p-3 rounded-xl border border-border bg-background text-left transition-all hover:border-primary/40 hover:bg-muted/40"
                    >
                      <div className="w-10 h-14 rounded bg-muted shrink-0 overflow-hidden flex items-center justify-center">
                        {c.cover_url ? (
                          <img
                            src={c.cover_url}
                            alt=""
                            className="w-full h-full object-cover"
                            onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
                          />
                        ) : (
                          <BookOpen className="w-4 h-4 text-muted-foreground" />
                        )}
                      </div>
                      <div className="flex-1 min-w-0 flex flex-col gap-0.5">
                        <p className="text-sm font-medium text-foreground line-clamp-2 leading-snug">{c.title}</p>
                        {c.author && <p className="text-xs text-muted-foreground truncate">{c.author}</p>}
                        {c.series && (
                          <p className="text-xs text-muted-foreground/70 truncate">
                            {c.series}{c.series_index != null ? ` #${c.series_index}` : ''}
                          </p>
                        )}
                        <span className="mt-1 inline-flex self-start text-[10px] px-1.5 py-0.5 rounded bg-muted border border-border text-muted-foreground font-medium">
                          {SOURCE_LABEL[c.source] ?? c.source}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              )}

              {/* Series results */}
              {searchMode === 'series' && seriesResults.length > 0 && (
                <div className="space-y-2">
                  {seriesResults.map(s => (
                    <button
                      key={`${s.source}-${s.source_id}`}
                      onClick={() => { setSelectedSeries(s); setError(null) }}
                      className="w-full flex items-start gap-3 p-3 rounded-xl border border-border bg-background text-left transition-all hover:border-primary/40 hover:bg-muted/40"
                    >
                      <SeriesThumb url={s.cover_url} />
                      <div className="flex-1 min-w-0 flex flex-col gap-0.5">
                        <p className="text-sm font-medium text-foreground line-clamp-2 leading-snug">{s.name}</p>
                        {s.author && <p className="text-xs text-muted-foreground truncate">{s.author}</p>}
                        {s.total != null && (
                          <p className="text-xs text-muted-foreground/70">{s.total} volume{s.total !== 1 ? 's' : ''}</p>
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              )}

              {query && !searching && (
                (searchMode === 'book' ? candidates.length === 0 : seriesResults.length === 0) && (
                  <p className="text-sm text-muted-foreground text-center py-4">No results found.</p>
                )
              )}

              {/* Manual fallback (book mode only) */}
              {searchMode === 'book' && (
                <button
                  type="button"
                  onClick={() => { setManualMode(true); setSelected(null); setWishWholeSeries(false); setError(null) }}
                  className="text-xs text-muted-foreground hover:text-foreground transition-colors underline-offset-2 hover:underline"
                >
                  Can't find it? Add manually
                </button>
              )}
            </>
          )}

          {manualMode && (
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">
                  Title <span className="text-destructive">*</span>
                </label>
                <input
                  value={manualTitle}
                  onChange={e => { setManualTitle(e.target.value); setError(null) }}
                  placeholder="Book title"
                  autoFocus
                  className="w-full h-9 rounded-lg border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">Author</label>
                <input
                  value={manualAuthor}
                  onChange={e => setManualAuthor(e.target.value)}
                  placeholder="Author name"
                  className="w-full h-9 rounded-lg border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">Series</label>
                <input
                  value={manualSeries}
                  onChange={e => setManualSeries(e.target.value)}
                  placeholder="Series name — fill this alone to wish for the whole series"
                  className="w-full h-9 rounded-lg border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">Note</label>
                <textarea
                  value={manualNote}
                  onChange={e => setManualNote(e.target.value)}
                  placeholder="Optional note (e.g. 'the new one')"
                  rows={2}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring resize-none"
                />
              </div>
              <button
                type="button"
                onClick={() => { setManualMode(false); setError(null) }}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors underline-offset-2 hover:underline"
              >
                Back to search
              </button>
            </div>
          )}

          {/* Book confirm view */}
          {!manualMode && selected && (
            <>
              <button
                type="button"
                onClick={() => { setSelected(null); setWishWholeSeries(false); setError(null); inputRef.current?.focus() }}
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
                Choose a different book
              </button>

              <div className="border border-primary/30 bg-primary/5 rounded-xl p-3 flex items-start gap-3">
                <div className="w-10 h-14 rounded bg-muted shrink-0 overflow-hidden flex items-center justify-center">
                  {selected.cover_url ? (
                    <img src={selected.cover_url} alt="" className="w-full h-full object-cover" />
                  ) : (
                    <BookOpen className="w-4 h-4 text-muted-foreground" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground leading-snug">{selected.title}</p>
                  {selected.author && <p className="text-xs text-muted-foreground mt-0.5">{selected.author}</p>}
                  {selected.series && (
                    <p className="text-xs text-muted-foreground/70">
                      {selected.series}{selected.series_index != null ? ` #${selected.series_index}` : ''}
                    </p>
                  )}
                </div>
              </div>

              <div className="flex rounded-lg overflow-hidden border border-border text-xs font-medium">
                <button
                  type="button"
                  onClick={() => setWishWholeSeries(false)}
                  className={cn(
                    'flex-1 px-3 py-1.5 transition-colors',
                    !wishWholeSeries
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground hover:text-foreground'
                  )}
                >
                  This volume
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setWishWholeSeries(true)
                    setSeriesNameDraft(deriveSeriesName(selected))
                  }}
                  className={cn(
                    'flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 transition-colors border-l border-border',
                    wishWholeSeries
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground hover:text-foreground'
                  )}
                >
                  <Layers className="w-3 h-3" />
                  Whole series
                </button>
              </div>

              {wishWholeSeries && (
                <div>
                  <label className="block text-xs font-medium text-muted-foreground mb-1">Series name</label>
                  <input
                    value={seriesNameDraft}
                    onChange={e => { setSeriesNameDraft(e.target.value); setError(null) }}
                    placeholder="Series name"
                    className="w-full h-9 rounded-lg border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">For precise series tracking, use the <strong>Series</strong> tab instead.</p>
                </div>
              )}
            </>
          )}

          {/* Series confirm view */}
          {!manualMode && selectedSeries && (
            <>
              <button
                type="button"
                onClick={() => { setSelectedSeries(null); setError(null); inputRef.current?.focus() }}
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
                Choose a different series
              </button>

              <div className="border border-primary/30 bg-primary/5 rounded-xl p-3 flex items-start gap-3">
                <SeriesThumb url={selectedSeries.cover_url} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground leading-snug">{selectedSeries.name}</p>
                  {selectedSeries.author && <p className="text-xs text-muted-foreground mt-0.5">{selectedSeries.author}</p>}
                  {selectedSeries.total != null && (
                    <p className="text-xs text-muted-foreground/70">{selectedSeries.total} volumes</p>
                  )}
                </div>
              </div>
              <p className="text-xs text-muted-foreground px-1">
                This wish covers the whole series and stays open as new volumes arrive.
              </p>
            </>
          )}

          {error && (
            <p className="text-xs text-destructive px-1">{error}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-border shrink-0">
          <button
            onClick={handleClose}
            className="px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={
              submitting
              || (manualMode && !manualTitle.trim())
              || (!manualMode && !selected && !selectedSeries)
              || (!manualMode && !!selected && wishWholeSeries && !seriesNameDraft.trim())
            }
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
          >
            {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            Add to Wishlist
          </button>
        </div>
      </div>
    </div>
  )
}
