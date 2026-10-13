import { Tablet } from 'lucide-react'
import { useLingui } from '@lingui/react/macro'
import { devicesFor, isStale, syncedAgo, useDeviceInventories } from '@/lib/deviceBooks'
import { cn } from '@/lib/utils'

/** "On <device>" pills for a book: one per KOReader device of the user's
 *  that holds it (TomeSync build 50+). Renders nothing for a book on no
 *  device, or for a user without devices. A list the device has not
 *  refreshed in a few days is muted, with its age in the tooltip. */
export function DevicePills({ bookId, size = 'sm', className }: {
  bookId: number
  size?: 'xs' | 'sm'
  className?: string
}) {
  const { t } = useLingui()
  const on = devicesFor(useDeviceInventories(), bookId)
  if (on.length === 0) return null
  return (
    <span className={cn('inline-flex flex-wrap items-center gap-1 min-w-0', className)}>
      {on.map(d => {
        const stale = isStale(d)
        const ago = syncedAgo(d.synced_at)
        const name = d.name
        return (
          <span
            key={d.name}
            title={t`On ${name} as of the last sync (${ago})`}
            className={cn(
              'inline-flex items-center gap-1 rounded-full font-medium min-w-0 max-w-full',
              size === 'xs' ? 'text-[10px] px-1.5 py-px' : 'text-[11px] px-2 py-0.5',
              stale
                ? 'bg-muted text-muted-foreground'
                : 'bg-primary/10 text-primary',
            )}
          >
            <Tablet className={cn('shrink-0', size === 'xs' ? 'w-2.5 h-2.5' : 'w-3 h-3')} />
            <span className="truncate">{t`On ${name}`}</span>
          </span>
        )
      })}
    </span>
  )
}
