import { useEffect, useState } from 'react'

// Light & dark: vibrant default — indigo/violet/pink/amber/emerald/blue/red/teal/orange/purple
const DEFAULT_PALETTE = [
  '#6366f1', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981',
  '#3b82f6', '#ef4444', '#14b8a6', '#f97316', '#a855f7',
]

// Amber: warm earth tones that harmonize with the parchment background
const AMBER_PALETTE = [
  '#a0571c', // burnt orange (accent)
  '#6b3a1a', // deep brown
  '#5e6877', // slate
  '#8a7128', // olive
  '#b56b4a', // terracotta
  '#6b7a4a', // sage
  '#9c4a2a', // rust
  '#7a5d4a', // taupe
  '#c08552', // tan
  '#4a5d6b', // steel blue
]

function readPalette(): string[] {
  if (typeof window === 'undefined') return DEFAULT_PALETTE
  return document.documentElement.classList.contains('theme-amber')
    ? AMBER_PALETTE
    : DEFAULT_PALETTE
}

export function useChartPalette(): string[] {
  const [palette, setPalette] = useState<string[]>(readPalette)

  useEffect(() => {
    setPalette(readPalette())
    const observer = new MutationObserver(() => setPalette(readPalette()))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  return palette
}
