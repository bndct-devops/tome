import { memo, startTransition, useEffect, useLayoutEffect, useState, useRef, useCallback } from 'react'
import { Trans } from '@lingui/react/macro'
import { useNavigate } from 'react-router-dom'
import { t, msg, plural } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'
import {
  Loader2, Search, X, ChevronUp, ChevronDown, BookOpen,
  Check, ArrowLeft, ArrowRight, SkipForward, RefreshCw,
  ExternalLink, Sparkles, Filter, Wand2, Upload, BookMarked,
} from 'lucide-react'
import { api } from '@/lib/api'
import type { BookDetail, BookType, Library, MetadataCandidate } from '@/lib/books'
import { cn } from '@/lib/utils'
import { BookAnimation } from '@/components/BookAnimation'
import { BulkMetadataReviewModal } from './BulkMetadataReviewModal'
import { UploadModal } from './UploadModal'
import { useShiftSelect } from '@/lib/useShiftSelect'
import { CoverImage } from './CoverImage'
import { useToast } from '@/contexts/ToastContext'
import { coverSrc } from '@/lib/covers'
import { Checkbox, FormLabel, Input, Pill, PRESS, Select, Swap, Textarea } from '@/components/page'
import { ModalShell, useEscape } from '@/components/ModalShell'
import { AnimatePresence } from 'motion/react'
import { Pop, Unfold } from '@/components/motion'
import { RowsSkeleton } from '@/components/FormMotion'

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
  { key: 'description', label: msg`Description` },
  { key: 'cover', label: msg`Cover` },
  { key: 'isbn', label: msg`ISBN` },
  { key: 'year', label: msg`Year` },
  { key: 'author', label: msg`Author` },
  { key: 'series', label: msg`Series` },
  { key: 'language', label: msg`Language` },
  { key: 'publisher', label: msg`Publisher` },
]

/** Header cell text: 12px small caps, like every hairline table. */
// eslint-disable-next-line lingui/no-unlocalized-strings -- class names
const HEAD = 'text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground'

// The table's first frame holds this many rows (more than a screen); the rest
// follow in steps after it, so opening a big library never stalls a frame.
const FIRST_ROWS = 40
const ROW_STEP = 60
const MIN_TABLE_PX = 200
// Selecting or clearing many rows: the rows in view change at once, the rest this many per frame.
const SEL_STEP = 150

/** Rows kept on screen while a re-sorted or re-filtered table fills from the top. */
interface RowWindow { from: number; to: number; pitch: number }

/** Indexes of the rendered rows that are in view, whichever of the table or the page scrolls. */
function rowsInView(el: HTMLElement): RowWindow | null {
  const trs = el.querySelectorAll<HTMLTableRowElement>('tbody > tr:not([data-gap])')
  if (trs.length < 2) return null
  const r0 = trs[0].getBoundingClientRect().top
  const pitch = trs[1].getBoundingClientRect().top - r0
  if (pitch <= 0) return null
  const box = el.getBoundingClientRect()
  const port = el.closest('main')?.getBoundingClientRect()
  const top = Math.max(box.top, port?.top ?? 0)
  const bottom = Math.min(box.bottom, port?.bottom ?? window.innerHeight)
  return { from: Math.max(0, Math.floor((top - r0) / pitch)), to: Math.max(0, Math.ceil((bottom - r0) / pitch) + 1), pitch }
}

/**
 * How many of `total` rows to render: `FIRST_ROWS` at once, then `ROW_STEP` more after each painted frame.
 * `restart()` (called with a sort or filter change) starts over from `FIRST_ROWS`: moving or
 * inserting hundreds of rows in one commit costs 60 to 100 ms of style and layout. When the table is
 * scrolled past the first rows, the rows under the viewport render at once below a spacer, so the
 * scroll position holds and nothing in view is missing.
 */
function useRowsShown(total: number, scrollRef: React.RefObject<HTMLDivElement | null>) {
  const [rows, setRows] = useState<{ shown: number; win: RowWindow | null }>({ shown: FIRST_ROWS, win: null })
  const { shown } = rows
  useEffect(() => {
    if (shown >= total) return
    let timer = 0
    const frame = requestAnimationFrame(() => { timer = window.setTimeout(() => setRows(r => ({ ...r, shown: r.shown + ROW_STEP }))) })
    return () => { cancelAnimationFrame(frame); clearTimeout(timer) }
  }, [shown, total])
  const restart = useCallback(() => {
    const view = scrollRef.current ? rowsInView(scrollRef.current) : null
    setRows({ shown: FIRST_ROWS, win: view && view.to > FIRST_ROWS ? view : null })
  }, [scrollRef])
  return { ...rows, restart }
}

/** "Missing" filter: a pill that opens a small checklist. */
function MissingFilter({ open, setOpen, selected, onToggle }: {
  open: boolean
  setOpen: (fn: (o: boolean) => boolean) => void
  selected: string[]
  onToggle: (key: string) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEscape(open, () => setOpen(() => false))
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(() => false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open, setOpen])
  return (
    <div ref={ref} className="relative">
      <Pill
        variant="secondary"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        className={cn(selected.length > 0 && 'text-primary')}
      >
        <Filter className="w-3.5 h-3.5" />
        <Trans>Missing</Trans>
        {selected.length > 0 && <span className="font-semibold">({selected.length})</span>}
        <ChevronDown className={cn('w-3.5 h-3.5 transition-transform duration-200 ease-(--spring)', open && 'rotate-180')} />
      </Pill>
      <Pop open={open} origin="top-left" className="absolute top-full left-0 mt-1.5 z-20 min-w-[180px] rounded-xl bg-card shadow-lg ring-1 ring-panel-ring py-1.5">
          {MISSING_FIELD_OPTIONS.map(opt => (
            <Checkbox
              key={opt.key}
              checked={selected.includes(opt.key)}
              onChange={() => onToggle(opt.key)}
              label={i18n._(opt.label)}
              className="flex w-full px-3 py-1.5 hover:bg-secondary transition-colors"
            />
          ))}
      </Pop>
    </div>
  )
}

