// One-time "What's new" panel after a server upgrade. The server reports its
// version with the release-notes entries for it (GET /meta/whats-new, parsed
// from the changelog); we remember the last version this browser saw and show
// the panel once when it changes. Fresh browsers/installs store silently; a
// first login should not open with a modal.
import { useEffect, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { api } from '@/lib/api'
import { Trans } from '@lingui/react/macro'
import { ModalShell } from '@/components/ModalShell'
import { Pill } from '@/components/page'

interface WhatsNewEntry {
  kind: string
  title: string
  body: string
}
interface WhatsNewResponse {
  version: string
  entries: WhatsNewEntry[]
}

const SEEN_KEY = 'tome_seen_version'
// Once per SPA session is enough: remounts (route changes) must not refetch.
let checkedThisSession = false

// eslint-disable-next-line lingui/no-unlocalized-strings -- changelog section keys
const KIND_ORDER = ['Added', 'Changed', 'Fixed', 'Removed']

// Inline `code` spans from the changelog render as code, not literal backticks.
function renderInline(text: string): ReactNode {
  const parts = text.split(/`([^`]+)`/g)
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <code key={i} className="rounded bg-secondary px-1 py-0.5 font-mono text-[0.85em] text-foreground">
        {part}
      </code>
    ) : (
      <span key={i}>{part}</span>
    ),
  )
}

// Older changelog entries have no bold lead: pull "Thing: detail" apart so
// every row still gets a scannable title.
function splitEntry(e: WhatsNewEntry): { title: string; body: string } {
  if (e.title) return { title: e.title, body: e.body }
  const idx = e.body.indexOf(': ')
  if (idx > 0 && idx < 70) {
    return { title: e.body.slice(0, idx), body: e.body.slice(idx + 2) }
  }
  return { title: '', body: e.body }
}

export function WhatsNewPanel() {
  const [data, setData] = useState<WhatsNewResponse | null>(null)

  useEffect(() => {
    if (checkedThisSession) return
    checkedThisSession = true
    api
      .get<WhatsNewResponse>('/meta/whats-new')
      .then((d) => {
        if (!d.version || d.version === 'dev') return
        const seen = localStorage.getItem(SEEN_KEY)
        if (seen === null) {
          // First visit from this browser: nothing to announce, just baseline.
          localStorage.setItem(SEEN_KEY, d.version)
          return
        }
        if (seen !== d.version && d.entries.length > 0) setData(d)
      })
      .catch(() => {})
  }, [])

  if (!data) return null

  const dismiss = () => {
    localStorage.setItem(SEEN_KEY, data.version)
    setData(null)
  }

  const groups = KIND_ORDER.filter((k) => data.entries.some((e) => e.kind === k)).concat(
    [...new Set(data.entries.map((e) => e.kind))].filter((k) => !KIND_ORDER.includes(k)),
  )
  const version = data.version

  // Portal to <body>: the header that mounts this has backdrop-blur, which
  // makes it the containing block for fixed descendants, so the overlay would
  // center on the header bar instead of the viewport.
  return createPortal(
    <ModalShell
      open
      onClose={dismiss}
      eyebrow={<Trans>Just updated</Trans>}
      title={<Trans>What&apos;s new in Tome {version}</Trans>}
      size="md"
      footer={<Pill variant="primary" onClick={dismiss}><Trans>Got it</Trans></Pill>}
    >
      <div className="flex flex-col gap-6">
        {groups.map((kind) => (
          <section key={kind}>
            <h3 className="border-b border-border/70 pb-2 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
              {kind}
            </h3>
            <ul>
              {data.entries
                .filter((e) => e.kind === kind)
                .map((e, i) => {
                  const { title, body } = splitEntry(e)
                  return (
                    <li key={i} className="border-b border-border/70 py-3">
                      {title && (
                        <p className="text-[15px] font-semibold leading-snug text-foreground">{renderInline(title)}</p>
                      )}
                      <p className={title ? 'mt-0.5 text-[14px] leading-relaxed text-muted-foreground' : 'text-[14px] leading-relaxed text-foreground/85'}>
                        {renderInline(body)}
                      </p>
                    </li>
                  )
                })}
            </ul>
          </section>
        ))}
      </div>
    </ModalShell>,
    document.body,
  )
}
