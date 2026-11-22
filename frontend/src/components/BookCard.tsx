import { useEffect, useRef } from 'react'
import { m } from 'motion/react'
import { BookOpen, Eye, Star } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { Trans, useLingui } from '@lingui/react/macro'
import type { Book, ReadingStatus } from '@/lib/books'
import { useBookTypes } from '@/lib/bookTypes'
import { cn, progressPercent } from '@/lib/utils'
import { CoverImage } from '@/components/CoverImage'
import { DevicePills } from '@/components/DevicePills'
import { coverSrc } from '@/lib/covers'
import { coverRing, useTilt } from '@/lib/tilt'
import { isPlainClick, openBookWithCover, previewState } from '@/lib/coverTransition'

/* eslint-disable lingui/no-unlocalized-strings -- Tailwind class map, not copy */
const COLOR_CLASSES: Record<string, string> = {
  blue: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  pink: 'bg-pink-100 text-pink-700 dark:bg-pink-900/40 dark:text-pink-300',
  orange: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
  purple: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  red: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  green: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
  yellow: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300',
  teal: 'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
}
/* eslint-enable lingui/no-unlocalized-strings */

export type ViewMode = 'large' | 'small' | 'list'

interface BookCardProps {
  book: Book
  view: ViewMode
  selected?: boolean
  focused?: boolean
  onSelect?: (e: React.MouseEvent) => void
  onTagClick?: (tag: string) => void
  onSeriesClick?: (series: string) => void
  onAuthorClick?: (author: string) => void
  readingStatus?: ReadingStatus
  progressPct?: number | null
  rating?: number | null
  index?: number
  // Home rows are usually all one format - the badge is noise there, so those
  // callers turn it off. Library grids keep it (that's where formats differ).
  showFormatBadge?: boolean
  /** Opens the quick look for this book, with the cover's box to grow from.
   *  Grid view only: a small pill in the cover's corner on hover or focus. The
   *  card click still opens the page. */
  onQuickLook?: (origin?: DOMRect) => void
  /** The Library grid/list switch: the cover and the title block are the same
   *  elements (keyed) in the grid card and the list row, and travel to their
   *  place in the other layout. */
  travel?: boolean
  /** Changes when the cover and title should measure for that hand-over (the
   *  view switch); between switches they skip measuring. */
  travelEpoch?: number
}

// The hand-over of cover and title between the grid card and the list row.
const TRAVEL = { duration: 0.42, ease: [0.2, 0.8, 0.2, 1] as const }

/** Compact read-only star rating for cards. Renders nothing when unrated. */
function RatingStars({ rating, size = 11 }: { rating?: number | null; size?: number }) {
  const { t } = useLingui()
  if (!rating) return null
  return (
    <span className="inline-flex items-center gap-px" aria-label={t`Rated ${rating} of 5`}>
      {[1, 2, 3, 4, 5].map(n => (
        <Star
          key={n}
          style={{ width: size, height: size }}
          className={n <= rating ? 'fill-rating text-rating' : 'text-muted-foreground/30'}
        />
      ))}
    </span>
  )
}

