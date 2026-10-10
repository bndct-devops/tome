// AI provider: status, keys, instance settings and the usage meter.
// Pages call useAiStatus() and render AI buttons only when aiAvailable() says
// so: no key, feature off or TOME_AI_ENABLED=false means the surface is
// hidden, not disabled. Keys are never returned by the API, only a masked suffix.
import { useCallback, useEffect, useSyncExternalStore } from 'react'
import { msg } from '@lingui/core/macro'
import { i18n, type MessageDescriptor } from '@lingui/core'
import { api } from '@/lib/api'
import type { MetadataCandidate, SeriesStatus } from '@/lib/books'
import { useAuth } from '@/contexts/AuthContext'

export type AiFeatureKey = 'bindery_identify' | 'fix_book' | 'series_cleanup'
export type AiKeySource = 'user' | 'instance'

export interface AiFeatureInfo {
  label: string
  description: string
  enabled: boolean
  /** Admin override, or null when the shipped default applies. */
  model: string | null
  default_model: string
}

export interface AiStatus {
  /** TOME_AI_ENABLED and the admin's instance switch. */
  enabled: boolean
  env_enabled: boolean
  provider: 'anthropic'
  provider_label: string
  /** Member or admin. Guests never use AI features. */
  can_use: boolean
  has_own_key: boolean
  own_key_suffix: string | null
  own_key_set_at: string | null
  instance_key_available: boolean
  /** Which key a call by this user would use, or null when none resolves. */
  key_source: AiKeySource | null
  confidence_threshold: number
  /** Model ids an admin may pick per feature. */
  models: string[]
  features: Record<AiFeatureKey, AiFeatureInfo>
  // Admin only:
  instance_enabled?: boolean
  instance_key_set?: boolean
  instance_key_source?: 'db' | 'env' | null
  instance_key_suffix?: string | null
  share_instance_key?: boolean
}

export interface AiInstanceUpdate {
  enabled?: boolean
  instance_key?: string
  clear_instance_key?: boolean
  share_instance_key?: boolean
  confidence_threshold?: number
  /** model: an id from AiStatus.models, or "default" to clear the override. */
  features?: Partial<Record<AiFeatureKey, { enabled?: boolean; model?: string }>>
}

export interface AiUsageTotals {
  calls: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
}

export interface AiUsageSummary {
  month: string
  scope: 'self' | 'all'
  total: AiUsageTotals
  by_feature: (AiUsageTotals & { feature: string; label: string })[]
  /** Only with all=true (admins). user_id/username are null for deleted users. */
  by_user?: (AiUsageTotals & { user_id: number | null; username: string | null })[]
}

// ── feature copy ─────────────────────────────────────────────────────────────
// The API returns English labels; the UI shows its own translated copy keyed by
// feature key and falls back to the API text for keys it does not know yet.

const FEATURE_COPY: Record<AiFeatureKey, { label: MessageDescriptor; description: MessageDescriptor }> = {
  bindery_identify: {
    label: msg`Identify in the Bindery`,
    description: msg`Pre-fills title, author, series, volume, type and tags for new files from the filename, embedded metadata and the first pages.`,
  },
  fix_book: {
    label: msg`Fix this book`,
    description: msg`Picks the right metadata match for one book and proposes a diff to confirm.`,
  },
  series_cleanup: {
    label: msg`Clean up this series`,
    description: msg`Proposes one diff for a series: name, volume titles and numbers, status and arcs.`,
  },
}

function isFeatureKey(key: string): key is AiFeatureKey {
  return Object.prototype.hasOwnProperty.call(FEATURE_COPY, key)
}

/** Translated feature label, or the API's English label for an unknown key. */
export function aiFeatureLabel(key: string, fallback?: string): string {
  return isFeatureKey(key) ? i18n._(FEATURE_COPY[key].label) : (fallback ?? key)
}

/** Translated feature description, or the API's English text for an unknown key. */
export function aiFeatureDescription(key: string, fallback?: string): string {
  return isFeatureKey(key) ? i18n._(FEATURE_COPY[key].description) : (fallback ?? '')
}

