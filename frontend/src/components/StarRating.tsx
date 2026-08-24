import { useState } from 'react'
import { Star } from 'lucide-react'
import { cn } from '@/lib/utils'
import { t } from '@lingui/core/macro'

interface StarRatingProps {
  /** The fill to display (may be a derived value, e.g. a volume average). */
  value: number | null
  /**
   * The user's own explicit value, used for click-to-clear toggling. Defaults
   * to `value`; pass it separately when `value` can be derived so clicking a
   * derived fill SETS it instead of clearing nothing.
   */
  selected?: number | null
  onChange?: (v: number | null) => void
  /** Render the fill muted — it's computed, not the user's own rating. */
  derived?: boolean
  /** Display-only: no hover, no clicks, renders spans instead of buttons. */
  readOnly?: boolean
  /** Size/color classes for each star (default 'w-5 h-5'). */
  starClassName?: string
  className?: string
}

/**
 * Interactive 5-star rating in half-star steps: the left half of each star
 * selects n−0.5, the right half n. Clicking the currently-selected value
 * clears it. Half fills render via a clipped overlay star.
 */
export function StarRating({
  value, selected, onChange, derived = false, readOnly = false,
  starClassName = 'w-5 h-5', className,
}: StarRatingProps) {
  const [hover, setHover] = useState<number | null>(null)
  const toggleBase = selected === undefined ? value : selected

  function candidate(n: number, e: React.MouseEvent<HTMLButtonElement>): number {
    const rect = e.currentTarget.getBoundingClientRect()
    return e.clientX - rect.left < rect.width / 2 ? n - 0.5 : n
  }

  const shown = (readOnly ? null : hover) ?? value ?? 0

  const star = (n: number) => {
    const fill = Math.min(1, Math.max(0, shown - (n - 1))) // 0 | 0.5 | 1
    return (
      <>
        <Star className={cn(starClassName, 'text-muted-foreground/40 transition-colors')} />
        {fill > 0 && (
          <span
            className={cn('pointer-events-none absolute inset-0', !readOnly && 'p-0.5')}
            style={fill >= 1 ? undefined : { clipPath: 'inset(0 50% 0 0)' }}
          >
            <Star className={cn(
              starClassName, 'fill-rating text-rating transition-colors',
              derived && hover == null && 'opacity-40',
            )} />
          </span>
        )}
      </>
    )
  }

  if (readOnly) {
    return (
      <span
        className={cn('inline-flex items-center gap-px', className)}
        aria-label={value != null ? t`Rated ${value} of 5` : undefined}
      >
        {[1, 2, 3, 4, 5].map(n => (
          <span key={n} className="relative inline-flex">{star(n)}</span>
        ))}
      </span>
    )
  }

  return (
    <div className={cn('flex items-center gap-0.5', className)} onMouseLeave={() => setHover(null)}>
      {[1, 2, 3, 4, 5].map(n => (
        <button
          key={n}
          type="button"
          aria-label={`${n} star${n > 1 ? 's' : ''}`}
          onMouseMove={e => setHover(candidate(n, e))}
          onClick={e => {
            const v = candidate(n, e)
            onChange?.(toggleBase === v ? null : v)
          }}
          className="relative p-0.5 transition-transform hover:scale-110"
        >
          {star(n)}
        </button>
      ))}
    </div>
  )
}
