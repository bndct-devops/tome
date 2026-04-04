import { useEffect, useState, useRef } from 'react'
import { Loader2, Sparkles, BookOpen, ExternalLink, Check, X } from 'lucide-react'
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
  label: string
  current: string | null
  incoming: string | null
  checked: boolean
}

function candidateToFields(book: BookDetail, c: MetadataCandidate): FieldRow[] {
  return [
    { key: 'title', label: 'Title', current: book.title, incoming: c.title, checked: true },
    { key: 'author', label: 'Author', current: book.author, incoming: c.author, checked: true },
    { key: 'description', label: 'Description', current: book.description, incoming: c.description, checked: true },
    { key: 'publisher', label: 'Publisher', current: book.publisher, incoming: c.publisher, checked: true },
    { key: 'year', label: 'Year', current: book.year?.toString() ?? null, incoming: c.year?.toString() ?? null, checked: true },
    { key: 'language', label: 'Language', current: book.language, incoming: c.language, checked: true },
    { key: 'isbn', label: 'ISBN', current: book.isbn, incoming: c.isbn, checked: true },
    { key: 'series', label: 'Series', current: book.series, incoming: c.series, checked: true },
    { key: 'series_index', label: 'Series #', current: book.series_index?.toString() ?? null, incoming: c.series_index?.toString() ?? null, checked: true },
    { key: 'tags', label: 'Tags', current: book.tags.map(t => t.tag).join(', ') || null, incoming: c.tags.join(', ') || null, checked: true },
    { key: 'cover', label: 'Cover', current: null, incoming: c.cover_url, checked: false },
  ]
}

