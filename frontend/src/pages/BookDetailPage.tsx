import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  Camera, Download, Edit2, Save, X,
  Calendar, Globe, Hash, Building2, FileText, Trash2, Loader2,
  Sparkles, Library, Check, BookMarked, ChevronLeft, ChevronRight, Home,
  Tag as TagIcon, StickyNote, ChevronDown, Archive, Star, AlignLeft,
  Plus, TrendingUp, TrendingDown, Minus, Info
} from 'lucide-react'
import { useAuth, isMember } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { ThemeToggle } from '@/components/ThemeToggle'
import { MetadataFetchModal } from '@/components/MetadataFetchModal'
import { CoverPickerModal } from '@/components/CoverPickerModal'
import { SendButton } from '@/components/SendButton'
import { BookAnimation } from '@/components/BookAnimation'
import { CoverImage } from '@/components/CoverImage'
import { AutocompleteInput } from '@/components/AutocompleteInput'
import { api } from '@/lib/api'
import type { BookDetail, BookFile, Library as LibraryType, BookStatus, ReadingStatus } from '@/lib/books'
import { formatBytes } from '@/lib/books'
import { useBookTypes } from '@/lib/bookTypes'
import { cn, formatDuration, formatDate } from '@/lib/utils'

interface Facets {
  authors: string[]
  series: string[]
  tags: string[]
  formats: string[]
}

interface Annotation {
  id: number
  anchor: string
  highlighted_text: string | null
  note: string | null
  chapter: string | null
  color: string | null
  datetime: string | null
  updated_at: string
}

interface BookReadingStats {
  total_seconds: number
  sessions: number
  pages_turned: number
  avg_session_seconds: number
  pace_pages_per_min: number | null
  first_read: string | null
  last_read: string | null
  finished_at: string | null
  progress: number | null
  status: string
  session_timeline: { date: string; seconds: number; pages: number; progress_pct: number | null }[]
  by_source: { device: string; seconds: number; sessions: number }[]
  momentum: { recent_seconds: number; prior_seconds: number; delta_pct: number | null; direction: string } | null
  estimated_finish_seconds: number | null
}

interface BookIntensity {
  bins: number
  curve: number[]
  total_seconds: number
  total_pages: number
  pages_read: number
  pct_read: number
  reread_bins: number
}

interface ReadingStatsResponse {
  own: BookReadingStats
  aggregate: {
    total_seconds: number
    total_sessions: number
    distinct_readers: number
  } | null
  intensity: BookIntensity | null
}

