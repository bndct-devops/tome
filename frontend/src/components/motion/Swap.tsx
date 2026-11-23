import { useState, type ComponentProps } from 'react'
import { m } from 'motion/react'
import { EASE } from './presets'

type DivProps = Omit<ComponentProps<typeof m.div>, 'initial' | 'animate' | 'exit' | 'transition' | 'children'>

const FROM = { opacity: 0.6, y: 4 }
const TO = { opacity: 1, y: 0, transition: { duration: 0.16, ease: EASE } }

/**
 * Swap: one block replaced by another in the same place (a loader by its
 * content, a value by its edit form, a button row by a confirm row).
 *
 *   <Swap id={editing ? 'edit' : 'view'}>{editing ? form : value}</Swap>
 *
 * A new `id` puts the new block in at once, risen 4 px from 60% opacity
 * (160 ms); the old block goes in the same frame, so the two never overlap,
 * nothing waits, and nothing fades up from nothing. The first render just
 * shows. `className` and other div props go on the wrapping `m.div`.
 */
export function Swap({ id, children, ...rest }: DivProps & { id: string | number; children?: React.ReactNode }) {
  const [first] = useState(id)
  const [changed, setChanged] = useState(false)
  if (!changed && id !== first) setChanged(true)
  return (
    <m.div key={id} initial={changed ? FROM : false} animate={TO} {...rest}>
      {children}
    </m.div>
  )
}
