import { ModalShell } from '@/components/ModalShell'
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
      { keys: ['⌘K'], description: msg`Command palette: jump to a book, series, author, or page` },
    ],
  },
  {
    title: msg`Library`,
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
    title: msg`Book detail`,
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
    title: msg`Calendar`,
    rows: [
      { keys: ['ArrowLeft', 'ArrowRight'], description: msg`Previous / next day` },
      { keys: ['Shift', 'ArrowLeft'], description: msg`Previous month (Shift with either arrow)` },
      { keys: ['t'], description: msg`Jump to today` },
      { keys: ['Escape'], description: msg`Close the day` },
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
    <kbd className="inline-flex items-center justify-center min-w-[1.9rem] h-7 px-2 rounded-md bg-secondary text-[12px] font-semibold text-foreground tabular-nums">
      {display}
    </kbd>
  )
}

export function KeyboardShortcutsModal({ open, onClose }: Props) {
  const { i18n } = useLingui()

  return (
    <ModalShell open={open} onClose={onClose} size="md" title={<Trans>Keyboard shortcuts</Trans>}>
      <div className="flex flex-col gap-7 pt-2">
        {SECTIONS.map((section, si) => (
          <div key={si}>
            <p className="text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground mb-1">
              {i18n._(section.title)}
            </p>
            <div className="divide-y divide-border/70">
              {section.rows.map((row, ri) => (
                <div key={ri} className="flex items-center justify-between gap-4 py-2.5">
                  <span className="text-[15px] text-foreground">{i18n._(row.description)}</span>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {row.keys.map((k, i) => (
                      <span key={k} className="flex items-center gap-1.5">
                        {i > 0 && <span className="text-[12px] text-muted-foreground">/</span>}
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
    </ModalShell>
  )
}
