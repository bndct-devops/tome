import { useEffect, useRef } from 'react'
import { Layers } from 'lucide-react'
import type { Book } from '@/lib/books'
import type { ViewMode } from '@/components/BookCard'
import { cn } from '@/lib/utils'
import { CoverImage } from '@/components/CoverImage'

interface SeriesStackCardProps {
  book: Book
  count: number
  view: ViewMode
  focused?: boolean
  index?: number
  onOpen: () => void
}

// Stacked card for the "Group by series" library view: the first volume's
// cover sits on top of the next volumes' real covers, which fan out on hover.
// Clicking anywhere opens the series detail view.
export function SeriesStackCard({ book, count, view, focused, index = 0, onOpen }: SeriesStackCardProps) {
  const cardRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (focused && cardRef.current) {
      cardRef.current.scrollIntoView({ block: 'nearest' })
    }
  }, [focused])

  const coverUrl = book.cover_path ? `/api/books/${book.id}/cover` : null
  const seriesName = book.series ?? book.title
  // Real covers of the next volumes, deepest last in the array
  const fanIds = (book.stack_cover_ids ?? []).slice(0, 2)

  // ── List view ────────────────────────────────────────────────────────────
  if (view === 'list') {
    return (
      <div
        ref={cardRef}
        onClick={onOpen}
        className={cn(
          'group flex items-center gap-3 px-3 py-2.5 rounded-lg border bg-card cursor-pointer transition-all duration-150 touch-feedback',
          'border-border hover:bg-accent hover:border-primary/20',
          focused && 'ring-2 ring-blue-500 ring-offset-1 ring-offset-background',
        )}
      >
        <div className="relative w-9 h-12 shrink-0">
          <div className="absolute inset-0 translate-x-1 -translate-y-0.5 rounded overflow-hidden bg-muted border border-border transition-transform duration-200 group-hover:translate-x-2 group-hover:-translate-y-1">
            {fanIds[0] && (
              <CoverImage src={`/api/books/${fanIds[0]}/cover`} alt="" iconClassName="w-3 h-3" />
            )}
          </div>
          <div className="relative w-full h-full rounded overflow-hidden bg-muted border border-border">
            <CoverImage src={coverUrl} alt={seriesName} iconClassName="w-4 h-4" />
          </div>
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate group-hover:text-primary transition-colors">
            {seriesName}
          </p>
          {book.author && (
            <p className="text-xs text-muted-foreground truncate">{book.author}</p>
          )}
        </div>
        <span className="shrink-0 flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded bg-muted border border-border text-muted-foreground">
          <Layers className="w-3 h-3" />
          {count} {count === 1 ? 'volume' : 'volumes'}
        </span>
      </div>
    )
  }

  // ── Cover grid (large / small) ────────────────────────────────────────────
  // The pile sits bottom-left in its cell; the cell reserves top/right padding
  // so the back covers (and the hover fan) stay inside the cell instead of
  // overlapping neighbouring cards. Small view halves the offsets — its grid
  // gap is tighter.
  const lg = view === 'large'
  const deepLayerCls = lg
    ? 'translate-x-2 -translate-y-2 group-hover:translate-x-3 group-hover:-translate-y-3.5 group-hover:rotate-2'
    : 'translate-x-1 -translate-y-1 group-hover:translate-x-1.5 group-hover:-translate-y-2 group-hover:rotate-2'
  const nearLayerCls = lg
    ? 'translate-x-1 -translate-y-1 group-hover:translate-x-1.5 group-hover:-translate-y-2 group-hover:rotate-1'
    : 'translate-x-0.5 -translate-y-0.5 group-hover:translate-x-1 group-hover:-translate-y-1 group-hover:rotate-1'
  const layerBaseCls = 'absolute inset-0 rounded-xl overflow-hidden border border-border bg-muted transition-transform duration-300 ease-out'

  return (
    <div
      ref={cardRef}
      onClick={onOpen}
      className="group flex flex-col cursor-pointer animate-card-appear touch-feedback"
      style={{ animationDelay: `${Math.min(index * 30, 400)}ms` }}
    >
      {/* Reserved bleed area for the stack layers */}
      <div className={cn('relative', lg ? 'pt-2 pr-2' : 'pt-1 pr-1')}>
        <div className="relative">
          {/* Volumes behind the top cover — real covers when available, fanning out on hover */}
          {fanIds.length > 0 ? (
            <>
              {fanIds[1] && (
                <div className={cn(layerBaseCls, deepLayerCls)}>
                  <CoverImage src={`/api/books/${fanIds[1]}/cover`} alt="" imgClassName="brightness-[0.7]" />
                </div>
              )}
              {fanIds[0] && (
                <div className={cn(layerBaseCls, nearLayerCls)}>
                  <CoverImage src={`/api/books/${fanIds[0]}/cover`} alt="" imgClassName="brightness-[0.85]" />
                </div>
              )}
            </>
          ) : (
            /* Fallback when no other volume has a cover: subtle paper layers */
            <>
              <div className={cn(layerBaseCls, deepLayerCls)} />
              <div className={cn(layerBaseCls, nearLayerCls, 'bg-card')} />
            </>
          )}
          <div
            className={cn(
              'relative aspect-[2/3] bg-muted overflow-hidden rounded-xl shadow-sm border border-border',
              'transition-transform duration-300 ease-out group-hover:shadow-lg group-hover:shadow-accent-soft group-hover:-translate-y-0.5',
              focused && 'ring-2 ring-blue-500 ring-offset-2 ring-offset-background',
            )}
          >
            <CoverImage
              src={coverUrl}
              alt={seriesName}
              iconClassName={lg ? 'w-12 h-12' : 'w-8 h-8'}
            />
            {/* Volume count badge — top right */}
            <span className="absolute top-1.5 right-1.5 flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold bg-background/70 backdrop-blur-sm text-foreground/90 border border-border/50">
              <Layers className="w-2.5 h-2.5" />
              {count}
            </span>
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-0.5 pt-2 px-0.5">
        <p
          className={cn(
            'font-medium text-foreground line-clamp-2 leading-snug transition-colors duration-150 group-hover:text-primary',
            view === 'large' ? 'text-[13px]' : 'text-xs',
          )}
        >
          {seriesName}
        </p>
        <p className={cn('text-muted-foreground line-clamp-1', view === 'large' ? 'text-xs' : 'text-[10px]')}>
          {count} {count === 1 ? 'volume' : 'volumes'}
          {book.author ? ` · ${book.author}` : ''}
        </p>
      </div>
    </div>
  )
}
