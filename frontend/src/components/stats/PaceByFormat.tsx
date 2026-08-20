import { ProgressRow } from './ProgressRow'
import { useChartAccent } from '@/lib/useChartAccent'
import { Trans } from '@lingui/react/macro'
import { plural } from '@lingui/core/macro'

interface FormatPace {
  format: string
  pages_per_min: number
  sessions: number
  pages: number
  seconds: number
}

export function PaceByFormat({ data }: { data: FormatPace[] }) {
  const accent = useChartAccent()
  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-4"><Trans>No pace data by format.</Trans></p>
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
          sub={(() => { const pages = f.pages; return plural(f.sessions, { one: `# session · ${pages} pages`, other: `# sessions · ${pages} pages` }) })()}
          color={accent}
        />
      ))}
    </div>
  )
}
