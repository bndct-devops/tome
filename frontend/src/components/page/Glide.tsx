import { useState, type ReactNode } from 'react'
import { LayoutGroup, m } from 'motion/react'
import { cn } from '@/lib/utils'
import { GLIDE } from './glideTransition'

// One indicator per control that glides from the old option to the new one.
// The group id is unique per mounted instance (a counter, not useId: two pages
// can share a tree position), so a freshly mounted control paints its
// indicator in place instead of flying in from another page's copy.

let seq = 0

/** Wraps one control; every `<Glide>` inside shares the same indicator. */
export function GlideGroup({ children }: { children: ReactNode }) {
  const [id] = useState(() => `glide-${++seq}`)
  return <LayoutGroup id={id}>{children}</LayoutGroup>
}

/**
 * The indicator, rendered inside the active option only. `className` sets its
 * colour; `radius` is applied as a style so the corners stay round while it
 * stretches. Behind (`-z-10`): needs an `isolate` ancestor and transparent
 * option backgrounds. Default (`z-0`): over sibling backgrounds, so the labels
 * must sit on `relative z-[1]`.
 *
 * Not for content that moves on a CSS transform of an ancestor (the phone
 * drawer): it measures on every render and would chase the ancestor's slide.
 */
export function Glide({ className, radius = 9999, behind = false, layoutId = 'glide' }: {
  className?: string
  radius?: number
  behind?: boolean
  layoutId?: string
}) {
  return (
    <m.span
      aria-hidden
      layoutId={layoutId}
      transition={GLIDE}
      style={{ borderRadius: radius }}
      className={cn('pointer-events-none absolute inset-0', behind ? '-z-10' : 'z-0', className)}
    />
  )
}
