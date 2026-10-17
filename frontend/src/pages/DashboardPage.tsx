import { useEffect, useLayoutEffect, useRef, useState, useCallback } from 'react'
import { AnimatePresence, m } from 'motion/react'
import { useSearchParams, useNavigate, useLocation, useNavigationType } from 'react-router-dom'
import {
  X,
  LayoutGrid, List,
  SlidersHorizontal, Loader2,
  Library as LibraryIcon, CheckSquare, XSquare, Download, Pencil,
  Play, CheckCheck, Trash2, Settings2, Layers, Star, Share2,
  Tablet,
} from 'lucide-react'
import { AppHeader, HeaderSearch } from '@/components/AppHeader'
import { ShellFrame } from '@/components/ShellFrame'
import { ModalShell } from '@/components/ModalShell'
import { useAuth, isMember, isAdmin } from '@/contexts/AuthContext'
import { Trans, useLingui, Plural } from '@lingui/react/macro'
import { plural, msg } from '@lingui/core/macro'
import type { MessageDescriptor } from '@lingui/core'
import { useToast } from '@/contexts/ToastContext'
import { useSidebarLists } from '@/lib/sidebarLists'
import { BookCard, type ViewMode } from '@/components/BookCard'
import { SeriesStackCard } from '@/components/SeriesStackCard'
import { StarRating } from '@/components/StarRating'
import { SeriesRating } from '@/components/SeriesRating'
import { SeriesFollowButton } from '@/components/SeriesFollowButton'
import { ShareModal } from '@/components/ShareShelfModal'
import { CoverImage } from '@/components/CoverImage'
import { HScrollRow } from '@/components/HScrollRow'
import { Sidebar } from '@/components/Sidebar'
import { SaveFilterButton } from '@/components/SaveFilterButton'
import { AutocompleteInput } from '@/components/AutocompleteInput'
import { UploadModal } from '@/components/UploadModal'
import { ManageSeriesModal } from '@/components/ManageSeriesModal'
import { SendButton } from '@/components/SendButton'
import { BookAnimation } from '@/components/BookAnimation'
import { HomeTab } from '@/components/home/HomeTab'
import { api } from '@/lib/api'
import type { Book, ReadingStatus, Arc, SeriesMeta, SeriesStatus } from '@/lib/books'
import { formatBytes } from '@/lib/books'
import { useBookTypes } from '@/lib/bookTypes'
import { useShiftSelect } from '@/lib/useShiftSelect'
import { cn } from '@/lib/utils'
import { devicesFor, useDeviceInventories } from '@/lib/deviceBooks'
import { describePace, formatEstimateDays, formatEstimateHours, type BacklogSummary } from '@/lib/backlog'
import { SeriesReadingStats } from '@/components/SeriesReadingStats'
import { SeriesCoverageStrip } from '@/components/SeriesCoverageStrip'
import { QuickLook } from '@/components/QuickLook'
import { SortMenu } from '@/components/library/SortMenu'
import { PageBody, PageHeader, SectionTitle, Pill } from '@/components/page'
// Library / Series tab icons, kept apart from the shared import above.
import { ArrowLeft, Eye, Upload } from 'lucide-react'
import { coverSrc } from '@/lib/covers'
import { preloadImages } from '@/lib/preload'

type SortField = 'title' | 'author' | 'year' | 'added_at' | 'rating'
type SortOrder = 'asc' | 'desc'

interface SeriesItem {
  name: string
  book_count: number
  cover_book_id: number
  description: string | null
  author: string | null
  read_count: number
  reading_count: number
  rating: number | null
}

/** Lower-case, accents and punctuation folded to spaces: "Good-Guys!" and
 *  "good guys" match. */
function normalizeForMatch(s: string): string {
  return s.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ').trim()
}

interface SeriesDetailBook {
  id: number
  updated_at?: string | null
  title: string
  series_index: number | null
  cover_path: string | null
  reading_status: 'unread' | 'reading' | 'read' | 'shelved' | 'want_to_read'
  progress_pct: number | null
}

interface SeriesDetail {
  name: string
  author: string | null
  backlog: BacklogSummary | null
  description: string | null
  books: SeriesDetailBook[]
}

interface Facets {
  series: string[]
  authors: string[]
  tags: string[]
  formats: string[]
  languages: { code: string; label: string }[]
}

const SORT_LABELS: Record<SortField, MessageDescriptor> = {
  title: msg`Title`, author: msg`Author`, year: msg`Year`, added_at: msg`Date Added`, rating: msg`My Rating`,
}

// Reading-status + series-status display names, shared by filter buttons and chips.
const READING_STATUS_LABELS: Record<string, MessageDescriptor> = {
  unread: msg`Unread`, want_to_read: msg`Want to Read`, reading: msg`Reading`,
  read: msg({ message: 'Read', context: 'status' }), shelved: msg`Shelved`,
}
const MISSING_LABELS: Record<string, MessageDescriptor> = {
  cover: msg`Cover`, description: msg`Description`, author: msg`Author`,
  series: msg`Series`, any: msg`Any`,
}
const SERIES_STATUS_LABELS: Record<string, MessageDescriptor> = {
  ongoing: msg`ongoing`, finished: msg`finished`, hiatus: msg`hiatus`,
}

const VIEW_KEY = 'tome_view'
const GRID_SIZE_KEY = 'tome_grid_size'
const GRID_SIZE_MIN = 110
const GRID_SIZE_MAX = 240
type ViewPref = 'grid' | 'list'
const SORT_KEY = 'tome_sort'
const ORDER_KEY = 'tome_order'
const GROUP_KEY = 'tome_group_series'

// ── Bulk Delete Modal ──────────────────────────────────────────────────────────
interface BulkDeleteModalProps {
  open: boolean
  books: Book[]
  selectedIds: Set<number>
  onCancel: () => void
  onConfirm: () => Promise<void>
}

