// Manage the public share link for one shelf, series, or book: create, copy,
// revoke. What a share exposes (and pointedly does not) is spelled out in the
// modal so nobody is surprised: metadata + your rating and highlights — never
// files. The `endpoint` prop is the management URL (e.g. /shelves/3/share).
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, Copy, Link2, Loader2, Share2, Trash2, X } from 'lucide-react'
import { api } from '@/lib/api'

export function ShareModal({ title, endpoint, noun = 'shelf', onClose }: {
  title: string
  endpoint: string
  noun?: 'shelf' | 'series' | 'book'
  onClose: () => void
}) {
  const [token, setToken] = useState<string | null | undefined>(undefined) // undefined = loading
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    api.get<{ token: string | null }>(endpoint)
      .then(r => setToken(r.token))
      .catch(() => setToken(null))
  }, [endpoint])

  const url = token ? `${window.location.origin}/share/${token}` : null

  async function create() {
    setBusy(true)
    try {
      const r = await api.post<{ token: string }>(endpoint)
      setToken(r.token)
    } finally {
      setBusy(false)
    }
  }

  async function revoke() {
    setBusy(true)
    try {
      await api.delete(endpoint)
      setToken(null)
    } finally {
      setBusy(false)
    }
  }

  function copy() {
    if (!url) return
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return createPortal(
    <>
      <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="pointer-events-auto w-full max-w-md rounded-xl border border-border bg-card shadow-xl">
          <div className="flex items-center gap-2 border-b border-border px-5 py-3.5">
            <Share2 className="h-4 w-4 text-primary" />
            <h2 className="font-display text-base text-foreground">Share &quot;{title}&quot;</h2>
            <button
              onClick={onClose}
              aria-label="Close"
              className="ml-auto rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="px-5 py-4">
            <p className="text-xs leading-relaxed text-muted-foreground">
              A share link is a read-only page anyone with the link can open — no account
              needed. It shows covers, titles, tags, descriptions, and your ratings and
              highlights for {noun === 'book' ? 'this book' : noun === 'series' ? 'the books in this series' : 'the books on this shelf'}.
              It never exposes files: no downloads, no reading, no way in. Revoke it any
              time and the link dies instantly.
            </p>

            {token === undefined ? (
              <div className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading…
              </div>
            ) : token === null ? (
              <button
                onClick={create}
                disabled={busy}
                className="mt-4 flex items-center gap-2 rounded-lg bg-primary px-3.5 py-2 text-xs font-semibold text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-50"
              >
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}
                Create share link
              </button>
            ) : (
              <div className="mt-4 flex flex-col gap-2.5">
                <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/50 px-3 py-2">
                  <code className="min-w-0 flex-1 truncate text-xs text-foreground">{url}</code>
                  <button
                    onClick={copy}
                    title="Copy link"
                    className="shrink-0 rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                  >
                    {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
                  </button>
                </div>
                <button
                  onClick={revoke}
                  disabled={busy}
                  className="flex w-fit items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-destructive disabled:opacity-50"
                >
                  {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                  Revoke link
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
