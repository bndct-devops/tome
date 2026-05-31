import { api } from '@/lib/api'

// ── Types ──────────────────────────────────────────────────────────────────

export interface NotificationOut {
  id: number
  user_id: number
  kind: string
  title: string
  body: string | null
  link: string | null
  read: boolean
  created_at: string
}

// ── API ────────────────────────────────────────────────────────────────────

export function listNotifications(unread?: boolean): Promise<NotificationOut[]> {
  const qs = unread != null ? `?unread=${unread}` : ''
  return api.get<NotificationOut[]>(`/notifications${qs}`)
}

export function markRead(id: number): Promise<NotificationOut> {
  return api.post<NotificationOut>(`/notifications/${id}/read`)
}

export function markAllRead(): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/notifications/read-all')
}
