// Entry motion of the designed Stats pages: one set of durations, easing and
// stagger, and the rule for when an entry plays. The motion itself is CSS
// (motion.css), run by a data-enter attribute that useEntry sets on a section
// element directly, so starting, waiting and finishing never re-render React.
//
// Usage in a section:
//   const e = useEntry('habits/week', stats ? rangeSig(days, custom) : null)
//   <section ref={e.ref} data-box="week">
//     <rect className={K.grow} style={stagger(i, n)} … />        bars grow up from their base
//     <div className={K.growX} …/>                               HTML fills grow from their start (Track does this)
//     <rect className={K.fade} style={stagger(col, cols)} … />    cells / dots / covers fade in (K.rise: also up 6 px)
//     <path className={K.draw} pathLength={1} … />                solid lines draw; K.wipe for dashed lines
//     <CountUp entry={e} value={n} format={num} />                figures count up in a reserved width
//   </section>
//
// When it plays (useEntry): the first time a section has data in this mount (pass
// the range the data answers, rangeSig(); pass ONCE for sections that do not follow
// the range). Not on re-render, hover, or coming back to a tab whose signature
// already played this session (remembered per scope, so use a scope unique to the
// section, e.g. 'overview/hero'). A section below the fold waits (marks held at
// their start) and plays once when 15 % of it is on screen. Reduced motion: final
// state at once. finishEntries() ends every entry (the image export calls it). Do
// not nest one entry section inside another.
//
// Once a section has been seen, new data does not replay it: a new signature is
// only remembered, and the data change itself is shown as a change (phase
// 'update'): CountUp runs from the figure it shows to the new one, and marks under
// useTween() (tween.ts) move from their old size and place to the new one.
//
// Only transform/opacity/clip-path/stroke-dashoffset move, and only on marks
// inside a section, never on a [data-box] element, so no box moves.
import { useLayoutEffect, useState, type CSSProperties } from 'react'
import type { CustomRange } from '@/components/stats/range'
import './motion.css'

/** The app's motion ease (quick look, modals, menus, the library grid). */
export const EASE = 'cubic-bezier(0.2, 0.8, 0.2, 1)'
/** Durations in ms; mirrored as CSS variables in motion.css. */
export const DUR = { grow: 520, fade: 300, draw: 420, drawDelay: 220, count: 650 } as const
/** Marks start at most `max` ms apart in all, `step` ms apart at most per mark. */
export const STAGGER = { step: 24, max: 200 } as const
/** Longest entry: the last mark's delay plus the longest animation. */
const TOTAL = Math.max(DUR.grow + STAGGER.max, DUR.drawDelay + DUR.draw, DUR.count) + 80

/** Class names of the animated marks (see motion.css). */
export const K = { grow: 'k-grow', growX: 'k-grow-x', fade: 'k-fade', rise: 'k-rise', draw: 'k-draw', wipe: 'k-wipe' } as const

/** Delay of the i-th of n marks (an even spread capped at STAGGER.max), as the style the CSS reads. */
export function stagger(i: number, n: number, max: number = STAGGER.max): CSSProperties {
  const step = n > 1 ? Math.min(STAGGER.step, max / (n - 1)) : 0
  return { '--k-d': `${Math.round(i * step)}ms` } as CSSProperties
}

/** Signature of a ranged answer, for useEntry. */
export const rangeSig = (days: number, custom: CustomRange | null) => (custom ? `${custom.start}..${custom.end}` : `d${days}`)
/** Signature of a section that does not follow the range: plays once per session. */
export const ONCE = 'once'

export const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export type Phase = 'idle' | 'wait' | 'play' | 'update' | 'done'

// What each scope last played, for the session (module state: survives tab switches and remounts).
const played = new Map<string, string>()
const live = new Set<Entry>()

/** One section's entry. Stable for the section's life; `ref` goes on the section element. */
export class Entry {
  phase: Phase = 'idle'
  sig: string | null = null
  scope = ''
  private el: Element | null = null
  private subs = new Set<(p: Phase) => void>()
  private io: IntersectionObserver | null = null
  private timer = 0

  /** Callback ref for the section element. */
  ref = (el: Element | null) => {
    this.el = el
    if (el && this.phase !== 'idle') el.setAttribute('data-enter', this.phase)
  }

