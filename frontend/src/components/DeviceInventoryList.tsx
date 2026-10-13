import { useState } from 'react'
import { Loader2, Tablet, Trash2 } from 'lucide-react'
import { Trans, useLingui, Plural } from '@lingui/react/macro'
import { api } from '@/lib/api'
import { isStale, refreshDeviceInventories, syncedAgo, useDeviceInventories } from '@/lib/deviceBooks'
import { cn } from '@/lib/utils'
import { useToast } from '@/contexts/ToastContext'

/** Settings > KOReader: the devices that report which books they hold
 *  (TomeSync build 50+), with a way to forget one you no longer use. */
export function DeviceInventoryList() {
  const { t } = useLingui()
  const { toast } = useToast()
  const devices = useDeviceInventories()
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  if (devices.length === 0) return null

  async function forget(name: string) {
    setBusy(name)
    try {
      await api.delete(`/device-books/${encodeURIComponent(name)}`)
      await refreshDeviceInventories()
      toast.success(t`Forgot ${name}`)
    } catch {
      toast.error(t`Could not forget ${name}`)
    } finally {
      setBusy(null)
      setConfirming(null)
    }
  }

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
        <Tablet className="w-3 h-3" /> <Trans>Books on your devices</Trans>
      </p>
      <p className="text-xs text-muted-foreground">
        <Trans>Each device sends the list of Tome books on it when it syncs. Books show an "On ..." pill, and the library can be filtered by device. Forget a device you no longer use; one still in use sends its list again on its next sync.</Trans>
      </p>
      <div className="rounded-lg border border-border overflow-hidden text-xs divide-y divide-border">
        {devices.map(d => {
          const name = d.name
          const count = d.book_count
          const ago = syncedAgo(d.synced_at)
          const stale = isStale(d)
          return (
            <div key={d.name} className="flex items-center gap-3 px-3 py-2">
              <span className="font-medium text-foreground truncate flex-1 min-w-0">{name}</span>
              <span className="text-muted-foreground tabular-nums shrink-0">
                <Plural value={count} one="# book" other="# books" />
              </span>
              <span className={cn('shrink-0 hidden sm:block', stale ? 'text-warning' : 'text-muted-foreground')}>
                <Trans>synced {ago}</Trans>
              </span>
              {confirming === name ? (
                <button
                  onClick={() => forget(name)}
                  disabled={busy === name}
                  className="shrink-0 px-2 py-0.5 rounded-md bg-destructive/10 text-destructive hover:bg-destructive/20 transition-colors disabled:opacity-50"
                >
                  {busy === name ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trans>Forget</Trans>}
                </button>
              ) : (
                <button
                  onClick={() => setConfirming(name)}
                  className="shrink-0 p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors"
                  title={t`Forget ${name}`}
                  aria-label={t`Forget ${name}`}
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
