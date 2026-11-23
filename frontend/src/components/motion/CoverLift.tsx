/**
 * CoverLift: the one hover behaviour for a clickable cover anywhere in the app.
 *
 *   <CoverLift className="w-[104px]">
 *     <button …><CoverImage … /></button>
 *   </CoverLift>
 *
 * The cover leans a few degrees toward the pointer and lifts 3 px (the Library
 * cards' numbers, `COVER_TILT` in lib/tilt.ts) and settles on leave; a press
 * shrinks it slightly. Off under reduced motion and on touch screens (the
 * press stays). `className` goes on the element that tilts (size it there),
 * `wrapperClassName` on the outer box that holds the perspective. The link or
 * button inside keeps its own focus ring. `disabled` holds it still (e.g. while
 * selecting).
 */
import { type ReactNode } from 'react'
import { useTilt } from '@/lib/tilt'
import { cn } from '@/lib/utils'
import './motion.css'

export function CoverLift({ children, className, wrapperClassName, disabled = false }: {
  children: ReactNode
  className?: string
  wrapperClassName?: string
  disabled?: boolean
}) {
  const { wrapperProps, tiltProps } = useTilt(!disabled)
  return (
    <div {...wrapperProps} className={cn('cover-lift shrink-0', wrapperClassName)}>
      <div {...tiltProps} className={className}>{children}</div>
    </div>
  )
}