  /** Called by CountUp and similar: told every phase change, starting with the current one. */
  subscribe(fn: (p: Phase) => void) {
    this.subs.add(fn)
    return () => { this.subs.delete(fn) }
  }

  set(p: Phase) {
    const el = this.el
    if (el) {
      // Playing again while playing: drop the attribute and flush styles so the CSS animations restart.
      if (p === 'play' && el.getAttribute('data-enter') === 'play') {
        el.removeAttribute('data-enter')
        void (el as HTMLElement).getBoundingClientRect()
      }
      el.setAttribute('data-enter', p)
    }
    this.phase = p
    this.unobserve()
    window.clearTimeout(this.timer)
    if (p === 'play') {
      played.set(this.scope, this.sig ?? '')
      this.timer = window.setTimeout(() => this.set('done'), TOTAL)
    } else if (p === 'update') this.timer = window.setTimeout(() => this.set('done'), TOTAL)
    else if (p === 'wait') this.observe()
    for (const fn of this.subs) fn(p)
  }

  /** Decide for a new signature: done (reduced motion, or already played), play (on screen) or wait. */
  decide(scope: string, sig: string) {
    const seen = this.seen() && scope === this.scope
    this.scope = scope
    this.sig = sig
    // Seen already: the new data is shown as a change (update()), not a new entry.
    if (seen) { played.set(scope, sig); return }
    if (reducedMotion() || played.get(scope) === sig) return this.set('done')
    this.set(onScreen(this.el) ? 'play' : 'wait')
  }

  /** The section has been drawn with data in this mount (its entry played, or was skipped). */
  seen() { return this.phase === 'play' || this.phase === 'update' || this.phase === 'done' }

  /**
   * Called by what shows a data change (CountUp, useTween) when it starts one:
   * true (and the section in 'update' until the change is over) when it may run,
   * false before the section has been seen or under reduced motion (show the end at once).
   */
  update(): boolean {
    if (!this.seen() || reducedMotion()) return false
    if (this.phase !== 'update') this.set('update')
    else {
      window.clearTimeout(this.timer)
      this.timer = window.setTimeout(() => this.set('done'), TOTAL)
    }
    return true
  }

  finish() { if (this.phase === 'wait' || this.phase === 'play' || this.phase === 'update') this.set('done') }

  /** Re-arm after a remount of the effect (StrictMode runs effects twice). */
  resume() {
    if (this.phase === 'wait') this.observe()
    else if (this.phase === 'play' || this.phase === 'update') this.timer = window.setTimeout(() => this.set('done'), TOTAL)
  }

  stop() {
    this.unobserve()
    window.clearTimeout(this.timer)
  }

  private observe() {
    if (!this.el || typeof IntersectionObserver === 'undefined') return this.set('play')
    this.io = new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting)) this.set('play') }, { threshold: 0.15 })
    this.io.observe(this.el)
  }

  private unobserve() {
    this.io?.disconnect()
    this.io = null
  }
}

function onScreen(el: Element | null) {
  if (!el) return true
  const r = el.getBoundingClientRect()
  const vh = window.innerHeight
  // On screen when its top is above the fold with a little to see (or it is short and fully visible).
  return r.bottom > 0 && r.top < vh - Math.min(80, r.height * 0.15)
}

/**
 * The entry of one section. `scope` names the section for the session ("overview/hero");
 * `sig` is null while loading, then what the data answers (rangeSig(), or ONCE).
 */
export function useEntry(scope: string, sig: string | null): Entry {
  const [entry] = useState(() => new Entry())
  useLayoutEffect(() => {
    live.add(entry)
    entry.resume()
    return () => { live.delete(entry); entry.stop() }
  }, [entry])
  useLayoutEffect(() => {
    if (sig == null || (entry.sig === sig && entry.scope === scope)) return
    entry.decide(scope, sig)
  }, [scope, sig, entry])
  return entry
}

/**
 * useEntry plus the section's callback ref as a separate value, for a component
 * that also hands the entry to children (the hooks lint reads `entry.ref` used in
 * render as a ref object read once the entry is passed on as a prop).
 */
export function useEntryRef(scope: string, sig: string | null): [Entry, (el: Element | null) => void] {
  const entry = useEntry(scope, sig)
  const [attach] = useState(() => (el: Element | null) => entry.ref(el))
  return [entry, attach]
}

/** End every running or waiting entry at once (before an image export of the page). */
export function finishEntries() {
  for (const e of live) e.finish()
}
