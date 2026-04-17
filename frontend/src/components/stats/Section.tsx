export function Section({
  title,
  icon,
  children,
}: {
  title: string
  icon?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-sm font-medium text-foreground flex items-center gap-2">
        {icon}
        {title}
      </h2>
      {children}
    </div>
  )
}
