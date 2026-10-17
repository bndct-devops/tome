import { useCallback, useEffect, useState } from 'react'
import { BellPlus, BellRing, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { getFollows, invalidateFollows, type FollowOut } from '@/lib/follows'
import { useToast } from '@/contexts/ToastContext'
import { formatDate } from '@/lib/utils'
import { Pill } from '@/components/page'
import { t } from '@lingui/core/macro'

/**
 * Follow/unfollow a series from its detail page (release detection). Renders
 * nothing when TOME_RELEASE_DETECTION is off (the follows endpoint 403s) or on
 * the "No Series" bucket. While followed, shows the tracker's latest volume +
 * release date under the button — labelled "Next" when the date is upcoming.
 */

export function SeriesFollowButton({ seriesName }: { seriesName: string }) {
  const { toast } = useToast()
  // undefined = follows not resolved yet (render NOTHING — an optimistic
  // button flickered on instances where release detection is disabled).
  const [rows, setRows] = useState<FollowOut[] | null | undefined>(undefined)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => { getFollows().then(setRows) }, [seriesName])
  useEffect(load, [load])

  if (rows == null || seriesName === '__unserialized__') return null
  const follow = rows.find(f => f.name.toLowerCase() === seriesName.toLowerCase()) ?? null

  const toggle = async () => {
    if (busy) return
    setBusy(true)
    try {
      if (follow) {
        await api.delete(`/wishlist/${follow.id}`)
      } else {
        await api.post('/wishlist/follow', { name: seriesName })
        toast.success(t`Following "${seriesName}" — you'll hear when a new volume is out`)
      }
      invalidateFollows()
      getFollows().then(setRows)
    } catch (e) {
      toast.error((e as Error).message ?? t`Could not resolve this series on Hardcover`)
    } finally {
      setBusy(false)
    }
  }

  const date = follow?.latest_release_date ?? null
  const vol = follow?.latest_known_index ?? null
  const upcoming = date != null && date >= new Date().toISOString().slice(0, 10)

  return (
    <div className="relative">
      <Pill
        variant={follow ? 'secondary' : 'quiet'}
        onClick={toggle}
        disabled={busy}
        aria-pressed={!!follow}
        title={follow ? t`Stop following this series` : t`Get notified when a new volume is released`}
      >
        {busy
          ? <Loader2 className="w-4 h-4 animate-spin" />
          : follow ? <BellRing className="w-4 h-4 text-primary" /> : <BellPlus className="w-4 h-4" />}
        {follow ? t`Following` : t`Follow`}
      </Pill>
      {follow && vol != null && date && (
        <p className="absolute top-full left-1/2 -translate-x-1/2 mt-1 whitespace-nowrap text-[10px] text-muted-foreground text-center">
          {(() => { const v = Number.isInteger(vol) ? vol : vol.toFixed(1); const d = formatDate(date); return upcoming ? t`Next: Vol ${v} · ${d}` : t`Latest: Vol ${v} · ${d}` })()}
        </p>
      )}
    </div>
  )
}
