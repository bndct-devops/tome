import { api } from '@/lib/api'

/**
 * Shared, session-cached access to the user's followed series.
 *
 * Release detection is opt-in (TOME_RELEASE_DETECTION); when it's off the
 * follows endpoint 403s. Every consumer (Wishlist Following section, the
 * series-page Follow button, the Home Upcoming-releases card) must render
 * NOTHING until this resolves — rendering optimistically caused a visible
 * flicker on instances with the feature disabled. A disabled verdict is cached
 * for the session so later navigations neither flicker nor re-fire the 403.
 */

export interface FollowOut {
  id: number
  name: string
  author: string | null
  cover_url: string | null
  source_id?: string | null
  latest_known_index: number | null
  latest_known_title: string | null
  latest_release_date: string | null
  owned_max_index: number | null
  last_checked_at?: string | null
}

// The disabled verdict also survives a reload for the rest of the browser
// session: nothing on the client says whether release detection is on, and
// without this every full page load fired one 403.
const DISABLED_KEY = 'tome_follows_disabled'
function storedDisabled(): boolean {
  try { return sessionStorage.getItem(DISABLED_KEY) === '1' } catch { return false }
}
function storeDisabled() {
  try { sessionStorage.setItem(DISABLED_KEY, '1') } catch { /* private mode: in-memory only */ }
}

// undefined = not fetched yet · null = feature disabled · array = follows
let cached: FollowOut[] | null | undefined = storedDisabled() ? null : undefined
let inflight: Promise<FollowOut[] | null> | null = null

/** Follows list, or null when release detection is disabled. */
export function getFollows(force = false): Promise<FollowOut[] | null> {
  if (cached === null) return Promise.resolve(null)         // disabled — final for the session
  if (!force && cached !== undefined) return Promise.resolve(cached)
  if (!force && inflight) return inflight
  inflight = api.get<FollowOut[]>('/wishlist/follows')
    .then(rows => { cached = rows; return rows as FollowOut[] | null })
    .catch((e: Error) => {
      // Our own endpoint's 403 detail mentions "disabled"; anything else
      // (network blip) is transient — don't latch the feature off for it.
      if (/disabled/i.test(e.message ?? '')) { cached = null; storeDisabled() }
      return cached ?? null
    })
    .finally(() => { inflight = null })
  return inflight
}

/** Call after follow/unfollow so the next getFollows refetches. */
export function invalidateFollows() {
  if (cached !== null) cached = undefined
}
