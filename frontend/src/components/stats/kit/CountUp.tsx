// A figure that counts up from zero when its section's entry plays (motion.ts),
// and from the figure it shows to the new one when the data changes after that
// (a new range): the change reads as a change, not a restart.
//   <CountUp entry={e} value={h.total_sessions} format={num} />
//   <CountUp entry={e} value={h.total_reading_seconds} format={formatDuration} />
// The final text is laid out invisibly and the counting text is painted over it,
// left-aligned (align="end" for right-aligned figures), so the figure's width is
// the final one from the first frame and nothing beside it moves. Use it inside
// tabular-nums text. The counting writes the DOM directly from requestAnimationFrame:
// no React render per frame. Screen readers get the final value. Reduced motion,
// and a new value before the section was seen: the final value at once.
import { useLayoutEffect, useRef } from 'react'
import { cn } from '@/lib/utils'
import { DUR, type Entry, type Phase } from './motion'

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
  // The frame loop, shared by the entry (from 0) and a change (from the figure shown).
  const loop = useRef({ raf: 0, shown: 0, running: false })
  const api = useRef<{ show: (n: number) => void; stop: () => void; count: (from: number, wait: number) => void } | null>(null)
  useLayoutEffect(() => {
    const L = loop.current
    const show = (n: number) => {
      L.shown = n
      if (paint.current) paint.current.textContent = live.current.format(n)
    }
    const stop = () => { cancelAnimationFrame(L.raf); L.running = false }
    const count = (from: number, wait: number) => {
      stop()
      const t0 = performance.now() + wait
      const whole = Number.isInteger(from) && Number.isInteger(live.current.value)
      L.running = true
      const step = (now: number) => {
        const t = Math.min(1, Math.max(0, (now - t0) / DUR.count))
        const v = live.current.value
        const n = from + (v - from) * easeOut(t)
        show(t >= 1 ? v : whole ? Math.round(n) : n)
        if (t < 1) L.raf = requestAnimationFrame(step)
        else L.running = false
      }
      show(from)
      L.raf = requestAnimationFrame(step)
    }
    api.current = { show, stop, count }
    const on = (p: Phase) => {
      if (p === 'play') count(0, delay)
      else if (p === 'wait') { stop(); show(0) }
      // A change under way keeps counting; otherwise the figure is final.
      else if (p === 'update') { if (!L.running) show(live.current.value) }
      else { stop(); show(live.current.value) }
    }
    const off = entry.subscribe(on)
    on(entry.phase)
    return () => { off(); stop() }
  }, [entry, delay])
  // A new value: counted from the figure shown once the section has been seen, else shown at once.
  const last = useRef(value)
  useLayoutEffect(() => {
    const prev = last.current
    last.current = value
    const a = api.current
    const p = entry.phase
    if (!a || p === 'play' || p === 'wait') return // the entry's loop reads the live value
    const L = loop.current
    if (prev !== value && entry.update()) a.count(L.running ? L.shown : prev, 0)
    else if (!L.running) a.show(value)
  }, [entry, value, final])
  return (
    <span className={cn('relative inline-block whitespace-nowrap', className)}>
      <span aria-hidden="true" className="invisible">{final}</span>
      <span aria-hidden="true" ref={paint} className={cn('absolute top-0', align === 'end' ? 'right-0' : 'left-0')} />
      <span className="sr-only">{final}</span>
    </span>
  )
}
