import { useRef } from 'react'

export function useShiftSelect<K>(orderedKeys: K[]) {
  const anchorRef = useRef<number | null>(null)

  function handleToggle(key: K, index: number, shiftKey: boolean, selected: Set<K>): Set<K> {
    const next = new Set(selected)

    if (shiftKey && anchorRef.current !== null) {
      const lo = Math.min(anchorRef.current, index)
      const hi = Math.max(anchorRef.current, index)
      for (let i = lo; i <= hi; i++) {
        next.add(orderedKeys[i])
      }
    } else {
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      anchorRef.current = index
    }

    return next
  }

  return { handleToggle }
}