export function MetadataFetchModal({ book, open, onClose, onApplied }: Props) {
  const [step, setStep] = useState<Step>('search')
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<MetadataCandidate[]>([])
  const [selected, setSelected] = useState<MetadataCandidate | null>(null)
  const [fields, setFields] = useState<FieldRow[]>([])
  const [query, setQuery] = useState('')
  const queryRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setCandidates([])
      setError(null)
      setStep('search')
      setQuery('')
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
    setSelected(null)
    setFields([])
    setQuery('')
  }

  async function doSearch(q: string) {
    setLoading(true)
    setError(null)
    setCandidates([])
    try {
      const qs = q.trim() ? `?q=${encodeURIComponent(q)}` : ''
      const r = await fetch(`${API}/api/books/${book.id}/fetch-metadata${qs}`, {
        headers: authHeader(),
      })
      if (!r.ok) throw new Error(await r.text())
      const data: MetadataCandidate[] = await r.json()
      if (data.length === 0) {
        setError('No results found. Try a different search query.')
      } else {
        setCandidates(data)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Search failed')
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
      if (checkedKeys.has('description') && selected.description) body.description = selected.description
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
      reset()
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Apply failed')
    } finally {
      setApplying(false)
    }
  }

  function handleClose() {
    reset()
    onClose()
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" onClick={handleClose} />

      {/* Panel */}
      <div className="relative z-10 bg-background border border-border rounded-xl shadow-xl w-full max-w-3xl mx-4 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border sticky top-0 bg-background z-10">
          <div className="flex items-center gap-2 font-semibold text-sm">
            <Sparkles className="h-4 w-4 text-primary" />
            Fetch Metadata
            <span className="text-muted-foreground font-normal">— {book.title}</span>
          </div>
          <button onClick={handleClose} className="text-muted-foreground hover:text-foreground transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-6 space-y-4">
          {/* ── Search step ─────────────────────────────────────────── */}
          {step === 'search' && (
            <>
              <p className="text-sm text-muted-foreground">
                Searching for: <span className="font-medium text-foreground">{book.title}</span>
                {book.author && <> · <span className="font-medium text-foreground">{book.author}</span></>}
              </p>

              <div className="flex gap-2">
                <input
                  ref={queryRef}
                  className="flex h-9 w-full rounded-md border border-border bg-transparent px-3 py-1 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                  placeholder="Override search query (optional)…"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && doSearch(query)}
                />
                <button
                  onClick={() => doSearch(query)}
                  disabled={loading}
                  className="shrink-0 flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
                >
                  {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Search'}
                </button>
              </div>

              {error && <p className="text-sm text-destructive">{error}</p>}

              {loading && (
                <div className="flex justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              )}

              {!loading && candidates.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground">
                    {candidates.length} result{candidates.length !== 1 ? 's' : ''} — pick one to review
                  </p>
                  {candidates.map(c => (
                    <button
                      key={`${c.source}-${c.source_id}`}
                      className="w-full text-left rounded-lg border border-border bg-card p-3 hover:bg-accent transition-colors flex gap-3"
                      onClick={() => selectCandidate(c)}
                    >
                      {c.cover_url ? (
                        <img src={c.cover_url} alt="" className="h-20 w-14 object-cover rounded shrink-0" />
                      ) : (
                        <div className="h-20 w-14 rounded bg-muted flex items-center justify-center shrink-0">
                          <BookOpen className="h-6 w-6 text-muted-foreground" />
                        </div>
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-2">
                          <p className="font-medium line-clamp-2">{c.title}</p>
                          <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground bg-muted">
                            {c.source === 'hardcover' ? 'Hardcover' : c.source === 'google_books' ? 'Google' : 'OpenLib'}
                          </span>
                        </div>
                        {c.author && <p className="text-sm text-muted-foreground mt-0.5">{c.author}</p>}
                        {c.year && <p className="text-xs text-muted-foreground">{c.year}</p>}
                        {c.description && (
                          <p className="text-xs text-muted-foreground line-clamp-2 mt-1">{c.description}</p>
                        )}
                        {c.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {c.tags.slice(0, 4).map(t => (
                              <span key={t} className="text-xs bg-muted rounded px-1">{t}</span>
                            ))}
                            {c.tags.length > 4 && (
                              <span className="text-xs text-muted-foreground">+{c.tags.length - 4}</span>
                            )}
                          </div>
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </>
          )}

          {/* ── Diff step ───────────────────────────────────────────── */}
          {step === 'diff' && selected && (
            <>
              <div className="flex items-center justify-between">
                <button
                  onClick={() => { setStep('search'); setSelected(null); setFields([]) }}
                  className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                  ← Back to results
                </button>
                <label className="flex items-center gap-2 text-sm font-medium cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={allChecked()}
                    onChange={toggleAll}
                    className="rounded border-border"
                  />
                  Select all
                </label>
              </div>

              <div className="rounded-lg border border-border overflow-hidden">
                {/* Header row */}
                <div className="grid grid-cols-[24px_100px_1fr_1fr] gap-3 items-center bg-muted px-3 py-2 text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
                  <span />
                  <span>Field</span>
                  <span>Current</span>
                  <span>
                    Incoming
                    <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded border border-border bg-background normal-case tracking-normal">
                      {selected.source === 'hardcover' ? 'Hardcover' : selected.source === 'google_books' ? 'Google Books' : 'Open Library'}
                    </span>
                  </span>
                </div>

                {fields.map((f, i) => {
                  const hasChange = !!(f.incoming && f.incoming !== f.current)
                  return (
                    <div
                      key={f.key}
                      className={[
                        'grid grid-cols-[24px_100px_1fr_1fr] gap-3 items-start px-3 py-2.5 text-sm',
                        i > 0 ? 'border-t border-border/50' : '',
                        !hasChange ? 'opacity-50' : '',
                      ].join(' ')}
                    >
                      <input
                        type="checkbox"
                        checked={f.checked}
                        onChange={() => toggleField(f.key)}
                        disabled={!hasChange}
                        className="mt-0.5 rounded border-border"
                      />
                      <span className="font-medium text-muted-foreground text-xs mt-0.5">{f.label}</span>

                      {/* Current value */}
                      {f.key === 'cover' ? (
                        <span className="text-muted-foreground text-xs italic mt-0.5">
                          {book.cover_path ? 'Has cover' : 'No cover'}
                        </span>
                      ) : (
                        <span className={`break-words line-clamp-3 text-xs ${hasChange ? 'line-through text-muted-foreground' : 'text-foreground'}`}>
                          {f.current ?? <span className="text-muted-foreground/50 italic">—</span>}
                        </span>
                      )}

                      {/* Incoming value */}
                      {f.key === 'cover' ? (
                        f.incoming ? (
                          <div className="flex items-start gap-2">
                            <img
                              src={f.incoming}
                              alt="New cover"
                              className="h-16 w-12 object-cover rounded border border-border"
                            />
                            <a
                              href={f.incoming}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-xs text-primary hover:underline flex items-center gap-1 mt-0.5"
                            >
                              Preview <ExternalLink className="h-3 w-3" />
                            </a>
                          </div>
                        ) : (
                          <span className="text-muted-foreground/50 italic text-xs mt-0.5">No cover available</span>
                        )
                      ) : (
                        <span className={`break-words line-clamp-3 text-xs ${hasChange ? 'text-primary font-medium' : 'text-foreground'}`}>
                          {f.incoming ?? <span className="text-muted-foreground/50 italic">—</span>}
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>

              {error && <p className="text-sm text-destructive">{error}</p>}

              <div className="flex justify-end gap-2 pt-1">
                <button
                  onClick={handleClose}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium border border-border bg-card text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
                >
                  <X className="h-3.5 w-3.5" /> Cancel
                </button>
                <button
                  onClick={applySelected}
                  disabled={applying || fields.every(f => !f.checked)}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
                >
                  {applying
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <Check className="h-3.5 w-3.5" />}
                  Apply Selected
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
