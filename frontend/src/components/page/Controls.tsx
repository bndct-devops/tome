import { forwardRef, useLayoutEffect, useRef, useState, type CSSProperties, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { Check } from 'lucide-react'
import { cancelFrame, frame } from 'motion/react'
import { cn } from '@/lib/utils'
import { Glide, GlideGroup } from './Glide'
import { PRESS_LABEL } from '@/components/motion/press'

// Form controls in the refreshed vocabulary. One filled, borderless field
// (bg-secondary, 36px, 14px text, ring on focus); selects get the chevron from
// `.tome-select`; checkboxes and radios are drawn, never the browser's own.

export const INPUT_CLASS =
  'w-full h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground ' +
  'focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50 disabled:cursor-not-allowed'

type InputProps = InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ invalid, className, ...rest }, ref) {
  return <input ref={ref} className={cn(INPUT_CLASS, invalid && 'ring-1 ring-destructive', className)} {...rest} />
})

type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & { invalid?: boolean }
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea({ invalid, className, ...rest }, ref) {
  return (
    <textarea
      ref={ref}
      className={cn(INPUT_CLASS, 'h-auto min-h-[88px] py-2 leading-relaxed resize-y', invalid && 'ring-1 ring-destructive', className)}
      {...rest}
    />
  )
})

type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & { invalid?: boolean }
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select({ invalid, className, children, ...rest }, ref) {
  return (
    <select ref={ref} className={cn(INPUT_CLASS, 'tome-select pr-8 appearance-none', invalid && 'ring-1 ring-destructive', className)} {...rest}>
      {children}
    </select>
  )
})

/** Label over a control. */
export function FormLabel({ htmlFor, children, hint, className }: { htmlFor?: string; children: ReactNode; hint?: ReactNode; className?: string }) {
  return (
    <label htmlFor={htmlFor} className={cn('block text-[13px] text-muted-foreground mb-1.5', className)}>
      {children}
      {hint && <span className="ml-1.5 text-muted-foreground/70">{hint}</span>}
    </label>
  )
}

type ChoiceProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  label?: ReactNode
  /** A second, muted line under the label. */
  description?: ReactNode
}

/** A drawn checkbox: 18px, filled with the accent when checked. */
export const Checkbox = forwardRef<HTMLInputElement, ChoiceProps>(function Checkbox({ label, description, className, disabled, ...rest }, ref) {
  return (
    <label className={cn('inline-flex items-start gap-2.5 cursor-pointer select-none', disabled && 'opacity-50 cursor-not-allowed', className)}>
      <span className="relative mt-0.5 inline-flex h-[18px] w-[18px] shrink-0">
        <input ref={ref} type="checkbox" disabled={disabled} className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed" {...rest} />
        {/* data-check-box: a list that selects hundreds of rows at once stops this fade by that name. */}
        <span data-check-box className="pointer-events-none inline-flex h-full w-full items-center justify-center rounded-[5px] bg-card ring-1 ring-muted-foreground/45 transition-colors peer-checked:bg-primary peer-checked:ring-primary peer-focus-visible:ring-2 peer-focus-visible:ring-ring [&>svg]:opacity-0 peer-checked:[&>svg]:opacity-100">
          <Check className="h-3 w-3 text-primary-foreground" strokeWidth={3} />
        </span>
      </span>
      {(label || description) && (
        <span className="min-w-0 text-[14px] leading-[18px] text-foreground">
          {label}
          {description && <span className="mt-0.5 block text-[13px] text-muted-foreground">{description}</span>}
        </span>
      )}
    </label>
  )
})

/** A drawn radio: 18px ring, accent dot when checked. */
export const Radio = forwardRef<HTMLInputElement, ChoiceProps>(function Radio({ label, description, className, disabled, ...rest }, ref) {
  return (
    <label className={cn('inline-flex items-start gap-2.5 cursor-pointer select-none', disabled && 'opacity-50 cursor-not-allowed', className)}>
      <span className="relative mt-0.5 inline-flex h-[18px] w-[18px] shrink-0">
        <input ref={ref} type="radio" disabled={disabled} className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed" {...rest} />
        <span className="pointer-events-none inline-flex h-full w-full items-center justify-center rounded-full bg-card ring-1 ring-muted-foreground/45 transition-colors peer-checked:ring-primary peer-focus-visible:ring-2 peer-focus-visible:ring-ring [&>span]:scale-0 peer-checked:[&>span]:scale-100">
          <span className="h-2 w-2 rounded-full bg-primary transition-transform" />
        </span>
      </span>
      {(label || description) && (
        <span className="min-w-0 text-[14px] leading-[18px] text-foreground">
          {label}
          {description && <span className="mt-0.5 block text-[13px] text-muted-foreground">{description}</span>}
        </span>
      )}
    </label>
  )
})

// When the gliding indicator (GLIDE: spring, 0.3 s, bounce 0.14) is halfway
// onto the chosen pill and halfway off the pill left, by how many pills it
// travels (1 to 6+), in ms. Worked out from the spring's equation.
const ARRIVE_MS = [90, 137, 160, 175, 185, 194]
const LEAVE_MS = [90, 53, 41, 34, 30, 27]
// The label's colour change (ease-in-out, 100 ms) is half done 50 ms in; the
// indicator starts a frame after the click.
const LABEL_HALF_MS = 50 - 16

