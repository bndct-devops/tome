import { useEffect, useRef, useState } from 'react'
import { BookOpen } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import type { Book, ReadingStatus } from '@/lib/books'
import { useBookTypes } from '@/lib/bookTypes'
import { cn } from '@/lib/utils'

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
  index?: number
}

export function BookCard({
  book, view, selected, focused, onSelect,
  onTagClick: _onTagClick, onSeriesClick, onAuthorClick,
  readingStatus, progressPct, index = 0,
}: BookCardProps) {
  const navigate = useNavigate()
  const bookTypes = useBookTypes()
  const cardRef = useRef<HTMLDivElement | HTMLAnchorElement>(null)

  useEffect(() => {
    if (focused && cardRef.current) {
      cardRef.current.scrollIntoView({ block: 'nearest' })
    }
  }, [focused])
  const bookType = book.book_type_id != null ? bookTypes.find(t => t.id === book.book_type_id) : null
  const typeBadgeClass = bookType
    ? (COLOR_CLASSES[bookType.color ?? ''] ?? 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300')
    : null

  function stop(e: React.MouseEvent, cb?: () => void) {
    e.preventDefault()
    e.stopPropagation()
    cb?.()
  }

  const coverUrl = book.cover_path ? `/api/books/${book.id}/cover` : null
  const hasReadableFile = book.files?.some(f => ['epub', 'cbz', 'cbr', 'pdf'].includes(f.format))

  // Progress bar width for the cover bottom strip (progressPct is 0-1 from the API)
  const barWidth =
    readingStatus === 'read' ? 100
    : readingStatus === 'reading' ? Math.round((progressPct ?? 0.15) * 100)
    : 0
  const barColor =
    readingStatus === 'read' ? 'bg-primary'
    : 'bg-primary/60'

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
        <div className="w-9 h-12 rounded overflow-hidden bg-muted shrink-0">
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
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate group-hover:text-primary transition-colors">
            {book.title}
          </p>
          <div className="flex items-center gap-1.5 flex-wrap">
            {book.author && (
              <button
                className="text-xs text-muted-foreground hover:text-primary transition-colors truncate py-1.5 -my-1.5"
                onClick={e => stop(e, () => onAuthorClick?.(book.author!))}
              >
                {book.author}
              </button>
            )}
            {bookType && typeBadgeClass && (
              <span className={cn('text-xs px-1.5 py-0.5 rounded-full font-medium shrink-0', typeBadgeClass)}>
                {bookType.label}
              </span>
            )}
          </div>
        </div>
        {/* Series stays in list view — earns its space there */}
        <div className="hidden sm:block w-40 shrink-0">
          {book.series && (
            <button
              className="text-xs text-primary/70 hover:text-primary transition-colors truncate w-full text-left py-1.5 -my-1.5"
              onClick={e => stop(e, () => onSeriesClick?.(book.series!))}
            >
              {book.series}{book.series_index != null ? ` #${book.series_index}` : ''}
            </button>
          )}
        </div>
        <div className="hidden md:block w-12 shrink-0 text-right">
          {book.year && <span className="text-xs text-muted-foreground">{book.year}</span>}
        </div>
        {book.files.length > 0 && (
          <span className="shrink-0 text-[10px] font-medium uppercase px-1.5 py-0.5 rounded bg-muted border border-border text-muted-foreground">
            {book.files[0].format}
          </span>
        )}
      </>
    )

    const cls = cn(
      'group flex items-center gap-3 px-3 py-2.5 rounded-lg border bg-card transition-all duration-150',
      selected ? 'border-primary bg-primary/5' : 'border-border hover:bg-accent hover:border-primary/20',
      focused && 'ring-2 ring-blue-500 ring-offset-1 ring-offset-background',
    )

    if (onSelect) return <div ref={cardRef as React.RefObject<HTMLDivElement>} className={cls} onClick={onSelect}>{inner}</div>
    return <Link ref={cardRef as React.RefObject<HTMLAnchorElement>} to={`/books/${book.id}`} className={cls}>{inner}</Link>
  }

  // ── Cover grid (large / small) ────────────────────────────────────────────
  const coverDivRef = useRef<HTMLDivElement>(null)
  const [isHovering, setIsHovering] = useState(false)
  const [mousePos, setMousePos] = useState({ x: 50, y: 50 })

  const tiltEnabled = !onSelect

  function handleMouseMove(e: React.MouseEvent<HTMLDivElement>) {
    if (!tiltEnabled) return
    const rect = e.currentTarget.getBoundingClientRect()
    const x = (e.clientX - rect.left) / rect.width  // 0 to 1
    const y = (e.clientY - rect.top) / rect.height   // 0 to 1
    const xRatio = x * 2 - 1  // -1 to 1
    const yRatio = y * 2 - 1  // -1 to 1
    setMousePos({ x: x * 100, y: y * 100 })
    if (coverDivRef.current) {
      coverDivRef.current.style.transform = `rotateY(${xRatio * 8}deg) rotateX(${yRatio * -8}deg) translateY(-4px)`
    }
  }

  function handleMouseLeave() {
    if (!tiltEnabled) return
    setIsHovering(false)
    setMousePos({ x: 50, y: 50 })
    if (coverDivRef.current) {
      coverDivRef.current.style.transform = 'rotateY(0deg) rotateX(0deg) translateY(0px)'
    }
  }

  function handleMouseEnter() {
    if (!tiltEnabled) return
    setIsHovering(true)
  }

  return (
    <div
      ref={cardRef as React.RefObject<HTMLDivElement>}
      className="group flex flex-col cursor-pointer animate-card-appear"
      style={{ animationDelay: `${Math.min(index * 30, 400)}ms`, perspective: '600px' }}
      onClick={onSelect ? onSelect : () => navigate(`/books/${book.id}`)}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onMouseEnter={handleMouseEnter}
    >
      {/* Cover */}
      <div
        ref={coverDivRef}
        className={cn(
          'aspect-[2/3] bg-muted relative overflow-hidden rounded-lg shadow-sm group-hover:shadow-lg',
          selected && 'ring-2 ring-primary ring-offset-2 ring-offset-background',
          focused && 'ring-2 ring-blue-500 ring-offset-2 ring-offset-background',
        )}
        style={{ transition: isHovering ? 'transform 0.05s ease-out, box-shadow 0.2s ease-out' : 'transform 0.3s ease-out, box-shadow 0.2s ease-out' }}
      >
        {coverUrl ? (
          <>
            <img
              src={coverUrl}
              alt={book.title}
              className="w-full h-full object-cover"
              loading="lazy"
              onError={e => { (e.target as HTMLImageElement).style.display = 'none'; (e.target as HTMLImageElement).parentElement!.querySelector('.cover-fallback')?.classList.remove('hidden') }}
            />
            <div className="cover-fallback hidden w-full h-full flex items-center justify-center absolute inset-0">
              <BookOpen className={cn('text-muted-foreground/30', view === 'large' ? 'w-12 h-12' : 'w-8 h-8')} />
            </div>
          </>
        ) : (
          <div
            className="w-full h-full flex items-center justify-center relative"
            style={{
              background: 'linear-gradient(90deg, transparent 25%, rgba(255,255,255,0.05) 50%, transparent 75%)',
              backgroundSize: '200% 100%',
              animation: 'shimmer 1.5s infinite',
            }}
          >
            <BookOpen className={cn('text-muted-foreground/30 relative z-10', view === 'large' ? 'w-12 h-12' : 'w-8 h-8')} />
          </div>
        )}

        {/* Shine/glare overlay — follows mouse */}
        {tiltEnabled && (
          <div
            className="absolute inset-0 z-20 pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity duration-300"
            style={{ background: `radial-gradient(circle at ${mousePos.x}% ${mousePos.y}%, rgba(255,255,255,0.12) 0%, transparent 60%)` }}
          />
        )}

        {/* Format badge — top right */}
        {book.files.length > 0 && (
          <span className="absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase bg-background/70 backdrop-blur-sm text-foreground/90 border border-border/50">
            {book.files[0].format}
          </span>
        )}

        {/* Selection checkbox — top left, circular */}
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

        {/* Quick-read button — bottom right, hover only */}
        {hasReadableFile && (
          <button
            className={cn(
              'absolute bottom-1.5 right-1.5 w-7 h-7 rounded-md',
              'flex items-center justify-center',
              'bg-background/40 backdrop-blur-sm hover:bg-background/60 transition-all',
              'opacity-0 group-hover:opacity-100',
            )}
            title="Read"
            aria-label="Read book"
            onClick={e => stop(e, () => navigate(`/reader/${book.id}`))}
          >
            <BookOpen className="w-3.5 h-3.5 text-foreground/90" />
          </button>
        )}

        {/* Reading progress strip — bottom edge */}
        {barWidth > 0 && (
          <div className="absolute inset-x-0 bottom-0 h-1.5 bg-background/40">
            <div
              className={cn('h-full transition-all duration-500', barColor)}
              style={{ width: `${barWidth}%` }}
            />
          </div>
        )}
      </div>

      {/* Info — bare below the cover, no border/card wrapper */}
      <div className={cn('flex flex-col gap-0.5', view === 'large' ? 'pt-2 px-0.5' : 'pt-2 px-0.5')}>
        <p className={cn(
          'font-medium text-foreground line-clamp-2 leading-snug transition-colors duration-150 group-hover:text-primary',
          view === 'large' ? 'text-[13px]' : 'text-xs'
        )}>
          {book.title}
        </p>
        {book.author && (
          <button
            className={cn(
              'text-muted-foreground line-clamp-1 text-left hover:text-primary transition-colors',
              view === 'large' ? 'text-xs' : 'text-[10px]'
            )}
            onClick={e => stop(e, () => onAuthorClick?.(book.author!))}
          >
            {book.author}
          </button>
        )}
        {bookType && typeBadgeClass && (
          <span className={cn('text-xs px-1.5 py-0.5 rounded-full font-medium self-start', typeBadgeClass)}>
            {bookType.label}
          </span>
        )}
      </div>
    </div>
  )
}
