import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  Camera, Download, Edit2, Save, X,
  Calendar, Globe, Hash, Building2, FileText, Trash2, Loader2,
  Sparkles, Library, Check, BookMarked, ChevronLeft, ChevronRight, Home,
  Tag as TagIcon, StickyNote, ChevronDown
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
  progress: number | null
  status: string
  session_timeline: { date: string; seconds: number; pages: number }[]
  estimated_finish_seconds: number | null
}

interface ReadingStatsResponse {
  own: BookReadingStats
  aggregate: {
    total_seconds: number
    total_sessions: number
    distinct_readers: number
  } | null
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
  const [progressAnimated, setProgressAnimated] = useState(false)
  const [statusSaving, setStatusSaving] = useState(false)
  const [statusPopKey, setStatusPopKey] = useState(0)
  const [kosyncDevice, setKosyncDevice] = useState<string | null>(null)
  const [annotations, setAnnotations] = useState<Annotation[]>([])
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
    api.get<BookStatus>(`/books/${id}/status`).then(s => { setBookStatus(s.status); setProgressPct(s.progress_pct); setProgressAnimated(false) }).catch(() => {})
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

  async function handleStatusChange(s: ReadingStatus) {
    if (!id || statusSaving) return
    setStatusSaving(true)
    try {
      const updated = await api.put<BookStatus>(`/books/${id}/status`, { status: s })
      setBookStatus(updated.status)
      setProgressPct(updated.progress_pct)
      setProgressAnimated(false)
      setStatusPopKey(k => k + 1)
    } catch {
      toast.error('Failed to update reading status')
    } finally {
      setStatusSaving(false)
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
                ? 'bg-yellow-400/10 border-yellow-400 text-yellow-600 dark:text-yellow-400 animate-[pop_0.2s_ease-out]'
                : s === 'read'
                  ? 'bg-green-500/10 border-green-500 text-green-600 dark:text-green-400 animate-[pop_0.2s_ease-out]'
                  : 'bg-muted border-border text-foreground animate-[pop_0.2s_ease-out]'
              : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground'
          )}
        >
          {s}
        </button>
      ))}
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

  // Full collapsible stats hero block
  const statsFull = readingStats && readingStats.own.sessions > 0 ? (
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
        <StatsLayoutHero own={readingStats.own} aggregate={readingStats.aggregate} />
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
            <li key={a.id} className="rounded-lg border border-border bg-muted/40 px-3 py-2.5">
              {a.chapter && (
                <p className="text-xs text-muted-foreground/70 mb-1.5 truncate">{a.chapter}</p>
              )}
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
            <Link to="/?tab=books" className="flex items-center gap-1 hover:text-foreground transition-colors shrink-0">
              <Home className="w-3.5 h-3.5" />
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
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-destructive text-white hover:opacity-90 transition-all disabled:opacity-50"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    {deleting ? 'Deleting…' : 'Confirm'}
                  </button>
                  <button
                    onClick={() => setConfirmDelete(false)}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
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
}

/** Shared activity chart used by all layout variants.
 *  Slim bars, flat bottom on a baseline, rounded top only. */
function ActivityChart({ timeline }: { timeline: { date: string; seconds: number; pages: number }[] }) {
  if (timeline.length < 2) return null
  const max = Math.max(...timeline.map(d => d.seconds), 1)
  const first = timeline[0]
  const last = timeline[timeline.length - 1]
  const fmtDay = (iso: string) =>
    new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  return (
    <div className="flex flex-col flex-1 min-h-0">
      <p className="text-xs text-muted-foreground/70 mb-1.5 shrink-0">Activity</p>
      {/* Bars + baseline — grows to fill, but capped so a few sessions don't balloon */}
      <div className="relative flex-1 min-h-0 max-h-32">
        {/* faint baseline rule */}
        <div className="absolute bottom-0 left-0 right-0 h-px bg-border/40" />
        <div className="absolute inset-0 flex items-end gap-1.5">
          {timeline.map(d => {
            const pct = d.seconds > 0 ? Math.max(d.seconds / max, 0.06) : 0
            const mins = Math.round(d.seconds / 60)
            const tip = `${fmtDay(d.date)}: ${mins}m${d.pages > 0 ? `, ${d.pages} pages` : ''}`
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
    </div>
  )
}

// ── Hero layout: large time-read headline + activity chart, bottom-pinned date/span stats ─────

function StatsLayoutHero({ own, aggregate }: StatsLayoutProps) {
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
