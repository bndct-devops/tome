/**
 * The shared numbers of Tome's motion. Every piece in this folder uses them;
 * a call site that animates by hand should too.
 *
 *   EASE      the app's curve, the same as `--spring` in CSS.
 *   LIST      transition for a row that moves inside a list.
 *   listItem  props for an `m.div` row in a list that changes:
 *               <AnimatePresence initial={false}>
 *                 {rows.map(r => <m.div key={r.id} {...listItem}>…</m.div>)}
 *               </AnimatePresence>
 *             Rows that stay glide to their new place (position only, 240 ms),
 *             rows that leave fade (120 ms), rows that arrive appear in place
 *             (no fade from nothing inside a painted panel).
 *             Keys must be unique among the rows. A row may hold a gliding
 *             indicator (page/Glide.tsx): Glide drops its layoutId while its
 *             row leaves, because a shared-layout element inside a leaving
 *             row kept the exit from ever finishing (invisible rows stayed
 *             in the DOM on fast typing).
 */
import type { Transition } from 'motion/react'

export const EASE = [0.2, 0.8, 0.2, 1] as const
/** The same curve as a CSS string, for element.animate() and inline styles. */
export const EASE_CSS = 'cubic-bezier(0.2, 0.8, 0.2, 1)'

export const LIST: Transition = {
  layout: { duration: 0.24, ease: EASE },
  duration: 0.12,
  ease: EASE,
}

export const listItem = {
  layout: 'position',
  initial: false,
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: LIST,
} as const

/** One step of a flow giving way to the next in the same place (sign-in,
 *  linking a reader), inside <AnimatePresence mode="wait" initial={false}>
 *  with a `key` per step: the old step fades out (100 ms), the new one rises
 *  4 px from partial opacity (140 ms). */
export const stepSwap = {
  initial: { opacity: 0.4, y: 4 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.14, ease: EASE } },
  exit: { opacity: 0, transition: { duration: 0.1, ease: EASE } },
} as const
