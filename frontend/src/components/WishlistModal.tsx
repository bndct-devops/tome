import { useState, useEffect, useLayoutEffect, useRef, useCallback, type ReactNode } from 'react'
import { Trans, Plural } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { Search, Loader2, BookOpen, Layers, ChevronLeft } from 'lucide-react'
import { AnimatePresence, m } from 'motion/react'
import { cn } from '@/lib/utils'
import { ModalShell } from '@/components/ModalShell'
import { FormLabel, Input, Pill, PillGroup, Textarea } from '@/components/page'
import { EASE, EASE_CSS, Unfold, listItem } from '@/components/motion'
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

const RESULT_ROW = 'group w-full flex items-start gap-4 py-3 text-left touch-feedback'

// A new step of the dialog (search, manual form, confirm) rises into place
// from part opacity; the step it replaces goes at once.
const STEP_IN = { opacity: 0.5, y: 6 }
const STEP_TRANSITION = { duration: 0.16, ease: EASE }

/* eslint-disable lingui/no-unlocalized-strings -- CSS values and a media query */
const STEP_MS = 260
const HEIGHT_MOVE = `height ${STEP_MS}ms ${EASE_CSS}`
const TOP_MOVE = `top ${STEP_MS}ms ${EASE_CSS}`
const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
/* eslint-enable lingui/no-unlocalized-strings */

/**
 * The dialog's body at a height that moves: when the content changes height (a step
 * replaced, results arriving) the box runs from the old height to the new one, clipped
 * while it moves, and the content is laid out at its final size from the first frame.
 * The panel, centred on the screen, is moved down by half the change (`top`, laid out in
 * the same frame as the height, see GoalDialog), so its top edge stays where it opened
 * and the dialog grows or shrinks at the bottom only. Written to the DOM straight from
 * the ResizeObserver, before the frame paints.
 */
