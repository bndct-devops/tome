import { useEffect, useState, useRef } from 'react'
import { Trans } from '@lingui/react/macro'
import { t, msg, plural } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'
import { Loader2, BookOpen, ExternalLink, Check, ChevronLeft, Search } from 'lucide-react'
import { BookAnimation } from '@/components/BookAnimation'
import { ModalShell } from '@/components/ModalShell'
import { Checkbox, Input, Pill } from '@/components/page'
import { cn, stripMarkdown } from '@/lib/utils'
import type { BookDetail, MetadataCandidate } from '@/lib/books'

const API = import.meta.env.VITE_API_URL ?? ''

function authHeader(): Record<string, string> {
  const token = localStorage.getItem('tome_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

interface Props {
  book: BookDetail
  open: boolean
  onClose: () => void
  onApplied: (updated: BookDetail) => void
}

type Step = 'search' | 'diff'

interface FieldRow {
  key: string
  label: MessageDescriptor
  current: string | null
  incoming: string | null
  checked: boolean
}

function candidateToFields(book: BookDetail, c: MetadataCandidate): FieldRow[] {
  return [
    { key: 'title', label: msg`Title`, current: book.title, incoming: c.title, checked: true },
    { key: 'author', label: msg`Author`, current: book.author, incoming: c.author, checked: true },
    { key: 'description', label: msg`Description`, current: book.description, incoming: c.description ? stripMarkdown(c.description) : null, checked: true },
    { key: 'publisher', label: msg`Publisher`, current: book.publisher, incoming: c.publisher, checked: true },
    { key: 'year', label: msg`Year`, current: book.year?.toString() ?? null, incoming: c.year?.toString() ?? null, checked: true },
    { key: 'language', label: msg`Language`, current: book.language, incoming: c.language, checked: true },
    { key: 'isbn', label: msg`ISBN`, current: book.isbn, incoming: c.isbn, checked: true },
    { key: 'series', label: msg`Series`, current: book.series, incoming: c.series, checked: true },
    { key: 'series_index', label: msg`Series #`, current: book.series_index?.toString() ?? null, incoming: c.series_index?.toString() ?? null, checked: true },
    { key: 'tags', label: msg`Tags`, current: book.tags.map(t => t.tag).join(', ') || null, incoming: c.tags.join(', ') || null, checked: true },
    { key: 'cover', label: msg`Cover`, current: null, incoming: c.cover_url, checked: false },
  ]
}

export function MetadataFetchModal({ book, open, onClose, onApplied }: Props) {
  const [step, setStep] = useState<Step>('search')
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<MetadataCandidate[]>([])
  const [sourceNotice, setSourceNotice] = useState<string | null>(null)
  const [selected, setSelected] = useState<MetadataCandidate | null>(null)
  const [fields, setFields] = useState<FieldRow[]>([])
  const [query, setQuery] = useState('')
  const queryRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      // Full reset on open (not close) so content survives the exit animation.
      reset()
      setTimeout(() => doSearch(''), 50)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  function reset() {
    setStep('search')
    setLoading(false)
    setApplying(false)
    setError(null)
    setCandidates([])
    setSourceNotice(null)
    setSelected(null)
    setFields([])
    setQuery('')
  }

  async function doSearch(q: string) {
    // The previous results stay (dimmed) until the new ones land.
    setLoading(true)
    setError(null)
    setSourceNotice(null)
    try {
      const qs = q.trim() ? `?q=${encodeURIComponent(q)}` : ''
      const r = await fetch(`${API}/api/books/${book.id}/fetch-metadata${qs}`, {
        headers: authHeader(),
      })
      if (!r.ok) throw new Error(await r.text())
      const data: { candidates: MetadataCandidate[]; sources?: Record<string, string> } = await r.json()

      // Be honest about degraded sources: "no results" used to swallow 429s.
      /* eslint-disable lingui/no-unlocalized-strings -- provider names */
      const labels: Record<string, string> = {
        hardcover: 'Hardcover', google_books: 'Google Books', open_library: 'Open Library', anilist: 'AniList',
      }
      /* eslint-enable lingui/no-unlocalized-strings */
      const degraded = Object.entries(data.sources ?? {})
        .filter(([, s]) => s === 'rate_limited' || s === 'timeout' || s === 'error')
        .map(([k, s]) => { const name = labels[k] ?? k; return s === 'rate_limited' ? t`${name} is rate-limited, try again in a minute` : s === 'timeout' ? t`${name} timed out` : t`${name} errored` })
      if (degraded.length) setSourceNotice(degraded.join(' · '))

      setCandidates(data.candidates)
      if (data.candidates.length === 0) {
        setError(degraded.length
          ? t`No results, but some sources were unavailable (see below). Retrying may help.`
          : t`No results found. Try a different search query.`)
      }
    } catch (e: unknown) {
      setCandidates([])
      setError(e instanceof Error ? e.message : t`Search failed`)
    } finally {
      setLoading(false)
    }
  }

  function selectCandidate(c: MetadataCandidate) {
    setSelected(c)
    setFields(candidateToFields(book, c))
    setStep('diff')
  }

  function toggleField(key: string) {
    setFields(prev => prev.map(f => f.key === key ? { ...f, checked: !f.checked } : f))
  }

  function allChecked() {
    return fields.every(f => f.checked)
  }

  function toggleAll() {
    const next = !allChecked()
    setFields(prev => prev.map(f => ({ ...f, checked: next })))
  }

  async function applySelected() {
    if (!selected) return
    setApplying(true)
    setError(null)
    try {
      const checkedKeys = new Set(fields.filter(f => f.checked).map(f => f.key))
      const body: Record<string, unknown> = {}

      if (checkedKeys.has('title') && selected.title) body.title = selected.title
      if (checkedKeys.has('author') && selected.author) body.author = selected.author
      if (checkedKeys.has('description') && selected.description) body.description = stripMarkdown(selected.description)
      if (checkedKeys.has('publisher') && selected.publisher) body.publisher = selected.publisher
      if (checkedKeys.has('year') && selected.year != null) body.year = selected.year
      if (checkedKeys.has('language') && selected.language) body.language = selected.language
      if (checkedKeys.has('isbn') && selected.isbn) body.isbn = selected.isbn
      if (checkedKeys.has('series') && selected.series) body.series = selected.series
      if (checkedKeys.has('series_index') && selected.series_index != null) body.series_index = selected.series_index
      if (checkedKeys.has('tags')) body.tags = selected.tags
      if (checkedKeys.has('cover') && selected.cover_url) body.cover_url = selected.cover_url

      const r = await fetch(`${API}/api/books/${book.id}/apply-metadata`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader() },
        body: JSON.stringify(body),
      })
      if (!r.ok) throw new Error(await r.text())
      const updated: BookDetail = await r.json()
      onApplied(updated)
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t`Apply failed`)
    } finally {
      setApplying(false)
    }
  }

  function handleClose() {
    onClose()
  }

  const sourceName = (src: string, short = false) =>
    /* eslint-disable lingui/no-unlocalized-strings -- provider names */
    src === 'hardcover' ? 'Hardcover'
      : src === 'google_books' ? (short ? 'Google' : 'Google Books')
        : src === 'anilist' ? 'AniList'
          : 'Open Library'
    /* eslint-enable lingui/no-unlocalized-strings */

  const footer = step === 'diff' && selected ? (
    <>
      <Pill variant="quiet" onClick={handleClose}><Trans>Cancel</Trans></Pill>
      <Pill variant="primary" onClick={applySelected} disabled={applying || fields.every(f => !f.checked)}>
        {applying ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
        <Trans>Apply selected</Trans>
      </Pill>
    </>
  ) : (
    <Pill variant="quiet" onClick={handleClose}><Trans>Cancel</Trans></Pill>
  )

  return (
    <ModalShell
      open={open}
      onClose={handleClose}
      title={<Trans>Fetch metadata</Trans>}
      eyebrow={book.title}
      size="lg"
      footer={footer}
    >
      {/* ── Search step ─────────────────────────────────────────── */}
      {step === 'search' && (
        <div className="flex flex-col gap-4">
          <p className="text-[14px] text-muted-foreground">
            {(() => { const title = book.title; return <Trans>Searching for: <span className="font-medium text-foreground">{title}</span></Trans> })()}
            {book.author && <> · <span className="font-medium text-foreground">{book.author}</span></>}
          </p>

          <div className="flex gap-2">
            <Input
              ref={queryRef}
              placeholder={t`Override search query (optional)…`}
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && doSearch(query)}
              aria-label={t`Search query`}
            />
            <Pill onClick={() => doSearch(query)} disabled={loading} className="shrink-0">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              <Trans>Search</Trans>
            </Pill>
          </div>

          {error && <p className="text-[14px] text-destructive">{error}</p>}

          {loading && candidates.length === 0 && (
            <div className="flex flex-col items-center justify-center gap-3 py-8">
              <BookAnimation variant="refresh" className="block h-12 w-12 text-primary" />
              <p className="text-[14px] text-muted-foreground"><Trans>Searching…</Trans></p>
            </div>
          )}

          {!loading && sourceNotice && (
            <p className="text-[13px] text-warning">{sourceNotice}</p>
          )}

          {candidates.length > 0 && (
            <div className={cn('transition-opacity', loading && 'pointer-events-none opacity-50')}>
              <p className="mb-1 text-[13px] text-muted-foreground">
                {plural(candidates.length, { one: '# result, pick one to review', other: '# results, pick one to review' })}
              </p>
              <ul className="border-t border-border/70">
                {candidates.map((c, i) => (
                  <li key={`${c.source}-${c.source_id}`} className="border-b border-border/70">
                    <button
                      type="button"
                      data-candidate
                      className="-mx-3 flex w-[calc(100%+1.5rem)] gap-3.5 rounded-lg px-3 py-3 text-left transition-colors hover:bg-secondary/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      onClick={() => selectCandidate(c)}
                    >
                      {c.cover_url ? (
                        <img src={c.cover_url} alt="" className="h-[72px] w-12 shrink-0 rounded-[8px] object-cover" style={{ boxShadow: 'var(--cover-bloom-fan)' }} />
                      ) : (
                        <div className="flex h-[72px] w-12 shrink-0 items-center justify-center rounded-[8px] bg-secondary">
                          <BookOpen className="h-5 w-5 text-muted-foreground" />
                        </div>
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-x-2 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
                          <span>{sourceName(c.source)}</span>
                          {i === 0 && <span className="text-primary"><Trans>Best match</Trans></span>}
                        </div>
                        <p className="mt-0.5 line-clamp-2 text-[15px] font-medium text-foreground">{c.title}</p>
                        {(c.author || c.year) && (
                          <p className="text-[14px] text-muted-foreground">
                            {[c.author, c.year].filter(Boolean).join(' · ')}
                          </p>
                        )}
                        {c.description && (
                          <p className="mt-1 line-clamp-2 text-[13px] text-muted-foreground">{stripMarkdown(c.description)}</p>
                        )}
                        {c.tags.length > 0 && (
                          <p className="mt-1 truncate text-[13px] text-muted-foreground/80">
                            {c.tags.slice(0, 4).join(' · ')}
                            {c.tags.length > 4 && ` +${c.tags.length - 4}`}
                          </p>
                        )}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* ── Diff step ───────────────────────────────────────────── */}
      {step === 'diff' && selected && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <Pill
              variant="quiet"
              className="-ml-3"
              onClick={() => { setStep('search'); setSelected(null); setFields([]) }}
            >
              <ChevronLeft className="h-4 w-4" />
              <Trans>Back to results</Trans>
            </Pill>
            <Checkbox checked={allChecked()} onChange={toggleAll} label={<Trans>Select all</Trans>} />
          </div>

          <p className="text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground sm:hidden">
            {(() => { const src = sourceName(selected.source); return <Trans>Incoming from {src}</Trans> })()}
          </p>

          <div className="border-t border-border/70">
            {/* Header row */}
            <div className="hidden grid-cols-[24px_100px_minmax(0,1fr)_minmax(0,1fr)] items-center gap-3 border-b border-border/70 py-2 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground sm:grid">
              <span />
              <span><Trans>Field</Trans></span>
              <span><Trans>Current</Trans></span>
              <span className="truncate">
                <Trans>Incoming</Trans>
                <span className="ml-1.5 text-primary">{sourceName(selected.source)}</span>
              </span>
            </div>

            {fields.map(f => {
              const hasChange = !!(f.incoming && f.incoming !== f.current)
              return (
                <div
                  key={f.key}
                  className={cn(
                    'grid grid-cols-[24px_minmax(0,1fr)] items-start gap-x-3 gap-y-1 border-b border-border/70 py-3 sm:grid-cols-[24px_100px_minmax(0,1fr)_minmax(0,1fr)]',
                    !hasChange && 'opacity-50',
                  )}
                >
                  <Checkbox
                    checked={f.checked}
                    onChange={() => toggleField(f.key)}
                    disabled={!hasChange}
                    aria-label={i18n._(f.label)}
                  />
                  <span className="pt-px text-[13px] font-medium text-muted-foreground">{i18n._(f.label)}</span>

                  {/* Current value */}
                  {f.key === 'cover' ? (
                    <span className="col-start-2 text-[13px] text-muted-foreground sm:col-start-auto">
                      {book.cover_path ? t`Has cover` : t`No cover`}
                    </span>
                  ) : (
                    <span className={cn('col-start-2 line-clamp-3 break-words text-[14px] sm:col-start-auto', hasChange ? 'text-muted-foreground line-through' : 'text-foreground')}>
                      {f.current ?? <span className="text-muted-foreground/50">-</span>}
                    </span>
                  )}

                  {/* Incoming value */}
                  {f.key === 'cover' ? (
                    f.incoming ? (
                      <div className="col-start-2 flex min-w-0 items-start gap-2.5 sm:col-start-auto">
                        <img
                          src={f.incoming}
                          alt={t`New cover`}
                          className="h-16 w-11 shrink-0 rounded-[6px] object-cover"
                        />
                        <a
                          href={f.incoming}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex shrink-0 items-center gap-1 text-[13px] text-primary hover:underline"
                        >
                          <Trans>Preview</Trans> <ExternalLink className="h-3 w-3" />
                        </a>
                      </div>
                    ) : (
                      <span className="col-start-2 text-[13px] text-muted-foreground/60 sm:col-start-auto"><Trans>No cover available</Trans></span>
                    )
                  ) : (
                    <span className={cn('col-start-2 line-clamp-3 break-words text-[14px] sm:col-start-auto', hasChange ? 'font-medium text-primary' : 'text-foreground')}>
                      {f.incoming ?? <span className="text-muted-foreground/50">-</span>}
                    </span>
                  )}
                </div>
              )
            })}
          </div>

          {error && <p className="text-[14px] text-destructive">{error}</p>}
        </div>
      )}
    </ModalShell>
  )
}
