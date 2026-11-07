// Manage the public share link for one shelf, series, or book: create, copy,
// revoke. What a share exposes (and pointedly does not) is spelled out in the
// modal so nobody is surprised: metadata + your rating and highlights, never
// files. The `endpoint` prop is the management URL (e.g. /shelves/3/share).
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, Copy, Link2, Loader2, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { ModalShell } from '@/components/ModalShell'
import { FormLabel, PillGroup, Pill } from '@/components/page'
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

  const expiryOptions = [
    { value: 'never', label: t`Never expires` },
    { value: '1', label: t`1 day` },
    { value: '7', label: t`7 days` },
    { value: '30', label: t`30 days` },
  ]

  const footer = token === undefined ? (
    <Pill variant="quiet" onClick={onClose}><Trans>Cancel</Trans></Pill>
  ) : token === null ? (
    <>
      <Pill variant="quiet" onClick={onClose}><Trans>Cancel</Trans></Pill>
      <Pill variant="primary" onClick={create} disabled={busy}>
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
        <Trans>Create share link</Trans>
      </Pill>
    </>
  ) : (
    <>
      <Pill variant="destructive" onClick={revoke} disabled={busy} className="mr-auto -ml-3">
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
        <Trans>Revoke link</Trans>
      </Pill>
      <Pill variant="quiet" onClick={onClose}><Trans>Close</Trans></Pill>
      <Pill variant="primary" onClick={copy}>
        {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
        {copied ? <Trans>Copied</Trans> : <Trans>Copy link</Trans>}
      </Pill>
    </>
  )

  return createPortal(
    <ModalShell open onClose={onClose} title={<Trans>Share link</Trans>} eyebrow={title} size="sm" footer={footer}>
      <p className="text-[14px] leading-relaxed text-muted-foreground">
        {noun === 'book'
          ? <Trans>A share link is a read-only page anyone with the link can open, no account needed. It shows covers, titles, tags, descriptions, and your ratings and highlights for this book. It never exposes files: no downloads, no reading, no way in. Revoke it any time and the link stops working at once.</Trans>
          : noun === 'series'
          ? <Trans>A share link is a read-only page anyone with the link can open, no account needed. It shows covers, titles, tags, descriptions, and your ratings and highlights for the books in this series. It never exposes files: no downloads, no reading, no way in. Revoke it any time and the link stops working at once.</Trans>
          : <Trans>A share link is a read-only page anyone with the link can open, no account needed. It shows covers, titles, tags, descriptions, and your ratings and highlights for the books on this shelf. It never exposes files: no downloads, no reading, no way in. Revoke it any time and the link stops working at once.</Trans>}
      </p>

      {token === undefined ? (
        <div className="flex items-center justify-center gap-2 py-6 text-[14px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> <Trans>Loading…</Trans>
        </div>
      ) : token === null ? (
        <div className="mt-5">
          <FormLabel><Trans>Link expires</Trans></FormLabel>
          <PillGroup
            size="sm"
            value={expiryDays == null ? 'never' : String(expiryDays)}
            onChange={v => setExpiryDays(v === 'never' ? null : Number(v))}
            options={expiryOptions}
            aria-label={t`Link expires`}
          />
        </div>
      ) : (
        <div className="mt-5 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <code data-share-url className="flex h-9 min-w-0 flex-1 items-center truncate rounded-lg bg-secondary px-3 font-mono text-[13px] text-foreground">{url}</code>
            <Pill icon onClick={copy} title={t`Copy link`} aria-label={t`Copy link`} className="shrink-0">
              {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
            </Pill>
          </div>
          <p className="text-[13px] text-muted-foreground">
            {expiresAt
              ? (() => { const d = new Date(expiresAt).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }); return t`Expires ${d}` })()
              : t`Never expires`}
            {t` · to change expiry, revoke and re-create`}
          </p>
        </div>
      )}
    </ModalShell>,
    document.body,
  )
}
