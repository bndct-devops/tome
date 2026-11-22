import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { m, useIsPresent } from 'motion/react'
import { useEscape, useRegisterOverlay, useShellInset } from '@/components/ModalShell'
import { useNavigate } from 'react-router-dom'
import { Trans, useLingui } from '@lingui/react/macro'
import { ArrowUpRight, Play, X } from 'lucide-react'
import { Stat } from '@/components/page'
import { api } from '@/lib/api'
import { CoverImage } from '@/components/CoverImage'
import { Pill } from '@/components/page'
import { coverSrc } from '@/lib/covers'
import { openBookWithCover, previewState } from '@/lib/coverTransition'
import { cn, progressPercent } from '@/lib/utils'
import type { BookDetail, BookStatus } from '@/lib/books'

interface OwnStats { total_seconds: number; sessions: number; last_read: string | null }

function fmtTime(sec: number): { value: string; unit?: string } {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60)
  return h > 0 ? { value: `${h}h`, unit: `${m}m` } : { value: `${Math.max(1, m)}`, unit: 'min' }
}
function dayLabel(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** Descriptions arrive as HTML from some sources; the quick look shows plain
 *  text but keeps the paragraph breaks (block ends and <br> become newlines,
 *  the rest of the whitespace folds to single spaces). */
function plainText(html: string): string {
  const el = document.createElement('div')
  el.innerHTML = html
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|li|h[1-6]|blockquote)>/gi, '$&\n\n')
  return (el.textContent ?? '')
    .split(/\n\s*\n/)
    .map(par => par.replace(/[^\S\n]+/g, ' ').replace(/ *\n */g, '\n').trim())
    .filter(Boolean)
    .join('\n\n')
}

/**
 * A quick look at a book from a cover in a grid or row: cover, title, author,
 * series, a few lines of description, progress, Resume and Open page. The
 * veil covers the whole viewport, the panel centres over the content panel.
 * Esc or the backdrop closes it. The full page stays the place for everything else.
 *
 * With `origin` (the box of the cover it was opened on) the panel grows out of
 * that cover and, when the caller wraps it in AnimatePresence, shrinks back
 * into it on close. Without it the panel rises in the centre.
 */
const EASE = [0.2, 0.8, 0.2, 1] as const

