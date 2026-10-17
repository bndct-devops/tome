import { Link } from 'react-router-dom'
import { t } from '@lingui/core/macro'
import type { WishCoverageVolume } from '@/lib/wishlist'

/* eslint-disable lingui/no-unlocalized-strings -- Tailwind classes */
const PILL_BASE = 'inline-flex items-center justify-center min-w-9 h-7 px-2.5 rounded-full text-[13px] font-medium tabular-nums transition-colors'
const PILL_PRESENT = 'bg-success/15 text-success hover:bg-success/25'
const PILL_MISSING = 'ring-1 ring-inset ring-border text-muted-foreground/60'
/* eslint-enable lingui/no-unlocalized-strings */

// The refreshed look (Wishlist): round chips on the neutral fill, present
// volumes filled in the success colour.
const CHIP_BASE = 'inline-flex items-center h-7 px-2.5 rounded-full text-[12px] font-semibold tabular-nums transition-colors'
const CHIP_PRESENT = 'bg-success text-background hover:bg-success/85'
const CHIP_MISSING = 'bg-secondary text-muted-foreground'

/**
 * Coverage for a whole-series wish, rendered as a 1..highest strip styled like
 * the reading-status pills: present volumes are green and link to the book,
 * missing volumes are plain muted pills so the gaps read clearly. Non-integer /
 * unnumbered volumes append as separate present pills. A "X of Y present" line
 * summarises.
 */
export function SeriesCoverageStrip({ coverage, total, look = 'boxed' }: { coverage: WishCoverageVolume[]; total?: number | null; look?: 'boxed' | 'chips' }) {
  if (!coverage || coverage.length === 0) return null
  const chips = look === 'chips'
  const present = chips ? `${CHIP_BASE} ${CHIP_PRESENT}` : `${PILL_BASE} ${PILL_PRESENT}`
  const missing = chips ? `${CHIP_BASE} ${CHIP_MISSING}` : `${PILL_BASE} ${PILL_MISSING}`

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
    <div className={chips ? 'mt-2.5' : 'mt-1.5'}>
      <div className="flex flex-wrap items-center gap-1.5">
        {slots.map(n => {
          const v = byIndex.get(n)
          return v ? (
            <Link key={n} to={`/books/${v.id}`} title={v.title} className={present}>
              #{n}
            </Link>
          ) : (
            <span key={n} title={t`Volume ${n} — not in the library`} className={missing}>
              #{n}
            </span>
          )
        })}
        {extras.map(v => (
          <Link key={v.id} to={`/books/${v.id}`} title={v.title} className={present}>
            {v.series_index != null ? `#${v.series_index}` : v.title}
          </Link>
        ))}
      </div>
      {maxN > 0 && (
        <p className={chips ? 'text-[13px] text-muted-foreground mt-2' : 'text-[10px] text-muted-foreground mt-1.5'}>
          {t`${presentCount} of ${maxN} present`}
        </p>
      )}
    </div>
  )
}
