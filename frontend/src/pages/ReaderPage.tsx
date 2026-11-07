import { useEffect, useRef, useState, useCallback, forwardRef, useImperativeHandle } from 'react'
import { Trans } from '@lingui/react/macro'
import { t, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import {
  BookOpen, Settings, Rows4,
  Minus, Plus, X,
  Loader2, AlignJustify, RotateCcw, GalleryHorizontalEnd, Columns2, Square,
  StretchHorizontal, StretchVertical, StickyNote, Trash2,
} from 'lucide-react'
import * as pdfjsLib from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { api } from '@/lib/api'
import type { Book, BookFile } from '@/lib/books'
import { cn } from '@/lib/utils'
import { Pill, Textarea } from '@/components/page'
import {
  THEMES, type ReaderTheme, readerVars, ReaderTopBar, ReaderBottomBar, ReaderPanel, ChromeIcon,
  ThemeSetting, FontSizeSetting, ChoiceSetting,
} from '@/components/reader/ReaderChrome'
import { AnnotationPainter, fillForColor, isWebAnnotation, type ReaderAnnotation } from '@/lib/readerAnnotations'

// pdf.js renders pages off the main thread; point it at the bundled worker.
pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl

// ── Types ─────────────────────────────────────────────────────────────────────

type FontFamily = 'default' | 'serif' | 'sans'
type FitMode = 'width' | 'height'
type ComicMode = 'page' | 'webtoon'

interface TocItem {
  label: string
  href: string
  subitems?: TocItem[]
  depth?: number
}

interface FoliateViewElement extends HTMLElement {
  renderer?: { setStyles(css: string): void } | null
  book?: { toc?: TocItem[] } | null
  open(file: File): Promise<void>
  goTo(target: string | number): Promise<void>
  goToFraction(fraction: number): Promise<void>
  getCFI(index: number, range: Range): string
  prev(): void
  next(): void
}

// KOReader wall-clock format - annotations compare timestamps as strings.
function koNow(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// Colour choices for web-created highlights (KOReader's common palette).
const HIGHLIGHT_COLORS = ['yellow', 'green', 'blue', 'purple', 'red'] as const

// ── Theme / font definitions ──────────────────────────────────────────────────

/* eslint-disable lingui/no-unlocalized-strings -- CSS font stacks */
const FONT_FAMILIES: Record<FontFamily, { css: string; label: MessageDescriptor }> = {
  default: { css: 'inherit', label: msg`Default` },
  serif:   { css: '"Georgia", "Times New Roman", serif', label: msg`Serif` },
  sans:    { css: '"Inter", "Helvetica Neue", sans-serif', label: msg`Sans` },
}
/* eslint-enable lingui/no-unlocalized-strings */

// ── Tap zones (issue #192) ────────────────────────────────────────────────────
// Where a tap on the reading surface turns the page. 'swapped' puts "next" on
// the left so the device can be held one-handed in the left hand. Swipes and
// keyboard navigation are unaffected.

type TapZoneMode = 'default' | 'swapped' | 'off'

const TAP_ZONE_MODES: Record<TapZoneMode, { label: MessageDescriptor; hint: MessageDescriptor }> = {
  default: { label: msg`Default`, hint: msg`Tap right for next page` },
  swapped: { label: msg`Swapped`, hint: msg`Tap left for next page` },
  off:     { label: msg`Off`,     hint: msg`Turn pages by swipe only` },
}

function loadTapZoneMode(): TapZoneMode {
  const v = localStorage.getItem('reader_tap_zones')
  return v === 'swapped' || v === 'off' ? v : 'default'
}

function TapZoneSetting({ value, onChange }: { value: TapZoneMode; onChange: (m: TapZoneMode) => void }) {
  return (
    <ChoiceSetting
      label={<Trans>Tap zones</Trans>}
      value={value}
      onChange={onChange}
      options={(Object.keys(TAP_ZONE_MODES) as TapZoneMode[]).map(m => ({ value: m, label: i18n._(TAP_ZONE_MODES[m].label) }))}
      hint={i18n._(TAP_ZONE_MODES[value].hint)}
    />
  )
}

// Build CSS string to inject into epub renderer
/* eslint-disable lingui/no-unlocalized-strings -- CSS template, not user-facing text */
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
/* eslint-enable lingui/no-unlocalized-strings */

// ── Streaming Comic Reader ────────────────────────────────────────────────────

interface StreamingComicReaderProps {
  bookId: string
  totalPages: number
  currentPage: number
  isRTL: boolean
  fitMode: FitMode
  spread: boolean
  theme: ReaderTheme
  tapZones: TapZoneMode
  onPageChange: (page: number) => void
  onReadComplete: () => void
  onCenterTap: () => void
}

function StreamingComicReader({
  bookId,
  totalPages,
  currentPage,
  isRTL,
  fitMode,
  spread,
  theme,
  tapZones,
  onPageChange,
  onReadComplete,
  onCenterTap,
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

  // Click navigation in thirds: the centre shows or hides the chrome, the
  // left and right thirds turn pages (respects RTL and the tap-zone setting;
  // page turns are disabled when zoomed in).
  const handleClick = useCallback((e: React.MouseEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const clickX = e.clientX - rect.left
    if (clickX > rect.width / 3 && clickX < (rect.width * 2) / 3) { onCenterTap(); return }
    if (zoomRef.current > 1 || tapZones === 'off') return
    const isLeft = clickX <= rect.width / 3
    let forward = isRTL ? isLeft : !isLeft
    if (tapZones === 'swapped') forward = !forward
    if (forward) goNext()
    else goPrev()
  }, [isRTL, tapZones, goNext, goPrev, onCenterTap])

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
        // Double tap - toggle zoom
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
    if ((dx > 0) === isRTL) goNext()
    else goPrev()
  }, [isRTL, goNext, goPrev])

  // Keyboard shortcuts handled by parent (ReaderPage) - expose navigation via callback
  // We expose goNext/goPrev on a ref so the parent can call them
  // (Actually keyboard handling is done in the parent for comic mode too)

  // The page box is the full reader area, so the percentages below resolve
  // against the viewport and a page never spills past the right edge.
  const imgStyle: React.CSSProperties = fitMode === 'height'
    ? { maxHeight: '100%', maxWidth: '100%', objectFit: 'contain', minWidth: 0 }
    : { maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', minWidth: 0 }

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
      className="flex-1 min-w-0 flex items-center justify-center overflow-hidden cursor-pointer select-none relative"
      style={{ background: themeColors.bg }}
      onClick={handleClick}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
    >
      <div
        className="flex items-center justify-center w-full h-full gap-0"
        style={{
          transform: `scale(${zoom}) translate(${panOffset.x / zoom}px, ${panOffset.y / zoom}px)`,
          transition: zoom === 1 ? 'transform 0.2s ease-out' : undefined,
          flexDirection: isRTL ? 'row-reverse' : 'row',
        }}
      >
        <img
          key={currentPage}
          src={pageUrl(currentPage)}
          alt={(() => { const n = currentPage + 1; return t`Page ${n}` })()}
          style={spread ? { maxHeight: '100%', maxWidth: hasSecondPage ? '50%' : '100%', objectFit: 'contain', minWidth: 0 } : imgStyle}
          onLoad={() => setImageLoaded(true)}
          onError={() => setImageLoaded(true)}
          draggable={false}
        />
        {hasSecondPage && (
          <img
            key={currentPage + 1}
            src={pageUrl(currentPage + 1)}
            alt={(() => { const n = currentPage + 2; return t`Page ${n}` })()}
            style={{ maxHeight: '100%', maxWidth: '50%', objectFit: 'contain', minWidth: 0 }}
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
            alt={(() => { const n = i + 1; return t`Page ${n}` })()}
            className="w-full max-w-2xl"
            loading={i < 3 ? 'eager' : 'lazy'}
            draggable={false}
          />
        ))}
      </div>
    </div>
  )
}

