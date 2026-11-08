import { BookCheck } from 'lucide-react'
import { Trans } from '@lingui/react/macro'

interface CompletionRate {
  started: number
  finished: number
  pct: number
}

export function CompletionRateCard({ data }: { data: CompletionRate | undefined }) {
  if (!data) return null
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <BookCheck className="w-3.5 h-3.5" />
        <span className="text-[14px]"><Trans>Completion rate</Trans></span>
      </div>
      <p className="font-display text-[30px] font-bold leading-none text-foreground">{data.pct}%</p>
      <p className="text-[14px] text-muted-foreground">
        {(() => { const fin = data.finished, st = data.started; return <Trans>{fin} of {st} started · all time</Trans> })()}
      </p>
    </div>
  )
}
