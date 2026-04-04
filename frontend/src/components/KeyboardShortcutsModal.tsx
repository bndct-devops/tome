import { useEffect } from 'react'
import { Keyboard, X } from 'lucide-react'

interface Props {
  open: boolean
  onClose: () => void
}

interface ShortcutRow {
  keys: string[]
  description: string
}

interface ShortcutSection {
  title: string
  rows: ShortcutRow[]
}

const SECTIONS: ShortcutSection[] = [
  {
    title: 'Dashboard',
    rows: [
      { keys: ['/'], description: 'Focus search' },
      { keys: ['j', 'ArrowDown'], description: 'Next book' },
      { keys: ['k', 'ArrowUp'], description: 'Previous book' },
      { keys: ['Enter'], description: 'Open selected book' },
      { keys: ['Escape'], description: 'Clear selection / blur search' },
      { keys: ['?'], description: 'Show this help' },
    ],
  },
  {
    title: 'Book Detail',
    rows: [
      { keys: ['Escape'], description: 'Go back' },
      { keys: ['r'], description: 'Open reader' },
      { keys: ['e'], description: 'Toggle metadata edit' },
    ],
  },
  {
    title: 'Reader',
    rows: [
      { keys: ['ArrowLeft', 'ArrowUp'], description: 'Previous page' },
      { keys: ['ArrowRight', 'ArrowDown'], description: 'Next page' },
    ],
  },
]

function KeyBadge({ label }: { label: string }) {
  const display =
    label === 'ArrowLeft' ? '\u2190'
    : label === 'ArrowRight' ? '\u2192'
    : label === 'ArrowUp' ? '\u2191'
    : label === 'ArrowDown' ? '\u2193'
    : label === 'Escape' ? 'Esc'
    : label === 'Enter' ? 'Enter'
    : label
  return (
    <kbd className="inline-flex items-center justify-center min-w-[1.75rem] h-7 px-1.5 rounded-md border border-border bg-muted text-xs font-mono font-semibold text-foreground shadow-sm">
      {display}
    </kbd>
  )
}

export function KeyboardShortcutsModal({ open, onClose }: Props) {
  useEffect(() => {
    if (!open) return
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* Panel */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="pointer-events-auto w-full max-w-md bg-card border border-border rounded-2xl shadow-2xl overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-border">
            <div className="flex items-center gap-2.5">
              <Keyboard className="w-4 h-4 text-primary" />
              <h2 className="text-sm font-semibold text-foreground">Keyboard Shortcuts</h2>
            </div>
            <button
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground transition-colors rounded-md p-0.5 hover:bg-muted"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Body */}
          <div className="px-5 py-4 flex flex-col gap-5 max-h-[70vh] overflow-y-auto">
            {SECTIONS.map(section => (
              <div key={section.title}>
                <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground mb-2.5">
                  {section.title}
                </p>
                <div className="flex flex-col gap-1.5">
                  {section.rows.map(row => (
                    <div key={row.description} className="flex items-center justify-between gap-3">
                      <span className="text-sm text-foreground">{row.description}</span>
                      <div className="flex items-center gap-1 shrink-0">
                        {row.keys.map((k, i) => (
                          <span key={k} className="flex items-center gap-1">
                            {i > 0 && <span className="text-xs text-muted-foreground">/</span>}
                            <KeyBadge label={k} />
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}
