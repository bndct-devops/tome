/**
 * Tome's motion kit. One way to do each thing; the header of each file says
 * how to use it.
 *
 *   Pop             menus, popovers, small floating panels
 *   Unfold          inline panels, expandable sections, confirm rows
 *   RunningNumber   a figure that runs to its new value
 *   CoverLift       hover tilt + lift + press for any clickable cover
 *   BookLink, useBookNav, SeriesLink, useSeriesNav
 *                   open a book or a series: preview state + cover transition
 *   TransitionRouter
 *                   the app's router with the page cross-fade (App.tsx)
 *   EASE, LIST, listItem, stepSwap
 *                   shared numbers; listItem for rows in a changing list,
 *                   stepSwap for one step of a flow replacing the next
 *   Swap            one block replaced by another in place
 *   PRESS, PRESS_LABEL
 *                   press + colour transition classes (Pill, PillGroup)
 *
 * Elsewhere, still the shared pieces: Glide / GlideGroup / PillGroup
 * (components/page) for an indicator that moves between options, ModalShell
 * for dialogs, `.touch-feedback` for press on plain buttons.
 */
export { Pop } from './Pop'
export { Unfold } from './Unfold'
export { RunningNumber } from './RunningNumber'
export { CoverLift } from './CoverLift'
export { BookLink, SeriesLink } from './BookLink'
export { useBookNav, useSeriesNav, bookPath, seriesPath, coverThumb, type CoverSource } from './nav'
export { TransitionRouter } from './TransitionRouter'
export { EASE, EASE_CSS, LIST, listItem, stepSwap } from './presets'
export { Swap } from './Swap'
export { PRESS, PRESS_LABEL } from './press'
export type { BookPreview } from '@/lib/coverTransition'
