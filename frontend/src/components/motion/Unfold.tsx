/**
 * Unfold: an inline panel that opens and closes in place (a filter panel, an
 * expandable section, a confirm row, an inline form).
 *
 *   {open && <div className="…">…</div>}
 * becomes
 *   <Unfold open={open} className="…">…</Unfold>
 *
 * Opening grows the height from 0 over 180 ms with the content fully opaque,
 * so it is revealed by the growing clip rather than faded up from nothing;
 * closing folds it away and lets it fade. The clip is on only while moving
 * (menus inside can hang out once it is open), and the box is a block
 * formatting context throughout, so nothing shifts when the clip comes off.
 *
 * `gap`: inside a flex or grid column with a gap, pass the gap in px; the
 * closed panel takes it back so its neighbours do not jump by the gap.
 * `initial`: animate on first mount too (default: an already open panel just
 * shows). Interrupting (toggling mid-way) reverses from where it is.
 */
import { type ComponentProps } from 'react'
import { AnimatePresence, m } from 'motion/react'
import { cn } from '@/lib/utils'
import { EASE } from './presets'

type DivProps = Omit<ComponentProps<typeof m.div>, 'initial' | 'animate' | 'exit' | 'transition' | 'children'>

const UNFOLD_TRANSITION = { duration: 0.18, ease: EASE }

export function Unfold({ open, gap = 0, initial = false, className, children, ...rest }: DivProps & {
  open: boolean
  gap?: number
  initial?: boolean
  children?: React.ReactNode
}) {
  return (
    <AnimatePresence initial={initial}>
      {open && (
        <m.div
          key="unfold"
          initial={{ height: 0, marginTop: -gap, overflow: 'hidden' }}
          animate={{ height: 'auto', opacity: 1, marginTop: 0, transitionEnd: { overflow: 'visible' } }}
          exit={{ height: 0, opacity: 0, marginTop: -gap, overflow: 'hidden' }}
          transition={UNFOLD_TRANSITION}
          className={cn('flow-root', className)}
          {...rest}
        >
          {children}
        </m.div>
      )}
    </AnimatePresence>
  )
}
