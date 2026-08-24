import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Info } from 'lucide-react'
import { useLingui } from '@lingui/react/macro'

// A small "i" that explains a chart or control on hover or tap. Tap-toggle so
// it works on touch (native title tooltips don't). Reserved for the non-obvious.
// Pass `text` for a plain sentence, or `children` for structured content (a
// legend, a definition list); `wide` widens the popover for the latter.
export function InfoHint({ text, children, wide }: { text?: string; children?: ReactNode; wide?: boolean }) {
  const { t } = useLingui()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLSpanElement>(null)
  // iOS Safari doesn't focus buttons on tap, so onBlur alone never closes the
  // popover there — close on any tap outside instead.
  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])
  return (
    <span ref={rootRef} className="relative inline-flex leading-none">
      <button
        type="button"
        aria-label={t`What is this?`}
        onClick={() => setOpen(o => !o)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onBlur={() => setOpen(false)}
        className="text-muted-foreground/40 hover:text-muted-foreground transition-colors"
      >
        <Info className="w-3 h-3" />
      </button>
      {open && (
        <span
          role="tooltip"
          className={`absolute left-0 top-5 z-20 ${wide ? 'w-80' : 'w-52'} rounded-md border border-border bg-background px-2.5 py-1.5 text-xs leading-snug text-muted-foreground shadow-lg`}
        >
          {children ?? text}
        </span>
      )}
    </span>
  )
}
