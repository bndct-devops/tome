import { useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatDuration, formatDate } from '@/lib/utils'

// ── Types ─────────────────────────────────────────────────────────────────────

interface PerVolume {
  book_id: number
  series_index: number | null
  title: string
  seconds: number
  status: string
}

interface LongestVolume {
  book_id: number
  title: string
  series_index: number | null
  seconds: number
}

interface SeriesOwnStats {
  total_seconds: number
  sessions: number
  pages_turned: number
  books_total: number
  books_finished: number
  books_in_progress: number
  books_with_sessions: number
  completion_pct: number
  avg_volume_seconds: number
  estimated_remaining_seconds: number | null
  longest_volume: LongestVolume | null
  first_read: string | null
  last_read: string | null
  per_volume: PerVolume[]
}

interface SeriesAggregateStats {
  total_seconds: number
  total_sessions: number
  distinct_readers: number
}

interface SeriesStatsResponse {
  own: SeriesOwnStats
  aggregate: SeriesAggregateStats | null
}

// ── Per-volume bar chart ──────────────────────────────────────────────────────

function VolumeChart({ volumes }: { volumes: PerVolume[] }) {
  if (volumes.length === 0) return null

  const max = Math.max(...volumes.map(v => v.seconds), 1)
  const withIndex = volumes.filter(v => v.series_index != null)
  const firstIdx = withIndex.length > 0 ? withIndex[0].series_index : null
  const lastIdx = withIndex.length > 0 ? withIndex[withIndex.length - 1].series_index : null

  return (
    <div className="flex flex-col">
      <p className="text-xs text-muted-foreground/70 mb-1.5 shrink-0">Time per volume</p>
      <div className="relative h-24">
        {/* faint baseline rule */}
        <div className="absolute bottom-0 left-0 right-0 h-px bg-border/40" />
        <div className="absolute inset-0 flex items-end gap-1">
          {volumes.map(v => {
            const isRead = v.seconds > 0
            const pct = isRead ? Math.max(v.seconds / max, 0.1) : 0
            const mins = Math.round(v.seconds / 60)
            const label = v.series_index != null ? `Vol ${v.series_index}` : v.title
            const tip = `${label}: ${mins > 0 ? `${mins}m` : 'unread'}`
            return (
              <div
                key={v.book_id}
                className={cn(
                  'flex-1 min-w-px transition-colors',
                  // Unread volumes are a hairline tick on the baseline — the old
                  // 8%-height rounded stubs read as clipped, half-loaded bars
                  // (UX sweep finding).
                  isRead
                    ? 'rounded-t-sm bg-primary/60 hover:bg-primary'
                    : 'bg-primary/15 hover:bg-primary/40',
                )}
                style={{ height: isRead ? `${Math.round(pct * 100)}%` : '3px' }}
                title={tip}
              />
            )
          })}
        </div>
      </div>
      <div className="flex justify-between text-xs text-muted-foreground/50 mt-1 shrink-0">
        <span>{firstIdx != null ? `Vol ${firstIdx}` : ''}</span>
        <span>{lastIdx != null ? `Vol ${lastIdx}` : ''}</span>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface SeriesReadingStatsProps {
  seriesName: string
}

export function SeriesReadingStats({ seriesName }: SeriesReadingStatsProps) {
  const [data, setData] = useState<SeriesStatsResponse | null>(null)
  const [open, setOpen] = useState(true)

  useEffect(() => {
    setData(null)
    api.get<SeriesStatsResponse>(
      `/series/${encodeURIComponent(seriesName)}/reading-stats`
    ).then(setData).catch(() => {})
  }, [seriesName])

  // Render nothing until loaded, or if the user has no sessions
  if (!data || data.own.sessions === 0) return null

  const { own, aggregate } = data

  const supporting: { label: string; value: string }[] = [
    { label: 'finished', value: `${own.books_finished}/${own.books_total} (${own.completion_pct}%)` },
    { label: 'sessions', value: String(own.sessions) },
    { label: 'pages', value: own.pages_turned > 0 ? String(own.pages_turned) : '—' },
    ...(own.avg_volume_seconds > 0
      ? [{ label: 'avg / volume', value: formatDuration(own.avg_volume_seconds) }] : []),
  ]

  // "Est. remaining" used to live here (avg finished volume × unfinished). The
  // series header now carries the backlog forecast (#187) — one number, not two.
  const bottomStats: { label: string; value: string }[] = []
  if (own.longest_volume != null) {
    bottomStats.push({
      label: 'Longest volume',
      value: own.longest_volume.series_index != null
        ? `Vol ${own.longest_volume.series_index} · ${formatDuration(own.longest_volume.seconds)}`
        : formatDuration(own.longest_volume.seconds),
    })
  }
  if (own.first_read) {
    bottomStats.push({ label: 'First read', value: formatDate(own.first_read.slice(0, 10)) })
  }
  if (own.last_read) {
    bottomStats.push({ label: 'Last read', value: formatDate(own.last_read.slice(0, 10)) })
  }

  return (
    <div className="mt-1 mb-1">
      {/* Collapsible header */}
      <div className="flex items-center gap-2 mb-2.5">
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          aria-expanded={open}
          className="flex items-center gap-1.5 font-display text-base text-foreground hover:text-primary transition-colors"
        >
          Reading Stats
          <ChevronDown className={cn('w-3.5 h-3.5 transition-transform', !open && '-rotate-90')} />
        </button>
      </div>

      {open && (
        <div className="rounded-xl border border-border bg-card px-5 py-4">
          {/* Headline + supporting metrics, one baseline-aligned row */}
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <div className="flex items-baseline gap-2">
              <p className="text-3xl font-semibold tabular-nums text-foreground leading-none">
                {formatDuration(own.total_seconds)}
              </p>
              <p className="text-sm text-muted-foreground">
                across {own.books_with_sessions} volume{own.books_with_sessions !== 1 ? 's' : ''}
              </p>
            </div>
            <div className="flex items-baseline gap-x-5 gap-y-1 flex-wrap">
              {supporting.map(s => (
                <div key={s.label} className="flex items-baseline gap-1.5">
                  <span className="text-sm font-medium tabular-nums text-foreground">{s.value}</span>
                  <span className="text-xs text-muted-foreground/70">{s.label}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Per-volume chart */}
          {own.per_volume.length > 0 && (
            <div className="mt-4">
              <VolumeChart volumes={own.per_volume} />
            </div>
          )}

          {/* Dates row — hairline-divided columns, no boxes */}
          {bottomStats.length > 0 && (
            <div className="mt-4 pt-3 border-t border-border/60 grid grid-cols-2 sm:flex">
              {bottomStats.map((s, i) => (
                <div key={s.label} className={cn('sm:flex-1 sm:px-4', i === 0 && 'sm:pl-0', i > 0 && 'sm:border-l sm:border-border/60')}>
                  <p className="text-xs text-muted-foreground/70">{s.label}</p>
                  <p className="text-sm font-medium tabular-nums text-foreground">{s.value}</p>
                </div>
              ))}
            </div>
          )}

          {/* Admin aggregate footer */}
          {aggregate && (
            <p className="text-xs text-muted-foreground/60 mt-3">
              All readers: {formatDuration(aggregate.total_seconds)} · {aggregate.total_sessions} session{aggregate.total_sessions !== 1 ? 's' : ''} · {aggregate.distinct_readers} reader{aggregate.distinct_readers !== 1 ? 's' : ''}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
