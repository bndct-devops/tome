import { useState } from 'react'
import { Link } from 'react-router-dom'
import { i18n } from '@lingui/core'
import { t } from '@lingui/core/macro'
import type { WishCoverageVolume } from '@/lib/wishlist'

/* eslint-disable lingui/no-unlocalized-strings -- Tailwind classes */
const PILL_BASE = 'inline-flex items-center justify-center min-w-9 h-7 px-2.5 rounded-full text-[13px] font-medium proportional-nums transition-colors'
const PILL_PRESENT = 'bg-success/15 text-success'
const PRESENT_HOVER = 'hover:bg-success/25'
const PILL_MISSING = 'bg-secondary ring-1 ring-inset ring-input text-muted-foreground'
/* eslint-enable lingui/no-unlocalized-strings */

// The refreshed look (Wishlist): round chips on the neutral fill, present
// volumes filled in the success colour.
const CHIP_BASE = 'inline-flex items-center h-7 px-2.5 rounded-full text-[12px] font-semibold proportional-nums transition-colors'
const CHIP_PRESENT = 'bg-success/15 text-success'
const CHIP_MISSING = 'bg-secondary text-muted-foreground'

const CHIPS_MAX = 24
const BAR_CELLS = 48
const GAPS_MAX = 8

/**
 * Coverage for a whole-series wish, rendered as a 1..highest strip styled like
 * the reading-status pills: present volumes are green and link to the book,
 * missing volumes are plain muted pills so the gaps read clearly. Non-integer /
 * unnumbered volumes append as separate present pills. A "X of Y present" line
 * summarises. A long series is one coverage bar instead.
 */
export function SeriesCoverageStrip({ coverage, total, look = 'boxed' }: { coverage: WishCoverageVolume[]; total?: number | null; look?: 'boxed' | 'chips' }) {
  const [showAll, setShowAll] = useState(false)
  if (!coverage || coverage.length === 0) return null
  const chips = look === 'chips'
  const present = `${chips ? `${CHIP_BASE} ${CHIP_PRESENT}` : `${PILL_BASE} ${PILL_PRESENT}`} ${PRESENT_HOVER}`
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
  const presentCount = byIndex.size

  // A long series (manga by chapter: 1,000+) cannot be one chip per volume.
  // Past CHIPS_MAX slots it is one bar of at most BAR_CELLS cells, each cell a
  // stretch of volumes (full, partly there, missing), with the missing ranges
  // spelled out below. Same height for 40 volumes or 1,000.
  if (maxN > CHIPS_MAX) {
    const cells = Math.min(maxN, BAR_CELLS)
    const bar = Array.from({ length: cells }, (_, i) => {
      const from = Math.floor((i * maxN) / cells) + 1
      const to = Math.floor(((i + 1) * maxN) / cells)
      let have = 0
      for (let n = from; n <= to; n++) if (byIndex.has(n)) have++
      return { from, to, have, size: to - from + 1 }
    })
    const gaps: string[] = []
    for (let n = 1, start = 0; n <= maxN + 1; n++) {
      const miss = n <= maxN && !byIndex.has(n)
      if (miss && !start) start = n
      if (!miss && start) {
        gaps.push(start === n - 1 ? `${start}` : `${start}-${n - 1}`)
        start = 0
      }
    }
    const gapList = (showAll ? gaps : gaps.slice(0, GAPS_MAX)).join(', ')
    const extraCount = extras.length
    const have = presentCount.toLocaleString(i18n.locale)
    const slotCount = maxN.toLocaleString(i18n.locale)
    return (
      <div className={chips ? 'mt-2.5' : 'mt-1.5'}>
        <div
          role="img"
          aria-label={t`${have} of ${slotCount} present`}
          className="flex h-2 w-full max-w-[360px] overflow-hidden rounded-full bg-secondary"
        >
          {bar.map(c => {
            const { from, to, have, size } = c
            return (
              <div
                key={from}
                title={t`Volumes ${from} to ${to}: ${have} of ${size} in the library`}
                className={`flex-1 ${have === size ? 'bg-success' : have > 0 ? 'bg-success/40' : ''}`}
              />
            )
          })}
        </div>
        <p className="text-[13px] text-muted-foreground mt-2">
          {t`${have} of ${slotCount} present`}
        </p>
        {gaps.length > 0 && (
          <p className="text-[13px] text-muted-foreground mt-1 proportional-nums break-words">
            {t`Missing: ${gapList}`}
            {gaps.length > GAPS_MAX && (
              <>
                {' '}
                <button
                  type="button"
                  onClick={() => setShowAll(v => !v)}
                  aria-expanded={showAll}
                  className="font-medium text-foreground/80 hover:text-foreground transition-colors"
                >
                  {showAll ? t`Show less` : t`Show all`}
                </button>
              </>
            )}
          </p>
        )}
        {extraCount > 0 && (
          <p className="text-[13px] text-muted-foreground mt-1">
            {t`${extraCount} more with other numbering`}
          </p>
        )}
      </div>
    )
  }

  return (
    <div className={chips ? 'mt-2.5' : 'mt-1.5'}>
      <div className="flex flex-wrap items-center gap-1.5">
        {Array.from({ length: maxN }, (_, i) => i + 1).map(n => {
          const v = byIndex.get(n)
          return v ? (
            <Link key={n} to={`/books/${v.id}`} title={v.title} className={present}>
              #{n}
            </Link>
          ) : (
            <span key={n} title={t`Volume ${n}, not in the library`} className={missing}>
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
        <p className="text-[13px] text-muted-foreground mt-2">
          {t`${presentCount} of ${maxN} present`}
        </p>
      )}
    </div>
  )
}
