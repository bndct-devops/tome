import { useEffect, useRef, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, BookOpen, Settings, Rows4,
  ChevronLeft, ChevronRight, Minus, Plus, X,
  Loader2, AlignJustify, RotateCcw, GalleryHorizontalEnd, Columns2, Square,
} from 'lucide-react'
import { api } from '@/lib/api'
import type { Book, BookFile } from '@/lib/books'
import { cn } from '@/lib/utils'

// ── Types ─────────────────────────────────────────────────────────────────────

type ReaderTheme = 'light' | 'sepia' | 'dark'
type FontFamily = 'default' | 'serif' | 'sans'
type FitMode = 'width' | 'height'
type ComicMode = 'page' | 'webtoon'

interface TocItem {
  label: string
  href: string
  subitems?: TocItem[]
}

interface FoliateViewElement extends HTMLElement {
  renderer?: { setStyles(css: string): void } | null
  book?: { toc?: TocItem[] } | null
  open(file: File): Promise<void>
  goTo(target: string | number): Promise<void>
  goToFraction(fraction: number): Promise<void>
  prev(): void
  next(): void
}

// ── Theme / font definitions ──────────────────────────────────────────────────

const THEMES: Record<ReaderTheme, { bg: string; text: string; label: string }> = {
  light: { bg: '#ffffff', text: '#1a1a1a', label: 'Light' },
  sepia: { bg: '#f7f3e9', text: '#5c4a32', label: 'Sepia' },
  dark:  { bg: '#1c1c1e', text: '#e5e5e7', label: 'Dark' },
}

const FONT_FAMILIES: Record<FontFamily, { css: string; label: string }> = {
  default: { css: 'inherit', label: 'Default' },
  serif:   { css: '"Georgia", "Times New Roman", serif', label: 'Serif' },
  sans:    { css: '"Inter", "Helvetica Neue", sans-serif', label: 'Sans' },
}

// Build CSS string to inject into epub renderer
function buildReaderCSS(theme: ReaderTheme, fontSize: number, fontFamily: FontFamily): string {
  const t = THEMES[theme]
  const ff = FONT_FAMILIES[fontFamily].css
  return `
    html, body {
      background: ${t.bg} !important;
      color: ${t.text} !important;
      font-size: ${fontSize}% !important;
      font-family: ${ff} !important;
      line-height: 1.8 !important;
      padding: 0 4px !important;
    }
    p, div, span, li, td { color: ${t.text} !important; }
    h1, h2, h3, h4, h5, h6 { color: ${t.text} !important; }
    a { color: ${theme === 'dark' ? '#60a5fa' : '#2563eb'} !important; }
  `
}

// ── Streaming Comic Reader ────────────────────────────────────────────────────

interface StreamingComicReaderProps {
  bookId: string
  totalPages: number
  currentPage: number
  isRTL: boolean
  fitMode: FitMode
  spread: boolean
  theme: ReaderTheme
  onPageChange: (page: number) => void
  onReadComplete: () => void
}