export function QuickLook({ bookId, onClose, origin, cover }: {
  bookId: number
  onClose: () => void
  /** The box of the cover it was opened on. */
  origin?: DOMRect
  /** The cover url the caller showed, so the same (cached) image appears. */
  cover?: string | null
}) {
  const navigate = useNavigate()
  const { t } = useLingui()
  // Both answers are kept with the id they belong to, so a switch to another
  // book shows nothing of the previous one without a reset in the effect.
  const [loaded, setLoaded] = useState<{ id: number; book: BookDetail } | null>(null)
  const [loadedStatus, setLoadedStatus] = useState<{ id: number; status: BookStatus } | null>(null)
  const [loadedStats, setLoadedStats] = useState<{ id: number; own: OwnStats } | null>(null)
  const book = loaded?.id === bookId ? loaded.book : null
  const status = loadedStatus?.id === bookId ? loadedStatus.status : null
  const own = loadedStats?.id === bookId ? loadedStats.own : null

  useEffect(() => {
    const ctrl = new AbortController()
    api.get<BookDetail>(`/books/${bookId}`, ctrl.signal).then(b => setLoaded({ id: bookId, book: b })).catch(() => {})
    api.get<BookStatus>(`/books/${bookId}/status`, ctrl.signal).then(st => setLoadedStatus({ id: bookId, status: st })).catch(() => {})
    api.get<{ own: OwnStats }>(`/books/${bookId}/reading-stats?tz_offset=${new Date().getTimezoneOffset()}`, ctrl.signal)
      .then(r => setLoadedStats({ id: bookId, own: r.own })).catch(() => {})
    return () => ctrl.abort()
  }, [bookId])

  const inset = useShellInset()
  const present = useIsPresent()
  useRegisterOverlay(present)
  useEscape(present, onClose)

  // The transform that puts the panel's cover exactly on the origin cover.
  // Measured on the first frame (once the panel has its place beside the
  // sidebar) for the way in, and again on close, since the panel has grown
  // with its content by then.
  const panelRef = useRef<HTMLDivElement>(null)
  const coverRef = useRef<HTMLDivElement>(null)
  const onOrigin = (): { x: number; y: number; scale: number } | null => {
    const p = panelRef.current?.getBoundingClientRect()
    const c = coverRef.current?.getBoundingClientRect()
    if (!origin || !p || !c || !c.width) return null
    const scale = origin.width / c.width
    const pcx = p.left + p.width / 2, pcy = p.top + p.height / 2
    const ccx = c.left + c.width / 2, ccy = c.top + c.height / 2
    return {
      x: origin.left + origin.width / 2 - pcx - scale * (ccx - pcx),
      y: origin.top + origin.height / 2 - pcy - scale * (ccy - pcy),
      scale,
    }
  }
  const [from, setFrom] = useState<{ x: number; y: number; scale: number } | null>(null)
  useLayoutEffect(() => {
    if (!origin) return
    const raf = requestAnimationFrame(() => setFrom(onOrigin()))
    return () => cancelAnimationFrame(raf)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // On close the panel is at rest (no transform), so this reads its true place.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const back = useMemo(() => (present ? null : onOrigin()), [present])
  const grows = !!origin
  const rest = { x: 0, y: 0, scale: 1, opacity: 1 }
  const panelMotion = !grows
    ? { initial: { opacity: 0, scale: 0.96, y: 8 }, animate: rest, exit: { opacity: 0, scale: 0.96, y: 8 }, transition: { duration: 0.2, ease: EASE } }
    : {
        // First frame: invisible until measured (one frame, before any paint of content).
        initial: { opacity: 0 },
        animate: from ? { x: [from.x, 0], y: [from.y, 0], scale: [from.scale, 1], opacity: 1 } : { opacity: 0 },
        exit: back ? { x: back.x, y: back.y, scale: back.scale } : { opacity: 0 },
        transition: { duration: 0.42, ease: EASE, opacity: { duration: 0 } },
      }
  // The card face (background, text) fades around the cover, which stays solid.
  const face = grows
    ? { initial: { opacity: 0 }, animate: { opacity: from ? 1 : 0 }, exit: { opacity: 0 }, transition: { duration: 0.22, ease: 'easeOut' as const } }
    : {}
  const coverUrl = cover !== undefined ? cover : coverSrc(bookId, { w: 400 })

  const pct = progressPercent(status?.progress_pct)
  const text = book?.description ? plainText(book.description) : null

  return (
    <div className={cn('fixed inset-0 z-50', !present && 'pointer-events-none')} onClick={onClose}>
      <m.div
        className="fixed inset-0 bg-black/65 backdrop-blur-[3px]"
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        transition={{ duration: 0.18, ease: 'easeOut' }}
      />
      <div className="fixed inset-0 grid place-items-center p-4 overflow-y-auto" style={inset ? { left: inset } : undefined}>
      <m.div
        ref={panelRef}
        role="dialog"
        aria-modal
        aria-label={book?.title ?? t`Quick look`}
        onClick={e => e.stopPropagation()}
        {...panelMotion}
        className="relative w-full max-w-[860px]"
      >
        {/* The face is its own layer so it can fade around the cover. */}
        <m.div {...face} aria-hidden className="absolute inset-0 rounded-[var(--radius-panel)] bg-card shadow-2xl shadow-black/40 ring-1 ring-panel-ring" />
        <div className="relative max-h-[calc(100dvh-2rem)] overflow-y-auto p-5 sm:p-7 flex flex-col sm:flex-row gap-5 sm:gap-8">
        <div ref={coverRef} data-cover-id={bookId} className="relative shrink-0 self-center sm:self-start w-[160px] sm:w-[220px] mx-auto sm:mx-0 rounded-[12px] overflow-hidden bg-muted" style={{ boxShadow: 'var(--cover-bloom-fan)' }}>
          <div className="relative aspect-[2/3]"><CoverImage src={coverUrl} alt="" loading="eager" iconClassName="w-8 h-8" /></div>
        </div>
        <m.div {...face} className="relative min-w-0 flex-1 flex flex-col">
          {book?.series && book.series_index != null && (
            <div className="text-[12px] font-bold uppercase tracking-[0.1em] text-primary">{book.series} · {(() => { const v = Number.isInteger(book.series_index) ? String(book.series_index) : book.series_index.toFixed(1); return <Trans>Volume {v}</Trans> })()}</div>
          )}
          <h2 className="font-display text-[26px] sm:text-[30px] leading-[1.1] font-bold text-foreground mt-1.5 pr-8">{book?.title ?? ''}</h2>
          {book?.author && <div className="mt-1 text-[15px] text-muted-foreground">{book.author}</div>}
          {text && <p className="mt-3 text-[15px] leading-relaxed text-foreground/80 line-clamp-4 whitespace-pre-line">{text}</p>}
          {pct > 0 && (
            <div className="mt-4 flex items-center gap-3">
              <div className="h-[5px] flex-1 rounded-full overflow-hidden" style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--card))' }}>
                <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, pct)}%` }} />
              </div>
              <span className="text-[15px] font-bold text-foreground">{Math.round(pct)}%</span>
            </div>
          )}
          {own && own.sessions > 0 && (
            <div className="mt-5 grid grid-cols-3 gap-4 pt-4 border-t border-border/70">
              <Stat size={30} label={t`Time reading`} value={fmtTime(own.total_seconds).value} unit={fmtTime(own.total_seconds).unit} />
              <Stat size={30} label={t`Sessions`} value={own.sessions} />
              <Stat size={30} label={t`Last read`} value={own.last_read ? dayLabel(own.last_read) : '-'} />
            </div>
          )}
          <div className="mt-auto pt-5 flex items-center gap-2">
            {/* A book known from a reader's sync only has nothing to open here. */}
            {book?.status !== 'no_file' && (
              <Pill variant="primary" onClick={() => navigate(`/reader/${bookId}`)}>
                <Play className="w-4 h-4 fill-current" />
                {status?.status === 'read' && pct >= 100 ? <Trans>Read again</Trans> : pct > 0 ? <Trans>Resume</Trans> : <Trans context="action">Read</Trans>}
              </Pill>
            )}
            <Pill onClick={() => {
              const state = book ? previewState({
                id: bookId, title: book.title, author: book.author, series: book.series,
                series_index: book.series_index, cover: coverSrc(bookId, { w: 400, v: book.updated_at }),
              }) : undefined
              openBookWithCover(coverRef.current, bookId, () => { onClose(); navigate(`/books/${bookId}`, { state }) })
            }}>
              <Trans>Open page</Trans>
              <ArrowUpRight className="w-4 h-4" />
            </Pill>
          </div>
        </m.div>
        </div>
        <m.button {...face} type="button" onClick={onClose} aria-label={t`Close`} className="absolute top-3 right-3 w-9 h-9 grid place-items-center rounded-full text-muted-foreground hover:text-foreground hover:bg-secondary">
          <X className="w-4 h-4" />
        </m.button>
      </m.div>
      </div>
    </div>
  )
}
