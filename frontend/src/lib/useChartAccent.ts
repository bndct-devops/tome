import { useEffect, useState } from 'react'

const FALLBACK = '#6366f1'

function readChartAccent(): string {
  if (typeof window === 'undefined') return FALLBACK
  const v = getComputedStyle(document.documentElement).getPropertyValue('--chart-accent').trim()
  return v || FALLBACK
}

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
