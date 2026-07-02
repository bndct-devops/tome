import { useEffect, useState } from 'react'
import { CalendarClock } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/utils'

/**
 * Home rail card: upcoming volumes of the series you follow (release
 * detection). Renders nothing unless detection is enabled AND at least one
 * followed series has a release date that's today or later — so for most
 * users, most of the time, it simply isn't there.
 */

interface FollowOut {
  id: number
  name: string
  cover_url: string | null
  latest_known_index: number | null
  latest_known_title: string | null
  latest_release_date: string | null
}

export function UpcomingReleases() {
  const [rows, setRows] = useState<FollowOut[]>([])

  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10)
    api.get<FollowOut[]>('/wishlist/follows')
      .then(all => setRows(
        all
          .filter(f => f.latest_release_date != null && f.latest_release_date >= today)
          .sort((a, b) => (a.latest_release_date! < b.latest_release_date! ? -1 : 1))
          .slice(0, 5),
      ))
      .catch(() => {})   // 403 = detection off; card stays absent
  }, [])

  if (rows.length === 0) return null

  return (
    <section className="p-4">
      <h2 className="flex items-center gap-1.5 text-sm font-semibold text-foreground mb-2.5">
        <CalendarClock className="w-4 h-4 text-primary/60" />
        Upcoming releases
      </h2>
      <div className="flex flex-col gap-2">
        {rows.map(f => (
          <div key={f.id} className="flex items-center gap-2.5">
            {f.cover_url
              ? <img src={f.cover_url} alt="" className="w-7 h-10 rounded object-cover shrink-0" />
              : <span className="w-7 h-10 rounded bg-muted shrink-0" />}
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-foreground truncate">{f.name}</p>
              <p className="text-[11px] text-muted-foreground truncate">
                {f.latest_known_index != null && <>Vol {Number.isInteger(f.latest_known_index) ? f.latest_known_index : f.latest_known_index.toFixed(1)} · </>}
                {formatDate(f.latest_release_date!)}
              </p>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
