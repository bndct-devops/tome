import { Fragment, useEffect, useId, useLayoutEffect, useRef, useState, useCallback } from 'react'
import { AnimatePresence, m, useReducedMotion } from 'motion/react'
import { useSearchParams, useNavigate, useLocation, useNavigationType } from 'react-router-dom'
import {
  X,
  LayoutGrid, List,
  SlidersHorizontal, Loader2,
  Library as LibraryIcon, CheckSquare, XSquare, Download, Pencil,
  Play, CheckCheck, Trash2, Settings2, Layers, Star, Share2,
  Tablet, Check,
} from 'lucide-react'
import { AppHeader, HeaderSearch } from '@/components/AppHeader'
import { ShellFrame } from '@/components/ShellFrame'
import { ModalShell, isModalOpen, useEscape } from '@/components/ModalShell'
import { useAuth, isMember, isAdmin } from '@/contexts/AuthContext'
import { Trans, useLingui, Plural } from '@lingui/react/macro'
import { plural, msg } from '@lingui/core/macro'
import type { MessageDescriptor } from '@lingui/core'
import { useToast } from '@/contexts/ToastContext'
import { useSidebarLists } from '@/lib/sidebarLists'
import type { ViewMode } from '@/components/BookCard'
import { LibraryGrid, LibrarySkeleton, type LibraryGridHandle, type ResumePoint, type StatusMap } from '@/components/library/LibraryGrid'
import { CountLabel, ToggleFill, ReserveLabel } from '@/components/library/Toolbar'
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
import { HomeTab } from '@/components/home/HomeTab'
import { api } from '@/lib/api'
import type { Book, ReadingStatus, Arc, SeriesMeta, SeriesStatus } from '@/lib/books'
import { formatBytes } from '@/lib/books'
import { useBookTypes } from '@/lib/bookTypes'
import { useShiftSelect } from '@/lib/useShiftSelect'
import { cn, progressPercent } from '@/lib/utils'
import { devicesFor, useDeviceInventories } from '@/lib/deviceBooks'
import { describePace, formatEstimateDays, formatEstimateHours, type BacklogSummary } from '@/lib/backlog'
import { SeriesReadingStats } from '@/components/SeriesReadingStats'
import { SeriesCoverageStrip } from '@/components/SeriesCoverageStrip'
import { QuickLook, type QuickLookPreview } from '@/components/QuickLook'
import { Unfold, Pop, CoverLift, RunningNumber, BookLink, useSeriesNav, useBookNav, coverThumb, listItem, EASE, EASE_CSS } from '@/components/motion'
import { GlideGroup, Glide } from '@/components/page/Glide'
import { findSeriesCover, openSeriesWithCover, readSeriesPreview, seriesPreviewState } from '@/lib/coverTransition'
import { peekSeriesSeed } from '@/components/motion/nav'
import { SortMenu } from '@/components/library/SortMenu'
import { FinishByPill } from '@/components/goals/FinishByPill'
import { PageBody, PageHeader, SectionTitle, Pill, PillGroup, Select, Input, FormLabel, INPUT_CLASS } from '@/components/page'
// Library / Series tab icons, kept apart from the shared import above.
import { ArrowLeft, Eye, Upload } from 'lucide-react'
import { coverSrc } from '@/lib/covers'
import { preloadImages } from '@/lib/preload'

type SortField = 'title' | 'author' | 'year' | 'added_at' | 'rating' | 'word_count'
type SortOrder = 'asc' | 'desc'

interface SeriesItem {
  name: string
  book_count: number
  cover_book_id: number
  cover_color?: string | null
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
  cover_color?: string | null
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
  title: msg`Title`, author: msg`Author`, year: msg`Year`, added_at: msg`Date added`, rating: msg`My rating`, word_count: msg`Length`,
}

// Reading-status + series-status display names, shared by filter buttons and chips.
const READING_STATUS_LABELS: Record<string, MessageDescriptor> = {
  unread: msg`Unread`, want_to_read: msg`Want to read`, reading: msg`Reading`,
  read: msg({ message: 'Read', context: 'status' }), shelved: msg`On hold`,
}
const MISSING_LABELS: Record<string, MessageDescriptor> = {
  cover: msg`Cover`, description: msg`Description`, author: msg`Author`,
  series: msg`Series`, any: msg`Any`, file: msg`File`,
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
    <ModalShell
      open={open}
      onClose={() => { if (!deleting) onCancel() }}
      size="sm"
      title={<Plural value={selectedIds.size} one="Delete # book" other="Delete # books" />}
      subtitle={<Trans>This permanently removes the selected books and their files from disk. This cannot be undone.</Trans>}
      bodyClassName="pb-2"
      footer={
        <>
          {deleting && (
            <span className="mr-auto text-[13px] text-muted-foreground">
              <Plural value={selectedIds.size} one="Deleting # book..." other="Deleting # books..." />
            </span>
          )}
          <Pill variant="quiet" onClick={onCancel} disabled={deleting}>
            <Trans>Cancel</Trans>
          </Pill>
          <Pill
            variant="destructive"
            onClick={handleDelete}
            disabled={deleting}
            className="bg-destructive/10 text-destructive font-semibold"
          >
            {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
            <Plural value={selectedIds.size} one="Delete # book" other="Delete # books" />
          </Pill>
        </>
      }
    >
      <div>
        {selectedBooks.map(book => {
          const primaryFile = book.files[0] ?? null
          return (
            <div key={book.id} className="flex items-center gap-3 py-2.5 border-b border-border/70 last:border-0">
              <div className="relative w-8 h-12 rounded-[6px] overflow-hidden shrink-0 bg-muted">
                <CoverImage
                  src={book.cover_path ? coverSrc(book.id, { w: 160 }) : null}
                  alt={book.title}
                  iconClassName="w-3.5 h-3.5"
                />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-[15px] font-medium text-foreground truncate leading-tight">{book.title}</p>
                {(book.author || primaryFile) && (
                  <p className="mt-0.5 text-[13px] text-muted-foreground truncate">
                    {book.author}
                    {book.author && primaryFile ? ' · ' : ''}
                    {primaryFile && primaryFile.format.toUpperCase()}
                    {primaryFile?.file_size ? ` · ${formatBytes(primaryFile.file_size)}` : ''}
                  </p>
                )}
              </div>
            </div>
          )
        })}
        {selectedIds.size > selectedBooks.length && (
          <p className="text-[13px] text-muted-foreground py-2.5">
            {(() => { const more = selectedIds.size - selectedBooks.length; return t`…and ${more} more not shown` })()}
          </p>
        )}
      </div>
    </ModalShell>
  )
}

const seriesScroll = new Map<string, number>()

// The library list as it was when the page was last left, so coming back
// (from a book, Stats, anywhere) shows it at once and where it was scrolled
// to, then refreshes it quietly. One entry: the list for one set of filters.
let listCache: {
  key: string
  books: Book[]
  /** Whether `books` is the grouped (series stacks) list. */
  grouped: boolean
  total: number | null
  statuses: StatusMap
  hasMore: boolean
  scrollTop: number
  /** The first card on screen when the list was left. */
  resume: ResumePoint | null
  locationKey: string
} | null = null

/** Resolves once these books' covers are in the browser (at most 300 ms). */
function coversReady(books: Book[], signal?: AbortSignal): Promise<void> {
  const urls = books.flatMap(b => [
    ...(b.cover_path ? [coverSrc(b.id, { w: 400, v: b.updated_at })] : []),
    ...(b.stack_cover_ids ?? []).slice(0, 2).map(id => coverSrc(id, { w: 400 })),
  ])
  const loads = urls.map(url => {
    const img = new Image()
    img.src = url
    return img.decode().catch(() => {})
  })
  return Promise.race([
    Promise.all(loads).then(() => {}),
    new Promise<void>(r => { setTimeout(r, 300); signal?.addEventListener('abort', () => r()) }),
  ])
}

/** A first page fetched ahead for the Group series toggle (`res` once it is in). */
interface GroupList {
  key: string
  at: number
  res: { data: Book[]; headers: Headers } | null
  promise: Promise<{ data: Book[]; headers: Headers } | null>
}
const GROUP_LIST_TTL = 30_000

/** A function whose identity never changes and that always calls the latest `fn`. */
function useStableCallback<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  const ref = useRef(fn)
  useLayoutEffect(() => { ref.current = fn })
  return useCallback((...args: A) => ref.current(...args), [])
}

/** The new list with the old objects kept where a book did not change, so its card does not re-render. */
function shareBooks(prev: Book[], next: Book[]): Book[] {
  if (prev.length === 0) return next
  const byId = new Map(prev.map(b => [b.id, b]))
  return next.map(b => {
    const old = byId.get(b.id)
    return old && JSON.stringify(old) === JSON.stringify(b) ? old : b
  })
}

