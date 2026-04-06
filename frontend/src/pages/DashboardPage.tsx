import { useEffect, useRef, useState, useCallback, type ReactNode } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import {
  BookOpen, Upload, Search, X, Home, ChevronRight,
  LayoutGrid, List,
  ChevronUp, ChevronDown, SlidersHorizontal, LayoutList, Loader2,
  Library as LibraryIcon, CheckSquare, XSquare, Download, Pencil, Menu,
  Flame, BookCheck, Clock, BookOpenCheck, Play, CheckCheck, Trash2,
} from 'lucide-react'
import { useAuth, isMember, isAdmin } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { BookCard, type ViewMode } from '@/components/BookCard'
import { Sidebar } from '@/components/Sidebar'
import { SaveFilterButton } from '@/components/SaveFilterButton'
import { AutocompleteInput } from '@/components/AutocompleteInput'
import { UploadModal } from '@/components/UploadModal'
import { api } from '@/lib/api'
import type { Book, Library, SavedFilter, ReadingStatus } from '@/lib/books'
import { formatBytes } from '@/lib/books'
import { useBookTypes } from '@/lib/bookTypes'
import { useShiftSelect } from '@/lib/useShiftSelect'
import { cn } from '@/lib/utils'

type SortField = 'title' | 'author' | 'year' | 'added_at'
type SortOrder = 'asc' | 'desc'

interface SeriesItem {
  name: string
  book_count: number
  cover_book_id: number
  description: string | null
  author: string | null
  read_count: number
  reading_count: number
}

interface SeriesDetailBook {
  id: number
  title: string
  series_index: number | null
  cover_path: string | null
  reading_status: 'unread' | 'reading' | 'read'
  progress_pct: number | null
}

interface SeriesDetail {
  name: string
  author: string | null
  description: string | null
  books: SeriesDetailBook[]
}

interface Facets {
  series: string[]
  authors: string[]
  tags: string[]
  formats: string[]
}

interface HomeStats {
  current_streak_days: number
  books_finished_30d: number
  reading_seconds_30d: number
  pages_turned_30d: number
}

interface ActivityEntry {
  book_id: number
  book_title: string
  book_cover_path: string | null
  started_at: string
  duration_seconds: number
  pages_turned: number
}

const SORT_LABELS: Record<SortField, string> = {
  title: 'Title', author: 'Author', year: 'Year', added_at: 'Date Added',
}

const VIEW_KEY = 'tome_view'
const SORT_KEY = 'tome_sort'
const ORDER_KEY = 'tome_order'

// ── Bulk Delete Modal ──────────────────────────────────────────────────────────
interface BulkDeleteModalProps {
  open: boolean
  books: Book[]
  selectedIds: Set<number>
  onCancel: () => void
  onConfirm: (onProgress: (done: number, total: number) => void) => Promise<void>
}

