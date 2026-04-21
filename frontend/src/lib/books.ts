export interface BookTag {
  id: number
  tag: string
  source: string | null
}

export interface BookFile {
  id: number
  format: string
  filename: string | null
  file_size: number | null
  added_at: string
}

export interface Book {
  id: number
  title: string
  subtitle: string | null
  author: string | null
  series: string | null
  series_index: number | null
  year: number | null
  language: string | null
  status: string
  content_type: string
  cover_path: string | null
  added_at: string
  files: BookFile[]
  tags: BookTag[]
  library_ids: number[]
  book_type_id: number | null
}

export interface BookDetail extends Book {
  isbn: string | null
  publisher: string | null
  description: string | null
  content_hash: string | null
  added_by: number | null
  updated_at: string
}

export interface MetadataCandidate {
  source: string
  source_id: string
  title: string
  author: string | null
  description: string | null
  cover_url: string | null
  publisher: string | null
  year: number | null
  page_count: number | null
  isbn: string | null
  language: string | null
  tags: string[]
  series: string | null
  series_index: number | null
}

export interface ScanResult {
  found: number
  added: number
  skipped: number
  duplicates: number
  errors: number
  error_details: string[]
}

export function formatBytes(bytes: number | null): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export interface BookType {
  id: number
  slug: string
  label: string
  icon: string | null
  color: string | null
  sort_order: number
  library_id: number | null
}

export interface Library {
  id: number
  name: string
  icon: string | null
  sort_order: number
  book_count: number
  is_public: boolean
  assigned_user_ids: number[]
}

export interface SavedFilter {
  id: number
  name: string
  icon: string | null
  params: Record<string, string | null>
  sort_order: number
}

export type ReadingStatus = 'unread' | 'reading' | 'read'

export interface Arc {
  id: number
  series_name: string
  name: string
  start_index: number
  end_index: number
  description: string | null
  created_at: string
  updated_at: string
}

export type SeriesStatus = 'ongoing' | 'finished' | 'hiatus' | 'unknown'

export interface SeriesMeta {
  series_name: string
  status: SeriesStatus
}

export interface BookStatus {
  book_id: number
  status: ReadingStatus
  progress_pct: number | null
  updated_at: string | null
}
