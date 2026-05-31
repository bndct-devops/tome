import { api } from '@/lib/api'

// ── Types ──────────────────────────────────────────────────────────────────

export interface WishCoverageVolume {
  id: number
  title: string
  series_index: number | null
  cover_path: string | null
}

export interface WishOut {
  id: number
  user_id: number
  kind: string
  status: 'open' | 'fulfilled' | 'dismissed'
  title: string
  author: string | null
  series: string | null
  series_index: number | null
  cover_url: string | null
  source: string | null
  source_id: string | null
  isbn: string | null
  note: string | null
  fulfilled_book_id: number | null
  fulfilled_by: number | null
  fulfilled_at: string | null
  suggested_book_ids: number[] | null
  series_coverage: WishCoverageVolume[] | null
  series_total: number | null
  external_series_id: string | null
  created_at: string
  updated_at: string
}

export interface WishAdminOut extends WishOut {
  requester_username: string | null
}

export interface WishCreate {
  title: string
  author?: string | null
  series?: string | null
  series_index?: number | null
  cover_url?: string | null
  source?: string | null
  source_id?: string | null
  isbn?: string | null
  note?: string | null
  external_series_id?: string | null
  series_total?: number | null
}

export interface WishSearchResult {
  source: string
  source_id: string
  title: string
  author: string | null
  cover_url: string | null
  series: string | null
  series_index: number | null
  isbn: string | null
  year: number | null
  description: string | null
}

export interface WishSeriesResult {
  source: string
  source_id: string
  name: string
  author: string | null
  total: number | null
  slug: string | null
  cover_url: string | null
}

// ── Member API ─────────────────────────────────────────────────────────────

export function listWishes(status?: string): Promise<WishOut[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : ''
  return api.get<WishOut[]>(`/wishlist${qs}`)
}

export function createWish(payload: WishCreate): Promise<WishOut> {
  return api.post<WishOut>('/wishlist', payload)
}

export function deleteWish(id: number): Promise<void> {
  return api.delete<void>(`/wishlist/${id}`)
}

export function searchWishCandidates(q: string): Promise<WishSearchResult[]> {
  return api.get<WishSearchResult[]>(`/wishlist/search?q=${encodeURIComponent(q)}`)
}

export function searchWishSeries(q: string): Promise<WishSeriesResult[]> {
  return api.get<WishSeriesResult[]>(`/wishlist/search-series?q=${encodeURIComponent(q)}`)
}

export function seriesSearchAvailable(): Promise<{ available: boolean }> {
  return api.get<{ available: boolean }>('/wishlist/series-search-available')
}

// ── Admin API ──────────────────────────────────────────────────────────────

export function adminListWishes(params?: { status?: string; user_id?: number }): Promise<WishAdminOut[]> {
  const qs = new URLSearchParams()
  if (params?.status) qs.set('status', params.status)
  if (params?.user_id != null) qs.set('user_id', String(params.user_id))
  const query = qs.toString() ? `?${qs}` : ''
  return api.get<WishAdminOut[]>(`/admin/wishlist${query}`)
}

export function fulfillWish(wishId: number, bookId?: number | null): Promise<WishOut> {
  return api.post<WishOut>(`/admin/wishlist/${wishId}/fulfill`, { book_id: bookId ?? null })
}

export function dismissWish(wishId: number): Promise<WishOut> {
  return api.post<WishOut>(`/admin/wishlist/${wishId}/dismiss`)
}

export function adminWishMatches(bookId: number): Promise<WishAdminOut[]> {
  return api.get<WishAdminOut[]>(`/admin/wishlist/matches?book_id=${bookId}`)
}
