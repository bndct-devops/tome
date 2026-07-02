import { useCallback, useEffect, useState } from 'react'
import { BellPlus, BellRing, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import { cn, formatDate } from '@/lib/utils'

/**
 * Follow/unfollow a series from its detail page (release detection). Renders
 * nothing when TOME_RELEASE_DETECTION is off (the follows endpoint 403s) or on
 * the "No Series" bucket. While followed, shows the tracker's latest volume +
 * release date under the button — labelled "Next" when the date is upcoming.
 */

interface FollowOut {
  id: number
  name: string
  latest_known_index: number | null
  latest_known_title: string | null
  latest_release_date: string | null
}

export function SeriesFollowButton({ seriesName }: { seriesName: string }) {
  const { toast } = useToast()
  const [enabled, setEnabled] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [follow, setFollow] = useState<FollowOut | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.get<FollowOut[]>('/wishlist/follows')
      .then(rows => {
        setFollow(rows.find(f => f.name.toLowerCase() === seriesName.toLowerCase()) ?? null)
        setLoaded(true)
      })
      .catch(() => setEnabled(false))
  }, [seriesName])
  useEffect(load, [load])

  if (!enabled || seriesName === '__unserialized__') return null

  const toggle = async () => {
    if (busy || !loaded) return
    setBusy(true)
    try {
      if (follow) {
        await api.delete(`/wishlist/${follow.id}`)
      } else {
        await api.post('/wishlist/follow', { name: seriesName })
        toast.success(`Following "${seriesName}" — you'll hear when a new volume is out`)
      }
      load()
    } catch (e) {
      toast.error((e as Error).message ?? 'Could not resolve this series on Hardcover')
    } finally {
      setBusy(false)
    }
  }

  const date = follow?.latest_release_date ?? null
  const vol = follow?.latest_known_index ?? null
  const upcoming = date != null && date >= new Date().toISOString().slice(0, 10)

  return (
    <div className="flex flex-col gap-1">
      <button
        onClick={toggle}
        disabled={busy || !loaded}
        className={cn(
          'flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-medium transition-all disabled:opacity-50',
          follow
            ? 'border-primary/40 bg-primary/10 text-foreground hover:bg-primary/15'
            : 'border-border bg-card text-foreground hover:bg-muted',
        )}
        title={follow ? 'Stop following this series' : 'Get notified when a new volume is released'}
      >
        {busy
          ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
          : follow ? <BellRing className="w-3.5 h-3.5 text-primary" /> : <BellPlus className="w-3.5 h-3.5" />}
        {follow ? 'Following' : 'Follow'}
      </button>
      {follow && vol != null && date && (
        <p className="text-[10px] text-muted-foreground text-center">
          {upcoming ? 'Next' : 'Latest'}: Vol {Number.isInteger(vol) ? vol : vol.toFixed(1)} · {formatDate(date)}
        </p>
      )}
    </div>
  )
}
