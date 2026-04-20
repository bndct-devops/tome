import { api } from './api'

export interface ApiTokenListItem {
  id: number
  name: string
  prefix: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
  user_id: number
  username: string
}

export interface ApiTokenCreateResponse {
  id: number
  name: string
  prefix: string
  token: string
  created_at: string
}

export function listTokens(allUsers = false): Promise<ApiTokenListItem[]> {
  return api.get<ApiTokenListItem[]>(`/tokens/${allUsers ? '?all=true' : ''}`)
}

export function createToken(name: string): Promise<ApiTokenCreateResponse> {
  return api.post<ApiTokenCreateResponse>('/tokens/', { name })
}

export function revokeToken(id: number): Promise<void> {
  return api.delete<void>(`/tokens/${id}`)
}
