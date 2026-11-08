export function StatCard({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub?: string
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        {icon}
        <span className="text-[14px]">{label}</span>
      </div>
      <p className="font-display text-[30px] font-bold leading-none text-foreground">{value}</p>
      {sub && <p className="text-[14px] text-muted-foreground">{sub}</p>}
    </div>
  )
}
