import { api } from './api'

/** A browser or the Tome app signed in as a user. */
export interface ClientDevice {
  id: number
  name: string
  platform: string | null
  app_version: string | null
  kind: 'app' | 'web'
  /** The browser making the request. */
  current: boolean
  created_at: string
  last_seen_at: string | null
  revoked_at: string | null
  user_id: number
  username: string
}

export function listDevices(allUsers = false): Promise<ClientDevice[]> {
  return api.get<ClientDevice[]>(`/auth/devices${allUsers ? '?all=true' : ''}`)
}

export function revokeDevice(id: number): Promise<void> {
  return api.delete<void>(`/auth/devices/${id}`)
}
