/** Colours and the press together, 150 ms, on the app's curve, for a button
 *  that also carries `.touch-feedback` (Pill). `.touch-feedback` (index.css,
 *  unlayered) lists the colour properties too, but not box-shadow or filter. */
export const PRESS = 'transition-[color,background-color,box-shadow,filter,transform]! duration-150! ease-(--spring)!'

/** Press feedback for an option that hosts a Glide: the label shrinks, not
 *  the button, since a scaled button would skew the indicator's measurement.
 *  Put `group/pg` on the option and this on its label span. */
export const PRESS_LABEL = 'inline-block transition-transform duration-150 ease-(--spring) group-active/pg:scale-[0.95] motion-reduce:transition-none motion-reduce:group-active/pg:scale-100'
