// One GET per URL for the designed Stats pages, cached for the session like
// /api/stats. A new URL (another range) keeps the previous answer on screen,
// flagged `pending`, until its own lands, so a page can dim in place instead
// of falling back to its skeleton.
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { CustomRange } from '@/components/stats/range'

const cache = new Map<string, unknown>()

/** The query part of a ranged stats request, with the browser's offset. */
export function rangeQuery(days: number, custom: CustomRange | null): string {
  const range = custom ? `start=${custom.start}&end=${custom.end}` : `days=${days}`
  return `${range}&tz_offset=${new Date().getTimezoneOffset()}`
}

/** `path` is relative to /api (e.g. `/stats/habits?days=90&tz_offset=-60`); null fetches nothing. */
export function useStatsGet<T>(path: string | null): { data: T | null; pending: boolean; failed: boolean } {
  const [state, setState] = useState<{ path: string; data: T } | null>(() => {
    const hit = path ? (cache.get(path) as T | undefined) : undefined
    return path && hit !== undefined ? { path, data: hit } : null
  })
  const [failedPath, setFailedPath] = useState<string | null>(null)
  useEffect(() => {
    if (!path) return
    let current = true
    api.get<T>(path)
      .then((r) => {
        cache.set(path, r)
        if (current) setState({ path, data: r })
      })
      .catch(() => { if (current) setFailedPath(path) })
    return () => { current = false }
  }, [path])
  // A cached answer for this URL shows at once; otherwise the last one stays, pending.
  const hit = path ? (cache.get(path) as T | undefined) : undefined
  return {
    data: hit !== undefined ? hit : state?.data ?? null,
    pending: hit === undefined && !!state && state.path !== path,
    failed: !!path && failedPath === path,
  }
}

/** Forget cached answers (after an edit that changes them, e.g. a deleted session). */
export function dropStatsCache(prefix: string) {
  for (const k of [...cache.keys()]) if (k.startsWith(prefix)) cache.delete(k)
}
