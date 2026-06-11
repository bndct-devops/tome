import { useEffect, useState } from 'react'

const FALLBACK = '#6366f1'
const TICK_FALLBACK = '#94a3b8'

function readVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

const readChartAccent = () => readVar('--chart-accent', FALLBACK)

export function useChartAccent(): string {
  const [accent, setAccent] = useState<string>(readChartAccent)

  useEffect(() => {
    setAccent(readChartAccent())
    const html = document.documentElement
    const observer = new MutationObserver(() => setAccent(readChartAccent()))
    observer.observe(html, { attributes: true, attributeFilter: ['class', 'style'] })
    return () => observer.disconnect()
  }, [])

  return accent
}

export interface ChartColors {
  accent: string
  /** Axis-tick / chart-label color — the theme's muted foreground. */
  tick: string
  /** Recharts Tooltip `cursor` prop: subtle hover wash that works on light and dark. */
  cursor: { fill: string; fillOpacity: number }
}

function readChartColors(): ChartColors {
  const tick = readVar('--muted-foreground', TICK_FALLBACK)
  return { accent: readChartAccent(), tick, cursor: { fill: tick, fillOpacity: 0.08 } }
}

// Theme-resolved chart colors (accent + tick), reactive to theme switches.
// Charts render into SVG attributes, so they need resolved values, not var() refs.
export function useChartColors(): ChartColors {
  const [colors, setColors] = useState<ChartColors>(readChartColors)

  useEffect(() => {
    setColors(readChartColors())
    const html = document.documentElement
    const observer = new MutationObserver(() => setColors(readChartColors()))
    observer.observe(html, { attributes: true, attributeFilter: ['class', 'style'] })
    return () => observer.disconnect()
  }, [])

  return colors
}