// ── FieldRow (shared with review flow) ───────────────────────────────────────

interface FieldRow {
  key: string
  label: MessageDescriptor
  current: string | null
  incoming: string | null
  checked: boolean
}

function buildFieldRows(book: BookDetail, c: MetadataCandidate): FieldRow[] {
  return [
    { key: 'title', label: msg`Title`, current: book.title, incoming: c.title, checked: true },
    { key: 'author', label: msg`Author`, current: book.author, incoming: c.author, checked: true },
    { key: 'description', label: msg`Description`, current: book.description, incoming: c.description, checked: true },
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

// ── Completeness badge ────────────────────────────────────────────────────────

function CompletenessBadge({ score, total }: { score: number; total: number }) {
  const pct = score / total
  const color = pct >= 0.8 ? 'text-success' : pct >= 0.5 ? 'text-warning' : 'text-destructive'
  return <span className={cn('text-[13px] font-mono font-medium tabular-nums', color)}>{score}/{total}</span>
}

// ── Presence dot ─────────────────────────────────────────────────────────────

function Dot({ present }: { present: boolean }) {
  return (
    <span className={cn('inline-block w-2 h-2 rounded-full', present ? 'bg-success' : 'bg-destructive')} />
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
        className="w-full bg-secondary rounded-md px-1.5 py-0.5 text-[13px] text-foreground ring-1 ring-ring focus:outline-none"
      />
    )
  }

  return (
    <span
      onClick={startEdit}
      className="cursor-pointer hover:bg-secondary transition-colors rounded-md px-1.5 py-0.5 text-[13px] block truncate group relative"
      title={value || undefined}
    >
      {value || <span className="text-muted-foreground/40 italic">-</span>}
      {/* Saving and saved sit over the cell's end, so the text never moves. */}
      {/* Plain elements, not a motion component: this cell is rendered thousands of times. */}
      {(saving || saved) && (
        <span className="absolute right-1 top-1/2 -translate-y-1/2 pointer-events-none">
          {saving
            ? <Loader2 className="h-3 w-3 animate-spin" />
            : <Check className="h-3 w-3 text-success animate-in fade-in-40 zoom-in-90 duration-150 motion-reduce:animate-none" />}
        </span>
      )}
    </span>
  )
}

// One table row. Memoised: selecting a row or opening a menu re-renders the
// manager, and re-rendering every row of a large library cost 100 ms a click.
const MetaRow = memo(function MetaRow({ book, selected, onToggle, setBooks }: {
  book: AuditBook
  selected: boolean
  onToggle: (id: number, shiftKey: boolean) => void
  setBooks: React.Dispatch<React.SetStateAction<AuditBook[]>>
}) {
  return (
    <tr
      className={cn('hover:bg-secondary/50 transition-colors', selected && 'bg-primary/5')}
    >
      <td className="pl-0.5 pr-2 py-1.5">
        <Checkbox
          checked={selected}
          onChange={e => onToggle(book.id, e.nativeEvent instanceof MouseEvent ? e.nativeEvent.shiftKey : false)}
          aria-label={t`Select`}
        />
      </td>
      <td className="px-2 py-1.5">
        {book.cover_path ? (
          <img
            src={API + coverSrc(book.id, { w: 160 })}
            alt=""
            loading="lazy"
            className="w-7 h-10 object-cover rounded-[4px]"
          />
        ) : (
          <div className="w-7 h-10 rounded-[4px] bg-secondary flex items-center justify-center">
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
  )
})

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
    setSelectedCandidate(null)
    setCandidates([])
    setCandidateError(null)
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
    // A refetch keeps the candidates on screen, dimmed, until the new ones land.
    setLoadingCandidates(true)
    setCandidateError(null)
    try {
      const qs = q?.trim() ? `?q=${encodeURIComponent(q)}` : ''
      const resp = await fetch(`${API}/api/books/${id}/fetch-metadata${qs}`, {
        headers: authHeader(),
      }).then(r => r.json()) as { candidates: MetadataCandidate[] }
      const data = resp.candidates ?? []
      setCandidates(data)
      if (data.length > 0 && book) {
        setSelectedCandidate(data[0])
        setFields(buildFieldRows(book, data[0]))
      } else if (data.length === 0) {
        setSelectedCandidate(null)
        setFields([])
        setCandidateError(t`No candidates found.`)
      }
    } catch {
      setCandidateError(t`Failed to fetch candidates.`)
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
      setApplyError(t`Failed to apply metadata.`)
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
      <div className="flex flex-col items-center justify-center py-24 gap-4 text-center animate-in fade-in-40 duration-150 motion-reduce:animate-none">
        <Check className="w-12 h-12 text-success" />
        <h2 className="font-display text-[22px] font-bold"><Trans>Review complete</Trans></h2>
        <p className="text-[15px] text-muted-foreground">
          {(() => { const total = queue.length; const applied = summary.applied; const skipped = summary.skipped; return <Trans>Reviewed {total} books. Applied changes to {applied}, skipped {skipped}.</Trans> })()}
        </p>
        <Pill variant="primary" onClick={onBack} className="mt-4">
          <Trans>Back to table</Trans>
        </Pill>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Progress header */}
      <div className="flex items-center justify-between py-3 border-b border-border/70 gap-4">
        <Pill variant="quiet" onClick={onBack} className="-ml-3">
          <ArrowLeft className="w-4 h-4" /> <Trans>Back to table</Trans>
        </Pill>
        <div className="flex items-center gap-3 flex-1 max-w-xs">
          <span className="text-[13px] text-muted-foreground whitespace-nowrap">
            {(() => { const at = idx + 1; const total = queue.length; return <Trans>Book {at} of {total}</Trans> })()}
          </span>
          <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--rail)' }}>
            <div
              className="h-full bg-primary rounded-full transition-[width] duration-300 ease-(--spring) motion-reduce:transition-none"
              style={{ width: `${((idx + 1) / queue.length) * 100}%` }}
            />
          </div>
        </div>
        <div className="w-24" />
      </div>

      {loadingBook && !book && (
        <div className="flex items-center justify-center py-24">
          <BookAnimation variant="refresh" className="block w-10 h-10 text-primary" />
        </div>
      )}

      {book && (
        <div className={cn('flex flex-col lg:flex-row flex-1 overflow-hidden transition-opacity duration-150', loadingBook && 'opacity-50 pointer-events-none')}>
          {/* Left: current metadata */}
          <div className="w-full lg:w-[420px] shrink-0 border-b lg:border-b-0 lg:border-r border-border/70 overflow-y-auto py-4 lg:py-6 lg:pr-6 space-y-4 max-h-[40vh] lg:max-h-none">
            <div className="flex gap-4">
              <div className="relative w-20 h-28 rounded-[8px] overflow-hidden shrink-0">
                <CoverImage
                  src={book.cover_path ? `${API}/api/books/${book.id}/cover` : null}
                  alt=""
                  iconClassName="w-6 h-6"
                />
              </div>
              <div className="min-w-0">
                <p className="font-semibold text-[15px] leading-snug">{book.title}</p>
                <p className="text-[13px] text-muted-foreground mt-0.5">{book.author || <span className="italic"><Trans>No author</Trans></span>}</p>
              </div>
            </div>

            <div className="space-y-2">
              {[
                { field: 'title', label: msg`Title`, value: book.title },
                { field: 'subtitle', label: msg`Subtitle`, value: book.subtitle || '' },
                { field: 'author', label: msg`Author`, value: book.author || '' },
                { field: 'series', label: msg`Series`, value: book.series || '' },
                { field: 'series_index', label: msg`Series #`, value: book.series_index?.toString() || '' },
                { field: 'year', label: msg`Year`, value: book.year?.toString() || '' },
                { field: 'isbn', label: msg`ISBN`, value: book.isbn || '' },
                { field: 'language', label: msg`Language`, value: book.language || '' },
                { field: 'publisher', label: msg`Publisher`, value: book.publisher || '' },
              ].map(({ field, label, value }) => (
                <div key={field} className="grid grid-cols-[80px_1fr] gap-2 items-center">
                  <span className="text-[13px] text-muted-foreground">{i18n._(label)}</span>
                  <Input
                    value={draft[field as keyof BookDetail] as string ?? value}
                    onChange={e => setDraft(d => ({ ...d, [field]: e.target.value }))}
                    aria-label={i18n._(label)}
                    className="h-8 text-[13px]"
                  />
                </div>
              ))}
              <div className="grid grid-cols-[80px_1fr] gap-2">
                <span className="text-[13px] text-muted-foreground pt-1"><Trans>Description</Trans></span>
                <Textarea
                  value={(draft.description as string | undefined) ?? book.description ?? ''}
                  onChange={e => setDraft(d => ({ ...d, description: e.target.value }))}
                  rows={4}
                  aria-label={t`Description`}
                  className="text-[13px] resize-none"
                />
              </div>
            </div>
          </div>

          {/* Right: candidates + diff */}
          <div className="flex-1 overflow-y-auto py-6 lg:pl-6 space-y-4">
            {/* Fetch again bar */}
            <div className="flex gap-2">
              <Input
                value={customQuery}
                onChange={e => setCustomQuery(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && fetchCandidates(book.id, customQuery)}
                placeholder={t`Override search query…`}
                className="flex-1"
              />
              <Pill variant="secondary" onClick={() => fetchCandidates(book.id, customQuery)} disabled={loadingCandidates} className="shrink-0">
                {loadingCandidates ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                <Trans>Fetch</Trans>
              </Pill>
            </div>

            {/* First fetch for this book: the candidates' place, held. */}
            {loadingCandidates && candidates.length === 0 && (
              <RowsSkeleton rows={3} rowClassName="h-[76px] px-2.5" className="border-y-0 divide-y-0" />
            )}

            {candidateError && !loadingCandidates && (
              <p className="text-[14px] text-muted-foreground text-center py-4">{candidateError}</p>
            )}

            {/* Candidate list */}
            {candidates.length > 0 && (
              <div className={cn('space-y-2 transition-opacity duration-150', loadingCandidates && 'opacity-50')}>
                <p className="text-[13px] text-muted-foreground">{plural(candidates.length, { one: '# candidate. Click to select.', other: '# candidates. Click to select.' })}</p>
                {candidates.map(c => (
                  <button
                    key={`${c.source}-${c.source_id}`}
                    onClick={() => selectCandidate(c)}
                    className={cn(
                      'w-full text-left rounded-lg p-2.5 flex gap-3 transition-[background-color,box-shadow] duration-150 touch-feedback', PRESS,
                      selectedCandidate?.source_id === c.source_id && selectedCandidate?.source === c.source
                        ? 'bg-primary/10 ring-1 ring-primary/40'
                        : 'hover:bg-secondary'
                    )}
                  >
                    {c.cover_url ? (
                      <img src={c.cover_url} alt="" className="h-14 w-10 object-cover rounded-[6px] shrink-0" />
                    ) : (
                      <div className="h-14 w-10 rounded-[6px] bg-secondary flex items-center justify-center shrink-0">
                        <BookOpen className="w-4 h-4 text-muted-foreground" />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-1">
                        <p className="text-[14px] font-medium line-clamp-1">{c.title}</p>
                        <span className="shrink-0 text-[12px] px-2 py-0.5 rounded-full text-muted-foreground bg-secondary">
                          {c.source === 'hardcover' ? 'HC' : c.source === 'google_books' ? 'GB' : c.source === 'anilist' ? 'AL' : 'OL'}
                        </span>
                      </div>
                      {c.author && <p className="text-[13px] text-muted-foreground">{c.author}</p>}
                      {c.year && <p className="text-[13px] text-muted-foreground">{c.year}</p>}
                    </div>
                  </button>
                ))}
              </div>
            )}

            {/* Diff table */}
            <Unfold open={!!selectedCandidate && fields.length > 0} gap={16}>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-[12px] font-bold text-muted-foreground uppercase tracking-[0.1em]"><Trans>Field diff</Trans></p>
                  <Checkbox
                    checked={fields.every(f => f.checked)}
                    onChange={toggleAll}
                    label={<span className="text-[13px]"><Trans>All</Trans></span>}
                  />
                </div>
                <div className="border-y border-border/70">
                  <div className="grid grid-cols-[20px_60px_1fr_1fr] sm:grid-cols-[20px_80px_1fr_1fr] gap-2 items-center border-b border-border/70 py-2 text-[12px] font-bold text-muted-foreground uppercase tracking-[0.1em]">
                    <span />
                    <span><Trans>Field</Trans></span>
                    <span><Trans>Current</Trans></span>
                    <span><Trans>Incoming</Trans></span>
                  </div>
                  {fields.map((f, i) => {
                    const hasChange = !!(f.incoming && f.incoming !== f.current)
                    return (
                      <div
                        key={f.key}
                        className={cn(
                          'grid grid-cols-[20px_60px_1fr_1fr] sm:grid-cols-[20px_80px_1fr_1fr] gap-2 items-start py-2 text-[13px]',
                          i > 0 && 'border-t border-border/70',
                          !hasChange && 'opacity-40'
                        )}
                      >
                        <Checkbox
                          checked={f.checked}
                          onChange={() => toggleField(f.key)}
                          disabled={!hasChange}
                          aria-label={i18n._(f.label)}
                        />
                        <span className="text-muted-foreground text-[13px] mt-0.5">{i18n._(f.label)}</span>
                        {f.key === 'cover' ? (
                          <span className="text-muted-foreground italic text-[13px]">
                            {book.cover_path ? t`Has cover` : t`No cover`}
                          </span>
                        ) : (
                          <span className={cn('break-words line-clamp-2 text-[13px]', hasChange ? 'line-through text-muted-foreground' : '')}>
                            {f.current ?? <span className="italic text-muted-foreground/40">-</span>}
                          </span>
                        )}
                        {f.key === 'cover' ? (
                          f.incoming ? (
                            <div className="flex items-start gap-1.5">
                              <img src={f.incoming} alt="" className="h-12 w-8 object-cover rounded-[8px]" />
                              <a href={f.incoming} target="_blank" rel="noopener noreferrer" className="text-[13px] text-primary hover:underline flex items-center gap-0.5 mt-0.5">
                                <ExternalLink className="w-3 h-3" />
                              </a>
                            </div>
                          ) : (
                            <span className="italic text-muted-foreground/40 text-[13px]">-</span>
                          )
                        ) : (
                          <span className={cn('break-words line-clamp-2 text-[13px]', hasChange ? 'text-primary font-medium' : '')}>
                            {f.incoming ?? <span className="italic text-muted-foreground/40">-</span>}
                          </span>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            </Unfold>
          </div>
        </div>
      )}

      {/* Footer nav */}
      {book && (
        <div className="border-t border-border px-6 py-3 flex items-center justify-between">
          <Pill variant="quiet" onClick={prev} disabled={idx === 0 || loadingBook}>
            <ArrowLeft className="w-3.5 h-3.5" /> <Trans>Previous</Trans>
          </Pill>
          <div className="flex items-center gap-2">
            <Swap id={applyError ?? ''}>
              {applyError && <span className="text-[13px] text-destructive">{applyError}</span>}
            </Swap>
            <Pill variant="secondary" onClick={skip} disabled={loadingBook}>
              <SkipForward className="w-3.5 h-3.5" /> <Trans>Skip</Trans>
            </Pill>
            <Pill variant="primary" onClick={applyAndNext} disabled={applying || loadingBook}>
              {applying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ArrowRight className="w-3.5 h-3.5" />}
              <Trans>Apply and next</Trans>
            </Pill>
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
      setError(t`Some changes failed to apply.`)
    } finally {
      setApplying(false)
    }
  }

  return (
    <ModalShell
      open
      onClose={applying ? undefined : onClose}
      size="lg"
      title={<Trans>Standardize titles</Trans>}
      subtitle={(() => { const n = changed.length; const total = proposals.length; return <Trans>{n} of {total} books would change</Trans> })()}
      footer={
        <>
          {applying && (
            <div className="mr-auto flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--rail)' }}>
              <div className="h-full bg-primary rounded-full transition-[width] duration-300 ease-(--spring) motion-reduce:transition-none" style={{ width: `${progress}%` }} />
            </div>
          )}
          {error && <p className="mr-auto flex-1 text-[13px] text-destructive animate-in fade-in-40 duration-150 motion-reduce:animate-none">{error}</p>}
          <Pill variant="quiet" onClick={onClose} disabled={applying}>
            <Trans>Cancel</Trans>
          </Pill>
          <Pill variant="primary" onClick={apply} disabled={applying || checked.size === 0}>
            {applying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
            {(() => { const n = checked.size; return n > 0 ? <Trans>Apply {n} changes</Trans> : <Trans>Apply changes</Trans> })()}
          </Pill>
        </>
      }
    >
      {changed.length === 0 ? (
        <p className="text-[14px] text-muted-foreground text-center py-12">
          <Trans>No titles need standardizing.</Trans>
        </p>
      ) : (
        <div className="border-y border-border/70 divide-y divide-border/70">
          <div className="py-2.5">
            {(() => { const n = changed.length; return (
              <Checkbox
                checked={checked.size === changed.length}
                onChange={toggleAll}
                label={<span className="text-muted-foreground"><Trans>Select all ({n})</Trans></span>}
              />
            ) })()}
          </div>
          {changed.map(p => (
            <div key={p.book_id} className="py-3">
              <Checkbox
                checked={checked.has(p.book_id)}
                onChange={() => toggle(p.book_id)}
                className="flex w-full"
                label={
                  <span className="block min-w-0 space-y-1">
                    <span className="block text-[13px] text-muted-foreground line-through truncate">{p.current_title}{p.current_subtitle ? `: ${p.current_subtitle}` : ''}</span>
                    <span className="block text-[14px] space-y-0.5">
                      {p.proposed_title !== p.current_title && (
                        <span className="block"><span className="text-muted-foreground w-20 inline-block"><Trans>Title</Trans></span><span className="font-medium">{p.proposed_title}</span></span>
                      )}
                      {p.proposed_subtitle !== p.current_subtitle && (
                        <span className="block"><span className="text-muted-foreground w-20 inline-block"><Trans>Subtitle</Trans></span><span className="font-medium">{p.proposed_subtitle ?? <span className="italic text-muted-foreground/60"><Trans>cleared</Trans></span>}</span></span>
                      )}
                      {p.proposed_series !== p.current_series && (
                        <span className="block"><span className="text-muted-foreground w-20 inline-block"><Trans>Series</Trans></span><span className="font-medium">{p.proposed_series ?? <span className="italic text-muted-foreground/60"><Trans>cleared</Trans></span>}</span></span>
                      )}
                      {p.proposed_series_index !== p.current_series_index && (
                        <span className="block"><span className="text-muted-foreground w-20 inline-block"><Trans>Index</Trans></span><span className="font-medium">{p.proposed_series_index ?? <span className="italic text-muted-foreground/60"><Trans>cleared</Trans></span>}</span></span>
                      )}
                    </span>
                  </span>
                }
              />
            </div>
          ))}
        </div>
      )}
    </ModalShell>
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
    if (num !== null) return t`${seriesName} Chapter ${num}`
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

  const detected = books.filter(b => extractChapterNum(b.title) !== null).length

  return (
    <ModalShell
      open={open}
      onClose={applying ? undefined : handleClose}
      size="sm"
      title={<Trans>Assign chapter metadata</Trans>}
      subtitle={(() => {
        const n = bookIds.length
        return detected > 0
          ? t`${n} chapters uploaded, ${detected} chapter numbers detected`
          : t`${n} chapters uploaded`
      })()}
      footer={
        <>
          {applying && progress && (() => {
            const done = progress.done, total = progress.total
            return <span className="mr-auto text-[13px] text-muted-foreground"><Trans>Applying {done}/{total}…</Trans></span>
          })()}
          <Pill variant="quiet" onClick={handleClose} disabled={applying}>
            <Trans>Cancel</Trans>
          </Pill>
          <Pill variant="primary" onClick={apply} disabled={applying || !series || books.length === 0}>
            {applying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
            {(() => { const n = bookIds.length; return <Trans>Apply to {n} chapters</Trans> })()}
          </Pill>
        </>
      }
    >
      <div className="space-y-4 pt-1">
        <div>
          <FormLabel htmlFor="chapter-series"><Trans>Series</Trans></FormLabel>
          <Input
            id="chapter-series"
            list="chapter-series-list"
            value={series}
            onChange={e => setSeries(e.target.value)}
            placeholder={t`Start typing to search...`}
          />
          <datalist id="chapter-series-list">
            {facets.series.map(s => <option key={s} value={s} />)}
          </datalist>
        </div>

        <div>
          <FormLabel htmlFor="chapter-author"><Trans>Author</Trans></FormLabel>
          <Input
            id="chapter-author"
            list="chapter-author-list"
            value={author}
            onChange={e => setAuthor(e.target.value)}
            placeholder={t`Auto-filled from series`}
          />
          <datalist id="chapter-author-list">
            {facets.authors.map(a => <option key={a} value={a} />)}
          </datalist>
        </div>

        <div>
          <FormLabel htmlFor="chapter-type"><Trans>Book type</Trans></FormLabel>
          <Select id="chapter-type" value={bookTypeId} onChange={e => setBookTypeId(e.target.value)}>
            <option value="">{t`None`}</option>
            {bookTypes.map(bt => <option key={bt.id} value={String(bt.id)}>{bt.label}</option>)}
          </Select>
        </div>

        {books.length > 0 && series && (
          <div className="max-h-40 overflow-y-auto border-y border-border/70 divide-y divide-border/70">
            {books.map(b => {
              const num = extractChapterNum(b.title)
              return (
                <div key={b.id} className="flex items-center gap-2 py-1.5 text-[13px]">
                  <span className="text-muted-foreground truncate flex-1">{b.title}</span>
                  <span className="shrink-0 font-medium">
                    {num !== null ? t`Ch. ${num}` : <span className="text-warning">?</span>}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </ModalShell>
  )
}

// ── Main MetadataManager ──────────────────────────────────────────────────────

export function MetadataManager() {
  const { toast } = useToast()
  const navigate = useNavigate()
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
      restartRows()
      setBooks(auditData.filter(b => b.content_type !== 'chapter'))
      setBookTypes(btData)
      setLibraries(libData)
    } catch {
      setError(t`Failed to load metadata audit data.`)
    } finally {
      setLoading(false)
    }
  }

  function handleSearchChange(val: string) {
    setSearch(val)
    if (searchDebounce.current) clearTimeout(searchDebounce.current)
    searchDebounce.current = setTimeout(() => { restartRows(); setDebouncedSearch(val) }, 250)
  }

  function toggleSort(col: SortCol) {
    restartRows()
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('asc') }
  }

  function toggleMissingField(key: string) {
    restartRows()
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
        if (b.fields_present[f]) return false // book has this field - exclude
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

  const { handleToggle } = useShiftSelect(sorted.map(b => b.id))
  const tableRef = useRef<HTMLDivElement>(null)
  const { shown: rowsShown, win: rowWindow, restart: restartRows } = useRowsShown(sorted.length, tableRef)

  // Rendered rows: the first `rowsShown`, then (while filling) a spacer and the rows under the viewport.
  const n = sorted.length
  const gapRows = rowWindow && rowWindow.from > rowsShown && rowWindow.from < n ? rowWindow.from - rowsShown : 0
  const rowSlices: ({ start: number; items: AuditBook[] } | null)[] = gapRows > 0
    ? [{ start: 0, items: sorted.slice(0, rowsShown) }, null, { start: rowWindow!.from, items: sorted.slice(rowWindow!.from, rowWindow!.to) }]
    : [{ start: 0, items: rowsShown >= n ? sorted : sorted.slice(0, Math.max(rowsShown, rowWindow?.to ?? 0)) }]

  // After a select all or clear, rows outside `from..to` (the view) and past
  // `upto` still show the previous selection for a frame or two.
  const [selLag, setSelLag] = useState<{ prev: Set<number>; from: number; to: number; upto: number } | null>(null)
  useEffect(() => {
    if (!selLag) return
    let timer = 0
    const frame = requestAnimationFrame(() => {
      timer = window.setTimeout(() => setSelLag(l => !l || l.upto + SEL_STEP >= n ? null : { ...l, upto: l.upto + SEL_STEP }))
    })
    return () => { cancelAnimationFrame(frame); clearTimeout(timer) }
  }, [selLag, n])
  function rowSelected(i: number, id: number) {
    if (!selLag || i < selLag.upto || (i >= selLag.from && i < selLag.to)) return selected.has(id)
    return selLag.prev.has(id)
  }

  function toggleSelect(id: number, shiftKey: boolean) {
    if (shiftKey) tableRef.current?.setAttribute('data-snap', '')
    setSelLag(null)
    setSelected(prev => {
      const index = sorted.findIndex(b => b.id === id)
      return handleToggle(id, index, shiftKey, prev)
    })
  }

  // A stable handler for the memoised rows; it always calls the current toggleSelect.
  const toggleRef = useRef(toggleSelect)
  useEffect(() => { toggleRef.current = toggleSelect })
  const onToggleRow = useCallback((id: number, shiftKey: boolean) => toggleRef.current(id, shiftKey), [])

  // Many rows changing at once snap instead of fading their colours (hundreds
  // of colour transitions starting in one frame cost more than they show),
  // and render in slices (a transition), so a big table never holds up a frame.
  // data-snap stops the rows' and the checkboxes' (data-check-box)
  // transitions; the selectors name only those so toggling it restyles little.
  useLayoutEffect(() => {
    const el = tableRef.current
    if (!el?.hasAttribute('data-snap') || selLag) return
    let timer = 0
    const frame = requestAnimationFrame(() => { timer = window.setTimeout(() => el.removeAttribute('data-snap')) })
    return () => { cancelAnimationFrame(frame); clearTimeout(timer) }
  }, [selected, selLag])
  // One vertical scroller at every size: two nested ones let a wheel over the
  // toolbar bring the page back to the top while the table stays scrolled,
  // leaving a row half under the sticky header. Where at least MIN_TABLE_PX is
  // left below the table's top, the table scrolls in exactly that space and the
  // page does not; below that, the table takes its full height, the page
  // scrolls, and the header (data-paged) is not sticky.
  const tableShown = !loading || books.length > 0
  useLayoutEffect(() => {
    const el = tableRef.current
    const main = el?.closest('main')
    if (!el || !main || mode !== 'table') return
    let frame = 0
    const fit = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const top = el.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop
        // What sits below the table inside main: later siblings and bottom padding of each ancestor.
        let after = 0
        for (let n: Element = el; n !== main && n.parentElement; n = n.parentElement) {
          for (let sib = n.nextElementSibling; sib; sib = sib.nextElementSibling) after += (sib as HTMLElement).offsetHeight
          const cs = getComputedStyle(n.parentElement)
          after += parseFloat(cs.paddingBottom) + parseFloat(cs.borderBottomWidth)
        }
        const room = Math.floor(main.clientHeight - top - after)
        el.toggleAttribute('data-paged', room < MIN_TABLE_PX)
        el.style.maxHeight = room < MIN_TABLE_PX ? 'none' : `${room}px`
      })
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(main)
    if (el.parentElement) ro.observe(el.parentElement)
    return () => { ro.disconnect(); cancelAnimationFrame(frame) }
  }, [mode, tableShown])

  function setSelectedMany(next: Set<number>) {
    const el = tableRef.current
    // Measured before data-snap is set: that restyles every row, and reading
    // positions after it would force that restyle inside the click.
    const view = el ? rowsInView(el) : null
    el?.setAttribute('data-snap', '')
    startTransition(() => {
      setSelLag({ prev: selected, from: view?.from ?? 0, to: view?.to ?? FIRST_ROWS, upto: 0 })
      setSelected(next)
    })
  }

  function toggleSelectAll() {
    if (selected.size === sorted.length) {
      setSelectedMany(new Set())
    } else {
      setSelectedMany(new Set(sorted.map(b => b.id)))
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
      // ignore - user can retry
    } finally {
      setStandardizeLoading(false)
    }
  }

  function clearFilters() {
    setSearch('')
    restartRows()
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
        className={cn('flex items-center gap-0.5 text-[12px] font-bold uppercase tracking-[0.1em] hover:text-foreground transition-colors touch-feedback', PRESS, active ? 'text-foreground' : 'text-muted-foreground')}
      >
        {label}
        {active ? (sortDir === 'asc' ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />) : <ChevronUp className="w-3 h-3 opacity-20" />}
      </button>
    )
  }

  // The table and the review cross-fade in one place.
  return (
    <Swap id={mode}>
      {mode === 'review' ? (
      <div className="h-[calc(100vh-56px)] flex flex-col">
        <ReviewFlow
          queue={reviewQueue}
          onBack={() => { setMode('table'); loadData() }}
          onBookUpdated={handleBookUpdated}
        />
      </div>
      ) : tableView()}
    </Swap>
  )

  function tableView() {
  return (
    <div className="flex flex-col gap-3">
      {/* Standardize modal */}
      <AnimatePresence>
        {standardizeProposals && (
          <StandardizeModal
            key="standardize"
            proposals={standardizeProposals}
            onClose={() => setStandardizeProposals(null)}
            onApplied={() => { setStandardizeProposals(null); loadData() }}
          />
        )}
      </AnimatePresence>

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
        onWishMatches={(wishIds) => {
          const n = wishIds.length
          toast.info(plural(n, {
            one: 'This upload satisfies # wish.',
            other: 'This upload satisfies # wishes.',
          }), { action: { label: t`Open Wishlist`, onClick: () => navigate('/admin/wishlist') } })
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
        <div className="relative w-full sm:w-56">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
          <Input
            value={search}
            onChange={e => handleSearchChange(e.target.value)}
            placeholder={t`Search title, author…`}
            aria-label={t`Search title, author…`}
            className="pl-8 pr-8"
          />
          {search && (
            <button onClick={() => handleSearchChange('')} aria-label={t`Clear search`} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors animate-in fade-in-40 duration-150 motion-reduce:animate-none">
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        <Select value={filterSeries} onChange={e => { restartRows(); setFilterSeries(e.target.value) }} aria-label={t`Series`} className="w-auto max-w-[14rem]">
          <option value="">{t`All series`}</option>
          {allSeries.map(s => <option key={s} value={s}>{s}</option>)}
        </Select>

        <Select value={filterTypeId} onChange={e => { restartRows(); setFilterTypeId(e.target.value) }} aria-label={t`Type`} className="w-auto">
          <option value="">{t`All types`}</option>
          {bookTypes.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
        </Select>

        <Select value={filterLibId} onChange={e => { restartRows(); setFilterLibId(e.target.value) }} aria-label={t`Library`} className="w-auto">
          <option value="">{t`All libraries`}</option>
          {libraries.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
        </Select>

        <MissingFilter
          open={missingDropOpen}
          setOpen={setMissingDropOpen}
          selected={missingFields}
          onToggle={toggleMissingField}
        />

        {/* Kept in place while no filter is set, so the count beside it does not jump. */}
        <Pill
          variant="quiet"
          onClick={clearFilters}
          disabled={!hasFilters}
          aria-hidden={!hasFilters || undefined}
          tabIndex={hasFilters ? undefined : -1}
          className={cn('transition-[opacity,color,background-color] duration-150', !hasFilters && 'opacity-0 disabled:opacity-0')}
        >
          <Trans>Clear</Trans>
        </Pill>

        <span className="text-[13px] text-muted-foreground ml-1 tabular-nums">
          {(() => {
            const shown = filtered.length, all = books.length
            return shown !== all
              ? t`${shown} of ${all} books`
              : plural(all, { one: '# book', other: '# books' })
          })()}
        </span>
      </div>

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-2">
        <Pill variant="secondary" onClick={() => { setChapterMode(false); setUploadModalOpen(true) }}>
          <Upload className="w-3.5 h-3.5" />
          <Trans>Upload</Trans>
        </Pill>
        <Pill variant="secondary" onClick={() => { setChapterMode(true); setUploadModalOpen(true) }}>
          <BookMarked className="w-3.5 h-3.5" />
          <Trans>Upload chapters</Trans>
        </Pill>
        <Pill variant="secondary" onClick={() => startBatchFetch(sorted.map(b => b.id))} disabled={sorted.length === 0}>
          <Sparkles className="w-3.5 h-3.5" />
          {(() => { const n = Math.min(sorted.length, 100); return <Trans>Batch fetch ({n})</Trans> })()}
        </Pill>
        <Pill
          variant={selected.size > 0 ? 'secondary' : 'primary'}
          onClick={() => startReview(sorted.map(b => b.id))}
          disabled={sorted.length === 0}
        >
          <Sparkles className="w-3.5 h-3.5" />
          {(() => { const n = sorted.length; return <Trans>Review all filtered ({n})</Trans> })()}
        </Pill>
        <Pill variant="secondary" onClick={() => startStandardize(sorted.map(b => b.id))} disabled={sorted.length === 0 || standardizeLoading}>
          {standardizeLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wand2 className="w-3.5 h-3.5" />}
          <Trans>Standardize titles</Trans>
        </Pill>
      </div>

      {/* Bulk bar */}
      <Unfold open={selected.size > 0} gap={12}>
        <div className="flex flex-wrap items-center gap-2 border-y border-border/70 py-2">
          {(() => { const n = selected.size; return <span className="text-[14px] font-medium text-foreground">{t`${n} selected`}</span> })()}
          <div className="flex-1" />
          <Pill variant="secondary" onClick={() => startBatchFetch([...selected])}>
            <Sparkles className="w-3.5 h-3.5" />
            <Trans>Batch fetch</Trans>
          </Pill>
          <Pill variant="primary" onClick={() => startReview([...selected])}>
            <Sparkles className="w-3.5 h-3.5" />
            <Trans>Review selected</Trans>
          </Pill>
          <Pill variant="secondary" onClick={() => startStandardize([...selected])} disabled={standardizeLoading}>
            {standardizeLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wand2 className="w-3.5 h-3.5" />}
            <Trans>Standardize</Trans>
          </Pill>
          <Pill variant="quiet" icon onClick={() => setSelectedMany(new Set())} aria-label={t`Clear selection`} title={t`Clear selection`}>
            <X className="w-4 h-4" />
          </Pill>
        </div>
      </Unfold>

      {/* Error / loading */}
      <Unfold open={!!error} gap={12}>
        <p className="text-[14px] text-destructive">{error}</p>
      </Unfold>
      {/* The first load holds the table's place; a reload keeps the table, dimmed. */}
      {loading && books.length === 0 && (
        <RowsSkeleton rows={10} rowClassName="h-[53px]" />
      )}

      {/* Table: rows on hairlines */}
      {(!loading || books.length > 0) && (
        <div ref={tableRef} className={cn('overflow-auto max-h-[calc(100dvh-220px)] border-y border-border/70 transition-opacity duration-150 data-snap:[&_tr]:transition-none data-snap:[&_[data-check-box]]:transition-none data-paged:[&_thead]:static data-paged:will-change-transform', loading && 'opacity-50 pointer-events-none')}>
          <table className="w-full min-w-[1120px] table-fixed text-[13px]">
            <thead className="sticky top-0 z-10 bg-background">
              <tr className="border-b border-border/70">
                <th className="w-9 pl-0.5 pr-2 py-2.5 text-left">
                  <Checkbox
                    checked={selected.size === sorted.length && sorted.length > 0}
                    onChange={toggleSelectAll}
                    aria-label={t`Select all`}
                  />
                </th>
                <th className="w-10 px-2 py-2.5" />
                <th className="px-2 py-2.5 text-left"><SortHeader col="title" label={t`Title`} /></th>
                <th className={cn('w-28 px-2 py-2.5 text-left', HEAD)}><Trans>Subtitle</Trans></th>
                <th className="w-36 px-2 py-2.5 text-left"><SortHeader col="author" label={t`Author`} /></th>
                <th className="w-36 px-2 py-2.5 text-left"><SortHeader col="series" label={t`Series`} /></th>
                <th className={cn('w-10 px-2 py-2.5 text-left', HEAD)}>#</th>
                <th className="w-[4.5rem] px-2 py-2.5 text-left"><SortHeader col="year" label={t`Year`} /></th>
                <th className={cn('w-14 px-2 py-2.5 text-center', HEAD)}><Trans>Desc</Trans></th>
                <th className={cn('w-14 px-2 py-2.5 text-center', HEAD)}><Trans>Cover</Trans></th>
                <th className={cn('w-28 px-2 py-2.5 text-left', HEAD)}>ISBN</th>
                <th className={cn('w-14 px-2 py-2.5 text-left', HEAD)}><Trans>Lang</Trans></th>
                <th className="w-16 pl-2 pr-0 py-2.5 text-left"><SortHeader col="completeness" label={t`Score`} /></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {/* One flat keyed list, so rows keep their elements when the spacer goes. */}
              {rowSlices.flatMap(slice => slice === null ? [
                <tr key="gap" data-gap="" aria-hidden><td colSpan={13} className="p-0" style={{ height: gapRows * (rowWindow?.pitch ?? 0) - 1 }} /></tr>,
              ] : slice.items.map((book, i) => (
                <MetaRow key={book.id} book={book} selected={rowSelected(slice.start + i, book.id)} onToggle={onToggleRow} setBooks={setBooks} />
              )))}
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={13} className="px-4 py-12 text-center text-muted-foreground text-[14px]">
                    <Trans>No books match the current filters.</Trans>
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
}
