// Backlog tile (#187): how long a pile of unstarted books would take at the
// user's pace, by book type. Scope (Want to Read / all unread / a library / a
// shelf) is tile config, picked in the edit-mode popover.
import { useEffect, useState } from 'react'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
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

  if (error) return <p className="text-sm text-muted-foreground text-center py-4"><Trans>Could not load this scope.</Trans></p>
  if (!data) return <p className="text-sm text-muted-foreground text-center py-4"><Trans>Loading…</Trans></p>
  if (data.books === 0) return <p className="text-sm text-muted-foreground text-center py-4"><Trans>No unstarted books in this scope.</Trans></p>

  const { pace } = data
  const usesTypeAvg = data.by_type.some(x => x.type_avg > 0)
  const paceNote = pace.minutes_per_day
    ? (() => { const mpd = Math.round(pace.minutes_per_day); const win = pace.window_days; return t`you read ~${mpd} min a day (last ${win} days)` })()
    : t`no recent reading, so no day estimate`

  if (data.estimated === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4">
        {plural(data.books, { one: '# book, none estimated yet.', other: '# books, none estimated yet.' })}{' '}
        <Trans>Finish a couple of books of each type so Tome can measure your pace.</Trans>
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline gap-2 flex-wrap" title={describePace(pace, usesTypeAvg ? 'type_avg' : (pace.wpm ? 'words' : 'default'))}>
        <span className="text-3xl font-semibold tabular-nums text-foreground leading-none">{formatEstimateHours(data.seconds)}</span>
        {data.days != null && (
          <span className="text-sm text-muted-foreground">
            {(() => { const d = formatEstimateDays(data.days).replace(/^~/, ''); return (
              <Trans>about <span className="text-foreground font-medium tabular-nums">{d}</span> at your pace</Trans>
            ) })()}
          </span>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {plural(data.books, { one: '# book', other: '# books' })} &middot; {paceNote}
        {usesTypeAvg && <Trans> &middot; books without a word count use your average for that type</Trans>}
        {data.unestimated > 0 && (() => { const n = data.unestimated; return <Trans> &middot; {n} not estimated yet</Trans> })()}
      </p>
      <div className="flex flex-col gap-3">
        {data.by_type.map(row => (
          <ProgressRow
            key={row.label}
            label={row.label}
            value={row.seconds > 0
              ? `${formatEstimateHours(row.seconds)}${row.days != null ? ` · ${formatEstimateDays(row.days)}` : ''}`
              : t`not estimated yet`}
            pct={data.seconds > 0 ? (row.seconds / data.seconds) * 100 : 0}
            sub={(() => {
              const booksStr = plural(row.books, { one: '# book', other: '# books' })
              const extra = row.unestimated > 0 && row.seconds > 0 ? ' · ' + plural(row.unestimated, { one: '# not estimated', other: '# not estimated' }) : ''
              return booksStr + extra
            })()}
          />
        ))}
      </div>
    </div>
  )
}
