// Position history + restore: the recovery UI for a bad sync. Lists the
// recent position log for one book (GET /books/{id}/position-history) and
// restores any entry as the live position, explicitly un-finishing the book
// when recovering from a false 100%.
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Loader2, RotateCcw } from 'lucide-react'
import { api } from '@/lib/api'
import { ModalShell } from '@/components/ModalShell'
import { Pill } from '@/components/page'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

interface HistoryEntry {
  id: number
  percentage: number
  device: string | null
  created_at: string
}

interface HistoryResponse {
  current: { percentage: number; device: string | null; updated_at: string } | null
  history: HistoryEntry[]
}

function fmtWhen(iso: string): string {
  const d = new Date(iso)
  const diff = Date.now() - d.getTime()
  if (diff < 3600_000) { const m = Math.max(1, Math.floor(diff / 60_000)); return t`${m} min ago` }
  if (diff < 86400_000) { const h = Math.floor(diff / 3600_000); return t`${h}h ago` }
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) +
    ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

// The same device words the book page's session list uses.
function deviceLabel(device: string | null): string {
  if (!device) return t`Unknown device`
  if (device === 'web' || device === 'web-reader') return t`Web reader`
  if (device === 'manual') return t`Manual`
  // eslint-disable-next-line lingui/no-unlocalized-strings -- product name
  if (device === 'koreader') return 'KOReader'
  return device
}

export function PositionHistoryModal({ bookId, onClose, onRestored }: {
  bookId: number
  onClose: () => void
  onRestored: () => void
}) {
  const [data, setData] = useState<HistoryResponse | null>(null)
  const [restoring, setRestoring] = useState<number | null>(null)

  const load = () => {
    api.get<HistoryResponse>(`/books/${bookId}/position-history`).then(setData).catch(() => {})
  }
  useEffect(load, [bookId])

  async function restore(entry: HistoryEntry) {
    if (restoring != null) return
    setRestoring(entry.id)
    try {
      await api.post(`/books/${bookId}/position-history/${entry.id}/restore`)
      load()
      onRestored()
    } finally {
      setRestoring(null)
    }
  }

  return createPortal(
    <ModalShell
      open
      onClose={onClose}
      title={<Trans>Position history</Trans>}
      size="sm"
      footer={<Pill variant="quiet" onClick={onClose}><Trans>Close</Trans></Pill>}
    >
      {!data ? (
        <div className="flex items-center justify-center gap-2 py-8 text-[14px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> <Trans>Loading…</Trans>
        </div>
      ) : data.history.length === 0 ? (
        <p className="py-6 text-center text-[14px] text-muted-foreground">
          <Trans>No position changes recorded yet. History starts with the next sync.</Trans>
        </p>
      ) : (
        <ul className="border-t border-border/70">
          {data.history.map((h, i) => {
            const isCurrent =
              i === 0 && data.current != null &&
              Math.abs(h.percentage - data.current.percentage) < 0.0005
            return (
              <li key={h.id} className="flex items-center gap-3 border-b border-border/70 py-2.5">
                <span className="w-14 shrink-0 text-[15px] font-semibold tabular-nums text-foreground">
                  {Math.round(h.percentage * 1000) / 10}%
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[14px] text-foreground">{deviceLabel(h.device)}</span>
                  <span className="block text-[13px] text-muted-foreground">{fmtWhen(h.created_at)}</span>
                </span>
                {isCurrent ? (
                  <span className="shrink-0 px-3 text-[11px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
                    <Trans>Current</Trans>
                  </span>
                ) : (
                  <Pill onClick={() => restore(h)} disabled={restoring != null} className="h-8 shrink-0 px-3 text-[13px]">
                    {restoring === h.id
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <RotateCcw className="h-3.5 w-3.5" />}
                    <Trans>Restore</Trans>
                  </Pill>
                )}
              </li>
            )
          })}
        </ul>
      )}
      <p className="pt-4 text-[13px] leading-relaxed text-muted-foreground">
        <Trans>Restoring sets the live position (and read status) back to that
        point; devices pick it up on their next sync. Restoring below
        100% un-finishes a falsely completed book.</Trans>
      </p>
    </ModalShell>,
    document.body,
  )
}
