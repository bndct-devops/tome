// Position history + restore: the recovery UI for a bad sync. Lists the
// recent position log for one book (GET /books/{id}/position-history) and
// restores any entry as the live position — explicitly un-finishing the book
// when recovering from a false 100%.
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { History, Loader2, RotateCcw, X } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

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
  if (diff < 3600_000) return `${Math.max(1, Math.floor(diff / 60_000))} min ago`
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)}h ago`
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) +
    ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
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
    <>
      <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="pointer-events-auto flex max-h-[70vh] w-full max-w-md flex-col rounded-xl border border-border bg-card shadow-xl">
          <div className="flex items-center gap-2 border-b border-border px-5 py-3.5">
            <History className="h-4 w-4 text-primary" />
            <h2 className="font-display text-base text-foreground">Position history</h2>
            <button
              onClick={onClose}
              aria-label="Close"
              className="ml-auto rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="min-h-0 overflow-y-auto overscroll-contain px-5 py-3">
            {!data ? (
              <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading…
              </div>
            ) : data.history.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No position changes recorded yet — history starts with the next sync.
              </p>
            ) : (
              <ul className="flex flex-col">
                {data.history.map((h, i) => {
                  const isCurrent =
                    i === 0 && data.current != null &&
                    Math.abs(h.percentage - data.current.percentage) < 0.0005
                  return (
                    <li
                      key={h.id}
                      className={cn(
                        'flex items-center gap-3 border-b border-border/50 py-2.5 last:border-b-0',
                      )}
                    >
                      <span className="w-12 text-sm font-semibold tabular-nums text-foreground">
                        {Math.round(h.percentage * 1000) / 10}%
                      </span>
                      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                        {h.device || 'unknown device'} · {fmtWhen(h.created_at)}
                        {isCurrent && (
                          <span className="ml-1.5 rounded bg-muted px-1.5 py-0.5 text-[10px] text-foreground">
                            current
                          </span>
                        )}
                      </span>
                      {!isCurrent && (
                        <button
                          onClick={() => restore(h)}
                          disabled={restoring != null}
                          className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
                        >
                          {restoring === h.id
                            ? <Loader2 className="h-3 w-3 animate-spin" />
                            : <RotateCcw className="h-3 w-3" />}
                          Restore
                        </button>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
            <p className="pb-1 pt-3 text-[11px] leading-relaxed text-muted-foreground">
              Restoring sets the live position (and read status) back to that
              point; devices pick it up on their next sync. Restoring below
              100% un-finishes a falsely completed book.
            </p>
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
