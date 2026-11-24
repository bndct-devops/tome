/**
 * Small pieces of the Library toolbar that keep it still.
 *
 *   <ToggleFill on={pressed} />    inside a `relative isolate` quiet Pill: the
 *                                  pressed fill grows in instead of switching
 *   <ReserveLabel labels={all}>{current}</ReserveLabel>
 *                                  as wide as the widest label, so a label
 *                                  change moves nothing beside it
 *   <CountLabel text={plural(n, …)} value={n} />
 *                                  the translated text with its number running
 *                                  (RunningNumber) from the last value
 */
import { useLingui } from '@lingui/react/macro'
import { RunningNumber } from '@/components/motion'
import { cn } from '@/lib/utils'
import './library.css'

export function ToggleFill({ on, className }: { on: boolean; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        'pointer-events-none absolute inset-0 -z-10 rounded-full bg-secondary',
        'transition-[scale,opacity] duration-150 ease-(--spring) motion-reduce:transition-none',
        on ? 'opacity-100 scale-100' : 'opacity-0 scale-75',
        className,
      )}
    />
  )
}

export function ReserveLabel({ labels, children, className }: { labels: string[]; children: React.ReactNode; className?: string }) {
  return (
    <span className={cn('reserve-label', className)} data-reserve={labels.join('\n')}>
      <span>{children}</span>
    </span>
  )
}

/** `text` holds `value` formatted for the locale once (as Lingui's `#` does). */
export function CountLabel({ text, value, className }: { text: string; value: number; className?: string }) {
  const { i18n } = useLingui()
  const fmt = (n: number) => Math.round(n).toLocaleString(i18n.locale)
  const shown = fmt(value)
  const at = text.indexOf(shown)
  if (at < 0 || text.indexOf(shown, at + 1) >= 0) return <span className={className}>{text}</span>
  return (
    <span className={className}>
      {text.slice(0, at)}
      <RunningNumber value={value} format={fmt} />
      {text.slice(at + shown.length)}
    </span>
  )
}
