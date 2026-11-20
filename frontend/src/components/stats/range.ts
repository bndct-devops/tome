// The Stats range presets and how a custom range's ends are printed.
import { msg } from '@lingui/core/macro'

export type CustomRange = { start: string; end: string }

export const RANGES = [
  { days: 7, label: msg`7d` },
  { days: 30, label: msg`30d` },
  { days: 90, label: msg`90d` },
  { days: 365, label: msg`1y` },
  { days: 0, label: msg`All` },
]

export const fmtDay = (s: string) => {
  try {
    return new Date(s + 'T00:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  } catch {
    return s
  }
}