// ── PDF Reader ──────────────────────────────────────────────────────────────
//
// Continuous vertical scroll backed by pdf.js. Pages are virtualized: each page
// is a fixed-size placeholder and only those near the viewport are rendered to a
// canvas (others are torn down to bound memory on large PDFs). PDFs are fixed
// layout, so unlike EPUB there is no font/size reflow - what's customizable is
// the page tint (theme), fit mode (width/height) and zoom.

interface PdfReaderHandle {
  scrollToPage: (page: number) => void
}

interface PdfReaderProps {
  bookId: string
  initialPage: number       // resolved page index, or -1 if unknown
  initialFraction: number   // fallback when initialPage < 0
  theme: ReaderTheme
  fitMode: FitMode
  zoom: number
  onDocLoaded: (total: number) => void
  onError: (message: string) => void
  onProgress: (page: number, total: number) => void
  onReadComplete: () => void
}

const PdfReader = forwardRef<PdfReaderHandle, PdfReaderProps>(function PdfReader(
  { bookId, initialPage, initialFraction, theme, fitMode, zoom, onDocLoaded, onError, onProgress, onReadComplete },
  ref,
) {
  const themeColors = THEMES[theme]
  const token = localStorage.getItem('tome_token') ?? ''

  const containerRef = useRef<HTMLDivElement>(null)
  const pageWrapRefs = useRef<(HTMLDivElement | null)[]>([])
  const canvasRefs = useRef<(HTMLCanvasElement | null)[]>([])
  const docRef = useRef<import('pdfjs-dist').PDFDocumentProxy | null>(null)
  const renderTasks = useRef<Map<number, import('pdfjs-dist').RenderTask>>(new Map())
  const renderedScale = useRef<Map<number, number>>(new Map())
  const observerRef = useRef<IntersectionObserver | null>(null)
  const visiblePages = useRef<Set<number>>(new Set())
  const didInitialScroll = useRef(false)
  const reportedComplete = useRef(false)
  const currentPageRef = useRef(0)

  const [numPages, setNumPages] = useState(0)
  // Page-1 dimensions at scale 1, used to size placeholders before render.
  const [base, setBase] = useState<{ w: number; h: number } | null>(null)
  const [containerW, setContainerW] = useState(0)
  const [containerH, setContainerH] = useState(0)
  // Per-page real heights once rendered (corrects the page-1 estimate).
  const [pageHeights, setPageHeights] = useState<Map<number, number>>(new Map())

  // CSS display width for every page given the current fit mode + zoom.
  const displayW = (() => {
    if (!base || !containerW || !containerH) return 0
    const aspect = base.h / base.w
    if (fitMode === 'height') {
      const h = (containerH - 24) * zoom
      return h / aspect
    }
    return (Math.min(containerW - 24, 1100)) * zoom
  })()

  const estPageHeight = base && displayW ? displayW * (base.h / base.w) : 0
  const heightFor = (i: number) => pageHeights.get(i) ?? estPageHeight

  // ── Load the document ──────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    const url = `/api/books/${bookId}/read.pdf?token=${encodeURIComponent(token)}`
    const task = pdfjsLib.getDocument({ url })
    task.promise.then(async (doc) => {
      if (cancelled) { task.destroy(); return }
      docRef.current = doc
      const page1 = await doc.getPage(1)
      const vp = page1.getViewport({ scale: 1 })
      if (cancelled) return
      setBase({ w: vp.width, h: vp.height })
      setNumPages(doc.numPages)
      onDocLoaded(doc.numPages)
    }).catch((e) => {
      if (!cancelled) { const msg2 = (e as Error).message; onError(t`Failed to load PDF: ${msg2}`) }
    })
    return () => {
      cancelled = true
      renderTasks.current.forEach((t) => { try { t.cancel() } catch { /* ignore */ } })
      renderTasks.current.clear()
      // Destroying the loading task aborts requests + tears down the worker/doc.
      task.destroy()
      docRef.current = null
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId])

  // ── Track container size ───────────────────────────────────────────────────
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => { setContainerW(el.clientWidth); setContainerH(el.clientHeight) }
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ── Render / tear down a single page ───────────────────────────────────────
  const renderPage = useCallback(async (i: number) => {
    const doc = docRef.current
    const canvas = canvasRefs.current[i]
    if (!doc || !canvas || !displayW) return
    if (renderedScale.current.get(i) === displayW) return // already current

    const existing = renderTasks.current.get(i)
    if (existing) { try { existing.cancel() } catch { /* ignore */ } renderTasks.current.delete(i) }

    try {
      const page = await doc.getPage(i + 1)
      const unit = page.getViewport({ scale: 1 })
      const cssScale = displayW / unit.width
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const viewport = page.getViewport({ scale: cssScale * dpr })
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      canvas.width = Math.floor(viewport.width)
      canvas.height = Math.floor(viewport.height)
      canvas.style.width = `${displayW}px`
      canvas.style.height = `${displayW * (unit.height / unit.width)}px`
      const task = page.render({ canvas, canvasContext: ctx, viewport })
      renderTasks.current.set(i, task)
      await task.promise
      renderTasks.current.delete(i)
      renderedScale.current.set(i, displayW)
      setPageHeights((prev) => {
        const real = displayW * (unit.height / unit.width)
        if (prev.get(i) === real) return prev
        const next = new Map(prev)
        next.set(i, real)
        return next
      })
    } catch (e) {
      // RenderingCancelledException is expected on fast scroll / rescale.
      if ((e as { name?: string })?.name !== 'RenderingCancelledException') {
        renderTasks.current.delete(i)
      }
    }
  }, [displayW])

  const clearPage = useCallback((i: number) => {
    const task = renderTasks.current.get(i)
    if (task) { try { task.cancel() } catch { /* ignore */ } renderTasks.current.delete(i) }
    const canvas = canvasRefs.current[i]
    if (canvas) { canvas.width = 0; canvas.height = 0 }
    renderedScale.current.delete(i)
  }, [])

  // ── Observe pages; render those near the viewport, tear down the rest ──────
  useEffect(() => {
    if (!numPages || !displayW) return
    const root = containerRef.current
    if (!root) return

    const obs = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const i = Number((entry.target as HTMLElement).dataset.page)
        if (entry.isIntersecting) {
          visiblePages.current.add(i)
          renderPage(i)
        } else {
          visiblePages.current.delete(i)
          clearPage(i)
        }
      }
    }, { root, rootMargin: '1200px 0px' }) // eslint-disable-line lingui/no-unlocalized-strings -- CSS margin
    observerRef.current = obs
    pageWrapRefs.current.forEach((el) => { if (el) obs.observe(el) })
    return () => { obs.disconnect(); observerRef.current = null }
  // Re-attach when pages first appear or width becomes known; zoom changes keep
  // displayW truthy so this doesn't churn on every zoom step.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [numPages, displayW > 0])

  // On scale change (zoom / fit mode), re-render the pages still on screen.
  useEffect(() => {
    if (!displayW) return
    renderedScale.current.clear()
    visiblePages.current.forEach((i) => renderPage(i))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayW])

  // ── Scroll → current page + progress ───────────────────────────────────────
  useEffect(() => {
    const el = containerRef.current
    if (!el || !numPages) return
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        const mid = el.scrollTop + el.clientHeight / 2
        let acc = 0
        let page = 0
        for (let i = 0; i < numPages; i++) {
          const h = heightFor(i) + 16 // wrapper + gap
          if (mid < acc + h) { page = i; break }
          acc += h
          page = i
        }
        if (page !== currentPageRef.current) {
          currentPageRef.current = page
          onProgress(page, numPages)
        }
        const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 4
        if (atBottom && !reportedComplete.current) {
          reportedComplete.current = true
          onReadComplete()
        }
      })
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => { el.removeEventListener('scroll', onScroll); if (raf) cancelAnimationFrame(raf) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [numPages, pageHeights, displayW])

  // ── Initial scroll to the saved position ───────────────────────────────────
  useEffect(() => {
    if (didInitialScroll.current || !numPages || !estPageHeight) return
    const target = initialPage >= 0
      ? initialPage
      : Math.round(initialFraction * (numPages - 1))
    if (target > 0) {
      const wrap = pageWrapRefs.current[target]
      if (wrap) { wrap.scrollIntoView(); currentPageRef.current = target }
    }
    didInitialScroll.current = true
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [numPages, estPageHeight])

  useImperativeHandle(ref, () => ({
    scrollToPage: (page: number) => {
      const clamped = Math.max(0, Math.min(numPages - 1, page))
      pageWrapRefs.current[clamped]?.scrollIntoView({ behavior: 'smooth' })
    },
  }), [numPages])

  const isDark = theme === 'dark'
  // Make the white page legible against the chosen background.
  /* eslint-disable lingui/no-unlocalized-strings -- CSS filters */
  const canvasFilter = theme === 'dark'
    ? 'invert(1) hue-rotate(180deg)'
    : theme === 'sepia'
      ? 'sepia(0.45) brightness(0.97)'
      : 'none'
  /* eslint-enable lingui/no-unlocalized-strings */

  return (
    <div
      ref={containerRef}
      className="flex-1 overflow-y-auto overflow-x-hidden"
      style={{ background: themeColors.bg }}
    >
      {!base && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <Loader2 className="w-6 h-6 animate-spin" style={{ color: themeColors.text, opacity: 0.5 }} />
        </div>
      )}
      <div className="flex flex-col items-center gap-4 py-4">
        {Array.from({ length: numPages }, (_, i) => (
          <div
            key={i}
            data-page={i}
            ref={(el) => { pageWrapRefs.current[i] = el }}
            style={{
              width: displayW || '80%',
              height: heightFor(i) || 600,
              background: isDark ? '#111' : '#fff',
              boxShadow: '0 1px 6px rgba(0,0,0,0.25)',
            }}
          >
            <canvas
              ref={(el) => { canvasRefs.current[i] = el }}
              style={{ display: 'block', width: '100%', height: '100%', filter: canvasFilter }}
            />
          </div>
        ))}
      </div>
    </div>
  )
})