function StreamingComicReader({
  bookId,
  totalPages,
  currentPage,
  isRTL,
  fitMode,
  spread,
  theme,
  onPageChange,
  onReadComplete,
}: StreamingComicReaderProps) {
  const [imageLoaded, setImageLoaded] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 })
  const preloadedRef = useRef<Set<number>>(new Set())
  const touchStartX = useRef<number | null>(null)
  const pinchStartDist = useRef<number | null>(null)
  const pinchStartZoom = useRef(1)
  const panStartOffset = useRef({ x: 0, y: 0 })
  const panStartTouch = useRef({ x: 0, y: 0 })
  const lastTapTime = useRef(0)
  const zoomRef = useRef(1)

  const themeColors = THEMES[theme]

  const token = localStorage.getItem('tome_token') ?? ''
  const pageUrl = (index: number) => `/api/books/${bookId}/pages/${index}?token=${encodeURIComponent(token)}`

  // Preload adjacent pages
  useEffect(() => {
    const toPreload = [currentPage - 1, currentPage + 1, currentPage + 2].filter(
      (p) => p >= 0 && p < totalPages && !preloadedRef.current.has(p)
    )
    toPreload.forEach((p) => {
      const img = new Image()
      img.src = pageUrl(p)
      preloadedRef.current.add(p)
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPage, totalPages, bookId])

  // Report read complete
  useEffect(() => {
    if (currentPage >= totalPages - 1 && totalPages > 0) {
      onReadComplete()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPage, totalPages])

  const step = spread ? 2 : 1

  const goToPage = useCallback((page: number) => {
    if (page >= 0 && page < totalPages) {
      setImageLoaded(false)
      onPageChange(page)
    }
  }, [totalPages, onPageChange])

  const goNext = useCallback(() => goToPage(Math.min(currentPage + step, totalPages - 1)), [currentPage, step, goToPage, totalPages])
  const goPrev = useCallback(() => goToPage(Math.max(currentPage - step, 0)), [currentPage, step, goToPage])

  // Click navigation: left half = prev, right half = next (respects RTL)
  // Disabled when zoomed in
  const handleClick = useCallback((e: React.MouseEvent) => {
    if (zoomRef.current > 1) return
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const clickX = e.clientX - rect.left
    const isLeftHalf = clickX < rect.width / 2
    if (isRTL) {
      isLeftHalf ? goNext() : goPrev()
    } else {
      isLeftHalf ? goPrev() : goNext()
    }
  }, [isRTL, goNext, goPrev])

  // Touch: swipe to navigate, pinch to zoom, double-tap to toggle zoom
  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    if (e.touches.length === 2) {
      // Pinch start
      const dx = e.touches[0].clientX - e.touches[1].clientX
      const dy = e.touches[0].clientY - e.touches[1].clientY
      pinchStartDist.current = Math.hypot(dx, dy)
      pinchStartZoom.current = zoomRef.current
    } else if (e.touches.length === 1) {
      // Check double-tap
      const now = Date.now()
      if (now - lastTapTime.current < 300) {
        // Double tap — toggle zoom
        const nextZoom = zoomRef.current > 1 ? 1 : 2
        setZoom(nextZoom)
        zoomRef.current = nextZoom
        if (nextZoom === 1) setPanOffset({ x: 0, y: 0 })
        lastTapTime.current = 0
        return
      }
      lastTapTime.current = now

      if (zoomRef.current > 1) {
        // Pan start
        panStartTouch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
        panStartOffset.current = { ...panOffset }
      } else {
        touchStartX.current = e.touches[0].clientX
      }
    }
  }, [panOffset])

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (e.touches.length === 2 && pinchStartDist.current !== null) {
      // Pinch zoom
      const dx = e.touches[0].clientX - e.touches[1].clientX
      const dy = e.touches[0].clientY - e.touches[1].clientY
      const dist = Math.hypot(dx, dy)
      const scale = Math.min(5, Math.max(1, pinchStartZoom.current * (dist / pinchStartDist.current)))
      setZoom(scale)
      zoomRef.current = scale
      if (scale <= 1) setPanOffset({ x: 0, y: 0 })
    } else if (e.touches.length === 1 && zoomRef.current > 1) {
      // Pan while zoomed
      const dx = e.touches[0].clientX - panStartTouch.current.x
      const dy = e.touches[0].clientY - panStartTouch.current.y
      setPanOffset({ x: panStartOffset.current.x + dx, y: panStartOffset.current.y + dy })
    }
  }, [])

  const handleTouchEnd = useCallback((e: React.TouchEvent) => {
    pinchStartDist.current = null
    if (zoomRef.current > 1) return // Don't navigate while zoomed
    if (touchStartX.current === null) return
    const dx = e.changedTouches[0].clientX - touchStartX.current
    touchStartX.current = null
    if (Math.abs(dx) < 30) return
    if (isRTL) {
      dx > 0 ? goNext() : goPrev()
    } else {
      dx > 0 ? goPrev() : goNext()
    }
  }, [isRTL, goNext, goPrev])

  // Keyboard shortcuts handled by parent (ReaderPage) — expose navigation via callback
  // We expose goNext/goPrev on a ref so the parent can call them
  // (Actually keyboard handling is done in the parent for comic mode too)

  const imgStyle: React.CSSProperties = fitMode === 'height'
    ? { maxHeight: '100%', maxWidth: '100%', objectFit: 'contain' }
    : { maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }

  // Reset zoom and loading state on page change
  useEffect(() => {
    setZoom(1)
    zoomRef.current = 1
    setPanOffset({ x: 0, y: 0 })
    setImageLoaded(false)
  }, [currentPage])

  // In spread mode, show two pages. For RTL: right page is current, left is next.
  // For LTR: left page is current, right is next.
  const hasSecondPage = spread && currentPage + 1 < totalPages

  return (
    <div
      className="flex-1 flex items-center justify-center overflow-hidden cursor-pointer select-none relative"
      style={{ background: themeColors.bg }}
      onClick={handleClick}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
    >
      <div
        className="flex items-center justify-center h-full gap-0"
        style={{
          transform: `scale(${zoom}) translate(${panOffset.x / zoom}px, ${panOffset.y / zoom}px)`,
          transition: zoom === 1 ? 'transform 0.2s ease-out' : undefined,
          flexDirection: isRTL ? 'row-reverse' : 'row',
        }}
      >
        <img
          key={currentPage}
          src={pageUrl(currentPage)}
          alt={`Page ${currentPage + 1}`}
          style={spread ? { maxHeight: '100%', maxWidth: hasSecondPage ? '50%' : '100%', objectFit: 'contain' } : imgStyle}
          onLoad={() => setImageLoaded(true)}
          draggable={false}
        />
        {hasSecondPage && (
          <img
            key={currentPage + 1}
            src={pageUrl(currentPage + 1)}
            alt={`Page ${currentPage + 2}`}
            style={{ maxHeight: '100%', maxWidth: '50%', objectFit: 'contain' }}
            draggable={false}
          />
        )}
      </div>
      {!imageLoaded && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <Loader2 className="w-6 h-6 animate-spin" style={{ color: themeColors.text, opacity: 0.5 }} />
        </div>
      )}
    </div>
  )
}

// ── Webtoon Reader ────────────────────────────────────────────────────────────

interface WebtoonReaderProps {
  bookId: string
  totalPages: number
  theme: ReaderTheme
  onProgress: (page: number, total: number) => void
  onReadComplete: () => void
}

