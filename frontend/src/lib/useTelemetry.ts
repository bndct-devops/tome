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

export function useTelemetry() {
  const { user } = useAuth()
  const admin = isAdmin(user)
  const [data, setData] = useState<TelemetryInfo | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!admin) return
    let live = true
    api.get<TelemetryInfo>('/admin/telemetry').then(r => { if (live) setData(r) }).catch(() => {})
    return () => { live = false }
  }, [admin])

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
  }, [])

  return { admin, data, busy, decide }
}