// ── Main ReaderPage component ─────────────────────────────────────────────────

export default function ReaderPage() {
  const { bookId } = useParams<{ bookId: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  // Preview (?preview=1): open the book without writing anything back (#289).
  const preview = searchParams.get('preview') === '1'

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
  // "Time left in chapter": chapter fraction boundaries × book word count ×
  // the user's true WPM (measured; 250 default until they have history).
  const pacingRef = useRef<{
    word_count: number | null
    wpm: number | null
    chapters: { title: string; start_fraction: number; end_fraction: number }[]
  } | null>(null)
  const lastFractionRef = useRef(0)
  const [chapterMinsLeft, setChapterMinsLeft] = useState<number | null>(null)
  const computeChapterLeft = useCallback((f: number) => {
    lastFractionRef.current = f
    const pacing = pacingRef.current
    if (!pacing?.word_count || pacing.chapters.length === 0) return
    const ch =
      pacing.chapters.find(c => f >= c.start_fraction && f < c.end_fraction) ??
      (f >= (pacing.chapters[pacing.chapters.length - 1]?.start_fraction ?? 1)
        ? pacing.chapters[pacing.chapters.length - 1]
        : null)
    if (ch) {
      const wordsLeft = pacing.word_count * Math.max(0, ch.end_fraction - f)
      setChapterMinsLeft(wordsLeft / (pacing.wpm || 250))
    } else {
      setChapterMinsLeft(null)
    }
  }, [])
  const [toc, setToc] = useState<TocItem[]>([])
  const [showToc, setShowToc] = useState(false)
  const [currentTocHref, setCurrentTocHref] = useState<string | null>(null)
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
  const [isRTL, setIsRTL] = useState(() => localStorage.getItem('reader_comic_rtl') === '1')
  const [tapZones, setTapZones] = useState<TapZoneMode>(loadTapZoneMode)
  const [fitMode, setFitMode] = useState<FitMode>(
    () => (localStorage.getItem('reader_comic_fit') as FitMode) ?? 'width'
  )
  const [comicMode, setComicMode] = useState<ComicMode>(
    () => (localStorage.getItem('reader_comic_mode') as ComicMode) ?? 'page'
  )
  const [spread, setSpread] = useState(() => {
    const stored = localStorage.getItem('reader_comic_spread')
    if (stored === '0' || stored === '1') return stored === '1'
    return typeof window !== 'undefined' && window.innerWidth >= 768
  })
  // A phone shows one page at a time: two half-width pages are unreadable
  // there, so the spread setting only applies from the sm breakpoint up.
  // eslint-disable-next-line lingui/no-unlocalized-strings -- media query
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 639px)') // eslint-disable-line lingui/no-unlocalized-strings -- media query
    const on = () => setNarrow(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  const effectiveSpread = spread && !narrow
  const [showToolbar, setShowToolbar] = useState(true)
  const [showThumbnails, setShowThumbnails] = useState(false)
  const toolbarTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const thumbnailActiveRef = useRef<HTMLButtonElement | null>(null)

  // ── PDF-only state ───────────────────────────────────────────────────────────
  const [isPdf, setIsPdf] = useState(false)
  const [pdfTotalPages, setPdfTotalPages] = useState(0)
  const [pdfCurrentPage, setPdfCurrentPage] = useState(0)
  const [pdfInitialPage, setPdfInitialPage] = useState(-1)
  const [pdfInitialFraction, setPdfInitialFraction] = useState(0)
  const [pdfFitMode, setPdfFitMode] = useState<FitMode>(
    () => (localStorage.getItem('reader_pdf_fit') as FitMode) ?? 'width'
  )
  const [pdfZoom, setPdfZoom] = useState<number>(
    () => Number(localStorage.getItem('reader_pdf_zoom') ?? 1) || 1
  )
  const pdfRef = useRef<PdfReaderHandle | null>(null)

  // ── EPUB refs ────────────────────────────────────────────────────────────────
  const containerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<FoliateViewElement | null>(null)
  // KOReader highlights painted into the EPUB view; tapping one opens the card.
  const painterRef = useRef<AnnotationPainter | null>(null)
  const [activeHighlight, setActiveHighlight] = useState<ReaderAnnotation | null>(null)
  // Text selection inside the EPUB → colour toolbar → POST → painted highlight.
  // The range is snapshotted eagerly: clicking the toolbar (outside the iframe)
  // can collapse the live selection before we read it.
  const selectionRef = useRef<{ doc: Document; index: number; range: Range; text: string } | null>(null)
  const selDocsRef = useRef<WeakSet<Document>>(new WeakSet())
  const [selText, setSelText] = useState<string | null>(null)
  const [noteDraft, setNoteDraft] = useState<string | null>(null)  // null = not editing
  const [savingAnnotation, setSavingAnnotation] = useState(false)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const initialCfi = useRef<string | null>(null)
  const readyToSave = useRef(false)

  // Every write the reader makes is this one call. In preview it is skipped,
  // which leaves status, progress, the synced device position, the derived
  // web reading session and everything downstream exactly as they were.
  const saveStatus = useCallback((body: { status: string; progress_pct?: number | null; cfi?: string | null }) => {
    if (!bookId || preview) return
    api.put(`/books/${bookId}/status`, body).catch(() => {})
  }, [bookId, preview])
  // Holds the latest applyStyles so the foliate 'load' listener (registered
  // once at init) always re-applies the CURRENT theme/font on each new
  // chapter, instead of the stale values captured when the book opened.
  const applyStylesRef = useRef<() => void>(() => {})

  // Persist comic reader preferences so they carry across chapters.
  useEffect(() => { localStorage.setItem('reader_comic_rtl', isRTL ? '1' : '0') }, [isRTL])
  useEffect(() => { localStorage.setItem('reader_tap_zones', tapZones) }, [tapZones])
  useEffect(() => { localStorage.setItem('reader_comic_fit', fitMode) }, [fitMode])
  useEffect(() => { localStorage.setItem('reader_comic_mode', comicMode) }, [comicMode])
  useEffect(() => { localStorage.setItem('reader_comic_spread', spread ? '1' : '0') }, [spread])

  // Persist PDF reader preferences.
  useEffect(() => { localStorage.setItem('reader_pdf_fit', pdfFitMode) }, [pdfFitMode])
  useEffect(() => { localStorage.setItem('reader_pdf_zoom', String(pdfZoom)) }, [pdfZoom])

  // ── Save progress (debounced) ────────────────────────────────────────────────
  //
  // Pages are 0-indexed (0..total-1); progress is 1-based so the last page = 100%.
  // When on the last page, persist status='read' directly instead of relying on
  // a second call - otherwise the debounced save can overwrite the completion.

  const saveProgress = useCallback((page: number, total: number) => {
    if (!bookId || total <= 0) return
    if (saveTimer.current) clearTimeout(saveTimer.current)
    const isLastPage = page >= total - 1
    const fraction = isLastPage ? 1 : (page + 1) / total
    saveTimer.current = setTimeout(() => {
      saveStatus({
        status: isLastPage ? 'read' : 'reading',
        progress_pct: fraction,
        cfi: `comic:${page}`,
      })
    }, 1500)
  }, [bookId, saveStatus])

  const handleComicReadComplete = useCallback(() => {
    if (!bookId || comicTotalPages <= 0) return
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveStatus({
      status: 'read',
      progress_pct: 1,
      cfi: `comic:${comicTotalPages - 1}`,
    })
  }, [bookId, comicTotalPages, saveStatus])

  // 1-based progress so the last page (index total-1) reads as 100%.
  const comicPctFor = (page: number, total: number) =>
    total > 0 ? Math.min(100, Math.round(((page + 1) / total) * 100)) : 0

  // Track progress whenever comicCurrentPage changes
  useEffect(() => {
    if (!isComic || comicTotalPages === 0) return
    setProgress(comicPctFor(comicCurrentPage, comicTotalPages))
    saveProgress(comicCurrentPage, comicTotalPages)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [comicCurrentPage, comicTotalPages, isComic])

  const handleComicProgress = useCallback((page: number, total: number) => {
    setComicCurrentPage(page)
    setProgress(comicPctFor(page, total))
  }, [])

  // ── PDF progress (position stored as `pdf:{page}`) ────────────────────────────

  const savePdfProgress = useCallback((page: number, total: number) => {
    if (!bookId || total <= 0) return
    if (saveTimer.current) clearTimeout(saveTimer.current)
    const isLast = page >= total - 1
    const fraction = isLast ? 1 : (page + 1) / total
    saveTimer.current = setTimeout(() => {
      saveStatus({
        status: isLast ? 'read' : 'reading',
        progress_pct: fraction,
        cfi: `pdf:${page}`,
      })
    }, 1500)
  }, [bookId, saveStatus])

  const handlePdfProgress = useCallback((page: number, total: number) => {
    setPdfCurrentPage(page)
    setProgress(comicPctFor(page, total))
    savePdfProgress(page, total)
  }, [savePdfProgress])

  const handlePdfReadComplete = useCallback(() => {
    if (!bookId || pdfTotalPages <= 0) return
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveStatus({
      status: 'read',
      progress_pct: 1,
      cfi: `pdf:${pdfTotalPages - 1}`,
    })
  }, [bookId, pdfTotalPages, saveStatus])

  // ── Toolbar auto-hide for comic mode ────────────────────────────────────────

  const resetToolbarTimer = useCallback(() => {
    setShowToolbar(true)
    if (toolbarTimer.current) clearTimeout(toolbarTimer.current)
    toolbarTimer.current = setTimeout(() => setShowToolbar(false), 3000)
  }, [])

  // Centre tap / click: show the chrome, or hide it when it is showing.
  const toggleToolbar = useCallback(() => {
    if (showToolbar) {
      if (toolbarTimer.current) clearTimeout(toolbarTimer.current)
      setShowToolbar(false)
    } else {
      resetToolbarTimer()
    }
  }, [showToolbar, resetToolbarTimer])

  // A real mouse movement shows the chrome; touches do not (a tap would show
  // it and the centre-tap toggle would hide it again in the same gesture).
  const lastPointer = useRef<{ x: number; y: number } | null>(null)
  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (e.pointerType !== 'mouse') return
    const last = lastPointer.current
    lastPointer.current = { x: e.clientX, y: e.clientY }
    if (last && Math.abs(last.x - e.clientX) + Math.abs(last.y - e.clientY) < 4) return
    resetToolbarTimer()
  }, [resetToolbarTimer])

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
        setLoadError(t`Book not found.`)
        setLoading(false)
        return
      }

      const hasComic = bookData.files?.some((f: BookFile) => f.format === 'cbz' || f.format === 'cbr')
      const hasEpub = bookData.files?.some((f: BookFile) => f.format === 'epub')
      const hasPdf = bookData.files?.some((f: BookFile) => f.format === 'pdf')

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
          saveStatus({ status: 'reading' })
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
          setProgress(comicPctFor(savedPage, pagesData.total))
        } catch (e: unknown) {
          { const msg2 = (e as Error).message; setLoadError(t`Failed to load comic pages: ${msg2}`) }
          setLoading(false)
          return
        }

        setLoading(false)
        return
      }

      // PDF path: prefer EPUB if both exist (richer reflow), else render the PDF.
      if (hasPdf && !hasEpub) {
        setIsPdf(true)
        try {
          const s = await api.get<{ status: string; progress_pct: number | null; cfi: string | null }>(`/books/${bookId}/status`)
          if (s.cfi?.startsWith('pdf:')) {
            setPdfInitialPage(parseInt(s.cfi.replace('pdf:', ''), 10) || 0)
          } else if (s.progress_pct && s.progress_pct > 0) {
            setPdfInitialFraction(s.progress_pct)
          }
        } catch { /* no saved position */ }
        saveStatus({ status: 'reading' })
        // PdfReader loads the document itself; clear the page-level spinner.
        setLoading(false)
        return
      }

      if (!hasEpub) {
        setLoadError(t`No readable file found for this book.`)
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

      saveStatus({ status: 'reading' })

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
        { const msg2 = (e as Error).message; setLoadError(t`Failed to load EPUB: ${msg2}`) }
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

      // KOReader highlights, re-anchored by text and painted via the overlayer.
      // The fetch runs in parallel with the EPUB download; painter.start() gates
      // section scanning until both the set and the TOC map are ready.
      const painter = new AnnotationPainter(
        view as unknown as ConstructorParameters<typeof AnnotationPainter>[0],
        a => setActiveHighlight(a),
      )
      painterRef.current = painter
      const annotationsPromise = api
        .get<ReaderAnnotation[]>(`/books/${bookId}/annotations`)
        .catch(() => [] as ReaderAnnotation[])
      // Pacing data for "time left in chapter" - non-blocking; the footer
      // simply stays quiet for books without a chapter map or word count.
      // Recompute on arrival: the initial relocate usually fires first.
      api
        .get<NonNullable<typeof pacingRef.current>>(`/books/${bookId}/reader-pacing`)
        .then(p => {
          pacingRef.current = p
          computeChapterLeft(lastFractionRef.current)
        })
        .catch(() => {})

      view.addEventListener('load', (e: Event) => {
        applyStylesRef.current()
        if (view.book?.toc) {
          setToc(flattenToc(view.book.toc))
        }
        const detail = (e as CustomEvent).detail as { doc?: Document; index?: number } | undefined
        if (detail?.doc && detail.index != null) {
          painter.onSectionLoad(detail.doc, detail.index).catch(() => {})
          // Track text selection per section document (attach once per doc).
          const doc = detail.doc
          const index = detail.index
          if (!selDocsRef.current.has(doc)) {
            selDocsRef.current.add(doc)
            // Keys typed while the book text has focus never reach window;
            // forward Escape so it can close the panels.
            doc.addEventListener('keydown', ev => {
              if (ev.key === 'Escape') window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
            })
            doc.addEventListener('selectionchange', () => {
              const sel = doc.getSelection()
              const text = sel && !sel.isCollapsed ? sel.toString() : ''
              if (sel && text.trim().length > 1) {
                selectionRef.current = { doc, index, range: sel.getRangeAt(0).cloneRange(), text }
                setSelText(text)
              } else {
                setSelText(prev => (prev != null ? null : prev))
              }
            })
          }
        }
      })

      view.addEventListener('relocate', (e: Event) => {
        const detail = (e as CustomEvent).detail as {
          fraction?: number
          cfi?: string
          tocItem?: { label?: string; href?: string }
        }
        const pct = Math.round((detail.fraction ?? 0) * 100)
        setProgress(pct)
        if (detail.tocItem?.label) setChapterLabel(detail.tocItem.label.trim())
        if (detail.tocItem?.href) setCurrentTocHref(detail.tocItem.href)

        // Chapter time-left from fraction boundaries - no pagination involved.
        computeChapterLeft(detail.fraction ?? 0)

        if (!readyToSave.current) return

        const fraction = detail.fraction ?? 0
        if (detail.cfi) saveCfi(detail.cfi, fraction)
        if (pct >= 95) {
          saveStatus({ status: 'read', progress_pct: 1, cfi: detail.cfi ?? null })
        }
      })

      try {
        await view.open(epubFile)
      } catch (e: unknown) {
        { const msg2 = (e as Error).message; setLoadError(t`EPUB failed to open: ${msg2}`) }
        setLoading(false)
        return
      }

      if (cancelled) return

      annotationsPromise.then(list => {
        if (!cancelled) painter.start(list).catch(() => {})
      })

      try {
        const cfi = initialCfi.current
        if (cfi && cfi.startsWith('epubcfi(')) {
          await view.goTo(cfi)
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
    applyStylesRef.current = applyStyles
    if (!isComic) applyStyles()
  }, [applyStyles, isComic])

  // ── Save EPUB position (debounced) ────────────────────────────────────────

  const saveCfi = useCallback((cfi: string, fraction: number) => {
    if (!bookId) return
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      saveStatus({
        status: 'reading',
        progress_pct: fraction,
        cfi,
      })
    }, 1500)
  }, [bookId, saveStatus])

  // ── EPUB navigation ────────────────────────────────────────────────────────

  function epubPrev() { viewRef.current?.prev() }
  function epubNext() { viewRef.current?.next() }

  // ── Web-created highlights (create / note / delete) ────────────────────────

  const createHighlight = useCallback(async (color: string) => {
    const s = selectionRef.current
    const view = viewRef.current
    if (!s || !view || savingAnnotation) return
    setSavingAnnotation(true)
    try {
      const cfi = view.getCFI(s.index, s.range)
      const created = await api.post<ReaderAnnotation>('/annotations', {
        book_id: Number(bookId),
        highlighted_text: s.text,
        cfi,
        color,
        chapter: chapterLabel || null,
        datetime: koNow(),
      })
      painterRef.current?.addLocal(created)
      s.doc.getSelection()?.removeAllRanges()
      selectionRef.current = null
      setSelText(null)
      setNoteDraft(null)
      setActiveHighlight(created)
    } catch { /* keep the selection so the user can retry */ }
    finally { setSavingAnnotation(false) }
  }, [bookId, chapterLabel, savingAnnotation])

  const saveNote = useCallback(async () => {
    if (!activeHighlight || noteDraft == null) return
    try {
      const updated = await api.put<ReaderAnnotation>(
        `/annotations/${activeHighlight.id}`, { note: noteDraft.trim() || null })
      const merged = { ...activeHighlight, ...updated }
      painterRef.current?.updateLocal(merged)
      setActiveHighlight(merged)
      setNoteDraft(null)
    } catch { /* stay in edit mode */ }
  }, [activeHighlight, noteDraft])

  const deleteHighlight = useCallback(async () => {
    if (!activeHighlight) return
    try {
      await api.delete(`/annotations/${activeHighlight.id}`)
      painterRef.current?.removeById(activeHighlight.id)
      setActiveHighlight(null)
      setNoteDraft(null)
    } catch { /* leave the card open */ }
  }, [activeHighlight])

  useEffect(() => {
    if (isComic || isPdf) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') epubPrev()
      else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') epubNext()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isComic, isPdf])

  // ── PDF keyboard navigation ───────────────────────────────────────────────
  useEffect(() => {
    if (!isPdf) return
    function onKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === 'PageDown') {
        e.preventDefault()
        pdfRef.current?.scrollToPage(pdfCurrentPage + 1)
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp' || e.key === 'PageUp') {
        e.preventDefault()
        pdfRef.current?.scrollToPage(pdfCurrentPage - 1)
      } else if (e.key === '+' || e.key === '=') {
        setPdfZoom((z) => Math.min(3, +(z + 0.15).toFixed(2)))
      } else if (e.key === '-') {
        setPdfZoom((z) => Math.max(0.5, +(z - 0.15).toFixed(2)))
      } else if (e.key === 'w' || e.key === 'W') {
        setPdfFitMode((m) => (m === 'width' ? 'height' : 'width'))
      } else if (e.key === 'Escape') {
        navigate(-1)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isPdf, pdfCurrentPage, navigate])

  // ── Escape closes the settings panel, the contents drawer and the highlight
  // card first; only with none of them open do the format handlers see it.
  useEffect(() => {
    if (!showSettings && !showToc && !activeHighlight) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopImmediatePropagation()
      setShowSettings(false)
      setShowToc(false)
      setActiveHighlight(null)
      setNoteDraft(null)
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [showSettings, showToc, activeHighlight])

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
      const kStep = effectiveSpread ? 2 : 1
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
  }, [isComic, isRTL, comicTotalPages, effectiveSpread, resetToolbarTimer])

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

  function flattenToc(items: TocItem[], depth = 0): TocItem[] {
    const result: TocItem[] = []
    for (const item of items) {
      result.push({ ...item, depth })
      if (item.subitems?.length) result.push(...flattenToc(item.subitems, depth + 1))
    }
    return result
  }

  // ── Render ────────────────────────────────────────────────────────────────

  const themeColors = THEMES[theme]
  const rootStyle = readerVars(theme)
  const pctCaption = (page: number, total: number) => (total > 0 ? `${page + 1} / ${total}` : '')

  if (loadError) {
    return (
      <div className="flex flex-col items-center justify-center h-screen gap-4 text-muted-foreground">
        <BookOpen className="w-12 h-12 opacity-30" />
        <p className="text-[14px]">{loadError}</p>
        <Pill variant="secondary" onClick={() => navigate(-1)}><Trans>Go back</Trans></Pill>
      </div>
    )
  }

  // ── Comic reader layout ────────────────────────────────────────────────────

  if (isComic) {
    return (
      <div
        className="fixed inset-0 z-50 flex flex-col overflow-hidden text-foreground"
        style={rootStyle}
        onPointerMove={handlePointerMove}
        onTouchStart={comicMode === 'webtoon' ? resetToolbarTimer : undefined}
      >
        {/* Top toolbar (auto-hide) */}
        <ReaderTopBar
          title={book?.title ?? ''}
          preview={preview}
          onBack={() => navigate(-1)}
          className={cn('transition-transform duration-200', showToolbar ? 'translate-y-0' : '-translate-y-full')}
        >
          {/* The quick toggles; on a phone they live in the settings panel only. */}
          <ChromeIcon
            active={isRTL}
            onClick={() => setIsRTL((v) => !v)}
            title={t`Toggle RTL (manga) mode`}
            className="hidden sm:inline-flex text-[12px] font-bold"
          >
            RTL
          </ChromeIcon>
          <ChromeIcon
            onClick={() => setFitMode((m) => m === 'width' ? 'height' : 'width')}
            title={fitMode === 'width' ? t`Switch to fit height` : t`Switch to fit width`}
            className="hidden sm:inline-flex"
          >
            {fitMode === 'width' ? <RotateCcw /> : <AlignJustify />}
          </ChromeIcon>
          {comicMode === 'page' && (
            <ChromeIcon
              active={spread}
              onClick={() => setSpread(prev => !prev)}
              title={spread ? t`Single page (S)` : t`Two-page spread (S)`}
              className="hidden sm:inline-flex"
            >
              {spread ? <Square /> : <Columns2 />}
            </ChromeIcon>
          )}
          {comicMode === 'page' && (
            <ChromeIcon active={showThumbnails} onClick={() => setShowThumbnails(prev => !prev)} title={t`Page thumbnails (T)`}>
              <GalleryHorizontalEnd />
            </ChromeIcon>
          )}
          <ChromeIcon
            onClick={() => setComicMode((m) => m === 'page' ? 'webtoon' : 'page')}
            title={comicMode === 'page' ? t`Switch to webtoon (scroll) mode` : t`Switch to page mode`}
            className="hidden sm:inline-flex"
          >
            {comicMode === 'page' ? <Rows4 /> : <BookOpen />}
          </ChromeIcon>
          <ChromeIcon active={showSettings} onClick={() => setShowSettings((o) => !o)} title={t`Reader settings`}>
            <Settings />
          </ChromeIcon>
        </ReaderTopBar>

        {/* Main area */}
        <div className="flex flex-1 min-h-0 overflow-hidden relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center z-10">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {!loading && comicMode === 'page' && (
            <StreamingComicReader
              bookId={bookId!}
              totalPages={comicTotalPages}
              currentPage={comicCurrentPage}
              isRTL={isRTL}
              fitMode={fitMode}
              spread={effectiveSpread}
              theme={theme}
              tapZones={tapZones}
              onPageChange={setComicCurrentPage}
              onReadComplete={handleComicReadComplete}
              onCenterTap={toggleToolbar}
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

          {showSettings && (
            <ReaderPanel side="right" title={<Trans>Settings</Trans>} onClose={() => setShowSettings(false)}>
              <div className="px-5 pb-6 pt-1 flex flex-col gap-6">
                <ChoiceSetting
                  label={<Trans>Mode</Trans>}
                  value={comicMode}
                  onChange={setComicMode}
                  options={[{ value: 'page', label: t`Page` }, { value: 'webtoon', label: t`Webtoon` }]}
                />
                <ChoiceSetting
                  label={<Trans>Reading direction</Trans>}
                  value={isRTL ? 'rtl' : 'ltr'}
                  onChange={(v) => setIsRTL(v === 'rtl')}
                  options={[{ value: 'ltr', label: t`Left to right` }, { value: 'rtl', label: t`Right to left` }]}
                />
                <ChoiceSetting
                  label={<Trans>Fit</Trans>}
                  value={fitMode}
                  onChange={setFitMode}
                  options={[{ value: 'width', label: t`Width` }, { value: 'height', label: t`Height` }]}
                />
                {comicMode === 'page' && !narrow && (
                  <ChoiceSetting
                    label={<Trans>Pages</Trans>}
                    value={spread ? 'spread' : 'single'}
                    onChange={(v) => setSpread(v === 'spread')}
                    options={[{ value: 'single', label: t`Single` }, { value: 'spread', label: t`Two-page spread` }]}
                  />
                )}
                <ThemeSetting value={theme} onChange={selectTheme} />
                <TapZoneSetting value={tapZones} onChange={setTapZones} />
              </div>
            </ReaderPanel>
          )}
        </div>

        {/* Thumbnail strip */}
        {showThumbnails && comicMode === 'page' && comicTotalPages > 0 && (
          <div className="flex items-center px-2 h-11 shrink-0 overflow-x-auto z-10 scrollbar-none bg-background/85 backdrop-blur">
            <div className="flex gap-1 mx-auto">
              {Array.from({ length: comicTotalPages }, (_, i) => (
                <button
                  key={i}
                  ref={i === comicCurrentPage ? thumbnailActiveRef : undefined}
                  onClick={() => setComicCurrentPage(i)}
                  className={cn(
                    'shrink-0 min-w-8 h-8 px-1.5 rounded-full text-[12px] font-medium tabular-nums transition-colors',
                    i === comicCurrentPage
                      ? 'bg-foreground text-background'
                      : 'bg-secondary text-muted-foreground hover:bg-surface-hover hover:text-foreground'
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
          <ReaderBottomBar
            className={cn('transition-transform duration-200', showToolbar ? 'translate-y-0' : 'translate-y-full')}
            onPrev={() => setComicCurrentPage((p) => Math.max(0, p - 1))}
            onNext={() => setComicCurrentPage((p) => Math.min(comicTotalPages - 1, p + 1))}
            caption={pctCaption(comicCurrentPage, comicTotalPages)}
            progress={progress}
          />
        )}
      </div>
    )
  }

  // ── PDF reader layout ──────────────────────────────────────────────────────

  if (isPdf) {
    const zoomOut = () => setPdfZoom((z) => Math.max(0.5, +(z - 0.15).toFixed(2)))
    const zoomIn = () => setPdfZoom((z) => Math.min(3, +(z + 0.15).toFixed(2)))
    return (
      <div className="fixed inset-0 z-50 flex flex-col overflow-hidden text-foreground" style={rootStyle}>
        <ReaderTopBar title={book?.title ?? ''} preview={preview} onBack={() => navigate(-1)}>
          <ChromeIcon
            onClick={() => setPdfFitMode((m) => (m === 'width' ? 'height' : 'width'))}
            title={pdfFitMode === 'width' ? t`Fit page height (W)` : t`Fit page width (W)`}
          >
            {pdfFitMode === 'width' ? <StretchVertical /> : <StretchHorizontal />}
          </ChromeIcon>
          <ChromeIcon onClick={zoomOut} title={t`Zoom out (-)`}><Minus /></ChromeIcon>
          <span className="w-10 text-center text-[13px] tabular-nums text-muted-foreground">{Math.round(pdfZoom * 100)}%</span>
          <ChromeIcon onClick={zoomIn} title={t`Zoom in (+)`}><Plus /></ChromeIcon>
          <ChromeIcon active={showSettings} onClick={() => setShowSettings((o) => !o)} title={t`Reader settings`}>
            <Settings />
          </ChromeIcon>
        </ReaderTopBar>

        <div className="flex flex-1 min-h-0 overflow-hidden relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center z-10">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {!loading && (
            <PdfReader
              ref={pdfRef}
              bookId={bookId!}
              initialPage={pdfInitialPage}
              initialFraction={pdfInitialFraction}
              theme={theme}
              fitMode={pdfFitMode}
              zoom={pdfZoom}
              onDocLoaded={setPdfTotalPages}
              onError={(m) => setLoadError(m)}
              onProgress={handlePdfProgress}
              onReadComplete={handlePdfReadComplete}
            />
          )}

          {showSettings && (
            <ReaderPanel side="right" title={<Trans>Settings</Trans>} onClose={() => setShowSettings(false)}>
              <div className="px-5 pb-6 pt-1 flex flex-col gap-6">
                <ThemeSetting value={theme} onChange={selectTheme} />
              </div>
            </ReaderPanel>
          )}
        </div>

        <ReaderBottomBar
          onPrev={() => pdfRef.current?.scrollToPage(pdfCurrentPage - 1)}
          onNext={() => pdfRef.current?.scrollToPage(pdfCurrentPage + 1)}
          caption={pctCaption(pdfCurrentPage, pdfTotalPages)}
          progress={progress}
        />
      </div>
    )
  }

  // ── EPUB reader layout ─────────────────────────────────────────────────────

  return (
    <div className="fixed inset-0 z-50 flex flex-col overflow-hidden select-none text-foreground" style={rootStyle}>
      <ReaderTopBar title={book?.title ?? ''} preview={preview} onBack={() => navigate(-1)}>
        <ChromeIcon
          active={showToc}
          onClick={() => { setShowToc((o) => !o); setShowSettings(false) }}
          title={t`Table of contents`}
        >
          <AlignJustify />
        </ChromeIcon>
        <ChromeIcon
          active={showSettings}
          onClick={() => { setShowSettings((o) => !o); setShowToc(false) }}
          title={t`Reader settings`}
        >
          <Settings />
        </ChromeIcon>
      </ReaderTopBar>

      {/* Main area */}
      <div className="flex flex-1 min-h-0 overflow-hidden relative">

        {/* Contents drawer */}
        {showToc && (
          <ReaderPanel side="left" title={<Trans>Contents</Trans>} onClose={() => setShowToc(false)} className="md:relative md:inset-auto">
            {toc.length === 0 && (
              <p className="px-5 py-4 text-[14px] text-muted-foreground"><Trans>No table of contents available.</Trans></p>
            )}
            <div className="pb-4">
              {toc.map((item, i) => {
                const current = item.href === currentTocHref
                return (
                  <button
                    key={i}
                    onClick={() => goToHref(item.href)}
                    aria-current={current ? 'location' : undefined}
                    className={cn(
                      'block w-full text-left pr-5 py-2 text-[15px] leading-snug transition-colors',
                      current ? 'text-primary font-medium' : 'text-foreground/80 hover:text-foreground',
                    )}
                    style={{ paddingLeft: 20 + (item.depth ?? 0) * 16 }}
                  >
                    {item.label?.trim()}
                  </button>
                )
              })}
            </div>
          </ReaderPanel>
        )}

        {/* foliate-view container */}
        <div className="flex-1 min-w-0 overflow-hidden relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center z-10">
              <div className="w-6 h-6 border-2 border-current border-t-transparent rounded-full animate-spin opacity-40" style={{ color: themeColors.text }} />
            </div>
          )}
          <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />

          {/* Click zones for prev/next (respect the tap-zone setting) */}
          {tapZones !== 'off' && (
            <>
              <div className="absolute inset-y-0 left-0 w-1/5 cursor-pointer z-10" onClick={tapZones === 'swapped' ? epubNext : epubPrev} />
              <div className="absolute inset-y-0 right-0 w-1/5 cursor-pointer z-10" onClick={tapZones === 'swapped' ? epubPrev : epubNext} />
            </>
          )}

          {/* Selection toolbar: pick a colour to create a highlight */}
          {selText && !activeHighlight && (
            <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-30 flex items-center gap-2.5 rounded-xl bg-card shadow-lg ring-1 ring-panel-ring px-4 py-2.5">
              <span className="text-[13px] font-medium mr-1 text-muted-foreground"><Trans>Highlight</Trans></span>
              {HIGHLIGHT_COLORS.map(c => (
                <button
                  key={c}
                  onClick={() => createHighlight(c)}
                  disabled={savingAnnotation}
                  aria-label={t`Highlight in ${c}`}
                  className="w-6 h-6 rounded-full transition-transform hover:scale-110 disabled:opacity-50"
                  style={{
                    background: fillForColor(c).replace(/[\d.]+\)$/, '0.9)'),
                    border: '1px solid rgba(128,128,128,0.35)',
                  }}
                />
              ))}
            </div>
          )}

          {/* Tapped-highlight card: synced and web-created highlights */}
          {activeHighlight && (
            <>
              <div className="absolute inset-0 z-20" onClick={() => { setActiveHighlight(null); setNoteDraft(null) }} />
              <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-30 w-[min(34rem,calc(100%-2rem))] rounded-xl bg-card shadow-lg ring-1 ring-panel-ring p-4">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div className="flex items-center gap-2 min-w-0 text-[13px] text-muted-foreground">
                    <span
                      className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ background: fillForColor(activeHighlight.color).replace(/[\d.]+\)$/, '1)') }}
                    />
                    {activeHighlight.chapter && <span className="truncate">{activeHighlight.chapter}</span>}
                    {activeHighlight.datetime && (
                      <span className="shrink-0">· {activeHighlight.datetime.slice(0, 10)}</span>
                    )}
                  </div>
                  <ChromeIcon className="-mr-2 -mt-2" onClick={() => { setActiveHighlight(null); setNoteDraft(null) }} title={t`Close`}>
                    <X />
                  </ChromeIcon>
                </div>
                {activeHighlight.highlighted_text && (
                  <p
                    className="text-[14px] leading-relaxed max-h-40 overflow-y-auto border-l-2 pl-2.5"
                    style={{ borderColor: fillForColor(activeHighlight.color).replace(/[\d.]+\)$/, '0.9)') }}
                  >
                    {activeHighlight.highlighted_text}
                  </p>
                )}
                {noteDraft != null ? (
                  <div className="mt-3">
                    <Textarea
                      value={noteDraft}
                      onChange={e => setNoteDraft(e.target.value)}
                      rows={3}
                      autoFocus
                      placeholder={t`Add a note…`}
                    />
                    <div className="mt-2 flex justify-end gap-1.5">
                      <Pill variant="quiet" onClick={() => setNoteDraft(null)}><Trans>Cancel</Trans></Pill>
                      <Pill variant="primary" onClick={saveNote}><Trans>Save</Trans></Pill>
                    </div>
                  </div>
                ) : activeHighlight.note ? (
                  <p className="mt-2.5 flex items-start gap-1.5 text-[14px] text-foreground/80">
                    <StickyNote className="w-3.5 h-3.5 mt-1 shrink-0" />
                    <span className="leading-relaxed">{activeHighlight.note}</span>
                  </p>
                ) : null}
                <div className="mt-2.5 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <p className="text-[13px] text-muted-foreground">
                    {isWebAnnotation(activeHighlight)
                      ? t`Made on the web, syncs to KOReader`
                      : t`Synced from KOReader`}
                  </p>
                  {noteDraft == null && (
                    <div className="flex items-center gap-0.5 -mr-2">
                      <Pill variant="quiet" onClick={() => setNoteDraft(activeHighlight.note ?? '')}>
                        <StickyNote className="w-3.5 h-3.5" />
                        {activeHighlight.note ? t`Edit note` : t`Add note`}
                      </Pill>
                      <Pill variant="destructive" onClick={deleteHighlight}>
                        <Trash2 className="w-3.5 h-3.5" />
                        <Trans>Delete</Trans>
                      </Pill>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        {/* Settings panel */}
        {showSettings && (
          <ReaderPanel side="right" title={<Trans>Settings</Trans>} onClose={() => setShowSettings(false)} className="md:relative md:inset-auto">
            <div className="px-5 pb-6 pt-1 flex flex-col gap-6">
              <FontSizeSetting value={fontSize} onChange={changeFontSize} />
              <ChoiceSetting
                label={<Trans>Font</Trans>}
                value={fontFamily}
                onChange={selectFontFamily}
                options={(Object.keys(FONT_FAMILIES) as FontFamily[]).map(f => ({
                  value: f,
                  label: <span style={{ fontFamily: FONT_FAMILIES[f].css }}>{i18n._(FONT_FAMILIES[f].label)}</span>,
                }))}
              />
              <ThemeSetting value={theme} onChange={selectTheme} />
              <TapZoneSetting value={tapZones} onChange={setTapZones} />
            </div>
          </ReaderPanel>
        )}
      </div>

      <ReaderBottomBar
        onPrev={epubPrev}
        onNext={epubNext}
        progress={progress}
        caption={
          <>
            {chapterLabel}
            {chapterMinsLeft != null && chapterMinsLeft >= 0.5 && (
              <span title={pacingRef.current?.wpm ? t`Based on your measured reading pace` : t`Based on a default pace (no reading history yet)`}>
                {chapterLabel ? ' · ' : ''}{(() => { const m = Math.max(1, Math.round(chapterMinsLeft)); return t`~${m} min left` })()}
              </span>
            )}
          </>
        }
      />
    </div>
  )
}
