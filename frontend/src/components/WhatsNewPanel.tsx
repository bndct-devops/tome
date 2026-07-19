// One-time "What's new" panel after a server upgrade. The server reports its
// version with the release-notes entries for it (GET /meta/whats-new, parsed
// from the changelog); we remember the last version this browser saw and show
// the panel once when it changes. Fresh browsers/installs store silently — a
// first login should not open with a modal.
import { useEffect, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Minus, Plus, RefreshCw, Sparkles, Wrench, X } from 'lucide-react'
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

// Per-kind visual identity — an icon chip per entry beats a wall of gray text.
const KIND_META: Record<string, { icon: typeof Plus; chip: string }> = {
  Added: { icon: Plus, chip: 'bg-success/15 text-success' },
  Changed: { icon: RefreshCw, chip: 'bg-primary/15 text-primary' },
  Fixed: { icon: Wrench, chip: 'bg-warning/15 text-warning' },
  Removed: { icon: Minus, chip: 'bg-destructive/15 text-destructive' },
}
const KIND_FALLBACK = { icon: Sparkles, chip: 'bg-muted text-muted-foreground' }

// Inline `code` spans from the changelog render as code, not literal backticks.
function renderInline(text: string): ReactNode {
  const parts = text.split(/`([^`]+)`/g)
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <code key={i} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] text-foreground">
        {part}
      </code>
    ) : (
      <span key={i}>{part}</span>
    ),
  )
}

// Older changelog entries have no bold lead — pull "Thing: detail" apart so
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

  // Portal to <body>: the header that mounts this has backdrop-blur, which
  // makes it the containing block for fixed descendants — the overlay would
  // center on the header bar instead of the viewport.
  return createPortal(
    <>
      <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" onClick={dismiss} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="pointer-events-auto flex max-h-[80vh] w-full max-w-lg flex-col rounded-xl border border-border bg-card shadow-xl">
          <div className="relative shrink-0 overflow-hidden border-b border-border px-5 pb-4 pt-5">
            {/* soft accent wash so the panel reads as an occasion, not a dialog */}
            <div
              className="pointer-events-none absolute inset-0"
              style={{ background: 'radial-gradient(120% 160% at 0% 0%, color-mix(in oklab, var(--primary) 14%, transparent), transparent 55%)' }}
            />
            <div className="relative flex items-start gap-3">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-primary/15">
                <Sparkles className="h-4.5 w-4.5 text-primary" />
              </span>
              <div className="min-w-0">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Just updated
                </p>
                <h2 className="font-display text-xl leading-tight text-foreground">
                  What&apos;s new in Tome {data.version}
                </h2>
              </div>
              <button
                onClick={dismiss}
                aria-label="Dismiss"
                className="ml-auto rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="min-h-0 overflow-y-auto overscroll-contain px-5 py-4">
            {groups.map((kind) => {
              const meta = KIND_META[kind] ?? KIND_FALLBACK
              const Icon = meta.icon
              return (
                <div key={kind} className="mb-5 last:mb-0">
                  <h3 className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {kind}
                    <span className="h-px flex-1 bg-border/60" />
                  </h3>
                  <ul className="flex flex-col gap-2">
                    {data.entries
                      .filter((e) => e.kind === kind)
                      .map((e, i) => {
                        const { title, body } = splitEntry(e)
                        return (
                          <li
                            key={i}
                            className="flex items-start gap-3 rounded-lg border border-border/60 bg-muted/30 px-3 py-2.5"
                          >
                            <span className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md ${meta.chip}`}>
                              <Icon className="h-3.5 w-3.5" />
                            </span>
                            <p className="min-w-0 text-sm leading-relaxed text-muted-foreground">
                              {title && (
                                <span className="font-semibold text-foreground">{renderInline(title)} </span>
                              )}
                              {renderInline(body)}
                            </p>
                          </li>
                        )
                      })}
                  </ul>
                </div>
              )
            })}
          </div>
          <div className="shrink-0 border-t border-border px-5 py-3 text-right">
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
