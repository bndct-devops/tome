// #2 — Heatmap fills in cell-by-cell, like a time-lapse of a year of reading.
import { useEffect, useState } from 'react'

const DOWS = ['M', 'T', 'W', 'T', 'F', 'S', 'S']

// Synthetic intensity (0-3) for 365 days. Seeded so it's stable.
function generate(): number[] {
  const data: number[] = []
  let seed = 1337
  const rand = () => { seed = (seed * 1664525 + 1013904223) | 0; return ((seed >>> 0) / 4294967296) }
  for (let i = 0; i < 365; i++) {
    const r = rand()
    // Bias toward 0/1 with occasional spikes
    if (r > 0.85) data.push(3)
    else if (r > 0.6) data.push(2)
    else if (r > 0.3) data.push(1)
    else data.push(0)
  }
  return data
}

export function HeatmapFillIn() {
  const [data] = useState(generate)
  const [filled, setFilled] = useState<number>(365) // SSR: all filled

  useEffect(() => {
    let cancelled = false
    const cycle = async () => {
      while (!cancelled) {
        setFilled(0)
        // Fill in over ~5s, then pause 1s, repeat
        const start = performance.now()
        const dur = 5000
        await new Promise<void>(resolve => {
          const tick = (t: number) => {
            if (cancelled) return resolve()
            const pct = Math.min(1, (t - start) / dur)
            setFilled(Math.floor(pct * 365))
            if (pct < 1) requestAnimationFrame(tick)
            else resolve()
          }
          requestAnimationFrame(tick)
        })
        await new Promise(r => setTimeout(r, 1200))
      }
    }
    cycle()
    return () => { cancelled = true }
  }, [])

  // Layout into ~52 weeks x 7 days
  const weeks: number[][] = []
  for (let w = 0; w < 53; w++) {
    const week: number[] = []
    for (let d = 0; d < 7; d++) {
      const idx = w * 7 + d
      if (idx < 365) week.push(idx)
    }
    if (week.length) weeks.push(week)
  }

  const intensityToOpacity = (v: number) => v === 0 ? 0.1 : 0.25 + v * 0.25

  return (
    <div className="p-4">
      <div className="flex items-start gap-2 min-w-0">
        <div className="flex flex-col gap-[3px] pt-1 pr-1">
          {DOWS.map((d, i) => (
            <div key={i} className="text-[9px] text-[var(--muted)] leading-none h-3 flex items-center">{i % 2 === 0 ? d : ''}</div>
          ))}
        </div>
        <div className="flex gap-[3px] overflow-x-auto max-w-full">
          {weeks.map((week, wi) => (
            <div key={wi} className="flex flex-col gap-[3px]">
              {week.map(idx => (
                <div
                  key={idx}
                  className="w-3 h-3 rounded-sm"
                  style={{
                    background: idx < filled
                      ? 'var(--accent)'
                      : 'color-mix(in oklab, var(--border), transparent 60%)',
                    opacity: idx < filled ? intensityToOpacity(data[idx]) : 1,
                    transition: 'background 200ms, opacity 200ms',
                  }}
                />
              ))}
            </div>
          ))}
        </div>
      </div>
      <div className="flex items-center justify-end gap-1 mt-3 text-[10px] text-[var(--muted)]">
        <span>less</span>
        {[0.1, 0.5, 0.75, 1].map(o => (
          <div key={o} className="w-3 h-3 rounded-sm" style={{ background: 'var(--accent)', opacity: o }} />
        ))}
        <span>more</span>
      </div>
    </div>
  )
}