function WebtoonReader({ bookId, totalPages, theme, onProgress, onReadComplete }: WebtoonReaderProps) {
  const themeColors = THEMES[theme]
  const webtoonToken = localStorage.getItem('tome_token') ?? ''
  const containerRef = useRef<HTMLDivElement>(null)
  const reportedRef = useRef(false)

  // Track scroll-based progress
  useEffect(() => {
    const container = containerRef.current
    if (!container || totalPages === 0) return

    function handleScroll() {
      if (!container) return
      const scrolled = container.scrollTop + container.clientHeight
      const total = container.scrollHeight
      const fraction = Math.min(scrolled / total, 1)
      const approxPage = Math.floor(fraction * totalPages)
      onProgress(approxPage, totalPages)
      if (fraction >= 0.99 && !reportedRef.current) {
        reportedRef.current = true
        onReadComplete()
      }
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [totalPages])

  return (
    <div
      ref={containerRef}
      className="flex-1 overflow-y-auto"
      style={{ background: themeColors.bg }}
    >
      <div className="flex flex-col items-center">
        {Array.from({ length: totalPages }, (_, i) => (
          <img
            key={i}
            src={`/api/books/${bookId}/pages/${i}?token=${encodeURIComponent(webtoonToken)}`}
            alt={`Page ${i + 1}`}
            className="w-full max-w-2xl"
            loading={i < 3 ? 'eager' : 'lazy'}
            draggable={false}
          />
        ))}
      </div>
    </div>
  )
}

// ── Main ReaderPage component ─────────────────────────────────────────────────

export default function ReaderPage() {
  const { bookId } = useParams<{ bookId: string }>()
  const navigate = useNavigate()

  const [book, setBook] = useState<Book | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // ── Shared state ────────────────────────────────────────────────────────────
  const [progress, setProgress] = useState(0)

  const [theme, setTheme] = useState<ReaderTheme>(
    () => (localStorage.getItem('reader_theme') as ReaderTheme) ?? 'light'
  )
  const [showSettings, setShowSettings] = useState(false)

  // ── EPUB-only state ─────────────────────────────────────────────────────────
  const [chapterLabel, setChapterLabel] = useState('')
  const [toc, setToc] = useState<TocItem[]>([])
  const [showToc, setShowToc] = useState(false)
  const [fontSize, setFontSize] = useState<number>(
    () => Number(localStorage.getItem('reader_font_size') ?? 100)
  )
  const [fontFamily, setFontFamily] = useState<FontFamily>(
    () => (localStorage.getItem('reader_font_family') as FontFamily) ?? 'default'
  )

  // ── Comic-only state ────────────────────────────────────────────────────────
  const [isComic, setIsComic] = useState(false)
  const [comicTotalPages, setComicTotalPages] = useState(0)
  const [comicCurrentPage, setComicCurrentPage] = useState(0)
  const [isRTL, setIsRTL] = useState(false)
  const [fitMode, setFitMode] = useState<FitMode>('width')
  const [comicMode, setComicMode] = useState<ComicMode>('page')
  const [spread, setSpread] = useState(() => typeof window !== 'undefined' && window.innerWidth >= 768)
  const [showToolbar, setShowToolbar] = useState(true)
  const [showThumbnails, setShowThumbnails] = useState(false)
  const toolbarTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const thumbnailActiveRef = useRef<HTMLButtonElement | null>(null)

  // ── EPUB refs ────────────────────────────────────────────────────────────────
  const containerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<FoliateViewElement | null>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const initialCfi = useRef<string | null>(null)
  const readyToSave = useRef(false)

  // ── Save progress (debounced) ────────────────────────────────────────────────

  const saveProgress = useCallback((page: number, total: number) => {
    if (!bookId) return
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      const fraction = total > 0 ? page / total : 0
      api.put(`/books/${bookId}/status`, {
        status: 'reading',
        progress_pct: fraction,
        cfi: `comic:${page}`,
      }).catch(() => {})
    }, 1500)
  }, [bookId])

  const handleComicReadComplete = useCallback(() => {
    if (!bookId) return
    api.put(`/books/${bookId}/status`, {
      status: 'read',
      progress_pct: 1,
      cfi: `comic:${comicTotalPages - 1}`,
    }).catch(() => {})
  }, [bookId, comicTotalPages])

  // Track progress whenever comicCurrentPage changes
  useEffect(() => {
    if (!isComic || comicTotalPages === 0) return
    setProgress(Math.round((comicCurrentPage / comicTotalPages) * 100))
    saveProgress(comicCurrentPage, comicTotalPages)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [comicCurrentPage, comicTotalPages, isComic])

  const handleComicProgress = useCallback((page: number, total: number) => {
    setComicCurrentPage(page)
    setProgress(total > 0 ? Math.round((page / total) * 100) : 0)
  }, [])

  // ── Toolbar auto-hide for comic mode ────────────────────────────────────────

  const resetToolbarTimer = useCallback(() => {
    setShowToolbar(true)
    if (toolbarTimer.current) clearTimeout(toolbarTimer.current)
    toolbarTimer.current = setTimeout(() => setShowToolbar(false), 3000)
  }, [])

  useEffect(() => {
    if (!isComic) return
    resetToolbarTimer()
    return () => {
      if (toolbarTimer.current) clearTimeout(toolbarTimer.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isComic])

  // ── Init ─────────────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!bookId) return
    let cancelled = false

    async function init() {
      let bookData: Book
      try {
        bookData = await api.get<Book>(`/books/${bookId}`)
        setBook(bookData)
      } catch {
        setLoadError('Book not found.')
        setLoading(false)
        return
      }

      const hasComic = bookData.files?.some((f: BookFile) => f.format === 'cbz' || f.format === 'cbr')
      const hasEpub = bookData.files?.some((f: BookFile) => f.format === 'epub')

      if (hasComic) {
        // Comic path: fetch page count, then stream pages
        setIsComic(true)

        let savedPage = 0
        try {
          const s = await api.get<{ status: string; progress_pct: number | null; cfi: string | null }>(`/books/${bookId}/status`)
          if (s.cfi?.startsWith('comic:')) {
            savedPage = parseInt(s.cfi.replace('comic:', ''), 10) || 0
          } else if (s.progress_pct) {
            // Will refine once we know totalPages
            savedPage = 0 // will be set below after we know total
          }
          api.put(`/books/${bookId}/status`, { status: 'reading' }).catch(() => {})
        } catch { /* no saved position */ }

        try {
          const pagesData = await api.get<{ total: number; pages: { index: number; filename: string }[] }>(`/books/${bookId}/pages`)
          if (cancelled) return
          setComicTotalPages(pagesData.total)
          // If we had a fractional progress but no cfi, approximate
          if (savedPage === 0) {
            try {
              const s = await api.get<{ status: string; progress_pct: number | null; cfi: string | null }>(`/books/${bookId}/status`)
              if (s.progress_pct && s.progress_pct > 0 && !s.cfi?.startsWith('comic:')) {
                savedPage = Math.floor(s.progress_pct * pagesData.total)
              }
            } catch { /* ignore */ }
          }
          setComicCurrentPage(savedPage)
          setProgress(pagesData.total > 0 ? Math.round((savedPage / pagesData.total) * 100) : 0)
        } catch (e: unknown) {
          setLoadError(`Failed to load comic pages: ${(e as Error).message}`)
          setLoading(false)
          return
        }

        setLoading(false)
        return
      }

      if (!hasEpub) {
        setLoadError('No readable file found for this book.')
        setLoading(false)
        return
      }

      // EPUB path (unchanged)
      let savedProgressPct = 0
      try {
        const s = await api.get<{ status: string; progress_pct: number | null; cfi: string | null }>(`/books/${bookId}/status`)
        if (s.cfi) initialCfi.current = s.cfi
        if (s.progress_pct) {
          savedProgressPct = s.progress_pct
          setProgress(Math.round(s.progress_pct * 100))
        }
      } catch { /* no saved position */ }

      api.put(`/books/${bookId}/status`, { status: 'reading' }).catch(() => {})

      if (!customElements.get('foliate-view')) {
        await new Promise<void>((resolve, reject) => {
          const script = document.createElement('script')
          script.type = 'module'
          script.src = '/foliate/view.js'
          script.onload = () => resolve()
          script.onerror = () => reject(new Error('Failed to load foliate/view.js'))
          document.head.appendChild(script)
        })
      }
      await customElements.whenDefined('foliate-view')

      if (cancelled) return

      const token = localStorage.getItem('tome_token') ?? ''
      let epubFile: File
      try {
        const resp = await fetch(`/api/books/${bookId}/read.epub?token=${encodeURIComponent(token)}`)
        if (!resp.ok) throw new Error(`Server returned ${resp.status}`)
        const blob = await resp.blob()
        epubFile = new File([blob], 'book.epub', { type: 'application/epub+zip' })
      } catch (e: unknown) {
        setLoadError(`Failed to load EPUB: ${(e as Error).message}`)
        setLoading(false)
        return
      }

      if (cancelled) return

      const container = containerRef.current
      if (!container) return

      const view = document.createElement('foliate-view') as FoliateViewElement
      view.style.cssText = 'display:block;width:100%;height:100%;'
      container.appendChild(view)
      viewRef.current = view

      view.addEventListener('load', () => {
        applyStyles()
        if (view.book?.toc) {
          setToc(flattenToc(view.book.toc))
        }
      })

      view.addEventListener('relocate', (e: Event) => {
        const detail = (e as CustomEvent).detail as {
          fraction?: number
          cfi?: string
          tocItem?: { label?: string }
        }
        const pct = Math.round((detail.fraction ?? 0) * 100)
        setProgress(pct)
        if (detail.tocItem?.label) setChapterLabel(detail.tocItem.label.trim())

        if (!readyToSave.current) return

        const fraction = detail.fraction ?? 0
        if (detail.cfi) saveCfi(detail.cfi, fraction)
        if (pct >= 95) {
          api.put(`/books/${bookId}/status`, { status: 'read', progress_pct: 1, cfi: detail.cfi ?? null }).catch(() => {})
        }
      })

      try {
        await view.open(epubFile)
      } catch (e: unknown) {
        setLoadError(`EPUB failed to open: ${(e as Error).message}`)
        setLoading(false)
        return
      }

      if (cancelled) return

      try {
        if (initialCfi.current) {
          await view.goTo(initialCfi.current)
        } else if (savedProgressPct > 0) {
          await view.goToFraction(savedProgressPct)
        } else {
          await view.goTo(0)
        }
      } catch { /* ignore nav errors */ }

      readyToSave.current = true
      setLoading(false)
    }

    init()

    return () => {
      cancelled = true
      if (saveTimer.current) clearTimeout(saveTimer.current)
      if (viewRef.current) {
        viewRef.current.remove()
        viewRef.current = null
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId])

  // ── Apply styles whenever EPUB settings change ────────────────────────────

  const applyStyles = useCallback(() => {
    const view = viewRef.current
    if (!view?.renderer?.setStyles) return
    view.renderer.setStyles(buildReaderCSS(theme, fontSize, fontFamily))
  }, [theme, fontSize, fontFamily])

  useEffect(() => {
    if (!isComic) applyStyles()
  }, [applyStyles, isComic])

  // ── Save EPUB position (debounced) ────────────────────────────────────────

  const saveCfi = useCallback((cfi: string, fraction: number) => {
    if (!bookId) return
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      api.put(`/books/${bookId}/status`, {
        status: 'reading',
        progress_pct: fraction,
        cfi,
      }).catch(() => {})
    }, 1500)
  }, [bookId])

  // ── EPUB navigation ────────────────────────────────────────────────────────

  function epubPrev() { viewRef.current?.prev() }
  function epubNext() { viewRef.current?.next() }

  useEffect(() => {
    if (isComic) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') epubPrev()
      else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') epubNext()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isComic])

  // ── Scroll active thumbnail into view ───────────────────────────────────────

  useEffect(() => {
    if (showThumbnails && thumbnailActiveRef.current) {
      thumbnailActiveRef.current.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' })
    }
  }, [comicCurrentPage, showThumbnails])

  // ── Comic keyboard navigation ─────────────────────────────────────────────

  useEffect(() => {
    if (!isComic) return
    function onKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      resetToolbarTimer()
      const kStep = spread ? 2 : 1
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        setComicCurrentPage((p) => {
          const next = isRTL ? Math.max(0, p - kStep) : Math.min(comicTotalPages - 1, p + kStep)
          return next
        })
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        setComicCurrentPage((p) => {
          const next = isRTL ? Math.min(comicTotalPages - 1, p + kStep) : Math.max(0, p - kStep)
          return next
        })
      } else if (e.key === 'f' || e.key === 'F') {
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
        else document.documentElement.requestFullscreen().catch(() => {})
      } else if (e.key === 'r' || e.key === 'R') {
        setIsRTL(prev => !prev)
      } else if (e.key === 'w' || e.key === 'W') {
        setFitMode(m => m === 'width' ? 'height' : 'width')
      } else if (e.key === 's' || e.key === 'S') {
        setSpread(prev => !prev)
      } else if (e.key === 't' || e.key === 'T') {
        setShowThumbnails(prev => !prev)
      } else if (e.key === 'Escape') {
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
        else navigate(-1)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isComic, isRTL, comicTotalPages, spread, resetToolbarTimer])

  function goToHref(href: string) {
    viewRef.current?.goTo(href).catch(() => {})
    setShowToc(false)
  }

  function changeFontSize(delta: number) {
    setFontSize(prev => {
      const next = Math.min(200, Math.max(60, prev + delta))
      localStorage.setItem('reader_font_size', String(next))
      return next
    })
  }

  function selectTheme(t: ReaderTheme) {
    setTheme(t)
    localStorage.setItem('reader_theme', t)
  }

  function selectFontFamily(f: FontFamily) {
    setFontFamily(f)
    localStorage.setItem('reader_font_family', f)
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function flattenToc(items: TocItem[]): TocItem[] {
    const result: TocItem[] = []
    for (const item of items) {
      result.push(item)
      if (item.subitems?.length) result.push(...flattenToc(item.subitems))
    }
    return result
  }

  // ── Render ────────────────────────────────────────────────────────────────

  const themeColors = THEMES[theme]
  const isDarkTheme = theme === 'dark'

  if (loadError) {
    return (
      <div className="flex flex-col items-center justify-center h-screen gap-4 text-muted-foreground">
        <BookOpen className="w-12 h-12 opacity-30" />
        <p className="text-sm">{loadError}</p>
        <button onClick={() => navigate(-1)} className="text-sm text-primary hover:underline">Go back</button>
      </div>
    )
  }

  // ── Comic reader layout ────────────────────────────────────────────────────

  if (isComic) {
    return (
      <div
        className="fixed inset-0 z-50 flex flex-col"
        style={{ background: themeColors.bg }}
        onMouseMove={resetToolbarTimer}
        onTouchStart={resetToolbarTimer}
      >
        {/* Top toolbar (auto-hide) */}
        <div
          className={cn(
            'flex items-center gap-3 px-4 h-12 shrink-0 border-b z-10 transition-transform duration-200',
            showToolbar ? 'translate-y-0' : '-translate-y-full'
          )}
          style={{ background: themeColors.bg, borderColor: isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }}
        >
          <button
            onClick={() => navigate(-1)}
            className={cn('p-1.5 rounded-lg transition-colors', isDarkTheme ? 'hover:bg-white/10 text-white/70' : 'hover:bg-black/10 text-black/60')}
          >
            <ArrowLeft className="w-4 h-4" />
          </button>

          <span className="flex-1 text-sm font-medium truncate" style={{ color: themeColors.text }}>
            {book?.title ?? ''}
          </span>

          {/* RTL toggle */}
          <button
            onClick={() => setIsRTL((v) => !v)}
            className={cn(
              'p-1.5 rounded-lg transition-colors text-xs font-bold',
              isRTL
                ? (isDarkTheme ? 'bg-white/20' : 'bg-black/15')
                : (isDarkTheme ? 'hover:bg-white/10 text-white/50' : 'hover:bg-black/10 text-black/40')
            )}
            title="Toggle RTL (manga) mode"
            style={{ color: themeColors.text }}
          >
            RTL
          </button>

          {/* Fit mode toggle */}
          <button
            onClick={() => setFitMode((m) => m === 'width' ? 'height' : 'width')}
            className={cn('p-1.5 rounded-lg transition-colors', isDarkTheme ? 'hover:bg-white/10 text-white/70' : 'hover:bg-black/10 text-black/60')}
            title={fitMode === 'width' ? 'Switch to fit height' : 'Switch to fit width'}
            style={{ color: themeColors.text }}
          >
            {fitMode === 'width' ? <RotateCcw className="w-4 h-4" /> : <AlignJustify className="w-4 h-4" />}
          </button>

          {/* Spread toggle */}
          {comicMode === 'page' && (
            <button
              onClick={() => setSpread(prev => !prev)}
              className={cn('p-1.5 rounded-lg transition-colors', isDarkTheme ? 'hover:bg-white/10 text-white/70' : 'hover:bg-black/10 text-black/60',
                spread && (isDarkTheme ? 'bg-white/15' : 'bg-black/10')
              )}
              title={spread ? 'Single page (S)' : 'Two-page spread (S)'}
              style={{ color: themeColors.text }}
            >
              {spread ? <Square className="w-4 h-4" /> : <Columns2 className="w-4 h-4" />}
            </button>
          )}

          {/* Thumbnail strip toggle */}
          {comicMode === 'page' && (
            <button
              onClick={() => setShowThumbnails(prev => !prev)}
              className={cn('p-1.5 rounded-lg transition-colors', isDarkTheme ? 'hover:bg-white/10 text-white/70' : 'hover:bg-black/10 text-black/60',
                showThumbnails && (isDarkTheme ? 'bg-white/15' : 'bg-black/10')
              )}
              title="Page thumbnails (T)"
              style={{ color: themeColors.text }}
            >
              <GalleryHorizontalEnd className="w-4 h-4" />
            </button>
          )}

          {/* Webtoon / Page mode toggle */}
          <button
            onClick={() => setComicMode((m) => m === 'page' ? 'webtoon' : 'page')}
            className={cn('p-1.5 rounded-lg transition-colors', isDarkTheme ? 'hover:bg-white/10 text-white/70' : 'hover:bg-black/10 text-black/60')}
            title={comicMode === 'page' ? 'Switch to webtoon (scroll) mode' : 'Switch to page mode'}
            style={{ color: themeColors.text }}
          >
            {comicMode === 'page' ? <Rows4 className="w-4 h-4" /> : <BookOpen className="w-4 h-4" />}
          </button>

          {/* Theme settings */}
          <button
            onClick={() => setShowSettings((o) => !o)}
            className={cn(
              'p-1.5 rounded-lg transition-colors',
              isDarkTheme ? 'hover:bg-white/10' : 'hover:bg-black/10',
              showSettings && (isDarkTheme ? 'bg-white/15' : 'bg-black/10')
            )}
            title="Reader settings"
            style={{ color: themeColors.text }}
          >
            <Settings className="w-4 h-4" />
          </button>
        </div>

        {/* Main area */}
        <div className="flex flex-1 overflow-hidden relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center z-10">
              <Loader2 className="w-6 h-6 animate-spin" style={{ color: themeColors.text, opacity: 0.4 }} />
            </div>
          )}

          {!loading && comicMode === 'page' && (
            <StreamingComicReader
              bookId={bookId!}
              totalPages={comicTotalPages}
              currentPage={comicCurrentPage}
              isRTL={isRTL}
              fitMode={fitMode}
              spread={spread}
              theme={theme}
              onPageChange={setComicCurrentPage}
              onReadComplete={handleComicReadComplete}
            />
          )}

          {!loading && comicMode === 'webtoon' && (
            <WebtoonReader
              bookId={bookId!}
              totalPages={comicTotalPages}
              theme={theme}
              onProgress={handleComicProgress}
              onReadComplete={handleComicReadComplete}
            />
          )}

          {/* Settings panel */}
          {showSettings && (
            <div
              className="absolute inset-y-0 right-0 w-64 max-w-[85vw] shrink-0 border-l overflow-y-auto flex flex-col z-20"
              style={{ background: themeColors.bg, borderColor: isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }}
            >
              <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }}>
                <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: themeColors.text, opacity: 0.5 }}>Settings</span>
                <button onClick={() => setShowSettings(false)} style={{ color: themeColors.text, opacity: 0.5 }}>
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="p-4 flex flex-col gap-6">
                <div>
                  <p className="text-xs font-medium mb-3" style={{ color: themeColors.text, opacity: 0.5 }}>BACKGROUND</p>
                  <div className="flex gap-2">
                    {(Object.keys(THEMES) as ReaderTheme[]).map((t) => (
                      <button
                        key={t}
                        onClick={() => selectTheme(t)}
                        className={cn(
                          'flex-1 py-2.5 rounded-lg border text-xs font-medium transition-all',
                          theme === t ? 'ring-2' : 'opacity-60 hover:opacity-90'
                        )}
                        style={{
                          background: THEMES[t].bg,
                          color: THEMES[t].text,
                          borderColor: THEMES[t].text + '33',
                        }}
                      >
                        {THEMES[t].label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Thumbnail strip */}
        {showThumbnails && comicMode === 'page' && comicTotalPages > 0 && (
          <div
            className="flex items-center px-2 h-10 shrink-0 border-t overflow-x-auto z-10 scrollbar-none"
            style={{ background: themeColors.bg, borderColor: isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }}
          >
            <div className="flex gap-1 mx-auto">
              {Array.from({ length: comicTotalPages }, (_, i) => (
                <button
                  key={i}
                  ref={i === comicCurrentPage ? thumbnailActiveRef : undefined}
                  onClick={() => setComicCurrentPage(i)}
                  className={cn(
                    'shrink-0 w-7 h-7 rounded text-[10px] font-medium transition-colors',
                    i === comicCurrentPage
                      ? 'bg-primary text-primary-foreground'
                      : isDarkTheme
                        ? 'bg-white/10 text-white/50 hover:bg-white/20'
                        : 'bg-black/8 text-black/40 hover:bg-black/15'
                  )}
                >
                  {i + 1}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Bottom bar (auto-hide) */}
        {comicMode === 'page' && (
          <div
            className={cn(
              'flex items-center gap-2 sm:gap-4 px-2 sm:px-4 h-11 shrink-0 border-t z-10 transition-transform duration-200',
              showToolbar ? 'translate-y-0' : 'translate-y-full'
            )}
            style={{ background: themeColors.bg, borderColor: isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }}
          >
            <button
              onClick={() => setComicCurrentPage((p) => Math.max(0, p - 1))}
              className="p-1 transition-opacity hover:opacity-100"
              style={{ color: themeColors.text, opacity: 0.5 }}
            >
              <ChevronLeft className="w-4 h-4" />
            </button>

            <span className="flex-1 text-xs truncate text-center" style={{ color: themeColors.text, opacity: 0.5 }}>
              {comicTotalPages > 0 ? `${comicCurrentPage + 1} / ${comicTotalPages}` : ''}
            </span>

            <div className="flex items-center gap-2">
              <div className="w-16 sm:w-24 h-1 rounded-full overflow-hidden" style={{ background: isDarkTheme ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.12)' }}>
                <div
                  className="h-full rounded-full transition-all duration-300"
                  style={{ width: `${progress}%`, background: isDarkTheme ? 'rgba(255,255,255,0.6)' : 'rgba(0,0,0,0.4)' }}
                />
              </div>
              <span className="text-xs w-8 text-right" style={{ color: themeColors.text, opacity: 0.5 }}>{progress}%</span>
            </div>

            <button
              onClick={() => setComicCurrentPage((p) => Math.min(comicTotalPages - 1, p + 1))}
              className="p-1 transition-opacity hover:opacity-100"
              style={{ color: themeColors.text, opacity: 0.5 }}
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>
    )
  }

  // ── EPUB reader layout (unchanged) ────────────────────────────────────────

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col select-none"
      style={{ background: themeColors.bg }}
    >
      {/* Top bar */}
      <div
        className={cn('flex items-center gap-3 px-4 h-12 shrink-0 border-b z-10')}
        style={{ background: themeColors.bg, borderColor: isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }}
      >
        <button
          onClick={() => navigate(-1)}
          className={cn('p-1.5 rounded-lg transition-colors', isDarkTheme ? 'hover:bg-white/10 text-white/70' : 'hover:bg-black/10 text-black/60')}
        >
          <ArrowLeft className="w-4 h-4" />
        </button>

        <span className="flex-1 text-sm font-medium truncate" style={{ color: themeColors.text }}>
          {book?.title ?? ''}
        </span>

        <button
          onClick={() => { setShowToc((o) => !o); setShowSettings(false) }}
          className={cn('p-1.5 rounded-lg transition-colors', isDarkTheme ? 'hover:bg-white/10' : 'hover:bg-black/10', showToc && (isDarkTheme ? 'bg-white/15' : 'bg-black/10'))}
          title="Table of contents"
          style={{ color: themeColors.text }}
        >
          <AlignJustify className="w-4 h-4" />
        </button>
        <button
          onClick={() => { setShowSettings((o) => !o); setShowToc(false) }}
          className={cn('p-1.5 rounded-lg transition-colors', isDarkTheme ? 'hover:bg-white/10' : 'hover:bg-black/10', showSettings && (isDarkTheme ? 'bg-white/15' : 'bg-black/10'))}
          title="Reader settings"
          style={{ color: themeColors.text }}
        >
          <Settings className="w-4 h-4" />
        </button>
      </div>

      {/* Main area */}
      <div className="flex flex-1 overflow-hidden relative">

        {/* TOC drawer */}
        {showToc && (
          <div
            className="absolute inset-y-0 left-0 md:relative md:inset-auto w-72 max-w-[85vw] shrink-0 border-r overflow-y-auto flex flex-col z-20"
            style={{ background: themeColors.bg, borderColor: isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }}>
              <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: themeColors.text, opacity: 0.5 }}>Contents</span>
              <button onClick={() => setShowToc(false)} style={{ color: themeColors.text, opacity: 0.5 }}>
                <X className="w-4 h-4" />
              </button>
            </div>
            {toc.length === 0 && (
              <p className="px-4 py-6 text-xs" style={{ color: themeColors.text, opacity: 0.4 }}>No table of contents available.</p>
            )}
            {toc.map((item, i) => (
              <button
                key={i}
                onClick={() => goToHref(item.href)}
                className="text-left px-4 py-2.5 text-sm transition-opacity hover:opacity-100"
                style={{ color: themeColors.text, opacity: 0.75 }}
              >
                {item.label?.trim()}
              </button>
            ))}
          </div>
        )}

        {/* foliate-view container */}
        <div className="flex-1 overflow-hidden relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center z-10">
              <div className="w-6 h-6 border-2 border-current border-t-transparent rounded-full animate-spin opacity-40" style={{ color: themeColors.text }} />
            </div>
          )}
          <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />

          {/* Click zones for prev/next */}
          <div className="absolute inset-y-0 left-0 w-1/5 cursor-pointer z-10" onClick={epubPrev} />
          <div className="absolute inset-y-0 right-0 w-1/5 cursor-pointer z-10" onClick={epubNext} />
        </div>

        {/* Settings panel */}
        {showSettings && (
          <div
            className="absolute inset-y-0 right-0 md:relative md:inset-auto w-72 max-w-[85vw] shrink-0 border-l overflow-y-auto flex flex-col z-20"
            style={{ background: themeColors.bg, borderColor: isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }}>
              <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: themeColors.text, opacity: 0.5 }}>Settings</span>
              <button onClick={() => setShowSettings(false)} style={{ color: themeColors.text, opacity: 0.5 }}>
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-4 flex flex-col gap-6">
              {/* Font size */}
              <div>
                <p className="text-xs font-medium mb-3" style={{ color: themeColors.text, opacity: 0.5 }}>FONT SIZE</p>
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => changeFontSize(-10)}
                    className="w-9 h-9 rounded-lg border flex items-center justify-center transition-opacity hover:opacity-100"
                    style={{ borderColor: isDarkTheme ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.15)', color: themeColors.text, opacity: 0.7 }}
                  >
                    <Minus className="w-4 h-4" />
                  </button>
                  <span className="flex-1 text-center text-sm font-medium" style={{ color: themeColors.text }}>{fontSize}%</span>
                  <button
                    onClick={() => changeFontSize(10)}
                    className="w-9 h-9 rounded-lg border flex items-center justify-center transition-opacity hover:opacity-100"
                    style={{ borderColor: isDarkTheme ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.15)', color: themeColors.text, opacity: 0.7 }}
                  >
                    <Plus className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* Font family */}
              <div>
                <p className="text-xs font-medium mb-3" style={{ color: themeColors.text, opacity: 0.5 }}>FONT</p>
                <div className="flex gap-2">
                  {(Object.keys(FONT_FAMILIES) as FontFamily[]).map((f) => (
                    <button
                      key={f}
                      onClick={() => selectFontFamily(f)}
                      className={cn(
                        'flex-1 py-2 rounded-lg border text-xs font-medium transition-all',
                        fontFamily === f ? 'border-transparent' : 'opacity-50 hover:opacity-75'
                      )}
                      style={{
                        color: themeColors.text,
                        borderColor: fontFamily === f ? themeColors.text : (isDarkTheme ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.15)'),
                        background: fontFamily === f ? (isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.08)') : 'transparent',
                        fontFamily: FONT_FAMILIES[f].css,
                      }}
                    >
                      {FONT_FAMILIES[f].label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Theme */}
              <div>
                <p className="text-xs font-medium mb-3" style={{ color: themeColors.text, opacity: 0.5 }}>THEME</p>
                <div className="flex gap-2">
                  {(Object.keys(THEMES) as ReaderTheme[]).map((t) => (
                    <button
                      key={t}
                      onClick={() => selectTheme(t)}
                      className={cn(
                        'flex-1 py-2.5 rounded-lg border text-xs font-medium transition-all',
                        theme === t ? 'ring-2' : 'opacity-60 hover:opacity-90'
                      )}
                      style={{
                        background: THEMES[t].bg,
                        color: THEMES[t].text,
                        borderColor: THEMES[t].text + '33',
                      }}
                    >
                      {THEMES[t].label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Bottom bar */}
      <div
        className="flex items-center gap-2 sm:gap-4 px-2 sm:px-4 h-11 shrink-0 border-t z-10"
        style={{ background: themeColors.bg, borderColor: isDarkTheme ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }}
      >
        <button onClick={epubPrev} className="p-1 transition-opacity hover:opacity-100" style={{ color: themeColors.text, opacity: 0.5 }}>
          <ChevronLeft className="w-4 h-4" />
        </button>

        <span className="flex-1 text-xs truncate text-center" style={{ color: themeColors.text, opacity: 0.5 }}>
          {chapterLabel}
        </span>

        <div className="flex items-center gap-2">
          <div className="w-16 sm:w-24 h-1 rounded-full overflow-hidden" style={{ background: isDarkTheme ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.12)' }}>
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{ width: `${progress}%`, background: isDarkTheme ? 'rgba(255,255,255,0.6)' : 'rgba(0,0,0,0.4)' }}
            />
          </div>
          <span className="text-xs w-8 text-right" style={{ color: themeColors.text, opacity: 0.5 }}>{progress}%</span>
        </div>

        <button onClick={epubNext} className="p-1 transition-opacity hover:opacity-100" style={{ color: themeColors.text, opacity: 0.5 }}>
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
