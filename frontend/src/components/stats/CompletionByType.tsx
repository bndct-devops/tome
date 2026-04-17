import { ProgressRow } from './ProgressRow'

interface CompletionByTypeEntry {
  category: string
  started: number
  finished: number
  pct: number
}

export function CompletionByType({ data }: { data: CompletionByTypeEntry[] }) {
  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-4">No completion data by type.</p>
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
          sub={`${c.finished} of ${c.started} finished`}
          color="#ec4899"
        />
      ))}
    </div>
  )
}
