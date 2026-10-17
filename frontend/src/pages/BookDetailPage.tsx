import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  Camera, Download, Edit2, X, Trash2, Loader2, Sparkles, Library as LibraryIcon, Check,
  ChevronLeft, ChevronRight, StickyNote, ChevronDown, Play, Link2, RefreshCw,
  Plus, TrendingUp, TrendingDown, Minus, History as HistoryIcon, Share2,
} from 'lucide-react'
import { useAuth, isMember } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { Trans, useLingui } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { MetadataFetchModal } from '@/components/MetadataFetchModal'
import { CoverPickerModal } from '@/components/CoverPickerModal'
import { PositionHistoryModal } from '@/components/PositionHistoryModal'
import { ShareModal } from '@/components/ShareShelfModal'
import { SendButton } from '@/components/SendButton'
import { DevicePills } from '@/components/DevicePills'
import { BookAnimation } from '@/components/BookAnimation'
import { StarRating } from '@/components/StarRating'
import { CoverImage } from '@/components/CoverImage'
import { AutocompleteInput } from '@/components/AutocompleteInput'
import { AppShell } from '@/components/AppShell'
import { QuickLook } from '@/components/QuickLook'
import { PageBody, SectionTitle, Stat, StatStrip, Field, Pill } from '@/components/page'
import { coverSrc } from '@/lib/covers'
import { SessionLog, SessionLogHint } from '@/components/stats/SessionLog'
import { InfoHint } from '@/components/InfoHint'
import { api } from '@/lib/api'
import type { BookDetail, BookFile, Library as LibraryType, BookStatus, ReadingStatus } from '@/lib/books'
import { formatBytes } from '@/lib/books'
import { useBookTypes } from '@/lib/bookTypes'
import { cn, formatDuration, formatDate } from '@/lib/utils'
import { describePace, formatEstimateDays, formatEstimateHours, type BookEstimate } from '@/lib/backlog'

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

export interface BookReadingStats {
  total_seconds: number
  // Reconciled sitting count: recorded web/manual sessions plus gap-clustered
  // imported KOReader history — exactly the rows the session log lists.
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
  estimated_finish_date: string | null
}

export interface BookIntensity {
  bins: number
  curve: number[]
  total_seconds: number
  total_pages: number
  pages_read: number
  pct_read: number
  reread_bins: number
}

interface ChapterSitting {
  start_ts: number
  end_ts: number
}

export interface ChapterTime {
  idx: number
  title: string
  start_fraction: number
  end_fraction: number
  seconds: number
  sittings: ChapterSitting[]
}

// When a chapter was actually read, honest about sittings:
//   one sitting  → "Jun 2, 17:40 – 18:54"
//   two or three → "Jun 2 17:40–18:10 · Jun 3 21:00–21:34"
//   more         → "5 sittings · Jun 2 – Jun 8"
function chapterReadSpan(c: ChapterTime): string {
  const s = c.sittings
  if (!s || s.length === 0) return ''
  const day = (ts: number) =>
    new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  const clock = (ts: number) =>
    new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  if (s.length === 1) {
    const d = day(s[0].start_ts), from = clock(s[0].start_ts), to = clock(s[0].end_ts)
    return t`read ${d}, ${from} – ${to}`
  }
  if (s.length <= 3) {
    const span = s.map(x => `${day(x.start_ts)} ${clock(x.start_ts)}–${clock(x.end_ts)}`).join(' · ')
    return t`read ${span}`
  }
  const count = s.length, from = day(s[0].start_ts), to = day(s[s.length - 1].end_ts)
  return t`${count} sittings · ${from} – ${to}`
}

// Short variant for the line-chart tooltip (dates only, no clock times).
function chapterReadSpanShort(c: ChapterTime): string {
  const s = c.sittings
  if (!s || s.length === 0) return ''
  const day = (ts: number) =>
    new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  const a = day(s[0].start_ts)
  const b = day(s[s.length - 1].end_ts)
  return a === b ? t`read ${a}` : t`read ${a} – ${b}`
}

interface ReadingStatsResponse {
  own: BookReadingStats
  aggregate: {
    total_seconds: number
    total_sessions: number
    distinct_readers: number
  } | null
  intensity: BookIntensity | null
  chapters: ChapterTime[] | null
}

interface KosyncUnlinkedDoc {
  document: string
  percentage: number
  device: string | null
  timestamp: number
}

interface KosyncStatus {
  linked: boolean
  device?: string
  percentage?: number | null
  timestamp?: number | null
  unlinked_documents?: KosyncUnlinkedDoc[]
}

interface SeriesVolume {
  id: number
  title: string
  series_index: number | null
  cover_path: string | null
  updated_at: string | null
  reading_status: string | null
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
  const { t, i18n } = useLingui()
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
  const [rating, setRating] = useState<number | null>(null)
  const [review, setReview] = useState('')       // saved review text
  const [reviewDraft, setReviewDraft] = useState('')  // in-progress edit
  const [editingReview, setEditingReview] = useState(false)
  const [kosyncDevice, setKosyncDevice] = useState<string | null>(null)
  // Where the linked KOSync position stands, for the "Synced from" pill.
  const [kosyncLinked, setKosyncLinked] = useState<{ percentage: number | null; timestamp: number | null } | null>(null)
  // Manual KOSync linking (#156): sync documents pushed by a KOSync client
  // (stock KOReader / Readest) that aren't attached to any Tome book yet.
  const [kosyncUnlinked, setKosyncUnlinked] = useState<KosyncUnlinkedDoc[]>([])
  const [kosyncPickerOpen, setKosyncPickerOpen] = useState(false)
  const [kosyncLinkDoc, setKosyncLinkDoc] = useState('')
  const [kosyncLinking, setKosyncLinking] = useState(false)
  const [annotations, setAnnotations] = useState<Annotation[]>([])
  const [confirmingHighlight, setConfirmingHighlight] = useState<number | null>(null)
  const [highlightsOpen, setHighlightsOpen] = useState(true)
  const [readingStats, setReadingStats] = useState<ReadingStatsResponse | null>(null)
  const [estimate, setEstimate] = useState<BookEstimate | null>(null)
  // Per-book session list (#150) — collapsed by default; the always-available
  // place to trim/delete a runaway session (the Stats tiles are removable).
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [showPositionHistory, setShowPositionHistory] = useState(false)
  const [showShare, setShowShare] = useState(false)
  const [editingNoteId, setEditingNoteId] = useState<number | null>(null)
  const [editingNoteDraft, setEditingNoteDraft] = useState('')
  const [descExpanded, setDescExpanded] = useState(false)
  // "Show more" only when the 4-line clamp actually hides text.
  const descRef = useRef<HTMLParagraphElement>(null)
  const [descClamped, setDescClamped] = useState(false)
  const [downloadMenuOpen, setDownloadMenuOpen] = useState(false)
  const downloadMenuRef = useRef<HTMLDivElement>(null)
  const [seriesVolumes, setSeriesVolumes] = useState<SeriesVolume[]>([])
  const [quickLookId, setQuickLookId] = useState<number | null>(null)
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
    if (!downloadMenuOpen) return
    function onMouseDown(e: MouseEvent) {
      if (downloadMenuRef.current && !downloadMenuRef.current.contains(e.target as Node)) {
        setDownloadMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [downloadMenuOpen])

  useEffect(() => {
    const el = descRef.current
    if (!el) { setDescClamped(false); return }
    const measure = () => { if (!descExpanded) setDescClamped(el.scrollHeight > el.clientHeight + 1) }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [book?.description, editing, descExpanded, loading])

  useEffect(() => {
    const name = book?.series
    if (!name) { setSeriesVolumes([]); return }
    api.get<{ books: SeriesVolume[] }>(`/books/series-detail?name=${encodeURIComponent(name)}`)
      .then(d => setSeriesVolumes(d.books ?? []))
      .catch(() => setSeriesVolumes([]))
  }, [book?.series])

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.key === 'Escape') {
        // The quick look closes itself on Esc; do not also leave the page.
        if (quickLookId != null) return
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
  }, [navigate, book, editing, canEdit, quickLookId])

