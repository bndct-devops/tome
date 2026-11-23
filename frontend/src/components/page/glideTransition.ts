import type { Transition } from 'motion/react'

/** Travel of an indicator or a settling row: a short spring with a small overshoot. */
export const GLIDE: Transition = { type: 'spring', visualDuration: 0.3, bounce: 0.14 }
