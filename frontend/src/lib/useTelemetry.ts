// Consent state and the report, for the Home card and the Settings line.
// Admin-only: non-admins never fetch and never see either.
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { useAuth, isAdmin } from '@/contexts/AuthContext'

export interface TelemetryConsent {
  state: 'unset' | 'granted' | 'declined' | 'env_off'
  schema: number
  decided_at?: string | null
  last_sent?: string | null
  next_due?: string | null
  stale?: boolean
}

export interface TelemetryInfo {
  consent: TelemetryConsent
  report: Record<string, unknown>
}

// Last known state, so a page that mounts again (back to Home) is settled at
// once and the refetch only updates it.
let lastKnown: { data: TelemetryInfo | null } | null = null

export function useTelemetry() {
  const { user } = useAuth()
  const admin = isAdmin(user)
  const [data, setDataState] = useState<TelemetryInfo | null>(lastKnown?.data ?? null)
  const setData = useCallback((next: TelemetryInfo | null | ((d: TelemetryInfo | null) => TelemetryInfo | null)) => {
    setDataState(prev => {
      const value = typeof next === 'function' ? next(prev) : next
      lastKnown = { data: value }
      return value
    })
  }, [])
  const [busy, setBusy] = useState(false)
  // True once the state is known (or failed to load), so Home can draw the
  // card in the same paint as everything else instead of pushing it down later.
  const [fetched, setFetched] = useState(!!lastKnown)

  useEffect(() => {
    if (!admin) return
    let live = true
    api.get<TelemetryInfo>('/admin/telemetry').then(r => { if (live) setData(r) }).catch(() => {})
      .finally(() => { if (live) { setFetched(true); lastKnown ??= { data: null } } })
    return () => { live = false }
  }, [admin, setData])

  const decide = useCallback(async (decision: 'granted' | 'declined') => {
    setBusy(true)
    try {
      const r = await api.post<{ consent: TelemetryConsent }>('/admin/telemetry/consent', { decision })
      setData(d => (d ? { ...d, consent: r.consent } : d))
      return true
    } catch {
      return false
    } finally {
      setBusy(false)
    }
  }, [setData])

  return { admin, data, busy, decide, settled: !admin || fetched }
}
