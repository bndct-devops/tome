import { api } from './api'

export type WordCountStatus = 'idle' | 'running' | 'done' | 'cancelled' | 'error'

export interface WordCountIssue {
  path: string
  reason: string
}

export interface WordCountState {
  status: WordCountStatus
  started_at: number | null
  finished_at: number | null
  triggered_by: string | null
  total_files: number
  total_bytes: number
  done_files: number
  done_bytes: number
  counted: number
  failed: number
  words_total: number
  current_file: string | null
  issues: WordCountIssue[]
  error: string | null
  elapsed_seconds: number
  eta_seconds: number | null
}

export interface WordCountPreflight {
  epub_total: number
  already_counted: number
  pending: number
  pending_bytes: number
}

export const getWordCountStatus = () => api.get<WordCountState>('/admin/word-count/status')
export const getWordCountPreflight = () => api.get<WordCountPreflight>('/admin/word-count/preflight')
export const startWordCount = () => api.post<WordCountState>('/admin/word-count/start')
export const cancelWordCount = () => api.post<WordCountState>('/admin/word-count/cancel')
export const dismissWordCount = () => api.post<WordCountState>('/admin/word-count/dismiss')