function SteadyHeight({ children }: { children: ReactNode }) {
  const outer = useRef<HTMLDivElement>(null)
  const inner = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const box = outer.current, el = inner.current
    if (!box || !el) return
    let last: number | null = null
    // The panel's height at the first measure: the height its top edge was centred for.
    let base: number | null = null
    const apply = () => {
      const next = el.offsetHeight
      const prev = last
      last = next
      if (prev === next) return
      const panel = el.closest<HTMLElement>('[role="dialog"]')
      if (prev == null || !panel) {
        base = panel?.offsetHeight ?? null
        box.style.height = `${next}px`
        // A number to run from: `top: auto` does not animate.
        if (panel) panel.style.top = '0px'
        return
      }
      // Layout sizes throughout: the panel is scaled while it opens, and a change can come
      // while the last one still runs (its drawn size then is neither the old nor the new).
      const to = panel.offsetHeight - box.offsetHeight + next
      const room = (window.innerHeight - to) / 2 - 16
      const y = base == null || room <= 0 ? 0 : Math.max(-room, Math.min((to - base) / 2, room))
      const animate = !reducedMotion()
      box.style.transition = animate ? HEIGHT_MOVE : ''
      box.style.overflow = animate ? 'hidden' : ''
      box.style.height = `${next}px`
      panel.style.transition = animate ? TOP_MOVE : ''
      panel.style.top = `${y}px`
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return (
    <div
      ref={outer}
      // Clipped only while it moves: focus rings at its edges show otherwise.
      onTransitionEnd={(e) => { if (e.target === outer.current && e.propertyName === 'height') outer.current.style.overflow = '' }}
    >
      <div ref={inner} className="flow-root">{children}</div>
    </div>
  )
}

/**
 * The search results' place. While a query stands it keeps the tallest height its results
 * have had, so results coming and going as the reader types do not bob the dialog; it lets
 * go when the query is cleared.
 */
function ResultsArea({ hold, className, outerClassName, children }: { hold: boolean; className?: string; outerClassName?: string; children: ReactNode }) {
  const outer = useRef<HTMLDivElement>(null)
  const inner = useRef<HTMLDivElement>(null)
  const tallest = useRef(0)
  useLayoutEffect(() => {
    const box = outer.current, el = inner.current
    if (!box || !el) return
    if (!hold) { tallest.current = 0; box.style.minHeight = ''; return }
    const apply = () => {
      const h = el.offsetHeight
      if (h > tallest.current) { tallest.current = h; box.style.minHeight = `${h}px` }
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [hold])
  return (
    <div ref={outer} className={cn('!mb-0', outerClassName)}>
      <div ref={inner} className={cn('flow-root', className)}>{children}</div>
    </div>
  )
}

function Thumb({ url, series = false }: { url: string | null; series?: boolean }) {
  // OpenLibrary answers a missing cover with a 1x1 image, not a 404: treat a
  // tiny image like a failed one and draw the placeholder icon instead.
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const show = !!url && failedUrl !== url
  const setFailed = () => setFailedUrl(url)
  return (
    <div className="w-11 h-16 rounded-[8px] bg-muted shrink-0 overflow-hidden flex items-center justify-center" style={{ boxShadow: 'var(--cover-bloom-fan)' }}>
      {show ? (
        <img
          src={url}
          alt=""
          className="w-full h-full object-cover"
          onError={setFailed}
          onLoad={e => { if (e.currentTarget.naturalWidth <= 2) setFailed() }}
        />
      ) : series ? (
        <Layers className="w-4 h-4 text-muted-foreground/60" />
      ) : (
        <BookOpen className="w-4 h-4 text-muted-foreground/60" />
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
  // The query the last finished search was for: "No results" only for that one, not
  // for a query whose search has not started yet.
  const [searchedFor, setSearchedFor] = useState('')
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
    // Series search is Hardcover-only - only offer the Series tab when available.
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
      setSearchedFor(q)
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
  const shownResults = searchMode === 'book' ? Math.min(candidates.length, 5) : seriesResults.length
  const noResults = !!query && !searching && searchedFor === query && shownResults === 0
  const hasResults = shownResults > 0 || noResults
  const step = manualMode ? 'manual' : selectedSeries ? 'series' : selected ? 'book' : 'search'
  // The first step shows with the dialog; only later changes rise in.
  const [lastStep, setLastStep] = useState(step)
  const [stepped, setStepped] = useState(false)
  if (lastStep !== step) { setLastStep(step); setStepped(true) }

  return (
    <ModalShell
      open={open}
      onClose={handleClose}
      size="md"
      title={<Trans>Add to wishlist</Trans>}
      bodyClassName="pt-1"
      footer={
        <>
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
            <Trans>Add to wishlist</Trans>
          </Pill>
        </>
      }
    >
      <SteadyHeight>
        <m.div
          key={step}
          initial={stepped ? STEP_IN : false}
          animate={{ opacity: 1, y: 0 }}
          transition={STEP_TRANSITION}
          className="space-y-4"
        >
          {inSearchState && (
            <>
              {/* Book / Series mode: Series only when Hardcover is configured */}
              {seriesAvailable && (
                <PillGroup
                  value={searchMode}
                  onChange={switchMode}
                  options={[
                    { value: 'book', label: <span className="inline-flex items-center gap-1.5"><BookOpen className="w-4 h-4" /><Trans>Book</Trans></span> },
                    { value: 'series', label: <span className="inline-flex items-center gap-1.5"><Layers className="w-4 h-4" /><Trans>Series</Trans></span> },
                  ]}
                />
              )}

              {/* Search box */}
              <div className="relative">
                <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                <Input
                  ref={inputRef}
                  value={query}
                  onChange={e => handleQueryChange(e.target.value)}
                  placeholder={searchMode === 'series' ? t`Search for a series…` : t`Search for a book…`}
                  aria-label={searchMode === 'series' ? t`Search for a series` : t`Search for a book`}
                  className="pl-10 pr-9"
                />
                {searching && (
                  <Loader2 className="absolute right-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground animate-spin" />
                )}
              </div>

              {/* Results, in a place that keeps its height while the query stands */}
              <ResultsArea
                hold={!!query}
                className={cn(hasResults && searchMode === 'book' && 'pb-4')}
                // Last in the column in Series mode: an empty one takes back the gap above it.
                outerClassName={cn(!hasResults && searchMode === 'series' && '-mt-4')}
              >
                {/* Book candidates */}
                {searchMode === 'book' && candidates.length > 0 && (
                  <div className="relative">
                    <AnimatePresence mode="popLayout" initial={false}>
                    {candidates.slice(0, 5).map((c, i) => (
                      <m.button
                        {...listItem}
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
                      </m.button>
                    ))}
                    </AnimatePresence>
                  </div>
                )}

                {/* Series results */}
                {searchMode === 'series' && seriesResults.length > 0 && (
                  <div className="relative">
                    <AnimatePresence mode="popLayout" initial={false}>
                    {seriesResults.map((s, i) => (
                      <m.button
                        {...listItem}
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
                      </m.button>
                    ))}
                    </AnimatePresence>
                  </div>
                )}

                {noResults && (
                  (searchMode === 'book' ? candidates.length === 0 : seriesResults.length === 0) && (
                    <p className="text-[14px] text-muted-foreground text-center py-4"><Trans>No results found.</Trans></p>
                  )
                )}
              </ResultsArea>

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
                <FormLabel>
                  <Trans>Title <span className="text-destructive">*</span></Trans>
                </FormLabel>
                <Input
                  value={manualTitle}
                  onChange={e => { setManualTitle(e.target.value); setError(null) }}
                  placeholder={t`Book title`}
                  autoFocus
                />
              </div>
              <div>
                <FormLabel><Trans>Author</Trans></FormLabel>
                <Input
                  value={manualAuthor}
                  onChange={e => setManualAuthor(e.target.value)}
                  placeholder={t`Author name`}
                />
              </div>
              <div>
                <FormLabel><Trans>Series</Trans></FormLabel>
                <Input
                  value={manualSeries}
                  onChange={e => setManualSeries(e.target.value)}
                  placeholder={t`Series name. Fill this alone to wish for the whole series.`}
                />
              </div>
              <div>
                <FormLabel><Trans>Note</Trans></FormLabel>
                <Textarea
                  value={manualNote}
                  onChange={e => setManualNote(e.target.value)}
                  placeholder={t`Optional note (e.g. 'the new one')`}
                  rows={2}
                  className="min-h-0 resize-none"
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

              <div className="flex items-start gap-4 py-3 border-y border-border/70">
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

              <PillGroup
                value={wishWholeSeries ? 'series' : 'volume'}
                onChange={v => {
                  if (v === 'volume') { setWishWholeSeries(false); return }
                  setWishWholeSeries(true)
                  setSeriesNameDraft(deriveSeriesName(selected))
                }}
                options={[
                  { value: 'volume', label: <Trans>This volume</Trans> },
                  { value: 'series', label: <span className="inline-flex items-center gap-1.5"><Layers className="w-4 h-4" /><Trans>Whole series</Trans></span> },
                ]}
              />

              <Unfold open={wishWholeSeries} gap={16}>
                <div>
                  <FormLabel><Trans>Series name</Trans></FormLabel>
                  <Input
                    value={seriesNameDraft}
                    onChange={e => { setSeriesNameDraft(e.target.value); setError(null) }}
                    placeholder={t`Series name`}
                    />
                  <p className="mt-1.5 text-[13px] text-muted-foreground"><Trans>For precise series tracking, use the <strong>Series</strong> tab instead.</Trans></p>
                </div>
              </Unfold>
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

              <div className="flex items-start gap-4 py-3 border-y border-border/70">
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

        </m.div>
        <Unfold open={!!error}>
          <p className="pt-4 text-[13px] text-destructive">{error}</p>
        </Unfold>
      </SteadyHeight>

    </ModalShell>
  )
}
