import { useState } from 'react'

/** The last non-empty value: a panel that folds or fades away keeps showing
 *  what it showed instead of emptying mid-exit. */
export function useHeld<T>(value: T | null | undefined | false): T | null {
  const [held, setHeld] = useState<T | null>(value || null)
  if (value && value !== held) setHeld(value)
  return value ? value : held
}
