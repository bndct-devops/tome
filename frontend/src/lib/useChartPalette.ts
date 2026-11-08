import { useEffect, useState } from 'react'

// The categorical chart palette is built from the theme's semantic tokens, so
// a pie or a stack uses the same hues as the rest of the page in every theme:
// the chart accent first, then info, success, rating, a muted tone and warning
// (warning last: it sits closest to rating and, in amber, to the accent).
// Charts render into SVG attributes, so the values are resolved, not var() refs.
const TOKENS = ['--chart-accent', '--info', '--success', '--rating', '--muted-foreground', '--warning'] as const

const FALLBACK = ['#8a2b35', '#3f6c9e', '#3f8a5c', '#b08a3a', '#7a756e', '#b07a2a']

function readPalette(): string[] {
  if (typeof window === 'undefined') return FALLBACK
  const style = getComputedStyle(document.documentElement)
  const base = TOKENS.map((name, i) => style.getPropertyValue(name).trim() || FALLBACK[i])
  const card = style.getPropertyValue('--card').trim() || 'transparent'
  // Past six series, the same hues again mixed halfway into the card, so a
  // seventh category never repeats the first one's exact colour.
  const soft = base.map((c) => `color-mix(in oklch, ${c} 55%, ${card})`)
  return [...base, ...soft]
}

export function useChartPalette(): string[] {
  const [palette, setPalette] = useState<string[]>(readPalette)

  useEffect(() => {
    setPalette(readPalette())
    const observer = new MutationObserver(() => setPalette(readPalette()))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] })
    return () => observer.disconnect()
  }, [])

  return palette
}
