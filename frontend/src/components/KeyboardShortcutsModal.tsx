import { useEffect } from 'react'
import { Keyboard, X } from 'lucide-react'
import { Trans, useLingui } from '@lingui/react/macro'
import { msg } from '@lingui/core/macro'
import type { MessageDescriptor } from '@lingui/core'

interface Props {
  open: boolean
  onClose: () => void
}

interface ShortcutRow {
  keys: string[]
  description: MessageDescriptor
}

interface ShortcutSection {
  title: MessageDescriptor
  rows: ShortcutRow[]
}

const SECTIONS: ShortcutSection[] = [
  {
    title: msg`Everywhere`,
    rows: [
      { keys: ['⌘', 'K'], description: msg`Command palette — jump to a book, series, author, or page` },
    ],
  },
  {
    title: msg`Dashboard`,
    rows: [
      { keys: ['/'], description: msg`Focus search` },
      { keys: ['j', 'ArrowDown'], description: msg`Next book` },
      { keys: ['k', 'ArrowUp'], description: msg`Previous book` },
      { keys: ['Enter'], description: msg`Open selected book` },
      { keys: ['Escape'], description: msg`Clear selection / blur search` },
      { keys: ['?'], description: msg`Show this help` },
    ],
  },
  {
    title: msg`Book Detail`,
    rows: [
      { keys: ['Escape'], description: msg`Go back` },
      { keys: ['r'], description: msg`Open reader` },
      { keys: ['e'], description: msg`Toggle metadata edit` },
    ],
  },
  {
    title: msg`Reader`,
    rows: [
      { keys: ['ArrowLeft', 'ArrowUp'], description: msg`Previous page` },
      { keys: ['ArrowRight', 'ArrowDown'], description: msg`Next page` },
    ],
  },
  {
    title: msg`Highlights`,
    rows: [
      { keys: ['/'], description: msg`Focus search` },
      { keys: ['Escape'], description: msg`Clear search` },
      { keys: ['c'], description: msg`Collapse / expand all books` },
      { keys: ['n'], description: msg`Toggle only-notes filter` },
      { keys: ['e'], description: msg`Download Markdown export` },
    ],
  },
]

function KeyBadge({ label }: { label: string }) {
  /* eslint-disable lingui/no-unlocalized-strings -- key-cap glyphs */
  const display =
    label === 'ArrowLeft' ? '\u2190'
    : label === 'ArrowRight' ? '\u2192'
    : label === 'ArrowUp' ? '\u2191'
    : label === 'ArrowDown' ? '\u2193'
    : label === 'Escape' ? 'Esc'
    : label === 'Enter' ? 'Enter'
    : label
  /* eslint-enable lingui/no-unlocalized-strings */
  return (
    <kbd className="inline-flex items-center justify-center min-w-[1.75rem] h-7 px-1.5 rounded-md border border-border bg-muted text-xs font-mono font-semibold text-foreground shadow-sm">
      {display}
    </kbd>
  )
}

export function KeyboardShortcutsModal({ open, onClose }: Props) {
  const { i18n } = useLingui()
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
              <h2 className="text-sm font-semibold text-foreground"><Trans>Keyboard Shortcuts</Trans></h2>
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
            {SECTIONS.map((section, si) => (
              <div key={si}>
                <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground mb-2.5">
                  {i18n._(section.title)}
                </p>
                <div className="flex flex-col gap-1.5">
                  {section.rows.map((row, ri) => (
                    <div key={ri} className="flex items-center justify-between gap-3">
                      <span className="text-sm text-foreground">{i18n._(row.description)}</span>
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