// While the indicator travels it covers the pills between the old choice and
// the new one, whose labels share its colour. For that time every label is
// painted in two colours split where the indicator's edges are (the page's
// background colour over the indicator, its text colour elsewhere), so each
// part of every label reads in every frame. Two rect reads per frame; the
// labels do not move in the row, so theirs are read once. It runs in motion's frame loop
// after the indicator is drawn for the frame, so the split never lags it.
function followIndicator(row: HTMLElement): () => void {
  const labels = Array.from(row.querySelectorAll<HTMLElement>('[role="radio"] > span:last-child'))
  // Offsets in the row (the row itself may move while the page changes under it).
  const at = (r: DOMRect, o: DOMRect) => ({ left: r.left - o.left, right: r.right - o.left, top: r.top - o.top, bottom: r.bottom - o.top, width: r.width, height: r.height })
  const origin = row.getBoundingClientRect()
  const boxes = labels.map(l => at(l.getBoundingClientRect(), origin))
  const chosen = row.querySelector('[aria-checked="true"]')
  const target = chosen && at(chosen.getBoundingClientRect(), origin)
  const start = performance.now()
  let still = 0
  const clear = () => labels.forEach(l => {
    l.style.removeProperty('color'); l.style.removeProperty('background-image')
    l.style.removeProperty('background-clip'); l.style.removeProperty('-webkit-background-clip')
  })
  const step = () => {
    const el = row.querySelector('[aria-checked="true"] > span[aria-hidden]')
    if (!el || !target) { stop(); return }
    const ind = at(el.getBoundingClientRect(), row.getBoundingClientRect())
    labels.forEach((l, i) => {
      const box = boxes[i]
      const over = Math.min(box.bottom, ind.bottom) - Math.max(box.top, ind.top) > box.height / 2
      const a = over ? Math.max(0, Math.min(box.width, ind.left - box.left)) : 0
      const z = over ? Math.max(0, Math.min(box.width, ind.right - box.left)) : 0
      l.style.color = 'transparent'
      l.style.setProperty('-webkit-background-clip', 'text')
      l.style.backgroundClip = 'text'
      // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS
      l.style.backgroundImage = `linear-gradient(90deg, var(--foreground) ${a}px, var(--background) ${a}px, var(--background) ${z}px, var(--foreground) ${z}px)`
    })
    const landed = Math.abs(ind.left - target.left) < 0.5 && Math.abs(ind.width - target.width) < 0.5 && Math.abs(ind.top - target.top) < 0.5
    still = landed ? still + 1 : 0
    if ((still >= 2 && performance.now() - start > 80) || performance.now() - start > 900) stop()
  }
  const stop = () => { cancelFrame(step); clear() }
  frame.postRender(step, true)
  return stop
}

/** One choice of a PillGroup. */
export interface PillOption<T extends string> {
  value: T
  label: ReactNode
  disabled?: boolean
  /** The pill's tooltip. */
  title?: string
  /** Unavailable but still focusable and clickable (the click can explain why): drawn
   *  dimmed with a not-allowed cursor and no hover; `onChange` still gets the click. */
  'aria-disabled'?: boolean
  /** Drawn faded while not chosen (one already in use, say), still a full choice. */
  dimmed?: boolean
}

/** A row of exclusive choices as pills (sort order, scope, layout preset). */
export function PillGroup<T extends string>({ value, onChange, options, size = 'md', className, pillClassName, 'aria-label': ariaLabel }: {
  value: T
  onChange: (v: T) => void
  options: PillOption<T>[]
  size?: 'sm' | 'md'
  className?: string
  /** Added to every pill (after the size's own classes, so it can change the padding). */
  pillClassName?: string
  'aria-label'?: string
}) {
  // The option chosen before this one, for how far the indicator travels.
  const [step, setStep] = useState({ value, prev: value })
  if (step.value !== value) setStep({ value, prev: step.value })
  const index = (v: T) => options.findIndex(o => o.value === v)
  const pills = Math.min(6, Math.max(1, Math.abs(index(value) - index(step.prev))))
  // Each label turns over as the indicator passes its middle: the chosen one
  // as it lands, the one left as it slides off. A shared timing left one of
  // the two in the colour of what was behind it for most of a long glide.
  const delay = (v: T) => v === value
    ? ARRIVE_MS[pills - 1] - LABEL_HALF_MS
    : v === step.prev ? Math.max(0, LEAVE_MS[pills - 1] - LABEL_HALF_MS) : 0

  const rowRef = useRef<HTMLDivElement>(null)
  const shown = useRef(value)
  useLayoutEffect(() => {
    if (shown.current === value || !rowRef.current) return
    shown.current = value
    return followIndicator(rowRef.current)
  }, [value])

  // The filled pill is one indicator that glides to the chosen option; it sits
  // over the other pills' fills and under every label.
  return (
    <GlideGroup value={value}>
      <div ref={rowRef} role="radiogroup" aria-label={ariaLabel} className={cn('isolate inline-flex flex-wrap gap-1.5', className)}>
        {options.map(o => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={o.value === value}
            aria-disabled={o['aria-disabled']}
            title={o.title}
            disabled={o.disabled}
            onClick={() => onChange(o.value)}
            style={{ '--pg-delay': `${delay(o.value)}ms` } as CSSProperties}
            className={cn(
              'group/pg relative rounded-full font-medium transition-colors duration-100 ease-in-out disabled:opacity-50',
              '[transition-delay:var(--pg-delay)] motion-reduce:[transition-delay:0ms]',
              size === 'sm' ? 'h-8 px-3 text-[13px]' : 'h-9 px-4 text-[14px]',
              o.value === value
                ? 'text-background'
                : 'bg-secondary text-foreground hover:bg-surface-hover',
              o.value !== value && (o.dimmed || o['aria-disabled']) && 'opacity-55',
              o['aria-disabled'] && 'cursor-not-allowed hover:bg-secondary',
              pillClassName,
            )}
          >
            {o.value === value && <Glide className="bg-foreground" />}
            <span className={cn('relative z-[1]', PRESS_LABEL)}>{o.label}</span>
          </button>
        ))}
      </div>
    </GlideGroup>
  )
}
