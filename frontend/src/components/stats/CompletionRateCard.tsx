import { BookCheck } from 'lucide-react'

interface CompletionRate {
  started: number
  finished: number
  pct: number
}

export function CompletionRateCard({ data }: { data: CompletionRate | undefined }) {
  if (!data) return null
  return (
    <div className="bg-card border border-border rounded-xl p-4 flex flex-col gap-1">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <BookCheck className="w-3.5 h-3.5" />
        <span className="text-xs">Completion Rate</span>
      </div>
      <p className="text-2xl font-bold text-foreground leading-tight">{data.pct}%</p>
      <p className="text-xs text-muted-foreground">
        {data.finished} of {data.started} started · all time
      </p>
    </div>
  )
}
