import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import { StarRating } from '@/components/StarRating'
import { t } from '@lingui/core/macro'

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
      toast.error(t`Failed to save series rating`)
    }
  }

  const hint = rating != null
    ? t`Applied to volumes you haven’t rated`
    : data.volume_average != null
      ? (() => { const avg = data.volume_average, rated = data.rated_volumes; return t`Your volumes average ${avg} (${rated} rated)` })()
      : t`Rate the whole series`

  return (
    <div className="mt-2 flex items-center gap-2.5">
      {/* Show the explicit rating if set, else the volume average. A derived
          fill renders muted ("computed, tap to make it yours") — and because
          `selected` is only the explicit rating, clicking a derived value SETS
          it rather than clearing nothing. */}
      <StarRating
        value={rating ?? data.display}
        selected={rating}
        onChange={save}
        derived={rating == null && data.display != null}
      />
      <span className="text-xs text-muted-foreground">{hint}</span>
    </div>
  )
}
