import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'

interface GrowthEntry {
  month: string
  total: number
  [category: string]: number | string
}

const GROWTH_COLORS = [
  '#6366f1',
  '#8b5cf6',
  '#ec4899',
  '#f59e0b',
  '#10b981',
  '#3b82f6',
  '#ef4444',
  '#14b8a6',
]

function formatMonth(m: string): string {
  try {
    return new Date(m + '-01T00:00:00').toLocaleDateString(undefined, { month: 'short', year: '2-digit' })
  } catch {
    return m
  }
}

function ChartTooltip({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card border border-border rounded-lg shadow-xl px-3 py-2 text-xs">
      {children}
    </div>
  )
}

export function LibraryGrowthChart({ data }: { data: GrowthEntry[] }) {
  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-8">No library growth data.</p>
    )
  }

  const categories = Array.from(
    new Set(data.flatMap(d => Object.keys(d).filter(k => k !== 'month' && k !== 'total')))
  ).sort()

  if (categories.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-8">No library growth data.</p>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <XAxis
          dataKey="month"
          tickFormatter={formatMonth}
          tick={{ fontSize: 10, fill: '#94a3b8' }}
          axisLine={false}
          tickLine={false}
          interval={3}
        />
        <YAxis
          tick={{ fontSize: 10, fill: '#94a3b8' }}
          width={36}
          axisLine={false}
          tickLine={false}
          allowDecimals={false}
        />
        <Tooltip
          cursor={{ fill: 'rgba(255,255,255,0.04)' }}
          wrapperStyle={{ outline: 'none', background: 'none', border: 'none', boxShadow: 'none' }}
          isAnimationActive={false}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const month = payload[0]?.payload?.month
            const total = payload[0]?.payload?.total
            return (
              <ChartTooltip>
                <div className="font-medium mb-1">{formatMonth(month)}</div>
                <div className="text-muted-foreground mb-1">Total: {total}</div>
                {payload
                  .filter(p => (p.value as number) > 0)
                  .map(p => (
                    <div key={p.dataKey as string} className="flex items-center gap-1.5">
                      <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: p.color }} />
                      <span>{p.dataKey as string}: {p.value}</span>
                    </div>
                  ))}
              </ChartTooltip>
            )
          }}
        />
        <Legend formatter={v => <span style={{ fontSize: 11 }}>{v}</span>} wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
        {categories.map((cat, i) => (
          <Area
            key={cat}
            type="monotone"
            dataKey={cat}
            stackId="1"
            fill={GROWTH_COLORS[i % GROWTH_COLORS.length]}
            fillOpacity={0.6}
            stroke={GROWTH_COLORS[i % GROWTH_COLORS.length]}
            strokeWidth={1.5}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}
