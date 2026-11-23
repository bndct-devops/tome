import { createContext, useContext, useState, type ReactNode } from 'react'
import { LayoutGroup, m, useIsPresent, type Transition } from 'motion/react'
import { cn } from '@/lib/utils'
import { GLIDE } from './glideTransition'

// One indicator per control that glides from the old option to the new one.
// The group id is unique per mounted instance (a counter, not useId: two pages
// can share a tree position), so a freshly mounted control paints its
// indicator in place instead of flying in from another page's copy.

let seq = 0

// The control's chosen value, when the group was given one.
const Chosen = createContext<{ value: unknown } | null>(null)

/** Wraps one control; every `<Glide>` inside shares the same indicator.
 *  `value` (the chosen option) makes the indicator travel only when the choice
 *  changes: when the whole control moves (a form opening above it, a row
 *  wrapping) it moves with it instead of chasing it. */
export function GlideGroup({ children, value }: { children: ReactNode; value?: unknown }) {
  const [id] = useState(() => `glide-${++seq}`)
  const group = <LayoutGroup id={id}>{children}</LayoutGroup>
  return value === undefined ? group : <Chosen.Provider value={{ value }}>{group}</Chosen.Provider>
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
export function Glide({ className, radius = 9999, behind = false, layoutId = 'glide', transition = GLIDE }: {
  className?: string
  radius?: number
  behind?: boolean
  layoutId?: string
  /** The travel; the glide spring by default. */
  transition?: Transition
}) {
  const chosen = useContext(Chosen)
  // Inside a row that is leaving (AnimatePresence) it stops being the shared
  // indicator: a layoutId element in a leaving subtree held its exit forever.
  const present = useIsPresent()
  return (
    <m.span
      aria-hidden
      layoutId={present ? layoutId : undefined}
      layoutDependency={chosen ? chosen.value : undefined}
      transition={transition}
      style={{ borderRadius: radius }}
      className={cn('pointer-events-none absolute inset-0', behind ? '-z-10' : 'z-0', className)}
    />
  )
}