// ── API calls ────────────────────────────────────────────────────────────────

export function getAiStatus(): Promise<AiStatus> {
  return api.get<AiStatus>('/ai/status')
}

export async function setKey(apiKey: string): Promise<AiStatus> {
  const s = await api.put<AiStatus>('/ai/key', { api_key: apiKey })
  publish(s)
  return s
}

export async function removeKey(): Promise<AiStatus> {
  const s = await api.delete<AiStatus>('/ai/key')
  publish(s)
  return s
}

export async function updateInstance(body: AiInstanceUpdate): Promise<AiStatus> {
  const s = await api.put<AiStatus>('/ai/instance', body)
  publish(s)
  return s
}

export function getUsage(month?: string, all = false): Promise<AiUsageSummary> {
  const q = new URLSearchParams()
  if (month) q.set('month', month)
  if (all) q.set('all', 'true')
  const qs = q.toString()
  return api.get<AiUsageSummary>(`/ai/usage${qs ? `?${qs}` : ''}`)
}

// ── Bindery identify ─────────────────────────────────────────────────────────

/** Files per /ai/identify request (the server makes one model call per 10). */
export const IDENTIFY_BATCH_SIZE = 10

export interface IdentifyProposal {
  title: string
  author: string | null
  series: string | null
  series_index: number | null
  content_type: 'volume' | 'chapter'
  book_type_slug: string | null
  book_type_id: number | null
  language: string | null
  year: number | null
  /** Only tags the metadata candidates carried; the model never invents one. */
  tags: string[]
}

export interface IdentifyResult {
  path: string
  /** null when the model returned nothing for this file. */
  proposal: IdentifyProposal | null
  confidence: number
  /** One sentence naming what decided it. */
  evidence: string
  /** The metadata-source candidate the model matched, if any. */
  candidate: MetadataCandidate | null
}

export interface IdentifyResponse {
  proposals: IdentifyResult[]
  threshold: number
}

/** Propose metadata for Bindery files (at most 50 paths). Writes nothing. */
export function identifyFiles(paths: string[]): Promise<IdentifyResponse> {
  return api.post<IdentifyResponse>('/ai/identify', { paths })
}

// ── Fix this book ────────────────────────────────────────────────────────────

/** The fields apply-metadata accepts. In a proposal, null means "no change". */
export interface FixBookFields {
  title: string | null
  author: string | null
  description: string | null
  publisher: string | null
  year: number | null
  language: string | null
  isbn: string | null
  series: string | null
  series_index: number | null
  /** Only tags the metadata candidates carried; never an empty list. */
  tags: string[] | null
  cover_url: string | null
}

export interface FixBookResponse {
  proposal: FixBookFields
  changed_fields: (keyof FixBookFields)[]
  confidence: number
  /** One sentence naming what decided it. */
  evidence: string
  /** Source of the candidate the model matched ("open_library", ...), if any. */
  candidate_source: string | null
  candidate: MetadataCandidate | null
  current: FixBookFields & { tags: string[]; has_cover: boolean }
  threshold: number
}

/** Ask the model for a metadata diff for one book. Writes nothing. */
export function fixBook(bookId: number, query?: string): Promise<FixBookResponse> {
  return api.post<FixBookResponse>(`/ai/books/${bookId}/fix`, query ? { query } : {})
}

/** The proposal as a MetadataCandidate for the metadata diff dialog. Fields the
 *  model left alone stay empty, so the dialog shows them as "no change". */
export function fixProposalToCandidate(res: FixBookResponse): MetadataCandidate {
  const p = res.proposal
  return {
    source: 'ai', source_id: 'ai-fix',
    title: p.title ?? '',
    author: p.author,
    description: p.description,
    cover_url: p.cover_url,
    publisher: p.publisher,
    year: p.year,
    page_count: null,
    isbn: p.isbn,
    language: p.language,
    tags: p.tags ?? [],
    series: p.series,
    series_index: p.series_index,
  }
}

// ── Clean up this series ─────────────────────────────────────────────────────

export interface SeriesCleanupArc {
  name: string
  start_index: number
  end_index: number
  description: string | null
}

