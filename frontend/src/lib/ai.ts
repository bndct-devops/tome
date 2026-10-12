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

export type AiFeatureKey =
  | 'bindery_identify' | 'fix_book' | 'series_cleanup'
  | 'reading_digest' | 'recap' | 'recall' | 'ask'
export type AiKeySource = 'user' | 'instance'

export interface AiFeatureInfo {
  label: string
  description: string
  /** "reading" for the reading-companion features; absent or other for the rest. */
  kind?: string
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
  /** USD per calendar month (UTC) charged to the instance key; null = no limit. */
  budget_instance_monthly_usd?: number | null
}

export interface AiInstanceUpdate {
  enabled?: boolean
  instance_key?: string
  clear_instance_key?: boolean
  share_instance_key?: boolean
  confidence_threshold?: number
  /** model: an id from AiStatus.models, or "default" to clear the override. */
  features?: Partial<Record<AiFeatureKey, { enabled?: boolean; model?: string }>>
  /** null removes the instance limit; leave the field out to keep it. */
  budget_instance_monthly_usd?: number | null
}

export interface AiUsageTotals {
  calls: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
}

/** This month's spend against the limits (current UTC month). */
export interface AiLimits {
  /** The caller's own monthly limit in USD, null = none. */
  user_monthly_usd: number | null
  /** A cap an admin set on the caller (the caller cannot change it), null = none. */
  admin_monthly_usd?: number | null
  /** The lower of the two: the limit that applies. */
  effective_monthly_usd?: number | null
  user_spent_usd: number
  /** Only for admins and members who can use the instance key. */
  instance_monthly_usd: number | null
  instance_spent_usd: number | null
  /** What is left before a limit stops calls, null when no limit applies. */
  remaining_usd: number | null
}

