// One-time "What's new" panel after a server upgrade. The server reports its
// version with the release-notes entries for it (GET /meta/whats-new, parsed
// from the changelog); we remember the last version this browser saw and show
// the panel once when it changes. Fresh browsers/installs store silently — a
// first login should not open with a modal.
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Sparkles, X } from 'lucide-react'
import { api } from '@/lib/api'

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
// Once per SPA session is enough — remounts (route changes) must not refetch.
let checkedThisSession = false

const KIND_ORDER = ['Added', 'Changed', 'Fixed', 'Removed']

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

  // Portal to <body>: the header that mounts this has backdrop-blur, which
  // makes it the containing block for fixed descendants — the overlay would
  // center on the header bar instead of the viewport.
  return createPortal(
    <>
      <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" onClick={dismiss} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="pointer-events-auto flex max-h-[80vh] w-full max-w-lg flex-col rounded-xl border border-border bg-card shadow-xl">
          <div className="flex items-center gap-2 border-b border-border px-5 py-3.5">
            <Sparkles className="h-4 w-4 text-primary" />
            <h2 className="font-display text-base text-foreground">
              What&apos;s new in Tome {data.version}
            </h2>
            <button
              onClick={dismiss}
              aria-label="Dismiss"
              className="ml-auto rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="min-h-0 overflow-y-auto overscroll-contain px-5 py-4">
            {groups.map((kind) => (
              <div key={kind} className="mb-4 last:mb-0">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {kind}
                </h3>
                <ul className="flex flex-col gap-2.5">
                  {data.entries
                    .filter((e) => e.kind === kind)
                    .map((e, i) => (
                      <li key={i} className="text-sm leading-relaxed">
                        {e.title && <span className="font-semibold text-foreground">{e.title} </span>}
                        <span className="text-muted-foreground">{e.body}</span>
                      </li>
                    ))}
                </ul>
              </div>
            ))}
          </div>
          <div className="border-t border-border px-5 py-3 text-right">
            <button
              onClick={dismiss}
              className="rounded-lg bg-primary px-3.5 py-1.5 text-xs font-semibold text-primary-foreground transition-all hover:bg-primary/90"
            >
              Got it
            </button>
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
