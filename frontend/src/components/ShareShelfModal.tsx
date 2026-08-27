// Manage the public share link for one shelf, series, or book: create, copy,
// revoke. What a share exposes (and pointedly does not) is spelled out in the
// modal so nobody is surprised: metadata + your rating and highlights — never
// files. The `endpoint` prop is the management URL (e.g. /shelves/3/share).
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, Copy, Link2, Loader2, Share2, Trash2, X } from 'lucide-react'
import { api } from '@/lib/api'
import { ModalShell } from '@/components/ModalShell'
import { copyToClipboard } from '@/lib/utils'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

export function ShareModal({ title, endpoint, noun = 'shelf', onClose }: {
  title: string
  endpoint: string
  noun?: 'shelf' | 'series' | 'book'
  onClose: () => void
}) {
  const [token, setToken] = useState<string | null | undefined>(undefined) // undefined = loading
  const [expiresAt, setExpiresAt] = useState<string | null>(null)
  const [expiryDays, setExpiryDays] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    api.get<{ token: string | null; expires_at: string | null }>(endpoint)
      .then(r => { setToken(r.token); setExpiresAt(r.expires_at) })
      .catch(() => setToken(null))
  }, [endpoint])

  const url = token ? `${window.location.origin}/share/${token}` : null

  async function create() {
    setBusy(true)
    try {
      const r = await api.post<{ token: string; expires_at: string | null }>(
        endpoint, expiryDays ? { expires_in_days: expiryDays } : {},
      )
      setToken(r.token)
      setExpiresAt(r.expires_at)
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

  async function copy() {
    if (!url) return
    if (await copyToClipboard(url)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } else {
      // Last resort: select the URL so a manual Cmd/Ctrl+C works.
      const el = document.querySelector('[data-share-url]')
      if (el) {
        const range = document.createRange()
        range.selectNodeContents(el)
        const sel = window.getSelection()
        sel?.removeAllRanges()
        sel?.addRange(range)
      }
    }
  }

  return createPortal(
    <ModalShell open onClose={onClose} className="w-full max-w-md">
        <div className="rounded-xl border border-border bg-card shadow-xl">
          <div className="flex items-center gap-2 border-b border-border px-5 py-3.5">
            <Share2 className="h-4 w-4 text-primary" />
            <h2 className="font-display text-base text-foreground"><Trans>Share &quot;{title}&quot;</Trans></h2>
            <button
              onClick={onClose}
              aria-label={t`Close`}
              className="ml-auto rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="px-5 py-4">
            <p className="text-xs leading-relaxed text-muted-foreground">
              {noun === 'book'
                ? <Trans>A share link is a read-only page anyone with the link can open — no account needed. It shows covers, titles, tags, descriptions, and your ratings and highlights for this book. It never exposes files: no downloads, no reading, no way in. Revoke it any time and the link dies instantly.</Trans>
                : noun === 'series'
                ? <Trans>A share link is a read-only page anyone with the link can open — no account needed. It shows covers, titles, tags, descriptions, and your ratings and highlights for the books in this series. It never exposes files: no downloads, no reading, no way in. Revoke it any time and the link dies instantly.</Trans>
                : <Trans>A share link is a read-only page anyone with the link can open — no account needed. It shows covers, titles, tags, descriptions, and your ratings and highlights for the books on this shelf. It never exposes files: no downloads, no reading, no way in. Revoke it any time and the link dies instantly.</Trans>}
            </p>

            {token === undefined ? (
              <div className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> <Trans>Loading…</Trans>
              </div>
            ) : token === null ? (
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <div className="flex items-center gap-1 rounded-lg bg-muted p-0.5">
                  {[
                    { d: null, label: t`Never expires` },
                    { d: 1, label: t`1 day` },
                    { d: 7, label: t`7 days` },
                    { d: 30, label: t`30 days` },
                  ].map(o => (
                    <button
                      key={String(o.d)}
                      onClick={() => setExpiryDays(o.d)}
                      className={
                        'rounded-md px-2 py-1 text-xs transition ' +
                        (expiryDays === o.d
                          ? 'bg-card font-medium text-foreground shadow-sm'
                          : 'text-muted-foreground hover:text-foreground')
                      }
                    >
                      {o.label}
                    </button>
                  ))}
                </div>
                <button
                  onClick={create}
                  disabled={busy}
                  className="flex items-center gap-2 rounded-lg bg-primary px-3.5 py-2 text-xs font-semibold text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-50"
                >
                  {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}
                  <Trans>Create share link</Trans>
                </button>
              </div>
            ) : (
              <div className="mt-4 flex flex-col gap-2.5">
                <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/50 px-3 py-2">
                  <code data-share-url className="min-w-0 flex-1 truncate text-xs text-foreground">{url}</code>
                  <button
                    onClick={copy}
                    title={t`Copy link`}
                    className="shrink-0 rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                  >
                    {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
                  </button>
                </div>
                <div className="flex items-center gap-3">
                  <button
                    onClick={revoke}
                    disabled={busy}
                    className="flex w-fit items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-destructive disabled:opacity-50"
                  >
                    {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                    <Trans>Revoke link</Trans>
                  </button>
                  <span className="text-[11px] text-muted-foreground">
                    {expiresAt
                      ? (() => { const d = new Date(expiresAt).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }); return t`Expires ${d}` })()
                      : t`Never expires`}
                    {t` · to change expiry, revoke and re-create`}
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
    </ModalShell>,
    document.body,
  )
}