export function DashboardPage() {
  const { t, i18n } = useLingui()
  const { user } = useAuth()
  const navigate = useNavigate()
  const openSeries = useSeriesNav()
  const openBook = useBookNav()
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null)

  // ── View / sort (persisted) ───────────────────────────────────────────────
  // Two view modes (grid/list) + a cover-size slider for the grid. Old installs
  // stored 'large' | 'small' - those migrate to grid at the matching size.
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

  // The grid/list switch: each card keeps its box, cover and title travel to
  // their place in the other layout (LibraryGrid measures the cards on screen
  // first, the first card on screen keeps its place in the scroll).
  const libraryGridRef = useRef<LibraryGridHandle | null>(null)
  function switchView(v: ViewPref) {
    if (v === view) return
    libraryGridRef.current?.prepare('view')
    persistView(v)
  }
  function persistGridSize(s: number) { libraryGridRef.current?.prepare('size'); setGridSize(s); localStorage.setItem(GRID_SIZE_KEY, String(s)) }
  function persistSort(s: SortField) { setSort(s); localStorage.setItem(SORT_KEY, s) }
  function persistOrder(o: SortOrder) { setOrder(o); localStorage.setItem(ORDER_KEY, o) }

  // ── Group by series (persisted) ───────────────────────────────────────────
  const [groupBySeries, setGroupBySeries] = useState(() => localStorage.getItem(GROUP_KEY) === 'true')
  function persistGroup(v: boolean) {
    setGroupBySeries(v)
    localStorage.setItem(GROUP_KEY, String(v))
    if (v) exitSelectionMode()
  }

  // ── Tab (Home / Books / Series) - derived from URL ───────────────────────
  const tab = (searchParams.get('tab') || 'home') as 'home' | 'books' | 'series'
  const [seriesList, setSeriesList] = useState<SeriesItem[]>([])
  const [seriesLoading, setSeriesLoading] = useState(false)
  // False until the first series list is in: until then the grid shows its
  // shape, never a false "No series yet".
  const [seriesLoaded, setSeriesLoaded] = useState(false)
  // Volumes just marked read: their badges grow in (once, not on every load).
  const [justMarked, setJustMarked] = useState(false)
  // Opened from a book page, the series' answers were asked for ahead
  // (components/motion/nav): the detail paints complete in its first frame.
  const [seriesSeed] = useState(() => {
    const name = tab === 'series' ? searchParams.get('series_detail') : null
    const seed = name ? peekSeriesSeed(name) : null
    return seed && name ? { name, ...seed } : null
  })
  const [expandedSeries, setExpandedSeries] = useState<string | null>(seriesSeed?.name ?? null)
  const [seriesDetail, setSeriesDetail] = useState<SeriesDetail | null>((seriesSeed?.detail as SeriesDetail | undefined) ?? null)
  const [shareSeries, setShareSeries] = useState<string | null>(null)
  const [seriesDetailLoading, setSeriesDetailLoading] = useState(false)
  const [markingAllRead, setMarkingAllRead] = useState(false)
  // Series meta (status badges) - keyed by series name
  const [seriesMetaMap, setSeriesMetaMap] = useState<Record<string, SeriesStatus>>((seriesSeed?.meta as Record<string, SeriesStatus> | undefined) ?? {})
  // Arcs for the currently open series detail
  const [seriesArcs, setSeriesArcs] = useState<Arc[]>((seriesSeed?.arcs as Arc[] | undefined) ?? [])
  // Manage series modal
  const [manageSeriesOpen, setManageSeriesOpen] = useState(false)
  const [seriesDescExpanded, setSeriesDescExpanded] = useState(false)
  // The follow tracker's "Latest: Vol N · date" line, shown in the series header's crumb line.
  const [followLatest, setFollowLatest] = useState<string | null>(null)

  function setTab(value: 'home' | 'books' | 'series') {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (value === 'home') next.delete('tab')
      else next.set('tab', value)
      if (value !== 'series') next.delete('series_detail')
      return next
    })
  }

  function fetchSeriesMetaForList(_list: SeriesItem[]) {
    // Single batch call - previously fired one GET per series which
    // exhausted the DB connection pool on libraries with many series.
    api.get<Record<string, SeriesStatus>>('/series/meta-map')
      .then(map => setSeriesMetaMap(map))
      .catch(() => {})
  }

  function openSeriesTab() {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      next.set('tab', 'series')
      next.delete('series_detail')
      return next
    })
    setExpandedSeries(null)
    setSeriesDetail(null)
    setSeriesLoading(true)
    api.get<SeriesItem[]>('/books/series')
      .then(list => {
        setSeriesList(list)
        fetchSeriesMetaForList(list)
      })
      .catch(() => {})
      .finally(() => { setSeriesLoading(false); setSeriesLoaded(true) })
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

  // A series' detail is a URL (?tab=series&series_detail=Name), so the way
  // back from a book returns to it. The grid pushes it, the Series crumb
  // drops it, and this effect follows the URL either way.
  function showSeries(name: string | null, state?: unknown) {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      next.set('tab', 'series')
      if (name) next.set('series_detail', name)
      else next.delete('series_detail')
      return next
    }, state ? { state } : undefined)
  }

  // The Series crumb: back the way the series was opened from the grid (the
  // cover returns to its card), else to the grid.
  function backToSeries() {
    if ((location.state as { fromSeriesGrid?: boolean } | null)?.fromSeriesGrid) navigate(-1)
    else showSeries(null)
  }

  // Auto-open series detail when navigating via ?tab=series&series_detail=Name
  useEffect(() => {
    if (tab !== 'series') return
    const detailName = searchParams.get('series_detail')
    if (!detailName) {
      if (expandedSeries) { setExpandedSeries(null); setSeriesDetail(null); setSeriesArcs([]) }
      return
    }
    if (detailName === expandedSeries) return
    if (seriesList.length > 0) { openSeriesDetail(detailName); return }
    // Load series list first, then open the detail
    setSeriesLoading(true)
    api.get<SeriesItem[]>('/books/series')
      .then(list => {
        setSeriesList(list)
        fetchSeriesMetaForList(list)
        setSeriesLoading(false)
        setSeriesLoaded(true)
        openSeriesDetail(detailName)
      })
      .catch(() => { setSeriesLoading(false); setSeriesLoaded(true) })
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
        label: t`${name} · Vol. ${start_index}-${end_index}`,
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
        large ? 'h-7 px-3 rounded-full text-[12px] tracking-[0.08em]' : 'px-1.5 py-0.5 rounded text-[12px] tracking-wide',
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
        setJustMarked(true)
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
  const filterMinWords = searchParams.get('min_words') ?? ''
  const filterMaxWords = searchParams.get('max_words') ?? ''
  // Chips show the bound the way people write it (30,000), the URL keeps the plain number.
  const minWords = filterMinWords ? Number(filterMinWords).toLocaleString(i18n.locale) : ''
  const maxWords = filterMaxWords ? Number(filterMaxWords).toLocaleString(i18n.locale) : ''
  const filterMissing = searchParams.get('missing') ?? ''
  const filterOwnership = searchParams.get('ownership') ?? ''
  const filterAddedBy = searchParams.get('added_by') ? Number(searchParams.get('added_by')) : null
  // The user's KOReader devices, comma-separated: books on all of
  // on_device and on none of not_on_device.
  const filterOnDevice = searchParams.get('on_device') ?? ''
  const filterNotOnDevice = searchParams.get('not_on_device') ?? ''
  // Type (content_type): Volumes when absent, so a plain link keeps the
  // default; 'all' in the address means no type filter.
  const contentTypeParam = searchParams.get('content_type')
  const contentType = contentTypeParam === null ? 'volume' : contentTypeParam === 'all' ? '' : contentTypeParam
  function setContentType(value: string) {
    setFilter('content_type', value === 'volume' ? '' : value === '' ? 'all' : value)
  }
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

  const hasFilters = !!(search || filterSeries || filterNoSeries || filterAuthor || filterTag || filterFormat || filterLanguage || filterReadingStatus || filterMinRating || filterMinWords || filterMaxWords || filterMissing || filterOwnership || filterAddedBy || filterOnDevice || filterNotOnDevice || contentTypeParam !== null)

  // Grouping is bypassed while drilling into a specific series - the user
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
  useEscape(bulkLibMenu, useCallback(() => setBulkLibMenu(false), []))

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
  function exitSelectionMode() {
    if (selectionMode) libraryGridRef.current?.prepare('select')
    setSelectionMode(false); setSelected(new Set())
  }

  async function bulkDownload() {
    if (!selected.size) return
    const chosen = books.filter(b => selected.has(b.id))
    if (chosen.length === selected.size && chosen.every(b => b.files.length === 0)) {
      toastError(t`None of the selected books has a file to download`)
      return
    }
    setBulkPending(true)
    try {
      const token = localStorage.getItem('tome_token')
      const resp = await fetch('/api/downloads', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ book_ids: [...selected] }),
      })
      if (!resp.ok) {
        const body = await resp.json().catch(() => null) as { detail?: unknown } | null
        throw new Error(typeof body?.detail === 'string' ? body.detail : t`Download failed`)
      }
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
      // Books without a file (a reader's sync) are personal records: the
      // server never puts them in a library. Count them for the toast - all
      // of the user's own are listed by "missing=file", so a select-all that
      // reaches past the loaded pages is counted right too.
      let noFile = books.filter(b => selected.has(b.id) && b.status === 'no_file').length
      if (books.filter(b => selected.has(b.id)).length < selected.size) {
        try {
          const res = await api.get<{ ids: number[] }>('/books/ids?include_no_file=true&missing=file')
          noFile = res.ids.filter(id => selected.has(id)).length
        } catch {
          // keep the loaded-books count
        }
      }
      await api.post(`/libraries/${libId}/books`, { book_ids: [...selected] })
      const added = selected.size - noFile
      if (noFile === 0) {
        toastSuccess(plural(added, { one: 'Added # book to library', other: 'Added # books to library' }))
      } else if (added === 0) {
        toast.info(plural(noFile, {
          one: 'Not added: # book without a file stays out of libraries',
          other: 'Not added: # books without a file stay out of libraries',
        }))
      } else {
        toastSuccess(plural(noFile, {
          one: `Added ${added} to library; # book without a file was left out`,
          other: `Added ${added} to library; # books without a file were left out`,
        }))
      }
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
      const res = await api.put<{ updated: number; skipped: number }>('/books/bulk-metadata', body)
      if (res.updated === 0) {
        toastError(t`Nothing was changed. You can only edit books you uploaded.`)
        return
      }
      const updatedText = plural(res.updated, { one: 'Updated metadata for # book', other: 'Updated metadata for # books' })
      const skippedText = plural(res.skipped, { one: '# book was left as it is because you did not upload it', other: '# books were left as they are because you did not upload them' })
      toastSuccess(res.skipped > 0 ? t`${updatedText}. ${skippedText}.` : updatedText)
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
  // Cached across page mounts (see useSidebarLists) - this refetch only
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
  const listKeyFor = (grouped: boolean) => {
    const p = buildFilterParams()
    p.set('sort', sort)
    p.set('order', order)
    if (grouped) p.set('group_by_series', 'true')
    return p.toString()
  }
  const listKey = () => listKeyFor(groupActive)
  const [seed] = useState(() => (listCache && listCache.key === listKey() ? listCache : null))
  // Back to a long list: it renders around the card that was first on screen.
  const [resume] = useState(() => (
    seed && seed.resume && navigationType === 'POP' && seed.locationKey === location.key && seed.books.length > 120 && seed.resume.height > 0 ? seed.resume : undefined
  ))
  const seededRef = useRef(!!seed)
  const loadedKeyRef = useRef(seed?.key ?? '')
  const [books, setBooks] = useState<Book[]>(() => seed?.books ?? [])
  // Whether `books` is the grouped list: the cards follow the list they show,
  // not the toggle, so a toggle changes nothing until its list is in.
  const [listGrouped, setListGrouped] = useState(() => seed?.grouped ?? groupActive)
  const { handleToggle } = useShiftSelect(books.map(b => b.id))

  const [totalCount, setTotalCount] = useState<number | null>(() => seed?.total ?? null)
  const [readingStatuses, setReadingStatuses] = useState<StatusMap>(() => seed?.statuses ?? {})
  const [facets, setFacets] = useState<Facets>({ series: [], authors: [], tags: [], formats: [], languages: [] })
  const [loading, setLoading] = useState(!seed)
  const [uploadModalOpen, setUploadModalOpen] = useState(false)
  const [filterOpen, setFilterOpen] = useState(false)
  // Quick look opened from a cover in the Library grid or a series' volumes.
  const [quickLookId, setQuickLookId] = useState<number | null>(null)
  // The cover box it was opened on: the quick look grows out of it and back.
  const [quickLookFrom, setQuickLookFrom] = useState<{ origin?: DOMRect; cover?: string | null }>({})
  const openQuickLook = useCallback((id: number, origin?: DOMRect, cover?: string | null) => { setQuickLookFrom({ origin, cover }); setQuickLookId(id) }, [])
  const closeQuickLook = useCallback(() => setQuickLookId(null), [])
  // The header lines the quick look shows until the book is in.
  const quickLookPreview = (id: number): QuickLookPreview | undefined => {
    const b = books.find(x => x.id === id)
    if (b) return { title: b.title, author: b.author, series: b.series, series_index: b.series_index }
    const v = seriesDetail?.books.find(x => x.id === id)
    return v && seriesDetail ? { title: v.title, author: seriesDetail.author, series: seriesDetail.name, series_index: v.series_index } : undefined
  }

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
  // Group series: the list the toggle switches to, fetched while the pointer
  // is on the toggle (and the list it leaves, kept for the way back), so a
  // click regroups the cards at once. Keyed by listKey; cleared by any reset.
  const groupLists = useRef(new Map<string, GroupList>())
  const groupToggled = useRef(false)

  function buildFilterParams(): URLSearchParams {
    const params = new URLSearchParams()
    // The user's own books without a file (made from their reader's sync)
    // belong in their Library; the API leaves them out unless asked.
    params.set('include_no_file', 'true')
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
    if (filterMinWords) params.set('min_words', filterMinWords)
    if (filterMaxWords) params.set('max_words', filterMaxWords)
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
    // The Group series toggle's list, fetched ahead (see prefetchGroup).
    let ahead: GroupList | undefined
    if (reset) {
      const found = groupToggled.current && !quiet ? groupLists.current.get(key) : undefined
      groupToggled.current = false
      if (found) groupLists.current.delete(key)
      if (found && performance.now() - found.at < GROUP_LIST_TTL) ahead = found
    }

    if (reset) {
      abortRef.current?.abort()
      abortRef.current = new AbortController()
      if (booksRef.current.length === 0) setLoading(true)
      else if (!quiet && !ahead?.res) setRefreshing(true)
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

    const apply = ({ data: newBooks, headers }: { data: Book[]; headers: Headers }) => {
      if (signal?.aborted) return
      if (reset) {
        loadedKeyRef.current = key
        const raw = headers.get('x-total-count')
        setTotalCount(raw !== null ? Number(raw) : null)
      }
      let merged: Book[]
      if (reset) {
        // The cards on screen are measured before they move.
        if (!quiet) libraryGridRef.current?.prepare('list', { toGrouped: groupActive })
        merged = shareBooks(booksRef.current, newBooks)
        setListGrouped(groupActive)
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
    }
    const settle = () => {
      if (reset) {
        // An aborted reset was superseded by a newer one that owns the
        // loading flags - leave them alone.
        if (signal?.aborted) return
        setLoading(false)
        setRefreshing(false)
      } else {
        // Always clear, even when aborted: this request is finished and a
        // stuck loadingMore would stall the infinite scroll for good.
        setLoadingMore(false)
      }
    }
    // Already in: the new list shows in this same commit.
    if (ahead?.res) {
      apply(ahead.res)
      settle()
      return
    }
    const fetchPage = () => api.getWithHeaders<Book[]>(`/books?${params}`, signal)
      // A new list over a shown one waits (dimmed, briefly) for its first
      // covers, so the cards that arrive have their pictures.
      .then(async res => {
        if (reset && !quiet && booksRef.current.length > 0) await coversReady(res.data.slice(0, 30), signal)
        return res
      })
    ;(ahead ? ahead.promise.then(res => res ?? fetchPage()) : fetchPage())
      .then(apply)
      .catch(err => {
        if (err instanceof Error && err.name === 'AbortError') return
        toastError(t`Failed to load books`)
      })
      .finally(settle)
  }, [sort, order, search, filterSeries, filterNoSeries, filterAuthor, filterTag, filterFormat, filterLanguage, filterLibrary, filterReadingStatus, filterMinRating, filterMinWords, filterMaxWords, filterMissing, contentType, filterOwnership, filterAddedBy, filterOnDevice, filterNotOnDevice, groupActive])

  useEffect(() => {
    if (seededRef.current) {
      seededRef.current = false
      loadBooks(true, true)
    } else loadBooks()
  }, [loadBooks])

  // The Group series toggle's next list: fetched when the pointer reaches the
  // toggle (or it takes focus), with its first covers, so the click shows it
  // in the same frame instead of after the request.
  function prefetchGroup() {
    if (filterSeries) return
    const key = listKeyFor(!groupActive)
    const have = groupLists.current.get(key)
    if (have && performance.now() - have.at < GROUP_LIST_TTL) return
    for (const [k, v] of groupLists.current) if (performance.now() - v.at > GROUP_LIST_TTL) groupLists.current.delete(k)
    const params = new URLSearchParams(key)
    params.set('skip', '0')
    params.set('limit', String(PAGE_SIZE))
    const entry: GroupList = { key, at: performance.now(), res: null, promise: Promise.resolve(null) }
    entry.promise = api.getWithHeaders<Book[]>(`/books?${params}`)
      .then(async res => { await coversReady(res.data.slice(0, 30)); entry.res = res; return res })
      .catch(() => null)
    groupLists.current.set(key, entry)
  }
  function toggleGroup() {
    const key = listKey()
    // The list shown now is the way back, already in.
    if (!filterSeries && books.length > 0 && loadedKeyRef.current === key) {
      const res = { data: books.slice(0, PAGE_SIZE), headers: new Headers(totalCount != null ? { 'x-total-count': String(totalCount) } : {}) }
      groupLists.current.set(key, { key, at: performance.now(), res, promise: Promise.resolve(res) })
    }
    groupToggled.current = !filterSeries
    persistGroup(!groupBySeries)
  }

  // Keep the list (and, on leaving, the scroll position) for the way back.
  useEffect(() => {
    if (!loadedKeyRef.current) return
    listCache = {
      key: loadedKeyRef.current, books, grouped: listGrouped, total: totalCount, statuses: readingStatuses, hasMore,
      scrollTop: listCache?.scrollTop ?? 0, resume: listCache?.resume ?? null, locationKey: listCache?.locationKey ?? '',
    }
  }, [books, listGrouped, totalCount, readingStatuses, hasMore])
  useLayoutEffect(() => {
    const main = bodyRef.current?.closest('main') ?? null
    // Back/forward to the entry the list was left from: same place as before
    // (a long list: LibraryGrid puts its first card back instead).
    if (seed && navigationType === 'POP' && seed.locationKey === location.key && main && !resume) main.scrollTop = seed.scrollTop
    return () => {
      if (listCache && main) {
        listCache.scrollTop = main.scrollTop
        listCache.resume = libraryGridRef.current?.resumePoint() ?? null
        listCache.locationKey = locationKeyRef.current
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Series grid and series detail: the <main> scroll position per history
  // entry, so Back from a book lands where the volumes were. Saved whenever
  // the entry is left (another URL or another page), restored on a POP once
  // the content it measured is back.
  const seriesReady = tab === 'series'
    && expandedSeries === (searchParams.get('series_detail') || null)
    && (expandedSeries
    ? !!seriesDetail && !seriesDetailLoading
    : !seriesLoading && seriesList.length > 0)
  useLayoutEffect(() => {
    const main = bodyRef.current?.closest('main') ?? null
    if (!main) return
    const key = location.key
    // Recorded as it moves, never on cleanup: a remount's own cleanup (dev
    // StrictMode, a re-run) would overwrite the saved spot with 0.
    const onScroll = () => { seriesScroll.set(key, main.scrollTop) }
    main.addEventListener('scroll', onScroll, { passive: true })
    return () => main.removeEventListener('scroll', onScroll)
  }, [location.key])
  const restoredKeyRef = useRef<string | null>(null)
  useLayoutEffect(() => {
    if (!seriesReady || restoredKeyRef.current === location.key) return
    restoredKeyRef.current = location.key
    const main = bodyRef.current?.closest('main')
    if (!main) return
    const saved = seriesScroll.get(location.key)
    const target = navigationType === 'POP' && saved != null ? saved : 0
    main.scrollTop = target
    if (main.scrollTop >= target - 1) return
    // The page is still growing (stats, covers): keep applying the position
    // until it fits, for at most 2s, and give up as soon as the user scrolls.
    let stop = false
    const giveUp = () => { stop = true }
    main.addEventListener('wheel', giveUp, { once: true, passive: true })
    main.addEventListener('touchstart', giveUp, { once: true, passive: true })
    const started = Date.now()
    const tick = () => {
      if (stop || Date.now() - started > 2000) return
      main.scrollTop = target
      if (main.scrollTop < target - 1) requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
    return () => { stop = true; main.removeEventListener('wheel', giveUp); main.removeEventListener('touchstart', giveUp) }
  }, [seriesReady, location.key, navigationType])

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

  // Mirror the shown list's grouping into a ref so the keydown handler (bound once) sees it
  const listGroupedRef = useRef(false)
  useEffect(() => { listGroupedRef.current = listGrouped }, [listGrouped])


  // The focused card lives in a ref too, so the key handler reads and writes
  // it directly: side effects (navigate, blur) inside a state updater ran
  // during render and React warned "Cannot update a component while rendering".
  const focusedRef = useRef<number | null>(null)
  useEffect(() => { focusedRef.current = focusedIndex }, [focusedIndex])
  useEffect(() => {
    function moveFocus(next: number | null) {
      focusedRef.current = next
      setFocusedIndex(next)
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (isModalOpen()) return
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return

      if (e.key === '/') {
        e.preventDefault()
        searchInputRef.current?.focus()
        return
      }

      const n = booksRef.current.length
      const prev = focusedRef.current
      if (e.key === 'j' || e.key === 'ArrowDown') {
        e.preventDefault()
        moveFocus(n === 0 ? null : prev === null ? 0 : (prev + 1) % n)
        return
      }

      if (e.key === 'k' || e.key === 'ArrowUp') {
        e.preventDefault()
        moveFocus(n === 0 ? null : prev === null ? n - 1 : (prev - 1 + n) % n)
        return
      }

      if (e.key === 'Enter') {
        // A link focused with Tab (a book card) opens itself; do not open
        // the j/k-highlighted book on top of it.
        if ((e.target as HTMLElement).closest('a')) return
        if (prev !== null && booksRef.current[prev]) {
          const b = booksRef.current[prev]
          // The focused card's cover grows into the page it opens.
          if (listGroupedRef.current && b.series && b.status !== 'no_file') {
            openSeries(b.series)
          } else {
            openBook({
              id: b.id, title: b.title, author: b.author, series: b.series, series_index: b.series_index,
              coverColor: b.cover_color,
            })
          }
        }
        return
      }

      if (e.key === 'Escape') {
        if (prev === null) searchInputRef.current?.blur()
        moveFocus(null)
        return
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [openBook, openSeries])

  useEffect(() => {
    api.get<SeriesItem[]>('/books/series').then(setSeriesList).catch(() => {}).finally(() => setSeriesLoaded(true))
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
    ...(filterNoSeries ? [{ label: t`No series`, key: 'no_series' }] : []),
    ...(filterAuthor ? [{ label: t`Author: ${filterAuthor}`, key: 'author' }] : []),
    ...(filterTag ? [{ label: t`Tag: ${filterTag}`, key: 'tag' }] : []),
    ...(filterFormat ? [{ label: t`Format: ${filterFormatUpper}`, key: 'format' }] : []),
    ...(filterLanguage ? [{ label: t`Language: ${filterLanguageLabel}`, key: 'language' }] : []),
    ...(filterReadingStatus ? [{ label: t`Status: ${filterStatusLabel}`, key: 'reading_status' }] : []),
    ...(filterMinRating ? [{ label: filterMinRating === 5 ? t`Rated: 5 stars` : t`Rated: ${filterMinRating}+ stars`, key: 'min_rating' }] : []),
    ...(filterMinWords ? [{ label: t`At least ${minWords} words`, key: 'min_words' }] : []),
    ...(filterMaxWords ? [{ label: t`At most ${maxWords} words`, key: 'max_words' }] : []),
    ...(filterMissing ? [{ label: t`Missing: ${filterMissingLabel}`, key: 'missing' }] : []),
    ...(filterOwnership === 'mine' ? [{ label: t`My books`, key: 'ownership' }] : filterOwnership === 'shared' ? [{ label: t`Shared library`, key: 'ownership' }] : []),
    ...(filterAddedBy ? [{ label: t`Uploader: ${filterUploaderName}`, key: 'added_by' }] : []),
    ...(contentTypeParam === 'chapter' ? [{ label: t`Type: Chapters`, key: 'content_type' }] : contentTypeParam === 'all' ? [{ label: t`Type: All`, key: 'content_type' }] : []),
    ...(onDevices.length ? [{ label: (() => { const names = onDevices.join(', '); return t`On ${names}` })(), key: 'on_device' }] : []),
    ...(notOnDevices.length ? [{ label: (() => { const names = notOnDevices.join(', '); return t`Not on ${names}` })(), key: 'not_on_device' }] : []),
  ]

  // Params to pass to SaveFilterButton: the same filters that build the
  // query, minus library_id (that belongs in the sidebar). Type is kept only
  // when it is Chapters: the default (Volumes) needs no param, and "All" has
  // no server-side value that shelves on devices and stats understand.
  const saveableParams: Record<string, string> = Object.fromEntries(buildFilterParams())
  delete saveableParams.library_id
  if (saveableParams.content_type !== 'chapter') delete saveableParams.content_type

  // What the cards call: the same functions for the page's whole life, so a
  // render of the page re-renders no card.
  const onToggleCard = useStableCallback((id: number, shift: boolean) => toggleSelect(id, shift))
  const onTagCard = useStableCallback((tag: string) => setFilter('tag', tag))
  const onSeriesCard = useStableCallback((series: string) => setFilter('series', series))
  const onAuthorCard = useStableCallback((author: string) => setFilter('author', author))

  // Card typography follows the slider value; the grid itself is LibraryGrid.
  const cardView: ViewMode = view === 'list' ? 'list' : gridSize < 150 ? 'small' : 'large'

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

      {/* Dialogs mounted while open: AnimatePresence keeps each for its exit. */}
      <AnimatePresence>
        {shareSeries && (
          <ShareModal
            key={shareSeries}
            title={shareSeries}
            endpoint={`/series/${encodeURIComponent(shareSeries)}/share`}
            noun="series"
            onClose={() => setShareSeries(null)}
          />
        )}
      </AnimatePresence>
      <UploadModal
        isOpen={uploadModalOpen}
        onClose={() => setUploadModalOpen(false)}
        onDone={() => { loadBooks(); loadFacets() }}
        onWishMatches={(wishIds) => {
          const n = wishIds.length
          // Only admins can open the wishlist review, so only they get the link.
          toast.info(plural(n, {
            one: 'This upload satisfies # wish.',
            other: 'This upload satisfies # wishes.',
          }), isAdmin(user) ? { action: { label: t`Open Wishlist`, onClick: () => navigate('/admin/wishlist') } } : undefined)
        }}
      />

      <AnimatePresence>
      {manageSeriesOpen && expandedSeries && (
        <ManageSeriesModal
          key={expandedSeries}
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
      </AnimatePresence>

      {/* ── Body: the dashboard tabs. The frame's panel scrolls; this wrapper
            only carries the page padding (the shared PageBody comes with the
            per-tab rebuilds). ────────────────────────────────────────────── */}
        <div ref={bodyRef} data-dashboard="">
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
            {/* The URL names the series from the first render (opened from a book page, say), so
                the page's first frame is its detail, not the grid. */}
            {/* The URL alone decides between grid and detail: going Back, the grid is in the very
                commit the URL changes in, so the cover has its card to return to. */}
            {searchParams.get('series_detail') ? (
              seriesDetailLoading || !seriesDetail || expandedSeries !== searchParams.get('series_detail') ? (() => {
                // While the series loads: its frame with the hero cover the
                // card showed, in the place the loaded page puts it, so the
                // cover transition has somewhere to land at once.
                const name = searchParams.get('series_detail')!
                const listed = seriesList.find(s => s.name === name)
                const previewCover = readSeriesPreview(location.state, name)?.cover
                  ?? (listed?.cover_book_id ? coverSrc(listed.cover_book_id, { w: 400 }) : null)
                return (
                <>
                  {/* The loaded header wraps its actions under the crumb on a phone. */}
                  <div className="flex items-center justify-between gap-4 flex-wrap min-h-9 max-sm:min-h-[72px]">
                    <div className="text-[13px] text-muted-foreground flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={backToSeries}
                        className="inline-flex items-center gap-1 hover:text-foreground transition-colors touch-feedback"
                      >
                        <ArrowLeft className="w-3.5 h-3.5" />
                        <Trans>Series</Trans>
                      </button>
                    </div>
                  </div>
                  <div className="grid gap-8 lg:gap-10 lg:grid-cols-[220px_minmax(0,1fr)] items-start">
                    <div className="mx-auto w-[200px] sm:w-[220px] lg:mx-0">
                      <div data-series-hero={name} className="rounded-[12px] overflow-hidden bg-muted" style={{ boxShadow: 'var(--cover-bloom-hero)' }}>
                        <div className="relative aspect-[2/3]">
                          {/* No picture known yet: the box holds empty rather than drawing "no cover". */}
                          {previewCover && <CoverImage src={previewCover} alt="" loading="eager" color={listed?.cover_color} iconClassName="w-10 h-10" />}
                        </div>
                      </div>
                    </div>
                    <div className="min-w-0">
                      <h1 className="font-display text-[34px] leading-none font-bold text-foreground">{name === '__unserialized__' ? t`No series` : name}</h1>
                    </div>
                  </div>
                </>
                )
              })() : (() => {
                const isUnserialized = seriesDetail.name === '__unserialized__'
                const total = seriesDetail.books.length
                const readN = seriesDetail.books.filter(b => b.reading_status === 'read').length
                const readingN = seriesDetail.books.filter(b => b.reading_status === 'reading').length
                const unreadN = total - readN - readingN
                const readPct = total ? (readN / total) * 100 : 0
                const readingPct = total ? (readingN / total) * 100 : 0
                const heroCoverId = seriesList.find(s => s.name === seriesDetail.name)?.cover_book_id
                  ?? (seriesDetail.books.find(b => b.cover_path) ?? seriesDetail.books[0])?.id ?? null
                const heroCoverColor = seriesDetail.books.find(b => b.id === heroCoverId)?.cover_color
                const sections = groupVolumesByArc(seriesDetail.books, seriesArcs)
                const hasArcs = seriesArcs.length > 0
                const coverage = seriesDetail.books.map(b => ({
                  id: b.id, title: b.title, series_index: b.series_index, cover_path: b.cover_path,
                }))
                const continueBook = getContinueBook(seriesDetail.books)
                return (
                  <>
                  {/* ── Header line: where you are on the left, the series' actions on the right ── */}
                  <div className="flex items-center justify-between gap-4 flex-wrap">
                    <div className="text-[13px] text-muted-foreground flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={backToSeries}
                        className="inline-flex items-center gap-1 hover:text-foreground transition-colors touch-feedback"
                      >
                        <ArrowLeft className="w-3.5 h-3.5" />
                        <Trans>Series</Trans>
                      </button>
                      <span aria-hidden>/</span>
                      <span><Plural value={total} one="# volume" other="# volumes" /></span>
                      {followLatest && (
                        <>
                          <span aria-hidden>/</span>
                          <span>{followLatest}</span>
                        </>
                      )}
                    </div>
                          <div className="flex flex-wrap items-center gap-1">
                            {/* First, so its late arrival or longer label ("Finish by Dec 31") moves nothing to its right. */}
                            {!isUnserialized && (
                              <FinishByPill
                                target={{ series: seriesDetail.name, cover: heroCoverId, read: readN, total }}
                                done={total > 0 && readN === total}
                              />
                            )}
                            {!isUnserialized && (
                              <Pill
                                variant="quiet"
                                onClick={() => setShareSeries(seriesDetail.name)}
                                title={t`Share this series as a public metadata-only page`}
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
                            <SeriesFollowButton seriesName={seriesDetail.name} onLatest={setFollowLatest} />
                          </div>
                  </div>

                  {/* ── Rail (cover, coverage) and content (title, state, description, actions, stats) ── */}
                  <div className="grid gap-8 lg:gap-10 lg:grid-cols-[220px_minmax(0,1fr)] items-start">
                    <div className="mx-auto w-[200px] sm:w-[220px] lg:mx-0">
                      <div data-series-hero={seriesDetail.name} className="rounded-[12px] overflow-hidden bg-muted" style={{ boxShadow: 'var(--cover-bloom-hero)' }}>
                        <div className="relative aspect-[2/3]">
                          <CoverImage
                            src={heroCoverId != null ? coverSrc(heroCoverId, { w: 400 }) : null}
                            alt=""
                            color={heroCoverColor}
                            iconClassName="w-10 h-10"
                          />
                        </div>
                      </div>
                      {!isUnserialized && coverage.some(v => v.series_index != null) && (
                        <div className="mt-6">
                          <div className="text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground mb-3"><Trans>In the library</Trans></div>
                          <SeriesCoverageStrip coverage={coverage} />
                        </div>
                      )}
                    </div>

                    <div className="min-w-0 flex flex-col gap-6">
                      <div>
                        <h1 className="font-display text-[34px] leading-none font-bold text-foreground">{isUnserialized ? t`No series` : seriesDetail.name}</h1>
                        {seriesDetail.author && <div className="mt-2 text-[16px] text-muted-foreground">{seriesDetail.author}</div>}
                      </div>

                      {/* Status and arcs */}
                      {(seriesMetaMap[seriesDetail.name] || hasArcs) && (
                        <div className="flex flex-wrap items-center gap-2">
                          <SeriesStatusBadge status={seriesMetaMap[seriesDetail.name]} large />
                          {sections.filter(sec => sec.arcId != null).map(sec => (
                            <button
                              key={sec.arcId}
                              type="button"
                              onClick={() => document.getElementById(`series-arc-${sec.arcId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                              className="h-7 px-3 rounded-full bg-secondary text-[13px] text-foreground hover:bg-surface-hover transition-[color,background-color,transform] touch-feedback"
                            >
                              {sec.name}
                            </button>
                          ))}
                        </div>
                      )}

                      <SeriesRating seriesName={seriesDetail.name} isUnserialized={isUnserialized} />


                      {/* Progress through the series: the bar and the figures move
                          when they change (Mark all read); another series starts fresh. */}
                      <div key={seriesDetail.name}>
                        <div className="flex items-center gap-3">
                          <div
                            className="h-[6px] flex-1 rounded-full overflow-hidden flex"
                            style={{ background: 'var(--rail)' }}
                          >
                            <div className="h-full bg-primary transition-[width] duration-300 ease-(--spring)" style={{ width: `${readPct}%` }} />
                            <div className="h-full bg-primary/50 transition-[width] duration-300 ease-(--spring)" style={{ width: `${readingPct}%` }} />
                          </div>
                          <span className="text-[17px] font-bold tabular-nums text-foreground">
                            <RunningNumber value={Math.round(readPct)} format={n => `${Math.round(n)}%`} />
                          </span>
                        </div>
                        <p className="mt-1.5 text-[13px] text-muted-foreground">
                          {(() => {
                            // Same message as before; the figures inside it run.
                            const readCount = <RunningNumber value={readN} />
                            const readingCount = <RunningNumber value={readingN} />
                            const unreadCount = <RunningNumber value={unreadN} />
                            return <Trans>{readCount} read &middot; {readingCount} reading &middot; {unreadCount} unread</Trans>
                          })()}
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
                          <ClampedText expanded={seriesDescExpanded} className="text-[15px] text-foreground/80 leading-relaxed whitespace-pre-line">
                            {seriesDetail.description}
                          </ClampedText>
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
                  </div>

                  {/* Per-series reading stats: full width, like the volumes below.
                      Beside the cover it stayed indented against an empty rail once
                      the cover had scrolled away. */}
                  <SeriesReadingStats seriesName={seriesDetail.name} />

                  {/* Volume grid - grouped by arc when arcs exist */}
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
                          <BookLink
                            key={vol.id}
                            book={{
                              id: vol.id, title: vol.title, author: seriesDetail.author ?? null, series: seriesDetail.name,
                              series_index: vol.series_index, cover: vol.cover_path ? coverSrc(vol.id, { w: 400, v: vol.updated_at }) : null,
                              coverColor: vol.cover_color,
                            }}
                            title={vol.title}
                            className="group relative block cursor-pointer rounded-[8px] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                          >
                            <CoverLift>
                            <div
                              data-cover-id={vol.id}
                              className="relative aspect-[2/3] w-full rounded-[8px] overflow-hidden bg-muted"
                              style={{ boxShadow: 'var(--cover-bloom-fan)' }}
                            >
                              <CoverImage
                                src={vol.cover_path ? coverSrc(vol.id, { w: 400, v: vol.updated_at }) : null}
                                alt={vol.title}
                                color={vol.cover_color}
                                iconClassName="w-4 h-4"
                              />
                              {/* Quick-read play button - top-left corner on hover */}
                              <button
                                type="button"
                                onClick={e => { e.preventDefault(); e.stopPropagation(); navigate(`/reader/${vol.id}`) }}
                                className="absolute top-1 left-1 z-10 w-7 h-7 rounded-full bg-background/75 backdrop-blur-sm text-foreground hover:bg-background flex items-center justify-center opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                title={t({ message: 'Read', context: 'action' })}
                                aria-label={t({ message: 'Read', context: 'action' })}
                              >
                                <Play className="w-3 h-3 fill-current ml-px" />
                              </button>
                              {/* Quick look - top-right corner on hover; the status dot steps aside */}
                              <button
                                type="button"
                                onClick={e => { e.preventDefault(); e.stopPropagation(); openQuickLook(vol.id, e.currentTarget.parentElement?.getBoundingClientRect(), vol.cover_path ? coverSrc(vol.id, { w: 400, v: vol.updated_at }) : null) }}
                                className="absolute top-1 right-1 z-10 w-7 h-7 rounded-full bg-background/75 backdrop-blur-sm text-foreground hover:bg-background flex items-center justify-center opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto group-focus-within:opacity-100 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                title={t`Quick look`}
                                aria-label={t`Quick look`}
                              >
                                <Eye className="w-3.5 h-3.5" />
                              </button>
                              {/* Read: a check badge (the old bare status dot read as a
                                  stray circle). Reading: a progress strip along the
                                  bottom edge. Other states draw nothing. */}
                              {vol.reading_status === 'read' && (
                                <span
                                  className={cn('absolute top-1.5 right-1.5 w-[18px] h-[18px] rounded-full bg-primary text-primary-foreground grid place-items-center shadow-sm transition-[opacity,scale] duration-200 ease-(--spring) group-hover:opacity-0 group-focus-within:opacity-0', justMarked && 'starting:scale-0')}
                                  title={t({ message: 'Read', context: 'status' })}
                                  aria-label={t({ message: 'Read', context: 'status' })}
                                >
                                  <Check className="w-3 h-3" strokeWidth={3} />
                                </span>
                              )}
                              {vol.reading_status === 'reading' && (
                                <div className="absolute inset-x-0 bottom-0 z-[1] h-[3px] bg-black/40" aria-hidden>
                                  <div className="h-full bg-primary transition-[width] duration-300" style={{ width: `${Math.max(4, Math.round(progressPercent(vol.progress_pct)))}%` }} />
                                </div>
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
                            </CoverLift>
                          </BookLink>
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
                eyebrow={!seriesLoaded ? undefined : (() => {
                  const n = seriesList.filter(s => s.name !== '__unserialized__').length
                  return <Plural value={n} one="# series" other="# series" />
                })()}
                title={<Trans>Series</Trans>}
              />
              {!seriesLoaded || (seriesLoading && seriesList.length === 0) ? (
                <div aria-busy="true" className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-x-5 gap-y-8 animate-pulse">
                  {Array.from({ length: 10 }, (_, i) => (
                    <div key={i}>
                      <div className="aspect-[2/3] rounded-[8px] bg-muted" />
                      <div className="mt-2.5 h-3.5 w-4/5 rounded bg-muted" />
                      <div className="mt-2 h-3 w-1/2 rounded bg-muted" />
                    </div>
                  ))}
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
                        onClick={e => {
                          if (isUnserialized) {
                            setSearchParams(prev => {
                              const p = new URLSearchParams(prev)
                              p.set('tab', 'books')
                              p.delete('saved_filter')
                              p.set('no_series', 'true')
                              return p
                            })
                          } else {
                            // The cover grows into the series page's hero; the page's
                            // Series crumb then goes back the same way.
                            const cover = findSeriesCover(s.name, e.currentTarget)
                            openSeriesWithCover(cover, s.name, () => showSeries(s.name, { ...seriesPreviewState({ name: s.name, cover: coverThumb(cover) }), fromSeriesGrid: true }))
                          }
                        }}
                        className="group flex flex-col text-left min-w-0 focus:outline-none"
                      >
                        <CoverLift>
                          <div
                            data-series-cover={isUnserialized ? undefined : s.name}
                            className="relative aspect-[2/3] rounded-[8px] bg-muted overflow-hidden group-focus-visible:ring-2 group-focus-visible:ring-ring"
                            style={{ boxShadow: 'var(--cover-bloom-fan)' }}
                          >
                            <CoverImage
                              src={s.cover_book_id ? coverSrc(s.cover_book_id, { w: 400 }) : null}
                              alt={isUnserialized ? t`Unserialized` : s.name}
                              color={s.cover_color}
                            />
                          </div>
                        </CoverLift>
                        <div className="pt-2.5 flex flex-col gap-0.5 min-w-0">
                          <div className="flex items-start gap-1.5 flex-wrap">
                            <span className="text-[14px] font-medium text-foreground leading-snug line-clamp-2 group-hover:text-primary transition-colors">
                              {isUnserialized ? t`No series` : s.name}
                            </span>
                            {!isUnserialized && <SeriesStatusBadge status={seriesMetaMap[s.name]} />}
                          </div>
                          {/* The count sits under the cover: on a light cover the
                              white label over a thin scrim was unreadable. */}
                          <span className="text-[13px] text-muted-foreground truncate">
                            {!isUnserialized && s.author && <>{s.author}{' · '}</>}
                            <Plural value={s.book_count} one="# book" other="# books" />
                          </span>
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
                              <div className="h-full bg-primary transition-[width] duration-300 ease-(--spring)" style={{ width: `${(s.read_count / s.book_count) * 100}%` }} />
                              <div className="h-full bg-primary/50 transition-[width] duration-300 ease-(--spring)" style={{ width: `${(s.reading_count / s.book_count) * 100}%` }} />
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
            eyebrow={loading && totalCount == null
              ? '…'
              : (() => {
                  // Grouped rows are series stacks + standalones - "titles", not the
                  // ambiguous "entries" (which read like a book count and never matched it).
                  // The total of the current view (filters included); how many rows have
                  // loaded so far is paging, not information for the header. While a new
                  // list loads, the old count stays, dimmed like the cards.
                  const n = totalCount ?? books.length
                  return (
                    <CountLabel
                      // Grouping changes what is counted: the new figure shows at once, not run to.
                      key={listGrouped ? 'titles' : 'books'}
                      className={cn('transition-opacity duration-150', refreshing && 'opacity-50')}
                      value={n}
                      text={listGrouped
                        ? plural(n, { one: '# title', other: '# titles' })
                        : plural(n, { one: '# book', other: '# books' })}
                    />
                  )
                })()}
            title={activeLibraryName ?? <Trans>All books</Trans>}
            actions={
              /* Capped at the viewport so the row wraps on phones instead of
                 running off the side. Every control keeps its slot: nothing
                 here moves when another one is toggled. */
              <div className="flex flex-wrap items-center sm:justify-end gap-1 max-w-[calc(100vw-2.5rem)]">
                <SortMenu
                  fields={(Object.keys(SORT_LABELS) as SortField[]).map(f => ({ id: f, label: i18n._(SORT_LABELS[f]) }))}
                  value={sort}
                  order={order}
                  onPick={toggleSort}
                />

                <Pill
                  variant="quiet"
                  onClick={() => setFilterOpen(o => !o)}
                  aria-expanded={filterOpen}
                  aria-label={t`Filters`}
                  title={t`Filters`}
                  className={cn('relative isolate px-3', (filterOpen || activeFilterChips.length > 0) && 'text-foreground')}
                >
                  <ToggleFill on={filterOpen || activeFilterChips.length > 0} />
                  <SlidersHorizontal className="w-4 h-4" />
                  <span className="hidden lg:inline"><Trans>Filters</Trans></span>
                  {/* Counts filter chips only: a search alone (hasFilters) is not
                      a filter and used to show a "0" badge. */}
                  <AnimatePresence initial={false}>
                    {activeFilterChips.length > 0 && (
                      <m.span
                        key="count"
                        initial={{ width: 0, opacity: 0, scale: 0.6 }}
                        animate={{ width: 'auto', opacity: 1, scale: 1 }}
                        exit={{ width: 0, opacity: 0, scale: 0.6 }}
                        transition={{ duration: 0.16, ease: EASE }}
                        className="inline-flex justify-end overflow-hidden -ml-1.5"
                      >
                        <span className="ml-1.5 min-w-5 h-5 px-1.5 grid place-items-center rounded-full bg-foreground text-background text-[12px] font-semibold tabular-nums">
                          <RunningNumber value={activeFilterChips.length} />
                        </span>
                      </m.span>
                    )}
                  </AnimatePresence>
                </Pill>

                {/* Group by series toggle */}
                <Pill
                  variant="quiet"
                  onClick={toggleGroup}
                  onPointerEnter={prefetchGroup}
                  onPointerDown={prefetchGroup}
                  onFocus={prefetchGroup}
                  title={groupBySeries ? t`Show individual volumes` : t`Group volumes by series`}
                  aria-label={t`Group by series`}
                  aria-pressed={groupBySeries}
                  className={cn('relative isolate px-3', groupBySeries && 'text-foreground')}
                >
                  <ToggleFill on={groupBySeries} />
                  <Layers className="w-4 h-4" />
                  <span className="hidden xl:inline"><Trans>Group series</Trans></span>
                </Pill>

                {/* Select mode: selection works on single books, so it rests while
                    grouped, in its place. */}
                {(() => {
                  const selectLabel = selectionMode ? (selected.size > 0 ? t`Deselect all` : t`Select all`) : t`Select`
                  return (
                    <Pill
                      variant="quiet"
                      disabled={books.length === 0 || groupActive}
                      onClick={() => {
                        if (!selectionMode) {
                          libraryGridRef.current?.prepare('select')
                          setSelectionMode(true)
                          selectAll()
                        } else if (selected.size > 0) {
                          clearSelection()
                        } else {
                          selectAll()
                        }
                      }}
                      className={cn('relative isolate px-3 disabled:opacity-35', selectionMode && 'text-foreground')}
                      aria-label={selectLabel}
                      title={selectLabel}
                    >
                      <ToggleFill on={selectionMode} />
                      {selectionMode && selected.size > 0 ? <XSquare className="w-4 h-4" /> : <CheckSquare className="w-4 h-4" />}
                      <ReserveLabel className="hidden xl:inline-grid" labels={[t`Select`, t`Select all`, t`Deselect all`]}>{selectLabel}</ReserveLabel>
                    </Pill>
                  )
                })()}
                {/* Its slot is kept while not selecting. */}
                <Pill
                  variant="quiet"
                  icon
                  onClick={exitSelectionMode}
                  title={t`Exit selection mode`}
                  aria-label={t`Exit selection mode`}
                  aria-hidden={!selectionMode || undefined}
                  tabIndex={selectionMode ? undefined : -1}
                  className={cn('transition-[opacity,scale,color,background-color]! motion-reduce:transition-none', !selectionMode && 'opacity-0 scale-75 pointer-events-none')}
                >
                  <X className="w-4 h-4" />
                </Pill>

                {/* Cover size (grid only, resting in the list) + grid / list */}
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
                    disabled={view !== 'grid'}
                    className={cn(
                      'cover-slider w-20 sm:w-24 mx-2 transition-opacity duration-150',
                      view === 'grid' ? 'opacity-100' : 'opacity-35 pointer-events-none'
                    )}
                    style={{
                      background: `linear-gradient(to right, var(--primary) ${((gridSize - GRID_SIZE_MIN) / (GRID_SIZE_MAX - GRID_SIZE_MIN)) * 100}%, var(--input) 0)`,
                    }}
                  />
                  <GlideGroup>
                    <div className="isolate flex items-center">
                      {([
                        { mode: 'grid' as ViewPref, Icon: LayoutGrid, title: t`Grid view` },
                        { mode: 'list' as ViewPref, Icon: List, title: t`List view` },
                      ]).map(({ mode, Icon, title }) => (
                        <Pill
                          key={mode}
                          icon
                          variant="quiet"
                          onClick={() => switchView(mode)}
                          title={title}
                          aria-label={title}
                          aria-pressed={view === mode}
                          className={cn('relative', view === mode && 'text-foreground')}
                        >
                          {view === mode && <Glide behind className="bg-secondary" />}
                          <Icon className="w-4 h-4" />
                        </Pill>
                      ))}
                    </div>
                  </GlideGroup>
                </div>
              </div>
            }
          />

          {/* ── Active filters as chips, Add shelf, Clear all ─────────────── */}
          {/* The row unfolds; chips come and go in it, the rest glide. */}
          <Unfold open={activeFilterChips.length > 0 || hasFilters} gap={16}>
            <div className="relative flex items-center gap-2 flex-wrap">
              <AnimatePresence mode="popLayout" initial={false}>
                {activeFilterChips.map(f => (
                  <m.span key={f.key} {...listItem} className="inline-flex items-center gap-1 h-8 pl-3.5 pr-1.5 rounded-full bg-secondary text-[13px] font-medium text-foreground">
                    {f.label}
                    <button
                      type="button"
                      onClick={() => setFilter(f.key, '')}
                      aria-label={t`Remove filter`}
                      className="w-6 h-6 grid place-items-center rounded-full text-muted-foreground hover:text-destructive hover:bg-background/60 transition-colors touch-feedback"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </m.span>
                ))}

                {/* Save filter button - only when content filters active (not library) */}
                <m.span key="save" layout="position" transition={listItem.transition}>
                  <SaveFilterButton params={saveableParams} onSaved={loadSavedFilters} />
                </m.span>

                <m.span key="clear" layout="position" transition={listItem.transition}>
                  <Pill variant="quiet" onClick={clearFilters}>
                    <Trans>Clear all</Trans>
                  </Pill>
                </m.span>
              </AnimatePresence>
            </div>
          </Unfold>

          {/* ── Filter panel ──────────────────────────────────────────────── */}
          {/* Unfolds under the header; gap 16 = the column's gap-4. */}
          <Unfold open={filterOpen} gap={16}>
          {filterOpen && (() => {
            /* eslint-disable lingui/no-unlocalized-strings -- Tailwind classes */
            const opt = (active: boolean) => cn(
              'h-8 px-3.5 rounded-full text-[13px] font-medium transition-colors inline-flex items-center gap-1',
              active ? 'bg-foreground text-background' : 'bg-secondary text-muted-foreground hover:text-foreground',
            )
            const rowLabel = 'text-[13px] text-muted-foreground w-20 shrink-0'
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
                <PillGroup
                  size="sm"
                  aria-label={t`Status`}
                  value={filterReadingStatus}
                  onChange={v => setFilter('reading_status', v)}
                  options={(['', 'unread', 'want_to_read', 'reading', 'read', 'shelved'] as const).map(s => ({
                    value: s as string, label: s === '' ? t`All` : i18n._(READING_STATUS_LABELS[s]),
                  }))}
                />
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <span className={rowLabel}><Trans>Length</Trans></span>
                <div className="flex items-center gap-2 flex-1 min-w-0 sm:flex-none">
                {([['min_words', t`Minimum words`, t`Min words`, filterMinWords], ['max_words', t`Maximum words`, t`Max words`, filterMaxWords]] as const).map(([key, label, placeholder, value]) => (
                  <Fragment key={key}>
                    {key === 'max_words' && <span className="text-[13px] text-muted-foreground" aria-hidden>–</span>}
                    {/* Commit on blur or Enter so typing doesn't refetch on every digit. */}
                    <Input
                      key={value}
                      type="number" inputMode="numeric" min="0" step="1" defaultValue={value}
                      aria-label={label}
                      placeholder={placeholder}
                      onBlur={e => {
                        if (e.currentTarget.validity.valid) setFilter(key, e.currentTarget.value === '' ? '' : String(e.currentTarget.valueAsNumber))
                      }}
                      onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}
                      className="w-auto min-w-0 flex-1 sm:flex-none sm:w-32 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                    />
                  </Fragment>
                ))}
                </div>
                <Unfold open={!!(filterMinWords || filterMaxWords)}>
                  <span className="block text-[13px] text-muted-foreground sm:ml-2"><Trans>Books without a word count are hidden.</Trans></span>
                </Unfold>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <span className={rowLabel}><Trans>Rating</Trans></span>
                <PillGroup
                  size="sm"
                  aria-label={t`Rating`}
                  value={filterMinRating ? String(filterMinRating) : ''}
                  onChange={v => setFilter('min_rating', v)}
                  options={([
                    { value: '', label: t`All`, starless: true },
                    { value: '1', label: t`Rated`, starless: true },
                    { value: '3', label: '3+', starless: false },
                    { value: '4', label: '4+', starless: false },
                    { value: '5', label: '5', starless: false },
                  ]).map(({ value, label, starless }) => ({
                    value,
                    label: starless ? label : <span className="inline-flex items-center gap-1"><Star className="w-3 h-3 fill-current" />{label}</span>,
                  }))}
                />
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <span className={rowLabel}><Trans>Missing</Trans></span>
                <PillGroup
                  size="sm"
                  aria-label={t`Missing`}
                  value={filterMissing}
                  onChange={v => setFilter('missing', v)}
                  options={[
                    { value: '', label: t`All` },
                    { value: 'cover', label: t`Cover` },
                    { value: 'description', label: t`Description` },
                    { value: 'author', label: t`Author` },
                    { value: 'series', label: t`Series` },
                    { value: 'any', label: t`Any` },
                    { value: 'file', label: t`File` },
                  ]}
                />
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
                          'touch-feedback transition-[color,background-color,transform]',
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
              {/* Ownership filter - members only (not admin) */}
              {isMember(user) && !isAdmin(user) && (
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={rowLabel}><Trans>Books</Trans></span>
                  <PillGroup
                    size="sm"
                    aria-label={t`Books`}
                    value={filterOwnership}
                    onChange={v => setFilter('ownership', v)}
                    options={[
                      { value: '', label: t`All` },
                      { value: 'mine', label: t`My books` },
                      { value: 'shared', label: t`Shared library` },
                    ]}
                  />
                </div>
              )}
              {/* Uploader filter - admins only */}
              {isAdmin(user) && userList.length > 0 && (
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={rowLabel}><Trans>Uploader</Trans></span>
                  <Select
                    value={filterAddedBy ?? ''}
                    onChange={e => setFilter('added_by', e.target.value)}
                    aria-label={t`Uploader`}
                    className="w-auto min-w-44"
                  >
                    <option value="">{t`All users`}</option>
                    {userList.map(u => (
                      <option key={u.id} value={u.id}>{u.username} ({u.role})</option>
                    ))}
                  </Select>
                </div>
              )}
              <div className="flex items-center gap-2 flex-wrap">
                <span className={rowLabel}><Trans>Type</Trans></span>
                <Select
                  value={contentType}
                  onChange={e => setContentType(e.target.value)}
                  aria-label={t`Type`}
                  className="w-auto min-w-36"
                >
                  <option value="volume">{t`Volumes`}</option>
                  <option value="chapter">{t`Chapters`}</option>
                  <option value="">{t`All`}</option>
                </Select>
              </div>
            </div>
            )
          })()}
          </Unfold>

          {/* ── Bulk action bar ──────────────────────────────────────────── */}
          <Unfold open={selectionMode} gap={16}>
            <div className="flex flex-wrap items-center gap-2 py-2.5 border-y border-border/70">
              {(() => { const n = selected.size; return <CountLabel className="text-[15px] font-semibold text-foreground tabular-nums mr-auto" value={n} text={t`${n} selected`} /> })()}
              <Pill onClick={bulkDownload} disabled={bulkPending || selected.size === 0}>
                <Download className="w-4 h-4" />
                <Trans>Download ZIP</Trans>
              </Pill>
              {isMember(user) && (
                (() => {
                  // Send needs each book's files, which only loaded rows have.
                  // "Select all" can select past the loaded pages; sending then
                  // would quietly drop the rest, so it waits for them instead.
                  // Books without a file (a reader's sync) have nothing to send.
                  const loaded = books.filter(b => selected.has(b.id))
                  const sendable = loaded.filter(b => b.files.length > 0)
                  const unloaded = selected.size - loaded.length
                  return (
                    <SendButton
                      variant="bulk"
                      disabled={bulkPending || sendable.length === 0 || unloaded > 0}
                      hint={unloaded > 0 ? plural(unloaded, { one: '# selected book is not loaded yet. Scroll down to load it, or select fewer, to send.', other: '# selected books are not loaded yet. Scroll down to load them, or select fewer, to send.' }) : undefined}
                      books={sendable.map(b => ({ id: b.id, title: b.title, files: b.files }))}
                    />
                  )
                })()
              )}
              <Pill
                onClick={() => { setBulkMetaOpen(true); api.get<Facets>('/books/facets').then(setFacets).catch(() => {}) }}
                disabled={bulkPending || selected.size === 0}
              >
                <Pencil className="w-4 h-4" />
                <Trans>Edit metadata</Trans>
              </Pill>
              {libraries.some(l => l.can_edit) && (
                <div className="relative">
                  <Pill
                    variant="primary"
                    onClick={() => setBulkLibMenu(o => !o)}
                    disabled={bulkPending || selected.size === 0}
                  >
                    <LibraryIcon className="w-4 h-4" />
                    <Trans>Add to library</Trans>
                  </Pill>
                  {bulkLibMenu && <div className="fixed inset-0 z-10" onClick={() => setBulkLibMenu(false)} />}
                  <Pop open={bulkLibMenu} origin="top-right" className="absolute right-0 top-full mt-2 z-20 rounded-xl bg-card shadow-lg ring-1 ring-panel-ring py-1.5 min-w-56 max-w-[calc(100vw-2rem)]">
                        <p className="border-b border-border px-3 pb-2 pt-0.5 mb-1 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
                          <Trans>Add to library</Trans>
                        </p>
                        {libraries.filter(l => l.can_edit).map(lib => (
                          <button
                            key={lib.id}
                            type="button"
                            onClick={() => bulkAddToLibrary(lib.id)}
                            className="flex items-center gap-2 w-full h-9 px-3 text-[14px] text-foreground hover:bg-secondary transition-colors"
                          >
                            <LibraryIcon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                            <span className="truncate">{lib.name}</span>
                          </button>
                        ))}
                  </Pop>
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
          </Unfold>
          </div>

          {/* ── Series matching the search ───────────────────────────────── */}
          <Unfold open={!!search && !groupActive && searchSeries.length > 0} gap={28}>
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
                      onClick={e => openSeries(name, undefined, { near: e.currentTarget })}
                      className="group flex items-center gap-3 w-72 shrink-0 p-2 pr-3 text-left rounded-xl hover:bg-secondary transition-colors touch-feedback"
                    >
                      <CoverLift className="w-12">
                        <div data-series-cover={name} className="relative aspect-[2/3] rounded-[6px] overflow-hidden bg-muted" style={{ boxShadow: 'var(--cover-bloom-fan)' }}>
                          <CoverImage
                            src={s.cover_book_id ? coverSrc(s.cover_book_id, { w: 400 }) : null}
                            alt={name}
                            color={s.cover_color}
                            iconClassName="w-4 h-4"
                          />
                        </div>
                      </CoverLift>
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
                            <div className="h-full bg-primary transition-[width] duration-300 ease-(--spring)" style={{ width: `${(s.read_count / s.book_count) * 100}%` }} />
                            <div className="h-full bg-primary/50 transition-[width] duration-300 ease-(--spring)" style={{ width: `${(s.reading_count / s.book_count) * 100}%` }} />
                          </div>
                        )}
                      </div>
                    </button>
                  )
                })}
              </HScrollRow>
            </section>
          </Unfold>

          {/* ── Grid / list, with the search result count above ─────────── */}
          <div>
          {/* The count only when there are results: an empty search has its own
              empty state below, and it said "no results" twice. */}
          {search && !loading && totalCount !== null && totalCount > 0 && (
            <p className="text-[14px] text-muted-foreground mb-4">
              <CountLabel value={totalCount} text={plural(totalCount, { one: `# result for "${search}"`, other: `# results for "${search}"` })} />
            </p>
          )}

          {loading ? (
            <LibrarySkeleton list={view === 'list'} gridSize={gridSize} />
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
            <LibraryGrid
              ref={libraryGridRef}
              books={books}
              grouped={listGrouped}
              view={cardView}
              gridSize={gridSize}
              statuses={readingStatuses}
              selected={selected}
              selecting={selectionMode}
              focusedIndex={focusedIndex}
              refreshing={refreshing}
              resume={resume}
              staged={!resume && !!seed}
              onToggle={onToggleCard}
              onTag={onTagCard}
              onSeries={onSeriesCard}
              onAuthor={onAuthorCard}
              onQuickLook={openQuickLook}
              onOpenSeries={openSeries}
            />
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
      <AnimatePresence>
        {quickLookId != null && <QuickLook key={quickLookId} bookId={quickLookId} origin={quickLookFrom.origin} cover={quickLookFrom.cover} preview={quickLookPreview(quickLookId)} onClose={closeQuickLook} />}
      </AnimatePresence>

      {/* ── Bulk metadata modal ─────────────────────────────────────────────── */}
      <ModalShell
        open={bulkMetaOpen}
        onClose={() => { if (!bulkMetaSaving) setBulkMetaOpen(false) }}
        size="sm"
        title={<Plural value={selected.size} one="Edit metadata for # book" other="Edit metadata for # books" />}
        subtitle={<Trans>Only filled fields will be updated. Leave blank to keep existing values.</Trans>}
        footer={
          <>
            <Pill variant="quiet" onClick={() => setBulkMetaOpen(false)} disabled={bulkMetaSaving}>
              <Trans>Cancel</Trans>
            </Pill>
            <Pill
              variant="primary"
              onClick={bulkSaveMetadata}
              disabled={bulkMetaSaving || (!bulkMetaAuthor && !bulkMetaSeries && !bulkMetaTagsAdd.length && !bulkMetaTypeId)}
            >
              {bulkMetaSaving && <Loader2 className="w-4 h-4 animate-spin" />}
              <Trans>Save changes</Trans>
            </Pill>
          </>
        }
      >
        <div className="flex flex-col gap-4 pt-1">
          <div>
            <FormLabel><Trans>Author</Trans></FormLabel>
            <AutocompleteInput
              value={bulkMetaAuthor}
              onChange={setBulkMetaAuthor}
              suggestions={facets.authors}
              placeholder={t`Author`}
              className={INPUT_CLASS}
            />
          </div>
          <div>
            <FormLabel><Trans>Series</Trans></FormLabel>
            <AutocompleteInput
              value={bulkMetaSeries}
              onChange={setBulkMetaSeries}
              suggestions={facets.series}
              placeholder={t`Series name`}
              className={INPUT_CLASS}
            />
          </div>
          <div>
            <FormLabel><Trans>Add tags</Trans></FormLabel>
            {bulkMetaTagsAdd.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-2">
                {bulkMetaTagsAdd.map(tag => (
                  <span key={tag} className="inline-flex items-center gap-1 h-7 pl-3 pr-1 rounded-full text-[13px] bg-secondary text-foreground">
                    {tag}
                    <button
                      type="button"
                      onClick={() => setBulkMetaTagsAdd(prev => prev.filter(t => t !== tag))}
                      aria-label={t`Remove tag`}
                      className="w-5 h-5 grid place-items-center rounded-full text-muted-foreground hover:text-destructive transition-colors"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
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
              className={INPUT_CLASS}
            />
          </div>
          <div>
            <FormLabel><Trans>Type</Trans></FormLabel>
            <Select
              value={bulkMetaTypeId}
              onChange={e => setBulkMetaTypeId(e.target.value ? Number(e.target.value) : '')}
            >
              <option value="">{t`Keep existing`}</option>
              {bookTypes.map(t => (
                <option key={t.id} value={t.id}>{t.label}</option>
              ))}
            </Select>
          </div>
        </div>
      </ModalShell>
    </ShellFrame>
  )
}

/** Text clamped to three lines that opens to its full height and closes back
 *  smoothly (200 ms); the ellipsis is there whenever it is closed. */
function ClampedText({ expanded, className, children }: { expanded: boolean; className?: string; children: React.ReactNode }) {
  const ref = useRef<HTMLParagraphElement>(null)
  const reduce = useReducedMotion()
  // While closing, the full text stays until the box has shrunk to three lines.
  const [closing, setClosing] = useState(false)
  const [was, setWas] = useState(expanded)
  if (was !== expanded) {
    setWas(expanded)
    setClosing(!expanded && !reduce)
  }
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || reduce || was !== expanded) return
    if (!expanded && !closing) return
    const now = el.getBoundingClientRect().height
    el.classList.add('line-clamp-3')
    const short = el.getBoundingClientRect().height
    el.classList.remove('line-clamp-3')
    const [from, to] = expanded ? [short, now] : [now, short]
    if (from === to) { if (!expanded) setClosing(false); return }
    /* eslint-disable lingui/no-unlocalized-strings -- CSS values */
    const anim = el.animate([{ height: `${from}px`, overflow: 'hidden' }, { height: `${to}px`, overflow: 'hidden' }], { duration: 200, easing: EASE_CSS })
    /* eslint-enable lingui/no-unlocalized-strings */
    if (!expanded) anim.onfinish = () => setClosing(false)
    return () => anim.cancel()
    // Runs once per open or close.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded])
  return <p ref={ref} className={cn(className, !expanded && !closing && 'line-clamp-3')}>{children}</p>
}

function FilterSelect({ label, value, options, onChange }: {
  label: string; value: string; options: (string | { value: string; label: string })[]; onChange: (v: string) => void
}) {
  const { t } = useLingui()
  const opts = options.map(o => typeof o === 'string' ? { value: o, label: o } : o)
  const id = useId()
  return (
    <div className="min-w-0">
      <FormLabel htmlFor={id}>{label}</FormLabel>
      <Select id={id} value={value} onChange={e => onChange(e.target.value)}>
        <option value="">{t`All`}</option>
        {opts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </Select>
    </div>
  )
}
