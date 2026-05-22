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

export function useKosyncStatus(): KosyncStatus | null {
  const [status, setStatus] = useState<KosyncStatus | null>(null)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    let cancelled = false

    async function fetchStatus() {
      if (typeof navigator !== 'undefined' && navigator.onLine === false) return
      try {
        const r = await api.get<ApiResponse>('/auth/me/kosync')
        if (cancelled) return
        setStatus({
          linked: r.linked,
          lastSync: r.last_sync ?? null,
          lastDevice: r.last_device ?? null,
          syncedDocuments: r.synced_documents ?? 0,
          isStale: computeStale(r.last_sync ?? null),
        })
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