export function BookCard({
  book, view, selected, focused, onSelect,
  onTagClick: _onTagClick, onSeriesClick, onAuthorClick,
  readingStatus, progressPct, rating, showFormatBadge = true, onQuickLook,
  travel = false, travelEpoch,
}: BookCardProps) {
  const { t } = useLingui()
  const navigate = useNavigate()
  const bookTypes = useBookTypes()
  const cardRef = useRef<HTMLDivElement | HTMLAnchorElement>(null)
  // The visible cover box: the cover transition and the quick look start from it.
  const coverRef = useRef<HTMLDivElement | null>(null)
  const tiltEnabled = !onSelect
  const { wrapperProps: tiltHandlers, tiltProps } = useTilt(tiltEnabled, 4, { lift: 3, scale: 1, glare: true })

  useEffect(() => {
    if (focused && cardRef.current) {
      cardRef.current.scrollIntoView({ block: 'nearest' })
    }
  }, [focused])
  const bookType = book.book_type_id != null ? bookTypes.find(t => t.id === book.book_type_id) : null
  const typeBadgeClass = bookType
    // eslint-disable-next-line lingui/no-unlocalized-strings -- Tailwind classes
    ? (COLOR_CLASSES[bookType.color ?? ''] ?? 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300')
    : null

  function stop(e: React.MouseEvent, cb?: () => void) {
    e.preventDefault()
    e.stopPropagation()
    cb?.()
  }

  const coverUrl = book.cover_path ? coverSrc(book.id, { w: 400, v: book.updated_at }) : null
  const hasReadableFile = book.files?.some(f => ['epub', 'cbz', 'cbr', 'pdf'].includes(f.format))
  // A record made from the user's reader sync: Tome has no file for it, so
  // it takes the format badge's place and offers nothing that needs a file.
  const noFile = book.status === 'no_file'

  // Progress bar width for the cover bottom strip (progressPct is 0-1 from the API)
  // Floor at 4% so a just-started book still shows a visible nub instead of nothing
  const barWidth =
    readingStatus === 'read' ? 100
    : readingStatus === 'reading' ? Math.max(4, Math.round(progressPct != null ? progressPercent(progressPct) : 15))
    : 0
  const barColor =
    readingStatus === 'read' ? 'bg-primary'
    : 'bg-primary/60'

  const travelProps = (part: 'cover' | 'title') => travel
    ? { layout: part === 'title' ? 'position' as const : true, layoutDependency: travelEpoch, transition: TRAVEL, 'data-travel': '' }
    : {}

  // A plain click opens the page with the cover transition and hands the page
  // what the card knows, so its hero paints at once.
  function openPage(e: React.MouseEvent) {
    if (!isPlainClick(e)) return
    e.preventDefault()
    const state = previewState({
      id: book.id, title: book.title, author: book.author, series: book.series,
      series_index: book.series_index, cover: coverUrl, coverColor: book.cover_color,
    })
    openBookWithCover(coverRef.current, book.id, () => navigate(`/books/${book.id}`, { state }))
  }

  // ── List view ────────────────────────────────────────────────────────────
  if (view === 'list') {
    const inner = (
      <>
        {onSelect ? (
          <div className="w-5 shrink-0 flex items-center justify-center">
            <div className={cn(
              'w-4 h-4 rounded-full border-2 flex items-center justify-center transition-all',
              selected
                ? 'border-primary bg-primary'
                : 'border-muted-foreground/40 group-hover:border-primary/60'
            )}>
              {selected && <div className="w-1.5 h-1.5 rounded-full bg-primary-foreground" />}
            </div>
          </div>
        ) : null}
        <m.div key="cover" {...travelProps('cover')} className="w-9 h-12 shrink-0">
        <div ref={coverRef} data-cover-id={book.id} className="w-full h-full rounded overflow-hidden bg-muted" style={coverUrl && book.cover_color ? { backgroundColor: book.cover_color } : undefined}>
          {coverUrl ? (
            <>
              <img
                src={coverUrl}
                alt=""
                className="w-full h-full object-cover"
                loading="lazy"
                onError={e => { (e.target as HTMLImageElement).style.display = 'none'; (e.target as HTMLImageElement).parentElement!.querySelector('.cover-fallback')?.classList.remove('hidden') }}
              />
              <div className="cover-fallback hidden w-full h-full flex items-center justify-center">
                <BookOpen className="w-4 h-4 text-muted-foreground/40" />
              </div>
            </>
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <BookOpen className="w-4 h-4 text-muted-foreground/40" />
            </div>
          )}
        </div>
        </m.div>
        <m.div key="title" {...travelProps('title')} className="flex-1 min-w-0">
          <p className="text-[15px] font-medium text-foreground truncate group-hover:text-primary transition-colors">
            {book.title}
          </p>
          <div className="flex items-center gap-1.5 flex-wrap">
            {book.author && (
              <button
                className="text-[13px] text-muted-foreground hover:text-primary transition-colors truncate py-1.5 -my-1.5"
                onClick={e => stop(e, () => onAuthorClick?.(book.author!))}
              >
                {book.author}
              </button>
            )}
            {bookType && typeBadgeClass && (
              <span className={cn('text-[13px] px-1.5 py-0.5 rounded-full font-medium shrink-0', typeBadgeClass)}>
                {bookType.label}
              </span>
            )}
            <DevicePills bookId={book.id} size="xs" />
          </div>
        </m.div>
        {/* Series stays in list view - earns its space there */}
        <div className="library-extra hidden sm:block w-40 shrink-0">
          {book.series && (
            <button
              className="text-[13px] text-primary/70 hover:text-primary transition-colors truncate w-full text-left py-1.5 -my-1.5"
              onClick={e => stop(e, () => onSeriesClick?.(book.series!))}
            >
              {book.series}{book.series_index != null ? ` #${book.series_index}` : ''}
            </button>
          )}
        </div>
        <div className="library-extra hidden md:block w-12 shrink-0 text-right">
          {book.year && <span className="text-[13px] text-muted-foreground">{book.year}</span>}
        </div>
        {rating ? (
          <div className="library-extra hidden sm:flex shrink-0 justify-end">
            <RatingStars rating={rating} size={12} />
          </div>
        ) : null}
        {noFile ? (
          <span className="library-extra shrink-0 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
            <Trans>No file</Trans>
          </span>
        ) : showFormatBadge && book.files.length > 0 && (
          <span className="library-extra shrink-0 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
            {book.files[0].format}
          </span>
        )}
      </>
    )

    const cls = cn(
      'group flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors duration-150 touch-feedback',
      selected ? 'bg-primary/10' : 'hover:bg-secondary',
      focused && 'ring-2 ring-ring ring-offset-1 ring-offset-background',
      onSelect && 'select-none',
    )

    if (onSelect) return <div ref={cardRef as React.RefObject<HTMLDivElement>} className={cls} onClick={onSelect}>{inner}</div>
    return <Link ref={cardRef as React.RefObject<HTMLAnchorElement>} to={`/books/${book.id}`} onClick={openPage} className={cls}>{inner}</Link>
  }

  // ── Cover grid (large / small) ────────────────────────────────────────────
  const gridCls = cn('group flex flex-col cursor-pointer touch-feedback', onSelect && 'select-none')
  const gridContent = (
    <>
      {/* Cover: the outer box travels on the grid/list switch, the inner one tilts */}
      <m.div key="cover" {...travelProps('cover')} style={{ perspective: '600px' }}>
      <div
        ref={node => { coverRef.current = node; tiltProps.ref(node) }}
        data-cover-id={book.id}
        className="aspect-[2/3] bg-muted relative overflow-hidden rounded-[8px]"
        style={{ boxShadow: coverRing(!!selected, !!focused), transition: 'transform 200ms ease-out' }}
      >
        <CoverImage
          src={coverUrl}
          alt={book.title}
          color={book.cover_color}
          iconClassName={view === 'large' ? 'w-12 h-12' : 'w-8 h-8'}
        />

        {/* Shine/glare overlay - follows mouse */}
        {tiltEnabled && (
          <div
            className="absolute inset-0 z-20 pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity duration-300"
            style={{ background: 'radial-gradient(circle at var(--glare-x, 50%) var(--glare-y, 50%), rgba(255,255,255,0.12) 0%, transparent 60%)' }}
          />
        )}

        {/* Format badge - top right; "No file" in its place for a reader-sync record */}
        {noFile ? (
          <span className="absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase bg-background/70 backdrop-blur-sm text-foreground/90 border border-border/50">
            <Trans>No file</Trans>
          </span>
        ) : showFormatBadge && book.files.length > 0 && (
          <span className="absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase bg-background/70 backdrop-blur-sm text-foreground/90 border border-border/50">
            {book.files[0].format}
          </span>
        )}

        {/* Selection checkbox - top left, circular */}
        {onSelect && (
          <div className={cn(
            'absolute top-1.5 left-1.5 transition-opacity',
            selected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
          )}>
            <div className={cn(
              'w-5 h-5 rounded-full border-2 flex items-center justify-center transition-all',
              selected
                ? 'border-primary bg-primary'
                : 'border-foreground/50 bg-background/40 backdrop-blur-sm'
            )}>
              {selected && <div className="w-1.5 h-1.5 rounded-full bg-primary-foreground" />}
            </div>
          </div>
        )}

        {/* Quick look - top left, on hover or keyboard focus. Not while
            selecting: the selection circle owns that corner then. */}
        {onQuickLook && !onSelect && (
          <button
            type="button"
            className={cn(
              'absolute top-1.5 left-1.5 z-30 w-8 h-8 rounded-full',
              'flex items-center justify-center text-foreground',
              'bg-background/75 backdrop-blur-sm hover:bg-background transition-opacity',
              // Not a hidden tap target on touch: a tap on the corner opens the page.
              'opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto group-focus-within:opacity-100',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            )}
            title={t`Quick look`}
            aria-label={t`Quick look`}
            onClick={e => stop(e, () => onQuickLook(coverRef.current?.getBoundingClientRect()))}
          >
            <Eye className="w-4 h-4" />
          </button>
        )}

        {/* Quick-read button - bottom right, hover only */}
        {hasReadableFile && (
          <button
            className={cn(
              'absolute bottom-2.5 right-1.5 z-30 w-8 h-8 rounded-full',
              'flex items-center justify-center text-foreground',
              'bg-background/75 backdrop-blur-sm hover:bg-background transition-opacity',
              'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            )}
            title={t({ message: 'Read', context: 'action' })}
            aria-label={t`Read book`}
            onClick={e => stop(e, () => navigate(`/reader/${book.id}`))}
          >
            <BookOpen className="w-4 h-4" />
          </button>
        )}

        {/* Reading progress strip - bottom edge */}
        {barWidth > 0 && (
          <div className="absolute inset-x-0 bottom-0 h-1.5 bg-background/40">
            <div
              className={cn('h-full transition-all duration-500', barColor)}
              style={{ width: `${barWidth}%` }}
            />
          </div>
        )}
      </div>
      </m.div>

      {/* Info - bare below the cover, no border/card wrapper */}
      <m.div key="title" {...travelProps('title')} className="flex flex-col gap-0.5 pt-2.5 px-0.5">
        <p className="text-[14px] font-medium text-foreground line-clamp-2 leading-snug transition-colors duration-150 group-hover:text-primary">
          {book.title}
        </p>
        {book.author && (
          <button
            className="text-[13px] text-muted-foreground line-clamp-1 text-left hover:text-primary transition-colors"
            onClick={e => stop(e, () => onAuthorClick?.(book.author!))}
          >
            {book.author}
          </button>
        )}
        {rating ? <RatingStars rating={rating} size={view === 'large' ? 12 : 10} /> : null}
        <DevicePills bookId={book.id} size="xs" className="mt-0.5" />
      </m.div>
    </>
  )

  // Selecting: the card toggles its checkbox. Otherwise it is a real link, so
  // middle-click, open-in-new-tab and Tab focus work like the list view.
  if (onSelect) {
    return (
      <div
        ref={cardRef as React.RefObject<HTMLDivElement>}
        className={gridCls}
        onClick={onSelect}
      >
        {gridContent}
      </div>
    )
  }
  return (
    <Link
      ref={cardRef as React.RefObject<HTMLAnchorElement>}
      to={`/books/${book.id}`}
      className={cn(gridCls, 'rounded-[8px] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background')}
      onClick={openPage}
      onMouseMove={tiltHandlers.onMouseMove}
      onMouseLeave={tiltHandlers.onMouseLeave}
    >
      {gridContent}
    </Link>
  )
}
