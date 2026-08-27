import { type ReactNode } from 'react'
import { LazyMotion, MotionConfig } from 'motion/react'

// Async so the engine is a separate chunk; `strict` makes any accidental
// `motion.div` (which would statically pull the full engine back into the
// main bundle) throw in dev — always use `m.div` from 'motion/react'.
const loadFeatures = () => import('./motion-features').then(mod => mod.default)

export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={loadFeatures} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  )
}
