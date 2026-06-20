import { useEffect, useState } from 'react'
import { Star } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import { cn } from '@/lib/utils'

interface SeriesRatingData {
  series_name: string
  rating: number | null          // your explicit series rating
  volume_average: number | null  // avg of your volume ratings
  rated_volumes: number
  display: number | null
}

/**
 * Per-user series rating. The interactive stars are *your explicit series
 * rating*, which is inherited by every volume you haven't rated individually.
 * When you haven't set one, we surface the average of your volume ratings as
 * context. Renders nothing for the "No Series" bucket.
 */
export function SeriesRating({ seriesName, isUnserialized }: { seriesName: string; isUnserialized?: boolean }) {
  const { toast } = useToast()
  const [data, setData] = useState<SeriesRatingData | null>(null)
  const [hover, setHover] = useState<number | null>(null)

  useEffect(() => {
    if (isUnserialized || !seriesName) return
    let cancelled = false
    api.get<SeriesRatingData>(`/series/${encodeURIComponent(seriesName)}/rating`)
      .then(d => { if (!cancelled) setData(d) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [seriesName, isUnserialized])

  if (isUnserialized || !data) return null

  const rating = data.rating
  async function save(next: number | null) {
    const prev = data
    setData(d => d ? { ...d, rating: next } : d)   // optimistic
    try {
      const updated = await api.put<SeriesRatingData>(`/series/${encodeURIComponent(seriesName)}/rating`, { rating: next })
      setData(updated)
    } catch {
      setData(prev)
      toast.error('Failed to save series rating')
    }
  }

  const hint = rating != null
    ? 'Applied to volumes you haven’t rated'
    : data.volume_average != null
      ? `Your volumes average ${data.volume_average} (${data.rated_volumes} rated)`
      : 'Rate the whole series'

  return (
    <div className="mt-2 flex items-center gap-2.5">
      <div className="flex items-center gap-0.5" onMouseLeave={() => setHover(null)}>
        {[1, 2, 3, 4, 5].map(n => {
          // Show the explicit rating if set, else the (rounded) volume average.
          // When the fill is derived from the average — not your own series
          // rating — render it muted so it reads "computed, tap to make it yours".
          const shown = hover ?? rating ?? data!.display ?? 0
          const active = shown >= n
          const derived = hover == null && rating == null && data!.display != null
          return (
            <button
              key={n}
              type="button"
              aria-label={`${n} star${n > 1 ? 's' : ''}`}
              onMouseEnter={() => setHover(n)}
              onClick={() => save(rating === n ? null : n)}
              className="p-0.5 transition-transform hover:scale-110"
            >
              <Star className={cn(
                'w-5 h-5 transition-colors',
                active ? cn('fill-rating text-rating', derived && 'opacity-40') : 'text-muted-foreground/40',
              )} />
            </button>
          )
        })}
      </div>
      <span className="text-xs text-muted-foreground">{hint}</span>
    </div>
  )
}
