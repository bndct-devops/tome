import { useState } from 'react'
import { api } from '@/lib/api'
import type { Library, SavedFilter } from '@/lib/books'

/**
 * Libraries + Shelves for the sidebar, with a module-level cache of the last
 * fetch. Every page mounts its own shell (Dashboard, Stats, Highlights,
 * Wishlist, Bindery), so without the cache each navigation rendered the
 * sidebar with empty sections for a beat and the lists flickered in. Seeding
 * from the cache paints the previous lists instantly; the mount-time refetch
 * then updates them silently.
 *
 * Keyed by user id - admin impersonation and re-login must not flash the
 * previous user's libraries into the next user's first paint.
 */
let cache: { userId: number | null; libraries: Library[]; savedFilters: SavedFilter[] } = {
  userId: null,
  libraries: [],
  savedFilters: [],
}

export function useSidebarLists(userId: number | null | undefined) {
  const uid = userId ?? null
  const seeded = cache.userId === uid
  const [libraries, setLibraries] = useState<Library[]>(seeded ? cache.libraries : [])
  const [savedFilters, setSavedFilters] = useState<SavedFilter[]>(seeded ? cache.savedFilters : [])

  const remember = (patch: Partial<Pick<typeof cache, 'libraries' | 'savedFilters'>>) => {
    if (cache.userId !== uid) cache = { userId: uid, libraries: [], savedFilters: [] }
    Object.assign(cache, patch)
  }

  const loadLibraries = () => {
    api.get<Library[]>('/libraries')
      .then(d => { remember({ libraries: d }); setLibraries(d) })
      .catch(() => {})
  }
  const loadSavedFilters = () => {
    api.get<SavedFilter[]>('/saved-filters')
      .then(d => { remember({ savedFilters: d }); setSavedFilters(d) })
      .catch(() => {})
  }

  return { libraries, savedFilters, loadLibraries, loadSavedFilters }
}
