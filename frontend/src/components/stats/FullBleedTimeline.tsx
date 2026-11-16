import { useLayoutEffect, useRef, useState } from 'react'
import { TimelineRibbon } from '@/components/stats/TimelineRibbon'

// A tab whose only tile is the Reading Timeline renders full-bleed in view mode:
// no card, no grid, edge-to-edge and viewport-tall. Edit mode falls back to the
// grid so the tile stays manageable like any other.
export function FullBleedTimeline() {
  const ref = useRef<HTMLDivElement>(null)
  const [h, setH] = useState<number | null>(null)
  useLayoutEffect(() => {
    const measure = () => {
      if (ref.current) setH(Math.max(420, window.innerHeight - ref.current.getBoundingClientRect().top - 16))
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])
  return (
    <div ref={ref} className="border-t border-border/70 pt-4" style={{ height: h ?? 480 }}>
      {h !== null && <TimelineRibbon standalone />}
    </div>
  )
}
