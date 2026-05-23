import { useChartAccent } from '@/lib/useChartAccent'

export function ProgressRow({
  label,
  value,
  pct,
  sub,
  color,
}: {
  label: string
  value: string
  pct: number
  sub?: string
  color?: string
}) {
  const accent = useChartAccent()
  const fill = color ?? accent
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-foreground truncate flex-1">{label}</span>
        <span className="text-xs text-muted-foreground shrink-0">{value}</span>
      </div>
      {sub && <span className="text-[10px] text-muted-foreground">{sub}</span>}
      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${Math.min(pct, 100)}%`, backgroundColor: fill }}
        />
      </div>
    </div>
  )
}
