import { useEffect, useState, useRef, useCallback } from 'react'
import {
  Loader2, Search, X, ChevronUp, ChevronDown, BookOpen,
  Check, ArrowLeft, ArrowRight, SkipForward, RefreshCw,
  ExternalLink, Sparkles, Filter, Wand2, Upload, BookMarked,
} from 'lucide-react'
import { api } from '@/lib/api'
import type { BookDetail, BookType, Library, MetadataCandidate } from '@/lib/books'
import { cn } from '@/lib/utils'
import { BulkMetadataReviewModal } from './BulkMetadataReviewModal'
import { UploadModal } from './UploadModal'

const API = import.meta.env.VITE_API_URL ?? ''

function authHeader(): Record<string, string> {
  const token = localStorage.getItem('tome_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface AuditBook {
  id: number
  title: string
  subtitle: string
  author: string
  series: string
  series_index: number | null
  year: number | null
  description_snippet: string
  isbn: string
  language: string
  publisher: string
  cover_path: string | null
  book_type_label: string | null
  book_type_id: number | null
  content_type: string
  library_ids: number[]
  fields_present: Record<string, boolean>
  completeness: number
  completeness_total: number
}

type SortCol = 'title' | 'author' | 'series' | 'year' | 'completeness'
type SortDir = 'asc' | 'desc'

const MISSING_FIELD_OPTIONS = [
  { key: 'description', label: 'Description' },
  { key: 'cover', label: 'Cover' },
  { key: 'isbn', label: 'ISBN' },
  { key: 'year', label: 'Year' },
  { key: 'author', label: 'Author' },
  { key: 'series', label: 'Series' },
  { key: 'language', label: 'Language' },
  { key: 'publisher', label: 'Publisher' },
]

// ── FieldRow (shared with review flow) ───────────────────────────────────────

interface FieldRow {
  key: string
  label: string
  current: string | null
  incoming: string | null
  checked: boolean
}

function buildFieldRows(book: BookDetail, c: MetadataCandidate): FieldRow[] {
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

// ── Completeness badge ────────────────────────────────────────────────────────

function CompletenessBadge({ score, total }: { score: number; total: number }) {
  const pct = score / total
  const color = pct >= 0.8 ? 'text-green-600 dark:text-green-400' : pct >= 0.5 ? 'text-yellow-600 dark:text-yellow-400' : 'text-red-600 dark:text-red-400'
  return <span className={cn('text-xs font-mono font-medium', color)}>{score}/{total}</span>
}

// ── Presence dot ─────────────────────────────────────────────────────────────

function Dot({ present }: { present: boolean }) {
  return (
    <span className={cn('inline-block w-2 h-2 rounded-full', present ? 'bg-green-500' : 'bg-red-400')} />
  )
}

// ── Inline editable cell ──────────────────────────────────────────────────────

interface EditCellProps {
  value: string
  bookId: number
  field: string
  onSaved: (id: number, field: string, value: string) => void
}

function EditCell({ value, bookId, field, onSaved }: EditCellProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function startEdit() {
    setDraft(value)
    setEditing(true)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  async function commit() {
    if (draft === value) { setEditing(false); return }
    setSaving(true)
    try {
      await api.put(`/books/${bookId}`, { [field]: draft || null })
      onSaved(bookId, field, draft)
      setSaved(true)
      setTimeout(() => setSaved(false), 1500)
    } catch {
      setDraft(value)
    } finally {
      setSaving(false)
      setEditing(false)
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') commit()
    if (e.key === 'Escape') { setDraft(value); setEditing(false) }
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={onKeyDown}
        className="w-full bg-background border border-primary rounded px-1 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
      />
    )
  }

  return (
    <span
      onClick={startEdit}
      className="cursor-pointer hover:bg-accent rounded px-1 py-0.5 text-xs block truncate group relative"
      title={value || undefined}
    >
      {saving && <Loader2 className="inline h-3 w-3 animate-spin mr-1" />}
      {saved && <Check className="inline h-3 w-3 text-green-500 mr-1" />}
      {value || <span className="text-muted-foreground/40 italic">—</span>}
    </span>
  )
}

// ── Review Flow ───────────────────────────────────────────────────────────────

interface ReviewFlowProps {
  queue: number[]
  onBack: () => void
  onBookUpdated: (id: number) => void
}

function ReviewFlow({ queue, onBack, onBookUpdated }: ReviewFlowProps) {
  const [idx, setIdx] = useState(0)
  const [book, setBook] = useState<BookDetail | null>(null)
  const [loadingBook, setLoadingBook] = useState(false)
  const [candidates, setCandidates] = useState<MetadataCandidate[]>([])
  const [loadingCandidates, setLoadingCandidates] = useState(false)
  const [candidateError, setCandidateError] = useState<string | null>(null)
  const [selectedCandidate, setSelectedCandidate] = useState<MetadataCandidate | null>(null)
  const [fields, setFields] = useState<FieldRow[]>([])
  const [draft, setDraft] = useState<Partial<BookDetail>>({})
  const [customQuery, setCustomQuery] = useState('')
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [summary, setSummary] = useState<{ applied: number; skipped: number } | null>(null)
  const appliedCount = useRef(0)

  const bookId = queue[idx]

  const loadBook = useCallback(async (id: number) => {
    setLoadingBook(true)
    setBook(null)
    setSelectedCandidate(null)
    setFields([])
    setDraft({})
    setCustomQuery('')
    setApplyError(null)
    try {
      const b = await api.get<BookDetail>(`/books/${id}`)
      setBook(b)
    } finally {
      setLoadingBook(false)
    }
  }, [])

  const fetchCandidates = useCallback(async (id: number, q?: string) => {
    setLoadingCandidates(true)
    setCandidates([])
    setCandidateError(null)
    setSelectedCandidate(null)
    setFields([])
    try {
      const qs = q?.trim() ? `?q=${encodeURIComponent(q)}` : ''
      const data = await fetch(`${API}/api/books/${id}/fetch-metadata${qs}`, {
        headers: authHeader(),
      }).then(r => r.json()) as MetadataCandidate[]
      setCandidates(data)
      if (data.length > 0 && book) {
        setSelectedCandidate(data[0])
        setFields(buildFieldRows(book, data[0]))
      } else if (data.length === 0) {
        setCandidateError('No candidates found.')
      }
    } catch {
      setCandidateError('Failed to fetch candidates.')
    } finally {
      setLoadingCandidates(false)
    }
  }, [book])

  // Load book on idx change
  useEffect(() => {
    if (bookId) loadBook(bookId)
  }, [bookId, loadBook])

  // Auto-fetch candidates after book loads
  useEffect(() => {
    if (book) fetchCandidates(book.id)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [book?.id])

  function selectCandidate(c: MetadataCandidate) {
    if (!book) return
    setSelectedCandidate(c)
    setFields(buildFieldRows(book, c))
  }

  function toggleField(key: string) {
    setFields(prev => prev.map(f => f.key === key ? { ...f, checked: !f.checked } : f))
  }

  function toggleAll() {
    const allChecked = fields.every(f => f.checked)
    setFields(prev => prev.map(f => ({ ...f, checked: !allChecked })))
  }

  function advance() {
    if (idx + 1 >= queue.length) {
      setSummary({ applied: appliedCount.current, skipped: queue.length - appliedCount.current })
    } else {
      setIdx(i => i + 1)
    }
  }

  async function applyAndNext() {
    if (!book) return
    setApplying(true)
    setApplyError(null)
    try {
      // Build body from checked candidate fields + manual draft overrides
      const checkedKeys = new Set(fields.filter(f => f.checked).map(f => f.key))
      const body: Record<string, unknown> = { ...draft }

      if (selectedCandidate) {
        if (checkedKeys.has('title') && selectedCandidate.title && !draft.title) body.title = selectedCandidate.title
        if (checkedKeys.has('author') && selectedCandidate.author && !draft.author) body.author = selectedCandidate.author
        if (checkedKeys.has('description') && selectedCandidate.description && !draft.description) body.description = selectedCandidate.description
        if (checkedKeys.has('publisher') && selectedCandidate.publisher && !draft.publisher) body.publisher = selectedCandidate.publisher
        if (checkedKeys.has('year') && selectedCandidate.year != null && !draft.year) body.year = selectedCandidate.year
        if (checkedKeys.has('language') && selectedCandidate.language && !draft.language) body.language = selectedCandidate.language
        if (checkedKeys.has('isbn') && selectedCandidate.isbn && !draft.isbn) body.isbn = selectedCandidate.isbn
        if (checkedKeys.has('series') && selectedCandidate.series && !draft.series) body.series = selectedCandidate.series
        if (checkedKeys.has('series_index') && selectedCandidate.series_index != null && draft.series_index == null) body.series_index = selectedCandidate.series_index
        if (checkedKeys.has('tags')) body.tags = selectedCandidate.tags
        if (checkedKeys.has('cover') && selectedCandidate.cover_url) body.cover_url = selectedCandidate.cover_url
      }

      // Apply manual edits even without a candidate
      if (Object.keys(body).length > 0) {
        const endpoint = selectedCandidate && Object.keys(body).length > 0 ? 'apply-metadata' : null
        if (endpoint && selectedCandidate) {
          await fetch(`${API}/api/books/${book.id}/apply-metadata`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...authHeader() },
            body: JSON.stringify(body),
          })
        } else if (Object.keys(draft).length > 0) {
          await api.put(`/books/${book.id}`, draft)
        }
        onBookUpdated(book.id)
        appliedCount.current += 1
      }
      advance()
    } catch {
      setApplyError('Failed to apply metadata.')
    } finally {
      setApplying(false)
    }
  }

  function skip() {
    advance()
  }

  function prev() {
    if (idx > 0) setIdx(i => i - 1)
  }

  // Summary screen
  if (summary) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-4 text-center">
        <Check className="w-12 h-12 text-green-500" />
        <h2 className="text-lg font-semibold">Review complete</h2>
        <p className="text-sm text-muted-foreground">
          Reviewed {queue.length} books — applied changes to {summary.applied}, skipped {summary.skipped}.
        </p>
        <button
          onClick={onBack}
          className="mt-4 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition-all"
        >
          Back to Table
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Progress header */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-border gap-4">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="w-4 h-4" /> Back to Table
        </button>
        <div className="flex items-center gap-3 flex-1 max-w-xs">
          <span className="text-xs text-muted-foreground whitespace-nowrap">
            Book {idx + 1} of {queue.length}
          </span>
          <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all"
              style={{ width: `${((idx + 1) / queue.length) * 100}%` }}
            />
          </div>
        </div>
        <div className="w-24" />
      </div>

      {loadingBook && (
        <div className="flex items-center justify-center py-24">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {book && !loadingBook && (
        <div className="flex flex-col lg:flex-row flex-1 overflow-hidden">
          {/* Left: current metadata */}
          <div className="w-full lg:w-[420px] shrink-0 border-b lg:border-b-0 lg:border-r border-border overflow-y-auto p-4 lg:p-6 space-y-4 max-h-[40vh] lg:max-h-none">
            <div className="flex gap-4">
              {book.cover_path ? (
                <img
                  src={`${API}/api/books/${book.id}/cover`}
                  alt=""
                  className="w-20 h-28 object-cover rounded border border-border shrink-0"
                />
              ) : (
                <div className="w-20 h-28 rounded border border-border bg-muted flex items-center justify-center shrink-0">
                  <BookOpen className="w-6 h-6 text-muted-foreground" />
                </div>
              )}
              <div className="min-w-0">
                <p className="font-semibold text-sm leading-snug">{book.title}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{book.author || <span className="italic">No author</span>}</p>
              </div>
            </div>

            <div className="space-y-2">
              {[
                { field: 'title', label: 'Title', value: book.title },
                { field: 'subtitle', label: 'Subtitle', value: book.subtitle || '' },
                { field: 'author', label: 'Author', value: book.author || '' },
                { field: 'series', label: 'Series', value: book.series || '' },
                { field: 'series_index', label: 'Series #', value: book.series_index?.toString() || '' },
                { field: 'year', label: 'Year', value: book.year?.toString() || '' },
                { field: 'isbn', label: 'ISBN', value: book.isbn || '' },
                { field: 'language', label: 'Language', value: book.language || '' },
                { field: 'publisher', label: 'Publisher', value: book.publisher || '' },
              ].map(({ field, label, value }) => (
                <div key={field} className="grid grid-cols-[80px_1fr] gap-2 items-center">
                  <span className="text-xs text-muted-foreground">{label}</span>
                  <input
                    value={draft[field as keyof BookDetail] as string ?? value}
                    onChange={e => setDraft(d => ({ ...d, [field]: e.target.value }))}
                    className="text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                </div>
              ))}
              <div className="grid grid-cols-[80px_1fr] gap-2">
                <span className="text-xs text-muted-foreground pt-1">Description</span>
                <textarea
                  value={(draft.description as string | undefined) ?? book.description ?? ''}
                  onChange={e => setDraft(d => ({ ...d, description: e.target.value }))}
                  rows={4}
                  className="text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring resize-none"
                />
              </div>
            </div>
          </div>

          {/* Right: candidates + diff */}
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            {/* Fetch again bar */}
            <div className="flex gap-2">
              <input
                value={customQuery}
                onChange={e => setCustomQuery(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && fetchCandidates(book.id, customQuery)}
                placeholder="Override search query…"
                className="flex-1 h-8 text-xs bg-background border border-border rounded px-2 focus:outline-none focus:ring-1 focus:ring-ring"
              />
              <button
                onClick={() => fetchCandidates(book.id, customQuery)}
                disabled={loadingCandidates}
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-muted hover:bg-accent border border-border transition-all disabled:opacity-50"
              >
                {loadingCandidates ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                Fetch
              </button>
            </div>

            {loadingCandidates && (
              <div className="flex justify-center py-8">
                <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
              </div>
            )}

            {candidateError && !loadingCandidates && (
              <p className="text-sm text-muted-foreground text-center py-4">{candidateError}</p>
            )}

            {/* Candidate list */}
            {candidates.length > 0 && !loadingCandidates && (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">{candidates.length} candidate{candidates.length !== 1 ? 's' : ''} — click to select</p>
                {candidates.map(c => (
                  <button
                    key={`${c.source}-${c.source_id}`}
                    onClick={() => selectCandidate(c)}
                    className={cn(
                      'w-full text-left rounded-lg border p-2.5 flex gap-2.5 transition-colors',
                      selectedCandidate?.source_id === c.source_id && selectedCandidate?.source === c.source
                        ? 'border-primary bg-primary/5'
                        : 'border-border bg-card hover:bg-accent'
                    )}
                  >
                    {c.cover_url ? (
                      <img src={c.cover_url} alt="" className="h-14 w-10 object-cover rounded shrink-0" />
                    ) : (
                      <div className="h-14 w-10 rounded bg-muted flex items-center justify-center shrink-0">
                        <BookOpen className="w-4 h-4 text-muted-foreground" />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-1">
                        <p className="text-xs font-medium line-clamp-1">{c.title}</p>
                        <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground bg-muted">
                          {c.source === 'hardcover' ? 'HC' : c.source === 'google_books' ? 'GB' : 'OL'}
                        </span>
                      </div>
                      {c.author && <p className="text-[11px] text-muted-foreground">{c.author}</p>}
                      {c.year && <p className="text-[10px] text-muted-foreground">{c.year}</p>}
                    </div>
                  </button>
                ))}
              </div>
            )}

            {/* Diff table */}
            {selectedCandidate && fields.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Field diff</p>
                  <label className="flex items-center gap-1.5 text-xs cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={fields.every(f => f.checked)}
                      onChange={toggleAll}
                      className="rounded border-border"
                    />
                    All
                  </label>
                </div>
                <div className="rounded-lg border border-border overflow-hidden">
                  <div className="grid grid-cols-[20px_60px_1fr_1fr] sm:grid-cols-[20px_80px_1fr_1fr] gap-2 items-center bg-muted px-3 py-1.5 text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                    <span />
                    <span>Field</span>
                    <span>Current</span>
                    <span>Incoming</span>
                  </div>
                  {fields.map((f, i) => {
                    const hasChange = !!(f.incoming && f.incoming !== f.current)
                    return (
                      <div
                        key={f.key}
                        className={cn(
                          'grid grid-cols-[20px_60px_1fr_1fr] sm:grid-cols-[20px_80px_1fr_1fr] gap-2 items-start px-3 py-2 text-xs',
                          i > 0 && 'border-t border-border/50',
                          !hasChange && 'opacity-40'
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={f.checked}
                          onChange={() => toggleField(f.key)}
                          disabled={!hasChange}
                          className="mt-0.5 rounded border-border"
                        />
                        <span className="text-muted-foreground text-[11px] mt-0.5">{f.label}</span>
                        {f.key === 'cover' ? (
                          <span className="text-muted-foreground italic text-[11px]">
                            {book.cover_path ? 'Has cover' : 'No cover'}
                          </span>
                        ) : (
                          <span className={cn('break-words line-clamp-2 text-[11px]', hasChange ? 'line-through text-muted-foreground' : '')}>
                            {f.current ?? <span className="italic text-muted-foreground/40">—</span>}
                          </span>
                        )}
                        {f.key === 'cover' ? (
                          f.incoming ? (
                            <div className="flex items-start gap-1.5">
                              <img src={f.incoming} alt="" className="h-12 w-8 object-cover rounded border border-border" />
                              <a href={f.incoming} target="_blank" rel="noopener noreferrer" className="text-[11px] text-primary hover:underline flex items-center gap-0.5 mt-0.5">
                                <ExternalLink className="w-3 h-3" />
                              </a>
                            </div>
                          ) : (
                            <span className="italic text-muted-foreground/40 text-[11px]">—</span>
                          )
                        ) : (
                          <span className={cn('break-words line-clamp-2 text-[11px]', hasChange ? 'text-primary font-medium' : '')}>
                            {f.incoming ?? <span className="italic text-muted-foreground/40">—</span>}
                          </span>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Footer nav */}
      {book && !loadingBook && (
        <div className="border-t border-border px-6 py-3 flex items-center justify-between">
          <button
            onClick={prev}
            disabled={idx === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border border-border bg-card text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-30 transition-all"
          >
            <ArrowLeft className="w-3.5 h-3.5" /> Previous
          </button>
          <div className="flex items-center gap-2">
            {applyError && <span className="text-xs text-destructive">{applyError}</span>}
            <button
              onClick={skip}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border border-border bg-card text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
            >
              <SkipForward className="w-3.5 h-3.5" /> Skip
            </button>
            <button
              onClick={applyAndNext}
              disabled={applying}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
            >
              {applying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ArrowRight className="w-3.5 h-3.5" />}
              Apply & Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Standardize Titles Modal ──────────────────────────────────────────────────

interface StandardizeProposal {
  book_id: number
  current_title: string
  current_subtitle: string | null
  current_series: string | null
  current_series_index: number | null
  proposed_title: string
  proposed_subtitle: string | null
  proposed_series: string | null
  proposed_series_index: number | null
  changed: boolean
}

interface StandardizeModalProps {
  proposals: StandardizeProposal[]
  onClose: () => void
  onApplied: () => void
}

function StandardizeModal({ proposals, onClose, onApplied }: StandardizeModalProps) {
  const changed = proposals.filter(p => p.changed)
  const [checked, setChecked] = useState<Set<number>>(new Set(changed.map(p => p.book_id)))
  const [applying, setApplying] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)

  function toggle(id: number) {
    setChecked(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function toggleAll() {
    if (checked.size === changed.length) setChecked(new Set())
    else setChecked(new Set(changed.map(p => p.book_id)))
  }

  async function apply() {
    const toApply = changed.filter(p => checked.has(p.book_id))
    if (!toApply.length) return
    setApplying(true)
    setError(null)
    let done = 0
    try {
      for (const p of toApply) {
        await api.put(`/books/${p.book_id}`, {
          title: p.proposed_title,
          subtitle: p.proposed_subtitle ?? null,
          series: p.proposed_series ?? null,
          series_index: p.proposed_series_index ?? null,
        })
        done++
        setProgress(Math.round((done / toApply.length) * 100))
      }
      onApplied()
      onClose()
    } catch {
      setError('Some changes failed to apply.')
    } finally {
      setApplying(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative z-10 bg-background border border-border rounded-xl shadow-xl w-full max-w-2xl mx-4 max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <div>
            <div className="flex items-center gap-2 font-semibold text-sm">
              <Wand2 className="w-4 h-4 text-primary" />
              Standardize Titles
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">
              {changed.length} of {proposals.length} books would change
            </p>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* List */}
        <div className="overflow-y-auto flex-1 divide-y divide-border/50">
          {changed.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-12">
              No titles need standardizing.
            </p>
          )}
          {changed.length > 0 && (
            <div className="flex items-center gap-2 px-5 py-2 bg-muted/40">
              <input
                type="checkbox"
                checked={checked.size === changed.length}
                onChange={toggleAll}
                className="rounded border-border"
              />
              <span className="text-xs text-muted-foreground">Select all ({changed.length})</span>
            </div>
          )}
          {changed.map(p => (
            <label key={p.book_id} className="flex items-start gap-3 px-5 py-3 hover:bg-accent/30 cursor-pointer">
              <input
                type="checkbox"
                checked={checked.has(p.book_id)}
                onChange={() => toggle(p.book_id)}
                className="rounded border-border mt-0.5 shrink-0"
              />
              <div className="min-w-0 flex-1 space-y-1">
                <p className="text-xs text-muted-foreground line-through truncate">{p.current_title}{p.current_subtitle ? ` — ${p.current_subtitle}` : ''}</p>
                <div className="text-xs space-y-0.5">
                  {p.proposed_title !== p.current_title && (
                    <p><span className="text-muted-foreground w-16 inline-block">Title</span><span className="font-medium">{p.proposed_title}</span></p>
                  )}
                  {p.proposed_subtitle !== p.current_subtitle && (
                    <p><span className="text-muted-foreground w-16 inline-block">Subtitle</span><span className="font-medium">{p.proposed_subtitle ?? <span className="italic text-muted-foreground/50">cleared</span>}</span></p>
                  )}
                  {p.proposed_series !== p.current_series && (
                    <p><span className="text-muted-foreground w-16 inline-block">Series</span><span className="font-medium">{p.proposed_series ?? <span className="italic text-muted-foreground/50">cleared</span>}</span></p>
                  )}
                  {p.proposed_series_index !== p.current_series_index && (
                    <p><span className="text-muted-foreground w-16 inline-block">Index</span><span className="font-medium">{p.proposed_series_index ?? <span className="italic text-muted-foreground/50">cleared</span>}</span></p>
                  )}
                </div>
              </div>
            </label>
          ))}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-border shrink-0 gap-3">
          {applying && (
            <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
              <div className="h-full bg-primary rounded-full transition-all" style={{ width: `${progress}%` }} />
            </div>
          )}
          {error && <p className="text-xs text-destructive flex-1">{error}</p>}
          {!applying && !error && <div className="flex-1" />}
          <button
            onClick={onClose}
            disabled={applying}
            className="px-3 py-1.5 rounded-lg text-sm font-medium border border-border bg-card text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-50 transition-all"
          >
            Cancel
          </button>
          <button
            onClick={apply}
            disabled={applying || checked.size === 0}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
          >
            {applying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
            Apply {checked.size > 0 ? `${checked.size} ` : ''}Changes
          </button>
        </div>
      </div>
    </div>
  )
}

// ── ChapterAssignModal ────────────────────────────────────────────────────────

interface ChapterAssignProps {
  bookIds: number[]
  open: boolean
  bookTypes: BookType[]
  onClose: () => void
  onDone: () => void
}

function ChapterAssignModal({ bookIds, open, bookTypes, onClose, onDone }: ChapterAssignProps) {
  const [series, setSeries] = useState('')
  const [author, setAuthor] = useState('')
  const [bookTypeId, setBookTypeId] = useState('')
  const [facets, setFacets] = useState<{ series: string[]; authors: string[] }>({ series: [], authors: [] })
  const [applying, setApplying] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [books, setBooks] = useState<{ id: number; title: string }[]>([])

  useEffect(() => {
    if (!open) return
    api.get<{ series: string[]; authors: string[] }>('/books/facets').then(setFacets).catch(() => {})
    Promise.all(bookIds.map(id => api.get<{ id: number; title: string }>(`/books/${id}`))).then(setBooks).catch(() => {})
  }, [open, bookIds])

  // Auto-fill author when series is selected
  useEffect(() => {
    if (!series) return
    api.get<Array<{ author: string | null }>>(`/books?series=${encodeURIComponent(series)}&limit=1`).then(data => {
      if (data[0]?.author) setAuthor(data[0].author)
    }).catch(() => {})
  }, [series])

  function extractChapterNum(title: string): number | null {
    const patterns = [
      /\bch(?:apter)?\.?\s*(\d+)/i,
      /\bc(\d{2,4})\b/i,
      /\bv(\d{2,4})\b/i,
      /#(\d+)/,
      /\b(\d{2,4})\b/,
    ]
    for (const pat of patterns) {
      const m = title.match(pat)
      if (m) return parseInt(m[1], 10)
    }
    return null
  }

  function buildCleanTitle(title: string, seriesName: string): string {
    const num = extractChapterNum(title)
    if (num !== null) return `${seriesName} Chapter ${num}`
    return title
  }

  async function apply() {
    if (!series) return
    setApplying(true)
    setProgress({ done: 0, total: books.length })
    let done = 0

    for (const book of books) {
      const chNum = extractChapterNum(book.title)
      const body: Record<string, unknown> = {
        series,
        title: buildCleanTitle(book.title, series),
        content_type: 'chapter',
      }
      if (author) body.author = author
      if (chNum !== null) body.series_index = chNum
      if (bookTypeId) body.book_type_id = Number(bookTypeId)

      try {
        await api.put(`/books/${book.id}`, body)
      } catch {
        // continue on error
      }
      done++
      setProgress({ done, total: books.length })
    }

    setApplying(false)
    setProgress(null)
    onDone()
    handleClose()
  }

  function handleClose() {
    if (applying) return
    setSeries('')
    setAuthor('')
    setBookTypeId('')
    setBooks([])
    setProgress(null)
    onClose()
  }

  if (!open) return null

  const detected = books.filter(b => extractChapterNum(b.title) !== null).length

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={handleClose} />
      <div className="relative z-10 bg-background border border-border rounded-xl shadow-xl w-full max-w-md mx-4 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h2 className="text-sm font-semibold">Assign Chapter Metadata</h2>
          <button onClick={handleClose} disabled={applying} className="text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          <p className="text-xs text-muted-foreground">
            {bookIds.length} chapters uploaded{detected > 0 ? ` — ${detected} chapter numbers detected` : ''}
          </p>

          {/* Series picker */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Series</label>
            <input
              list="chapter-series-list"
              value={series}
              onChange={e => setSeries(e.target.value)}
              placeholder="Start typing to search..."
              className="w-full text-sm rounded-lg border border-border bg-background px-3 py-2 focus:outline-none focus:ring-1 focus:ring-ring"
            />
            <datalist id="chapter-series-list">
              {facets.series.map(s => <option key={s} value={s} />)}
            </datalist>
          </div>

          {/* Author */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Author</label>
            <input
              list="chapter-author-list"
              value={author}
              onChange={e => setAuthor(e.target.value)}
              placeholder="Auto-filled from series"
              className="w-full text-sm rounded-lg border border-border bg-background px-3 py-2 focus:outline-none focus:ring-1 focus:ring-ring"
            />
            <datalist id="chapter-author-list">
              {facets.authors.map(a => <option key={a} value={a} />)}
            </datalist>
          </div>

          {/* Book type */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Book Type</label>
            <select
              value={bookTypeId}
              onChange={e => setBookTypeId(e.target.value)}
              className="w-full text-sm rounded-lg border border-border bg-background px-3 py-2 focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">None</option>
              {bookTypes.map(bt => <option key={bt.id} value={String(bt.id)}>{bt.label}</option>)}
            </select>
          </div>

          {/* Preview */}
          {books.length > 0 && series && (
            <div className="rounded-lg border border-border bg-muted/30 max-h-40 overflow-y-auto">
              <div className="divide-y divide-border/50">
                {books.map(b => {
                  const num = extractChapterNum(b.title)
                  return (
                    <div key={b.id} className="flex items-center gap-2 px-3 py-1.5 text-xs">
                      <span className="text-muted-foreground truncate flex-1">{b.title}</span>
                      <span className="shrink-0 font-medium">
                        {num !== null ? `Ch. ${num}` : <span className="text-yellow-500">?</span>}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-border flex items-center justify-between">
          {applying && progress ? (
            <span className="text-xs text-muted-foreground">Applying {progress.done}/{progress.total}...</span>
          ) : (
            <span />
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
              onClick={apply}
              disabled={applying || !series || books.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
            >
              {applying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
              Apply to {bookIds.length} Chapters
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Main MetadataManager ──────────────────────────────────────────────────────

export function MetadataManager() {
  const [mode, setMode] = useState<'table' | 'review'>('table')
  const [standardizeProposals, setStandardizeProposals] = useState<StandardizeProposal[] | null>(null)
  const [standardizeLoading, setStandardizeLoading] = useState(false)
  const [books, setBooks] = useState<AuditBook[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [reviewQueue, setReviewQueue] = useState<number[]>([])
  const [bookTypes, setBookTypes] = useState<BookType[]>([])
  const [libraries, setLibraries] = useState<Library[]>([])

  // Filters
  const [search, setSearch] = useState('')
  const [filterSeries, setFilterSeries] = useState('')
  const [filterTypeId, setFilterTypeId] = useState('')
  const [filterLibId, setFilterLibId] = useState('')
  const [missingFields, setMissingFields] = useState<string[]>([])
  const [missingDropOpen, setMissingDropOpen] = useState(false)
  const searchDebounce = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [debouncedSearch, setDebouncedSearch] = useState('')

  // Bulk fetch modal
  const [bulkModalOpen, setBulkModalOpen] = useState(false)
  const [bulkModalBookIds, setBulkModalBookIds] = useState<number[]>([])

  // Upload modal
  const [uploadModalOpen, setUploadModalOpen] = useState(false)

  // Chapter import mode
  const [chapterMode, setChapterMode] = useState(false)
  const [chapterAssignOpen, setChapterAssignOpen] = useState(false)
  const [chapterBookIds, setChapterBookIds] = useState<number[]>([])

  // Sort
  const [sortCol, setSortCol] = useState<SortCol>('completeness')
  const [sortDir, setSortDir] = useState<SortDir>('asc')

  useEffect(() => {
    loadData()
  }, [])

  async function loadData() {
    setLoading(true)
    setError(null)
    try {
      const [auditData, btData, libData] = await Promise.all([
        api.get<AuditBook[]>('/books/metadata-audit'),
        api.get<BookType[]>('/book-types'),
        api.get<Library[]>('/libraries'),
      ])
      setBooks(auditData.filter(b => b.content_type !== 'chapter'))
      setBookTypes(btData)
      setLibraries(libData)
    } catch {
      setError('Failed to load metadata audit data.')
    } finally {
      setLoading(false)
    }
  }

  function handleSearchChange(val: string) {
    setSearch(val)
    if (searchDebounce.current) clearTimeout(searchDebounce.current)
    searchDebounce.current = setTimeout(() => setDebouncedSearch(val), 250)
  }

  function toggleSort(col: SortCol) {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('asc') }
  }

  function toggleMissingField(key: string) {
    setMissingFields(prev =>
      prev.includes(key) ? prev.filter(f => f !== key) : [...prev, key]
    )
  }

  // Derived: all distinct series from loaded data
  const allSeries = [...new Set(books.map(b => b.series).filter(Boolean))].sort()

  // Filtered + sorted books
  const filtered = books.filter(b => {
    if (debouncedSearch) {
      const q = debouncedSearch.toLowerCase()
      if (!b.title.toLowerCase().includes(q) && !b.author.toLowerCase().includes(q)) return false
    }
    if (filterSeries && b.series !== filterSeries) return false
    if (filterTypeId && b.book_type_id !== Number(filterTypeId)) return false
    if (filterLibId && !b.library_ids.includes(Number(filterLibId))) return false
    if (missingFields.length > 0) {
      for (const f of missingFields) {
        if (b.fields_present[f]) return false // book has this field — exclude
      }
    }
    return true
  })

  const sorted = [...filtered].sort((a, b) => {
    let cmp = 0
    if (sortCol === 'title') cmp = a.title.localeCompare(b.title)
    else if (sortCol === 'author') cmp = a.author.localeCompare(b.author)
    else if (sortCol === 'series') {
      const ai = a.series_index ?? Infinity
      const bi = b.series_index ?? Infinity
      cmp = ai !== bi ? ai - bi : a.series.localeCompare(b.series)
    }
    else if (sortCol === 'year') cmp = (a.year ?? 0) - (b.year ?? 0)
    else if (sortCol === 'completeness') cmp = a.completeness - b.completeness
    // Secondary sort: within ties, order by series_index numerically
    if (cmp === 0) {
      const ai = a.series_index ?? Infinity
      const bi = b.series_index ?? Infinity
      if (ai !== bi) cmp = ai - bi
    }
    return sortDir === 'asc' ? cmp : -cmp
  })

  function toggleSelect(id: number) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function toggleSelectAll() {
    if (selected.size === sorted.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(sorted.map(b => b.id)))
    }
  }

  function startReview(ids: number[]) {
    setReviewQueue(ids)
    setMode('review')
  }

  function startBatchFetch(ids: number[]) {
    setBulkModalBookIds(ids.slice(0, 100))
    setBulkModalOpen(true)
  }

  async function startStandardize(ids: number[]) {
    setStandardizeLoading(true)
    try {
      const proposals = await api.post<StandardizeProposal[]>('/books/standardize-titles', { book_ids: ids })
      setStandardizeProposals(proposals)
    } catch {
      // ignore — user can retry
    } finally {
      setStandardizeLoading(false)
    }
  }

  function clearFilters() {
    setSearch('')
    setDebouncedSearch('')
    setFilterSeries('')
    setFilterTypeId('')
    setFilterLibId('')
    setMissingFields([])
  }

  const hasFilters = search || filterSeries || filterTypeId || filterLibId || missingFields.length > 0

  function handleBookUpdated(_id: number) {
    // Refresh completeness for that book by reloading all
    api.get<AuditBook[]>('/books/metadata-audit').then(setBooks).catch(() => {})
  }

  function SortHeader({ col, label }: { col: SortCol; label: string }) {
    const active = sortCol === col
    return (
      <button
        onClick={() => toggleSort(col)}
        className={cn('flex items-center gap-0.5 text-[11px] font-medium uppercase tracking-wide hover:text-foreground transition-colors', active ? 'text-foreground' : 'text-muted-foreground')}
      >
        {label}
        {active ? (sortDir === 'asc' ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />) : <ChevronUp className="w-3 h-3 opacity-20" />}
      </button>
    )
  }

  if (mode === 'review') {
    return (
      <div className="h-[calc(100vh-56px)] flex flex-col">
        <ReviewFlow
          queue={reviewQueue}
          onBack={() => { setMode('table'); loadData() }}
          onBookUpdated={handleBookUpdated}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Standardize modal */}
      {standardizeProposals && (
        <StandardizeModal
          proposals={standardizeProposals}
          onClose={() => setStandardizeProposals(null)}
          onApplied={() => { setStandardizeProposals(null); loadData() }}
        />
      )}

      <BulkMetadataReviewModal
        bookIds={bulkModalBookIds}
        open={bulkModalOpen}
        onClose={() => setBulkModalOpen(false)}
        onApplied={() => { setBulkModalOpen(false); loadData() }}
        onManualSearch={(bookId) => { setBulkModalOpen(false); startReview([bookId]) }}
        onReviewUncertain={(ids) => { setBulkModalOpen(false); startReview(ids) }}
      />

      <UploadModal
        isOpen={uploadModalOpen}
        onClose={() => setUploadModalOpen(false)}
        onDone={loadData}
        onUploaded={(ids) => {
          setUploadModalOpen(false)
          loadData()
          if (chapterMode) {
            setChapterBookIds(ids)
            setChapterAssignOpen(true)
          } else {
            setBulkModalBookIds(ids.slice(0, 100))
            setBulkModalOpen(true)
          }
        }}
      />

      <ChapterAssignModal
        bookIds={chapterBookIds}
        open={chapterAssignOpen}
        bookTypes={bookTypes}
        onClose={() => setChapterAssignOpen(false)}
        onDone={() => { setChapterAssignOpen(false); loadData() }}
      />

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Search */}
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            value={search}
            onChange={e => handleSearchChange(e.target.value)}
            placeholder="Search title, author…"
            className="pl-8 pr-3 h-8 w-52 text-xs bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-ring"
          />
          {search && (
            <button onClick={() => handleSearchChange('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
              <X className="w-3 h-3" />
            </button>
          )}
        </div>

        {/* Series */}
        <select
          value={filterSeries}
          onChange={e => setFilterSeries(e.target.value)}
          className="h-8 px-2 text-xs bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="">All series</option>
          {allSeries.map(s => <option key={s} value={s}>{s}</option>)}
        </select>

        {/* Book type */}
        <select
          value={filterTypeId}
          onChange={e => setFilterTypeId(e.target.value)}
          className="h-8 px-2 text-xs bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="">All types</option>
          {bookTypes.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>

        {/* Library */}
        <select
          value={filterLibId}
          onChange={e => setFilterLibId(e.target.value)}
          className="h-8 px-2 text-xs bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="">All libraries</option>
          {libraries.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>

        {/* Missing fields multi-select */}
        <div className="relative">
          <button
            onClick={() => setMissingDropOpen(o => !o)}
            className={cn(
              'flex items-center gap-1.5 h-8 px-2.5 text-xs rounded-lg border transition-colors',
              missingFields.length > 0 ? 'border-primary bg-primary/5 text-primary' : 'border-border bg-background text-muted-foreground hover:text-foreground'
            )}
          >
            <Filter className="w-3 h-3" />
            Missing
            {missingFields.length > 0 && <span className="font-medium">({missingFields.length})</span>}
            <ChevronDown className="w-3 h-3" />
          </button>
          {missingDropOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setMissingDropOpen(false)} />
              <div className="absolute top-full left-0 mt-1 z-20 bg-background border border-border rounded-lg shadow-lg py-1 min-w-[160px]">
                {MISSING_FIELD_OPTIONS.map(opt => (
                  <label key={opt.key} className="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-accent cursor-pointer">
                    <input
                      type="checkbox"
                      checked={missingFields.includes(opt.key)}
                      onChange={() => toggleMissingField(opt.key)}
                      className="rounded border-border"
                    />
                    {opt.label}
                  </label>
                ))}
              </div>
            </>
          )}
        </div>

        {hasFilters && (
          <button onClick={clearFilters} className="h-8 px-2.5 text-xs text-muted-foreground hover:text-foreground border border-border rounded-lg hover:bg-muted transition-colors">
            Clear
          </button>
        )}

        <span className="text-xs text-muted-foreground ml-1">
          {filtered.length !== books.length ? `${filtered.length} of ${books.length}` : `${books.length}`} books
        </span>

        <div className="flex-1" />

        {/* Upload */}
        <button
          onClick={() => { setChapterMode(false); setUploadModalOpen(true) }}
          className="flex items-center gap-1.5 h-8 px-3 text-xs font-medium rounded-lg border border-border bg-card text-foreground hover:bg-muted transition-all"
        >
          <Upload className="w-3.5 h-3.5" />
          Upload
        </button>
        <button
          onClick={() => { setChapterMode(true); setUploadModalOpen(true) }}
          className="flex items-center gap-1.5 h-8 px-3 text-xs font-medium rounded-lg border border-border bg-card text-foreground hover:bg-muted transition-all"
        >
          <BookMarked className="w-3.5 h-3.5" />
          Upload Chapters
        </button>

        {/* Batch Fetch */}
        <button
          onClick={() => startBatchFetch(sorted.map(b => b.id))}
          disabled={sorted.length === 0}
          className="flex items-center gap-1.5 h-8 px-3 text-xs font-medium rounded-lg border border-border bg-card text-foreground hover:bg-muted disabled:opacity-40 transition-all"
        >
          <Sparkles className="w-3.5 h-3.5" />
          Batch Fetch ({Math.min(sorted.length, 100)})
        </button>

        {/* Review all filtered */}
        <button
          onClick={() => startReview(sorted.map(b => b.id))}
          disabled={sorted.length === 0}
          className="flex items-center gap-1.5 h-8 px-3 text-xs font-medium rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-40 transition-all"
        >
          <Sparkles className="w-3.5 h-3.5" />
          Review All Filtered ({sorted.length})
        </button>
        <button
          onClick={() => startStandardize(sorted.map(b => b.id))}
          disabled={sorted.length === 0 || standardizeLoading}
          className="flex items-center gap-1.5 h-8 px-3 text-xs font-medium rounded-lg border border-border bg-card text-foreground hover:bg-muted disabled:opacity-40 transition-all"
        >
          {standardizeLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wand2 className="w-3.5 h-3.5" />}
          Standardize Titles
        </button>
      </div>

      {/* Bulk bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-primary/5 border border-primary/20">
          <span className="text-xs font-medium text-primary">{selected.size} selected</span>
          <div className="flex-1" />
          <button
            onClick={() => startBatchFetch([...selected])}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted transition-all"
          >
            <Sparkles className="w-3.5 h-3.5" />
            Batch Fetch
          </button>
          <button
            onClick={() => startReview([...selected])}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all"
          >
            <Sparkles className="w-3.5 h-3.5" />
            Review Selected
          </button>
          <button
            onClick={() => startStandardize([...selected])}
            disabled={standardizeLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted disabled:opacity-50 transition-all"
          >
            {standardizeLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wand2 className="w-3.5 h-3.5" />}
            Standardize
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Error / loading */}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {loading && (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Table */}
      {!loading && (
        <div className="rounded-lg border border-border overflow-auto max-h-[calc(100vh-220px)]">
          <table className="w-full min-w-[900px] text-xs">
            <thead className="sticky top-0 z-10">
              <tr className="bg-muted border-b border-border">
                <th className="w-8 px-2 py-2 text-left">
                  <input
                    type="checkbox"
                    checked={selected.size === sorted.length && sorted.length > 0}
                    onChange={toggleSelectAll}
                    className="rounded border-border"
                  />
                </th>
                <th className="w-10 px-2 py-2" />
                <th className="px-2 py-2 text-left"><SortHeader col="title" label="Title" /></th>
                <th className="w-28 px-2 py-2 text-left text-[11px] font-medium text-muted-foreground uppercase tracking-wide">Subtitle</th>
                <th className="w-36 px-2 py-2 text-left"><SortHeader col="author" label="Author" /></th>
                <th className="w-36 px-2 py-2 text-left"><SortHeader col="series" label="Series" /></th>
                <th className="w-10 px-2 py-2 text-left text-[11px] font-medium text-muted-foreground uppercase tracking-wide">#</th>
                <th className="w-16 px-2 py-2 text-left"><SortHeader col="year" label="Year" /></th>
                <th className="w-12 px-2 py-2 text-center text-[11px] font-medium text-muted-foreground uppercase tracking-wide">Desc</th>
                <th className="w-12 px-2 py-2 text-center text-[11px] font-medium text-muted-foreground uppercase tracking-wide">Cover</th>
                <th className="w-24 px-2 py-2 text-left text-[11px] font-medium text-muted-foreground uppercase tracking-wide">ISBN</th>
                <th className="w-12 px-2 py-2 text-left text-[11px] font-medium text-muted-foreground uppercase tracking-wide">Lang</th>
                <th className="w-14 px-2 py-2 text-left"><SortHeader col="completeness" label="Score" /></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(book => (
                <tr
                  key={book.id}
                  className={cn('border-b border-border/50 hover:bg-accent/30 transition-colors', selected.has(book.id) && 'bg-primary/5')}
                >
                  <td className="px-2 py-1.5">
                    <input
                      type="checkbox"
                      checked={selected.has(book.id)}
                      onChange={() => toggleSelect(book.id)}
                      className="rounded border-border"
                    />
                  </td>
                  <td className="px-2 py-1.5">
                    {book.cover_path ? (
                      <img
                        src={`${API}/api/books/${book.id}/cover`}
                        alt=""
                        className="w-7 h-9 object-cover rounded border border-border"
                      />
                    ) : (
                      <div className="w-7 h-9 rounded border border-border bg-muted flex items-center justify-center">
                        <BookOpen className="w-3 h-3 text-muted-foreground" />
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-1.5">
                    <EditCell value={book.title} bookId={book.id} field="title" onSaved={(id, field, val) => setBooks(prev => prev.map(b => b.id === id ? { ...b, [field]: val, fields_present: { ...b.fields_present, title: !!val }, completeness: Object.values({ ...b.fields_present, [field]: !!val }).filter(Boolean).length } : b))} />
                  </td>
                  <td className="px-2 py-1.5">
                    <EditCell value={book.subtitle} bookId={book.id} field="subtitle" onSaved={(id, field, val) => setBooks(prev => prev.map(b => b.id === id ? { ...b, [field]: val } : b))} />
                  </td>
                  <td className="px-2 py-1.5">
                    <EditCell value={book.author} bookId={book.id} field="author" onSaved={(id, field, val) => setBooks(prev => prev.map(b => b.id === id ? { ...b, [field]: val, fields_present: { ...b.fields_present, author: !!val }, completeness: Object.values({ ...b.fields_present, author: !!val }).filter(Boolean).length } : b))} />
                  </td>
                  <td className="px-2 py-1.5">
                    <EditCell value={book.series} bookId={book.id} field="series" onSaved={(id, field, val) => setBooks(prev => prev.map(b => b.id === id ? { ...b, [field]: val, fields_present: { ...b.fields_present, series: !!val }, completeness: Object.values({ ...b.fields_present, series: !!val }).filter(Boolean).length } : b))} />
                  </td>
                  <td className="px-2 py-1.5">
                    <EditCell value={book.series_index?.toString() ?? ''} bookId={book.id} field="series_index" onSaved={(id, _field, val) => setBooks(prev => prev.map(b => b.id === id ? { ...b, series_index: val ? Number(val) : null } : b))} />
                  </td>
                  <td className="px-2 py-1.5">
                    <EditCell value={book.year?.toString() ?? ''} bookId={book.id} field="year" onSaved={(id, _field, val) => setBooks(prev => prev.map(b => b.id === id ? { ...b, year: val ? Number(val) : null, fields_present: { ...b.fields_present, year: !!val }, completeness: Object.values({ ...b.fields_present, year: !!val }).filter(Boolean).length } : b))} />
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <Dot present={book.fields_present.description} />
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <Dot present={book.fields_present.cover} />
                  </td>
                  <td className="px-2 py-1.5">
                    <EditCell value={book.isbn} bookId={book.id} field="isbn" onSaved={(id, field, val) => setBooks(prev => prev.map(b => b.id === id ? { ...b, [field]: val, fields_present: { ...b.fields_present, isbn: !!val }, completeness: Object.values({ ...b.fields_present, isbn: !!val }).filter(Boolean).length } : b))} />
                  </td>
                  <td className="px-2 py-1.5">
                    <EditCell value={book.language} bookId={book.id} field="language" onSaved={(id, field, val) => setBooks(prev => prev.map(b => b.id === id ? { ...b, [field]: val, fields_present: { ...b.fields_present, language: !!val }, completeness: Object.values({ ...b.fields_present, language: !!val }).filter(Boolean).length } : b))} />
                  </td>
                  <td className="px-2 py-1.5">
                    <CompletenessBadge score={book.completeness} total={book.completeness_total} />
                  </td>
                </tr>
              ))}
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={13} className="px-4 py-12 text-center text-muted-foreground text-sm">
                    No books match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
