import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'

const POLL_MS = 60_000
const STALE_MS = 30 * 60_000

export interface KosyncStatus {
  linked: boolean
  lastSync: string | null
  lastDevice: string | null
  syncedDocuments: number
  isStale: boolean
}

interface ApiResponse {
  linked: boolean
  synced_documents?: number
  last_sync?: string | null
  last_device?: string | null
}

function computeStale(lastSync: string | null): boolean {
  if (!lastSync) return true
  const ts = new Date(lastSync).getTime()
  if (Number.isNaN(ts)) return true
  return Date.now() - ts > STALE_MS
}

// The last answer, kept across mounts: the book page and the library each
// mount their own header, and a badge that starts empty blinks on the way over.
// Held per login, so signing out or impersonating never shows another
// account's sync time.
let last: { token: string | null; status: KosyncStatus } | null = null

const currentToken = () => localStorage.getItem('tome_token')
const remembered = () => (last && last.token === currentToken() ? last.status : null)

export function useKosyncStatus(): KosyncStatus | null {
  const [status, setStatus] = useState<KosyncStatus | null>(remembered)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    let cancelled = false

    async function fetchStatus() {
      if (typeof navigator !== 'undefined' && navigator.onLine === false) return
      const token = currentToken()
      try {
        const r = await api.get<ApiResponse>('/auth/me/kosync')
        if (cancelled) return
        const next = {
          linked: r.linked,
          lastSync: r.last_sync ?? null,
          lastDevice: r.last_device ?? null,
          syncedDocuments: r.synced_documents ?? 0,
          isStale: computeStale(r.last_sync ?? null),
        }
        last = { token, status: next }
        setStatus(next)
      } catch {
        // Leave previous status in place on transient errors
      }
    }

    fetchStatus()
    timerRef.current = window.setInterval(fetchStatus, POLL_MS)

    function onVisibility() {
      if (document.visibilityState === 'visible') fetchStatus()
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      cancelled = true
      if (timerRef.current !== null) window.clearInterval(timerRef.current)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  return status
}