export interface SeriesCleanupBookRow {
  book_id: number
  current: { title: string; series_index: number | null }
  /** null means "no change" for that field. */
  proposed: { title: string | null; series_index: number | null }
  evidence: string
  /** False when this user may not edit the book (members edit only their uploads). */
  editable: boolean
}

export interface SeriesCleanupProposal {
  series: string
  book_count: number
  /** proposed null = keep the current name. */
  series_name: { current: string; proposed: string | null; evidence: string }
  /** Only the books with a proposed change. */
  books: SeriesCleanupBookRow[]
  status: { current: SeriesStatus; proposed: SeriesStatus | null; evidence: string }
  /** proposed null = keep the current arcs; a list replaces them. */
  arcs: { current: SeriesCleanupArc[]; proposed: SeriesCleanupArc[] | null; evidence: string }
  confidence: number
  evidence: string
  threshold: number
  /** What this user may apply. */
  permissions: { series_name: boolean; status: boolean; arcs: boolean }
}

/** Send only the parts the user kept. arcs is the full new arc list. */
export interface SeriesCleanupApply {
  series_name?: string
  books?: { book_id: number; title?: string | null; series_index?: number | null }[]
  status?: SeriesStatus
  arcs?: SeriesCleanupArc[]
}

export interface SeriesCleanupResult {
  /** The series name after the apply (the new one when renamed). */
  series_name: string
  renamed: boolean
  books_updated: number
  books_moved: number
  status: SeriesStatus
  arcs: SeriesCleanupArc[]
}

/** Ask the model for one cleanup diff of a series. Writes nothing. */
export function proposeSeriesCleanup(name: string): Promise<SeriesCleanupProposal> {
  return api.post<SeriesCleanupProposal>(`/ai/series/${encodeURIComponent(name)}/cleanup`, {})
}

/** Apply the checked parts of a cleanup proposal in one transaction. */
export function applySeriesCleanup(name: string, body: SeriesCleanupApply): Promise<SeriesCleanupResult> {
  return api.post<SeriesCleanupResult>(`/ai/series/${encodeURIComponent(name)}/cleanup/apply`, body)
}

// ── shared status cache ──────────────────────────────────────────────────────
// One fetch per signed-in user, shared by every component that asks. Writes
// above publish the fresh status so buttons appear/disappear everywhere at once.

let cache: { userId: number; status: AiStatus } | null = null
let cacheUserId: number | null = null
let inflight: Promise<void> | null = null
const listeners = new Set<() => void>()

function publish(status: AiStatus) {
  if (cacheUserId == null) return
  cache = { userId: cacheUserId, status }
  listeners.forEach(l => l())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function load(userId: number, force = false): Promise<void> {
  if (!force && cache?.userId === userId) return Promise.resolve()
  if (!force && inflight && cacheUserId === userId) return inflight
  cacheUserId = userId
  const p = getAiStatus()
    .then(s => { if (cacheUserId === userId) publish(s) })
    .catch(() => {})
    .finally(() => { if (inflight === p) inflight = null })
  inflight = p
  return p
}

/** True when this user can see the given AI feature at all. */
export function aiAvailable(status: AiStatus | null, feature?: AiFeatureKey): boolean {
  if (!status || !status.enabled || !status.can_use || status.key_source == null) return false
  return feature ? status.features[feature]?.enabled === true : true
}

export function useAiStatus(): {
  status: AiStatus | null
  loading: boolean
  refresh: () => Promise<void>
  available: (feature?: AiFeatureKey) => boolean
} {
  const { user } = useAuth()
  const userId = user?.id ?? null
  const snapshot = useSyncExternalStore(subscribe, () => cache)
  const status = snapshot && snapshot.userId === userId ? snapshot.status : null

  useEffect(() => {
    if (userId != null) void load(userId)
  }, [userId])

  const refresh = useCallback(async () => {
    if (userId != null) await load(userId, true)
  }, [userId])

  const available = useCallback((feature?: AiFeatureKey) => aiAvailable(status, feature), [status])

  return { status, loading: userId != null && status == null, refresh, available }
}