  useEffect(() => {
    if (!id) return
    setAdjacent(null)
    setSessionsOpen(false)
    setDescExpanded(false)
    setConfirmDelete(false)
    setKosyncLinked(null)
    setKosyncDevice(null)
    // The shell scrolls inside <main>; a new book starts at its top.
    document.querySelector('main')?.scrollTo(0, 0)
    api.get<BookDetail>(`/books/${id}`)
      .then(b => { setBook(b); setDraft(b); setLocalLibIds(b.library_ids ?? []) })
      .catch(() => setError(t`Book not found`))
      .finally(() => setLoading(false))
    api.get<LibraryType[]>('/libraries').then(setLibraries).catch(() => toast.error(t`Failed to load libraries`))
    api.get<BookStatus>(`/books/${id}/status`).then(s => { setBookStatus(s.status); setProgressPct(s.progress_pct); setCfi(s.cfi ?? null); setProgressAnimated(false); setRating(s.rating ?? null); setReview(s.review ?? ''); setEditingReview(false) }).catch(() => {})
    api.get<typeof adjacent>(`/books/${id}/adjacent`).then(setAdjacent).catch(() => {})
    api.get<KosyncStatus>(`/books/${id}/kosync-progress`)
      .then(r => {
        if (r.linked && r.device) setKosyncDevice(r.device)
        setKosyncLinked(r.linked ? { percentage: r.percentage ?? null, timestamp: r.timestamp ?? null } : null)
        setKosyncUnlinked(!r.linked && r.unlinked_documents ? r.unlinked_documents : [])
      })
      .catch(() => {})  // KOSync is optional — silent fail is fine
    api.get<Annotation[]>(`/books/${id}/annotations`).then(setAnnotations).catch(() => {})
    api.get<ReadingStatsResponse>(`/books/${id}/reading-stats?tz_offset=${new Date().getTimezoneOffset()}`).then(setReadingStats).catch(() => {})
    api.get<BookEstimate>(`/books/${id}/estimate?tz_offset=${new Date().getTimezoneOffset()}`).then(setEstimate).catch(() => {})
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
  }

  async function restoreStatus(prev: { status: ReadingStatus; progress_pct: number | null; cfi: string | null }) {
    if (!id) return
    try {
      // Send progress + cfi back too, so undoing an "unread" (which clears them) restores your position.
      const restored = await api.put<BookStatus>(`/books/${id}/status`, prev)
      applyStatus(restored)
    } catch {
      toast.error(t`Failed to undo`)
    }
  }

  const STATUS_LABEL: Record<ReadingStatus, string> = {
    unread: t`Marked unread — reading progress cleared`,
    reading: t`Marked as reading`,
    read: t`Marked as read`,
    shelved: t`Shelved — kept your progress`,
    want_to_read: t`Added to Want to Read`,
  }

  async function handleStatusChange(s: ReadingStatus) {
    if (!id || statusSaving || s === bookStatus) return
    const prev = { status: bookStatus, progress_pct: progressPct, cfi }
    setStatusSaving(true)
    try {
      const updated = await api.put<BookStatus>(`/books/${id}/status`, { status: s })
      applyStatus(updated)
      toast.info(STATUS_LABEL[s], { action: { label: t`Undo`, onClick: () => restoreStatus(prev) } })
    } catch {
      toast.error(t`Failed to update reading status`)
    } finally {
      setStatusSaving(false)
    }
  }

  async function linkKosyncDocument() {
    if (!id || !kosyncLinkDoc || kosyncLinking) return
    setKosyncLinking(true)
    try {
      await api.post(`/books/${id}/link-kosync`, { document: kosyncLinkDoc })
      // Linking applies the synced progress to this book — refresh both.
      const [ks, s] = await Promise.all([
        api.get<KosyncStatus>(`/books/${id}/kosync-progress`),
        api.get<BookStatus>(`/books/${id}/status`),
      ])
      if (ks.linked && ks.device) setKosyncDevice(ks.device)
      setKosyncLinked(ks.linked ? { percentage: ks.percentage ?? null, timestamp: ks.timestamp ?? null } : null)
      setKosyncUnlinked(!ks.linked && ks.unlinked_documents ? ks.unlinked_documents : [])
      applyStatus(s)
      setKosyncPickerOpen(false)
      toast.info(t`KOReader sync linked`)
    } catch {
      toast.error(t`Failed to link sync document`)
    } finally {
      setKosyncLinking(false)
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
      toast.error(t`Failed to save rating`)
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
      toast.error(t`Failed to save review`)
    }
  }

  async function handleDelete() {
    if (!book) return
    setDeleting(true)
    try {
      await api.delete(`/books/${book.id}`)
      toast.success(t`Book deleted`)
      navigate('/')
    } catch (err) {
      const msg = err instanceof Error ? err.message : t`Delete failed`
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
    api.get<Facets>('/books/facets').then(setFacets).catch(() => toast.error(t`Failed to load facets`))
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
      toast.success(t`Metadata saved`)
    } catch (err) {
      setError(err instanceof Error ? err.message : t`Save failed`)
      toast.error(err instanceof Error ? err.message : t`Save failed`)
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
    <AppShell>
      <div className="flex h-full min-h-[60vh] items-center justify-center">
        <BookAnimation variant="refresh" className="block w-10 h-10 text-primary" />
      </div>
    </AppShell>
  )

  if (error || !book) return (
    <AppShell>
      <div className="flex h-full min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
        <p className="text-muted-foreground">{error ?? t`Book not found`}</p>
        <Link to="/" className="text-sm text-primary hover:underline"><Trans>Back to library</Trans></Link>
      </div>
    </AppShell>
  )

  // ── Pieces ──────────────────────────────────────────────────────────────────

  const refreshStats = () =>
    api.get<ReadingStatsResponse>(`/books/${id}/reading-stats?tz_offset=${new Date().getTimezoneOffset()}`).then(setReadingStats).catch(() => {})

  async function deleteHighlight(annotationId: number) {
    try {
      await api.delete(`/annotations/${annotationId}`)
      setAnnotations(prev => prev.filter(a => a.id !== annotationId))
      setConfirmingHighlight(null)
      toast.success(t`Highlight deleted`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t`Failed to delete highlight`)
    }
  }

  async function saveHighlightNote(annotationId: number) {
    try {
      const note = editingNoteDraft.trim() || null
      await api.put(`/annotations/${annotationId}`, { note })
      setAnnotations(prev => prev.map(a => (a.id === annotationId ? { ...a, note } : a)))
      setEditingNoteId(null)
      toast.success(note ? t`Note saved — syncs to your devices` : t`Note removed`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t`Failed to save note`)
    }
  }

  async function downloadOne(f: BookFile) {
    if (!book) return
    setDownloadMenuOpen(false)
    setDownloadingId(f.id)
    try { await downloadFile(book, f) }
    catch (e) { setError(e instanceof Error ? e.message : t`Download failed`) }
    finally { setDownloadingId(null) }
  }

  const readable = book.files.some(f => ['epub', 'cbz', 'cbr', 'pdf'].includes(f.format))
  const showProgress = progressPct != null && progressPct > 0 && bookStatus !== 'unread'
  const pctShown = progressPct != null ? Math.round(progressPct * 100) : 0
  const own = readingStats?.own ?? null
  const hasSessions = !!own && own.sessions > 0
  const typeLabel = bookTypes.find(bt => bt.id === book.book_type_id)?.label
  const seriesHref = book.series ? `/?tab=series&series_detail=${encodeURIComponent(book.series)}` : null
  const shortDay = (d: Date) => d.toLocaleDateString(i18n.locale, { month: 'short', day: 'numeric' })
  /* eslint-disable lingui/no-unlocalized-strings -- Tailwind classes */
  const inputCls = 'w-full rounded-lg bg-secondary px-3 py-1.5 text-[15px] text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-2 focus:ring-ring'
  const smallCaps = 'text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground'
  const menuCls = 'absolute top-full mt-1.5 z-40 min-w-52 max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-border bg-card py-1 shadow-xl'
  /* eslint-enable lingui/no-unlocalized-strings */

  const STATUS_PILLS: { id: ReadingStatus; label: string; title?: string }[] = [
    { id: 'unread', label: t`Unread` },
    { id: 'reading', label: t`Reading` },
    { id: 'read', label: t`Read` },
    { id: 'want_to_read', label: t`Want to read`, title: t`Queue this book up next` },
    { id: 'shelved', label: t`Shelved`, title: t`Set aside, keeps your progress` },
  ]

  // ── Header line: where you are, previous/next volume, the page's actions ────

  const crumbs = (
    <nav className="flex min-w-0 items-center gap-1.5 text-[13px] text-muted-foreground" aria-label={t`Breadcrumb`}>
      <Link to="/?tab=books" className="shrink-0 hover:text-foreground transition-colors"><Trans>Library</Trans></Link>
      {book.series && seriesHref ? (
        <>
          <span aria-hidden>/</span>
          <Link to={seriesHref} className="truncate hover:text-foreground transition-colors">{book.series}</Link>
        </>
      ) : adjacent?.mode === 'author' && book.author ? (
        <>
          <span aria-hidden>/</span>
          <Link to={`/?tab=books&author=${encodeURIComponent(book.author)}`} className="truncate hover:text-foreground transition-colors">{book.author}</Link>
        </>
      ) : null}
      {adjacent && (adjacent.prev || adjacent.next) && (
        <span className="ml-1 inline-flex shrink-0 items-center">
          {adjacent.prev ? (
            <Link to={`/books/${adjacent.prev.id}`} title={adjacent.prev.title} aria-label={t`Previous volume`}
              className="grid h-7 w-7 place-items-center rounded-full hover:bg-secondary hover:text-foreground transition-colors">
              <ChevronLeft className="h-4 w-4" />
            </Link>
          ) : <span className="grid h-7 w-7 place-items-center opacity-30" aria-hidden><ChevronLeft className="h-4 w-4" /></span>}
          {adjacent.next ? (
            <Link to={`/books/${adjacent.next.id}`} title={adjacent.next.title} aria-label={t`Next volume`}
              className="grid h-7 w-7 place-items-center rounded-full hover:bg-secondary hover:text-foreground transition-colors">
              <ChevronRight className="h-4 w-4" />
            </Link>
          ) : <span className="grid h-7 w-7 place-items-center opacity-30" aria-hidden><ChevronRight className="h-4 w-4" /></span>}
        </span>
      )}
    </nav>
  )

  const pageActions = editing ? (
    <div className="flex items-center gap-1.5">
      <Pill variant="quiet" onClick={cancelEdit}><X className="h-4 w-4" /><Trans>Cancel</Trans></Pill>
      <Pill variant="primary" onClick={saveEdit} disabled={saving}>
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
        {saving ? t`Saving…` : t`Save`}
      </Pill>
    </div>
  ) : confirmDelete ? (
    <div className="flex items-center gap-1.5">
      {(() => { const title = book.title; return <span className="hidden max-w-[220px] truncate text-[14px] text-muted-foreground sm:inline"><Trans>Delete "{title}"?</Trans></span> })()}
      <Pill variant="quiet" onClick={() => setConfirmDelete(false)}><Trans>Cancel</Trans></Pill>
      <Pill onClick={handleDelete} disabled={deleting} className="bg-destructive text-white hover:bg-destructive/90">
        {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
        {deleting ? t`Deleting…` : t`Delete`}
      </Pill>
    </div>
  ) : (canEdit || canDelete) ? (
    <div className="-mr-2 flex flex-wrap items-center justify-end gap-0.5">
      {canEdit && (
        <Pill variant="quiet" onClick={startEdit} aria-label={t`Edit`}>
          <Edit2 className="h-4 w-4" /><span className="hidden sm:inline"><Trans>Edit</Trans></span>
        </Pill>
      )}
      {canEdit && book.content_type !== 'chapter' && (
        <Pill variant="quiet" onClick={() => setFetchModalOpen(true)} aria-label={t`Fetch Metadata`}>
          <Sparkles className="h-4 w-4" /><span className="hidden sm:inline"><Trans>Fetch Metadata</Trans></span>
        </Pill>
      )}
      {canEdit && libraries.some(l => l.can_edit) && (
        <div className="relative" ref={libMenuRef}>
          <Pill variant="quiet" onClick={() => setLibMenuOpen(o => !o)} aria-expanded={libMenuOpen} aria-label={t`Libraries`}
            className={cn(libMenuOpen && 'bg-secondary text-foreground')}>
            <LibraryIcon className="h-4 w-4" /><span className="hidden sm:inline"><Trans>Libraries</Trans></span>
            {localLibIds.length > 0 && (
              <span className="ml-0.5 grid h-5 min-w-5 place-items-center rounded-full bg-secondary px-1.5 text-[11px] font-semibold tabular-nums text-foreground">
                {localLibIds.length}
              </span>
            )}
          </Pill>
          {libMenuOpen && (
            <div className={cn(menuCls, 'right-0')}>
              <p className="border-b border-border px-3 pb-2 pt-1.5 text-[11px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
                <Trans>Add to library</Trans>
              </p>
              {libraries.filter(l => l.can_edit).map(lib => {
                const inLib = localLibIds.includes(lib.id)
                const pending = libPending.has(lib.id)
                return (
                  <div key={lib.id} className="flex w-full items-center gap-2 px-3 py-2 text-[14px] hover:bg-secondary transition-colors">
                    {pending
                      ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
                      : <LibraryIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                    <Link to={`/?tab=books&library_id=${lib.id}`} onClick={() => setLibMenuOpen(false)}
                      className="flex-1 truncate hover:text-primary transition-colors">
                      {lib.name}
                    </Link>
                    <button onClick={() => toggleLibrary(lib.id)} disabled={pending}
                      className="shrink-0 rounded p-0.5 hover:bg-muted transition-colors disabled:opacity-60"
                      title={inLib ? t`Remove from library` : t`Add to library`}
                      aria-label={inLib ? t`Remove from library` : t`Add to library`}>
                      <Check className={cn('h-3.5 w-3.5', inLib ? 'text-primary' : 'text-muted-foreground/30')} />
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
      {canEdit && (
        <Pill variant="quiet" onClick={() => setShowShare(true)} title={t`Share this book as a public page with its metadata only`} aria-label={t`Share`}>
          <Share2 className="h-4 w-4" /><span className="hidden sm:inline"><Trans>Share</Trans></span>
        </Pill>
      )}
      {canDelete && (
        <Pill variant="destructive" icon onClick={() => setConfirmDelete(true)} aria-label={t`Delete`} title={t`Delete`}>
          <Trash2 className="h-4 w-4" />
        </Pill>
      )}
    </div>
  ) : null

  // ── Rail: cover, files, details, genres ─────────────────────────────────────

  const cover = (
    <div
      className={cn('relative aspect-[2/3] overflow-hidden rounded-[12px] bg-muted', canEdit && 'group cursor-pointer')}
      style={{ boxShadow: 'var(--cover-bloom-hero)' }}
      onClick={() => canEdit && setCoverPickerOpen(true)}
      title={canEdit ? t`Change cover` : undefined}
    >
      <CoverImage
        src={book.cover_path ? `/api/books/${book.id}/cover?v=${encodeURIComponent(book.updated_at)}` : null}
        alt={book.title}
        loading="eager"
        iconClassName="w-12 h-12"
      />
      {canEdit && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 transition-opacity group-hover:opacity-100">
          <Camera className="h-8 w-8 text-white drop-shadow" />
        </div>
      )}
    </div>
  )

  const fileLines = book.files.length > 0 ? (
    <div className="mt-4 space-y-1 px-1">
      {book.files.map(f => (
        <div key={f.id} className="flex items-center justify-between text-[13px] text-muted-foreground">
          <span className="uppercase">{f.format}</span>
          {f.file_size ? <span className="tabular-nums">{formatBytes(f.file_size)}</span> : null}
        </div>
      ))}
    </div>
  ) : null

  const wordsValue = book.word_count != null
    ? (() => { const words = book.word_count.toLocaleString(i18n.locale); return t`${words} words` })()
    : null

  const details = editing ? (
    <div className="grid grid-cols-1 gap-y-3">
      <EditField label={t`Year`} value={field('year') ?? ''} onChange={v => setField('year', v)} type="number" placeholder="2024" className={inputCls} />
      <EditField label={t`Language`} value={field('language') ?? ''} onChange={v => setField('language', v)} placeholder="en" className={inputCls} />
      <EditField label={t`ISBN`} value={field('isbn') ?? ''} onChange={v => setField('isbn', v)} placeholder="978-..." className={inputCls} />
      <EditField label={t`Publisher`} value={field('publisher') ?? ''} onChange={v => setField('publisher', v)} placeholder={t`Publisher`} className={inputCls} />
      <label className="block">
        <span className="text-[13px] text-muted-foreground"><Trans>Type</Trans></span>
        <select value={draftBookTypeId ?? ''} onChange={e => setDraftBookTypeId(e.target.value ? Number(e.target.value) : null)} className={cn(inputCls, 'mt-1')}>
          <option value="">{t`No type`}</option>
          {bookTypes.map(bt => <option key={bt.id} value={bt.id}>{bt.label}</option>)}
        </select>
      </label>
      <label className="block">
        <span className="text-[13px] text-muted-foreground"><Trans>Content</Trans></span>
        <select value={draft.content_type ?? 'volume'} onChange={e => setDraft(d => ({ ...d, content_type: e.target.value }))} className={cn(inputCls, 'mt-1')}>
          <option value="volume">{t`Volume`}</option>
          <option value="chapter">{t`Chapter`}</option>
        </select>
      </label>
    </div>
  ) : (
    <div className="grid grid-cols-2 gap-x-4 gap-y-4">
      {book.year != null && <Field label={t`Year`} value={String(book.year)} />}
      {book.language && <Field label={t`Language`} value={book.language} />}
      {book.files.length > 0 && <Field label={t`Format`} value={book.files.map(f => f.format.toUpperCase()).join(', ')} />}
      {/* How long the whole book would take at your pace (#187). In-progress
          books additionally get "Est. remaining" in the Reading section. */}
      {estimate?.seconds != null && (
        <Field label={t`Est. read time`} value={
          <span title={describePace(estimate.pace, estimate.method)}>
            {formatEstimateHours(estimate.seconds)}
            {estimate.days != null && <span className="block text-[13px] text-muted-foreground">{formatEstimateDays(estimate.days)}</span>}
          </span>
        } />
      )}
      {/* Hardcover match, only when the sync matcher has linked this book */}
      {book.hardcover_slug && (
        // eslint-disable-next-line lingui/no-unlocalized-strings -- product name
        <Field label="Hardcover" className="col-span-2" value={
          <a href={`https://hardcover.app/books/${book.hardcover_slug}`} target="_blank" rel="noopener noreferrer"
            className="block truncate text-primary hover:underline underline-offset-4">
            {book.hardcover_slug}
          </a>
        } />
      )}
      <Field label={t`Type`} value={typeLabel ?? <span className="italic text-muted-foreground/60">{t`None`}</span>} />
      <Field label={t`Content`} value={book.content_type === 'chapter' ? t`Chapter` : t`Volume`} />
      {wordsValue && <Field label={t`Words`} value={wordsValue} />}
      {book.publisher && <Field label={t`Publisher`} value={book.publisher} className="col-span-2" />}
      {book.isbn && <Field label={t`ISBN`} value={<span className="break-all">{book.isbn}</span>} className="col-span-2" />}
    </div>
  )

  const genres = editing ? (
    <div>
      <div className="mb-2 text-[13px] text-muted-foreground"><Trans>Genres</Trans></div>
      {draftTags.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {draftTags.map(tag => (
            <span key={tag} className="inline-flex h-7 items-center gap-1 rounded-full bg-secondary pl-3 pr-2 text-[13px] text-foreground">
              {tag}
              <button type="button" onClick={() => setDraftTags(prev => prev.filter(x => x !== tag))}
                aria-label={t`Remove`} className="text-muted-foreground hover:text-destructive transition-colors">
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <AutocompleteInput
        value={tagInput}
        onChange={setTagInput}
        suggestions={facets.tags.filter(x => !draftTags.includes(x))}
        placeholder={t`Add tag…`}
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
        className={inputCls}
      />
    </div>
  ) : book.tags.length > 0 ? (
    <div>
      <div className="mb-2 text-[13px] text-muted-foreground"><Trans>Genres</Trans></div>
      <div className="flex flex-wrap gap-1.5">
        {book.tags.map(tg => (
          <button key={tg.id} type="button" onClick={() => navigate(`/?tab=books&tag=${encodeURIComponent(tg.tag)}`)}
            className="inline-flex h-7 items-center rounded-full bg-secondary px-3 text-[13px] text-foreground hover:bg-muted hover:text-primary transition-colors">
            {tg.tag}
          </button>
        ))}
      </div>
    </div>
  ) : null

  // The rail is two grid children so phones can read cover, then the hero,
  // then details; on desktop both sit in the left column.
  const railCover = (
    <div className="mx-auto w-[200px] sm:w-[220px] lg:mx-0 lg:col-start-1 lg:row-start-1">
      {cover}
      {fileLines}
    </div>
  )
  const railDetails = (
    <aside className="order-3 mx-auto w-full max-w-[420px] lg:order-none lg:mx-0 lg:max-w-none lg:col-start-1 lg:row-start-2">
      <div className={cn(smallCaps, 'mb-3')}><Trans>Details</Trans></div>
      {details}
      {genres && <div className="mt-6">{genres}</div>}
    </aside>
  )

  // ── Hero: series, title, author, status, progress, actions, sync, rating ───

  const heroTitle = editing ? (
    <div className="flex flex-col gap-2">
      <input value={field('title') ?? ''} onChange={e => setField('title', e.target.value)} placeholder={t`Title`}
        className={cn(inputCls, 'font-display text-[24px] font-bold py-2')} />
      <input value={field('subtitle') ?? ''} onChange={e => setField('subtitle', e.target.value)} placeholder={t`Subtitle (optional)`}
        className={inputCls} />
      <AutocompleteInput value={field('author') ?? ''} onChange={v => setField('author', v)} suggestions={facets.authors}
        placeholder={t`Author`} className={inputCls} />
      <div className="flex gap-2">
        <AutocompleteInput value={field('series') ?? ''} onChange={v => setField('series', v)} suggestions={facets.series}
          placeholder={t`Series name`} className={cn(inputCls, 'flex-1 min-w-0')} />
        <input value={field('series_index') ?? ''} onChange={e => setField('series_index', e.target.value)} type="number" step="0.1"
          placeholder="#" className={cn(inputCls, 'w-24')} />
      </div>
    </div>
  ) : (
    <>
      {book.series && (
        <button type="button" onClick={() => navigate(`/?tab=books&series=${encodeURIComponent(book.series!)}`)}
          className={cn(smallCaps, 'block text-left text-primary hover:underline underline-offset-4')}>
          {book.series}{book.series_index != null ? ` · ${book.series_index}` : ''}
        </button>
      )}
      <h1 className="mt-2 font-display text-[30px] sm:text-[34px] font-bold leading-[1.05] text-foreground">
        {book.title}
        {book.content_type === 'chapter' && (
          <span className="ml-3 inline-flex h-6 -translate-y-1 items-center rounded-full bg-secondary px-2.5 align-middle font-sans text-[11px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
            <Trans>Chapter</Trans>
          </span>
        )}
      </h1>
      {book.subtitle && <p className="mt-1.5 text-[16px] text-foreground/80">{book.subtitle}</p>}
      {book.author && (
        <button type="button" onClick={() => navigate(`/?tab=books&author=${encodeURIComponent(book.author!)}`)}
          className="mt-1.5 block text-left text-[16px] text-muted-foreground hover:text-primary transition-colors">
          {book.author}
        </button>
      )}
      <DevicePills bookId={book.id} className="mt-3 flex" />
    </>
  )

  const statusRow = (
    <div className="mt-5 flex flex-wrap items-center gap-2">
      {STATUS_PILLS.map(s => (
        <button
          key={s.id}
          type="button"
          disabled={statusSaving}
          onClick={() => handleStatusChange(s.id)}
          title={s.title}
          aria-pressed={bookStatus === s.id}
          className={cn(
            'h-8 rounded-full px-3.5 text-[13px] font-medium transition-colors disabled:cursor-default',
            bookStatus === s.id ? 'bg-foreground text-background' : 'bg-secondary text-muted-foreground hover:text-foreground',
          )}
        >
          {s.label}
        </button>
      ))}
      {statusSaving && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
    </div>
  )

  const progressRow = showProgress ? (
    <div className="mt-5 max-w-[560px]">
      <div className="flex items-center gap-3">
        <div className="h-[6px] flex-1 overflow-hidden rounded-full" style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--background))' }}>
          <div className="h-full rounded-full bg-primary transition-[width] duration-700 ease-out"
            style={{ width: progressAnimated ? `${Math.min(100, pctShown)}%` : '0%' }} />
        </div>
        <span className="text-[17px] font-bold tabular-nums text-foreground">{pctShown}%</span>
      </div>
      {book.hardcover_pages != null && book.hardcover_pages > 0 && (() => {
        // Print-edition pagination (from the matched Hardcover edition):
        // font-size agnostic, unlike the device's reflowed page count.
        const page = Math.max(1, Math.round((progressPct ?? 0) * book.hardcover_pages))
        const totalPages = book.hardcover_pages
        return <div className="mt-1.5 text-[13px] text-muted-foreground"><Trans>p. {page} of {totalPages}</Trans></div>
      })()}
    </div>
  ) : null

  const downloadPill = book.files.length === 1 ? (
    <Pill size="lg" icon onClick={() => downloadOne(book.files[0])} disabled={downloadingId != null}
      aria-label={t`Download`} title={(() => { const fmt = book.files[0].format.toUpperCase(); return t`Download ${fmt}` })()}>
      {downloadingId != null ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
    </Pill>
  ) : book.files.length > 1 ? (
    <div className="relative" ref={downloadMenuRef}>
      <Pill size="lg" icon onClick={() => setDownloadMenuOpen(o => !o)} aria-expanded={downloadMenuOpen} aria-label={t`Download`} title={t`Download`}>
        {downloadingId != null ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
      </Pill>
      {downloadMenuOpen && (
        <div className={cn(menuCls, 'left-0 min-w-44')}>
          {book.files.map(f => (
            <button key={f.id} type="button" onClick={() => downloadOne(f)} disabled={downloadingId === f.id}
              className="flex w-full items-center justify-between gap-4 px-3 py-2 text-[14px] text-foreground hover:bg-secondary transition-colors disabled:opacity-60">
              <span className="flex items-center gap-2 font-medium uppercase"><Download className="h-3.5 w-3.5 text-muted-foreground" />{f.format}</span>
              {f.file_size ? <span className="text-[13px] tabular-nums text-muted-foreground">{formatBytes(f.file_size)}</span> : null}
            </button>
          ))}
        </div>
      )}
    </div>
  ) : null

  const actionRow = (readable || book.files.length > 0) ? (
    <div className="mt-5 flex flex-wrap items-center gap-2">
      {readable && (
        <Pill variant="primary" size="lg" onClick={() => navigate(`/reader/${book.id}`)}>
          <Play className="h-4 w-4 fill-current" />
          {showProgress ? <Trans>Resume</Trans> : <Trans context="action">Read</Trans>}
        </Pill>
      )}
      {isMember(user) && book.files.length > 0 && (
        <SendButton books={[{ id: book.id, title: book.title, files: book.files }]} variant="pill" />
      )}
      {downloadPill}
    </div>
  ) : null

  const syncRow = kosyncDevice && kosyncLinked ? (
    <div className="mt-4">
      <span className="inline-flex h-9 max-w-full items-center gap-2 rounded-full bg-secondary px-4 text-[14px] font-medium text-foreground">
        <RefreshCw className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">
          {(() => { const device = kosyncDevice; return <Trans>Synced from {device}</Trans> })()}
          <span className="text-muted-foreground">
            {kosyncLinked.percentage != null && ` · ${Math.round(kosyncLinked.percentage * 100)}%`}
            {kosyncLinked.timestamp ? ` · ${shortDay(new Date(kosyncLinked.timestamp * 1000))}` : ''}
          </span>
        </span>
      </span>
    </div>
  ) : kosyncUnlinked.length > 0 && !kosyncDevice ? (
    <div className="mt-4">
      {!kosyncPickerOpen ? (
        <Pill variant="quiet" className="-ml-3"
          onClick={() => { setKosyncPickerOpen(true); setKosyncLinkDoc(kosyncUnlinked[0]?.document ?? '') }}
          title={t`Attach progress synced from a KOReader-compatible app to this book`}>
          <Link2 className="h-4 w-4" /><Trans>Link KOReader sync</Trans>
        </Pill>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[14px] text-muted-foreground"><Trans>Synced document</Trans></span>
          <select value={kosyncLinkDoc} onChange={e => setKosyncLinkDoc(e.target.value)}
            className="h-9 max-w-[18rem] rounded-lg bg-secondary px-2.5 text-[14px] text-foreground focus:outline-none focus:ring-2 focus:ring-ring">
            {kosyncUnlinked.map(d => (
              <option key={d.document} value={d.document}>
                {d.device || t`unknown device`} · {Math.round(d.percentage * 100)}% · {new Date(d.timestamp * 1000).toLocaleDateString(i18n.locale)} · {d.document.slice(0, 8)}
              </option>
            ))}
          </select>
          <Pill disabled={kosyncLinking || !kosyncLinkDoc} onClick={linkKosyncDocument}>
            {kosyncLinking ? t`Linking…` : t`Link`}
          </Pill>
          <Pill variant="quiet" onClick={() => setKosyncPickerOpen(false)}><Trans>Cancel</Trans></Pill>
        </div>
      )}
    </div>
  ) : null

  const ratingRow = (
    <div className="mt-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StarRating value={rating} onChange={saveRating} />
        <span className="text-[13px] text-muted-foreground">
          {rating ? t`You rated this ${rating}/5` : t`Rate this book`}
        </span>
        {!review && !editingReview && (
          <button type="button" onClick={startEditingReview} className="text-[13px] text-primary hover:underline underline-offset-4">
            <Trans>Add a review</Trans>
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
          placeholder={t`What did you think? (optional, saved automatically)`}
          className={cn(inputCls, 'mt-3 resize-y py-2 leading-relaxed')}
        />
      ) : review ? (
        <div className="group/review relative mt-3 border-l-2 border-primary/30 pl-3">
          <p className="whitespace-pre-wrap pr-7 text-[15px] leading-relaxed text-foreground/90">{review}</p>
          <button type="button" onClick={startEditingReview} aria-label={t`Edit review`} title={t`Edit review`}
            className="absolute right-0 top-0 p-1 text-muted-foreground/50 transition-colors hover:text-foreground">
            <Edit2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : null}
    </div>
  )

  const hero = (
    <div className="min-w-0 max-w-[680px]">
      {heroTitle}
      {statusRow}
      {progressRow}
      {actionRow}
      {syncRow}
      {ratingRow}
    </div>
  )

  // ── Sections ────────────────────────────────────────────────────────────────

  const descriptionSection = (
    <section className="max-w-[720px]">
      <SectionTitle size={20}><Trans>Description</Trans></SectionTitle>
      {editing ? (
        <textarea value={field('description') ?? ''} onChange={e => setField('description', e.target.value)} rows={8}
          placeholder={t`No description`} className={cn(inputCls, 'mt-3 resize-y py-2 leading-relaxed')} />
      ) : book.description ? (
        <>
          <p ref={descRef} className={cn('mt-3 text-[15px] leading-relaxed text-foreground/85', !descExpanded && 'line-clamp-4')}>
            {book.description}
          </p>
          {(descClamped || descExpanded) && (
            <button type="button" onClick={() => setDescExpanded(e => !e)}
              className="mt-2 text-[14px] font-medium text-primary hover:underline underline-offset-4">
              {descExpanded ? t`Show less` : t`Show more`}
            </button>
          )}
        </>
      ) : (
        <p className="mt-3 text-[15px] text-muted-foreground"><Trans>No description</Trans></p>
      )}
    </section>
  )

  const readingSection = readingStats ? (
    <section>
      <SectionTitle size={20} action={
        <Pill variant="quiet" className="-my-2 -mr-3" onClick={() => setShowPositionHistory(true)} title={t`Restore an earlier reading position after a bad sync`}>
          <HistoryIcon className="h-4 w-4" /><Trans>Position history</Trans>
        </Pill>
      }>
        <Trans>Reading</Trans>
      </SectionTitle>
      {hasSessions && own ? (
        <ReadingBody own={own} aggregate={readingStats.aggregate} bookId={Number(id)} onChange={refreshStats} />
      ) : (
        // No history yet: the manual-tracking entry point (paper / un-synced device).
        <div className="mt-3 flex flex-col items-start gap-3">
          <p className="max-w-[60ch] text-[15px] text-muted-foreground">
            <Trans>No reading logged yet. Track time by hand, handy for a paper copy or a device that isn&apos;t synced.</Trans>
          </p>
          <ManualLogControls bookId={Number(id)} onChange={refreshStats} />
        </div>
      )}
    </section>
  ) : null

  const sessionsSection = hasSessions && own ? (
    <section>
      <div className="flex items-center gap-2">
        <button type="button" onClick={() => setSessionsOpen(o => !o)} aria-expanded={sessionsOpen}
          className="flex items-center gap-2 text-left">
          <h2 className="font-display text-[20px] font-bold leading-none text-foreground">
            {(() => { const n = own.sessions; return <Trans>Sessions <span className="font-semibold text-muted-foreground">({n})</span></Trans> })()}
          </h2>
          <ChevronDown className={cn('h-5 w-5 text-muted-foreground transition-transform', sessionsOpen && 'rotate-180')} />
        </button>
        <SessionLogHint />
      </div>
      {sessionsOpen && (
        <div className="mt-4">
          <SessionLog bookId={Number(id)} onChange={refreshStats} />
        </div>
      )}
    </section>
  ) : null

  const highlightsSection = annotations.length > 0 ? (
    <section className="max-w-[720px]">
      <div className="flex items-baseline justify-between gap-4">
        <button type="button" onClick={() => setHighlightsOpen(o => !o)} aria-expanded={highlightsOpen}
          className="flex items-center gap-2 text-left">
          <h2 className="font-display text-[20px] font-bold leading-none text-foreground">
            {(() => { const n = annotations.length; return <Trans>Highlights &amp; Notes <span className="font-semibold text-muted-foreground">({n})</span></Trans> })()}
          </h2>
          <ChevronDown className={cn('h-5 w-5 text-muted-foreground transition-transform', highlightsOpen && 'rotate-180')} />
        </button>
        <Link to="/highlights" className="shrink-0 text-[15px] font-medium text-primary hover:underline underline-offset-4">
          <Trans>All highlights</Trans>
        </Link>
      </div>
      {highlightsOpen && (
        <ul className="mt-2">
          {annotations.map((a, i) => (
            <li key={a.id} className={cn('group py-4', i > 0 && 'border-t border-border/70')}>
              {a.highlighted_text && (
                <p className="text-[15px] leading-relaxed text-foreground/90">{a.highlighted_text}</p>
              )}
              {editingNoteId === a.id ? (
                <div className="mt-2.5 flex flex-col gap-2">
                  <textarea value={editingNoteDraft} onChange={e => setEditingNoteDraft(e.target.value)} autoFocus rows={2}
                    placeholder={t`Your note, syncs back to KOReader on the next sync`}
                    className={cn(inputCls, 'py-2 text-[14px]')} />
                  <span className="flex items-center gap-1.5">
                    <Pill variant="primary" onClick={() => saveHighlightNote(a.id)}><Trans>Save note</Trans></Pill>
                    <Pill variant="quiet" onClick={() => setEditingNoteId(null)}><Trans>Cancel</Trans></Pill>
                  </span>
                </div>
              ) : a.note ? (
                <p className="mt-1.5 flex items-start gap-1.5 text-[14px] text-muted-foreground">
                  <StickyNote className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span className="leading-relaxed">{a.note}</span>
                </p>
              ) : null}
              <div className="mt-1.5 flex items-center justify-between gap-3">
                <span className="min-w-0 truncate text-[12px] text-muted-foreground/80">
                  {[a.chapter, a.datetime ? shortDay(new Date(a.datetime)) : null].filter(Boolean).join(' · ')}
                </span>
                {confirmingHighlight === a.id ? (
                  <span className="flex shrink-0 items-center gap-3 text-[13px]">
                    <button onClick={() => deleteHighlight(a.id)} className="font-medium text-destructive hover:underline">
                      <Trans>Delete</Trans>
                    </button>
                    <button onClick={() => setConfirmingHighlight(null)} className="text-muted-foreground hover:text-foreground">
                      <Trans>Cancel</Trans>
                    </button>
                  </span>
                ) : (
                  <span className="flex shrink-0 items-center gap-0.5">
                    <button
                      onClick={() => { setEditingNoteDraft(a.note ?? ''); setEditingNoteId(prev => (prev === a.id ? null : a.id)) }}
                      title={a.note ? t`Edit note` : t`Add a note`}
                      aria-label={a.note ? t`Edit note` : t`Add a note`}
                      className="rounded p-1 text-muted-foreground/60 opacity-60 transition-all hover:text-foreground focus:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
                    >
                      <Edit2 className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => setConfirmingHighlight(a.id)}
                      title={t`Delete this highlight`}
                      aria-label={t`Delete this highlight`}
                      className="rounded p-1 text-muted-foreground/60 opacity-60 transition-all hover:text-destructive focus:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  ) : null

  const seriesSection = seriesVolumes.length > 1 && seriesHref ? (
    <section>
      <SectionTitle size={20} action={
        <Link to={seriesHref} className="text-primary hover:underline underline-offset-4"><Trans>Open series</Trans></Link>
      }>
        <Trans>In this series</Trans>
      </SectionTitle>
      <div className="-mx-1 mt-4 flex gap-4 overflow-x-auto px-1 pb-2 pt-1">
        {seriesVolumes.map(v => {
          const here = v.id === book.id
          const idx = v.series_index
          return (
            <div key={v.id} className="w-[104px] shrink-0">
              <button type="button" onClick={() => !here && setQuickLookId(v.id)} aria-label={v.title} title={v.title}
                className={cn(
                  'relative block aspect-[2/3] w-full overflow-hidden rounded-[8px] bg-muted transition',
                  here ? 'cursor-default ring-2 ring-primary ring-offset-2 ring-offset-background'
                    : v.reading_status === 'read' ? 'opacity-55 hover:opacity-100' : 'hover:opacity-90',
                )}
                style={{ boxShadow: 'var(--cover-bloom-fan)' }}>
                <CoverImage src={v.cover_path ? coverSrc(v.id, { w: 160, v: v.updated_at }) : null} alt="" iconClassName="w-5 h-5" />
              </button>
              {here ? (
                <div className="mt-2 truncate text-[13px] font-medium text-foreground">{idx != null ? t`Vol. ${idx}` : v.title}</div>
              ) : (
                <Link to={`/books/${v.id}`} title={v.title}
                  className="mt-2 block truncate text-[13px] text-muted-foreground hover:text-foreground transition-colors">
                  {idx != null ? t`Vol. ${idx}` : v.title}
                </Link>
              )}
            </div>
          )
        })}
      </div>
    </section>
  ) : null

  return (
    <AppShell>
      <PageBody width="wide">
        {/* The page header for a book: where you are on the left, the page's
            actions on the right. The hero's h1 is the title. */}
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          {crumbs}
          {pageActions}
        </div>

        <div className="grid items-start gap-10 lg:grid-cols-[220px_minmax(0,1fr)] lg:grid-rows-[auto_1fr]">
          {railCover}
          <div className="order-2 flex min-w-0 flex-col gap-7 lg:order-none lg:col-start-2 lg:row-start-1 lg:row-span-2">
            {hero}
            {descriptionSection}
            {readingSection}
            {readingStats?.intensity && (
              <IntensityBlock data={readingStats.intensity} bookId={Number(id)} onChange={refreshStats} />
            )}
            {readingStats?.chapters && readingStats.chapters.length > 0 && (
              <ChapterTimesBlock chapters={readingStats.chapters} />
            )}
            {sessionsSection}
            {highlightsSection}
            {seriesSection}
          </div>
          {railDetails}
        </div>
      </PageBody>

      {quickLookId != null && <QuickLook bookId={quickLookId} onClose={() => setQuickLookId(null)} />}
      <MetadataFetchModal
        book={book}
        open={fetchModalOpen}
        onClose={() => setFetchModalOpen(false)}
        onApplied={updated => { setBook(updated); setDraft(updated) }}
      />
      <CoverPickerModal
        book={book}
        open={coverPickerOpen}
        onClose={() => setCoverPickerOpen(false)}
        onApplied={updated => { setBook(updated); setDraft(updated) }}
      />
      {showShare && (
        <ShareModal
          title={book.title}
          endpoint={`/books/${book.id}/share`}
          noun="book"
          onClose={() => setShowShare(false)}
        />
      )}
      {showPositionHistory && (
        <PositionHistoryModal
          bookId={Number(id)}
          onClose={() => setShowPositionHistory(false)}
          onRestored={() => {
            refreshStats()
            api.get<BookStatus>(`/books/${id}/status`)
              .then(s => { setBookStatus(s.status); setProgressPct(s.progress_pct); setCfi(s.cfi ?? null) })
              .catch(() => {})
          }}
        />
      )}
    </AppShell>
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
 *  drawn whenever the timeline carries 2+ progress points (the backend only
 *  emits progress_pct where a position is actually known). */
export function ActivityChart({ timeline, bookId, onChange }: {
  timeline: { date: string; seconds: number; pages: number; progress_pct?: number | null }[]
  bookId?: number
  onChange?: () => void
}) {
  const { t, i18n } = useLingui()
  // Click-through: a bar opens that day's sessions for quick trim/delete —
  // e.g. an accidentally imported sitting, without scrolling to the full log.
  const [dayOpen, setDayOpen] = useState<string | null>(null)
  if (timeline.length < 2) return null
  // Real time axis: fill the calendar days between the first and last reading
  // day with zero bars. Active-days-only bars, evenly spaced, misrepresent the
  // span (two adjacent bars could be one day or a month apart) — and with 2–3
  // reading days they rendered as giant full-width slabs. Capped at the last
  // 365 days so a years-long log can't produce more bars than pixels.
  const byDate = new Map(timeline.map(d => [d.date, d]))
  // eslint-disable-next-line lingui/no-unlocalized-strings -- ISO time suffix
  const startD = new Date(timeline[0].date + 'T00:00:00')
  // eslint-disable-next-line lingui/no-unlocalized-strings -- ISO time suffix
  const endD = new Date(timeline[timeline.length - 1].date + 'T00:00:00')
  let days: typeof timeline = []
  for (const d = new Date(startD); d <= endD; d.setDate(d.getDate() + 1)) {
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    days.push(byDate.get(key) ?? { date: key, seconds: 0, pages: 0 })
  }
  if (days.length > 365) days = days.slice(-365)
  const max = Math.max(...days.map(d => d.seconds), 1)
  const n = days.length
  const first = days[0]
  const last = days[days.length - 1]
  const fmtDay = (iso: string) =>
    new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  // Progress journey: % complete on each reading day, drawn as a slim lane below.
  const hasJourney = days.some(d => d.progress_pct != null)
  const lanePts = (days
    .map((d, i) => (d.progress_pct != null
      ? `${((i / (n - 1)) * 100).toFixed(2)},${(100 - d.progress_pct).toFixed(2)}`
      : null))
    .filter(Boolean) as string[])
  const lastProgress = [...days].reverse().find(d => d.progress_pct != null)?.progress_pct ?? null
  return (
    // Always full card width — a narrower "day-sized bars" cap was tried and
    // looked half-finished against the full-width card chrome. Big bars for a
    // short history are fine; the gap-filled axis keeps the spacing honest.
    <div className="flex flex-col">
      <p className="text-xs text-muted-foreground/70 mb-1.5 shrink-0"><Trans>Activity</Trans></p>
      {/* Fixed-height strip: flex-1 fill only works inside a sized flex parent,
          and the hero wraps this in a plain div — flex-1 collapsed to 0px there
          (bars rendered into no vertical space at all) */}
      <div className="relative h-20">
        {/* faint baseline rule */}
        <div className="absolute bottom-0 left-0 right-0 h-px bg-border/40" />
        <div className={cn('absolute inset-0 flex items-end', n > 60 ? 'gap-px' : 'gap-1.5')}>
          {days.map(d => {
            const pct = d.seconds > 0 ? Math.max(d.seconds / max, 0.06) : 0
            const mins = Math.round(d.seconds / 60)
            const clickable = d.seconds > 0 && bookId != null
            const dayStr = fmtDay(d.date)
            const pages = d.pages, prog = d.progress_pct
            const tip = t`${dayStr}: ${mins}m` +
              (pages > 0 ? t`, ${pages} pages` : '') +
              (prog != null ? t` · ${prog}% in` : '') +
              (clickable ? t` — click for sessions` : '')
            return (
              <div
                key={d.date}
                className={cn(
                  'flex-1 min-w-px bg-primary/60 hover:bg-primary transition-colors rounded-t-sm',
                  clickable && 'cursor-pointer',
                )}
                style={{ height: pct > 0 ? `${Math.round(pct * 100)}%` : '0%' }}
                title={d.seconds > 0 ? tip : undefined}
                onClick={clickable ? () => setDayOpen(d.date) : undefined}
              />
            )
          })}
        </div>
      </div>
      <div className="flex justify-between text-xs text-muted-foreground/50 mt-1 shrink-0">
        <span>{first?.date ? fmtDay(first.date) : ''}</span>
        <span>{last?.date ? fmtDay(last.date) : ''}</span>
      </div>
      {dayOpen && bookId != null && createPortal(
        <>
          <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" onClick={() => setDayOpen(null)} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
            <div className="pointer-events-auto flex max-h-[70vh] w-full max-w-2xl flex-col rounded-xl border border-border bg-card shadow-xl">
              <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-3.5">
                <span className="flex items-center gap-1.5">
                  <h2 className="font-display text-sm text-foreground">
                    {(() => { const d = new Date(dayOpen + 'T00:00:00').toLocaleDateString(i18n.locale, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }); return t`Sessions · ${d}` })()}
                  </h2>
                  <SessionLogHint />
                </span>
                <button
                  type="button"
                  aria-label={t`Close`}
                  onClick={() => setDayOpen(null)}
                  className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="overflow-y-auto px-5 py-3">
                <SessionLog bookId={bookId} day={dayOpen} onChange={onChange} />
              </div>
            </div>
          </div>
        </>,
        document.body,
      )}
      {hasJourney && lanePts.length > 1 && (
        <div className="mt-3">
          <div className="flex items-center justify-between mb-1">
            <span className="flex items-center gap-1">
              <p className="text-xs text-muted-foreground/70"><Trans>Progress</Trans></p>
              <InfoHint text={t`How far through the book you'd read by each date.`} />
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
  if (!device || device === 'web-reader' || device === 'web') return t`Web reader`
  if (device === 'manual') return t`Manual`
  // eslint-disable-next-line lingui/no-unlocalized-strings -- product name
  if (device === 'koreader') return 'KOReader'
  return device
}

const SOURCE_SHADES = ['bg-primary', 'bg-primary/65', 'bg-primary/40', 'bg-primary/25']

// Where the reading time came from: a thin stacked bar + legend.
export function SourceSplit({ sources }: { sources: { device: string; seconds: number; sessions: number }[] }) {
  const total = sources.reduce((sum, s) => sum + s.seconds, 0)
  // A single source is a full-width solid bar — pointless; only split when it splits.
  if (total <= 0 || sources.length < 2) return null
  return (
    <div className="mt-4">
      <p className="text-xs text-muted-foreground/70 mb-1.5"><Trans>Where you read</Trans></p>
      {/* gap-px: adjacent primary shades are close — a hairline seam marks the split */}
      <div className="flex gap-px h-2 rounded-full overflow-hidden bg-muted">
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

export function IntensityBlock({ data, bookId, onChange }: { data: BookIntensity; bookId: number; onChange?: () => void }) {
  const { t } = useLingui()
  const max = Math.max(...data.curve, 1)
  const finished = data.pct_read >= 99
  const [confirmClear, setConfirmClear] = useState(false)
  const [clearing, setClearing] = useState(false)
  const clearImported = () => {
    setClearing(true)
    api.delete(`/books/${bookId}/imported-history`)
      .then(() => { setConfirmClear(false); onChange?.() })
      .catch(() => {})
      .finally(() => setClearing(false))
  }
  return (
    <section>
      <ChartSectionTitle
        title={<Trans>Reading intensity</Trans>}
        hint={t`Where in the book your time went — taller means more time spent on those pages.`}
        foot={<>
          <span className="font-medium tabular-nums text-foreground">{data.pages_read.toLocaleString()}</span>
          {' '}{(() => { const totalPages = data.total_pages.toLocaleString(); return <Trans>of {totalPages} pages</Trans> })()}
          {!finished && <span className="tabular-nums"> · {data.pct_read}%</span>}
          {' · '}{(() => { const dur = formatDuration(data.total_seconds); return <Trans>{dur} on device</Trans> })()}
        </>}
        action={
          <Pill variant="destructive" icon className="-my-2 -mr-2" onClick={() => setConfirmClear(o => !o)}
            aria-label={t`Clear imported history`}
            title={t`Clear imported history — remove this book's KOReader page data`}>
            <Trash2 className="h-4 w-4" />
          </Pill>
        }
      />
      {confirmClear && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[14px]">
          <span className="text-muted-foreground">
            <Trans>Remove all of this book&apos;s imported KOReader history? Web and manual sessions stay. Reading it on the device again will re-import from there on.</Trans>
          </span>
          <Pill onClick={clearImported} disabled={clearing} className="bg-destructive text-white hover:bg-destructive/90">
            {clearing ? t`Clearing…` : t`Clear`}
          </Pill>
          <Pill variant="quiet" onClick={() => setConfirmClear(false)}>
            <Trans>Cancel</Trans>
          </Pill>
        </div>
      )}
      {/* Dwell across the book, 0% → 100%. Taller bar = more time spent there. */}
      <div className="mt-4 relative h-16">
        <div className="absolute bottom-0 left-0 right-0 h-px bg-border/40" />
        <div className="absolute inset-0 flex items-end gap-px">
          {data.curve.map((secs, i) => {
            const pct = secs > 0 ? Math.max(secs / max, 0.04) : 0
            const at = Math.round((i / data.curve.length) * 100)
            const dur = formatDuration(secs)
            const tip = secs > 0 ? t`~${at}% in · ${dur}` : t`~${at}% in · not read`
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
        <span>{t`start`}</span>
        {data.reread_bins > 0 && (
          <span className="text-muted-foreground/70">{plural(data.reread_bins, { one: '# stretch re-read', other: '# stretches re-read' })}</span>
        )}
        <span>{t`end`}</span>
      </div>
    </section>
  )
}

// ── Time per chapter: page-stat dwell bucketed into the book's TOC boundaries ─────

export function ChapterTimesBlock({ chapters }: { chapters: ChapterTime[] }) {
  const { t } = useLingui()
  const [showDetails, setShowDetails] = useState(false)
  const max = Math.max(...chapters.map(c => c.seconds), 1)
  const total = chapters.reduce((s, c) => s + c.seconds, 0)
  return (
    <section>
      <ChartSectionTitle
        title={<Trans>Time per chapter</Trans>}
        hint={t`Device reading time mapped into the book's chapters. Hover the line for a chapter's time; a flat stretch hasn't been read on a synced device.`}
        foot={(() => { const dur = formatDuration(total); const n = chapters.length; return <span className="tabular-nums">{t`${dur} across ${n} chapters`}</span> })()}
      />
      <ChapterLineChart chapters={chapters} />
      {showDetails && (
        <ul className="mt-3 space-y-1.5">
          {chapters.map(c => (
            <li
              key={c.idx}
              className="flex items-center gap-3 -mx-2 px-2 py-0.5 rounded-md hover:bg-muted/50 transition-colors"
              title={[c.title, chapterReadSpan(c)].filter(Boolean).join(' · ')}
            >
              <span className="w-40 sm:w-56 shrink-0 truncate text-xs text-muted-foreground">
                {c.title}
              </span>
              <span className="flex-1 h-2 rounded bg-muted/60 overflow-hidden">
                <span
                  className="block h-full rounded bg-primary/60"
                  style={{ width: c.seconds > 0 ? `${Math.max(Math.round((c.seconds / max) * 100), 3)}%` : '0%' }}
                />
              </span>
              <span className="w-14 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                {c.seconds <= 0 ? '—' : c.seconds < 60 ? '<1m' : formatDuration(c.seconds)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <button
        onClick={() => setShowDetails(v => !v)}
        className="mt-3 text-[14px] font-medium text-primary hover:underline underline-offset-4"
      >
        {showDetails ? t`Hide details` : t`Show details`}
      </button>
    </section>
  )
}

// Line/area rendering of time-per-chapter: one series, so no legend — the
// block title names it. Hover snaps to the nearest chapter and shows a styled
// tooltip (name + time) with a marker dot; both are HTML overlays positioned
// in percent, because the SVG stretches (preserveAspectRatio="none") and
// would distort anything drawn inside it.
function ChapterLineChart({ chapters }: { chapters: ChapterTime[] }) {
  const { t } = useLingui()
  const W = 600, H = 80, PAD = 2
  const n = chapters.length
  const max = Math.max(...chapters.map(c => c.seconds), 1)
  const [hover, setHover] = useState<number | null>(null)
  const x = (i: number) => n === 1 ? W / 2 : PAD + (i / (n - 1)) * (W - 2 * PAD)
  const y = (s: number) => H - PAD - (s / max) * (H - 2 * PAD)
  const pts = chapters.map((c, i) => `${x(i).toFixed(1)},${y(c.seconds).toFixed(1)}`).join(' ')
  const area = `${PAD},${H - PAD} ${pts} ${(W - PAD).toFixed(1)},${H - PAD}`

  const onMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const frac = (e.clientX - rect.left) / rect.width
    setHover(Math.min(Math.max(Math.round(frac * (n - 1)), 0), n - 1))
  }
  const h = hover !== null ? chapters[hover] : null
  const hx = hover !== null ? (x(hover) / W) * 100 : 0
  const hy = h ? (y(h.seconds) / H) * 100 : 0

  return (
    <div className="mt-3">
      <div className="relative" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-20 block" preserveAspectRatio="none" role="img" aria-label={t`Time per chapter, line view`}>
          <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} className="stroke-border/60" strokeWidth="1" />
          <polygon points={area} className="fill-primary/15" />
          <polyline points={pts} className="stroke-primary/70 fill-none" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        </svg>
        {h && (
          <>
            <span
              className="pointer-events-none absolute w-2 h-2 rounded-full bg-primary ring-2 ring-card -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${hx}%`, top: `${hy}%` }}
            />
            <div
              className="pointer-events-none absolute z-10 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs shadow-md"
              style={{
                left: `${Math.min(Math.max(hx, 10), 90)}%`,
                top: `${Math.max(hy - 8, 0)}%`,
                transform: 'translate(-50%, -100%)',
              }}
            >
              <span className="text-foreground">{h.title}</span>
              <span className="text-muted-foreground tabular-nums">
                {' · '}{h.seconds <= 0 ? t`not read` : h.seconds < 60 ? '<1m' : formatDuration(h.seconds)}
                {chapterReadSpanShort(h) && ` · ${chapterReadSpanShort(h)}`}
              </span>
            </div>
          </>
        )}
      </div>
      <div className="flex justify-between text-xs text-muted-foreground/50 mt-1">
        <span><Trans>ch. 1</Trans></span>
        <span><Trans>ch. {n}</Trans></span>
      </div>
    </div>
  )
}

// ── Hero layout: large time-read headline + activity chart, bottom-pinned date/span stats ─────

// Manual reading-log entry point: log a session by hand (paper / un-synced
// device) and export the log. Used both in the stats hero and on books with no
// reading history yet, so manual trackers always have a way in.
export function ManualLogControls({ bookId, onChange, exportRows }: {
  bookId: number
  onChange: () => void
  exportRows?: { date: string; seconds: number; pages: number; progress_pct: number | null }[]
}) {
  const { t } = useLingui()
  const { toast } = useToast()
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
      await api.post(`/books/${bookId}/sessions?tz_offset=${new Date().getTimezoneOffset()}`, body)
      setMinutes(''); setPct(''); setLogging(false)
      onChange()
    } catch (e) {
      toast.error((e as Error).message ?? t`Failed to log session`)
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
  // eslint-disable-next-line lingui/no-unlocalized-strings -- Tailwind classes
  const chip = 'rounded-md border border-border px-2 py-0.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:border-primary/50 transition-colors'

  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <button
          type="button"
          onClick={() => setLogging(o => !o)}
          className="flex items-center gap-1.5 text-sm font-medium text-foreground hover:text-primary transition-colors"
        >
          <Plus className="w-4 h-4" /> <Trans>Log session</Trans>
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
            <span className="text-xs text-muted-foreground/70"><Trans>Minutes</Trans></span>
            <input
              type="number" min="1" step="1" required autoFocus
              value={minutes} onChange={e => setMinutes(e.target.value)}
              className="w-24 rounded-md border border-border bg-background px-2 py-1.5 text-sm tabular-nums focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground/70"><Trans>Progress % <span className="text-muted-foreground/40">(optional)</span></Trans></span>
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
            {saving ? t`Saving…` : t`Add`}
          </button>
          <button
            type="button" onClick={() => { setLogging(false); setMinutes(''); setPct('') }}
            className="rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <Trans>Cancel</Trans>
          </button>
        </form>
      )}
    </div>
  )
}

function MomentumChip({ momentum }: { momentum: NonNullable<BookReadingStats['momentum']> }) {
  const { t } = useLingui()
  const Icon = momentum.direction === 'up' ? TrendingUp : momentum.direction === 'down' ? TrendingDown : Minus
  const tone =
    // eslint-disable-next-line lingui/no-unlocalized-strings -- Tailwind classes
    momentum.direction === 'up' ? 'text-emerald-600 dark:text-emerald-400'
      : momentum.direction === 'down' ? 'text-muted-foreground'
        : 'text-muted-foreground'
  const deltaStr = momentum.delta_pct != null ? `${momentum.delta_pct > 0 ? '+' : ''}${momentum.delta_pct}` : ''
  const label = momentum.delta_pct != null
    ? t`${deltaStr}% vs last week`
    : t`new this week`
  return (
    <span
      className={cn('flex items-center gap-1 text-xs font-medium', tone)}
      title={(() => { const recent = formatDuration(momentum.recent_seconds); const prior = formatDuration(momentum.prior_seconds); return t`${recent} in the last 7 days vs ${prior} the week before` })()}
    >
      <Icon className="w-3.5 h-3.5" />
      {label}
    </span>
  )
}

/** The Reading section's body: the numbers as a bare strip, the existing
 *  charts as they are, the dates as fields, then the manual log. */
function ReadingBody({ own, aggregate, bookId, onChange }: StatsLayoutProps) {
  const { t } = useLingui()
  const h = Math.floor(own.total_seconds / 3600)
  const m = Math.round((own.total_seconds % 3600) / 60)
  const pace = own.pace_pages_per_min

  const dates: { label: string; value: string }[] = []
  if (own.first_read) dates.push({ label: t`First read`, value: formatDate(own.first_read.slice(0, 10)) })
  if (own.last_read) dates.push({ label: t`Last read`, value: formatDate(own.last_read.slice(0, 10)) })
  if (own.finished_at) dates.push({ label: t({ message: 'Finished', context: 'reading' }), value: formatDate(own.finished_at.slice(0, 10)) })
  if (own.session_timeline.length > 0) dates.push({ label: t`Reading days`, value: String(own.session_timeline.length) })
  if (own.status === 'reading' && own.estimated_finish_seconds != null) {
    dates.push({ label: t`Est. remaining`, value: formatDuration(own.estimated_finish_seconds) })
  }
  // Date at your recent per-day rate on this book; absent when it's paused.
  if (own.status === 'reading' && own.estimated_finish_date) {
    dates.push({ label: t`Est. finish`, value: formatDate(own.estimated_finish_date) })
  }

  return (
    <div className="mt-5 flex flex-col gap-6">
      <StatStrip cols={5} bordered={false} className="gap-y-6">
        <Stat size={30} label={t`Time reading`}
          value={h > 0 ? t`${h}h` : m}
          unit={h > 0 ? t`${m}m` : t`min`}
          foot={own.momentum ? <MomentumChip momentum={own.momentum} /> : undefined} />
        <Stat size={30} label={t`Sessions`} value={own.sessions} />
        <Stat size={30} label={t`Pages`} value={own.pages_turned > 0 ? own.pages_turned : '-'} />
        <Stat size={30} label={t`Avg session`} value={Math.round(own.avg_session_seconds / 60)} unit={t`min`} />
        <Stat size={30} label={t`Pace`} value={pace ?? '-'} unit={pace != null ? t`pg/min` : undefined} />
      </StatStrip>

      {/* The charts, unchanged: activity on a real date axis, progress, where you read. */}
      <div>
        {own.session_timeline.length > 1 && (
          <ActivityChart timeline={own.session_timeline} bookId={bookId} onChange={onChange} />
        )}
        {/* Current progress, only when the journey line doesn't actually render
            (it needs 2+ progress points; a single point used to suppress BOTH,
            leaving real progress displayed nowhere). Web books with a drawn line
            show their % in its header, so this avoids two "Progress" rows. */}
        {own.progress != null && own.progress > 0 &&
          !(own.session_timeline.length > 1 &&
            own.session_timeline.filter(d => d.progress_pct != null).length > 1) && (
          <div className="mt-4">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-xs text-muted-foreground/70"><Trans>Progress</Trans></span>
              <span className="text-xs font-medium tabular-nums text-foreground">{Math.round(own.progress * 100)}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(Math.round(own.progress * 100), 100)}%` }} />
            </div>
          </div>
        )}
        <SourceSplit sources={own.by_source} />
      </div>

      {dates.length > 0 && (
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-6">
          {dates.map(d => <Field key={d.label} label={d.label} value={<span className="tabular-nums">{d.value}</span>} />)}
        </div>
      )}

      {/* Skip when the only reader is you: the line would just repeat the numbers.
          Still shown when someone ELSE read a book you haven't touched. */}
      {aggregate && (aggregate.distinct_readers > 1 || own.total_seconds === 0) && (
        <p className="text-[13px] text-muted-foreground">
          {(() => {
            const dur = formatDuration(aggregate.total_seconds)
            const days = plural(aggregate.total_sessions, { one: '# reading day', other: '# reading days' })
            const readers = plural(aggregate.distinct_readers, { one: '# reader', other: '# readers' })
            return t`All readers: ${dur} · ${days} · ${readers}`
          })()}
        </p>
      )}

      <ManualLogControls bookId={bookId} onChange={onChange} exportRows={own.session_timeline} />
    </div>
  )
}

function EditField({ label, value, onChange, type = 'text', placeholder = '', className }: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
  className?: string
}) {
  return (
    <label className="block">
      <span className="text-[13px] text-muted-foreground">{label}</span>
      <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} className={cn(className, 'mt-1')} />
    </label>
  )
}

/** A section heading with the block's explainer beside it and an optional
 *  line of figures under it, for the reading charts. */
function ChartSectionTitle({ title, hint, foot, action }: { title: ReactNode; hint: string; foot?: ReactNode; action?: ReactNode }) {
  return (
    <div>
      <SectionTitle size={20} action={action}>
        <span className="inline-flex items-center gap-2">
          {title}
          <span className="font-sans text-[14px] font-normal"><InfoHint text={hint} /></span>
        </span>
      </SectionTitle>
      {foot && <div className="mt-2 text-[14px] text-muted-foreground">{foot}</div>}
    </div>
  )
}
