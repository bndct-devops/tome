// Remembered shape of the goal blocks. A goal card's lines wrap where its words
// do (numbers never truncate), so its height is only known once drawn. Every
// measured block (a row of cards, a detail's chart row, its figures) writes its
// height to this browser's storage under a key that says where and how wide it
// was drawn; a second visit's skeleton reserves exactly that. With nothing
// remembered (a browser's first visit) a page draws the kit's Pending placeholder
// instead and nothing below it, so what is drawn never moves.
import { useLayoutEffect, useRef, useState } from 'react'

const PREFIX = 'tome_stats_goal_shape:'

function load(key: string): Record<string, number> | null {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFIX + key) ?? 'null') as unknown
    if (!raw || typeof raw !== 'object') return null
    const out: Record<string, number> = {}
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) if (typeof v === 'number' && v > 0 && v < 2000) out[k] = v
    return out
  } catch {
    return null
  }
}

/** The heights remembered under `key` (null: none), read once per mount. */
export function useShape(key: string) {
  const [saved] = useState(() => load(key))
  const els = useRef(new Map<string, HTMLElement>())
  const refs = useRef(new Map<string, (el: HTMLElement | null) => void>())
  // After every drawn render, write what the measured blocks are now (only when it changed).
  useLayoutEffect(() => {
    if (!els.current.size) return
    const now: Record<string, number> = {}
    for (const [name, el] of els.current) now[name] = Math.round(el.getBoundingClientRect().height)
    const prev = load(key)
    if (prev && Object.keys(now).every((k) => prev[k] === now[k]) && Object.keys(prev).length === Object.keys(now).length) return
    try {
      localStorage.setItem(PREFIX + key, JSON.stringify(now))
    } catch {
      // storage unavailable: the next visit waits for the answer
    }
  })
  return {
    /** The remembered height of block `name`, if any. */
    h: (name: string): number | undefined => saved?.[name],
    /** Whether every one of `names` is remembered. */
    has: (names: string[]) => !!saved && names.every((n) => saved[n] != null),
    /** Ref for a loaded block whose height is to be remembered. */
    measure: (name: string) => {
      let f = refs.current.get(name)
      if (!f) {
        f = (el: HTMLElement | null) => { if (el) els.current.set(name, el); else els.current.delete(name) }
        refs.current.set(name, f)
      }
      return f
    },
  }
}

/** Whether any shape was ever remembered under `key` (without mounting the hook). */
export function shapeKnown(key: string, names: string[]): boolean {
  const s = load(key)
  return !!s && names.every((n) => s[n] != null)
}