function BulkDeleteModal({ open, books, selectedIds, onCancel, onConfirm }: BulkDeleteModalProps) {
  const { t } = useLingui()
  const [deleting, setDeleting] = useState(false)

  const selectedBooks = books.filter(b => selectedIds.has(b.id))

  async function handleDelete() {
    setDeleting(true)
    await onConfirm()
    setDeleting(false)
  }

  return (
    <ModalShell open={open} onClose={() => { if (!deleting) onCancel() }} className="w-full max-w-md">
        <div className="bg-card border border-border rounded-2xl shadow-2xl shadow-accent-soft flex flex-col max-h-[80vh]">
          {/* Header */}
          <div className="flex items-center justify-between px-6 pt-6 pb-4 shrink-0">
            <div className="flex items-center gap-2">
              <Trash2 className="w-4 h-4 text-destructive" />
              <h2 className="text-base font-semibold text-foreground">
                <Plural value={selectedIds.size} one="Delete # book" other="Delete # books" />
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
              <Trans>This permanently removes the selected books and their files from disk. This cannot be undone.</Trans>
            </p>
          </div>

          {/* Book list */}
          <div className="overflow-y-auto flex-1 px-6 pb-3">
            <div className="flex flex-col gap-1">
              {selectedBooks.map(book => {
                const primaryFile = book.files[0] ?? null
                return (
                  <div key={book.id} className="flex items-center gap-3 py-2 border-b border-border last:border-0">
                    <div className="relative w-8 h-11 rounded overflow-hidden shrink-0 border border-border">
                      <CoverImage
                        src={book.cover_path ? coverSrc(book.id, { w: 160 }) : null}
                        alt={book.title}
                        iconClassName="w-3.5 h-3.5"
                      />
                    </div>
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
              {selectedIds.size > selectedBooks.length && (
                <p className="text-xs text-muted-foreground py-2">
                  {(() => { const more = selectedIds.size - selectedBooks.length; return t`…and ${more} more not shown` })()}
                </p>
              )}
            </div>
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-border shrink-0">
            {deleting && (
              <span className="text-xs text-muted-foreground mr-auto">
                <Plural value={selectedIds.size} one="Deleting # book..." other="Deleting # books..." />
              </span>
            )}
            <button
              onClick={onCancel}
              disabled={deleting}
              className="px-3 py-1.5 rounded-lg text-sm border border-border text-muted-foreground hover:bg-muted disabled:opacity-50 transition-colors"
            >
              <Trans>Cancel</Trans>
            </button>
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-destructive text-destructive-foreground hover:opacity-90 disabled:opacity-50 transition-all"
            >
              {deleting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              <Plural value={selectedIds.size} one="Delete # book" other="Delete # books" />
            </button>
          </div>
        </div>
    </ModalShell>
  )
}

type StatusMap = Record<number, { status: ReadingStatus; progress_pct: number | null; rating: number | null }>

// The library list as it was when the page was last left, so coming back
// (from a book, Stats, anywhere) shows it at once and where it was scrolled
// to, then refreshes it quietly. One entry: the list for one set of filters.
let listCache: {
  key: string
  books: Book[]
  total: number | null
  statuses: StatusMap
  hasMore: boolean
  scrollTop: number
  locationKey: string
} | null = null

export function DashboardPage() {
  const { t, i18n } = useLingui()
  const { user } = useAuth()
  const navigate = useNavigate()
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null)

  // ── View / sort (persisted) ───────────────────────────────────────────────
  // Two view modes (grid/list) + a cover-size slider for the grid. Old installs
  // stored 'large' | 'small' — those migrate to grid at the matching size.
  const [view, setView] = useState<ViewPref>(() =>
    localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'grid')
  const [gridSize, setGridSize] = useState<number>(() => {
    const stored = Number(localStorage.getItem(GRID_SIZE_KEY))
    if (stored >= GRID_SIZE_MIN && stored <= GRID_SIZE_MAX) return stored
    return localStorage.getItem(VIEW_KEY) === 'small' ? 120 : 180
  })
  const [sort, setSort] = useState<SortField>(() =>
    (localStorage.getItem(SORT_KEY) as SortField | null) ?? 'title')
  const [order, setOrder] = useState<SortOrder>(() =>
    (localStorage.getItem(ORDER_KEY) as SortOrder | null) ?? 'asc')

  function persistView(v: ViewPref) { setView(v); localStorage.setItem(VIEW_KEY, v) }
  function persistGridSize(s: number) { setGridSize(s); localStorage.setItem(GRID_SIZE_KEY, String(s)) }
  function persistSort(s: SortField) { setSort(s); localStorage.setItem(SORT_KEY, s) }
  function persistOrder(o: SortOrder) { setOrder(o); localStorage.setItem(ORDER_KEY, o) }

  // ── Group by series (persisted) ───────────────────────────────────────────
  const [groupBySeries, setGroupBySeries] = useState(() => localStorage.getItem(GROUP_KEY) === 'true')
  function persistGroup(v: boolean) {
    setGroupBySeries(v)
    localStorage.setItem(GROUP_KEY, String(v))
    if (v) exitSelectionMode()
  }

  // ── Tab (Home / Books / Series) — derived from URL ───────────────────────
  const tab = (searchParams.get('tab') || 'home') as 'home' | 'books' | 'series'
  const [seriesList, setSeriesList] = useState<SeriesItem[]>([])
  const [seriesLoading, setSeriesLoading] = useState(false)
  const [expandedSeries, setExpandedSeries] = useState<string | null>(null)
  const [seriesDetail, setSeriesDetail] = useState<SeriesDetail | null>(null)
  const [shareSeries, setShareSeries] = useState<string | null>(null)
  const [seriesDetailLoading, setSeriesDetailLoading] = useState(false)
  const [markingAllRead, setMarkingAllRead] = useState(false)
  const [contentType, setContentType] = useState<string>('volume')
  // Series meta (status badges) — keyed by series name
  const [seriesMetaMap, setSeriesMetaMap] = useState<Record<string, SeriesStatus>>({})
  // Arcs for the currently open series detail
  const [seriesArcs, setSeriesArcs] = useState<Arc[]>([])
  // Manage series modal
  const [manageSeriesOpen, setManageSeriesOpen] = useState(false)
  const [seriesDescExpanded, setSeriesDescExpanded] = useState(false)

  function setTab(value: 'home' | 'books' | 'series') {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (value === 'home') next.delete('tab')
      else next.set('tab', value)
      return next
    })
  }

  function fetchSeriesMetaForList(_list: SeriesItem[]) {
    // Single batch call — previously fired one GET per series which
    // exhausted the DB connection pool on libraries with many series.
    api.get<Record<string, SeriesStatus>>('/series/meta-map')
      .then(map => setSeriesMetaMap(map))
      .catch(() => {})
  }

  function openSeriesTab() {
    setTab('series')
    setExpandedSeries(null)
    setSeriesDetail(null)
    setSeriesLoading(true)
    api.get<SeriesItem[]>('/books/series')
      .then(list => {
        setSeriesList(list)
        fetchSeriesMetaForList(list)
      })
      .catch(() => {})
      .finally(() => setSeriesLoading(false))
  }

  function openSeriesDetail(seriesName: string) {
    if (expandedSeries === seriesName) {
      setExpandedSeries(null)
      setSeriesDetail(null)
      setSeriesArcs([])
      return
    }
    setExpandedSeries(seriesName)
    setSeriesDetailLoading(true)
    setSeriesDetail(null)
    setSeriesArcs([])
    setSeriesDescExpanded(false)
    Promise.all([
      api.get<SeriesDetail>(`/books/series-detail?name=${encodeURIComponent(seriesName)}`),
      api.get<Arc[]>(`/series/${encodeURIComponent(seriesName)}/arcs`),
    ])
      .then(([detail, arcs]) => {
        setSeriesDetail(detail)
        setSeriesArcs(arcs)
      })
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
        fetchSeriesMetaForList(list)
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
    // Explicit intent beats position: a queued (want-to-read) volume is next.
    const queued = books.find(b => b.reading_status === 'want_to_read')
    if (queued) return queued
    // Find first unread where previous volume (by index order) is read
    for (let i = 1; i < books.length; i++) {
      if (books[i].reading_status === 'unread' && books[i - 1].reading_status === 'read') {
        return books[i]
      }
    }
    return books.find(b => b.reading_status === 'unread') ?? null
  }

  interface ArcSection {
    /** The arc's id, null for the "Unassigned" bucket and a series without arcs. */
    arcId: number | null
    name: string
    label: string
    description: string | null
    volumes: SeriesDetailBook[]
  }

  function groupVolumesByArc(books: SeriesDetailBook[], arcs: Arc[]): ArcSection[] {
    if (arcs.length === 0) return [{ arcId: null, name: '', label: '', description: null, volumes: books }]
    const sorted = [...arcs].sort((a, b) => a.start_index - b.start_index)
    const sections: ArcSection[] = sorted.map(arc => {
      const { name, start_index, end_index } = arc
      return {
        arcId: arc.id,
        name,
        label: t`${name} · Vol. ${start_index}–${end_index}`,
        description: arc.description ?? null,
        volumes: books.filter(v =>
          v.series_index != null &&
          v.series_index >= arc.start_index &&
          v.series_index <= arc.end_index
        ),
      }
    })
    const unassigned = books.filter(v =>
      v.series_index == null ||
      !sorted.some(a => v.series_index! >= a.start_index && v.series_index! <= a.end_index)
    )
    if (unassigned.length > 0) {
      sections.push({ arcId: null, name: '', label: t`Unassigned`, description: null, volumes: unassigned })
    }
    return sections.filter(s => s.volumes.length > 0)
  }

  function SeriesStatusBadge({ status, large = false }: { status: SeriesStatus | undefined; large?: boolean }) {
    if (!status || status === 'unknown') return null
    /* eslint-disable lingui/no-unlocalized-strings -- Tailwind classes */
    const cls =
      status === 'ongoing'
        ? 'bg-warning/15 text-warning'
        : status === 'finished'
        ? 'bg-success/15 text-success'
        : /* hiatus */ 'bg-muted text-muted-foreground'
    /* eslint-enable lingui/no-unlocalized-strings */
    return (
      <span className={cn(
        'inline-flex items-center font-semibold uppercase leading-none',
        large ? 'h-7 px-3 rounded-full text-[12px] tracking-[0.08em]' : 'px-1.5 py-0.5 rounded text-[10px] tracking-wide',
        cls,
      )}>
        {SERIES_STATUS_LABELS[status] ? i18n._(SERIES_STATUS_LABELS[status]) : status}
      </span>
    )
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
  const filterLanguage = searchParams.get('language') ?? ''
  const filterLibrary = searchParams.get('library_id') ? Number(searchParams.get('library_id')) : null
  const filterReadingStatus = searchParams.get('reading_status') ?? ''
  const filterMinRating = searchParams.get('min_rating') ? Number(searchParams.get('min_rating')) : null
  const filterMissing = searchParams.get('missing') ?? ''
  const filterOwnership = searchParams.get('ownership') ?? ''
  const filterAddedBy = searchParams.get('added_by') ? Number(searchParams.get('added_by')) : null
  // The user's KOReader devices, comma-separated: books on all of
  // on_device and on none of not_on_device.
  const filterOnDevice = searchParams.get('on_device') ?? ''
  const filterNotOnDevice = searchParams.get('not_on_device') ?? ''
  const onDevices = filterOnDevice.split(',').filter(Boolean)
  const notOnDevices = filterNotOnDevice.split(',').filter(Boolean)
  const devices = useDeviceInventories()

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

  // A device pill cycles off -> on -> not on -> off.
  function cycleDeviceFilter(device: string) {
    const on = onDevices.filter(d => d !== device)
    const not = notOnDevices.filter(d => d !== device)
    if (onDevices.includes(device)) not.push(device)
    else if (!notOnDevices.includes(device)) on.push(device)
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      next.delete('saved_filter')
      if (on.length) next.set('on_device', on.join(','))
      else next.delete('on_device')
      if (not.length) next.set('not_on_device', not.join(','))
      else next.delete('not_on_device')
      return next
    })
  }

  function clearFilters() {
    setSearchParams(prev => {
      const next = new URLSearchParams()
      const t = prev.get('tab')
      if (t) next.set('tab', t)
      return next
    })
  }

  const hasFilters = !!(search || filterSeries || filterNoSeries || filterAuthor || filterTag || filterFormat || filterLanguage || filterReadingStatus || filterMinRating || filterMissing || filterOwnership || filterAddedBy || filterOnDevice || filterNotOnDevice)

  // Grouping is bypassed while drilling into a specific series — the user
  // explicitly asked for that series' volumes, so a single stack is useless.
  const groupActive = groupBySeries && !filterSeries

  // ── Series matching the search ───────────────────────────────────────────
  // A search that names a series ("good guys") shows the series themselves in
  // a row above the volumes. Matched on the series name only. The list comes from /books/series (visible
  // series only), fetched once per page and reused while typing. Each query
  // word must start a word of the name, so a half-typed "goo" already finds
  // "The Good Guys" but "the" does not hit "Brother".
  const allSeriesRef = useRef<SeriesItem[] | null>(null)
  const [searchSeries, setSearchSeries] = useState<SeriesItem[]>([])
  useEffect(() => {
    const words = normalizeForMatch(search).split(' ').filter(Boolean)
    if (tab !== 'books' || words.length === 0) { setSearchSeries([]); return }
    let cancelled = false
    const pick = (list: SeriesItem[]) => {
      const query = words.join(' ')
      const hits = list
        .filter(s => s.name !== '__unserialized__')
        .map(s => ({ s, name: normalizeForMatch(s.name) }))
        .filter(({ name }) => {
          const nameWords = name.split(' ')
          return words.every(w => nameWords.some(nw => nw.startsWith(w)))
        })
        // Names starting with the query first, then bigger series.
        .sort((a, b) => Number(b.name.startsWith(query)) - Number(a.name.startsWith(query))
          || b.s.book_count - a.s.book_count)
        .slice(0, 12)
        .map(({ s }) => s)
      if (!cancelled) setSearchSeries(hits)
    }
    if (allSeriesRef.current) pick(allSeriesRef.current)
    else {
      api.get<SeriesItem[]>('/books/series')
        .then(list => { allSeriesRef.current = list; pick(list) })
        .catch(() => {})
    }
    return () => { cancelled = true }
  }, [search, tab])

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
  async function selectAll() {
    // Select the whole filtered set, not just the pages the infinite scroll
    // has loaded (issue #165 follow-up). Loaded books are selected instantly
    // so the UI responds; the full id list replaces them when it arrives.
    setSelected(new Set(books.map(b => b.id)))
    try {
      const res = await api.get<{ ids: number[] }>(`/books/ids?${buildFilterParams()}`)
      setSelected(new Set(res.ids))
    } catch {
      // keep the loaded-books selection as fallback
    }
  }
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
      toastError(e instanceof Error ? e.message : t`Download failed`)
    } finally {
      setBulkPending(false)
    }
  }

  async function bulkAddToLibrary(libId: number) {
    if (!selected.size) return
    setBulkPending(true)
    try {
      await api.post(`/libraries/${libId}/books`, { book_ids: [...selected] })
      toastSuccess(plural(selected.size, { one: 'Added # book to library', other: 'Added # books to library' }))
      clearSelection()
      setBulkLibMenu(false)
      loadLibraries()
    } catch (e) {
      toastError(e instanceof Error ? e.message : t`Failed`)
    } finally {
      setBulkPending(false)
    }
  }

  async function bulkDelete() {
    if (!selected.size) return
    setBulkPending(true)
    const ids = [...selected]
    const CHUNK = 500 // server caps bulk-delete at 500 ids per request
    let deleted = 0
    let failed = 0
    try {
      for (let i = 0; i < ids.length; i += CHUNK) {
        const res = await api.post<{ deleted: number[]; errors: { book_id: number; error: string }[] }>(
          '/books/bulk-delete',
          { book_ids: ids.slice(i, i + CHUNK) },
        )
        deleted += res.deleted.length
        failed += res.errors.length
      }
      toastSuccess(plural(deleted, { one: 'Deleted # book', other: 'Deleted # books' }))
      if (failed) {
        toastError(plural(failed, { one: '# book could not be deleted', other: '# books could not be deleted' }))
      }
    } catch (e) {
      toastError(e instanceof Error ? e.message : t`Delete failed`)
    }
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
      toastSuccess(plural(selected.size, { one: 'Updated metadata for # book', other: 'Updated metadata for # books' }))
      setBulkMetaOpen(false)
      setBulkMetaAuthor(''); setBulkMetaSeries(''); setBulkMetaTagsAdd([]); setBulkMetaTypeId('')
      clearSelection()
      loadBooks(); loadFacets()
    } catch (e) {
      toastError(e instanceof Error ? e.message : t`Failed to update metadata`)
    } finally {
      setBulkMetaSaving(false)
    }
  }

  // ── Sidebar data ──────────────────────────────────────────────────────────
  const { libraries, savedFilters, loadLibraries, loadSavedFilters } = useSidebarLists(user?.id)
  // Cached across page mounts (see useSidebarLists) — this refetch only
  // freshens the lists in the background, it never blanks them.
  useEffect(() => { loadLibraries(); loadSavedFilters() }, [])

  // ── Books + facets ────────────────────────────────────────────────────────
  const location = useLocation()
  const navigationType = useNavigationType()
  // The shell's <main> scrolls; reached through the body wrapper below.
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const locationKeyRef = useRef(location.key)
  locationKeyRef.current = location.key
  // Filters + sort of the list the current `books` belong to.
  const listKey = () => {
    const p = buildFilterParams()
    p.set('sort', sort)
    p.set('order', order)
    if (groupActive) p.set('group_by_series', 'true')
    return p.toString()
  }
  const [seed] = useState(() => (listCache && listCache.key === listKey() ? listCache : null))
  const seededRef = useRef(!!seed)
  const loadedKeyRef = useRef(seed?.key ?? '')
  const [books, setBooks] = useState<Book[]>(() => seed?.books ?? [])
  const { handleToggle } = useShiftSelect(books.map(b => b.id))

  const booksGridRef = useRef<HTMLDivElement | null>(null)
  const [totalCount, setTotalCount] = useState<number | null>(() => seed?.total ?? null)
  const [readingStatuses, setReadingStatuses] = useState<StatusMap>(() => seed?.statuses ?? {})
  const [facets, setFacets] = useState<Facets>({ series: [], authors: [], tags: [], formats: [], languages: [] })
  const [loading, setLoading] = useState(!seed)
  const [uploadModalOpen, setUploadModalOpen] = useState(false)
  const [filterOpen, setFilterOpen] = useState(false)
  // Quick look opened from a cover in the Library grid or a series' volumes.
  const [quickLookId, setQuickLookId] = useState<number | null>(null)
  const closeQuickLook = useCallback(() => setQuickLookId(null), [])

  const { toast } = useToast()
  const toastSuccess = toast.success
  const toastError = toast.error
  const searchInputRef = useRef<HTMLInputElement>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const booksRef = useRef<Book[]>(seed?.books ?? [])
  const abortRef = useRef<AbortController | null>(null)
  const [hasMore, setHasMore] = useState(seed?.hasMore ?? true)
  const hasMoreRef = useRef(seed?.hasMore ?? true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const PAGE_SIZE = 60

  function buildFilterParams(): URLSearchParams {
    const params = new URLSearchParams()
    if (search) params.set('q', search)
    if (filterSeries) params.set('series', filterSeries)
    if (filterNoSeries) params.set('no_series', 'true')
    if (filterAuthor) params.set('author', filterAuthor)
    if (filterTag) params.set('tag', filterTag)
    if (filterFormat) params.set('format', filterFormat)
    if (filterLanguage) params.set('language', filterLanguage)
    if (filterLibrary) params.set('library_id', String(filterLibrary))
    if (filterReadingStatus) params.set('reading_status', filterReadingStatus)
    if (filterMinRating) params.set('min_rating', String(filterMinRating))
    if (filterMissing) params.set('missing', filterMissing)
    if (contentType) params.set('content_type', contentType)
    if (filterOwnership) params.set('ownership', filterOwnership)
    if (filterAddedBy) params.set('added_by', String(filterAddedBy))
    if (filterOnDevice) params.set('on_device', filterOnDevice)
    if (filterNotOnDevice) params.set('not_on_device', filterNotOnDevice)
    return params
  }

  // `quiet`: refresh what is already shown (coming back to the page) without
  // dimming it, as many books as were loaded so the scroll position holds.
  const loadBooks = useCallback((reset = true, quiet = false) => {
    const skip = reset ? 0 : booksRef.current.length
    const limit = quiet ? Math.max(PAGE_SIZE, Math.ceil(booksRef.current.length / PAGE_SIZE) * PAGE_SIZE) : PAGE_SIZE
    const key = listKey()

    if (reset) {
      abortRef.current?.abort()
      abortRef.current = new AbortController()
      if (booksRef.current.length === 0) setLoading(true)
      else if (!quiet) setRefreshing(true)
      hasMoreRef.current = true
      setHasMore(true)
    } else {
      if (!hasMoreRef.current) return
      setLoadingMore(true)
    }

    // Load-more requests share the reset's controller: a new reset (filter
    // change, group toggle) aborts any in-flight page so its stale results
    // can't be appended onto the fresh list (viewport-duplicates bug).
    const signal = abortRef.current?.signal
    const params = buildFilterParams()
    params.set('sort', sort)
    params.set('order', order)
    params.set('skip', String(skip))
    params.set('limit', String(limit))
    if (groupActive) params.set('group_by_series', 'true')
    api.getWithHeaders<Book[]>(`/books?${params}`, signal)
      .then(({ data: newBooks, headers }) => {
        if (signal?.aborted) return
        if (reset) {
          loadedKeyRef.current = key
          const raw = headers.get('x-total-count')
          setTotalCount(raw !== null ? Number(raw) : null)
        }
        let merged: Book[]
        if (reset) {
          merged = newBooks
        } else {
          const seen = new Set(booksRef.current.map(b => b.id))
          merged = [...booksRef.current, ...newBooks.filter(b => !seen.has(b.id))]
        }
        booksRef.current = merged
        setBooks(merged)
        hasMoreRef.current = newBooks.length === limit
        setHasMore(newBooks.length === limit)
        if (newBooks.length > 0) {
          api.post<Record<string, { status: string; progress_pct: number | null; rating: number | null }>>('/books/statuses', { book_ids: newBooks.map(b => b.id) })
            .then(map => {
              const s: StatusMap = {}
              Object.entries(map).forEach(([id, val]) => { s[Number(id)] = { status: val.status as ReadingStatus, progress_pct: val.progress_pct, rating: val.rating ?? null } })
              setReadingStatuses(prev => ({ ...prev, ...s }))
            })
            .catch(() => {})
        } else if (reset) {
          setReadingStatuses({})
        }
      })
      .catch(err => {
        if (err instanceof Error && err.name === 'AbortError') return
        toastError(t`Failed to load books`)
      })
      .finally(() => {
        if (reset) {
          // An aborted reset was superseded by a newer one that owns the
          // loading flags — leave them alone.
          if (signal?.aborted) return
          setLoading(false)
          setRefreshing(false)
        } else {
          // Always clear, even when aborted: this request is finished and a
          // stuck loadingMore would stall the infinite scroll for good.
          setLoadingMore(false)
        }
      })
  }, [sort, order, search, filterSeries, filterNoSeries, filterAuthor, filterTag, filterFormat, filterLanguage, filterLibrary, filterReadingStatus, filterMinRating, filterMissing, contentType, filterOwnership, filterAddedBy, filterOnDevice, filterNotOnDevice, groupActive])

  useEffect(() => {
    if (seededRef.current) {
      seededRef.current = false
      loadBooks(true, true)
    } else loadBooks()
  }, [loadBooks])

  // Keep the list (and, on leaving, the scroll position) for the way back.
  useEffect(() => {
    if (!loadedKeyRef.current) return
    listCache = {
      key: loadedKeyRef.current, books, total: totalCount, statuses: readingStatuses, hasMore,
      scrollTop: listCache?.scrollTop ?? 0, locationKey: listCache?.locationKey ?? '',
    }
  }, [books, totalCount, readingStatuses, hasMore])
  useLayoutEffect(() => {
    const main = bodyRef.current?.closest('main') ?? null
    // Back/forward to the entry the list was left from: same place as before.
    if (seed && navigationType === 'POP' && seed.locationKey === location.key && main) main.scrollTop = seed.scrollTop
    return () => {
      if (listCache && main) { listCache.scrollTop = main.scrollTop; listCache.locationKey = locationKeyRef.current }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Reset focused index when book list changes
  useEffect(() => { setFocusedIndex(null) }, [books])

  const loadingMoreRef = useRef(false)
  useEffect(() => { loadingMoreRef.current = loadingMore }, [loadingMore])
  const loadingRef = useRef(false)
  useEffect(() => { loadingRef.current = loading }, [loading])
  const refreshingRef = useRef(false)
  useEffect(() => { refreshingRef.current = refreshing }, [refreshing])

  const observerInstanceRef = useRef<IntersectionObserver | null>(null)
  const sentinelCallback = useCallback((node: HTMLDivElement | null) => {
    if (observerInstanceRef.current) {
      observerInstanceRef.current.disconnect()
      observerInstanceRef.current = null
    }
    sentinelRef.current = node
    if (!node) return
    const observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting && !loadingMoreRef.current && !loadingRef.current && !refreshingRef.current && hasMoreRef.current) {
        loadBooks(false)
      }
    }, { rootMargin: '1500px' })
    observer.observe(node)
    observerInstanceRef.current = observer
  }, [loadBooks])

  // The grid's covers load lazily as they near the screen; these load the
  // ones further down in idle time, so they are there before the scroll is.
  // On Home (which loads the library list too) the first screens' worth, so
  // the first switch to All Books has them.
  useEffect(() => {
    if (tab === 'books' || tab === 'home') {
      const urls: string[] = []
      for (const b of tab === 'home' ? books.slice(0, 30) : books) {
        if (b.cover_path) urls.push(coverSrc(b.id, { w: 400, v: b.updated_at }))
        if (groupActive) for (const id of (b.stack_cover_ids ?? []).slice(0, 2)) urls.push(coverSrc(id, { w: 400 }))
      }
      return preloadImages(urls)
    }
    if (tab === 'series') {
      return preloadImages(seriesList.slice(0, 120).flatMap(s => s.cover_book_id ? [coverSrc(s.cover_book_id, { w: 400 })] : []))
    }
  }, [tab, books, seriesList, groupActive])

  function loadFacets() { api.get<Facets>('/books/facets').then(setFacets).catch(() => {}) }
  useEffect(() => { loadFacets() }, [])

  // Mirror groupActive into a ref so the keydown handler (bound once) sees it
  const groupActiveRef = useRef(false)
  useEffect(() => { groupActiveRef.current = groupActive }, [groupActive])


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
            const b = booksRef.current[prev]
            if (groupActiveRef.current && b.series) {
              navigate(`/?tab=series&series_detail=${encodeURIComponent(b.series)}`)
            } else {
              navigate(`/books/${b.id}`)
            }
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
    api.get<SeriesItem[]>('/books/series').then(setSeriesList).catch(() => {})
  }, [])

  // ── Filter chip helpers ───────────────────────────────────────────────────
  const filterLibraryChipName = libraries.find(l => l.id === filterLibrary)?.name ?? t`Library`
  const filterFormatUpper = filterFormat.toUpperCase()
  const filterLanguageLabel = facets.languages.find(l => l.code === filterLanguage)?.label ?? filterLanguage
  const filterStatusLabel = READING_STATUS_LABELS[filterReadingStatus] ? i18n._(READING_STATUS_LABELS[filterReadingStatus]) : filterReadingStatus
  const filterMissingLabel = MISSING_LABELS[filterMissing] ? i18n._(MISSING_LABELS[filterMissing]) : filterMissing
  const filterUploaderName = userList.find(u => u.id === filterAddedBy)?.username ?? filterAddedBy
  const activeFilterChips: { label: string; key: string }[] = [
    ...(filterLibrary ? [{ label: t`Library: ${filterLibraryChipName}`, key: 'library_id' }] : []),
    ...(filterSeries ? [{ label: t`Series: ${filterSeries}`, key: 'series' }] : []),
    ...(filterNoSeries ? [{ label: t`No Series`, key: 'no_series' }] : []),
    ...(filterAuthor ? [{ label: t`Author: ${filterAuthor}`, key: 'author' }] : []),
    ...(filterTag ? [{ label: t`Tag: ${filterTag}`, key: 'tag' }] : []),
    ...(filterFormat ? [{ label: t`Format: ${filterFormatUpper}`, key: 'format' }] : []),
    ...(filterLanguage ? [{ label: t`Language: ${filterLanguageLabel}`, key: 'language' }] : []),
    ...(filterReadingStatus ? [{ label: t`Status: ${filterStatusLabel}`, key: 'reading_status' }] : []),
    ...(filterMinRating ? [{ label: filterMinRating === 5 ? t`Rated: 5 stars` : t`Rated: ${filterMinRating}+ stars`, key: 'min_rating' }] : []),
    ...(filterMissing ? [{ label: t`Missing: ${filterMissingLabel}`, key: 'missing' }] : []),
    ...(filterOwnership === 'mine' ? [{ label: t`My Books`, key: 'ownership' }] : filterOwnership === 'shared' ? [{ label: t`Shared Library`, key: 'ownership' }] : []),
    ...(filterAddedBy ? [{ label: t`Uploader: ${filterUploaderName}`, key: 'added_by' }] : []),
    ...(onDevices.length ? [{ label: (() => { const names = onDevices.join(', '); return t`On ${names}` })(), key: 'on_device' }] : []),
    ...(notOnDevices.length ? [{ label: (() => { const names = notOnDevices.join(', '); return t`Not on ${names}` })(), key: 'not_on_device' }] : []),
  ]

  // Params to pass to SaveFilterButton (excludes library_id — that belongs in sidebar)
  const saveableParams: Record<string, string> = {}
  if (search) saveableParams.q = search
  if (filterSeries) saveableParams.series = filterSeries
  if (filterAuthor) saveableParams.author = filterAuthor
  if (filterTag) saveableParams.tag = filterTag
  if (filterFormat) saveableParams.format = filterFormat
  if (filterLanguage) saveableParams.language = filterLanguage
  if (filterOnDevice) saveableParams.on_device = filterOnDevice
  if (filterNotOnDevice) saveableParams.not_on_device = filterNotOnDevice

  // Grid columns flow from the spring-smoothed cover size; card typography
  // follows the slider value. The grid/list switch swaps in place: the old
  // view-fade faded the new view up from nothing inside the painted panel.
  // List rows sit on hairlines, no boxes.
  const cardView: ViewMode = view === 'list' ? 'list' : gridSize < 150 ? 'small' : 'large'
  const gridClass = view === 'list'
    // eslint-disable-next-line lingui/no-unlocalized-strings -- Tailwind classes
    ? 'flex flex-col divide-y divide-border/70'
    : cn(
        'grid transition-[gap] duration-300 ease-out',
        gridSize < 150 ? 'gap-x-3 gap-y-5' : 'gap-x-5 gap-y-7'
      )
  // min(size, 42vw) keeps phones at two columns regardless of the slider —
  // without it a 180px minimum fits only one track on a 390px screen
  const gridStyle = view === 'list'
    ? undefined
    // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS grid template
    : { gridTemplateColumns: `repeat(auto-fill, minmax(min(${gridSize}px, 42vw), 1fr))` }

  // Active library name for heading
  const activeLibraryName = filterLibrary ? libraries.find(l => l.id === filterLibrary)?.name : null

  return (
    <ShellFrame
      sidebar={
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
      }
      header={
        <AppHeader
          onMenuClick={() => setMobileSidebarOpen(true)}
          search={
            <HeaderSearch
              value={searchInput}
              onChange={handleSearchInput}
              onClear={() => { setSearchInput(''); setFilter('q', '') }}
              inputRef={searchInputRef}
            />
          }
          onUploadClick={isMember(user) ? () => setUploadModalOpen(true) : undefined}
        />
      }
    >

      {shareSeries && (
        <ShareModal
          title={shareSeries}
          endpoint={`/series/${encodeURIComponent(shareSeries)}/share`}
          noun="series"
          onClose={() => setShareSeries(null)}
        />
      )}
      <UploadModal
        isOpen={uploadModalOpen}
        onClose={() => setUploadModalOpen(false)}
        onDone={() => { loadBooks(); loadFacets() }}
        onWishMatches={(wishIds) => {
          const n = wishIds.length
          toast.info(plural(n, {
            one: 'This upload satisfies # wish — review in Admin > Wishlist',
            other: 'This upload satisfies # wishes — review in Admin > Wishlist',
          }))
        }}
      />

      {manageSeriesOpen && expandedSeries && (
        <ManageSeriesModal
          seriesName={expandedSeries}
          volumes={
            seriesDetail
              ? seriesDetail.books
                  .map(b => b.series_index)
                  .filter((n): n is number => n != null)
              : []
          }
          onClose={() => setManageSeriesOpen(false)}
          onSaved={() => {
            // Refresh meta map and arcs for the open series
            if (expandedSeries) {
              api.get<SeriesMeta>(`/series/${encodeURIComponent(expandedSeries)}/meta`)
                .then(m => setSeriesMetaMap(prev => ({ ...prev, [expandedSeries]: m.status })))
                .catch(() => {})
              api.get<Arc[]>(`/series/${encodeURIComponent(expandedSeries)}/arcs`)
                .then(setSeriesArcs)
                .catch(() => {})
            }
          }}
        />
      )}

      {/* ── Body: the dashboard tabs. The frame's panel scrolls; this wrapper
            only carries the page padding (the shared PageBody comes with the
            per-tab rebuilds). ────────────────────────────────────────────── */}
        <div ref={bodyRef}>
          {/* Every tab brings its own PageBody + PageHeader now; the library
              name heading only had a job before the Library tab had a header. */}
          {tab === 'home' && activeLibraryName && (
            <h2 className="text-lg font-semibold mb-3">{activeLibraryName}</h2>
          )}

          {tab === 'home' ? (
            <HomeTab
              onBrowse={() => setTab('books')}
              onSeeAllRecent={() => { persistSort('added_at'); persistOrder('desc'); setTab('books') }}
            />
          ) : tab === 'series' ? (
            /* ── Series tab: the series grid, or one series' detail ──────────── */
            <PageBody width="wide">
            {expandedSeries ? (
              seriesDetailLoading || !seriesDetail ? (
                <>
                  <PageHeader
                    eyebrow={
                      <button
                        type="button"
                        onClick={() => { setExpandedSeries(null); setSeriesDetail(null) }}
                        className="inline-flex items-center gap-1 uppercase hover:text-foreground transition-colors"
                      >
                        <ArrowLeft className="w-3.5 h-3.5" />
                        <Trans>Series</Trans>
                      </button>
                    }
                    title={expandedSeries === '__unserialized__' ? t`No Series` : expandedSeries}
                  />
                  <div className="flex justify-center py-16">
                    <Loader2 className="w-5 h-5 animate-spin text-primary" />
                  </div>
                </>
              ) : (() => {
                const isUnserialized = seriesDetail.name === '__unserialized__'
                const total = seriesDetail.books.length
                const readCount = seriesDetail.books.filter(b => b.reading_status === 'read').length
                const readingCount = seriesDetail.books.filter(b => b.reading_status === 'reading').length
                const unreadCount = total - readCount - readingCount
                const readPct = total ? (readCount / total) * 100 : 0
                const readingPct = total ? (readingCount / total) * 100 : 0
                const heroCoverId = seriesList.find(s => s.name === seriesDetail.name)?.cover_book_id
                  ?? seriesDetail.books[0]?.id ?? null
                const sections = groupVolumesByArc(seriesDetail.books, seriesArcs)
                const hasArcs = seriesArcs.length > 0
                const coverage = seriesDetail.books.map(b => ({
                  id: b.id, title: b.title, series_index: b.series_index, cover_path: b.cover_path,
                }))
                const continueBook = getContinueBook(seriesDetail.books)
                return (
                  <>
                  {/* ── Hero: cover on the left, the header and the series' state on the right ── */}
                  <section className="grid gap-8 lg:grid-cols-[220px_minmax(0,1fr)] items-start">
                    <div className="w-[160px] sm:w-[220px] mx-auto lg:mx-0 rounded-[12px] overflow-hidden bg-muted" style={{ boxShadow: 'var(--cover-bloom-hero)' }}>
                      <div className="relative aspect-[2/3]">
                        <CoverImage
                          src={heroCoverId != null ? coverSrc(heroCoverId, { w: 400 }) : null}
                          alt=""
                          iconClassName="w-10 h-10"
                        />
                      </div>
                    </div>

                    <div className="min-w-0 flex flex-col gap-5">
                      <PageHeader
                        eyebrow={
                          <span className="inline-flex items-center gap-1.5 flex-wrap">
                            <button
                              type="button"
                              onClick={() => { setExpandedSeries(null); setSeriesDetail(null) }}
                              className="inline-flex items-center gap-1 uppercase hover:text-foreground transition-colors"
                            >
                              <ArrowLeft className="w-3.5 h-3.5" />
                              <Trans>Series</Trans>
                            </button>
                            <span aria-hidden>&middot;</span>
                            <span><Plural value={total} one="# volume" other="# volumes" /></span>
                          </span>
                        }
                        title={isUnserialized ? t`No Series` : seriesDetail.name}
                        subtitle={seriesDetail.author ?? undefined}
                        actions={
                          <div className="flex flex-wrap items-center gap-1">
                            {!isUnserialized && (
                              <Pill
                                variant="quiet"
                                onClick={() => setShareSeries(seriesDetail.name)}
                                title={t`Share this series — a public metadata-only page`}
                              >
                                <Share2 className="w-4 h-4" />
                                <Trans>Share</Trans>
                              </Pill>
                            )}
                            {isAdmin(user) && (
                              <Pill variant="quiet" onClick={() => setManageSeriesOpen(true)} title={t`Manage series`}>
                                <Settings2 className="w-4 h-4" />
                                <Trans>Manage</Trans>
                              </Pill>
                            )}
                            <SeriesFollowButton seriesName={seriesDetail.name} />
                          </div>
                        }
                      />

                      {/* Status and arcs */}
                      {(seriesMetaMap[seriesDetail.name] || hasArcs) && (
                        <div className="flex flex-wrap items-center gap-2">
                          <SeriesStatusBadge status={seriesMetaMap[seriesDetail.name]} large />
                          {sections.filter(sec => sec.arcId != null).map(sec => (
                            <button
                              key={sec.arcId}
                              type="button"
                              onClick={() => document.getElementById(`series-arc-${sec.arcId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                              className="h-7 px-3 rounded-full bg-secondary text-[13px] text-foreground hover:bg-muted transition-colors"
                            >
                              {sec.name}
                            </button>
                          ))}
                        </div>
                      )}

                      <SeriesRating seriesName={seriesDetail.name} isUnserialized={isUnserialized} />

                      {/* Progress through the series */}
                      <div>
                        <div className="flex items-center gap-3">
                          <div
                            className="h-[6px] flex-1 rounded-full overflow-hidden flex"
                            style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--background))' }}
                          >
                            <div className="h-full bg-primary" style={{ width: `${readPct}%` }} />
                            <div className="h-full bg-primary/50" style={{ width: `${readingPct}%` }} />
                          </div>
                          <span className="text-[17px] font-bold tabular-nums text-foreground">{Math.round(readPct)}%</span>
                        </div>
                        <p className="mt-1.5 text-[13px] text-muted-foreground">
                          <Trans>{readCount} read &middot; {readingCount} reading &middot; {unreadCount} unread</Trans>
                        </p>
                        {seriesDetail.backlog && seriesDetail.backlog.estimated > 0 && (
                          <p className="mt-1 text-[13px] text-muted-foreground" title={describePace(seriesDetail.backlog.pace, seriesDetail.backlog.by_type.some(t => t.type_avg > 0) ? 'type_avg' : 'words')}>
                            {(() => { const dur = formatEstimateHours(seriesDetail.backlog.seconds); return (
                              <Trans><span className="text-foreground font-medium tabular-nums">{dur}</span> left in this series</Trans>
                            ) })()}
                            {seriesDetail.backlog.days != null && (() => { const d = formatEstimateDays(seriesDetail.backlog.days); return <Trans> &middot; {d} at your pace</Trans> })()}
                            {seriesDetail.backlog.unestimated > 0 && (() => { const n = seriesDetail.backlog.unestimated; return <Trans> &middot; {n} not estimated</Trans> })()}
                          </p>
                        )}
                        {devices.some(d => seriesDetail.books.some(b => devicesFor([d], b.id).length > 0)) && (
                          <p className="mt-1 text-[13px] text-muted-foreground flex flex-wrap gap-x-4 gap-y-1">
                            {devices.map(d => {
                              const n = seriesDetail.books.filter(b => devicesFor([d], b.id).length > 0).length
                              if (n === 0) return null
                              const name = d.name
                              return (
                                <span key={d.name} className="inline-flex items-center gap-1">
                                  <Tablet className="w-3.5 h-3.5" />
                                  <Trans>On {name}: <span className="text-foreground font-medium tabular-nums">{n}</span> of {total}</Trans>
                                </span>
                              )
                            })}
                          </p>
                        )}
                      </div>

                      {seriesDetail.description && (
                        <div className="max-w-[680px]">
                          <p className={cn(
                            'text-[15px] text-foreground/80 leading-relaxed whitespace-pre-line',
                            !seriesDescExpanded && 'line-clamp-3'
                          )}>
                            {seriesDetail.description}
                          </p>
                          {seriesDetail.description.length > 240 && (
                            <button
                              onClick={() => setSeriesDescExpanded(v => !v)}
                              className="text-[14px] font-medium text-primary hover:underline underline-offset-4 mt-1"
                            >
                              {seriesDescExpanded ? t`Show less` : t`Show more`}
                            </button>
                          )}
                        </div>
                      )}

                      {/* Which volumes the library has, gaps included */}
                      {!isUnserialized && coverage.some(v => v.series_index != null) && (
                        <SeriesCoverageStrip coverage={coverage} />
                      )}

                      <div className="flex flex-wrap items-center gap-2">
                        {continueBook && (() => {
                          const volIdx = continueBook.series_index
                          const volLabel = volIdx != null ? t`Vol. ${volIdx}` : continueBook.title
                          const isResuming = continueBook.reading_status === 'reading'
                          return (
                            <Pill variant="primary" size="lg" onClick={() => navigate(`/reader/${continueBook.id}`)}>
                              <Play className="w-4 h-4 fill-current" />
                              {isResuming ? t`Resume ${volLabel}` : t`Start ${volLabel}`}
                            </Pill>
                          )
                        })()}
                        <Pill
                          size="lg"
                          onClick={() => markAllRead(seriesDetail.books)}
                          disabled={markingAllRead || seriesDetail.books.every(b => b.reading_status === 'read')}
                        >
                          {markingAllRead ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCheck className="w-4 h-4" />}
                          <Trans>Mark all read</Trans>
                        </Pill>
                      </div>
                    </div>
                  </section>

                  {/* Per-series reading stats */}
                  <SeriesReadingStats seriesName={seriesDetail.name} />

                  {/* Volume grid — grouped by arc when arcs exist */}
                  {sections.map((section, si) => (
                    <section key={si} id={section.arcId != null ? `series-arc-${section.arcId}` : undefined} className="scroll-mt-6">
                      {hasArcs ? (
                        <>
                          <SectionTitle size={20}>{section.label}</SectionTitle>
                          {section.description && (
                            <p className="mt-2 text-[14px] text-muted-foreground max-w-[70ch]">{section.description}</p>
                          )}
                        </>
                      ) : (
                        <SectionTitle size={20}><Trans>Volumes</Trans></SectionTitle>
                      )}
                      <div className="mt-4 grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10 gap-x-3 gap-y-4">
                        {section.volumes.map(vol => (
                          <div
                            key={vol.id}
                            onClick={() => navigate(`/books/${vol.id}`)}
                            title={vol.title}
                            className="group relative cursor-pointer touch-feedback"
                          >
                            <div
                              className="relative aspect-[2/3] w-full rounded-[8px] overflow-hidden bg-muted transition-transform duration-150 group-hover:-translate-y-0.5"
                              style={{ boxShadow: 'var(--cover-bloom-fan)' }}
                            >
                              <CoverImage
                                src={vol.cover_path ? coverSrc(vol.id, { w: 400, v: vol.updated_at }) : null}
                                alt={vol.title}
                                iconClassName="w-4 h-4"
                              />
                              {/* Quick-read play button — top-left corner on hover */}
                              <button
                                type="button"
                                onClick={e => { e.stopPropagation(); navigate(`/reader/${vol.id}`) }}
                                className="absolute top-1 left-1 z-10 w-7 h-7 rounded-full bg-background/75 backdrop-blur-sm text-foreground hover:bg-background flex items-center justify-center opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                title={t({ message: 'Read', context: 'action' })}
                                aria-label={t({ message: 'Read', context: 'action' })}
                              >
                                <Play className="w-3 h-3 fill-current ml-px" />
                              </button>
                              {/* Quick look — top-right corner on hover; the status dot steps aside */}
                              <button
                                type="button"
                                onClick={e => { e.stopPropagation(); setQuickLookId(vol.id) }}
                                className="absolute top-1 right-1 z-10 w-7 h-7 rounded-full bg-background/75 backdrop-blur-sm text-foreground hover:bg-background flex items-center justify-center opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto group-focus-within:opacity-100 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                title={t`Quick look`}
                                aria-label={t`Quick look`}
                              >
                                <Eye className="w-3.5 h-3.5" />
                              </button>
                              {/* Status dot */}
                              {vol.reading_status !== 'unread' && (
                                <div className={cn(
                                  'absolute top-1.5 right-1.5 w-2.5 h-2.5 rounded-full ring-2 ring-background transition-opacity group-hover:opacity-0 group-focus-within:opacity-0',
                                  vol.reading_status === 'read' ? 'bg-primary' : 'bg-primary/60'
                                )} />
                              )}
                              {/* Volume bar: number left, on-device glyph right */}
                              {(() => {
                                const idx = vol.series_index
                                const on = devicesFor(devices, vol.id)
                                if (idx == null && on.length === 0) return null
                                const names = on.map(d => d.name).join(', ')
                                return (
                                  <div className="absolute bottom-0 inset-x-0 bg-black/60 px-1.5 py-1 flex items-center justify-between gap-1">
                                    <span className="text-[10px] font-bold text-white leading-none">
                                      {idx != null && <Trans>Vol. {idx}</Trans>}
                                    </span>
                                    {on.length > 0 && (
                                      <span title={t`On ${names}`} aria-label={t`On ${names}`}>
                                        <Tablet className="w-3 h-3 text-white" />
                                      </span>
                                    )}
                                  </div>
                                )
                              })()}
                            </div>
                          </div>
                        ))}
                      </div>
                    </section>
                  ))}
                  </>
                )
              })()
            ) : (
              <>
              <PageHeader
                eyebrow={seriesLoading ? undefined : (() => {
                  const n = seriesList.filter(s => s.name !== '__unserialized__').length
                  return <Plural value={n} one="# series" other="# series" />
                })()}
                title={<Trans>Series</Trans>}
              />
              {seriesLoading ? (
                <div className="flex justify-center py-24">
                  <BookAnimation variant="refresh" className="block w-10 h-10 text-primary" />
                </div>
              ) : seriesList.length === 0 ? (
                <div className="flex flex-col items-start gap-4 py-10">
                  <div>
                    <p className="text-[17px] font-medium text-foreground"><Trans>No series yet</Trans></p>
                    <p className="text-[15px] text-muted-foreground mt-1"><Trans>Books with series metadata show up here, grouped by series.</Trans></p>
                  </div>
                  <Pill onClick={() => setTab('books')}><Trans>Show all books</Trans></Pill>
                </div>
              ) : (
                /* Series card grid */
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-x-5 gap-y-8">
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
                        className="group flex flex-col text-left min-w-0 touch-feedback focus:outline-none"
                      >
                        <div
                          className="relative aspect-[2/3] rounded-[8px] bg-muted overflow-hidden transition-transform duration-200 group-hover:-translate-y-0.5 group-focus-visible:ring-2 group-focus-visible:ring-ring"
                          style={{ boxShadow: 'var(--cover-bloom-fan)' }}
                        >
                          <CoverImage
                            src={s.cover_book_id ? coverSrc(s.cover_book_id, { w: 400 }) : null}
                            alt={isUnserialized ? t`Unserialized` : s.name}
                          />
                          <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/70 to-transparent px-2.5 pt-8 pb-2">
                            <span className="text-[12px] font-semibold text-white/90">
                              <Plural value={s.book_count} one="# book" other="# books" />
                            </span>
                          </div>
                        </div>
                        <div className="pt-2.5 flex flex-col gap-0.5 min-w-0">
                          <div className="flex items-start gap-1.5 flex-wrap">
                            <span className="text-[14px] font-medium text-foreground leading-snug line-clamp-2 group-hover:text-primary transition-colors">
                              {isUnserialized ? t`No Series` : s.name}
                            </span>
                            {!isUnserialized && <SeriesStatusBadge status={seriesMetaMap[s.name]} />}
                          </div>
                          {!isUnserialized && s.author && <span className="text-[13px] text-muted-foreground truncate">{s.author}</span>}
                          {!isUnserialized && s.rating != null && (
                            <StarRating value={s.rating} readOnly starClassName="w-3 h-3" className="mt-0.5" />
                          )}
                          {!isUnserialized && s.description && (
                            <p className="text-[13px] text-muted-foreground leading-snug line-clamp-2 mt-0.5">{s.description}</p>
                          )}
                          {isUnserialized && (
                            <p className="text-[13px] text-muted-foreground leading-snug mt-0.5"><Trans>Books without a series</Trans></p>
                          )}
                          {!isUnserialized && s.book_count > 0 && (s.read_count > 0 || s.reading_count > 0) && (
                            <div
                              className="mt-2 h-[3px] rounded-full overflow-hidden flex"
                              style={{ background: 'color-mix(in oklch, var(--primary) 25%, var(--background))' }}
                            >
                              <div className="h-full bg-primary" style={{ width: `${(s.read_count / s.book_count) * 100}%` }} />
                              <div className="h-full bg-primary/50" style={{ width: `${(s.reading_count / s.book_count) * 100}%` }} />
                            </div>
                          )}
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}
              </>
            )}
            </PageBody>
          ) : (
          /* ── Library tab: header with the toolbar, filters, selection, grid ── */
          <PageBody width="wide">
          <div className="flex flex-col gap-4">
          <PageHeader
            eyebrow={loading
              ? '…'
              : (() => {
                  // Grouped rows are series stacks + standalones — "titles", not the
                  // ambiguous "entries" (which read like a book count and never matched it).
                  const shown = books.length
                  if (totalCount !== null && totalCount > shown) {
                    return groupActive
                      ? plural(totalCount, { one: `${shown} of # title`, other: `${shown} of # titles` })
                      : plural(totalCount, { one: `${shown} of # book`, other: `${shown} of # books` })
                  }
                  return groupActive
                    ? plural(shown, { one: '# title', other: '# titles' })
                    : plural(shown, { one: '# book', other: '# books' })
                })()}
            title={activeLibraryName ?? <Trans>Library</Trans>}
            actions={
              /* Capped at the viewport so the row wraps on phones instead of
                 running off the side. */
              <div className="flex flex-wrap items-center sm:justify-end gap-1 max-w-[calc(100vw-2.5rem)]">
                <SortMenu
                  fields={(Object.keys(SORT_LABELS) as SortField[]).map(f => ({ id: f, label: i18n._(SORT_LABELS[f]) }))}
                  value={sort}
                  order={order}
                  onPick={toggleSort}
                />

                <Pill
                  variant={filterOpen || activeFilterChips.length > 0 ? 'secondary' : 'quiet'}
                  onClick={() => setFilterOpen(o => !o)}
                  aria-expanded={filterOpen}
                  aria-label={t`Filters`}
                  title={t`Filters`}
                  className="px-3"
                >
                  <SlidersHorizontal className="w-4 h-4" />
                  <span className="hidden lg:inline"><Trans>Filters</Trans></span>
                  {/* Counts filter chips only: a search alone (hasFilters) is not
                      a filter and used to show a "0" badge. */}
                  {activeFilterChips.length > 0 && (
                    <span className="min-w-5 h-5 px-1.5 grid place-items-center rounded-full bg-foreground text-background text-[11px] font-semibold tabular-nums">
                      {activeFilterChips.length}
                    </span>
                  )}
                </Pill>

                {/* Group by series toggle */}
                <Pill
                  variant={groupBySeries ? 'secondary' : 'quiet'}
                  onClick={() => persistGroup(!groupBySeries)}
                  title={groupBySeries ? t`Show individual volumes` : t`Group volumes by series`}
                  aria-label={t`Group by series`}
                  aria-pressed={groupBySeries}
                  className="px-3"
                >
                  <Layers className="w-4 h-4" />
                  <span className="hidden xl:inline"><Trans>Group series</Trans></span>
                </Pill>

                {/* Select mode toggle — selection operates on individual books, hidden while grouped */}
                {books.length > 0 && !groupActive && (
                  <>
                    <Pill
                      variant={selectionMode ? 'secondary' : 'quiet'}
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
                      className="px-3"
                      aria-label={selectionMode ? (selected.size > 0 ? t`Deselect all` : t`Select all`) : t`Select`}
                      title={selectionMode ? (selected.size > 0 ? t`Deselect all` : t`Select all`) : t`Select`}
                    >
                      {selectionMode
                        ? selected.size > 0
                          ? <><XSquare className="w-4 h-4" /><span className="hidden xl:inline"><Trans>Deselect all</Trans></span></>
                          : <><CheckSquare className="w-4 h-4" /><span className="hidden xl:inline"><Trans>Select all</Trans></span></>
                        : <><CheckSquare className="w-4 h-4" /><span className="hidden xl:inline"><Trans>Select</Trans></span></>
                      }
                    </Pill>
                    {selectionMode && (
                      <Pill
                        variant="quiet"
                        icon
                        onClick={exitSelectionMode}
                        title={t`Exit selection mode`}
                        aria-label={t`Exit selection mode`}
                      >
                        <X className="w-4 h-4" />
                      </Pill>
                    )}
                  </>
                )}

                {/* Cover size (grid only) + grid / list */}
                <div className="flex items-center">
                  <input
                    type="range"
                    min={GRID_SIZE_MIN}
                    max={GRID_SIZE_MAX}
                    step={10}
                    value={gridSize}
                    onChange={e => persistGridSize(Number(e.target.value))}
                    title={t`Cover size`}
                    aria-label={t`Cover size`}
                    tabIndex={view === 'grid' ? 0 : -1}
                    className={cn(
                      'cover-slider transition-all duration-200',
                      view === 'grid'
                        ? 'w-20 sm:w-24 opacity-100 mx-2'
                        : 'w-0 opacity-0 mx-0 pointer-events-none'
                    )}
                    style={{
                      background: `linear-gradient(to right, var(--primary) ${((gridSize - GRID_SIZE_MIN) / (GRID_SIZE_MAX - GRID_SIZE_MIN)) * 100}%, var(--input) 0)`,
                    }}
                  />
                  {([
                    { mode: 'grid' as ViewPref, Icon: LayoutGrid, title: t`Grid view` },
                    { mode: 'list' as ViewPref, Icon: List, title: t`List view` },
                  ]).map(({ mode, Icon, title }) => (
                    <Pill
                      key={mode}
                      icon
                      variant={view === mode ? 'secondary' : 'quiet'}
                      onClick={() => persistView(mode)}
                      title={title}
                      aria-label={title}
                      aria-pressed={view === mode}
                    >
                      <Icon className="w-4 h-4" />
                    </Pill>
                  ))}
                </div>
              </div>
            }
          />

          {/* ── Active filters as chips, Add shelf, Clear all ─────────────── */}
          {(activeFilterChips.length > 0 || hasFilters) && (
            <div className="flex items-center gap-2 flex-wrap">
              {activeFilterChips.map(f => (
                <span key={f.key} className="inline-flex items-center gap-1 h-8 pl-3.5 pr-1.5 rounded-full bg-secondary text-[13px] font-medium text-foreground">
                  {f.label}
                  <button
                    type="button"
                    onClick={() => setFilter(f.key, '')}
                    aria-label={t`Remove filter`}
                    className="w-6 h-6 grid place-items-center rounded-full text-muted-foreground hover:text-destructive hover:bg-background/60 transition-colors"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </span>
              ))}

              {/* Save filter button — only when content filters active (not library) */}
              <SaveFilterButton params={saveableParams} onSaved={loadSavedFilters} />

              <Pill variant="quiet" onClick={clearFilters}>
                <Trans>Clear all</Trans>
              </Pill>
            </div>
          )}

          {/* ── Filter panel ──────────────────────────────────────────────── */}
          {filterOpen && (() => {
            /* eslint-disable lingui/no-unlocalized-strings -- Tailwind classes */
            const opt = (active: boolean) => cn(
              'h-8 px-3.5 rounded-full text-[13px] font-medium transition-colors inline-flex items-center gap-1',
              active ? 'bg-foreground text-background' : 'bg-secondary text-muted-foreground hover:text-foreground',
            )
            const rowLabel = 'text-[13px] text-muted-foreground w-20 shrink-0'
            const selectCls = 'h-9 text-[14px] rounded-lg border border-border bg-background px-2.5 text-foreground focus:outline-none focus:ring-2 focus:ring-ring'
            /* eslint-enable lingui/no-unlocalized-strings */
            return (
            <div className="rounded-[var(--radius-panel)] bg-card p-5 flex flex-col gap-4">
              <div className="flex items-center justify-between sm:hidden">
                <span className="text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground"><Trans>Filters</Trans></span>
                <Pill variant="quiet" icon onClick={() => setFilterOpen(false)} aria-label={t`Close`}>
                  <X className="w-4 h-4" />
                </Pill>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <FilterSelect label={t`Series`} value={filterSeries} options={facets.series} onChange={v => setFilter('series', v)} />
                <FilterSelect label={t`Author`} value={filterAuthor} options={facets.authors} onChange={v => setFilter('author', v)} />
                <FilterSelect label={t`Tag`} value={filterTag} options={facets.tags} onChange={v => setFilter('tag', v)} />
                <FilterSelect label={t`Format`} value={filterFormat} options={facets.formats.map(f => f.toUpperCase())} onChange={v => setFilter('format', v.toLowerCase())} />
                {facets.languages.length > 1 && (
                  <FilterSelect label={t`Language`} value={filterLanguage} options={facets.languages.map(l => ({ value: l.code, label: l.label }))} onChange={v => setFilter('language', v)} />
                )}
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <span className={rowLabel}><Trans>Status</Trans></span>
                {(['', 'unread', 'want_to_read', 'reading', 'read', 'shelved'] as const).map(s => (
                  <button key={s} type="button" onClick={() => setFilter('reading_status', s)} className={opt(filterReadingStatus === s)}>
                    {s === '' ? t`All` : i18n._(READING_STATUS_LABELS[s])}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <span className={rowLabel}><Trans>Rating</Trans></span>
                {([
                  { value: '', label: t`All`, starless: true },
                  { value: '1', label: t`Rated`, starless: true },
                  { value: '3', label: '3+', starless: false },
                  { value: '4', label: '4+', starless: false },
                  { value: '5', label: '5', starless: false },
                ]).map(({ value, label, starless }) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setFilter('min_rating', value)}
                    className={opt((filterMinRating ? String(filterMinRating) : '') === value)}
                  >
                    {!starless && <Star className="w-3 h-3 fill-current" />}
                    {label}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <span className={rowLabel}><Trans>Missing</Trans></span>
                {([
                  { value: '', label: t`None` },
                  { value: 'cover', label: t`Cover` },
                  { value: 'description', label: t`Description` },
                  { value: 'author', label: t`Author` },
                  { value: 'series', label: t`Series` },
                  { value: 'any', label: t`Any` },
                ]).map(({ value, label }) => (
                  <button key={value} type="button" onClick={() => setFilter('missing', value)} className={opt(filterMissing === value)}>
                    {label}
                  </button>
                ))}
              </div>
              {/* Device filter: one pill per device, click cycles off / on / not on */}
              {devices.length > 0 && (
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={rowLabel}><Trans>Devices</Trans></span>
                  {devices.map(d => {
                    const name = d.name
                    const state = onDevices.includes(name) ? 'on' : notOnDevices.includes(name) ? 'not' : 'off'
                    return (
                      <button
                        key={name}
                        type="button"
                        onClick={() => cycleDeviceFilter(name)}
                        title={state === 'off' ? t`Click: only books on ${name}`
                          : state === 'on' ? t`Click: only books not on ${name}`
                          : t`Click: ${name} does not matter`}
                        className={cn(
                          opt(state === 'on'),
                          state === 'not' && 'bg-destructive/10 text-destructive hover:text-destructive',
                        )}
                      >
                        <Tablet className="w-3 h-3" />
                        {state === 'not' ? t`Not on ${name}` : name}
                      </button>
                    )
                  })}
                </div>
              )}
              {/* Ownership filter — members only (not admin) */}
              {isMember(user) && !isAdmin(user) && (
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={rowLabel}><Trans>Books</Trans></span>
                  {([
                    { value: '', label: t`All` },
                    { value: 'mine', label: t`My Books` },
                    { value: 'shared', label: t`Shared Library` },
                  ] as const).map(({ value, label }) => (
                    <button key={value} type="button" onClick={() => setFilter('ownership', value)} className={opt(filterOwnership === value)}>
                      {label}
                    </button>
                  ))}
                </div>
              )}
              {/* Uploader filter — admins only */}
              {isAdmin(user) && userList.length > 0 && (
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={rowLabel}><Trans>Uploader</Trans></span>
                  <select
                    value={filterAddedBy ?? ''}
                    onChange={e => setFilter('added_by', e.target.value)}
                    className={selectCls}
                  >
                    <option value="">{t`All users`}</option>
                    {userList.map(u => (
                      <option key={u.id} value={u.id}>{u.username} ({u.role})</option>
                    ))}
                  </select>
                </div>
              )}
              <div className="flex items-center gap-2 flex-wrap">
                <span className={rowLabel}><Trans>Type</Trans></span>
                <select
                  value={contentType}
                  onChange={e => setContentType(e.target.value)}
                  className={selectCls}
                >
                  <option value="volume">{t`Volumes`}</option>
                  <option value="chapter">{t`Chapters`}</option>
                  <option value="">{t`All`}</option>
                </select>
              </div>
            </div>
            )
          })()}

          {/* ── Bulk action bar ──────────────────────────────────────────── */}
          {selectionMode && (
            <div className="flex flex-wrap items-center gap-2 py-2.5 border-y border-border/70">
              <span className="text-[15px] font-semibold text-foreground tabular-nums mr-auto">{(() => { const n = selected.size; return t`${n} selected` })()}</span>
              <Pill onClick={bulkDownload} disabled={bulkPending || selected.size === 0}>
                <Download className="w-4 h-4" />
                <Trans>Download ZIP</Trans>
              </Pill>
              {isMember(user) && (
                <SendButton
                  variant="bulk"
                  disabled={bulkPending || selected.size === 0}
                  books={books.filter(b => selected.has(b.id)).map(b => ({ id: b.id, title: b.title, files: b.files }))}
                />
              )}
              <Pill
                onClick={() => { setBulkMetaOpen(true); api.get<Facets>('/books/facets').then(setFacets).catch(() => {}) }}
                disabled={bulkPending || selected.size === 0}
              >
                <Pencil className="w-4 h-4" />
                <Trans>Edit Metadata</Trans>
              </Pill>
              {libraries.some(l => l.can_edit) && (
                <div className="relative">
                  <Pill
                    variant="primary"
                    onClick={() => setBulkLibMenu(o => !o)}
                    disabled={bulkPending || selected.size === 0}
                  >
                    <LibraryIcon className="w-4 h-4" />
                    <Trans>Add to Library</Trans>
                  </Pill>
                  {bulkLibMenu && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setBulkLibMenu(false)} />
                      <div className="absolute right-0 top-full mt-2 z-20 bg-card border border-border/60 rounded-xl shadow-xl p-1.5 min-w-48">
                        {libraries.filter(l => l.can_edit).map(lib => (
                          <button
                            key={lib.id}
                            type="button"
                            onClick={() => bulkAddToLibrary(lib.id)}
                            className="flex items-center gap-2 w-full h-9 px-3 rounded-lg text-[14px] text-foreground hover:bg-secondary transition-colors"
                          >
                            <LibraryIcon className="w-4 h-4 text-muted-foreground shrink-0" />
                            {lib.name}
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}
              <Pill
                variant="destructive"
                onClick={() => setDeleteModalOpen(true)}
                disabled={bulkPending || selected.size === 0}
              >
                <Trash2 className="w-4 h-4" />
                <Trans>Delete</Trans>
              </Pill>
              <Pill variant="quiet" onClick={exitSelectionMode}>
                <Trans>Done</Trans>
              </Pill>
            </div>
          )}
          </div>

          {/* ── Series matching the search ───────────────────────────────── */}
          {search && !groupActive && searchSeries.length > 0 && (
            <section>
              <p className="text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground mb-3 flex items-center gap-1.5">
                <Layers className="w-3.5 h-3.5" />
                <Trans>Series</Trans>
              </p>
              <HScrollRow className="gap-2 pb-1" fadeFrom="background">
                {searchSeries.map(s => {
                  const name = s.name
                  return (
                    <button
                      key={name}
                      onClick={() => navigate(`/?tab=series&series_detail=${encodeURIComponent(name)}`)}
                      className="group flex items-center gap-3 w-72 shrink-0 p-2 pr-3 text-left rounded-xl hover:bg-secondary transition-colors"
                    >
                      <div className="relative w-12 aspect-[2/3] shrink-0 rounded-[6px] overflow-hidden bg-muted" style={{ boxShadow: 'var(--cover-bloom-fan)' }}>
                        <CoverImage
                          src={s.cover_book_id ? coverSrc(s.cover_book_id, { w: 400 }) : null}
                          alt={name}
                          iconClassName="w-4 h-4"
                        />
                      </div>
                      <div className="flex flex-col gap-0.5 min-w-0 flex-1">
                        <span className="text-[15px] font-medium text-foreground leading-tight line-clamp-2 group-hover:text-primary transition-colors">{name}</span>
                        {s.author && <span className="text-[13px] text-muted-foreground truncate">{s.author}</span>}
                        <span className="text-[13px] text-muted-foreground">
                          {(() => { const n = s.book_count; const r = s.read_count; return r > 0
                            ? <Trans>{n} books &middot; {r} read</Trans>
                            : <Plural value={n} one="# book" other="# books" /> })()}
                        </span>
                        {s.book_count > 0 && (s.read_count > 0 || s.reading_count > 0) && (
                          <div className="mt-1 h-[3px] rounded-full overflow-hidden flex" style={{ background: 'color-mix(in oklch, var(--primary) 25%, var(--background))' }}>
                            <div className="h-full bg-primary" style={{ width: `${(s.read_count / s.book_count) * 100}%` }} />
                            <div className="h-full bg-primary/50" style={{ width: `${(s.reading_count / s.book_count) * 100}%` }} />
                          </div>
                        )}
                      </div>
                    </button>
                  )
                })}
              </HScrollRow>
            </section>
          )}

          {/* ── Grid / list, with the search result count above ─────────── */}
          <div>
          {search && !loading && totalCount !== null && (
            <p className="text-[14px] text-muted-foreground mb-4">
              {totalCount === 0
                ? t`No results for "${search}"`
                : plural(totalCount, { one: `# result for "${search}"`, other: `# results for "${search}"` })}
            </p>
          )}

          {loading ? (
            <div className="flex justify-center py-24">
              <BookAnimation variant="refresh" className="block w-12 h-12 text-primary" />
            </div>
          ) : books.length === 0 ? (
            <div className="flex flex-col items-start gap-4 py-10">
              {search ? (
                <>
                  <div>
                    <p className="text-[17px] font-medium text-foreground"><Trans>No results found</Trans></p>
                    <p className="text-[15px] text-muted-foreground mt-1"><Trans>Nothing matched &ldquo;{search}&rdquo;</Trans></p>
                  </div>
                  <Pill onClick={() => { setSearchInput(''); setFilter('q', '') }}>
                    <Trans>Clear search</Trans>
                  </Pill>
                </>
              ) : (hasFilters || filterLibrary) ? (
                <>
                  <div>
                    <p className="text-[17px] font-medium text-foreground"><Trans>No matches</Trans></p>
                    <p className="text-[15px] text-muted-foreground mt-1"><Trans>Try adjusting your filters</Trans></p>
                  </div>
                  <Pill onClick={clearFilters}><Trans>Clear all filters</Trans></Pill>
                </>
              ) : (
                <>
                  <div>
                    <p className="text-[17px] font-medium text-foreground"><Trans>Your library is empty</Trans></p>
                    <p className="text-[15px] text-muted-foreground mt-1"><Trans>Upload or scan a folder to get started</Trans></p>
                  </div>
                  {isMember(user) && (
                    <Pill variant="primary" onClick={() => setUploadModalOpen(true)}>
                      <Upload className="w-4 h-4" />
                      <Trans>Upload books</Trans>
                    </Pill>
                  )}
                </>
              )}
            </div>
          ) : (
            <div key={view} ref={booksGridRef} className={cn(gridClass, refreshing && 'opacity-50 transition-opacity duration-150')} style={gridStyle}>
              {/* Motion layout animations: `layout` glides the survivors on a
                  reflow (cover-size slider, filter changes). Arriving cards
                  appear in place, never fading up from nothing inside the
                  painted panel; leaving cards fade out. */}
              <AnimatePresence mode="popLayout" initial={false}>
              {books.map((book, i) => (
                <m.div
                  key={`${cardView}-${groupActive && book.series ? 'stack-' : ''}${book.id}`}
                  layout
                  initial={false}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.92 }}
                  transition={{ layout: { duration: 0.24, ease: [0.2, 0.8, 0.2, 1] }, duration: 0.15 }}
                >
                {groupActive && book.series ? (
                  <SeriesStackCard
                    book={book}
                    count={book.series_count ?? 1}
                    view={cardView}
                    index={i}
                    focused={focusedIndex === i}
                    onOpen={() => navigate(`/?tab=series&series_detail=${encodeURIComponent(book.series!)}`)}
                  />
                ) : (
                <BookCard
                  book={book}
                  view={cardView}
                  index={i}
                  selected={selected.has(book.id)}
                  focused={focusedIndex === i}
                  onSelect={selectionMode ? (e) => { e.preventDefault(); toggleSelect(book.id, e.shiftKey) } : undefined}
                  onTagClick={tag => setFilter('tag', tag)}
                  onSeriesClick={series => setFilter('series', series)}
                  onAuthorClick={author => setFilter('author', author)}
                  onQuickLook={() => setQuickLookId(book.id)}
                  readingStatus={readingStatuses[book.id]?.status}
                  progressPct={readingStatuses[book.id]?.progress_pct}
                  rating={readingStatuses[book.id]?.rating}
                />
                )}
                </m.div>
              ))}
              </AnimatePresence>
            </div>
          )}
          {/* Infinite scroll sentinel */}
          <div ref={sentinelCallback} className="h-1 mt-2" />
          {loadingMore && (
            <div className="flex justify-center py-4">
              <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
            </div>
          )}
          {!hasMore && booksRef.current.length >= PAGE_SIZE && (() => {
            const loadedCount = booksRef.current.length
            return (
              <p className="text-center text-[13px] text-muted-foreground py-4">
                <Trans>All {loadedCount} books loaded</Trans>
              </p>
            )
          })()}
          </div>
          </PageBody>
          )}
        </div>

      {/* ── Bulk delete modal ───────────────────────────────────────────────── */}
      <BulkDeleteModal
        open={deleteModalOpen}
        books={books}
        selectedIds={selected}
        onCancel={() => setDeleteModalOpen(false)}
        onConfirm={bulkDelete}
      />

      {/* ── Quick look (Library grid, series volumes) ───────────────────────── */}
      {quickLookId != null && <QuickLook bookId={quickLookId} onClose={closeQuickLook} />}

      {/* ── Bulk metadata modal ─────────────────────────────────────────────── */}
      <ModalShell open={bulkMetaOpen} onClose={() => { if (!bulkMetaSaving) setBulkMetaOpen(false) }} className="w-full max-w-md">
            <div className="bg-card border border-border rounded-2xl shadow-2xl p-6">
              <div className="flex items-start justify-between mb-1">
                <h2 className="text-base font-semibold text-foreground">
                  <Plural value={selected.size} one="Edit Metadata for # Book" other="Edit Metadata for # Books" />
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
                <Trans>Only filled fields will be updated. Leave blank to keep existing values.</Trans>
              </p>

              <div className="space-y-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block"><Trans>Author</Trans></label>
                  <AutocompleteInput
                    value={bulkMetaAuthor}
                    onChange={setBulkMetaAuthor}
                    suggestions={facets.authors}
                    placeholder={t`Author`}
                    className="w-full text-sm bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block"><Trans>Series</Trans></label>
                  <AutocompleteInput
                    value={bulkMetaSeries}
                    onChange={setBulkMetaSeries}
                    suggestions={facets.series}
                    placeholder={t`Series name`}
                    className="w-full text-sm bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>

                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block"><Trans>Add Tags</Trans></label>
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
                    placeholder={t`Add tag…`}
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
                  <label className="text-xs font-medium text-muted-foreground mb-1 block"><Trans>Type</Trans></label>
                  <select
                    value={bulkMetaTypeId}
                    onChange={e => setBulkMetaTypeId(e.target.value ? Number(e.target.value) : '')}
                    className="w-full text-sm bg-muted rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring text-foreground"
                  >
                    <option value="">{t`— keep existing —`}</option>
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
                  <Trans>Cancel</Trans>
                </button>
                <button
                  onClick={bulkSaveMetadata}
                  disabled={bulkMetaSaving || (!bulkMetaAuthor && !bulkMetaSeries && !bulkMetaTagsAdd.length && !bulkMetaTypeId)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
                >
                  {bulkMetaSaving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <Trans>Save Changes</Trans>
                </button>
              </div>
            </div>
      </ModalShell>
    </ShellFrame>
  )
}

function FilterSelect({ label, value, options, onChange }: {
  label: string; value: string; options: (string | { value: string; label: string })[]; onChange: (v: string) => void
}) {
  const { t } = useLingui()
  const opts = options.map(o => typeof o === 'string' ? { value: o, label: o } : o)
  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      <label className="text-[13px] text-muted-foreground">{label}</label>
      <select value={value} onChange={e => onChange(e.target.value)}
        className="h-9 w-full rounded-lg border border-border bg-background px-2.5 text-[14px] focus:outline-none focus:ring-2 focus:ring-ring text-foreground">
        <option value="">{t`All`}</option>
        {opts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  )
}
