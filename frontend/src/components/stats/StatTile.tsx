import type { ReactNode } from 'react'

interface StatTileProps {
  icon: ReactNode
  label: string
  value: string
}

/** Compact labelled metric tile - used in reading stats cards. */
export function StatTile({ icon, label, value }: StatTileProps) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        {icon}
        <p className="text-[13px]">{label}</p>
      </div>
      <p className="text-[15px] font-semibold text-foreground">{value}</p>
    </div>
  )
}
