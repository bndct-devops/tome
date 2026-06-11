import { api } from './api'

export type BakeStatus = 'idle' | 'running' | 'done' | 'cancelled' | 'error'

export interface BakeIssue {
  path: string
  status: 'skipped' | 'readonly' | 'failed'
  reason: string | null
}

export interface BakeState {
  status: BakeStatus
  started_at: number | null
  finished_at: number | null
  triggered_by: string | null
  total_files: number
  total_bytes: number
  done_files: number
  done_bytes: number
  baked: number
  skipped: number
  failed: number
  current_file: string | null
  issues: BakeIssue[]
  error: string | null
  elapsed_seconds: number
  eta_seconds: number | null
  library_writable: boolean
  enabled: boolean
}

export interface BakePreflight {
  bakeable_total: number
  already_current: number
  pending: number
  pending_bytes: number
  library_writable: boolean
  enabled: boolean
}

export const getBakeStatus = () => api.get<BakeState>('/admin/bake/status')
export const getBakePreflight = () => api.get<BakePreflight>('/admin/bake/preflight')
export const startBake = () => api.post<BakeState>('/admin/bake/start')
export const cancelBake = () => api.post<BakeState>('/admin/bake/cancel')
export const dismissBake = () => api.post<BakeState>('/admin/bake/dismiss')
