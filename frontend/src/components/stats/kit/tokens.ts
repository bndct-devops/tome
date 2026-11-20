// Shared look of the designed Stats pages: tokens, formatting and the width hook.
// Components are in Kit.tsx (kept apart so fast refresh sees a component-only file).
import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { i18n } from '@lingui/core'

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
// Numbers follow the app's language, the locale Lingui formats a plural's "#" with,
// so a figure, its count-up, its tip and the "# books" beside it all read alike
// ("22,479" in English whatever the browser's own locale; "22 479" in French).
const fmts = new Map<string, Intl.NumberFormat>()
const fmtFor = (digits: number) => {
  const key = `${i18n.locale}|${digits}`
  let f = fmts.get(key)
  if (!f) {
    f = new Intl.NumberFormat(i18n.locale || undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
    fmts.set(key, f)
  }
  return f
}
/** A whole number with the app language's grouping ("22,479"). */
export const num = (n: number) => fmtFor(0).format(n)
/** One decimal in the app language ("4.3"; "4,3" in French). */
export const dec1 = (n: number) => fmtFor(1).format(n)
/** A figure as the server sent it, in the app language: grouped, up to two decimals ("1,117", "96.7", "3.71"). */
export const fig = (n: number) => new Intl.NumberFormat(i18n.locale || undefined, { maximumFractionDigits: 2 }).format(n)
/** A large count in words from a million on ("6.6 million"; "6,6 millions" in French), else as num(). */
export const big = (n: number) =>
  n < 1_000_000 ? num(n) : new Intl.NumberFormat(i18n.locale || undefined, { notation: 'compact', compactDisplay: 'long', maximumFractionDigits: 1 }).format(n)

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