function BulkDeleteModal({ open, books, selectedIds, onCancel, onConfirm }: BulkDeleteModalProps) {
  const [deleting, setDeleting] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)

  if (!open) return null

  const selectedBooks = books.filter(b => selectedIds.has(b.id))

  async function handleDelete() {
    setDeleting(true)
    setProgress({ done: 0, total: selectedIds.size })
    await onConfirm((done, total) => setProgress({ done, total }))
    setDeleting(false)
    setProgress(null)
  }

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/50"
        onClick={() => { if (!deleting) onCancel() }}
      />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="pointer-events-auto w-full max-w-md bg-card border border-border rounded-2xl shadow-2xl flex flex-col max-h-[80vh]">
          {/* Header */}
          <div className="flex items-center justify-between px-6 pt-6 pb-4 shrink-0">
            <div className="flex items-center gap-2">
              <Trash2 className="w-4 h-4 text-destructive" />
              <h2 className="text-base font-semibold text-foreground">
                Delete {selectedIds.size} book{selectedIds.size !== 1 ? 's' : ''}
              </h2>
            </div>
            <button
              onClick={onCancel}
              disabled={deleting}
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Warning */}
          <div className="px-6 pb-3 shrink-0">
            <p className="text-xs text-destructive bg-destructive/10 border border-destructive/20 rounded-lg px-3 py-2">
              This permanently removes the selected books and their files from disk. This cannot be undone.
            </p>
          </div>

          {/* Book list */}
          <div className="overflow-y-auto flex-1 px-6 pb-3">
            <div className="flex flex-col gap-1">
              {selectedBooks.map(book => {
                const primaryFile = book.files[0] ?? null
                return (
                  <div key={book.id} className="flex items-center gap-3 py-2 border-b border-border last:border-0">
                    {book.cover_path ? (
                      <img
                        src={`/api/books/${book.id}/cover`}
                        alt={book.title}
                        className="w-8 h-11 rounded object-cover shrink-0 border border-border"
                        onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                      />
                    ) : (
                      <div className="w-8 h-11 rounded bg-muted border border-border shrink-0 flex items-center justify-center">
                        <BookOpen className="w-3.5 h-3.5 text-muted-foreground" />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-foreground truncate leading-tight">{book.title}</p>
                      {book.author && (
                        <p className="text-xs text-muted-foreground truncate">{book.author}</p>
                      )}
                      {primaryFile && (
                        <p className="text-[10px] text-muted-foreground/70 truncate">
                          {primaryFile.format.toUpperCase()}
                          {primaryFile.file_size ? ` · ${formatBytes(primaryFile.file_size)}` : ''}
                        </p>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-border shrink-0">
            {progress && (
              <span className="text-xs text-muted-foreground mr-auto">
                Deleting {progress.done}/{progress.total}...
              </span>
            )}
            <button
              onClick={onCancel}
              disabled={deleting}
              className="px-3 py-1.5 rounded-lg text-sm border border-border text-muted-foreground hover:bg-muted disabled:opacity-50 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-destructive text-destructive-foreground hover:opacity-90 disabled:opacity-50 transition-all"
            >
              {deleting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              Delete {selectedIds.size} book{selectedIds.size !== 1 ? 's' : ''}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

function formatReadingTime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h === 0) return `${m}m`
  if (m === 0) return `${h}h`
  return `${h}h ${m}m`
}

function relativeTime(isoString: string): string {
  const now = Date.now()
  const then = new Date(isoString).getTime()
  const diff = Math.floor((now - then) / 1000)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 172800) return 'yesterday'
  return `${Math.floor(diff / 86400)}d ago`
}

function StatPill({ icon, value, label }: { icon: ReactNode; value: string; label: string }) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-muted/60 border border-border">
      <span className="text-muted-foreground">{icon}</span>
      <span className="text-sm font-semibold text-foreground">{value}</span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  )
}

export function DashboardPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null)

  // ── View / sort (persisted) ───────────────────────────────────────────────
  const [view, setView] = useState<ViewMode>(() =>
    (localStorage.getItem(VIEW_KEY) as ViewMode | null) ?? 'large')
  const [sort, setSort] = useState<SortField>(() =>
    (localStorage.getItem(SORT_KEY) as SortField | null) ?? 'title')
  const [order, setOrder] = useState<SortOrder>(() =>
    (localStorage.getItem(ORDER_KEY) as SortOrder | null) ?? 'asc')

  function persistView(v: ViewMode) { setView(v); localStorage.setItem(VIEW_KEY, v) }
  function persistSort(s: SortField) { setSort(s); localStorage.setItem(SORT_KEY, s) }
  function persistOrder(o: SortOrder) { setOrder(o); localStorage.setItem(ORDER_KEY, o) }

  // ── Tab (Home / Books / Series) — derived from URL ───────────────────────
  const tab = (searchParams.get('tab') || 'home') as 'home' | 'books' | 'series'
  const [seriesList, setSeriesList] = useState<SeriesItem[]>([])
  const [seriesLoading, setSeriesLoading] = useState(false)
  const [expandedSeries, setExpandedSeries] = useState<string | null>(null)
  const [seriesDetail, setSeriesDetail] = useState<SeriesDetail | null>(null)
  const [seriesDetailLoading, setSeriesDetailLoading] = useState(false)
  const [markingAllRead, setMarkingAllRead] = useState(false)
  const [contentType, setContentType] = useState<string>('volume')

  function setTab(value: 'home' | 'books' | 'series') {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (value === 'home') next.delete('tab')
      else next.set('tab', value)
      return next
    })
  }

  function openSeriesTab() {
    setTab('series')
    setExpandedSeries(null)
    setSeriesDetail(null)
    setSeriesLoading(true)
    api.get<SeriesItem[]>('/books/series')
      .then(setSeriesList)
      .catch(() => {})
      .finally(() => setSeriesLoading(false))
  }

  function openSeriesDetail(seriesName: string) {
    if (expandedSeries === seriesName) {
      setExpandedSeries(null)
      setSeriesDetail(null)
      return
    }
    setExpandedSeries(seriesName)
    setSeriesDetailLoading(true)
    setSeriesDetail(null)
    api.get<SeriesDetail>(`/books/series-detail?name=${encodeURIComponent(seriesName)}`)
      .then(detail => { setSeriesDetail(detail) })
      .catch(() => {})
      .finally(() => setSeriesDetailLoading(false))
  }

  // Auto-open series detail when navigating via ?tab=series&series_detail=Name
  useEffect(() => {
    if (tab !== 'series') return
    const detailName = searchParams.get('series_detail')
    if (!detailName || detailName === expandedSeries) return
    // Load series list first, then open the detail
    setSeriesLoading(true)
    api.get<SeriesItem[]>('/books/series')
      .then(list => {
        setSeriesList(list)
        setSeriesLoading(false)
        openSeriesDetail(detailName)
      })
      .catch(() => setSeriesLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, searchParams])

  function getContinueBook(books: SeriesDetailBook[]): SeriesDetailBook | null {
    if (books.length === 0) return null
    const reading = books.find(b => b.reading_status === 'reading')
    if (reading) return reading
    // Find first unread where previous volume (by index order) is read
    for (let i = 1; i < books.length; i++) {
      if (books[i].reading_status === 'unread' && books[i - 1].reading_status === 'read') {
        return books[i]
      }
    }
    return books.find(b => b.reading_status === 'unread') ?? null
  }

  async function markAllRead(books: SeriesDetailBook[]) {
    if (markingAllRead) return
    setMarkingAllRead(true)
    try {
      const unread = books.filter(b => b.reading_status !== 'read')
      await Promise.all(unread.map(b => api.put(`/books/${b.id}/status`, { status: 'read' })))
      // Refresh detail
      if (expandedSeries) {
        const detail = await api.get<SeriesDetail>(`/books/series-detail?name=${encodeURIComponent(expandedSeries)}`)
        setSeriesDetail(detail)
      }
    } catch {
      // silent
    } finally {
      setMarkingAllRead(false)
    }
  }

  function toggleSort(field: SortField) {
    if (sort === field) persistOrder(order === 'asc' ? 'desc' : 'asc')
    else { persistSort(field); persistOrder('asc') }
  }

  // ── URL-based filters ─────────────────────────────────────────────────────
  const search = searchParams.get('q') ?? ''
  const filterSeries = searchParams.get('series') ?? ''
  const filterNoSeries = searchParams.get('no_series') === 'true'
  const filterAuthor = searchParams.get('author') ?? ''
  const filterTag = searchParams.get('tag') ?? ''
  const filterFormat = searchParams.get('format') ?? ''
  const filterLibrary = searchParams.get('library_id') ? Number(searchParams.get('library_id')) : null
  const filterReadingStatus = searchParams.get('reading_status') ?? ''
  const filterMissing = searchParams.get('missing') ?? ''
  const filterOwnership = searchParams.get('ownership') ?? ''
  const filterAddedBy = searchParams.get('added_by') ? Number(searchParams.get('added_by')) : null

  // ── User list for admin uploader filter ──────────────────────────────────
  interface SimpleUser { id: number; username: string; role: string }
  const [userList, setUserList] = useState<SimpleUser[]>([])
  useEffect(() => {
    if (isAdmin(user)) {
      api.get<SimpleUser[]>('/users/list').then(setUserList).catch(() => {})
    }
  }, [user])

  function setFilter(key: string, value: string, replace = false) {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      // When changing a content filter, drop saved_filter reference
      if (key !== 'library_id') next.delete('saved_filter')
      if (value) next.set(key, value)
      else next.delete(key)
      return next
    }, { replace })
  }

  function clearFilters() {
    setSearchParams(prev => {
      const next = new URLSearchParams()
      const t = prev.get('tab')
      if (t) next.set('tab', t)
      return next
    })
  }

  const hasFilters = !!(search || filterSeries || filterNoSeries || filterAuthor || filterTag || filterFormat || filterReadingStatus || filterMissing || filterOwnership || filterAddedBy)

  // ── Debounced search ──────────────────────────────────────────────────────
  const [searchInput, setSearchInput] = useState(search)
  const searchDebounce = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  function handleSearchInput(val: string) {
    setSearchInput(val)
    clearTimeout(searchDebounce.current)
    searchDebounce.current = setTimeout(() => {
      setSearchParams(prev => {
        const next = new URLSearchParams(prev)
        next.delete('saved_filter')
        if (val) {
          next.set('q', val)
          next.set('tab', 'books')
        } else {
          next.delete('q')
        }
        return next
      }, { replace: true })
    }, 300)
  }
  useEffect(() => { setSearchInput(search) }, [search])

  // ── Multi-select + bulk actions ──────────────────────────────────────────
  const [selectionMode, setSelectionMode] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkLibMenu, setBulkLibMenu] = useState(false)
  const [bulkPending, setBulkPending] = useState(false)
  const [deleteModalOpen, setDeleteModalOpen] = useState(false)
  const [bulkMetaOpen, setBulkMetaOpen] = useState(false)
  const [bulkMetaAuthor, setBulkMetaAuthor] = useState('')
  const [bulkMetaSeries, setBulkMetaSeries] = useState('')
  const [bulkMetaTagsAdd, setBulkMetaTagsAdd] = useState<string[]>([])
  const [bulkMetaTagInput, setBulkMetaTagInput] = useState('')
  const [bulkMetaTypeId, setBulkMetaTypeId] = useState<number | ''>('')
  const [bulkMetaSaving, setBulkMetaSaving] = useState(false)
  const bookTypes = useBookTypes()

  function toggleSelect(id: number, shiftKey: boolean) {
    setSelected(prev => {
      const index = books.findIndex(b => b.id === id)
      return handleToggle(id, index, shiftKey, prev)
    })
  }
  function selectAll() { setSelected(new Set(books.map(b => b.id))) }
  function clearSelection() { setSelected(new Set()) }
  function exitSelectionMode() { setSelectionMode(false); setSelected(new Set()) }

  async function bulkDownload() {
    if (!selected.size) return
    setBulkPending(true)
    try {
      const token = localStorage.getItem('tome_token')
      const resp = await fetch('/api/downloads', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ book_ids: [...selected] }),
      })
      if (!resp.ok) throw new Error('Download failed')
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'tome-books.zip'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      toastError(e instanceof Error ? e.message : 'Download failed')
    } finally {
      setBulkPending(false)
    }
  }

  async function bulkAddToLibrary(libId: number) {
    if (!selected.size) return
    setBulkPending(true)
    try {
      await api.post(`/libraries/${libId}/books`, { book_ids: [...selected] })
      toastSuccess(`Added ${selected.size} book${selected.size !== 1 ? 's' : ''} to library`)
      clearSelection()
      setBulkLibMenu(false)
      loadLibraries()
    } catch (e) {
      toastError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setBulkPending(false)
    }
  }

  async function bulkDelete(onProgress?: (done: number, total: number) => void) {
    if (!selected.size) return
    setBulkPending(true)
    let deleted = 0
    const ids = [...selected]
    for (let i = 0; i < ids.length; i++) {
      try {
        await api.delete(`/books/${ids[i]}`)
        deleted++
      } catch {
        // continue with rest
      }
      onProgress?.(i + 1, ids.length)
    }
    toastSuccess(`Deleted ${deleted} book${deleted !== 1 ? 's' : ''}`)
    setDeleteModalOpen(false)
    clearSelection()
    loadBooks()
    setBulkPending(false)
  }

  async function bulkSaveMetadata() {
    if (!selected.size) return
    setBulkMetaSaving(true)
    try {
      const body: Record<string, unknown> = { book_ids: [...selected] }
      if (bulkMetaAuthor) body.author = bulkMetaAuthor
      if (bulkMetaSeries) body.series = bulkMetaSeries
      if (bulkMetaTagsAdd.length) body.tags_add = bulkMetaTagsAdd
      if (bulkMetaTypeId) body.book_type_id = bulkMetaTypeId
      await api.put('/books/bulk-metadata', body)
      toastSuccess(`Updated metadata for ${selected.size} book${selected.size !== 1 ? 's' : ''}`)
      setBulkMetaOpen(false)
      setBulkMetaAuthor(''); setBulkMetaSeries(''); setBulkMetaTagsAdd([]); setBulkMetaTypeId('')
      clearSelection()
      loadBooks(); loadFacets()
    } catch (e) {
      toastError(e instanceof Error ? e.message : 'Failed to update metadata')
    } finally {
      setBulkMetaSaving(false)
    }
  }

  // ── Sidebar data ──────────────────────────────────────────────────────────
  const [libraries, setLibraries] = useState<Library[]>([])
  const [savedFilters, setSavedFilters] = useState<SavedFilter[]>([])
  function loadLibraries() { api.get<Library[]>('/libraries').then(setLibraries).catch(() => {}) }
  function loadSavedFilters() { api.get<SavedFilter[]>('/saved-filters').then(setSavedFilters).catch(() => {}) }
  useEffect(() => { loadLibraries(); loadSavedFilters() }, [])

  // ── Books + facets ────────────────────────────────────────────────────────
  const [books, setBooks] = useState<Book[]>([])
  const { handleToggle } = useShiftSelect(books.map(b => b.id))
  const [totalCount, setTotalCount] = useState<number | null>(null)
  const [readingStatuses, setReadingStatuses] = useState<Record<number, { status: ReadingStatus; progress_pct: number | null }>>({})
  const [facets, setFacets] = useState<Facets>({ series: [], authors: [], tags: [], formats: [] })
  const [loading, setLoading] = useState(true)
  const [uploadModalOpen, setUploadModalOpen] = useState(false)
  const [filterOpen, setFilterOpen] = useState(false)

  const { toast } = useToast()
  const toastSuccess = toast.success
  const toastError = toast.error
  const searchInputRef = useRef<HTMLInputElement>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const booksRef = useRef<Book[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const [hasMore, setHasMore] = useState(true)
  const hasMoreRef = useRef(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [continueReading, setContinueReading] = useState<Book[]>([])
  const [homeStats, setHomeStats] = useState<HomeStats | null>(null)
  const [recentlyFinished, setRecentlyFinished] = useState<Book[]>([])
  const [recentlyAdded, setRecentlyAdded] = useState<Book[]>([])
  const [activityLog, setActivityLog] = useState<ActivityEntry[]>([])

  const PAGE_SIZE = 60

  const loadBooks = useCallback((reset = true) => {
    const skip = reset ? 0 : booksRef.current.length

    if (reset) {
      abortRef.current?.abort()
      abortRef.current = new AbortController()
      if (booksRef.current.length === 0) setLoading(true)
      else setRefreshing(true)
      hasMoreRef.current = true
      setHasMore(true)
    } else {
      if (!hasMoreRef.current) return
      setLoadingMore(true)
    }

    const signal = reset ? abortRef.current!.signal : undefined
    const params = new URLSearchParams()
    params.set('sort', sort)
    params.set('order', order)
    params.set('skip', String(skip))
    params.set('limit', String(PAGE_SIZE))
    if (search) params.set('q', search)
    if (filterSeries) params.set('series', filterSeries)
    if (filterNoSeries) params.set('no_series', 'true')
    if (filterAuthor) params.set('author', filterAuthor)
    if (filterTag) params.set('tag', filterTag)
    if (filterFormat) params.set('format', filterFormat)
    if (filterLibrary) params.set('library_id', String(filterLibrary))
    if (filterReadingStatus) params.set('reading_status', filterReadingStatus)
    if (filterMissing) params.set('missing', filterMissing)
    if (contentType) params.set('content_type', contentType)
    if (filterOwnership) params.set('ownership', filterOwnership)
    if (filterAddedBy) params.set('added_by', String(filterAddedBy))
    api.getWithHeaders<Book[]>(`/books?${params}`, signal)
      .then(({ data: newBooks, headers }) => {
        if (signal?.aborted) return
        if (reset) {
          const raw = headers.get('x-total-count')
          setTotalCount(raw !== null ? Number(raw) : null)
        }
        const merged = reset ? newBooks : [...booksRef.current, ...newBooks]
        booksRef.current = merged
        setBooks(merged)
        hasMoreRef.current = newBooks.length === PAGE_SIZE
        setHasMore(newBooks.length === PAGE_SIZE)
        if (newBooks.length > 0) {
          api.post<Record<string, { status: string; progress_pct: number | null }>>('/books/statuses', { book_ids: newBooks.map(b => b.id) })
            .then(map => {
              const s: Record<number, { status: ReadingStatus; progress_pct: number | null }> = {}
              Object.entries(map).forEach(([id, val]) => { s[Number(id)] = { status: val.status as ReadingStatus, progress_pct: val.progress_pct } })
              setReadingStatuses(prev => ({ ...prev, ...s }))
            })
            .catch(() => {})
        } else if (reset) {
          setReadingStatuses({})
        }
      })
      .catch(err => {
        if (err instanceof Error && err.name === 'AbortError') return
        toastError('Failed to load books')
      })
      .finally(() => {
        if (signal?.aborted) return
        if (reset) { setLoading(false); setRefreshing(false) }
        else setLoadingMore(false)
      })
  }, [sort, order, search, filterSeries, filterNoSeries, filterAuthor, filterTag, filterFormat, filterLibrary, filterReadingStatus, filterMissing, contentType, filterOwnership, filterAddedBy])

  useEffect(() => { loadBooks() }, [loadBooks])

  // Reset focused index when book list changes
  useEffect(() => { setFocusedIndex(null) }, [books])

  const loadingMoreRef = useRef(false)
  useEffect(() => { loadingMoreRef.current = loadingMore }, [loadingMore])
  const loadingRef = useRef(false)
  useEffect(() => { loadingRef.current = loading }, [loading])
  const refreshingRef = useRef(false)
  useEffect(() => { refreshingRef.current = refreshing }, [refreshing])

  useEffect(() => {
    if (!sentinelRef.current) return
    const observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting && !loadingMoreRef.current && !loadingRef.current && !refreshingRef.current && hasMoreRef.current) {
        loadBooks(false)
      }
    }, { rootMargin: '200px' })
    observer.observe(sentinelRef.current)
    return () => observer.disconnect()
  }, [loadBooks])

  function loadFacets() { api.get<Facets>('/books/facets').then(setFacets).catch(() => {}) }
  useEffect(() => { loadFacets() }, [])


  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return

      if (e.key === '/') {
        e.preventDefault()
        searchInputRef.current?.focus()
        return
      }

      if (e.key === 'j' || e.key === 'ArrowDown') {
        e.preventDefault()
        setFocusedIndex(prev => {
          if (booksRef.current.length === 0) return null
          if (prev === null) return 0
          return (prev + 1) % booksRef.current.length
        })
        return
      }

      if (e.key === 'k' || e.key === 'ArrowUp') {
        e.preventDefault()
        setFocusedIndex(prev => {
          if (booksRef.current.length === 0) return null
          if (prev === null) return booksRef.current.length - 1
          return (prev - 1 + booksRef.current.length) % booksRef.current.length
        })
        return
      }

      if (e.key === 'Enter') {
        setFocusedIndex(prev => {
          if (prev !== null && booksRef.current[prev]) {
            navigate(`/books/${booksRef.current[prev].id}`)
          }
          return prev
        })
        return
      }

      if (e.key === 'Escape') {
        setFocusedIndex(prev => {
          if (prev !== null) return null
          searchInputRef.current?.blur()
          return null
        })
        return
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [navigate])

  useEffect(() => {
    api.get<Book[]>('/books?reading_status=reading&sort=added_at&order=desc&limit=20')
      .then(books => {
        setContinueReading(books)
        if (books.length > 0) {
          api.post<Record<string, { status: string; progress_pct: number | null }>>('/books/statuses', { book_ids: books.map(b => b.id) })
            .then(map => {
              const s: Record<number, { status: ReadingStatus; progress_pct: number | null }> = {}
              Object.entries(map).forEach(([id, val]) => { s[Number(id)] = { status: val.status as ReadingStatus, progress_pct: val.progress_pct } })
              setReadingStatuses(prev => ({ ...prev, ...s }))
            })
            .catch(() => {})
        }
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    api.get<HomeStats>('/home/stats').then(setHomeStats).catch(() => {})
    api.get<Book[]>('/books?reading_status=read&sort=status_updated&order=desc&limit=6').then(setRecentlyFinished).catch(() => {})
    api.get<Book[]>('/books?sort=added_at&order=desc&limit=6').then(setRecentlyAdded).catch(() => {})
    api.get<ActivityEntry[]>('/home/activity').then(setActivityLog).catch(() => {})
    api.get<SeriesItem[]>('/books/series').then(setSeriesList).catch(() => {})
  }, [])

  // ── Filter chip helpers ───────────────────────────────────────────────────
  const activeFilterChips: { label: string; key: string }[] = [
    ...(filterLibrary ? [{ label: `Library: ${libraries.find(l => l.id === filterLibrary)?.name ?? 'Library'}`, key: 'library_id' }] : []),
    ...(filterSeries ? [{ label: `Series: ${filterSeries}`, key: 'series' }] : []),
    ...(filterNoSeries ? [{ label: 'No Series', key: 'no_series' }] : []),
    ...(filterAuthor ? [{ label: `Author: ${filterAuthor}`, key: 'author' }] : []),
    ...(filterTag ? [{ label: `Tag: ${filterTag}`, key: 'tag' }] : []),
    ...(filterFormat ? [{ label: `Format: ${filterFormat.toUpperCase()}`, key: 'format' }] : []),
    ...(filterReadingStatus ? [{ label: `Status: ${filterReadingStatus.charAt(0).toUpperCase() + filterReadingStatus.slice(1)}`, key: 'reading_status' }] : []),
    ...(filterMissing ? [{ label: `Missing: ${filterMissing.charAt(0).toUpperCase() + filterMissing.slice(1)}`, key: 'missing' }] : []),
    ...(filterOwnership === 'mine' ? [{ label: 'My Books', key: 'ownership' }] : filterOwnership === 'shared' ? [{ label: 'Shared Library', key: 'ownership' }] : []),
    ...(filterAddedBy ? [{ label: `Uploader: ${userList.find(u => u.id === filterAddedBy)?.username ?? filterAddedBy}`, key: 'added_by' }] : []),
  ]

  // Params to pass to SaveFilterButton (excludes library_id — that belongs in sidebar)
  const saveableParams: Record<string, string> = {}
  if (search) saveableParams.q = search
  if (filterSeries) saveableParams.series = filterSeries
  if (filterAuthor) saveableParams.author = filterAuthor
  if (filterTag) saveableParams.tag = filterTag
  if (filterFormat) saveableParams.format = filterFormat

  const gridClass = view === 'large'
    ? 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4'
    : view === 'small'
    ? 'grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10 gap-2'
    : 'flex flex-col gap-2'

  // Active library name for heading
  const activeLibraryName = filterLibrary ? libraries.find(l => l.id === filterLibrary)?.name : null

  return (
    <div className="h-screen bg-background flex flex-col overflow-hidden">
      {/* ── Navbar ──────────────────────────────────────────────────────── */}
      <header className="border-b border-border bg-card/50 backdrop-blur-sm sticky top-0 z-20 shrink-0 safe-top">
        <div className="px-4 h-14 flex items-center gap-3">
          <button
            className="md:hidden flex items-center justify-center w-8 h-8 text-muted-foreground hover:text-foreground transition-colors rounded-lg hover:bg-muted shrink-0"
            onClick={() => setMobileSidebarOpen(true)}
            aria-label="Open navigation"
          >
            <Menu className="w-5 h-5" />
          </button>
          <div className="flex items-center gap-2 mr-2 shrink-0">
            <BookOpen className="w-5 h-5 text-primary" />
            <span className="font-semibold text-sm">Tome</span>
          </div>
          <div className="relative flex-1 sm:max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
            <input
              ref={searchInputRef}
              className="w-full h-8 pl-9 pr-8 rounded-lg bg-muted border border-border text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder="Search books… (/)"
              value={searchInput}
              onChange={e => handleSearchInput(e.target.value)}
            />
            {searchInput && (
              <button className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                aria-label="Clear search"
                onClick={() => { setSearchInput(''); setFilter('q', '') }}>
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
          <div className="flex items-center gap-1.5 ml-auto">
            {isMember(user) && (
              <button onClick={() => setUploadModalOpen(true)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card hover:bg-muted transition-all touch-feedback">
                <Upload className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Upload</span>
              </button>
            )}
          </div>
        </div>
      </header>

      <UploadModal
        isOpen={uploadModalOpen}
        onClose={() => setUploadModalOpen(false)}
        onDone={() => { loadBooks(); loadFacets() }}
      />

      {/* ── Body (sidebar + main) ────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">
        <Sidebar
          libraries={libraries}
          savedFilters={savedFilters}
          activeTab={tab}
          onLibrariesChange={loadLibraries}
          onSavedFiltersChange={loadSavedFilters}
          onOpenSeriesView={openSeriesTab}
          onOpenHomeView={() => setTab('home')}
          mobileOpen={mobileSidebarOpen}
          onMobileClose={() => setMobileSidebarOpen(false)}
        />

        <main className="flex-1 overflow-y-auto px-4 py-4 min-w-0">
          {/* Section heading */}
          {activeLibraryName && (
            <h2 className="text-lg font-semibold mb-3">{activeLibraryName}</h2>
          )}

          {tab === 'home' ? (
            /* ── Home tab ────────────────────────────────────────────────── */
            <div className="flex flex-col gap-8">

              {/* ── Quick Stats Strip ─────────────────────────────────────── */}
              {homeStats && (
                <div className="flex flex-wrap gap-2">
                  <StatPill
                    icon={<Flame className="w-3.5 h-3.5" />}
                    value={String(homeStats.current_streak_days)}
                    label={homeStats.current_streak_days === 1 ? 'day streak' : 'day streak'}
                  />
                  <StatPill
                    icon={<BookCheck className="w-3.5 h-3.5" />}
                    value={String(homeStats.books_finished_30d)}
                    label="finished (30d)"
                  />
                  <StatPill
                    icon={<Clock className="w-3.5 h-3.5" />}
                    value={formatReadingTime(homeStats.reading_seconds_30d)}
                    label="read (30d)"
                  />
                  <StatPill
                    icon={<BookOpenCheck className="w-3.5 h-3.5" />}
                    value={String(homeStats.pages_turned_30d)}
                    label="pages (30d)"
                  />
                </div>
              )}

              {/* ── Continue Reading ──────────────────────────────────────── */}
              <div className="flex flex-col gap-3">
                <h2 className="text-sm font-semibold text-foreground">Continue Reading</h2>
                {continueReading.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-24 gap-3 text-muted-foreground">
                    <BookOpen className="w-12 h-12 opacity-20" />
                    <p className="text-sm">No books in progress. Start reading something!</p>
                    <button onClick={() => setTab('books')} className="text-xs text-primary hover:underline">
                      Browse All Books
                    </button>
                  </div>
                ) : (
                  <div className={gridClass}>
                    {continueReading.map((book, i) => {
                      const status = readingStatuses[book.id]
                      return (
                        <BookCard
                          key={`${view}-${book.id}`}
                          book={book}
                          view={view}
                          index={i}
                          selected={false}
                          onTagClick={tag => setSearchParams(prev => { const p = new URLSearchParams(prev); p.set('tab', 'books'); p.set('tag', tag); p.delete('saved_filter'); return p })}
                          onSeriesClick={series => setSearchParams(prev => { const p = new URLSearchParams(prev); p.set('tab', 'books'); p.set('series', series); p.delete('saved_filter'); return p })}
                          onAuthorClick={author => setSearchParams(prev => { const p = new URLSearchParams(prev); p.set('tab', 'books'); p.set('author', author); p.delete('saved_filter'); return p })}
                          readingStatus={status?.status}
                          progressPct={status?.progress_pct}
                        />
                      )
                    })}
                  </div>
                )}
              </div>

              {/* ── Series Progress ───────────────────────────────────────── */}
              {(() => {
                const seriesBooks = continueReading.filter(b => b.series && b.series_index != null)
                if (seriesBooks.length === 0) return null
                return (
                  <div className="flex flex-col gap-3">
                    <h2 className="text-sm font-semibold text-foreground">Series Progress</h2>
                    <div className="flex flex-col gap-2">
                      {seriesBooks.map(book => {
                        const seriesData = seriesList.find(s => s.name === book.series)
                        const total = seriesData?.book_count ?? null
                        const current = book.series_index!
                        const pct = total ? Math.min(100, (current / total) * 100) : null
                        return (
                          <div key={book.id} className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-muted/50 border border-border">
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between gap-2 mb-1.5">
                                <span className="text-xs font-medium text-foreground truncate">{book.series}</span>
                                <span className="text-xs text-muted-foreground shrink-0">
                                  {total ? `Book ${current} of ${total}` : `Book ${current}`}
                                </span>
                              </div>
                              {pct !== null && (
                                <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                                  <div
                                    className="h-full rounded-full bg-primary transition-all"
                                    style={{ width: `${pct}%` }}
                                  />
                                </div>
                              )}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )
              })()}

              {/* ── Recently Finished ─────────────────────────────────────── */}
              {recentlyFinished.length > 0 && (
                <div className="flex flex-col gap-3">
                  <h2 className="text-sm font-semibold text-foreground">Recently Finished</h2>
                  <div className="flex gap-3 overflow-x-auto pb-1 -mx-1 px-1">
                    {recentlyFinished.map(book => (
                      <div key={book.id} className="shrink-0 w-24">
                        <BookCard
                          book={book}
                          view="small"
                          index={0}
                          selected={false}
                          readingStatus="read"
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* ── Recently Added ────────────────────────────────────────── */}
              {recentlyAdded.length > 0 && (
                <div className="flex flex-col gap-3">
                  <h2 className="text-sm font-semibold text-foreground">Recently Added</h2>
                  <div className="flex gap-3 overflow-x-auto pb-1 -mx-1 px-1">
                    {recentlyAdded.map(book => (
                      <div key={book.id} className="shrink-0 w-24">
                        <BookCard
                          book={book}
                          view="small"
                          index={0}
                          selected={false}
                          readingStatus={readingStatuses[book.id]?.status}
                          progressPct={readingStatuses[book.id]?.progress_pct}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* ── Reading Log ───────────────────────────────────────────── */}
              {activityLog.length > 0 && (
                <div className="flex flex-col gap-3">
                  <h2 className="text-sm font-semibold text-foreground">Reading Log</h2>
                  <div className="flex flex-col gap-1">
                    {activityLog.map((entry, i) => (
                      <div key={i} className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-muted/50 transition-colors">
                        {entry.book_cover_path ? (
                          <img
                            src={`/api/books/${entry.book_id}/cover`}
                            alt={entry.book_title}
                            className="w-8 h-11 rounded object-cover shrink-0 border border-border"
                            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                          />
                        ) : (
                          <div className="w-8 h-11 rounded bg-muted border border-border shrink-0 flex items-center justify-center">
                            <BookOpen className="w-3.5 h-3.5 text-muted-foreground" />
                          </div>
                        )}
                        <div className="min-w-0 flex-1">
                          <p className="text-xs font-medium text-foreground truncate">{entry.book_title}</p>
                          <p className="text-[10px] text-muted-foreground">{formatReadingTime(entry.duration_seconds)}</p>
                        </div>
                        <span className="text-[10px] text-muted-foreground shrink-0">{relativeTime(entry.started_at)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

            </div>
          ) : tab === 'series' ? (
            /* ── Series grid ─────────────────────────────────────────────── */
            <>
            <div className="flex items-center gap-1 mb-5 text-sm text-muted-foreground">
              <button
                onClick={() => setTab('books')}
                className="flex items-center gap-1 hover:text-foreground transition-colors shrink-0"
              >
                <Home className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Library</span>
              </button>
              <ChevronRight className="w-3.5 h-3.5 opacity-30 shrink-0" />
              {expandedSeries ? (
                <>
                  <button
                    onClick={() => { setExpandedSeries(null); setSeriesDetail(null) }}
                    className="hover:text-foreground transition-colors"
                  >
                    Series
                  </button>
                  <ChevronRight className="w-3.5 h-3.5 opacity-30 shrink-0" />
                  <span className="font-medium text-foreground truncate max-w-[200px] sm:max-w-[300px]">{expandedSeries}</span>
                </>
              ) : (
                <span className="font-medium text-foreground">Series</span>
              )}
            </div>
            {seriesLoading ? (
              <div className="flex justify-center py-24">
                <Loader2 className="w-6 h-6 animate-spin text-primary" />
              </div>
            ) : seriesList.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-24 gap-3 text-muted-foreground">
                <BookOpen className="w-12 h-12 opacity-20" />
                <p className="text-sm">No series found — add series metadata to your books.</p>
              </div>
            ) : (
              <div className="flex flex-col gap-4">
                {/* Series detail panel — replaces grid when a series is selected */}
                {expandedSeries ? (
                  <div className="rounded-xl border border-border bg-card overflow-hidden">
                    {seriesDetailLoading || !seriesDetail ? (
                      <div className="flex justify-center py-12">
                        <Loader2 className="w-5 h-5 animate-spin text-primary" />
                      </div>
                    ) : (
                      <div className="p-5 flex flex-col gap-5">
                        {/* Header */}
                        <div className="flex flex-col sm:flex-row sm:items-start gap-4">
                          <div className="flex-1 min-w-0">
                            <h2 className="text-lg font-bold text-foreground leading-tight">{seriesDetail.name}</h2>
                            {seriesDetail.author && (
                              <p className="text-sm text-muted-foreground mt-0.5">{seriesDetail.author}</p>
                            )}
                            {(() => {
                              const total = seriesDetail.books.length
                              const readCount = seriesDetail.books.filter(b => b.reading_status === 'read').length
                              const readingCount = seriesDetail.books.filter(b => b.reading_status === 'reading').length
                              const readPct = total ? (readCount / total) * 100 : 0
                              const readingPct = total ? (readingCount / total) * 100 : 0
                              return (
                                <div className="mt-3">
                                  <div className="flex items-center justify-between text-[10px] text-muted-foreground mb-1">
                                    <span>{readCount} read &middot; {readingCount} reading &middot; {total - readCount - readingCount} unread</span>
                                    <span>{total} volumes</span>
                                  </div>
                                  <div className="h-2 rounded-full bg-muted overflow-hidden flex">
                                    <div className="h-full bg-green-500 transition-all" style={{ width: `${readPct}%` }} />
                                    <div className="h-full bg-blue-500 transition-all" style={{ width: `${readingPct}%` }} />
                                  </div>
                                </div>
                              )
                            })()}
                            {seriesDetail.description && (
                              <p className="text-xs text-muted-foreground leading-relaxed mt-3 line-clamp-3">{seriesDetail.description}</p>
                            )}
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            {(() => {
                              const continueBook = getContinueBook(seriesDetail.books)
                              if (!continueBook) return null
                              const volLabel = continueBook.series_index != null ? `Vol. ${continueBook.series_index}` : continueBook.title
                              const isResuming = continueBook.reading_status === 'reading'
                              return (
                                <button
                                  onClick={() => navigate(`/reader/${continueBook.id}`)}
                                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition-opacity"
                                >
                                  <Play className="w-3.5 h-3.5" />
                                  {isResuming ? `Resume ${volLabel}` : `Start ${volLabel}`}
                                </button>
                              )
                            })()}
                            <button
                              onClick={() => markAllRead(seriesDetail.books)}
                              disabled={markingAllRead || seriesDetail.books.every(b => b.reading_status === 'read')}
                              className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-card text-sm font-medium text-foreground hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                            >
                              {markingAllRead ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCheck className="w-3.5 h-3.5" />}
                              Mark all read
                            </button>
                          </div>
                        </div>

                        {/* Volume grid */}
                        <div className="grid grid-cols-5 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10 gap-2">
                          {seriesDetail.books.map(vol => (
                            <div
                              key={vol.id}
                              onClick={() => navigate(`/books/${vol.id}`)}
                              title={vol.title}
                              className="group relative flex flex-col rounded-lg overflow-hidden border bg-muted transition-all duration-150 hover:shadow-md hover:scale-105 cursor-pointer"
                              style={{
                                borderColor: vol.reading_status === 'read'
                                  ? 'rgb(34 197 94 / 0.6)'
                                  : vol.reading_status === 'reading'
                                  ? 'rgb(59 130 246 / 0.6)'
                                  : undefined,
                              }}
                            >
                              <div className="relative aspect-[2/3] w-full overflow-hidden">
                                {vol.cover_path ? (
                                  <img
                                    src={`/api/books/${vol.id}/cover`}
                                    alt={vol.title}
                                    className="w-full h-full object-cover"
                                    onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                                  />
                                ) : (
                                  <div className="w-full h-full flex items-center justify-center bg-muted">
                                    <BookOpen className="w-4 h-4 text-muted-foreground" />
                                  </div>
                                )}
                                {/* Quick-read play button — top-left corner on hover */}
                                <button
                                  onClick={e => { e.stopPropagation(); navigate(`/reader/${vol.id}`) }}
                                  className="absolute top-1 left-1 opacity-0 group-hover:opacity-100 transition-opacity z-10"
                                  title="Read"
                                  aria-label="Read"
                                >
                                  <div className="w-5 h-5 rounded-full bg-white/90 flex items-center justify-center shadow">
                                    <Play className="w-2.5 h-2.5 text-black fill-black ml-px" />
                                  </div>
                                </button>
                                {/* Status dot */}
                                {vol.reading_status !== 'unread' && (
                                  <div className={cn(
                                    'absolute top-1 right-1 w-2 h-2 rounded-full ring-1 ring-background',
                                    vol.reading_status === 'read' ? 'bg-green-500' : 'bg-blue-500'
                                  )} />
                                )}
                                {/* Volume number overlay */}
                                {vol.series_index != null && (
                                  <div className="absolute bottom-0 inset-x-0 bg-black/60 px-1 py-0.5">
                                    <span className="text-[9px] font-bold text-white leading-none">
                                      Vol. {vol.series_index}
                                    </span>
                                  </div>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  /* Series card grid */
                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
                    {seriesList.map(s => {
                      const isUnserialized = s.name === '__unserialized__'
                      return (
                        <button
                          key={s.name}
                          onClick={() => {
                            if (isUnserialized) {
                              setSearchParams(prev => {
                                const p = new URLSearchParams(prev)
                                p.set('tab', 'books')
                                p.delete('saved_filter')
                                p.set('no_series', 'true')
                                return p
                              })
                            } else {
                              openSeriesDetail(s.name)
                            }
                          }}
                          className="group flex flex-col text-left rounded-xl overflow-hidden border border-border bg-card hover:border-primary/40 hover:shadow-md transition-all duration-150"
                        >
                          <div className="relative aspect-[2/3] bg-muted overflow-hidden">
                            {s.cover_book_id && (
                              <img
                                src={`/api/books/${s.cover_book_id}/cover`}
                                alt={isUnserialized ? 'Unserialized' : s.name}
                                className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                                onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                              />
                            )}
                            <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/70 to-transparent px-2 pt-6 pb-2">
                              <span className="text-[10px] font-semibold text-white/90">
                                {s.book_count} {s.book_count === 1 ? 'book' : 'books'}
                              </span>
                            </div>
                          </div>
                          <div className="px-3 py-2.5 flex flex-col gap-0.5 min-w-0">
                            <span className="text-xs font-semibold text-foreground leading-tight line-clamp-2">
                              {isUnserialized ? 'No Series' : s.name}
                            </span>
                            {!isUnserialized && s.author && <span className="text-[10px] text-muted-foreground truncate">{s.author}</span>}
                            {!isUnserialized && s.description && (
                              <p className="text-[10px] text-muted-foreground leading-snug line-clamp-2 mt-0.5">{s.description}</p>
                            )}
                            {isUnserialized && (
                              <p className="text-[10px] text-muted-foreground leading-snug mt-0.5">Books without a series</p>
                            )}
                            {!isUnserialized && s.book_count > 0 && (s.read_count > 0 || s.reading_count > 0) && (
                              <div className="mt-1.5 h-1 rounded-full bg-muted overflow-hidden flex">
                                <div
                                  className="h-full bg-green-500"
                                  style={{ width: `${(s.read_count / s.book_count) * 100}%` }}
                                />
                                <div
                                  className="h-full bg-blue-500"
                                  style={{ width: `${(s.reading_count / s.book_count) * 100}%` }}
                                />
                              </div>
                            )}
                          </div>
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
            }
            </>
          ) : (
          <>

          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <div className="flex items-center gap-1 bg-muted rounded-lg p-0.5">
              {(Object.keys(SORT_LABELS) as SortField[]).map(f => (
                <button key={f} onClick={() => toggleSort(f)}
                  className={cn(
                    'flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-all',
                    sort === f ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
                  )}>
                  {SORT_LABELS[f]}
                  {sort === f && (order === 'asc' ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />)}
                </button>
              ))}
            </div>

            <button
              onClick={() => setFilterOpen(o => !o)}
              className={cn(
                'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-all',
                filterOpen || hasFilters
                  ? 'border-primary/40 bg-primary/5 text-primary'
                  : 'border-border bg-card text-muted-foreground hover:text-foreground hover:bg-muted'
              )}>
              <SlidersHorizontal className="w-3.5 h-3.5" />
              Filters
              {hasFilters && (
                <span className="ml-0.5 w-4 h-4 rounded-full bg-primary text-primary-foreground text-[9px] flex items-center justify-center font-bold">
                  {activeFilterChips.length}
                </span>
              )}
            </button>

            {/* Save filter button — only when content filters active (not library) */}
            <SaveFilterButton params={saveableParams} onSaved={loadSavedFilters} />

            {activeFilterChips.map(f => (
              <span key={f.key} className="flex items-center gap-1 px-2 py-1 rounded-full text-xs bg-primary/10 text-primary border border-primary/20">
                {f.label}
                <button onClick={() => setFilter(f.key, '')} className="hover:text-destructive transition-colors">
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}

            {(hasFilters || filterLibrary) && (
              <button onClick={clearFilters} className="text-xs text-muted-foreground hover:text-foreground transition-colors ml-1">
                Clear all
              </button>
            )}

            <div className="flex-1" />
            <span className="text-xs text-muted-foreground hidden sm:block">
              {loading ? '…' : `${books.length} book${books.length !== 1 ? 's' : ''}`}
            </span>

            {/* Select mode toggle */}
            {books.length > 0 && (
              <div className="flex items-center gap-1">
                <button
                  onClick={() => {
                    if (!selectionMode) {
                      setSelectionMode(true)
                      selectAll()
                    } else if (selected.size > 0) {
                      clearSelection()
                    } else {
                      selectAll()
                    }
                  }}
                  className={cn(
                    'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-all',
                    selectionMode
                      ? 'border-primary/40 bg-primary/10 text-primary'
                      : 'border-border bg-card text-muted-foreground hover:text-foreground hover:bg-muted'
                  )}
                >
                  {selectionMode
                    ? selected.size > 0
                      ? <><XSquare className="w-3.5 h-3.5" /><span className="hidden sm:inline"> Deselect all</span></>
                      : <><CheckSquare className="w-3.5 h-3.5" /><span className="hidden sm:inline"> Select all</span></>
                    : <><CheckSquare className="w-3.5 h-3.5" /><span className="hidden sm:inline"> Select</span></>
                  }
                </button>
                {selectionMode && (
                  <button
                    onClick={exitSelectionMode}
                    title="Exit selection mode"
                    aria-label="Exit selection mode"
                    className="p-1.5 rounded-lg border border-border bg-card text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            )}

            <div className="flex items-center gap-0.5 bg-muted rounded-lg p-0.5">
              {([
                { mode: 'large' as ViewMode, Icon: LayoutGrid, title: 'Large covers' },
                { mode: 'small' as ViewMode, Icon: LayoutList, title: 'Small covers' },
                { mode: 'list' as ViewMode, Icon: List, title: 'List view' },
              ]).map(({ mode, Icon, title }) => (
                <button key={mode} onClick={() => persistView(mode)} title={title} aria-label={title}
                  className={cn('p-1.5 rounded-md transition-all',
                    view === mode ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}>
                  <Icon className="w-3.5 h-3.5" />
                </button>
              ))}
            </div>
          </div>

          {/* ── Filter panel ──────────────────────────────────────────────── */}
          {filterOpen && (
            <div className="mb-4 p-4 rounded-xl border border-border bg-card flex flex-col gap-4">
              <div className="flex items-center justify-between sm:hidden">
                <span className="text-xs font-medium text-muted-foreground">Filters</span>
                <button
                  onClick={() => setFilterOpen(false)}
                  className="p-1 text-muted-foreground hover:text-foreground"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <FilterSelect label="Series" value={filterSeries} options={facets.series} onChange={v => setFilter('series', v)} />
                <FilterSelect label="Author" value={filterAuthor} options={facets.authors} onChange={v => setFilter('author', v)} />
                <FilterSelect label="Tag" value={filterTag} options={facets.tags} onChange={v => setFilter('tag', v)} />
                <FilterSelect label="Format" value={filterFormat} options={facets.formats.map(f => f.toUpperCase())} onChange={v => setFilter('format', v.toLowerCase())} />
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs text-muted-foreground font-medium w-14">Status</span>
                {(['', 'unread', 'reading', 'read'] as const).map(s => (
                  <button
                    key={s}
                    onClick={() => setFilter('reading_status', s)}
                    className={cn(
                      'px-3 py-1 rounded-lg text-xs font-medium border transition-all',
                      filterReadingStatus === s
                        ? 'bg-primary text-primary-foreground border-primary'
                        : 'border-border bg-card text-muted-foreground hover:text-foreground'
                    )}
                  >
                    {s === '' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1)}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs text-muted-foreground font-medium w-14">Missing</span>
                {([
                  { value: '', label: 'None' },
                  { value: 'cover', label: 'Cover' },
                  { value: 'description', label: 'Description' },
                  { value: 'author', label: 'Author' },
                  { value: 'series', label: 'Series' },
                  { value: 'any', label: 'Any' },
                ]).map(({ value, label }) => (
                  <button
                    key={value}
                    onClick={() => setFilter('missing', value)}
                    className={cn(
                      'px-3 py-1 rounded-lg text-xs font-medium border transition-all',
                      filterMissing === value
                        ? 'bg-primary text-primary-foreground border-primary'
                        : 'border-border bg-card text-muted-foreground hover:text-foreground'
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {/* Ownership filter — members only (not admin) */}
              {isMember(user) && !isAdmin(user) && (
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs text-muted-foreground font-medium w-14">Books</span>
                  {([
                    { value: '', label: 'All' },
                    { value: 'mine', label: 'My Books' },
                    { value: 'shared', label: 'Shared Library' },
                  ] as const).map(({ value, label }) => (
                    <button
                      key={value}
                      onClick={() => setFilter('ownership', value)}
                      className={cn(
                        'px-3 py-1 rounded-lg text-xs font-medium border transition-all',
                        filterOwnership === value
                          ? 'bg-primary text-primary-foreground border-primary'
                          : 'border-border bg-card text-muted-foreground hover:text-foreground'
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
              {/* Uploader filter — admins only */}
              {isAdmin(user) && userList.length > 0 && (
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs text-muted-foreground font-medium w-14">Uploader</span>
                  <select
                    value={filterAddedBy ?? ''}
                    onChange={e => setFilter('added_by', e.target.value)}
                    className="text-xs rounded-lg border border-border bg-background px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring"
                  >
                    <option value="">All users</option>
                    {userList.map(u => (
                      <option key={u.id} value={u.id}>{u.username} ({u.role})</option>
                    ))}
                  </select>
                </div>
              )}
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs text-muted-foreground font-medium w-14">Type</span>
                <select
                  value={contentType}
                  onChange={e => setContentType(e.target.value)}
                  className="text-xs rounded-lg border border-border bg-background px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  <option value="volume">Volumes</option>
                  <option value="chapter">Chapters</option>
                  <option value="">All</option>
                </select>
              </div>
            </div>
          )}

          {/* ── Bulk action bar ──────────────────────────────────────────── */}
          {selectionMode && (
            <div className="mb-3 flex items-center gap-2 px-3 py-2 rounded-xl bg-primary/5 border border-primary/20">
              <span className="text-xs font-medium text-primary">{selected.size} selected</span>
              <div className="flex-1" />
              <button
                onClick={bulkDownload}
                disabled={bulkPending || selected.size === 0}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted disabled:opacity-50 transition-all"
              >
                <Download className="w-3.5 h-3.5" />
                Download ZIP
              </button>
              <button
                onClick={() => { setBulkMetaOpen(true); api.get<Facets>('/books/facets').then(setFacets).catch(() => {}) }}
                disabled={bulkPending || selected.size === 0}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted disabled:opacity-50 transition-all"
              >
                <Pencil className="w-3.5 h-3.5" />
                Edit Metadata
              </button>
              {libraries.length > 0 && (
                <div className="relative">
                  <button
                    onClick={() => setBulkLibMenu(o => !o)}
                    disabled={bulkPending || selected.size === 0}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
                  >
                    <LibraryIcon className="w-3.5 h-3.5" />
                    Add to Library
                  </button>
                  {bulkLibMenu && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setBulkLibMenu(false)} />
                      <div className="absolute right-0 top-full mt-1 z-20 bg-card border border-border rounded-xl shadow-xl py-1 min-w-44">
                        {libraries.map(lib => (
                          <button
                            key={lib.id}
                            onClick={() => bulkAddToLibrary(lib.id)}
                            className="flex items-center gap-2 w-full px-3 py-2 text-sm hover:bg-muted transition-colors"
                          >
                            <LibraryIcon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                            {lib.name}
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}
              <button
                onClick={() => setDeleteModalOpen(true)}
                disabled={bulkPending || selected.size === 0}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-destructive/30 bg-destructive/10 text-destructive hover:bg-destructive/20 disabled:opacity-50 transition-all"
              >
                <Trash2 className="w-3.5 h-3.5" />
                Delete
              </button>
              <button onClick={exitSelectionMode} className="text-xs text-muted-foreground hover:text-foreground transition-colors">
                Done
              </button>
            </div>
          )}

          {/* ── Search result count ───────────────────────────────────────── */}
          {search && !loading && totalCount !== null && (
            <p className="text-sm text-muted-foreground mb-3">
              {totalCount === 0
                ? `No results for "${search}"`
                : `${totalCount} result${totalCount !== 1 ? 's' : ''} for "${search}"`}
            </p>
          )}

          {/* ── Grid / list ───────────────────────────────────────────────── */}
          {loading ? (
            <div className="flex justify-center py-24">
              <Loader2 className="w-6 h-6 animate-spin text-primary" />
            </div>
          ) : books.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-24 gap-3 text-muted-foreground">
              <BookOpen className="w-12 h-12 opacity-20" />
              {search ? (
                <>
                  <p className="text-sm">No results for &ldquo;{search}&rdquo;</p>
                  <button
                    onClick={() => { setSearchInput(''); setFilter('q', '') }}
                    className="text-xs text-primary hover:underline"
                  >
                    Clear search
                  </button>
                </>
              ) : (hasFilters || filterLibrary) ? (
                <>
                  <p className="text-sm">No books match your filters.</p>
                  <button onClick={clearFilters} className="text-xs text-primary hover:underline">Clear filters</button>
                </>
              ) : (
                <p className="text-sm">No books yet — upload or import some!</p>
              )}
            </div>
          ) : (
            <div className={cn(gridClass, refreshing && 'opacity-50 transition-opacity duration-150')}>
              {books.map((book, i) => (
                <BookCard
                  key={`${view}-${book.id}`}
                  book={book}
                  view={view}
                  index={i}
                  selected={selected.has(book.id)}
                  focused={focusedIndex === i}
                  onSelect={selectionMode ? (e) => { e.preventDefault(); toggleSelect(book.id, e.shiftKey) } : undefined}
                  onTagClick={tag => setFilter('tag', tag)}
                  onSeriesClick={series => setFilter('series', series)}
                  onAuthorClick={author => setFilter('author', author)}
                  readingStatus={readingStatuses[book.id]?.status}
                  progressPct={readingStatuses[book.id]?.progress_pct}
                />
              ))}
            </div>
          )}
          {/* Infinite scroll sentinel */}
          <div ref={sentinelRef} className="h-1 mt-2" />
          {loadingMore && (
            <div className="flex justify-center py-4">
              <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
            </div>
          )}
          {!hasMore && booksRef.current.length >= PAGE_SIZE && (
            <p className="text-center text-xs text-muted-foreground py-4">
              All {booksRef.current.length} books loaded
            </p>
          )}
          </>
          )}
        </main>
      </div>

      {/* ── Bulk delete modal ───────────────────────────────────────────────── */}
      <BulkDeleteModal
        open={deleteModalOpen}
        books={books}
        selectedIds={selected}
        onCancel={() => setDeleteModalOpen(false)}
        onConfirm={bulkDelete}
      />

      {/* ── Bulk metadata modal ─────────────────────────────────────────────── */}
      {bulkMetaOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/50"
            onClick={() => { if (!bulkMetaSaving) setBulkMetaOpen(false) }}
          />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
            <div className="pointer-events-auto w-full max-w-md bg-card border border-border rounded-2xl shadow-2xl p-6">
              <div className="flex items-start justify-between mb-1">
                <h2 className="text-base font-semibold text-foreground">
                  Edit Metadata for {selected.size} Book{selected.size !== 1 ? 's' : ''}
                </h2>
                <button
                  onClick={() => setBulkMetaOpen(false)}
                  disabled={bulkMetaSaving}
                  className="text-muted-foreground hover:text-foreground transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              <p className="text-xs text-muted-foreground mb-4">
                Only filled fields will be updated. Leave blank to keep existing values.
              </p>

              <div className="space-y-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Author</label>
                  <AutocompleteInput
                    value={bulkMetaAuthor}
                    onChange={setBulkMetaAuthor}
                    suggestions={facets.authors}
                    placeholder="Author"
                    className="w-full text-sm bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Series</label>
                  <AutocompleteInput
                    value={bulkMetaSeries}
                    onChange={setBulkMetaSeries}
                    suggestions={facets.series}
                    placeholder="Series name"
                    className="w-full text-sm bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>

                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Add Tags</label>
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {bulkMetaTagsAdd.map(tag => (
                      <span key={tag} className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-muted border border-border text-foreground">
                        {tag}
                        <button
                          type="button"
                          onClick={() => setBulkMetaTagsAdd(prev => prev.filter(t => t !== tag))}
                          className="text-muted-foreground hover:text-destructive transition-colors"
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </span>
                    ))}
                  </div>
                  <AutocompleteInput
                    value={bulkMetaTagInput}
                    onChange={setBulkMetaTagInput}
                    suggestions={facets.tags.filter(t => !bulkMetaTagsAdd.includes(t))}
                    placeholder="Add tag…"
                    onSelect={tag => {
                      if (tag && !bulkMetaTagsAdd.includes(tag)) {
                        setBulkMetaTagsAdd(prev => [...prev, tag])
                        setBulkMetaTagInput('')
                      }
                    }}
                    onEnter={val => {
                      const trimmed = val.trim()
                      if (trimmed && !bulkMetaTagsAdd.includes(trimmed)) {
                        setBulkMetaTagsAdd(prev => [...prev, trimmed])
                        setBulkMetaTagInput('')
                      }
                    }}
                    className="text-sm bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring w-48"
                  />
                </div>

                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Type</label>
                  <select
                    value={bulkMetaTypeId}
                    onChange={e => setBulkMetaTypeId(e.target.value ? Number(e.target.value) : '')}
                    className="w-full text-sm bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring text-foreground"
                  >
                    <option value="">— keep existing —</option>
                    {bookTypes.map(t => (
                      <option key={t.id} value={t.id}>{t.label}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="flex justify-end gap-2 mt-5">
                <button
                  onClick={() => setBulkMetaOpen(false)}
                  disabled={bulkMetaSaving}
                  className="px-3 py-1.5 rounded-lg text-sm border border-border text-muted-foreground hover:bg-muted transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={bulkSaveMetadata}
                  disabled={bulkMetaSaving || (!bulkMetaAuthor && !bulkMetaSeries && !bulkMetaTagsAdd.length && !bulkMetaTypeId)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
                >
                  {bulkMetaSaving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  Save Changes
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function FilterSelect({ label, value, options, onChange }: {
  label: string; value: string; options: string[]; onChange: (v: string) => void
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-muted-foreground">{label}</label>
      <select value={value} onChange={e => onChange(e.target.value)}
        className="h-8 rounded-md border border-border bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring text-foreground">
        <option value="">All</option>
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  )
}
