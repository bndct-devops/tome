import { useEffect, useState, type ReactNode } from 'react'
import {
  BarChart2, ChevronDown, Clock, Layers, FileText,
  Gauge, Hourglass, CalendarPlus, CalendarCheck, Trophy,
} from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatDuration, formatDate } from '@/lib/utils'
import { StatTile } from '@/components/stats/StatTile'

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
    <div className="flex flex-col flex-1 min-h-0">
      <p className="text-xs text-muted-foreground/70 mb-1.5 shrink-0">Time per volume</p>
      <div className="relative flex-1 min-h-0 max-h-32">
        {/* faint baseline rule */}
        <div className="absolute bottom-0 left-0 right-0 h-px bg-border/40" />
        <div className="absolute inset-0 flex items-end gap-1">
          {volumes.map(v => {
            const isRead = v.seconds > 0
            // Read volumes scale by time (min 10%); unread show a faint floor stub
            const pct = isRead ? Math.max(v.seconds / max, 0.1) : 0.08
            const mins = Math.round(v.seconds / 60)
            const label = v.series_index != null ? `Vol ${v.series_index}` : v.title
            const tip = `${label}: ${mins > 0 ? `${mins}m` : 'unread'}`
            return (
              <div
                key={v.book_id}
                className={cn(
                  'flex-1 min-w-px rounded-t-sm transition-colors',
                  isRead
                    ? 'bg-primary/60 hover:bg-primary'
                    : 'bg-primary/15 hover:bg-primary/30',
                )}
                style={{ height: `${Math.round(pct * 100)}%` }}
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

  const supportingTiles: { icon: ReactNode; label: string; value: string }[] = [
    {
      icon: <BarChart2 className="w-3 h-3" />,
      label: 'Completion',
      value: `${own.books_finished}/${own.books_total} (${own.completion_pct}%)`,
    },
    {
      icon: <Layers className="w-3 h-3" />,
      label: 'Sessions',
      value: String(own.sessions),
    },
    {
      icon: <FileText className="w-3 h-3" />,
      label: 'Pages',
      value: own.pages_turned > 0 ? String(own.pages_turned) : '—',
    },
    {
      icon: <Gauge className="w-3 h-3" />,
      label: 'Avg / volume',
      value: own.avg_volume_seconds > 0 ? formatDuration(own.avg_volume_seconds) : '—',
    },
  ]

  const bottomTiles: { icon: ReactNode; label: string; value: string }[] = []
  if (own.estimated_remaining_seconds != null) {
    bottomTiles.push({
      icon: <Hourglass className="w-3 h-3" />,
      label: 'Est. remaining',
      value: formatDuration(own.estimated_remaining_seconds),
    })
  }
  if (own.longest_volume != null) {
    bottomTiles.push({
      icon: <Trophy className="w-3 h-3" />,
      label: 'Longest vol',
      value: own.longest_volume.series_index != null
        ? `Vol ${own.longest_volume.series_index} · ${formatDuration(own.longest_volume.seconds)}`
        : formatDuration(own.longest_volume.seconds),
    })
  }
  if (own.first_read) {
    bottomTiles.push({
      icon: <CalendarPlus className="w-3 h-3" />,
      label: 'First read',
      value: formatDate(own.first_read.slice(0, 10)),
    })
  }
  if (own.last_read) {
    bottomTiles.push({
      icon: <CalendarCheck className="w-3 h-3" />,
      label: 'Last read',
      value: formatDate(own.last_read.slice(0, 10)),
    })
  }

  const bottomGridCols = bottomTiles.length >= 4
    ? 'grid-cols-2 sm:grid-cols-4'
    : bottomTiles.length === 3
      ? 'grid-cols-2 sm:grid-cols-3'
      : bottomTiles.length === 2
        ? 'grid-cols-2'
        : 'grid-cols-1'

  return (
    <div className="mt-1 mb-1">
      {/* Collapsible header */}
      <div className="flex items-center gap-2 mb-2.5">
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          aria-expanded={open}
          className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide hover:text-foreground transition-colors"
        >
          <BarChart2 className="w-3.5 h-3.5" /> Reading Stats
          <ChevronDown className={cn('w-3.5 h-3.5 transition-transform', !open && '-rotate-90')} />
        </button>
      </div>

      {open && (
        <div className="space-y-2">
          {/* Hero panel + right column tiles */}
          <div className="flex flex-col sm:flex-row gap-2">
            {/* Hero panel */}
            <div className="rounded-lg border border-border bg-muted/30 p-4 flex-1 min-w-0 flex flex-col gap-2.5">
              {/* Headline */}
              <div className="flex items-baseline gap-2 shrink-0">
                <Clock className="w-4 h-4 text-muted-foreground shrink-0 self-center" />
                <p className="text-2xl font-semibold tabular-nums text-foreground leading-none">
                  {formatDuration(own.total_seconds)}
                </p>
                <p className="text-xs text-muted-foreground">
                  across {own.books_with_sessions} volume{own.books_with_sessions !== 1 ? 's' : ''}
                </p>
              </div>
              {/* Per-volume chart */}
              {own.per_volume.length > 0 && (
                <VolumeChart volumes={own.per_volume} />
              )}
            </div>

            {/* Supporting stat tiles */}
            {supportingTiles.length > 0 && (
              <div className="grid grid-cols-2 sm:grid-cols-1 gap-1 sm:w-44 shrink-0">
                {supportingTiles.map(s => (
                  <StatTile key={s.label} icon={s.icon} label={s.label} value={s.value} />
                ))}
              </div>
            )}
          </div>

          {/* Bottom date tiles */}
          {bottomTiles.length > 0 && (
            <div className={`grid ${bottomGridCols} gap-2`}>
              {bottomTiles.map(s => (
                <StatTile key={s.label} icon={s.icon} label={s.label} value={s.value} />
              ))}
            </div>
          )}

          {/* Admin aggregate footer */}
          {aggregate && (
            <p className="text-xs text-muted-foreground/60 pt-1">
              All readers: {formatDuration(aggregate.total_seconds)} · {aggregate.total_sessions} session{aggregate.total_sessions !== 1 ? 's' : ''} · {aggregate.distinct_readers} reader{aggregate.distinct_readers !== 1 ? 's' : ''}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
