// Backlog tile (#187): how long a pile of unstarted books would take at the
// user's pace, by book type. Scope (Want to Read / all unread / a library / a
// shelf) is tile config, picked in the edit-mode popover.
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { describePace, formatEstimateDays, formatEstimateHours, type BacklogSummary } from '@/lib/backlog'
import { ProgressRow } from './ProgressRow'

export const DEFAULT_BACKLOG_SCOPE = 'want'

export function BacklogEstimate({ scope = DEFAULT_BACKLOG_SCOPE }: { scope?: string }) {
  const [data, setData] = useState<BacklogSummary | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let alive = true
    setData(null)
    setError(false)
    api.get<BacklogSummary>(`/stats/backlog?scope=${encodeURIComponent(scope)}&tz_offset=${new Date().getTimezoneOffset()}`)
      .then(d => { if (alive) setData(d) })
      .catch(() => { if (alive) setError(true) })
    return () => { alive = false }
  }, [scope])

  if (error) return <p className="text-sm text-muted-foreground text-center py-4">Could not load this scope.</p>
  if (!data) return <p className="text-sm text-muted-foreground text-center py-4">Loading…</p>
  if (data.books === 0) return <p className="text-sm text-muted-foreground text-center py-4">No unstarted books in this scope.</p>

  const { pace } = data
  const usesTypeAvg = data.by_type.some(t => t.type_avg > 0)
  const paceNote = pace.minutes_per_day
    ? `you read ~${Math.round(pace.minutes_per_day)} min a day (last ${pace.window_days} days)`
    : 'no recent reading, so no day estimate'

  if (data.estimated === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4">
        {data.books} {data.books === 1 ? 'book' : 'books'}, none estimated yet. Finish a couple of books of each type so Tome can measure your pace.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline gap-2 flex-wrap" title={describePace(pace, usesTypeAvg ? 'type_avg' : (pace.wpm ? 'words' : 'default'))}>
        <span className="text-3xl font-semibold tabular-nums text-foreground leading-none">{formatEstimateHours(data.seconds)}</span>
        {data.days != null && (
          <span className="text-sm text-muted-foreground">
            about <span className="text-foreground font-medium tabular-nums">{formatEstimateDays(data.days).replace(/^~/, '')}</span> at your pace
          </span>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {data.books} {data.books === 1 ? 'book' : 'books'} &middot; {paceNote}
        {usesTypeAvg && <> &middot; books without a word count use your average for that type</>}
        {data.unestimated > 0 && <> &middot; {data.unestimated} not estimated yet</>}
      </p>
      <div className="flex flex-col gap-3">
        {data.by_type.map(t => (
          <ProgressRow
            key={t.label}
            label={t.label}
            value={t.seconds > 0
              ? `${formatEstimateHours(t.seconds)}${t.days != null ? ` · ${formatEstimateDays(t.days)}` : ''}`
              : 'not estimated yet'}
            pct={data.seconds > 0 ? (t.seconds / data.seconds) * 100 : 0}
            sub={`${t.books} ${t.books === 1 ? 'book' : 'books'}${t.unestimated > 0 && t.seconds > 0 ? ` · ${t.unestimated} not estimated` : ''}`}
          />
        ))}
      </div>
    </div>
  )
}