export interface AiUsageSummary {
  month: string
  scope: 'self' | 'all'
  total: AiUsageTotals
  by_feature: (AiUsageTotals & { feature: string; label: string })[]
  /** Only with all=true (admins). user_id/username are null for deleted users. */
  by_user?: (AiUsageTotals & { user_id: number | null; username: string | null; monthly_limit_usd?: number | null; admin_limit_usd?: number | null; effective_limit_usd?: number | null })[]
  /** Only with all=true (admins): every member and admin, to set limits for.
   *  monthly_limit_usd is the user's own; admin_limit_usd the cap an admin set. */
  members?: { user_id: number; username: string; monthly_limit_usd: number | null; admin_limit_usd?: number | null; effective_limit_usd?: number | null }[]
  limits: AiLimits
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
  reading_digest: {
    label: msg`Build the glossary as you read`,
    description: msg`Reads each chapter you have finished once and notes who and what appears in it. Recap and the glossary are built from these notes.`,
  },
  recap: {
    label: msg`Recap to here`,
    description: msg`Writes a short recap of the story up to where you are, from the chapter notes and the current chapter. Never reads past your position.`,
  },
  recall: {
    label: msg`Recall`,
    description: msg`Tells you who or what a name is, using only the pages before your position.`,
  },
  ask: {
    label: msg`Ask about what you have read`,
    description: msg`Answers a question from the chapters you have read, with a quote for every claim. Nothing after your position.`,
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

/** Set (or with null, remove) your own monthly spend limit in USD. */
export function setOwnLimit(usd: number | null): Promise<{ limits: AiLimits }> {
  return api.put<{ limits: AiLimits }>('/ai/limits', { user_monthly_usd: usd })
}

/** Admins: set (or with null, remove) the cap kept on a member's monthly
 *  spend in USD (separate from the member's own limit; the lower applies). */
export function setUserLimit(
  userId: number, usd: number | null,
): Promise<{ user_id: number; username: string; monthly_limit_usd: number | null; admin_limit_usd?: number | null; effective_limit_usd?: number | null }> {
  return api.put(`/ai/users/${userId}/limit`, { user_monthly_usd: usd })
}

// ── activity: digest jobs and their calls ────────────────────────────────────

/** A job in the history (from its ai_jobs row, live values while it runs). */
export interface AiJobSummary {
  id: string
  user_id: number
  /** Admin view only. */
  username?: string | null
  scope: DigestScope
  status: DigestJobStatus
  total: number
  done: number
  failed: number
  calls: number
  cost_usd: number
  est_cost_usd: number | null
  est_seconds: number | null
  actual_seconds: number | null
  created_at: string | null
  finished_at: string | null
  stop_reason: string | null
  step?: DigestJobStep | null
  step_done?: number
  step_total?: number
}

export interface AiJobBook {
  book_id: number
  title: string | null
  series: string | null
  series_index: number | null
  /** Its jobs plus the direct calls (recap, Recall, fixes) on the book. */
  cost_usd: number
  calls: number
  chapters_digested: number
  last_activity: string | null
  jobs: AiJobSummary[]
}

export interface AiJobsList {
  days: number
  running: (DigestJob & { book_title: string | null; username?: string | null })[]
  books: AiJobBook[]
}

export interface AiJobCall {
  id: number
  created_at: string | null
  model: string
  feature: string
  /** The volume the call read (a series job reads several). */
  book_id: number | null
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  duration_ms: number | null
  cost_usd: number
}

/** Running jobs, then the last `days` days grouped by book. Admins see
 *  everyone, or one user with userId; members see their own. */
export function getAiJobs(days = 30, userId?: number | null): Promise<AiJobsList> {
  const q = new URLSearchParams({ days: String(days) })
  if (userId != null) q.set('user_id', String(userId))
  return api.get<AiJobsList>(`/ai/jobs?${q.toString()}`)
}

export function getAiJobCalls(jobId: string): Promise<{ job_id: string; calls: AiJobCall[] }> {
  return api.get<{ job_id: string; calls: AiJobCall[] }>(`/ai/jobs/${jobId}/calls`)
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
  /** True when the proposal changed the title or author and a second fetch on
   *  the corrected identity found the matching edition; description, tags,
   *  ISBN, cover, publisher and year then come from it (`candidate`). */
  refetched: boolean
  /** The query of that second fetch, or null when none ran. */
  refetch_query: string | null
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

// ── reading companion: recap, glossary, Recall ──────────────────────────────
// Everything is cut at the reader's position: the server only uses the
// chapters before the position chapter plus that chapter up to the position.

export type CompanionReason =
  | 'feature_off' | 'not_epub' | 'no_chapters' | 'no_key' | 'no_position'

export interface CompanionPosition {
  /** 0..1 book fraction. */
  pct: number
  chapter_idx: number
  chapter_title: string | null
  source: 'device' | 'web'
  updated_at: string | null
  /** True when the book is marked read: the window is the whole book and
   *  there is no "up to" chapter. Optional until every server sends it. */
  finished?: boolean
}

export interface CompanionCoverage {
  /** Chapters done, including those the model could not read (never re-sent). */
  digested: number
  needed: number
  /** Of the digested chapters, how many the model could not read. */
  failed?: number
}

export interface CompanionVolume {
  book_id: number
  title: string
  series_index: number | null
}

export interface CompanionScope {
  included_volumes: CompanionVolume[]
  excluded_volumes: (CompanionVolume & { reason: 'not_read' | 'no_chapters' | 'not_epub' })[]
  scope_hash: string
}

export type DigestJobStatus =
  | 'queued' | 'running' | 'stopping'
  | 'done' | 'cancelled' | 'stopped' | 'error' | 'interrupted'

/** "book": this book's chapters before the position; "series": the read
 *  earlier volumes too. */
export type DigestScope = 'book' | 'series'

export type DigestJobStep = 'reading' | 'merging' | 'writing'

export interface DigestJob {
  id: string
  kind: 'digest' | 'reread'
  status: DigestJobStatus
  scope: DigestScope
  user_id: number
  book_id: number
  upto_chapter: number
  /** Chapters processed so far, failed ones included. */
  done: number
  total: number
  failed: number
  /** Model calls made (one per unit of chapters). */
  calls: number
  cost_usd: number
  est_seconds: number | null
  est_cost_usd: number | null
  est_chapters: number | null
  /** The first unit in flight; `chapters` is how many chapters it holds. */
  current: {
    book_id: number; title: string; chapter_idx: number; chapter_title: string | null; chapters?: number
  } | null
  in_flight?: number
  errors: { book_id: number; chapter_idx: number; message: string }[]
  stop_reason: string | null
  /** The step a multi-step job is on (reading chapters, merging duplicate
   *  entries, writing recaps); null for a plain reading job. */
  step: DigestJobStep | null
  /** Progress within the step (writing: recaps written of how many). */
  step_done: number
  step_total: number
  followups: DigestJobFollowup[]
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

/** A follow-up step of a job. The `volume_recaps` follow-up of "Write all"
 *  also says what it wrote, what failed and what it skipped. */
export interface DigestJobFollowup {
  name: string
  status: 'pending' | 'done' | 'skipped' | 'failed'
  message?: string
  written?: number[]
  failed?: { book_id: number; title: string; series_index: number | null; reason: string; message?: string }[]
  skipped?: { book_id: number; title: string; series_index: number | null; reason: 'fresh' | 'unread'; missing_chapters: number }[]
  /** reconcile: candidate pairs checked, pairs merged, model calls, and
   *  which entry (by name) went into which. */
  candidates?: number
  merged?: number
  calls?: number
  merges?: { from: string; into: string }[]
}

function hasFollowup(job: Pick<DigestJob, 'followups'>, name: string): boolean {
  return (job.followups ?? []).some(f => f.name === name)
}

/** A "Write all" job (it carries the volume_recaps follow-up). */
export function isWriteAllJob(job: Pick<DigestJob, 'followups'>): boolean {
  return hasFollowup(job, 'volume_recaps')
}

/** A "Merge duplicates" job: nothing to read, only the reconcile step (a
 *  Write all with nothing to read also has total 0 and a reconcile step). */
export function isMergeOnlyJob(job: Pick<DigestJob, 'total' | 'followups'>): boolean {
  return job.total === 0 && hasFollowup(job, 'reconcile') && !isWriteAllJob(job)
}

/** The progress line of an active job by its step, or null when the job has
 *  no step (the caller keeps its own reading line). Shared by the companion,
 *  the volume list and Activity. */
export function digestJobStepLine(job: Pick<DigestJob, 'step' | 'done' | 'total' | 'step_done' | 'step_total'>): string | null {
  const done = job.done
  const total = job.total
  const stepDone = job.step_done
  const stepTotal = job.step_total
  switch (job.step) {
    case 'reading': return i18n._(msg`Reading chapters: ${done} of ${total}…`)
    case 'merging': return i18n._(msg`Merging duplicate entries…`)
    case 'writing': return i18n._(msg`Writing recaps: ${stepDone} of ${stepTotal}…`)
    default: return null
  }
}

/** The whole progress line of an active job: stopping, getting ready, or
 *  its step. */
export function activeJobLine(job: Pick<DigestJob, 'status' | 'step' | 'done' | 'total' | 'step_done' | 'step_total' | 'followups'>): string {
  const reading = job.step == null || job.step === 'reading'
  if (job.status === 'stopping') {
    return reading ? i18n._(msg`Stopping after the current chapters…`) : i18n._(msg`Stopping after the current call…`)
  }
  if (job.step == null && isMergeOnlyJob(job)) return i18n._(msg`Getting ready…`)
  if (reading && (job.status === 'queued' || job.total === 0)) return i18n._(msg`Getting ready to read the chapters…`)
  const done = job.done
  const total = job.total
  return digestJobStepLine(job) ?? i18n._(msg`Reading chapters: ${done} of ${total}…`)
}

/** Still working: polls continue, and the page shows the progress line. */
export const DIGEST_JOB_ACTIVE: DigestJobStatus[] = ['queued', 'running', 'stopping']
/** Ended before every chapter was read: the page offers "Continue". */
export const DIGEST_JOB_RESUMABLE: DigestJobStatus[] = ['cancelled', 'stopped', 'interrupted']

/** "Continue" is offered only for a reading job that ended early: a resumable
 *  status and a step of null or 'reading'. A Write all that ended while
 *  merging or writing cannot be resumed as such, a merge-only job never. */
export function isResumable(job: Pick<DigestJob, 'status' | 'step' | 'total' | 'followups'>): boolean {
  if (!DIGEST_JOB_RESUMABLE.includes(job.status)) return false
  if (job.step != null && job.step !== 'reading') return false
  return !isMergeOnlyJob(job)
}

/** Up to this many chapters start without the confirmation dialog. */
export const CONFIRM_THRESHOLD = 20

/** What reading the missing chapters before the position costs and takes. */
export interface DigestEstimate {
  scope: DigestScope
  chapters: number
  calls: number
  words: number
  est_seconds: number
  est_cost_usd: number
  model: string
  /** null when no monthly limit applies. */
  remaining_budget_usd: number | null
  over_budget: boolean
  volumes: { book_id: number; title: string; chapters: number }[]
}

export interface DigestEstimates {
  book: DigestEstimate
  /** null when no earlier volume is marked read. */
  series: DigestEstimate | null
  chapter_idx: number
  chapter_title: string | null
}

export interface DigestStart {
  /** null (200) when nothing before the position is missing. */
  job: DigestJob | null
  estimate: DigestEstimate | null
  coverage: CompanionCoverage
  scope: DigestScope
  chapter_idx: number
  chapter_title: string | null
}

export interface Companion {
  available: boolean
  reason: CompanionReason | null
  features: { recap: boolean; recall: boolean; glossary: boolean; ask?: boolean }
  position: CompanionPosition | null
  chapters: number
  coverage: CompanionCoverage | null
  job: DigestJob | null
  /** The latest job for this book when none is active (for "Continue"). */
  last_job: DigestJob | null
  recap_cached: boolean
  glossary_count: number
  previously: { book_id: number; title: string } | null
  scope: CompanionScope | null
  /** True when the user has read earlier volumes in this book's window and
   *  recap + digests are on. Also set when `available` is false with reason
   *  `no_position`. Optional until every server sends it. */
  series_recaps?: boolean
  /** Upper-bound cost in USD of one Ask question on the current model.
   *  Optional until every server sends it. */
  ask_cost_hint_usd?: number
}

export interface Recap {
  /** Paragraphs separated by a blank line. */
  text: string
  chapter_idx: number
  /** null for a whole-book recap. */
  chapter_title: string | null
  whole_book: boolean
  cached: boolean
  /** Written while some chapters had no digest. */
  partial: boolean
  created_at: string | null
  scope: CompanionScope
  /** Volumes with chapters that had no digest when the recap was written.
   *  Empty (or absent on older servers) when nothing was missing. */
  missing_volumes?: RecapMissingVolume[]
}

export interface RecapMissingVolume {
  book_id: number
  title: string | null
  series_index: number | null
  /** Chapters with no digest. */
  missing: number
  /** Chapters the recap would have used from this volume. */
  needed: number
}

/** 202: chapters before the position are not read yet. Nothing is queued:
 *  start them with startDigest (asking first over CONFIRM_THRESHOLD). */
export interface RecapPending {
  needs_digest: true
  estimate: { book: DigestEstimate; series: DigestEstimate | null }
  coverage: CompanionCoverage
  /** The active job for this book, if one is already running. */
  job: DigestJob | null
  chapter_idx: number
  chapter_title: string | null
}

export interface GlossaryPlace {
  book_id: number
  book_title: string
  /** Volume number in the series, null for a standalone or unnumbered book.
   *  Optional until every server sends it. */
  series_index?: number | null
  chapter_idx: number
  chapter_title: string | null
}

export interface GlossaryEntry {
  name: string
  kind: 'person' | 'place' | 'term'
  line: string
  more: string
  /** Verbatim quote from the chapter the entry came from. */
  evidence: string
  first_seen: GlossaryPlace
  last_seen: GlossaryPlace
  /** Distinct chapters that listed the name (first sight plus updates). */
  chapters?: number
  // Entity glossary (v2). All optional: an older server sends none of them.
  entity_id?: number | null
  /** Other spellings and names learned along the way. */
  aliases?: string[]
  /** One stable line: who or what this is, never situational. */
  identity?: string
  status?: string | null
  /** Latest value per field inside the window, with where it was set. */
  facts?: Partial<Record<GlossaryFactField, GlossaryFact>>
  /** What they did, chapter by chapter, oldest first (capped). */
  timeline?: GlossaryTimelineItem[]
  /** True when earlier timeline lines were cut off by the cap. */
  timeline_truncated?: boolean
}

/** `powers_rank` is the legacy single field (servers before the rank/powers
 *  split); read as powers when `powers` is absent. */
export type GlossaryFactField =
  'appearance' | 'rank' | 'powers' | 'affiliations' | 'relationships' | 'status' | 'location'
  | 'powers_rank'

/** A place in the series: the shape facts, timeline lines and Ask quotes share. */
export interface SeriesPlace {
  book_id: number
  series_index: number | null
  chapter_idx: number
  /** Absent on glossary facts and on list timeline lines. */
  chapter_title?: string | null
}

export interface GlossaryFact extends SeriesPlace {
  value: string
  /** Earlier distinct values inside the window, newest first (at most 2; at most 5 for `powers`); only on the opened entry (getGlossaryEntry), never in the list. */
  history?: GlossaryFactHistoryItem[]
}

export interface GlossaryFactHistoryItem extends SeriesPlace {
  value: string
}

export interface GlossaryTimelineItem extends SeriesPlace {
  line: string
}

export interface AskQuote extends SeriesPlace {
  text: string
}

export interface AskResult {
  answer: string
  /** False: `answer` is the server's own "nothing found" wording. */
  found: boolean
  quotes: AskQuote[]
  chapters_read: number
  chapter_idx: number
  chapter_title: string | null
}

/** Longest question the server accepts (422 above it). */
export const ASK_MAX_CHARS = 300

export interface Glossary {
  book_id: number
  chapter_idx: number
  chapter_title: string | null
  /** In order of first appearance. */
  entries: GlossaryEntry[]
  count: number
  scope: CompanionScope
  coverage: CompanionCoverage
  job: DigestJob | null
  rev: string
}

export interface RecallResult {
  term: string
  chapter_idx: number
  chapter_title: string | null
  found: boolean
  /** True when the answer came from a live call on the current chapter. */
  live: boolean
  entry?: GlossaryEntry
  message?: string
}

export interface Previously {
  book_id: number
  title: string
  series_index: number | null
  text: string
  chapter_idx: number
  partial: boolean
  created_at: string | null
}

/** What the book page needs to show the companion. Never queues work. */
export function getCompanion(bookId: number): Promise<Companion> {
  return api.get<Companion>(`/ai/books/${bookId}/companion`)
}

export function isRecapPending(r: Recap | RecapPending): r is RecapPending {
  return !('text' in r)
}

/** The recap up to the position (or an earlier chapter). Cached ones are free;
 *  otherwise the server either writes it now or answers with a digest job. */
export function requestRecap(
  bookId: number,
  opts: { chapter?: number; partial?: boolean } = {},
): Promise<Recap | RecapPending> {
  return api.post<Recap | RecapPending>(`/ai/books/${bookId}/recap`, opts)
}

/** Drop the cached recap ("Ask again"). */
export function clearRecap(bookId: number, chapter?: number): Promise<{ deleted: boolean; chapter_idx: number }> {
  const qs = chapter != null ? `?chapter=${chapter}` : ''
  return api.delete<{ deleted: boolean; chapter_idx: number }>(`/ai/books/${bookId}/recap${qs}`)
}

/** The glossary up to the position, from storage. Read-only: never queues. */
export function getGlossary(bookId: number, opts: { chapter?: number } = {}): Promise<Glossary> {
  const qs = opts.chapter != null ? `?chapter=${opts.chapter}` : ''
  return api.get<Glossary>(`/ai/books/${bookId}/glossary${qs}`)
}

/** Both scopes' estimate for the missing chapters. Never calls a model. */
export function getDigestEstimate(bookId: number): Promise<DigestEstimates> {
  return api.get<DigestEstimates>(`/ai/books/${bookId}/digest/estimate`)
}

/** One glossary entry in full (timeline up to 200 lines, titled places).
 *  By entity id, or by name and kind when the entry has no entity id. */
export function getGlossaryEntry(
  bookId: number,
  key: { entity_id: number } | { name: string; kind: GlossaryEntry['kind'] },
): Promise<{ entry: GlossaryEntry }> {
  const qs = 'entity_id' in key
    ? `entity_id=${key.entity_id}`
    : `name=${encodeURIComponent(key.name)}&kind=${encodeURIComponent(key.kind)}`
  return api.get<{ entry: GlossaryEntry }>(`/ai/books/${bookId}/glossary/entry?${qs}`)
}

/** True for the 409 the server sends while a question or volume recap is
 *  already in flight for that book (the error message is its `detail`). */
export function isAlreadyRunning(e: unknown): boolean {
  return e instanceof Error && /already (running|being)/i.test(e.message)
}

/** Start reading the missing chapters before the position. Rejects with the
 *  server's message when over a monthly limit (409) or without a key. */
export function startDigest(bookId: number, scope: DigestScope): Promise<DigestStart> {
  return api.post<DigestStart>(`/ai/books/${bookId}/digest`, { scope })
}

/** "Read again" (admins): what reading every chapter of each scope again
 *  costs, and what it deletes first (for everyone). Each scope counts every
 *  chapter, since all of them will be missing. */
export interface RereadEstimate {
  book: DigestEstimate | null
  series: DigestEstimate | null
  chapter_idx: number
  chapter_title: string | null
  deletes: {
    book: { digests: number; recaps: number }
    /** Counted over the whole series, not only the volumes in your scope. */
    series: { volumes: number; digests: number; entities: number; recaps: number }
  }
}

export function getRereadEstimate(bookId: number): Promise<RereadEstimate> {
  return api.get<RereadEstimate>(`/ai/books/${bookId}/reread/estimate`)
}

/** Delete the stored notes of the scope and read it again; the 202 body is
 *  startDigest's. Rejects with 403 (not an admin) or 409 (budget, already
 *  running); the budget is checked before anything is deleted. */
export function startReread(bookId: number, scope: DigestScope): Promise<DigestStart> {
  return api.post<DigestStart>(`/ai/books/${bookId}/reread`, { scope })
}

/** One term: from the glossary when it is there, else one live call. */
export function recallTerm(bookId: number, term: string, chapter?: number): Promise<RecallResult> {
  return api.post<RecallResult>(`/ai/books/${bookId}/recall`, chapter != null ? { term, chapter } : { term })
}

/** One question about what was read, inside the window. One paid call each;
 *  rejects with the server's message (409 over a monthly limit, 422 empty or
 *  too long). */
export function askBook(bookId: number, question: string): Promise<AskResult> {
  return api.post<AskResult>(`/ai/books/${bookId}/ask`, { question })
}

/** The previous volume's whole-book recap. Rejects (404) when there is none. */
export function getPreviously(bookId: number): Promise<Previously> {
  return api.get<Previously>(`/ai/books/${bookId}/previously`)
}

// ── recap per volume ─────────────────────────────────────────────────────────
// One recap per earlier volume in the reader's window, written from that
// volume's chapter summaries only. User-independent like the digests.

export interface VolumeRecap {
  book_id: number
  title: string
  series_index: number | null
  chapters: number
  /** ready: written from every chapter; partial: written while some chapters
   *  were unread; missing: not written yet. */
  status: 'ready' | 'partial' | 'missing'
  digested: number
  needed: number
  text: string | null
  created_at: string | null
  /** Chapters of this volume with no digest yet. */
  missing_chapters: number
}

export interface RecapVolumes {
  /** False when this user may not start writes (no key, feature off). */
  can_write: boolean
  /** Earlier volumes in reading order; the open book is `current`. */
  volumes: VolumeRecap[]
  current: {
    book_id: number
    title: string
    series_index: number | null
    has_position: boolean
    recap_cached: boolean
  }
  /** What "Write all" would do now: `count` missing or stale recaps and a
   *  conservative cost for them; `reading` the chapters of read earlier
   *  volumes that are read first (0 when none); `job` the active run for this
   *  book (reconnects after a reload), `last` the most recent one. */
  write_all: WriteAll
}

export interface WriteAll {
  count: number
  est_cost_usd: number
  reading: { chapters: number; volumes: number; est_cost_usd: number; est_seconds: number }
  /** Merging duplicate glossary entries after the reading; `pairs` 0 means
   *  nothing to merge. Older servers leave it out. */
  merging?: { pairs: number; calls: number; est_cost_usd: number }
  job: DigestJob | null
  last: DigestJob | null
  /** Left of the user's monthly limit, when the server sends it (the dialog
   *  then warns before a run that would pass it). */
  remaining_budget_usd?: number | null
}

/** 202: the volume still has unread chapters. Nothing is queued: start them
 *  with startDigest(volumeId, 'book'), then ask for the recap again. */
export interface VolumeRecapPending {
  needs_digest: true
  /** One scope, for that volume. */
  estimate: DigestEstimate
  coverage: CompanionCoverage
}

export function isVolumeRecapPending(r: VolumeRecap | VolumeRecapPending): r is VolumeRecapPending {
  return 'needs_digest' in r && r.needs_digest === true
}

/** The earlier volumes' recaps, from storage. Never writes. */
export function getRecapVolumes(bookId: number): Promise<RecapVolumes> {
  return api.get<RecapVolumes>(`/ai/books/${bookId}/recap/volumes`)
}

/** Write one volume's recap (force: rewrite a partial one; again: rewrite a
 *  complete one in place, "Ask again"). */
export function requestVolumeRecap(
  bookId: number, volumeId: number, opts: { force?: boolean; again?: boolean } = {},
): Promise<VolumeRecap | VolumeRecapPending> {
  return api.post<VolumeRecap | VolumeRecapPending>(`/ai/books/${bookId}/recap/volumes/${volumeId}`, opts)
}

/** "Write all": one background job that reads the missing chapters of the
 *  read earlier volumes, merges duplicate glossary entries, then writes every
 *  missing or stale volume recap. Poll it with getDigestJob. Rejects with 409
 *  (budget, already running). */
export function startWriteAll(bookId: number): Promise<{ job: DigestJob }> {
  return api.post<{ job: DigestJob }>(`/ai/books/${bookId}/recap/volumes`, {})
}

/** "Merge duplicates": a job with the merging step only. Rejects with 409
 *  while a job for the book is active. */
export function startReconcile(bookId: number): Promise<{ job: DigestJob }> {
  return api.post<{ job: DigestJob }>(`/ai/books/${bookId}/reconcile`, {})
}

export function getDigestJob(jobId: string): Promise<DigestJob> {
  return api.get<DigestJob>(`/ai/digest-jobs/${jobId}`)
}

export function cancelDigestJob(jobId: string): Promise<DigestJob> {
  return api.post<DigestJob>(`/ai/digest-jobs/${jobId}/cancel`, {})
}

// ── Companion overview (Settings > AI > Companion) ───────────────────────────

/** A volume's recap: "none" when the volume has no chapter text. */
export type OverviewRecapState = 'ready' | 'partial' | 'missing' | 'none'

export interface OverviewVolume {
  book_id: number
  title: string
  series_index: number | null
  chapters: number
  /** Stored digests that feed the glossary (failed placeholders not counted). */
  digested: number
  failed: number
  recap: OverviewRecapState
  last_digest_at: string | null
}

export type OverviewJobKind = 'read' | 'merge' | 'reread' | 'write_all'

export interface OverviewSeries {
  /** The series key; a standalone book is `book:{id}`. */
  key: string
  name: string
  author: string | null
  /** Admins only: users with a read status or position on a volume. */
  readers: { id: number; username: string }[]
  volumes: OverviewVolume[]
  people: number
  places: number
  terms: number
  last_merge: { finished_at: string | null; candidates: number; merged: number } | null
  last_job: { id: string; kind: OverviewJobKind; status: DigestJobStatus; finished_at: string | null; cost_usd: number } | null
  spend_month_usd: number
  /** The viewer's live job on a volume of the series. */
  active_job: DigestJob | null
  /** The volume the row's actions start on. */
  anchor_book_id: number
  /** False when the viewer has neither read nor holds a position on any
   *  volume (an admin seeing someone else's series): actions are hidden.
   *  Older servers leave it out; treat missing as true. */
  in_scope?: boolean
}

export function getCompanionOverview(): Promise<{ series: OverviewSeries[] }> {
  return api.get<{ series: OverviewSeries[] }>('/ai/companion/overview')
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
