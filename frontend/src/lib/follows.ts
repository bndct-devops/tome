import { api } from '@/lib/api'

/**
 * Shared, session-cached access to the user's followed series.
 *
 * Release detection is opt-in (TOME_RELEASE_DETECTION); when it's off the
 * follows endpoint 403s, so the client never calls it then (see
 * setFollowsAvailable). Every consumer (Wishlist Following section, the
 * series-page Follow button, the Home Upcoming-releases card) must render
 * NOTHING until this resolves - rendering optimistically caused a visible
 * flicker on instances with the feature disabled.
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

// Whether follows exist for the signed-in user: release detection on
// (the `release_detection` flag from /auth/me) and a member or admin role.
// AuthContext sets this before it publishes a user, so no consumer calls the
// endpoint while the feature is off. undefined = not known yet.
let available: boolean | undefined

// undefined = not fetched yet · array = follows
let cached: FollowOut[] | undefined
let inflight: Promise<FollowOut[] | null> | null = null

/** Called by AuthContext whenever the signed-in user changes. */
export function setFollowsAvailable(next: boolean | undefined) {
  if (next !== available) cached = undefined
  available = next
}

/** Follows list, or null when release detection is off for this user. */
export function getFollows(force = false): Promise<FollowOut[] | null> {
  if (available !== true) return Promise.resolve(null)
  if (!force && cached !== undefined) return Promise.resolve(cached)
  if (!force && inflight) return inflight
  inflight = api.get<FollowOut[]>('/wishlist/follows')
    .then(rows => { cached = rows; return rows as FollowOut[] | null })
    // A network blip is transient: answer null now, refetch next time.
    .catch(() => cached ?? null)
    .finally(() => { inflight = null })
  return inflight
}

/** Call after follow/unfollow so the next getFollows refetches. */
export function invalidateFollows() {
  cached = undefined
}
