import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Trans, useLingui } from '@lingui/react/macro'
import { ArrowUpRight, Play, X } from 'lucide-react'
import { Stat } from '@/components/page'
import { api } from '@/lib/api'
import { CoverImage } from '@/components/CoverImage'
import { Pill } from '@/components/page'
import { coverSrc } from '@/lib/covers'
import type { BookDetail, BookStatus } from '@/lib/books'

interface OwnStats { total_seconds: number; sessions: number; last_read: string | null }

function fmtTime(sec: number): { value: string; unit?: string } {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60)
  return h > 0 ? { value: `${h}h`, unit: `${m}m` } : { value: `${Math.max(1, m)}`, unit: 'min' }
}
function dayLabel(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** Descriptions arrive as HTML from some sources; the quick look shows plain text. */
function plainText(html: string): string {
  const el = document.createElement('div')
  el.innerHTML = html
  return (el.textContent ?? '').replace(/\s+/g, ' ').trim()
}

/**
 * A quick look at a book from a cover in a grid or row: cover, title, author,
 * series, a few lines of description, progress, Resume and Open page. Centred
 * over the content panel (the sidebar is 256px on desktop). Esc or the
 * backdrop closes it. The full page stays the place for everything else.
 */
export function QuickLook({ bookId, onClose }: { bookId: number; onClose: () => void }) {
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const pct = status?.progress_pct ?? 0
  const text = book?.description ? plainText(book.description) : null

  return (
    <div className="fixed inset-0 md:left-64 z-50 grid place-items-center p-4" onClick={onClose}>
      <div className="absolute inset-0 bg-black/65 backdrop-blur-[3px] animate-[quicklook-veil_180ms_ease-out]" />
      <div
        role="dialog"
        aria-modal
        aria-label={book?.title ?? t`Quick look`}
        onClick={e => e.stopPropagation()}
        className="relative w-full max-w-[860px] rounded-[var(--radius-panel)] bg-card shadow-2xl shadow-black/40 ring-1 ring-panel-ring p-5 sm:p-7 flex flex-col sm:flex-row gap-5 sm:gap-8 animate-[quicklook-in_220ms_cubic-bezier(0.2,0.8,0.2,1)]"
      >
        <div className="shrink-0 w-[160px] sm:w-[220px] mx-auto sm:mx-0 rounded-[12px] overflow-hidden bg-muted" style={{ boxShadow: 'var(--cover-bloom-fan)' }}>
          <div className="relative aspect-[2/3]"><CoverImage src={coverSrc(bookId, { w: 400 })} alt="" iconClassName="w-8 h-8" /></div>
        </div>
        <div className="min-w-0 flex-1 flex flex-col">
          {book?.series && book.series_index != null && (
            <div className="text-[12px] font-bold uppercase tracking-[0.1em] text-primary">{book.series} · {book.series_index}</div>
          )}
          <h2 className="font-display text-[26px] sm:text-[30px] leading-[1.1] font-bold text-foreground mt-1.5 pr-8">{book?.title ?? ''}</h2>
          {book?.author && <div className="mt-1 text-[15px] text-muted-foreground">{book.author}</div>}
          {text && <p className="mt-3 text-[15px] leading-relaxed text-foreground/80 line-clamp-3">{text}</p>}
          {pct > 0 && (
            <div className="mt-4 flex items-center gap-3">
              <div className="h-[5px] flex-1 rounded-full overflow-hidden" style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--card))' }}>
                <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, pct)}%` }} />
              </div>
              <span className="text-[15px] font-bold tabular-nums text-foreground">{Math.round(pct)}%</span>
            </div>
          )}
          {own && own.sessions > 0 && (
            <div className="mt-5 grid grid-cols-3 gap-4 pt-4 border-t border-border/70">
              <Stat size={30} label={t`Time reading`} value={fmtTime(own.total_seconds).value} unit={fmtTime(own.total_seconds).unit} />
              <Stat size={30} label={t`Sessions`} value={own.sessions} />
              <Stat size={30} label={t`Last read`} value={own.last_read ? dayLabel(own.last_read) : '–'} />
            </div>
          )}
          <div className="mt-auto pt-5 flex items-center gap-2">
            <Pill variant="primary" onClick={() => navigate(`/reader/${bookId}`)}>
              <Play className="w-4 h-4 fill-current" />
              {pct > 0 ? <Trans>Resume</Trans> : <Trans>Read</Trans>}
            </Pill>
            <Pill onClick={() => { onClose(); navigate(`/books/${bookId}`) }}>
              <Trans>Open page</Trans>
              <ArrowUpRight className="w-4 h-4" />
            </Pill>
          </div>
        </div>
        <button type="button" onClick={onClose} aria-label={t`Close`} className="absolute top-3 right-3 w-9 h-9 grid place-items-center rounded-full text-muted-foreground hover:text-foreground hover:bg-secondary">
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
