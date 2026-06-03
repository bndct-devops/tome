import type { ReactNode } from 'react'

interface StatTileProps {
  icon: ReactNode
  label: string
  value: string
}

/** Compact labelled metric tile — used in reading stats cards. */
export function StatTile({ icon, label, value }: StatTileProps) {
  return (
    <div className="rounded-md border border-border bg-muted/20 px-2.5 py-1.5 flex flex-col gap-0.5">
      <div className="flex items-center gap-1 text-muted-foreground/60">
        {icon}
        <p className="text-xs">{label}</p>
      </div>
      <p className="text-sm font-medium text-foreground tabular-nums">{value}</p>
    </div>
  )
}
