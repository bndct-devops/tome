/**
 * RunningNumber: a figure that runs from the value it shows to a new one.
 *
 *   <RunningNumber value={minutes} format={n => formatDuration(n)} />
 *
 * About 300 ms, ease-out, driven by requestAnimationFrame. The first render
 * shows the value as is (no counting up from zero on arrival); under reduced
 * motion every value shows at once. Digits are tabular and the box keeps the
 * width of the wider of the start and end values while it runs, so nothing
 * beside it shifts. A change mid-run carries on from where it is. `format`
 * turns the number into text (default: rounded integer); `className` goes on
 * the wrapping span.
 */
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import './motion.css'

const MS = 300

const plain = (n: number) => String(Math.round(n))

export function RunningNumber({ value, format = plain, className }: {
  value: number
  format?: (n: number) => string
  className?: string
}) {
  const [shown, setShown] = useState(value)
  const [run, setRun] = useState({ from: value, to: value })
  // A new value starts a run from what is on screen now.
  if (run.to !== value) setRun({ from: shown, to: value })

  useEffect(() => {
    const { from, to } = run
    if (from === to) return
    // eslint-disable-next-line lingui/no-unlocalized-strings -- media query
    const instant = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    // performance.now(), not the rAF timestamp: that can lie before t0.
    const t0 = performance.now()
    let raf = 0
    const tick = () => {
      const k = instant ? 1 : Math.min(1, (performance.now() - t0) / MS)
      const e = 1 - Math.pow(1 - k, 3)
      setShown(k < 1 ? from + (to - from) * e : to)
      if (k < 1) raf = requestAnimationFrame(tick)
      else setRun({ from: to, to })
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [run])

  const moving = run.from !== run.to
  // On the way, an integer run shows integers.
  const mid = Number.isInteger(run.from) && Number.isInteger(run.to) ? Math.round(shown) : shown
  return (
    <span
      className={cn('running-number tabular-nums', className)}
      data-reserve={moving ? `${format(run.from)}\n${format(run.to)}` : undefined}
    >
      {format(moving ? mid : value)}
    </span>
  )
}
