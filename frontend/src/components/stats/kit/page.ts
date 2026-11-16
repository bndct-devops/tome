import type { StatsResponse } from '@/components/stats/shared'
import type { CustomRange } from '@/components/stats/range'

export type StatsTab = 'overview' | 'habits' | 'library' | 'taste' | 'timeline' | 'sessions' | 'boards'

/** What the Stats frame hands each designed page. */
export type DesignedPageProps = {
  /** The shared /api/stats answer; null while the first one is on its way (draw the skeleton). */
  stats: StatsResponse | null
  /** A newer range was asked for: dim the range-bound sections in place until it lands. */
  pending: boolean
  /** The range `stats` answers (while pending, the previous range). */
  days: number
  custom: CustomRange | null
  /** Switch to another Stats tab, optionally scrolling to an element id on it. */
  goto: (tab: StatsTab, anchor?: string) => void
}
