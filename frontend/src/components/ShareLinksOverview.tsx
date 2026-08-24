// Settings → Share links: every public link you've minted, in one place —
// what it points at, when it expires, copy and revoke.
import { useEffect, useState } from 'react'
import { Book, Bookmark, Check, Copy, Layers, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { cn, copyToClipboard } from '@/lib/utils'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

interface ShareLinkRow {
  id: number
  kind: 'shelf' | 'series' | 'book'
  title: string
  token: string
  created_at: string
  expires_at: string | null
  expired: boolean
}

const KIND_ICON = { shelf: Bookmark, series: Layers, book: Book } as const

export function ShareLinksOverview() {
  const [rows, setRows] = useState<ShareLinkRow[] | null>(null)
  const [copiedId, setCopiedId] = useState<number | null>(null)

  const load = () => {
    api.get<ShareLinkRow[]>('/share-links').then(setRows).catch(() => setRows([]))
  }
  useEffect(load, [])

  async function copy(row: ShareLinkRow) {
    if (await copyToClipboard(`${window.location.origin}/share/${row.token}`)) {
      setCopiedId(row.id)
      setTimeout(() => setCopiedId(null), 1500)
    }
  }

  function fmtExpiry(row: ShareLinkRow): string {
    if (row.expired) return t`expired`
    if (!row.expires_at) return t`never expires`
    { const d = new Date(row.expires_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }); return t`expires ${d}` }
  }

  return (
    <div className="mt-3 rounded-xl border border-border/60 bg-card/50 p-5">
      <p className="mb-4 text-xs text-muted-foreground">
        <Trans>Every public link you&apos;ve shared — shelves, series, and books. Links show
        metadata, your ratings, highlights, and reading stats; never files. Revoking
        kills a link instantly.</Trans>
      </p>
      {rows === null ? (
        <p className="animate-pulse text-xs text-muted-foreground"><Trans>Loading…</Trans></p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          <Trans>Nothing shared yet. Use the share icon on a shelf, a series page, or a book page.</Trans>
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map(row => {
            const Icon = KIND_ICON[row.kind]
            return (
              <li
                key={row.id}
                className={cn(
                  'flex items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-2',
                  row.expired && 'opacity-60',
                )}
              >
                <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-foreground">{row.title}</span>
                  <span className="block text-[11px] text-muted-foreground">
                    {row.kind} · {fmtExpiry(row)}
                  </span>
                </span>
                {!row.expired && (
                  <a
                    href={`/share/${row.token}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0 text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                  >
                    <Trans>Open</Trans>
                  </a>
                )}
                <button
                  onClick={() => copy(row)}
                  title={t`Copy link`}
                  className="shrink-0 rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                >
                  {copiedId === row.id ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
                </button>
                <button
                  onClick={() => api.delete(`/share-links/${row.id}`).then(load)}
                  title={t`Revoke link`}
                  className="shrink-0 rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:text-destructive"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
