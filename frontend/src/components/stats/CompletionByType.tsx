import { ProgressRow } from './ProgressRow'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

interface CompletionByTypeEntry {
  category: string
  started: number
  finished: number
  pct: number
}

export function CompletionByType({ data }: { data: CompletionByTypeEntry[] }) {
  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-4"><Trans>No completion data by type.</Trans></p>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {data.map(c => (
        <ProgressRow
          key={c.category}
          label={c.category}
          value={`${c.pct}%`}
          pct={c.pct}
          sub={(() => { const fin = c.finished, st = c.started; return t`${fin} of ${st} finished` })()}
        />
      ))}
    </div>
  )
}
