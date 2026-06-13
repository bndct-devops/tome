import type { ReactNode } from 'react'
import { useChartAccent } from '@/lib/useChartAccent'

interface ProgressRingProps {
  /** Progress percentage 0–100+. Arc is clamped at 100% visually. */
  pct: number
  /** Outer diameter in px (default 80) */
  size?: number
  /** Stroke width in px (default 7) */
  stroke?: number
  /** Content rendered in the centre of the ring */
  children?: ReactNode
}

/**
 * SVG circular progress ring.
 * Track uses border color, arc uses the theme primary accent.
 * linecap is round; arc is clamped at 100% even when pct > 100.
 */
export function ProgressRing({ pct, size = 80, stroke = 7, children }: ProgressRingProps) {
  const accent = useChartAccent()
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const clamped = Math.min(Math.max(pct, 0), 100)
  const dashOffset = circumference - (clamped / 100) * circumference

  return (
    <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        style={{ transform: 'rotate(-90deg)' }}
        aria-hidden="true"
      >
        {/* Track */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={stroke}
          className="text-border"
        />
        {/* Progress arc */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={accent}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          style={{ transition: 'stroke-dashoffset 0.4s ease' }}
        />
      </svg>
      {children && (
        <div className="absolute inset-0 flex items-center justify-center">
          {children}
        </div>
      )}
    </div>
  )
}
