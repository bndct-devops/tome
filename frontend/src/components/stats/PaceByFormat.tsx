import { ProgressRow } from './ProgressRow'

interface FormatPace {
  format: string
  pages_per_min: number
  sessions: number
  pages: number
  seconds: number
}

export function PaceByFormat({ data }: { data: FormatPace[] }) {
  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-4">No pace data by format.</p>
    )
  }

  const maxPpm = Math.max(...data.map(f => f.pages_per_min), 1)

  return (
    <div className="flex flex-col gap-3">
      {data.map(f => (
        <ProgressRow
          key={f.format}
          label={f.format.toUpperCase()}
          value={`${f.pages_per_min} p/min`}
          pct={(f.pages_per_min / maxPpm) * 100}
          sub={`${f.sessions} session${f.sessions !== 1 ? 's' : ''} · ${f.pages} pages`}
          color="#10b981"
        />
      ))}
    </div>
  )
}
