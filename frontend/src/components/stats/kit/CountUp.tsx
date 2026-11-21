// A figure that counts up from zero when its section's entry plays (motion.ts).
//   <CountUp entry={e} value={h.total_sessions} format={num} />
//   <CountUp entry={e} value={h.total_reading_seconds} format={formatDuration} />
// The final text is laid out invisibly and the counting text is painted over it,
// left-aligned (align="end" for right-aligned figures), so the figure's width is
// the final one from the first frame and nothing beside it moves. Use it inside
// tabular-nums text. The counting writes the DOM directly from requestAnimationFrame:
// no React render per frame. Screen readers get the final value.
import { useLayoutEffect, useRef } from 'react'
import { cn } from '@/lib/utils'
import { DUR, type Entry } from './motion'

const easeOut = (t: number) => 1 - Math.pow(1 - t, 3)

export function CountUp({ entry, value, format, delay = 0, align = 'start', className }: {
  entry: Entry
  value: number
  format: (n: number) => string
  /** ms after the section starts. */
  delay?: number
  align?: 'start' | 'end'
  className?: string
}) {
  const paint = useRef<HTMLSpanElement>(null)
  const final = format(value)
  // The latest value and format for the frame loop, without restarting it on every render.
  const live = useRef({ value, format })
  useLayoutEffect(() => {
    live.current = { value, format }
  })
  useLayoutEffect(() => {
    const el = paint.current
    if (!el) return
    let raf = 0
    const show = (n: number) => { el.textContent = live.current.format(n) }
    const run = () => {
      const t0 = performance.now() + delay
      const step = (now: number) => {
        const t = Math.min(1, Math.max(0, (now - t0) / DUR.count))
        const v = live.current.value
        show(t >= 1 ? v : Math.round(v * easeOut(t)))
        if (t < 1) raf = requestAnimationFrame(step)
      }
      show(0)
      raf = requestAnimationFrame(step)
    }
    const on = (p: string) => {
      cancelAnimationFrame(raf)
      if (p === 'play') run()
      else if (p === 'wait') show(0)
      else show(live.current.value)
    }
    const off = entry.subscribe(on)
    on(entry.phase)
    return () => { off(); cancelAnimationFrame(raf) }
  }, [entry, delay])
  // A new value outside an entry (e.g. a later answer) shows at once.
  useLayoutEffect(() => {
    if (entry.phase === 'done' || entry.phase === 'idle') { if (paint.current) paint.current.textContent = final }
  }, [entry, final])
  return (
    <span className={cn('relative inline-block whitespace-nowrap', className)}>
      <span aria-hidden="true" className="invisible">{final}</span>
      <span aria-hidden="true" ref={paint} className={cn('absolute top-0', align === 'end' ? 'right-0' : 'left-0')} />
      <span className="sr-only">{final}</span>
    </span>
  )
}