async function downloadFile(book: BookDetail, f: BookFile) {
  const token = localStorage.getItem('tome_token')
  const resp = await fetch(`/api/books/${book.id}/download/${f.id}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok) throw new Error('Download failed')
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = f.filename || `${book.title}.${f.format.toLowerCase()}`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export function BookDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { user } = useAuth()
  const { toast } = useToast()
  const [book, setBook] = useState<BookDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const bookTypes = useBookTypes()
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [downloadingId, setDownloadingId] = useState<number | null>(null)
  const [draft, setDraft] = useState<Partial<BookDetail>>({})
  const [draftBookTypeId, setDraftBookTypeId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fetchModalOpen, setFetchModalOpen] = useState(false)
  const [coverPickerOpen, setCoverPickerOpen] = useState(false)
  const [libraries, setLibraries] = useState<LibraryType[]>([])
  const [libMenuOpen, setLibMenuOpen] = useState(false)
  const libMenuRef = useRef<HTMLDivElement>(null)
  const [libPending, setLibPending] = useState<Set<number>>(new Set())
  // optimistic local library_ids so UI updates instantly
  const [localLibIds, setLocalLibIds] = useState<number[]>([])
  const [bookStatus, setBookStatus] = useState<ReadingStatus>('unread')
  const [progressPct, setProgressPct] = useState<number | null>(null)
  const [cfi, setCfi] = useState<string | null>(null)
  const [progressAnimated, setProgressAnimated] = useState(false)
  const [statusSaving, setStatusSaving] = useState(false)
  const [statusPopKey, setStatusPopKey] = useState(0)
  const [rating, setRating] = useState<number | null>(null)
  const [hoverRating, setHoverRating] = useState<number | null>(null)
  const [review, setReview] = useState('')       // saved review text
  const [reviewDraft, setReviewDraft] = useState('')  // in-progress edit
  const [editingReview, setEditingReview] = useState(false)
  const [kosyncDevice, setKosyncDevice] = useState<string | null>(null)
  const [annotations, setAnnotations] = useState<Annotation[]>([])
  const [confirmingHighlight, setConfirmingHighlight] = useState<number | null>(null)
  const [highlightsOpen, setHighlightsOpen] = useState(true)
  const [readingStats, setReadingStats] = useState<ReadingStatsResponse | null>(null)
  const [statsOpen, setStatsOpen] = useState(true)
  const [descExpanded, setDescExpanded] = useState(false)
  const [facets, setFacets] = useState<Facets>({ authors: [], series: [], tags: [], formats: [] })
  const [draftTags, setDraftTags] = useState<string[]>([])
  const [tagInput, setTagInput] = useState('')
  const [adjacent, setAdjacent] = useState<{
    prev: { id: number; title: string; series_index?: number } | null
    next: { id: number; title: string; series_index?: number } | null
    mode: 'series' | 'author' | null
  } | null>(null)

  const isDirty = editing && book != null && JSON.stringify(draft) !== JSON.stringify(book)
  const canEdit = isMember(user)
  const canDelete = isMember(user)

  useEffect(() => {
    if (!isDirty) return
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault()
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [isDirty])

  useEffect(() => {
    if (!libMenuOpen) return
    function onMouseDown(e: MouseEvent) {
      if (libMenuRef.current && !libMenuRef.current.contains(e.target as Node)) {
        setLibMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [libMenuOpen])

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.key === 'Escape') {
        e.preventDefault()
        navigate(-1)
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'r' && book) {
        const hasReadableFile = book.files?.some(f => ['epub', 'cbz', 'cbr', 'pdf'].includes(f.format))
        if (hasReadableFile) {
          navigate(`/reader/${book.id}`)
        }
        return
      }
      if (e.key === 'e' && canEdit) {
        e.preventDefault()
        if (editing) {
          cancelEdit()
        } else {
          startEdit()
        }
        return
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [navigate, book, editing, canEdit])

  useEffect(() => {
    if (!id) return
    setAdjacent(null)
    api.get<BookDetail>(`/books/${id}`)
      .then(b => { setBook(b); setDraft(b); setLocalLibIds(b.library_ids ?? []) })
      .catch(() => setError('Book not found'))
      .finally(() => setLoading(false))
    api.get<LibraryType[]>('/libraries').then(setLibraries).catch(() => toast.error('Failed to load libraries'))
    api.get<BookStatus>(`/books/${id}/status`).then(s => { setBookStatus(s.status); setProgressPct(s.progress_pct); setCfi(s.cfi ?? null); setProgressAnimated(false); setRating(s.rating ?? null); setReview(s.review ?? ''); setEditingReview(false) }).catch(() => {})
    api.get<typeof adjacent>(`/books/${id}/adjacent`).then(setAdjacent).catch(() => {})
    api.get<{ linked: boolean; device?: string }>(`/books/${id}/kosync-progress`)
      .then(r => { if (r.linked && r.device) setKosyncDevice(r.device) })
      .catch(() => {})  // KOSync is optional — silent fail is fine
    api.get<Annotation[]>(`/books/${id}/annotations`).then(setAnnotations).catch(() => {})
    api.get<ReadingStatsResponse>(`/books/${id}/reading-stats`).then(setReadingStats).catch(() => {})
  }, [id])

  // Animate progress bar from 0 after it loads
  useEffect(() => {
    if (progressPct != null && progressPct > 0 && !progressAnimated) {
      const t = setTimeout(() => setProgressAnimated(true), 100)
      return () => clearTimeout(t)
    }
  }, [progressPct, progressAnimated])

  async function toggleLibrary(libId: number) {
    if (!book || libPending.has(libId)) return
    const inLib = localLibIds.includes(libId)
    // optimistic update
    setLocalLibIds(prev => inLib ? prev.filter(x => x !== libId) : [...prev, libId])
    setLibPending(prev => new Set(prev).add(libId))
    try {
      if (inLib) {
        await api.delete(`/libraries/${libId}/books/${book.id}`)
      } else {
        await api.post(`/libraries/${libId}/books`, { book_ids: [book.id] })
      }
    } catch {
      // revert on error
      setLocalLibIds(prev => inLib ? [...prev, libId] : prev.filter(x => x !== libId))
    } finally {
      setLibPending(prev => { const s = new Set(prev); s.delete(libId); return s })
    }
  }

  function applyStatus(s: BookStatus) {
    setBookStatus(s.status)
    setProgressPct(s.progress_pct)
    setCfi(s.cfi ?? null)
    setProgressAnimated(false)
    setStatusPopKey(k => k + 1)
  }

  async function restoreStatus(prev: { status: ReadingStatus; progress_pct: number | null; cfi: string | null }) {
    if (!id) return
    try {
      // Send progress + cfi back too, so undoing an "unread" (which clears them) restores your position.
      const restored = await api.put<BookStatus>(`/books/${id}/status`, prev)
      applyStatus(restored)
    } catch {
      toast.error('Failed to undo')
    }
  }

  const STATUS_LABEL: Record<ReadingStatus, string> = {
    unread: 'Marked unread — reading progress cleared',
    reading: 'Marked as reading',
    read: 'Marked as read',
    shelved: 'Shelved — kept your progress',
  }

  async function handleStatusChange(s: ReadingStatus) {
    if (!id || statusSaving || s === bookStatus) return
    const prev = { status: bookStatus, progress_pct: progressPct, cfi }
    setStatusSaving(true)
    try {
      const updated = await api.put<BookStatus>(`/books/${id}/status`, { status: s })
      applyStatus(updated)
      toast.info(STATUS_LABEL[s], { action: { label: 'Undo', onClick: () => restoreStatus(prev) } })
    } catch {
      toast.error('Failed to update reading status')
    } finally {
      setStatusSaving(false)
    }
  }

  async function saveRating(next: number | null) {
    if (!id) return
    const prev = rating
    setRating(next)                  // optimistic
    try {
      await api.put<BookStatus>(`/books/${id}/rating`, { rating: next })
    } catch {
      setRating(prev)
      toast.error('Failed to save rating')
    }
  }

  function startEditingReview() {
    setReviewDraft(review)
    setEditingReview(true)
  }

  async function saveReview() {
    if (!id) return
    const next = reviewDraft.trim()
    setReview(next)                  // optimistic — collapse to rendered view
    setEditingReview(false)
    try {
      await api.put<BookStatus>(`/books/${id}/rating`, { review: next || null })
    } catch {
      toast.error('Failed to save review')
    }
  }

  async function handleDelete() {
    if (!book) return
    setDeleting(true)
    try {
      await api.delete(`/books/${book.id}`)
      toast.success('Book deleted')
      navigate('/')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Delete failed'
      setError(msg)
      toast.error(msg)
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  function startEdit() {
    if (!book) return
    setDraft({ ...book })
    setDraftTags(book.tags.map(t => t.tag))
    setDraftBookTypeId(book.book_type_id ?? null)
    setEditing(true)
    api.get<Facets>('/books/facets').then(setFacets).catch(() => toast.error('Failed to load facets'))
  }
  function cancelEdit() {
    if (!book) return
    setDraft({ ...book })
    setDraftTags(book.tags.map(t => t.tag))
    setDraftBookTypeId(book.book_type_id ?? null)
    setTagInput('')
    setEditing(false)
  }

  async function saveEdit() {
    if (!book) return
    setSaving(true)
    try {
      const updated = await api.put<BookDetail>(`/books/${book.id}`, {
        title: draft.title,
        subtitle: draft.subtitle,
        author: draft.author,
        series: draft.series,
        series_index: draft.series_index,
        isbn: draft.isbn,
        publisher: draft.publisher,
        description: draft.description,
        language: draft.language,
        year: draft.year,
        tags: draftTags,
        book_type_id: draftBookTypeId,
        content_type: draft.content_type,
      })
      setBook(updated)
      setDraftTags(updated.tags.map(t => t.tag))
      setDraftBookTypeId(updated.book_type_id ?? null)
      setEditing(false)
      toast.success('Metadata saved')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
      toast.error(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  function field(key: keyof BookDetail) {
    return (draft as Record<string, unknown>)[key] as string | undefined
  }
  function setField(key: keyof BookDetail, value: string) {
    setDraft(d => ({ ...d, [key]: value || null }))
  }

  if (loading) return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <BookAnimation variant="refresh" className="block w-10 h-10 text-primary" />
    </div>
  )

  if (error || !book) return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <div className="text-center gap-3 flex flex-col items-center">
        <p className="text-muted-foreground">{error ?? 'Book not found'}</p>
        <Link to="/" className="text-sm text-primary hover:underline">← Back to library</Link>
      </div>
    </div>
  )

  // ── Render helpers (shared across layout variants) ──────────────────────────

  const coverBlock = (
    <div
      className={cn(
        "relative mx-auto w-40 sm:w-48 aspect-[2/3] rounded-xl overflow-hidden border border-border bg-muted shadow-lg",
        canEdit && "group cursor-pointer"
      )}
      onClick={() => canEdit && setCoverPickerOpen(true)}
    >
      <CoverImage
        src={book.cover_path ? `/api/books/${book.id}/cover?v=${encodeURIComponent(book.updated_at)}` : null}
        alt={book.title}
        loading="eager"
        iconClassName="w-12 h-12"
      />
      {canEdit && (
        <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
          <Camera className="w-8 h-8 text-white drop-shadow" />
        </div>
      )}
    </div>
  )

  const readButton = book.files.some(f => ['epub', 'cbz', 'cbr', 'pdf'].includes(f.format)) ? (
    <button
      onClick={() => navigate(`/reader/${book.id}`)}
      className="mt-4 flex items-center justify-center gap-2 w-full px-3 py-2.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md"
    >
      <BookMarked className="w-4 h-4" />
      Read
    </button>
  ) : null

  const downloadButtons = book.files.length > 0 ? (
    <div className="mt-4 space-y-2">
      {book.files.map(f => (
        <button
          key={f.id}
          onClick={async () => {
            setDownloadingId(f.id)
            try { await downloadFile(book, f) }
            catch (e) { setError(e instanceof Error ? e.message : 'Download failed') }
            finally { setDownloadingId(null) }
          }}
          disabled={downloadingId === f.id}
          className={cn(
            'flex items-center justify-between gap-2 w-full px-3 py-2 rounded-lg text-sm',
            'border border-border bg-card text-foreground',
            'hover:bg-muted hover:-translate-y-0.5 hover:shadow-sm',
            'transition-all duration-200 disabled:opacity-60 disabled:cursor-not-allowed'
          )}
        >
          <span className="flex items-center gap-1.5">
            {downloadingId === f.id
              ? <Loader2 className="w-3.5 h-3.5 text-muted-foreground animate-spin" />
              : <Download className="w-3.5 h-3.5 text-muted-foreground" />}
            <span className="uppercase font-medium text-xs">{f.format}</span>
          </span>
          {f.file_size && (
            <span className="text-xs text-muted-foreground">{formatBytes(f.file_size)}</span>
          )}
        </button>
      ))}
    </div>
  ) : null

  const sendToDeviceButton = isMember(user) && book.files.length > 0 ? (
    <SendButton
      books={[{ id: book.id, title: book.title, files: book.files }]}
      variant="rail"
    />
  ) : null

  // Left rail actions: cover + Read + Download + Send
  const leftRailActions = (
    <>
      {coverBlock}
      {readButton}
      {downloadButtons}
      {sendToDeviceButton}
    </>
  )

  // Title block: editing title/subtitle/author/series or display
  const titleBlock = (
    <>
      {editing ? (
        <>
          <input
            value={field('title') ?? ''}
            onChange={e => setField('title', e.target.value)}
            className="w-full text-2xl font-bold text-foreground bg-muted rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring mb-1"
            placeholder="Title"
          />
          <input
            value={field('subtitle') ?? ''}
            onChange={e => setField('subtitle', e.target.value)}
            className="w-full text-base text-muted-foreground bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring mt-1"
            placeholder="Subtitle (optional)"
          />
        </>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl sm:text-3xl font-bold text-foreground">{book.title}</h1>
            {book.content_type === 'chapter' && (
              <span className="shrink-0 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider rounded bg-muted text-muted-foreground border border-border">
                Chapter
              </span>
            )}
          </div>
          {book.subtitle && (
            <p className="text-sm sm:text-base text-muted-foreground mt-0.5">{book.subtitle}</p>
          )}
        </>
      )}

      {editing ? (
        <AutocompleteInput
          value={field('author') ?? ''}
          onChange={v => setField('author', v)}
          suggestions={facets.authors}
          placeholder="Author"
          className="w-full text-base bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring mt-2"
        />
      ) : book.author ? (
        <button
          onClick={() => navigate(`/?tab=books&author=${encodeURIComponent(book.author!)}`)}
          className="text-base text-muted-foreground mt-1 hover:text-primary transition-colors text-left"
        >
          {book.author}
        </button>
      ) : null}

      {(book.series || editing) && (
        <div className="flex items-center gap-2 mt-2 flex-wrap">
          {editing ? (
            <>
              <AutocompleteInput
                value={field('series') ?? ''}
                onChange={v => setField('series', v)}
                suggestions={facets.series}
                placeholder="Series name"
                className="flex-1 min-w-0 text-sm bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <input
                value={field('series_index') ?? ''}
                onChange={e => setField('series_index', e.target.value)}
                type="number"
                step="0.1"
                className="w-20 text-sm bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
                placeholder="#"
              />
            </>
          ) : (
            <button
              onClick={() => navigate(`/?tab=books&series=${encodeURIComponent(book.series!)}`)}
              className="text-sm text-primary/80 hover:text-primary transition-colors text-left"
            >
              {book.series}{book.series_index != null ? ` #${book.series_index}` : ''}
            </button>
          )}
        </div>
      )}
    </>
  )

  // Status + progress bar
  const statusProgressBlock = (
    <div className="mt-4 mb-4 flex items-center flex-wrap gap-1.5">
      {(['unread', 'reading', 'read'] as ReadingStatus[]).map(s => (
        <button
          key={bookStatus === s ? `${s}-${statusPopKey}` : s}
          disabled={statusSaving}
          onClick={() => handleStatusChange(s)}
          className={cn(
            'px-2.5 py-1 rounded-md text-xs font-medium border transition-all capitalize',
            bookStatus === s
              ? s === 'reading'
                ? 'bg-warning/10 border-warning text-warning animate-[pop_0.2s_ease-out]'
                : s === 'read'
                  ? 'bg-success/10 border-success text-success animate-[pop_0.2s_ease-out]'
                  : 'bg-muted border-border text-foreground animate-[pop_0.2s_ease-out]'
              : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground'
          )}
        >
          {s}
        </button>
      ))}
      {/* Shelved — set apart from the linear unread→reading→read progression.
          Keeps your reading position; just removes the book from Continue
          Reading, series progress, and stats until you pick it back up. */}
      <span className="mx-1 h-4 w-px bg-border self-center" aria-hidden />
      <button
        key={bookStatus === 'shelved' ? `shelved-${statusPopKey}` : 'shelved'}
        disabled={statusSaving}
        onClick={() => handleStatusChange('shelved')}
        title="Shelved — set aside, keeps your progress"
        className={cn(
          'px-2.5 py-1 rounded-md text-xs font-medium border transition-all inline-flex items-center gap-1.5',
          bookStatus === 'shelved'
            ? 'bg-muted border-foreground/30 text-foreground animate-[pop_0.2s_ease-out]'
            : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground'
        )}
      >
        <Archive className="w-3.5 h-3.5" />
        Shelved
      </button>
      {statusSaving && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground self-center" />}
      {progressPct != null && progressPct > 0 && bookStatus !== 'unread' && (
        <div className="flex items-center gap-2 w-full mt-1.5">
          <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all duration-700 ease-out"
              style={{ width: progressAnimated ? `${Math.round(progressPct * 100)}%` : '0%' }}
            />
          </div>
          <span className="text-xs text-muted-foreground tabular-nums shrink-0">
            {Math.round(progressPct * 100)}%
            {kosyncDevice && (
              <span className="ml-1 opacity-60">· {kosyncDevice}</span>
            )}
          </span>
        </div>
      )}
    </div>
  )

  // Your rating + optional review
  const ratingBlock = (
    <div className="mb-5">
      <div className="flex items-center gap-2.5">
        <div
          className="flex items-center gap-0.5"
          onMouseLeave={() => setHoverRating(null)}
        >
          {[1, 2, 3, 4, 5].map(n => {
            const active = (hoverRating ?? rating ?? 0) >= n
            return (
              <button
                key={n}
                type="button"
                aria-label={`${n} star${n > 1 ? 's' : ''}`}
                onMouseEnter={() => setHoverRating(n)}
                onClick={() => saveRating(rating === n ? null : n)}
                className="p-0.5 transition-transform hover:scale-110"
              >
                <Star
                  className={cn(
                    'w-5 h-5 transition-colors',
                    active ? 'fill-rating text-rating' : 'text-muted-foreground/40'
                  )}
                />
              </button>
            )
          })}
        </div>
        <span className="text-xs text-muted-foreground">
          {rating ? `You rated this ${rating}/5` : 'Rate this book'}
        </span>
        {!review && !editingReview && (
          <button
            type="button"
            onClick={startEditingReview}
            className="text-xs text-primary hover:underline"
          >
            Add a review
          </button>
        )}
      </div>
      {editingReview ? (
        <textarea
          value={reviewDraft}
          onChange={e => setReviewDraft(e.target.value)}
          onBlur={saveReview}
          autoFocus
          rows={3}
          placeholder="What did you think? (optional, saved automatically)"
          className="mt-2 w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary focus:outline-none"
        />
      ) : review ? (
        <div className="group/review relative mt-2.5 border-l-2 border-primary/30 pl-3">
          <p className="whitespace-pre-wrap pr-7 text-sm leading-relaxed text-foreground/90">{review}</p>
          <button
            type="button"
            onClick={startEditingReview}
            aria-label="Edit review"
            title="Edit review"
            className="absolute right-0 top-0 p-1 text-muted-foreground/50 transition-colors hover:text-foreground"
          >
            <Edit2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : null}
    </div>
  )

  // Full collapsible stats hero block. Shows when there are live sessions OR
  // imported KOReader page-stats (a book read only on the device has no sessions
  // but does have an intensity curve).
  const refreshStats = () =>
    api.get<ReadingStatsResponse>(`/books/${id}/reading-stats`).then(setReadingStats).catch(() => {})
  const hasReadingData = readingStats && (readingStats.own.sessions > 0 || !!readingStats.intensity)
  const statsFull = readingStats ? (
    <div className="mt-1 mb-5">
      <div className="flex items-center gap-2 mb-2.5">
        <button
          type="button"
          onClick={() => setStatsOpen(o => !o)}
          aria-expanded={statsOpen}
          className="flex items-center gap-1.5 font-display text-base text-foreground hover:text-primary transition-colors"
        >
          Reading Stats
          <ChevronDown className={cn('w-3.5 h-3.5 transition-transform', !statsOpen && '-rotate-90')} />
        </button>
      </div>
      {statsOpen && (
        hasReadingData ? (
          <div className="flex flex-col gap-3">
            {readingStats.own.sessions > 0 && (
              <StatsLayoutHero
                own={readingStats.own}
                aggregate={readingStats.aggregate}
                bookId={Number(id)}
                onChange={refreshStats}
              />
            )}
            {readingStats.intensity && <IntensityBlock data={readingStats.intensity} />}
          </div>
        ) : (
          // No history yet — the manual-tracking entry point (paper / un-synced device).
          <div className="rounded-xl border border-border bg-card px-5 py-4">
            <p className="text-sm text-muted-foreground">
              No reading logged yet. Track time by hand — handy for a paper copy or a device that isn&apos;t synced.
            </p>
            <div className="mt-3">
              <ManualLogControls bookId={Number(id)} onChange={refreshStats} />
            </div>
          </div>
        )
      )}
    </div>
  ) : null

  // Genres block for the left sidebar (variant 1)
  const genresBlock = editing ? (
    <div>
      <p className="font-display text-base text-foreground mb-2">Genres</p>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {draftTags.map(tag => (
          <span key={tag} className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-muted border border-border text-foreground">
            {tag}
            <button
              type="button"
              onClick={() => setDraftTags(prev => prev.filter(t => t !== tag))}
              className="text-muted-foreground hover:text-destructive transition-colors"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
      </div>
      <AutocompleteInput
        value={tagInput}
        onChange={setTagInput}
        suggestions={facets.tags.filter(t => !draftTags.includes(t))}
        placeholder="Add tag…"
        onSelect={tag => {
          if (tag && !draftTags.includes(tag)) {
            setDraftTags(prev => [...prev, tag])
            setTagInput('')
          }
        }}
        onEnter={val => {
          const trimmed = val.trim()
          if (trimmed && !draftTags.includes(trimmed)) {
            setDraftTags(prev => [...prev, trimmed])
            setTagInput('')
          }
        }}
        className="text-sm bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring w-full"
      />
    </div>
  ) : book.tags.length > 0 ? (
    <div>
      <p className="font-display text-base text-foreground mb-2">Genres</p>
      <div className="flex flex-wrap gap-1.5">
        {book.tags.map(t => (
          <button
            key={t.id}
            onClick={() => navigate(`/?tab=books&tag=${encodeURIComponent(t.tag)}`)}
            className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border hover:bg-primary/10 hover:text-primary hover:border-primary/20 transition-colors"
          >
            {t.tag}
          </button>
        ))}
      </div>
    </div>
  ) : null

  // Full-width Details grid for below the description (variant 1)
  const metadataGridFull = (
    <div className="mt-6">
      <div className="font-display text-base text-foreground mb-3">Details</div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-6 gap-y-4">
        <MetaField icon={<Calendar className="w-3.5 h-3.5" />} label="Year"
          value={field('year') ?? ''} editing={editing}
          onChange={v => setField('year', v)} type="number" placeholder="2024" />
        <MetaField icon={<Globe className="w-3.5 h-3.5" />} label="Language"
          value={field('language') ?? ''} editing={editing}
          onChange={v => setField('language', v)} placeholder="en" />
        <MetaField icon={<Hash className="w-3.5 h-3.5" />} label="ISBN"
          value={field('isbn') ?? ''} editing={editing}
          onChange={v => setField('isbn', v)} placeholder="978-..." />
        <MetaField icon={<Building2 className="w-3.5 h-3.5" />} label="Publisher"
          value={field('publisher') ?? ''} editing={editing}
          onChange={v => setField('publisher', v)} placeholder="Publisher" />
        <MetaField icon={<FileText className="w-3.5 h-3.5" />} label="Format"
          value={book.files.map(f => f.format.toUpperCase()).join(', ')} editing={false}
          onChange={() => {}} />
        <MetaField icon={<AlignLeft className="w-3.5 h-3.5" />} label="Words"
          value={book.word_count != null ? `${book.word_count.toLocaleString()} words` : ''}
          editing={false} onChange={() => {}} />
        {/* Book Type */}
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground mt-0.5 shrink-0"><TagIcon className="w-3.5 h-3.5" /></span>
          <div className="flex-1 min-w-0">
            <p className="text-xs text-muted-foreground/70 mb-0.5">Type</p>
            {editing ? (
              <select
                value={draftBookTypeId ?? ''}
                onChange={e => setDraftBookTypeId(e.target.value ? Number(e.target.value) : null)}
                className="w-full text-sm bg-muted rounded-lg px-2 py-1.5 border border-border focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="">No type</option>
                {bookTypes.map(bt => (
                  <option key={bt.id} value={bt.id}>{bt.label}</option>
                ))}
              </select>
            ) : (
              <p className="text-sm text-foreground">
                {bookTypes.find(bt => bt.id === book.book_type_id)?.label ?? <span className="text-muted-foreground/50 italic">None</span>}
              </p>
            )}
          </div>
        </div>
        {/* Content Type */}
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground mt-0.5 shrink-0"><FileText className="w-3.5 h-3.5" /></span>
          <div className="flex-1 min-w-0">
            <p className="text-xs text-muted-foreground/70 mb-0.5">Content</p>
            {editing ? (
              <select
                value={draft.content_type ?? 'volume'}
                onChange={e => setDraft(d => ({ ...d, content_type: e.target.value }))}
                className="w-full text-sm bg-muted rounded-lg px-2 py-1.5 border border-border focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="volume">Volume</option>
                <option value="chapter">Chapter</option>
              </select>
            ) : (
              <p className="text-sm text-foreground capitalize">{book.content_type ?? 'volume'}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  )

  const descriptionBlock = (
    <div className="mt-6">
      <div className="font-display text-base text-foreground mb-2">Description</div>
      {editing ? (
        <textarea
          value={field('description') ?? ''}
          onChange={e => setField('description', e.target.value)}
          rows={6}
          className="w-full text-sm text-foreground bg-muted rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring resize-none"
          placeholder="No description"
        />
      ) : book.description ? (
        <div>
          <p className={cn('text-sm text-muted-foreground leading-relaxed', !descExpanded && 'line-clamp-3')}>
            {book.description}
          </p>
          {book.description.length > 180 && (
            <button
              type="button"
              onClick={() => setDescExpanded(e => !e)}
              className="mt-1 text-xs font-medium text-primary hover:underline"
            >
              {descExpanded ? 'Show less' : 'Show more'}
            </button>
          )}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground/50 italic">No description</p>
      )}
    </div>
  )

  async function deleteHighlight(annotationId: number) {
    try {
      await api.delete(`/annotations/${annotationId}`)
      setAnnotations(prev => prev.filter(a => a.id !== annotationId))
      setConfirmingHighlight(null)
      toast.success('Highlight deleted')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to delete highlight')
    }
  }

  const highlightsBlock = annotations.length > 0 ? (
    <div className="mt-6">
      <button
        type="button"
        onClick={() => setHighlightsOpen(o => !o)}
        aria-expanded={highlightsOpen}
        className="flex items-center gap-1.5 font-display text-base text-foreground mb-3 hover:text-primary transition-colors"
      >
        Highlights &amp; Notes
        <span className="text-muted-foreground/50 font-normal text-sm">({annotations.length})</span>
        <ChevronDown className={cn('w-3.5 h-3.5 transition-transform', !highlightsOpen && '-rotate-90')} />
      </button>
      {highlightsOpen && (
        <ul className="space-y-3 max-h-[28rem] overflow-y-auto pr-1">
          {annotations.map(a => (
            <li key={a.id} className="group rounded-lg border border-border bg-muted/40 px-3 py-2.5">
              <div className="flex items-start justify-between gap-2">
                {a.chapter ? (
                  <p className="text-xs text-muted-foreground/70 mb-1.5 truncate">{a.chapter}</p>
                ) : <span />}
                {confirmingHighlight === a.id ? (
                  <span className="flex items-center gap-1.5 text-[11px] shrink-0">
                    <button onClick={() => deleteHighlight(a.id)} className="font-medium text-destructive hover:underline">
                      Delete
                    </button>
                    <button onClick={() => setConfirmingHighlight(null)} className="text-muted-foreground hover:text-foreground">
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    onClick={() => setConfirmingHighlight(a.id)}
                    title="Delete this highlight"
                    aria-label="Delete this highlight"
                    className="p-1 -mt-1 -mr-1 rounded text-muted-foreground/50 hover:text-destructive transition-all opacity-60 sm:opacity-0 sm:group-hover:opacity-100 focus:opacity-100 shrink-0"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
              {a.highlighted_text && (
                <p className="text-sm text-foreground leading-relaxed border-l-2 border-primary/40 pl-2.5">
                  {a.highlighted_text}
                </p>
              )}
              {a.note && (
                <p className="mt-2 flex items-start gap-1.5 text-sm text-muted-foreground">
                  <StickyNote className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                  <span className="leading-relaxed">{a.note}</span>
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  ) : null

  return (
    <div className="min-h-screen bg-background">
      {/* Nav */}
      <header className="border-b border-border bg-card/50 backdrop-blur-sm sticky top-0 z-10 safe-top">
        <div className="max-w-5xl mx-auto px-4 py-2 sm:py-0 sm:h-14 flex items-center justify-between gap-2 min-h-14">
          <div className="flex items-center gap-1 text-sm text-muted-foreground min-w-0 shrink">
            <Link to="/" title="Home" aria-label="Home" className="flex items-center hover:text-foreground transition-colors shrink-0">
              <Home className="w-3.5 h-3.5" />
            </Link>
            <ChevronRight className="w-3.5 h-3.5 opacity-30 shrink-0" />
            <Link to="/?tab=books" title="Library" className="flex items-center gap-1 hover:text-foreground transition-colors shrink-0">
              <Library className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Library</span>
            </Link>
            {adjacent && (adjacent.prev || adjacent.next) && book && (
              <>
                <ChevronRight className="w-3.5 h-3.5 opacity-30 shrink-0" />
                {adjacent.mode === 'series' && book.series ? (
                  <Link
                    to={`/?tab=series&series_detail=${encodeURIComponent(book.series)}`}
                    className="hover:text-foreground transition-colors truncate max-w-[120px] sm:max-w-[200px]"
                  >
                    {book.series}
                  </Link>
                ) : (
                  <span className="truncate max-w-[120px] sm:max-w-[200px]">{book.author}</span>
                )}
                <ChevronRight className="w-3.5 h-3.5 opacity-30 shrink-0" />
                <span className="text-foreground font-medium truncate max-w-[100px] sm:max-w-[180px]">
                  {book.series_index != null ? `Vol. ${book.series_index}` : book.title}
                </span>
                <div className="flex items-center gap-0 shrink-0 ml-1">
                  {adjacent.prev ? (
                    <Link
                      to={`/books/${adjacent.prev.id}`}
                      title={adjacent.prev.title}
                      className="p-1 rounded hover:text-foreground hover:bg-accent transition-colors"
                    >
                      <ChevronLeft className="w-3.5 h-3.5" />
                    </Link>
                  ) : (
                    <span className="p-1 opacity-25"><ChevronLeft className="w-3.5 h-3.5" /></span>
                  )}
                  {adjacent.next ? (
                    <Link
                      to={`/books/${adjacent.next.id}`}
                      title={adjacent.next.title}
                      className="p-1 rounded hover:text-foreground hover:bg-accent transition-colors"
                    >
                      <ChevronRight className="w-3.5 h-3.5" />
                    </Link>
                  ) : (
                    <span className="p-1 opacity-25"><ChevronRight className="w-3.5 h-3.5" /></span>
                  )}
                </div>
              </>
            )}
          </div>
          <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
            {canDelete && !editing && (
              confirmDelete ? (
                <div className="flex items-center gap-1.5 flex-wrap justify-end">
                  <span className="text-xs text-muted-foreground hidden sm:inline truncate max-w-[120px]">Delete "{book.title}"?</span>
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-transparent bg-destructive text-white hover:opacity-90 transition-all disabled:opacity-50"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    {deleting ? 'Deleting…' : 'Confirm'}
                  </button>
                  <button
                    onClick={() => setConfirmDelete(false)}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-transparent text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
                  >
                    <X className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">Cancel</span>
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmDelete(true)}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-muted-foreground hover:text-destructive hover:border-destructive/30 transition-all duration-200"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">Delete</span>
                </button>
              )
            )}
            {canEdit && !editing && (
              <>
                {/* Add to Library dropdown */}
                {libraries.some(l => l.can_edit) && (
                  <div className="relative" ref={libMenuRef}>
                    <button
                      onClick={() => setLibMenuOpen(o => !o)}
                      className={cn(
                        "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-all duration-200",
                        localLibIds.length > 0
                          ? "border-primary/40 bg-primary/10 text-primary"
                          : "border-border bg-card text-foreground hover:bg-muted hover:-translate-y-0.5"
                      )}
                    >
                      <Library className="w-3.5 h-3.5" />
                      <span className="hidden sm:inline">Libraries</span>
                      {localLibIds.length > 0 && (
                        <span className="ml-0.5 bg-primary text-primary-foreground rounded-full px-1.5 py-px text-[10px] font-bold leading-none">
                          {localLibIds.length}
                        </span>
                      )}
                    </button>
                    {libMenuOpen && (
                      <div className="absolute right-0 top-full mt-1 z-40 bg-card border border-border rounded-xl shadow-xl shadow-accent-soft py-1 min-w-48 max-w-[calc(100vw-2rem)]">
                        <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground border-b border-border mb-1">
                          Add to library
                        </p>
                        {libraries.filter(l => l.can_edit).map(lib => {
                          const inLib = localLibIds.includes(lib.id)
                          const pending = libPending.has(lib.id)
                          return (
                            <div
                              key={lib.id}
                              className="flex items-center gap-2 w-full px-3 py-2 text-sm hover:bg-muted transition-colors"
                            >
                              {pending
                                ? <Loader2 className="w-3.5 h-3.5 text-muted-foreground shrink-0 animate-spin" />
                                : <Library className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                              }
                              <Link
                                to={`/?tab=books&library_id=${lib.id}`}
                                className="flex-1 truncate hover:text-primary transition-colors"
                                onClick={() => setLibMenuOpen(false)}
                              >
                                {lib.name}
                              </Link>
                              <button
                                onClick={() => toggleLibrary(lib.id)}
                                disabled={pending}
                                className="shrink-0 p-0.5 rounded hover:bg-accent transition-colors disabled:opacity-60"
                                title={inLib ? 'Remove from library' : 'Add to library'}
                              >
                                {inLib
                                  ? <Check className="w-3.5 h-3.5 text-primary" />
                                  : <Check className="w-3.5 h-3.5 text-muted-foreground/30" />
                                }
                              </button>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}
                {book.content_type !== 'chapter' && (
                  <button
                    onClick={() => setFetchModalOpen(true)}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted hover:-translate-y-0.5 transition-all duration-200"
                  >
                    <Sparkles className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">Fetch Metadata</span>
                  </button>
                )}
                <button
                  onClick={startEdit}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted hover:-translate-y-0.5 transition-all duration-200"
                >
                  <Edit2 className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">Edit</span>
                </button>
              </>
            )}
            {editing && (
              <>
                <button
                  onClick={saveEdit}
                  disabled={saving}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all disabled:opacity-50"
                >
                  <Save className="w-3.5 h-3.5" />
                  {saving ? 'Saving…' : 'Save'}
                </button>
                <button
                  onClick={cancelEdit}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
                >
                  <X className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">Cancel</span>
                </button>
              </>
            )}
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-6 sm:py-8">
        <div className="flex gap-6 sm:gap-8 flex-col sm:flex-row">
          {/* Left rail: cover + actions + genres */}
          <div className="shrink-0 w-full sm:w-48">
            {leftRailActions}
            {(editing || book.tags.length > 0) && (
              <div className="mt-5 pt-4 border-t border-border">
                {genresBlock}
              </div>
            )}
          </div>
          {/* Right column: title → status → stats → description → details grid → highlights */}
          <div className="flex-1 min-w-0">
            {titleBlock}
            {statusProgressBlock}
            {ratingBlock}
            {statsFull}
            {descriptionBlock}
            {metadataGridFull}
            {highlightsBlock}
          </div>
        </div>
      </main>

      {book && (
        <MetadataFetchModal
          book={book}
          open={fetchModalOpen}
          onClose={() => setFetchModalOpen(false)}
          onApplied={updated => { setBook(updated); setDraft(updated) }}
        />
      )}
      {book && (
        <CoverPickerModal
          book={book}
          open={coverPickerOpen}
          onClose={() => setCoverPickerOpen(false)}
          onApplied={updated => { setBook(updated); setDraft(updated) }}
        />
      )}
    </div>
  )
}

// ── Reading stats sub-components ─────────────────────────────────────────────

interface StatsLayoutProps {
  own: BookReadingStats
  aggregate: { total_seconds: number; total_sessions: number; distinct_readers: number } | null
  bookId: number
  onChange: () => void
}

/** Activity bars (minutes per reading day) + an optional progress lane below.
 *  The progress lane is the per-book "journey" — % complete over calendar time —
 *  gated by `showProgress` so it only appears when there's no Reading-intensity
 *  chart (web/manual books); a book never stacks two look-alike area charts. */
function ActivityChart({ timeline }: {
  timeline: { date: string; seconds: number; pages: number; progress_pct?: number | null }[]
}) {
  if (timeline.length < 2) return null
  const max = Math.max(...timeline.map(d => d.seconds), 1)
  const n = timeline.length
  const first = timeline[0]
  const last = timeline[timeline.length - 1]
  const fmtDay = (iso: string) =>
    new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  // Progress journey: % complete on each reading day, drawn as a slim lane below.
  const hasJourney = timeline.some(d => d.progress_pct != null)
  const lanePts = (timeline
    .map((d, i) => (d.progress_pct != null
      ? `${((i / (n - 1)) * 100).toFixed(2)},${(100 - d.progress_pct).toFixed(2)}`
      : null))
    .filter(Boolean) as string[])
  const lastProgress = [...timeline].reverse().find(d => d.progress_pct != null)?.progress_pct ?? null
  return (
    <div className="flex flex-col">
      <p className="text-xs text-muted-foreground/70 mb-1.5 shrink-0">Activity</p>
      {/* Fixed-height strip: flex-1 fill only works inside a sized flex parent,
          and the hero wraps this in a plain div — flex-1 collapsed to 0px there
          (bars rendered into no vertical space at all) */}
      <div className="relative h-20">
        {/* faint baseline rule */}
        <div className="absolute bottom-0 left-0 right-0 h-px bg-border/40" />
        <div className="absolute inset-0 flex items-end gap-1.5">
          {timeline.map(d => {
            const pct = d.seconds > 0 ? Math.max(d.seconds / max, 0.06) : 0
            const mins = Math.round(d.seconds / 60)
            const tip = `${fmtDay(d.date)}: ${mins}m${d.pages > 0 ? `, ${d.pages} pages` : ''}${d.progress_pct != null ? ` · ${d.progress_pct}% in` : ''}`
            return (
              <div
                key={d.date}
                className="flex-1 min-w-px bg-primary/60 hover:bg-primary transition-colors rounded-t-sm"
                style={{ height: pct > 0 ? `${Math.round(pct * 100)}%` : '0%' }}
                title={tip}
              />
            )
          })}
        </div>
      </div>
      <div className="flex justify-between text-xs text-muted-foreground/50 mt-1 shrink-0">
        <span>{first?.date ? fmtDay(first.date) : ''}</span>
        <span>{last?.date ? fmtDay(last.date) : ''}</span>
      </div>
      {hasJourney && lanePts.length > 1 && (
        <div className="mt-3">
          <div className="flex items-center justify-between mb-1">
            <span className="flex items-center gap-1">
              <p className="text-xs text-muted-foreground/70">Progress</p>
              <InfoHint text="How far through the book you'd read by each date." />
            </span>
            {lastProgress != null && (
              <p className="text-xs tabular-nums text-muted-foreground/60">{Math.round(lastProgress)}%</p>
            )}
          </div>
          <div className="relative h-7">
            <div className="absolute bottom-0 left-0 right-0 h-px bg-border/40" />
            <svg className="absolute inset-0 w-full h-full text-primary" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
              <polygon points={`0,100 ${lanePts.join(' ')} 100,100`} fill="currentColor" opacity={0.14} />
              <polyline points={lanePts.join(' ')} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            </svg>
          </div>
        </div>
      )}
    </div>
  )
}

// Friendly label for a ReadingSession.device value.
function sourceLabel(device: string): string {
  if (!device || device === 'web-reader') return 'Web reader'
  if (device === 'manual') return 'Manual'
  if (device === 'koreader') return 'KOReader'
  return device
}

const SOURCE_SHADES = ['bg-primary', 'bg-primary/65', 'bg-primary/40', 'bg-primary/25']

// Where the reading time came from: a thin stacked bar + legend.
function SourceSplit({ sources }: { sources: { device: string; seconds: number; sessions: number }[] }) {
  const total = sources.reduce((sum, s) => sum + s.seconds, 0)
  // A single source is a full-width solid bar — pointless; only split when it splits.
  if (total <= 0 || sources.length < 2) return null
  return (
    <div className="mt-4">
      <p className="text-xs text-muted-foreground/70 mb-1.5">Where you read</p>
      <div className="flex h-2 rounded-full overflow-hidden bg-muted">
        {sources.map((s, i) => (
          <div
            key={s.device}
            className={cn(SOURCE_SHADES[Math.min(i, SOURCE_SHADES.length - 1)], 'transition-all')}
            style={{ width: `${(s.seconds / total) * 100}%` }}
            title={`${sourceLabel(s.device)}: ${formatDuration(s.seconds)}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
        {sources.map((s, i) => (
          <div key={s.device} className="flex items-center gap-1.5">
            <span className={cn('inline-block w-2 h-2 rounded-full', SOURCE_SHADES[Math.min(i, SOURCE_SHADES.length - 1)])} />
            <span className="text-xs text-muted-foreground">{sourceLabel(s.device)}</span>
            <span className="text-xs font-medium tabular-nums text-foreground">{formatDuration(s.seconds)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Reading intensity: per-page dwell across the book, from imported KOReader page-stats ─────

function IntensityBlock({ data }: { data: BookIntensity }) {
  const max = Math.max(...data.curve, 1)
  const finished = data.pct_read >= 99
  return (
    <div className="rounded-xl border border-border bg-card px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="flex items-center gap-1.5">
          <p className="font-display text-sm text-foreground">Reading intensity</p>
          <InfoHint text="Where in the book your time went — taller means more time spent on those pages." />
        </span>
        <p className="text-xs text-muted-foreground">
          <span className="font-medium tabular-nums text-foreground">{data.pages_read.toLocaleString()}</span>
          {' '}of {data.total_pages.toLocaleString()} pages
          {!finished && <span className="tabular-nums"> · {data.pct_read}%</span>}
          {' · '}{formatDuration(data.total_seconds)} on device
        </p>
      </div>
      {/* Dwell across the book, 0% → 100%. Taller bar = more time spent there. */}
      <div className="mt-3 relative h-16">
        <div className="absolute bottom-0 left-0 right-0 h-px bg-border/40" />
        <div className="absolute inset-0 flex items-end gap-px">
          {data.curve.map((secs, i) => {
            const pct = secs > 0 ? Math.max(secs / max, 0.04) : 0
            const at = Math.round((i / data.curve.length) * 100)
            const tip = secs > 0 ? `~${at}% in · ${formatDuration(secs)}` : `~${at}% in · not read`
            return (
              <div
                key={i}
                className="flex-1 min-w-px bg-primary/60 hover:bg-primary transition-colors rounded-t-[1px]"
                style={{ height: pct > 0 ? `${Math.round(pct * 100)}%` : '0%' }}
                title={tip}
              />
            )
          })}
        </div>
      </div>
      <div className="flex justify-between text-xs text-muted-foreground/50 mt-1">
        <span>start</span>
        {data.reread_bins > 0 && (
          <span className="text-muted-foreground/70">{data.reread_bins} stretch{data.reread_bins !== 1 ? 'es' : ''} re-read</span>
        )}
        <span>end</span>
      </div>
    </div>
  )
}

// ── Hero layout: large time-read headline + activity chart, bottom-pinned date/span stats ─────

// Manual reading-log entry point: log a session by hand (paper / un-synced
// device) and export the log. Used both in the stats hero and on books with no
// reading history yet, so manual trackers always have a way in.
function ManualLogControls({ bookId, onChange, exportRows }: {
  bookId: number
  onChange: () => void
  exportRows?: { date: string; seconds: number; pages: number; progress_pct: number | null }[]
}) {
  const [logging, setLogging] = useState(false)
  const [minutes, setMinutes] = useState('')
  const [pct, setPct] = useState('')
  const [saving, setSaving] = useState(false)

  const submitLog = async (e: React.FormEvent) => {
    e.preventDefault()
    const mins = parseFloat(minutes)
    if (!mins || mins <= 0) return
    setSaving(true)
    try {
      const body: { duration_minutes: number; end_progress?: number } = { duration_minutes: mins }
      if (pct.trim() !== '') {
        const p = parseFloat(pct)
        if (!Number.isNaN(p)) body.end_progress = Math.min(Math.max(p / 100, 0), 1)
      }
      await api.post(`/books/${bookId}/sessions`, body)
      setMinutes(''); setPct(''); setLogging(false)
      onChange()
    } finally {
      setSaving(false)
    }
  }

  const exportSessions = (fmt: 'csv' | 'json') => {
    const rows = exportRows ?? []
    let content: string
    let mime: string
    if (fmt === 'csv') {
      content = 'date,minutes,pages,progress_pct\n' +
        rows.map(r => `${r.date},${Math.round(r.seconds / 60)},${r.pages},${r.progress_pct ?? ''}`).join('\n')
      mime = 'text/csv'
    } else {
      content = JSON.stringify(rows, null, 2)
      mime = 'application/json'
    }
    const blob = new Blob([content], { type: mime })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `reading-log-${bookId}.${fmt}`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const canExport = exportRows != null && exportRows.length > 0
  const chip = 'rounded-md border border-border px-2 py-0.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:border-primary/50 transition-colors'

  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <button
          type="button"
          onClick={() => setLogging(o => !o)}
          className="flex items-center gap-1.5 text-sm font-medium text-foreground hover:text-primary transition-colors"
        >
          <Plus className="w-4 h-4" /> Log session
        </button>
        {canExport && (
          <div className="flex items-center gap-1.5">
            <Download className="w-3.5 h-3.5 text-muted-foreground/50" />
            <button type="button" onClick={() => exportSessions('csv')} className={chip}>CSV</button>
            <button type="button" onClick={() => exportSessions('json')} className={chip}>JSON</button>
          </div>
        )}
      </div>
      {logging && (
        <form onSubmit={submitLog} className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground/70">Minutes</span>
            <input
              type="number" min="1" step="1" required autoFocus
              value={minutes} onChange={e => setMinutes(e.target.value)}
              className="w-24 rounded-md border border-border bg-background px-2 py-1.5 text-sm tabular-nums focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground/70">Progress % <span className="text-muted-foreground/40">(optional)</span></span>
            <input
              type="number" min="0" max="100" step="1"
              value={pct} onChange={e => setPct(e.target.value)}
              className="w-28 rounded-md border border-border bg-background px-2 py-1.5 text-sm tabular-nums focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </label>
          <button
            type="submit" disabled={saving || !minutes}
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Add'}
          </button>
          <button
            type="button" onClick={() => { setLogging(false); setMinutes(''); setPct('') }}
            className="rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            Cancel
          </button>
        </form>
      )}
    </div>
  )
}

// A small "i" that explains a chart on hover or tap. Tap-toggle so it works on
// touch (native title tooltips don't). Reserved for the non-obvious charts.
function InfoHint({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  return (
    <span className="relative inline-flex leading-none">
      <button
        type="button"
        aria-label="What is this chart?"
        onClick={() => setOpen(o => !o)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onBlur={() => setOpen(false)}
        className="text-muted-foreground/40 hover:text-muted-foreground transition-colors"
      >
        <Info className="w-3 h-3" />
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute left-0 top-5 z-20 w-52 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs leading-snug text-muted-foreground shadow-lg"
        >
          {text}
        </span>
      )}
    </span>
  )
}

function MomentumChip({ momentum }: { momentum: NonNullable<BookReadingStats['momentum']> }) {
  if (momentum.recent_seconds === 0 && momentum.prior_seconds === 0) return null
  const Icon = momentum.direction === 'up' ? TrendingUp : momentum.direction === 'down' ? TrendingDown : Minus
  const tone =
    momentum.direction === 'up' ? 'text-emerald-600 dark:text-emerald-400'
      : momentum.direction === 'down' ? 'text-muted-foreground'
        : 'text-muted-foreground'
  const label = momentum.delta_pct != null
    ? `${momentum.delta_pct > 0 ? '+' : ''}${momentum.delta_pct}% vs last week`
    : 'new this week'
  return (
    <span
      className={cn('flex items-center gap-1 text-xs font-medium', tone)}
      title={`${formatDuration(momentum.recent_seconds)} in the last 7 days vs ${formatDuration(momentum.prior_seconds)} the week before`}
    >
      <Icon className="w-3.5 h-3.5" />
      {label}
    </span>
  )
}

function StatsLayoutHero({ own, aggregate, bookId, onChange }: StatsLayoutProps) {
  const supporting: { label: string; value: string }[] = [
    { label: 'sessions', value: String(own.sessions) },
    { label: 'pages', value: own.pages_turned > 0 ? String(own.pages_turned) : '—' },
    { label: 'avg session', value: formatDuration(own.avg_session_seconds) },
    ...(own.pace_pages_per_min != null
      ? [{ label: 'pace', value: `${own.pace_pages_per_min} pg/min` }] : []),
  ]

  const bottomStats: { label: string; value: string }[] = []
  if (own.first_read) {
    bottomStats.push({ label: 'First read', value: formatDate(own.first_read.slice(0, 10)) })
  }
  if (own.last_read) {
    bottomStats.push({ label: 'Last read', value: formatDate(own.last_read.slice(0, 10)) })
  }
  if (own.finished_at) {
    bottomStats.push({ label: 'Finished', value: formatDate(own.finished_at.slice(0, 10)) })
  }
  // Reading days from distinct session days
  if (own.session_timeline.length > 0) {
    bottomStats.push({ label: 'Reading days', value: String(own.session_timeline.length) })
  }
  if (own.status === 'reading' && own.estimated_finish_seconds != null) {
    bottomStats.push({ label: 'Est. remaining', value: formatDuration(own.estimated_finish_seconds) })
  }

  return (
    <div className="rounded-xl border border-border bg-card px-5 py-4">
      {/* Headline + supporting metrics, one baseline-aligned row */}
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <div className="flex items-baseline gap-2">
          <p className="text-3xl font-semibold tabular-nums text-foreground leading-none">
            {formatDuration(own.total_seconds)}
          </p>
          <p className="text-sm text-muted-foreground">read</p>
        </div>
        {own.momentum && <MomentumChip momentum={own.momentum} />}
        <div className="flex items-baseline gap-x-5 gap-y-1 flex-wrap">
          {supporting.map(s => (
            <div key={s.label} className="flex items-baseline gap-1.5">
              <span className="text-sm font-medium tabular-nums text-foreground">{s.value}</span>
              <span className="text-xs text-muted-foreground/70">{s.label}</span>
            </div>
          ))}
        </div>
      </div>
      {own.session_timeline.length > 1 && (
        <div className="mt-4">
          <ActivityChart timeline={own.session_timeline} />
        </div>
      )}
      <SourceSplit sources={own.by_source} />
      {/* Dates row — hairline-divided columns, no boxes */}
      {bottomStats.length > 0 && (
        <div className="mt-4 pt-3 border-t border-border/60 grid grid-cols-2 sm:flex">
          {bottomStats.map((s, i) => (
            <div key={s.label} className={cn('sm:flex-1 sm:px-4', i === 0 && 'sm:pl-0', i > 0 && 'sm:border-l sm:border-border/60')}>
              <p className="text-xs text-muted-foreground/70">{s.label}</p>
              <p className="text-sm font-medium tabular-nums text-foreground">{s.value}</p>
            </div>
          ))}
        </div>
      )}
      {aggregate && (
        <p className="text-xs text-muted-foreground/60 mt-3">
          All readers: {formatDuration(aggregate.total_seconds)} · {aggregate.total_sessions} sessions · {aggregate.distinct_readers} reader{aggregate.distinct_readers !== 1 ? 's' : ''}
        </p>
      )}
      {/* Log a session by hand · export the log */}
      <div className="mt-4 pt-3 border-t border-border/60">
        <ManualLogControls bookId={bookId} onChange={onChange} exportRows={own.session_timeline} />
      </div>
    </div>
  )
}


function MetaField({
  icon, label, value, editing, onChange, type = 'text', placeholder = ''
}: {
  icon: React.ReactNode
  label: string
  value: string
  editing: boolean
  onChange: (v: string) => void
  type?: string
  placeholder?: string
}) {
  if (!editing && !value) return null
  return (
    <div className="flex items-start gap-2">
      <span className="text-muted-foreground mt-0.5 shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <p className="text-xs text-muted-foreground/70 mb-0.5">{label}</p>
        {editing ? (
          <input
            type={type}
            value={value}
            onChange={e => onChange(e.target.value)}
            placeholder={placeholder}
            className="w-full text-sm bg-muted rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
          />
        ) : (
          <p className="text-sm text-foreground truncate">{value}</p>
        )}
      </div>
    </div>
  )
}
