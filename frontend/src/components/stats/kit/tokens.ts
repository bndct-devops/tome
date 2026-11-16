// Shared look of the designed Stats pages: tokens, formatting and the width hook.
// Components are in Kit.tsx (kept apart so fast refresh sees a component-only file).
import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react'

/** Container width from which a designed page uses its desktop layout. */
export const DESKTOP_MIN = 896

/** Hairlines between rows and figures, as Home's (`border-border/70`). */
export const HAIR = 'border-border/70'

/** A range change in flight: the range-bound sections dim in place. */
export const DIM = 'opacity-50 transition-opacity duration-150'
export const UNDIM = 'transition-opacity duration-150'

// The heat scale of the page's charts (hour grids, the year, their legends): one
// hue, the accent laid over the empty cell's colour in steps. Mixed in oklab, a
// straight line between the two, so the mid steps are a dimmer accent; mixed in
// oklch they turned towards the ground's warm hue and read as brown.
export const LEVELS = {
  '--k-lv0': 'color-mix(in oklch, var(--foreground) 7%, var(--background))',
  '--k-lv1': 'color-mix(in oklab, var(--chart-accent) 36%, var(--k-lv0))',
  '--k-lv2': 'color-mix(in oklab, var(--chart-accent) 54%, var(--k-lv0))',
  '--k-lv3': 'color-mix(in oklab, var(--chart-accent) 71%, var(--k-lv0))',
  '--k-lv4': 'color-mix(in oklab, var(--chart-accent) 86%, var(--k-lv0))',
  '--k-lv5': 'var(--chart-accent)',
} as CSSProperties


/** Medium date with the year ("Nov 7, 2026") of a YYYY-MM-DD day. */
export const fmtDay = (iso: string) => new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
export const num = (n: number) => n.toLocaleString()

/** Measures its element's width before the first paint (layout effect), so charts draw once at their final size. */
export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const read = () => setW(Math.floor(el.getBoundingClientRect().width))
    read()
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}
