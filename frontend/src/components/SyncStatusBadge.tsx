import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useKosyncStatus } from '@/hooks/useKosyncStatus'
import { cn } from '@/lib/utils'
import { t, plural } from '@lingui/core/macro'

const FRESH_MS = 5 * 60_000
const RECENT_MS = 30 * 60_000

function formatRelative(iso: string | null): string {
  if (!iso) return t`Never`
  const ts = new Date(iso).getTime()
  if (Number.isNaN(ts)) return t`Never`
  const diff = Date.now() - ts
  if (diff < 60_000) return t`Just synced`
  const mins = Math.floor(diff / 60_000)
  if (mins < 60) return t`${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return t`${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days === 1) return t`Yesterday`
  if (days < 7) return t`${days}d ago`
  return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

// Freshness tints the sync glyph itself — a bare colored dot next to "12h ago"
// never said what the chip was about.
function iconColor(diff: number): string {
  if (diff < FRESH_MS) return 'text-success'
  if (diff < RECENT_MS) return 'text-warning'
  return 'text-muted-foreground/60'
}

export function SyncStatusBadge() {
  const status = useKosyncStatus()
  const [, force] = useState(0)

  // Re-render every 60s so the relative label drifts forward even without new data
  useEffect(() => {
    const id = window.setInterval(() => force(n => n + 1), 60_000)
    return () => window.clearInterval(id)
  }, [])

  if (!status || !status.linked) return null

  const ts = status.lastSync ? new Date(status.lastSync).getTime() : 0
  const diff = ts ? Date.now() - ts : Infinity
  const label = formatRelative(status.lastSync)
  const lastDevice = status.lastDevice
  const tooltip = lastDevice
    ? plural(status.syncedDocuments, { one: `Last sync from ${lastDevice} · # book tracked`, other: `Last sync from ${lastDevice} · # books tracked` })
    : plural(status.syncedDocuments, { one: '# book tracked', other: '# books tracked' })

  return (
    <div
      title={`${label} — ${tooltip}`}
      className="inline-flex items-center gap-1.5 h-7 px-1.5 sm:px-2 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-default select-none"
      aria-label={t`KOReader sync: ${label}. ${tooltip}`}
    >
      <RefreshCw className={cn('w-3.5 h-3.5 sm:w-3 sm:h-3', iconColor(diff))} />
      <span className="font-medium tabular-nums hidden sm:inline">{label}</span>
    </div>
  )
}
