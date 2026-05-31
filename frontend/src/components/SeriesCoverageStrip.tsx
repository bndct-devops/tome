import { Link } from 'react-router-dom'
import type { WishCoverageVolume } from '@/lib/wishlist'

const PILL_BASE = 'inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium border transition-all'
const PILL_PRESENT = 'bg-green-500/10 border-green-500 text-green-600 dark:text-green-400 hover:bg-green-500/20'
const PILL_MISSING = 'border-border text-muted-foreground/50'

/**
 * Coverage for a whole-series wish, rendered as a 1..highest strip styled like
 * the reading-status pills: present volumes are green and link to the book,
 * missing volumes are plain muted pills so the gaps read clearly. Non-integer /
 * unnumbered volumes append as separate present pills. A "X of Y present" line
 * summarises.
 */
export function SeriesCoverageStrip({ coverage, total }: { coverage: WishCoverageVolume[]; total?: number | null }) {
  if (!coverage || coverage.length === 0) return null

  const byIndex = new Map<number, WishCoverageVolume>()
  const extras: WishCoverageVolume[] = []
  let maxPresent = 0
  for (const v of coverage) {
    if (v.series_index != null && Number.isInteger(v.series_index) && v.series_index >= 1) {
      byIndex.set(v.series_index, v)
      if (v.series_index > maxPresent) maxPresent = v.series_index
    } else {
      extras.push(v)
    }
  }
  // Render up to the known total (from Hardcover) when we have it, so gaps and
  // not-yet-released volumes show; otherwise fall back to the highest present.
  const maxN = Math.max(maxPresent, total ?? 0)
  const slots = Array.from({ length: maxN }, (_, i) => i + 1)
  const presentCount = byIndex.size

  return (
    <div className="mt-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {slots.map(n => {
          const v = byIndex.get(n)
          return v ? (
            <Link key={n} to={`/books/${v.id}`} title={v.title} className={`${PILL_BASE} ${PILL_PRESENT}`}>
              #{n}
            </Link>
          ) : (
            <span key={n} title={`Volume ${n} — not in the library`} className={`${PILL_BASE} ${PILL_MISSING}`}>
              #{n}
            </span>
          )
        })}
        {extras.map(v => (
          <Link key={v.id} to={`/books/${v.id}`} title={v.title} className={`${PILL_BASE} ${PILL_PRESENT}`}>
            {v.series_index != null ? `#${v.series_index}` : v.title}
          </Link>
        ))}
      </div>
      {maxN > 0 && (
        <p className="text-[10px] text-muted-foreground mt-1.5">
          {presentCount} of {maxN} present
        </p>
      )}
    </div>
  )
}
