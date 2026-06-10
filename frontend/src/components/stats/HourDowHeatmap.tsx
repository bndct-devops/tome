import { useState } from 'react'
import { useChartColors } from '@/lib/useChartAccent'

interface HourDowCell {
  dow: number
  hour: number
  seconds: number
  sessions: number
}

const DOW_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function formatDuration(seconds: number): string {
  if (seconds === 0) return '0m'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h === 0) return `${m}m`
  return `${h}h ${m}m`
}

export function HourDowHeatmap({ data }: { data: HourDowCell[] }) {
  const { accent, tick } = useChartColors()
  const [tooltip, setTooltip] = useState<{
    x: number
    y: number
    dow: number
    hour: number
    seconds: number
    sessions: number
  } | null>(null)

  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-8">No session data.</p>
    )
  }

  const maxSeconds = Math.max(...data.map(c => c.seconds), 1)

  function getFillOpacity(seconds: number): number {
    if (seconds === 0) return 0.08
    return 0.2 + (seconds / maxSeconds) * 0.8
  }

  const CELL = 14
  const GAP = 2
  const LEFT_PAD = 34
  const TOP_PAD = 18

  return (
    <div className="relative overflow-x-auto">
      <svg
        width={LEFT_PAD + 24 * (CELL + GAP)}
        height={TOP_PAD + 7 * (CELL + GAP)}
        className="mx-auto"
        style={{ display: 'block' }}
      >
        {Array.from({ length: 24 }, (_, h) => (
          (h % 3 === 0) ? (
            <text
              key={h}
              x={LEFT_PAD + h * (CELL + GAP) + CELL / 2}
              y={12}
              fontSize={8}
              fill={tick}
              textAnchor="middle"
            >
              {h}
            </text>
          ) : null
        ))}

        {DOW_LABELS.map((label, d) => (
          <text
            key={d}
            x={LEFT_PAD - 4}
            y={TOP_PAD + d * (CELL + GAP) + CELL - 2}
            fontSize={8}
            fill={tick}
            textAnchor="end"
          >
            {label}
          </text>
        ))}

        {data.map(cell => (
          <rect
            key={`${cell.dow}-${cell.hour}`}
            x={LEFT_PAD + cell.hour * (CELL + GAP)}
            y={TOP_PAD + cell.dow * (CELL + GAP)}
            width={CELL}
            height={CELL}
            rx={2}
            fill={cell.seconds === 0 ? tick : accent}
            fillOpacity={getFillOpacity(cell.seconds)}
            style={{ cursor: cell.seconds > 0 ? 'default' : 'default' }}
            onMouseEnter={e =>
              setTooltip({
                x: e.clientX,
                y: e.clientY,
                dow: cell.dow,
                hour: cell.hour,
                seconds: cell.seconds,
                sessions: cell.sessions,
              })
            }
            onMouseLeave={() => setTooltip(null)}
          />
        ))}
      </svg>

      {tooltip && (
        <div
          className="fixed z-50 pointer-events-none bg-card border border-border rounded-lg shadow-xl px-3 py-2 text-xs"
          style={{
            left: Math.min(tooltip.x + 12, window.innerWidth - 180),
            top: tooltip.y - 44,
          }}
        >
          <div className="font-medium">
            {DOW_LABELS[tooltip.dow]} {tooltip.hour}:00
          </div>
          <div className="text-muted-foreground">
            {tooltip.seconds > 0
              ? `${formatDuration(tooltip.seconds)} · ${tooltip.sessions} session${tooltip.sessions !== 1 ? 's' : ''}`
              : 'No activity'}
          </div>
        </div>
      )}
    </div>
  )
}
