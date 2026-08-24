import { useEffect, useState, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  RefreshCw, Loader2, BookOpen, Check, X, Trash2, Search, Layers,
  ChevronRight, ChevronDown, ArrowLeft, FolderOpen,
  Inbox, Zap, Eye, HelpCircle, Library as LibraryIcon,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { DOCS, docsLink } from '@/lib/docs'
import { api } from '@/lib/api'
import { useBookTypes } from '@/lib/bookTypes'
import { useToast } from '@/contexts/ToastContext'
import { Trans, useLingui, Plural } from '@lingui/react/macro'
import { plural, msg } from '@lingui/core/macro'
import type { MessageDescriptor } from '@lingui/core'
import type { MetadataCandidate, BookType } from '@/lib/books'
import { formatBytes } from '@/lib/books'
import { cn } from '@/lib/utils'
import { useShiftSelect } from '@/lib/useShiftSelect'
import { CoverImage } from '@/components/CoverImage'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UnreviewedBook {
  id: number
  title: string
  author: string | null
  series: string | null
  series_index: number | null
  cover_path: string | null
  added_at: string
  format: string | null
}

interface BinderyItem {
  path: string
  filename: string
  size: number
  modified: number
  format: string
  content_type: string
  series: string | null
  series_index: number | null
  title: string
  author: string | null
  folder: string | null
}

interface BinderyAcceptFile {
  path: string
  title: string
  author?: string | null
  series?: string | null
  series_index?: number | null
  content_type?: string
  book_type_id?: number | null
  description?: string | null
  publisher?: string | null
  year?: number | null
  isbn?: string | null
  language?: string | null
  cover_url?: string | null
  tags?: string[]
  library_ids?: number[]
}

type View = 'list' | 'review'

interface SeriesGroupFile {
  path: string
  filename: string
  title: string
  series_index: number | null
  content_type: string
  format: string
  size: number
  dest_preview: string
}

interface SeriesGroup {
  key: string
  series: string | null
  author: string | null
  book_type_id: number | null
  language: string | null
  library_ids: number[]
  library_match: { series: string; volume_count: number; from_reviewed: boolean } | null
  files: SeriesGroupFile[]
}

// Per-item form state used in review
interface ItemForm {
  title: string
  author: string
  series: string
  series_index: string
  content_type: string
  book_type_id: string
  description: string
  publisher: string
  year: string
  isbn: string
  language: string
  tags: string
  cover_url: string
  library_ids: number[]
}

function itemToForm(item: BinderyItem, bookTypes: BookType[]): ItemForm {
  const isCbx = ['cbz', 'cbr'].includes(item.format.toLowerCase())
  const defaultTypeId = isCbx
    ? String(bookTypes.find(bt => bt.slug === 'manga' || bt.slug === 'comics')?.id ?? '')
    : ''
  return {
    title: item.title ?? '',
    author: item.author ?? '',
    series: item.series ?? '',
    series_index: item.series_index != null ? String(item.series_index) : '',
    content_type: item.content_type ?? 'volume',
    book_type_id: defaultTypeId,
    description: '',
    publisher: '',
    year: '',
    isbn: '',
    language: '',
    tags: '',
    cover_url: '',
    library_ids: [],
  }
}

function formToAcceptFile(path: string, form: ItemForm): BinderyAcceptFile {
  return {
    path,
    title: form.title,
    author: form.author || null,
    series: form.series || null,
    series_index: form.series_index ? parseFloat(form.series_index) : null,
    content_type: form.content_type,
    book_type_id: form.book_type_id ? parseInt(form.book_type_id, 10) : null,
    description: form.description || null,
    publisher: form.publisher || null,
    year: form.year ? parseInt(form.year, 10) : null,
    isbn: form.isbn || null,
    language: form.language || null,
    cover_url: form.cover_url || null,
    tags: form.tags
      ? form.tags.split(',').map(t => t.trim()).filter(Boolean)
      : [],
    library_ids: form.library_ids,
  }
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function FormatBadge({ format }: { format: string }) {
  const f = format.toLowerCase()
  /* eslint-disable lingui/no-unlocalized-strings -- Tailwind class map */
  const colorMap: Record<string, string> = {
    epub: 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20',
    pdf: 'bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20',
    cbz: 'bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20',
    cbr: 'bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20',
    mobi: 'bg-green-500/10 text-green-600 dark:text-green-400 border-green-500/20',
  }
  const cls = colorMap[f] ?? 'bg-muted text-muted-foreground border-border'
  /* eslint-enable lingui/no-unlocalized-strings */
  return (
    <span className={cn('text-[10px] font-medium px-1.5 py-0.5 rounded border uppercase tracking-wide', cls)}>
      {format}
    </span>
  )
}

const CONTENT_TYPE_LABELS: Record<string, MessageDescriptor> = { volume: msg`volume`, chapter: msg`chapter` }

function ContentTypeBadge({ type }: { type: string }) {
  const { i18n } = useLingui()
  const isChapter = type === 'chapter'
  return (
    <span className={cn(
      'text-[10px] font-medium px-1.5 py-0.5 rounded border capitalize',
      isChapter
        ? 'bg-warning/10 text-warning border-warning/20'
        : 'bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20'
    )}>
      {CONTENT_TYPE_LABELS[type] ? i18n._(CONTENT_TYPE_LABELS[type]) : type}
    </span>
  )
}

function SourceBadge({ source }: { source: string }) {
  /* eslint-disable lingui/no-unlocalized-strings -- provider names */
  const label =
    source === 'hardcover' ? 'Hardcover'
    : source === 'google_books' ? 'Google'
    : 'OpenLib'
  /* eslint-enable lingui/no-unlocalized-strings */
  return (
    <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground bg-muted">
      {label}
    </span>
  )
}

// Shared input class (matches MetadataFetchModal)
const INPUT_CLS =
  'flex h-9 w-full rounded-md border border-border bg-transparent px-3 py-1 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring'
const TEXTAREA_CLS =
  'w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring resize-none'
const LABEL_CLS = 'block text-xs font-medium text-muted-foreground mb-1'

// ---------------------------------------------------------------------------
// LibrariesSelect — same idea as the Book page's "Libraries" dropdown (#103)
// ---------------------------------------------------------------------------

interface LibraryOption {
  id: number
  name: string
  can_edit: boolean
}

function LibrariesSelect({ value, onChange, libraries, className, placeholder }: {
  value: number[]
  onChange: (ids: number[]) => void
  libraries: LibraryOption[]
  className?: string
  placeholder?: string
}) {
  const { t } = useLingui()
  const resolvedPlaceholder = placeholder ?? t`No libraries`
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const editable = libraries.filter(l => l.can_edit)
  if (editable.length === 0) return null
  const toggle = (id: number) =>
    onChange(value.includes(id) ? value.filter(v => v !== id) : [...value, id])

  return (
    <div className={cn('relative', className)} ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={cn(
          INPUT_CLS,
          'flex items-center gap-2 cursor-pointer text-left',
          value.length > 0 ? 'text-foreground' : 'text-muted-foreground'
        )}
      >
        <LibraryIcon className="w-3.5 h-3.5 shrink-0" />
        <span className="flex-1 truncate">
          {value.length > 0
            ? editable.filter(l => value.includes(l.id)).map(l => l.name).join(', ')
            : resolvedPlaceholder}
        </span>
        <ChevronDown className="w-3.5 h-3.5 shrink-0 opacity-60" />
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-full mt-1 z-40 bg-card border border-border rounded-xl shadow-xl py-1 max-h-56 overflow-y-auto">
          {editable.map(lib => {
            const selected = value.includes(lib.id)
            return (
              <button
                key={lib.id}
                type="button"
                onClick={() => toggle(lib.id)}
                className="flex items-center gap-2 w-full px-3 py-2 text-sm hover:bg-muted transition-colors text-left"
              >
                <LibraryIcon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                <span className="flex-1 truncate">{lib.name}</span>
                <Check className={cn('w-3.5 h-3.5 shrink-0', selected ? 'text-primary' : 'text-muted-foreground/30')} />
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SelectMenu — custom single-select matching LibrariesSelect (native <select>
// menus clash visually and their chevron never lines up with ours)
// ---------------------------------------------------------------------------

function SelectMenu({ value, onChange, options, className }: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const current = options.find(o => o.value === value)

  return (
    <div className={cn('relative', className)} ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={cn(INPUT_CLS, 'flex items-center gap-2 cursor-pointer text-left')}
      >
        <span className="flex-1 truncate">{current?.label ?? value}</span>
        <ChevronDown className="w-3.5 h-3.5 shrink-0 opacity-60" />
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-full mt-1 z-40 bg-card border border-border rounded-xl shadow-xl py-1 max-h-56 overflow-y-auto">
          {options.map(opt => (
            <button
              key={opt.value}
              type="button"
              onClick={() => { onChange(opt.value); setOpen(false) }}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm hover:bg-muted transition-colors text-left"
            >
              <span className="flex-1 truncate">{opt.label}</span>
              <Check className={cn('w-3.5 h-3.5 shrink-0', opt.value === value ? 'text-primary' : 'text-transparent')} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// MetadataForm
// ---------------------------------------------------------------------------

interface MetadataFormProps {
  form: ItemForm
  onChange: (field: keyof ItemForm, value: string | number[]) => void
  bookTypes: BookType[]
  libraries: LibraryOption[]
}

function MetadataForm({ form, onChange, bookTypes, libraries }: MetadataFormProps) {
  const { t } = useLingui()
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3">
        <div>
          <label className={LABEL_CLS}><Trans>Title *</Trans></label>
          <input
            className={INPUT_CLS}
            value={form.title}
            onChange={e => onChange('title', e.target.value)}
            placeholder={t`Book title`}
          />
        </div>
        <div>
          <label className={LABEL_CLS}><Trans>Author</Trans></label>
          <input
            className={INPUT_CLS}
            value={form.author}
            onChange={e => onChange('author', e.target.value)}
            placeholder={t`Author name`}
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={LABEL_CLS}><Trans>Series</Trans></label>
          <input
            className={INPUT_CLS}
            value={form.series}
            onChange={e => onChange('series', e.target.value)}
            placeholder={t`Series name`}
          />
        </div>
        <div>
          <label className={LABEL_CLS}><Trans>Series #</Trans></label>
          <input
            className={INPUT_CLS}
            type="number"
            step="0.1"
            value={form.series_index}
            onChange={e => onChange('series_index', e.target.value)}
            placeholder={t`e.g. 1`}
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={LABEL_CLS}><Trans>Content Type</Trans></label>
          <SelectMenu
            value={form.content_type}
            onChange={v => onChange('content_type', v)}
            options={[{ value: 'volume', label: t`Volume` }, { value: 'chapter', label: t`Chapter` }]}
          />
        </div>
        <div>
          <label className={LABEL_CLS}><Trans>Book Type</Trans></label>
          <SelectMenu
            value={form.book_type_id}
            onChange={v => onChange('book_type_id', v)}
            options={[{ value: '', label: t`No type` },
                      ...bookTypes.map(bt => ({ value: String(bt.id), label: bt.label }))]}
          />
        </div>
      </div>
      <div>
        <label className={LABEL_CLS}><Trans>Libraries</Trans></label>
        <LibrariesSelect
          value={form.library_ids}
          onChange={ids => onChange('library_ids', ids)}
          libraries={libraries}
        />
        <p className="mt-1 text-[11px] text-muted-foreground">
          <Trans>The book type's own library is always added automatically.</Trans>
        </p>
      </div>
      <div>
        <label className={LABEL_CLS}><Trans>Description</Trans></label>
        <textarea
          className={TEXTAREA_CLS}
          rows={3}
          value={form.description}
          onChange={e => onChange('description', e.target.value)}
          placeholder={t`Synopsis or description`}
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={LABEL_CLS}><Trans>Publisher</Trans></label>
          <input
            className={INPUT_CLS}
            value={form.publisher}
            onChange={e => onChange('publisher', e.target.value)}
            placeholder={t`Publisher`}
          />
        </div>
        <div>
          <label className={LABEL_CLS}><Trans>Year</Trans></label>
          <input
            className={INPUT_CLS}
            type="number"
            value={form.year}
            onChange={e => onChange('year', e.target.value)}
            placeholder={t`e.g. 2023`}
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={LABEL_CLS}>ISBN</label>
          <input
            className={INPUT_CLS}
            value={form.isbn}
            onChange={e => onChange('isbn', e.target.value)}
            placeholder="ISBN"
          />
        </div>
        <div>
          <label className={LABEL_CLS}><Trans>Language</Trans></label>
          <input
            className={INPUT_CLS}
            value={form.language}
            onChange={e => onChange('language', e.target.value)}
            placeholder={t`e.g. en`}
          />
        </div>
      </div>
      <div>
        <label className={LABEL_CLS}><Trans>Tags (comma-separated)</Trans></label>
        <input
          className={INPUT_CLS}
          value={form.tags}
          onChange={e => onChange('tags', e.target.value)}
          placeholder={t`fantasy, action, romance`}
        />
      </div>
      <div>
        <label className={LABEL_CLS}><Trans>Cover URL</Trans></label>
        <input
          className={INPUT_CLS}
          value={form.cover_url}
          onChange={e => onChange('cover_url', e.target.value)}
          placeholder="https://..."
        />
        {form.cover_url && (
          <img
            src={form.cover_url}
            alt={t`Cover preview`}
            className="mt-2 h-24 w-16 object-cover rounded border border-border"
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// CandidatePanel
// ---------------------------------------------------------------------------

interface CandidatePanelProps {
  candidates: MetadataCandidate[]
  loading: boolean
  searchQuery: string
  onSearchQueryChange: (q: string) => void
  onSearch: (q: string) => void
  onSelect: (c: MetadataCandidate) => void
  appliedId: string | null
  targetLabel?: string
}

function CandidatePanel({ candidates, loading, searchQuery, onSearchQueryChange, onSearch, onSelect, appliedId, targetLabel }: CandidatePanelProps) {
  const { t } = useLingui()
  return (
    <div className="flex flex-col h-full">
      <div className="mb-3">
        <span className="text-sm font-medium"><Trans>Metadata Suggestions</Trans></span>
        {targetLabel && (
          <span className="block text-xs text-muted-foreground truncate">
            <Trans>for {targetLabel} — click a file to switch</Trans>
          </span>
        )}
      </div>
      <form
        className="flex gap-1.5 mb-3"
        onSubmit={e => { e.preventDefault(); onSearch(searchQuery) }}
      >
        <input
          className="flex-1 h-8 rounded-md border border-border bg-transparent px-2.5 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          value={searchQuery}
          onChange={e => onSearchQueryChange(e.target.value)}
          placeholder={t`Search query...`}
        />
        <button
          type="submit"
          disabled={loading || !searchQuery.trim()}
          className="shrink-0 h-8 w-8 rounded-md border border-border bg-card flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
        </button>
      </form>
      {loading && (
        <div className="flex-1 flex items-center justify-center py-12">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      )}
      {!loading && candidates.length === 0 && (
        <div className="flex-1 flex flex-col items-center justify-center py-12 text-center">
          <BookOpen className="h-8 w-8 text-muted-foreground/40 mb-2" />
          <p className="text-sm text-muted-foreground"><Trans>No metadata found</Trans></p>
          <p className="text-xs text-muted-foreground/60 mt-1"><Trans>Try editing the title or series fields</Trans></p>
        </div>
      )}
      {!loading && candidates.length > 0 && (
        <div className="space-y-2 overflow-y-auto flex-1">
          <p className="text-xs text-muted-foreground mb-1">
            <Plural value={candidates.length} one="# result — click to fill form" other="# results — click to fill form" />
          </p>
          {candidates.map(c => (
            <button
              key={`${c.source}-${c.source_id}`}
              className={cn(
                'w-full text-left rounded-lg border border-border bg-card p-3 hover:bg-accent transition-colors flex gap-3',
                appliedId === `${c.source}-${c.source_id}` && 'ring-2 ring-primary/50 bg-primary/5'
              )}
              onClick={() => onSelect(c)}
            >
              {c.cover_url ? (
                <img src={c.cover_url} alt="" className="h-20 w-14 object-cover rounded shrink-0" />
              ) : (
                <div className="h-20 w-14 rounded bg-muted flex items-center justify-center shrink-0">
                  <BookOpen className="h-5 w-5 text-muted-foreground" />
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-sm line-clamp-2">{c.title}</p>
                  <SourceBadge source={c.source} />
                </div>
                {c.author && <p className="text-xs text-muted-foreground mt-0.5">{c.author}</p>}
                {c.year && <p className="text-xs text-muted-foreground">{c.year}</p>}
                {c.description && (
                  <p className="text-xs text-muted-foreground line-clamp-2 mt-1">{c.description}</p>
                )}
                {c.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    {c.tags.slice(0, 3).map(t => (
                      <span key={t} className="text-[10px] bg-muted rounded px-1">{t}</span>
                    ))}
                    {c.tags.length > 3 && (
                      <span className="text-[10px] text-muted-foreground">+{c.tags.length - 3}</span>
                    )}
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ConfirmDialog
// ---------------------------------------------------------------------------

interface ConfirmDialogProps {
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  destructive?: boolean
  onConfirm: () => void
  onCancel: () => void
}

function SeriesGroupCard({ group, bookTypes, libraries, busy, onAccept }: {
  group: SeriesGroup
  bookTypes: BookType[]
  libraries: LibraryOption[]
  busy: boolean
  onAccept: (form: { series: string; author: string; book_type_id: string; library_ids: number[] }) => void
}) {
  const { t } = useLingui()
  const [series, setSeries] = useState(group.series ?? '')
  const [author, setAuthor] = useState(group.author ?? '')
  const [typeId, setTypeId] = useState(group.book_type_id != null ? String(group.book_type_id) : '')
  const [libraryIds, setLibraryIds] = useState<number[]>(group.library_ids)
  const destFolder = group.files[0]?.dest_preview.split('/').slice(0, -1).join('/')

  return (
    <div className="rounded-xl border border-border bg-card p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <Layers className="w-4 h-4 text-primary/70 shrink-0" />
            <span className="text-sm font-semibold text-foreground truncate">
              {series || t`Ungrouped files`}
            </span>
            {group.library_match && (
              <span
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-success/10 text-success text-[10px] font-medium"
                title={(() => { const name = group.library_match.series; return t`Matches "${name}" already in your library — name, author, type and libraries adopted from it` })()}
              >
                <Check className="w-3 h-3" />
                {plural(group.library_match.volume_count, { one: 'In library · # vol', other: 'In library · # vols' })}
                {group.library_match.from_reviewed ? t` · reviewed` : ''}
              </span>
            )}
            {!group.library_match && group.series && (
              <span className="px-2 py-0.5 rounded-full bg-muted text-muted-foreground text-[10px] font-medium">
                <Trans>New series</Trans>
              </span>
            )}
          </div>
          {destFolder && (
            <p className="mt-1 text-[11px] text-muted-foreground font-mono truncate" title={destFolder}>
              → {destFolder}/
            </p>
          )}
        </div>
        <button
          onClick={() => onAccept({ series, author, book_type_id: typeId, library_ids: libraryIds })}
          disabled={busy}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all shrink-0"
        >
          {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
          <Plural value={group.files.length} one="Accept # volume" other="Accept # volumes" />
        </button>
      </div>

      {/* One identity decision for the whole group */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
        <input
          value={series}
          onChange={e => setSeries(e.target.value)}
          placeholder={t`Series`}
          className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring"
        />
        <input
          value={author}
          onChange={e => setAuthor(e.target.value)}
          placeholder={t`Author`}
          className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring"
        />
        <SelectMenu
          value={typeId}
          onChange={setTypeId}
          options={[{ value: '', label: t`No type` }, ...bookTypes.map(bt => ({ value: String(bt.id), label: bt.label }))]}
          className="[&>button]:py-1.5 [&>button]:text-xs"
        />
        <LibrariesSelect
          value={libraryIds}
          onChange={setLibraryIds}
          libraries={libraries}
          className="[&>button]:py-1.5 [&>button]:text-xs"
        />
      </div>

      {/* Per-volume confirmation strip */}
      <div className="flex flex-wrap gap-1.5">
        {group.files.map(f => (
          <span
            key={f.path}
            title={`${f.filename} → ${f.dest_preview}`}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-muted/60 text-[11px] text-foreground"
          >
            {(() => { const idx = f.series_index; return idx != null ? t`Vol ${idx}` : (f.title || f.filename) })()}
            <span className="text-muted-foreground uppercase text-[9px]">{f.format}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

function ConfirmDialog({ open, title, message, confirmLabel, destructive, onConfirm, onCancel }: ConfirmDialogProps) {
  const { t } = useLingui()
  if (!open) return null
  const resolvedConfirmLabel = confirmLabel ?? t`Confirm`
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onCancel} />
      <div className="relative z-10 bg-background border border-border rounded-2xl shadow-xl shadow-accent-soft w-full max-w-sm mx-4 p-6">
        <h2 className="text-base font-semibold mb-2">{title}</h2>
        <p className="text-sm text-muted-foreground mb-6">{message}</p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <Trans>Cancel</Trans>
          </button>
          <button
            onClick={onConfirm}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-all',
              destructive
                ? 'bg-destructive text-destructive-foreground hover:opacity-90'
                : 'bg-primary text-primary-foreground hover:opacity-90'
            )}
          >
            {resolvedConfirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function BinderyPage() {
  const { t } = useLingui()
  const { toast } = useToast()
  const bookTypes = useBookTypes()
  const navigate = useNavigate()
  const [libraries, setLibraries] = useState<LibraryOption[]>([])
  useEffect(() => {
    api.get<LibraryOption[]>('/libraries').then(setLibraries).catch(() => {})
  }, [])

  // List view state
  const [view, setView] = useState<View>('list')
  const [items, setItems] = useState<BinderyItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [collapsedFolders, setCollapsedFolders] = useState<Set<string>>(new Set())

  // Unreviewed (auto-imported) books
  const [unreviewed, setUnreviewed] = useState<UnreviewedBook[]>([])
  const [unreviewedLoading, setUnreviewedLoading] = useState(false)
  const [confirmRejectUnreviewed, setConfirmRejectUnreviewed] = useState<number | null>(null)
  const [rejectingUnreviewed, setRejectingUnreviewed] = useState(false)
  const [reviewingAll, setReviewingAll] = useState(false)

  // Reject confirmation
  const [confirmReject, setConfirmReject] = useState<string[] | null>(null)
  const [rejecting, setRejecting] = useState(false)

  // Review state
  const [reviewItems, setReviewItems] = useState<BinderyItem[]>([])
  const [reviewIndex, setReviewIndex] = useState(0)
  const [formData, setFormData] = useState<Record<string, ItemForm>>({})
  const [candidates, setCandidates] = useState<MetadataCandidate[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [fetchingMeta, setFetchingMeta] = useState(false)
  const [accepting, setAccepting] = useState(false)

  // Accept flash state
  const [acceptFlash, setAcceptFlash] = useState(false)

  // Applied candidate flash
  const [appliedId, setAppliedId] = useState<string | null>(null)

  // View transition
  const [viewTransition, setViewTransition] = useState(false)

  // Quick accept progress
  type QAStatus = 'pending' | 'fetching' | 'accepting' | 'done' | 'error'
  interface QAProgress {
    total: number
    items: { path: string; title: string; status: QAStatus; error?: string }[]
  }
  const [qaProgress, setQaProgress] = useState<QAProgress | null>(null)
  const [qaModalOpen, setQaModalOpen] = useState(false)

  // Batch shared fields (apply-to-all)
  const [batchSeries, setBatchSeries] = useState('')
  const [batchAuthor, setBatchAuthor] = useState('')
  const [batchBookTypeId, setBatchBookTypeId] = useState('')
  const [batchLibraryIds, setBatchLibraryIds] = useState<number[]>([])
  // Libraries picked in the LIST toolbar — consumed by the next Quick Accept
  // or Review run, then reset so the next batch doesn't inherit it silently
  const [listLibraryIds, setListLibraryIds] = useState<number[]>([])
  const [matchingAll, setMatchingAll] = useState<number | null>(null)
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set())
  // Which file the suggestions panel searches/applies for in batch review —
  // expanding a row retargets the panel (it used to silently hit file #1 only)
  const [suggestPath, setSuggestPath] = useState<string | null>(null)

  // ---------------------------------------------------------------------------
  // View transition helper
  // ---------------------------------------------------------------------------

  function transitionTo(newView: View) {
    setViewTransition(true)
    setTimeout(() => {
      setView(newView)
      setTimeout(() => setViewTransition(false), 20)
    }, 150)
  }

  // ---------------------------------------------------------------------------
  // Fetch helpers
  // ---------------------------------------------------------------------------

  const fetchUnreviewed = useCallback(async () => {
    setUnreviewedLoading(true)
    try {
      const data = await api.get<UnreviewedBook[]>('/bindery/unreviewed')
      setUnreviewed(data)
    } catch {
      // Non-fatal — endpoint may not exist yet or returns empty
      setUnreviewed([])
    } finally {
      setUnreviewedLoading(false)
    }
  }, [])

  const [groups, setGroups] = useState<SeriesGroup[]>([])
  const fetchItems = useCallback(async () => {
    setLoading(true)
    try {
      const [data, grouped] = await Promise.all([
        api.get<BinderyItem[]>('/bindery'),
        api.get<SeriesGroup[]>('/bindery/groups').catch(() => [] as SeriesGroup[]),
      ])
      setItems(data)
      setGroups(grouped)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Failed to load bindery`)
    } finally {
      setLoading(false)
    }
  }, [toast])

  const fetchAll = useCallback(async () => {
    await Promise.all([fetchItems(), fetchUnreviewed()])
  }, [fetchItems, fetchUnreviewed])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  // ---------------------------------------------------------------------------
  // Series-group accept — one identity decision covers every volume in a group
  // ---------------------------------------------------------------------------

  const [acceptingGroup, setAcceptingGroup] = useState<string | null>(null)

  async function acceptGroup(
    group: SeriesGroup,
    form: { series: string; author: string; book_type_id: string; library_ids: number[] },
  ) {
    setAcceptingGroup(group.key)
    try {
      const files: BinderyAcceptFile[] = group.files.map(f => ({
        path: f.path,
        title: f.title || f.filename.replace(/\.[^.]+$/, ''),
        author: form.author || null,
        series: form.series || null,
        series_index: f.series_index,
        content_type: f.content_type,
        book_type_id: form.book_type_id ? parseInt(form.book_type_id, 10) : null,
        language: group.language,
        library_ids: form.library_ids,
      }))
      const res = await api.post<{ accepted: unknown[]; errors: { path: string; error: string }[] }>(
        '/bindery/accept', { files })
      if (res.errors?.length) {
        { const failedN = res.errors.length, totalN = files.length, firstErr = res.errors[0].error; toast.error(t`${failedN} of ${totalN} failed — ${firstErr}`) }
      } else {
        {
          const n = files.length, seriesName = form.series
          toast.success(seriesName
            ? plural(n, { one: `Accepted # volume of ${seriesName}`, other: `Accepted # volumes of ${seriesName}` })
            : plural(n, { one: 'Accepted # volume', other: 'Accepted # volumes' }))
        }
      }
      await fetchAll()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Accept failed`)
    } finally {
      setAcceptingGroup(null)
    }
  }

  // ---------------------------------------------------------------------------
  // Grouping
  // ---------------------------------------------------------------------------

  const folders = Array.from(
    new Set(items.map(i => i.folder).filter((f): f is string => f !== null))
  ).sort()
  const ungrouped = items.filter(i => i.folder === null)

  // ---------------------------------------------------------------------------
  // Selection
  // ---------------------------------------------------------------------------

  const { handleToggle } = useShiftSelect(items.map(i => i.path))

  function toggleSelect(path: string, shiftKey: boolean) {
    setSelected(prev => {
      const index = items.findIndex(i => i.path === path)
      return handleToggle(path, index, shiftKey, prev)
    })
  }

  function selectAll() {
    setSelected(new Set(items.map(i => i.path)))
  }

  function clearSelection() {
    setSelected(new Set())
  }

  function toggleFolder(folder: string) {
    setCollapsedFolders(prev => {
      const next = new Set(prev)
      if (next.has(folder)) next.delete(folder)
      else next.add(folder)
      return next
    })
  }

  function selectFolder(folder: string) {
    const folderPaths = items.filter(i => i.folder === folder).map(i => i.path)
    setSelected(prev => {
      const next = new Set(prev)
      const allSelected = folderPaths.every(p => next.has(p))
      if (allSelected) folderPaths.forEach(p => next.delete(p))
      else folderPaths.forEach(p => next.add(p))
      return next
    })
  }

  // ---------------------------------------------------------------------------
  // Enter review
  // ---------------------------------------------------------------------------

  function enterReview(paths: string[]) {
    const toReview = paths.map(p => items.find(i => i.path === p)).filter((i): i is BinderyItem => !!i)
    if (toReview.length === 0) return

    const forms: Record<string, ItemForm> = {}
    for (const item of toReview) {
      forms[item.path] = itemToForm(item, bookTypes)
      forms[item.path].library_ids = [...listLibraryIds]
    }

    setReviewItems(toReview)
    setReviewIndex(0)
    setFormData(forms)
    setCandidates([])
    setSearchQuery('')
    setBatchSeries(toReview[0]?.series ?? '')
    setBatchAuthor('')
    setBatchBookTypeId(forms[toReview[0]?.path ?? '']?.book_type_id ?? '')
    setBatchLibraryIds([...listLibraryIds])
    setListLibraryIds([])
    setExpandedRows(new Set())
    setSuggestPath(toReview[0]?.path ?? null)
    transitionTo('review')

    // Fetch metadata for the first item
    fetchPreview(toReview[0])
  }

  async function fetchPreview(item: BinderyItem, queryOverride?: string, autoApply = true) {
    const itemPath = item.path
    setFetchingMeta(true)
    setCandidates([])
    try {
      const body: { path: string; query?: string } = { path: itemPath }
      if (queryOverride !== undefined) body.query = queryOverride
      const result = await api.post<{ file_metadata: Record<string, unknown>; candidates: MetadataCandidate[]; query_used: string }>(
        '/bindery/preview',
        body
      )
      setCandidates(result.candidates)
      setSearchQuery(result.query_used)
      // Embedded metadata (calibre:series etc.) beats the filename guess —
      // same precedence as auto-import. Candidate apply still wins on top.
      if (autoApply && !queryOverride) {
        applyEmbedded(result.file_metadata, itemPath)
        if (result.candidates.length > 0) {
          applyCandidate(result.candidates[0], itemPath)
        }
      }
    } catch {
      // Non-fatal
    } finally {
      setFetchingMeta(false)
    }
  }

  // Trust-the-matcher tier for batch review: fetch and apply the best
  // candidate to EVERY file's form, then the user eyeballs the rows and
  // Accept-Alls. Explicit button — it does overwrite hand edits.
  async function matchAll() {
    setMatchingAll(0)
    let matched = 0
    try {
      for (let i = 0; i < reviewItems.length; i++) {
        const item = reviewItems[i]
        setMatchingAll(i + 1)
        try {
          const result = await api.post<{ file_metadata: Record<string, unknown>; candidates: MetadataCandidate[] }>(
            '/bindery/preview',
            { path: item.path }
          )
          applyEmbedded(result.file_metadata, item.path)
          if (result.candidates.length > 0) {
            applyCandidate(result.candidates[0], item.path)
            matched++
          }
        } catch {
          // Non-fatal — leave this file's parsed data in place
        }
      }
    } finally {
      setMatchingAll(null)
    }
    if (matched === reviewItems.length) {
      toast.success(t`Best match applied to all ${matched} files`)
    } else {
      { const totalN = reviewItems.length; toast.success(t`Best match applied to ${matched} of ${totalN} files — no match for the rest`) }
    }
  }

  function manualSearch(query: string) {
    const item = (reviewItems.length > 1 && suggestPath
      ? reviewItems.find(i => i.path === suggestPath)
      : reviewItems[reviewIndex ?? 0]) ?? reviewItems[reviewIndex ?? 0]
    if (!item) return
    fetchPreview(item, query)
  }

  // ---------------------------------------------------------------------------
  // Candidate selection
  // ---------------------------------------------------------------------------

  // Merge metadata embedded in the file itself (calibre:series, OPF dc:*)
  // into a form row. Embedded values win over the filename parse — the
  // filename is a guess, the file is ground truth. Candidates applied
  // afterwards still override (unchanged semantics).
  function applyEmbedded(meta: Record<string, unknown>, targetPath: string) {
    if (!meta) return
    const str = (k: string): string | null => {
      const v = meta[k]
      return typeof v === 'string' && v.trim() ? v : null
    }
    const num = (k: string): number | null => {
      const v = meta[k]
      return typeof v === 'number' && Number.isFinite(v) ? v : null
    }
    setFormData(prev => {
      const existing = prev[targetPath]
      if (!existing) return prev
      return {
        ...prev,
        [targetPath]: {
          ...existing,
          title: str('title') || existing.title,
          author: str('author') || existing.author,
          series: str('series') || existing.series,
          series_index: num('series_index') != null ? String(num('series_index')) : existing.series_index,
          description: str('description') || existing.description,
          publisher: str('publisher') || existing.publisher,
          year: num('year') != null ? String(num('year')) : existing.year,
          isbn: str('isbn') || existing.isbn,
          language: str('language') || existing.language,
        },
      }
    })
    const embeddedSeries = str('series')
    if (embeddedSeries) setBatchSeries(prev => prev || embeddedSeries)
    const embeddedAuthor = str('author')
    if (embeddedAuthor) setBatchAuthor(prev => prev || embeddedAuthor)
  }

  function applyCandidate(c: MetadataCandidate, targetPath?: string) {
    setFormData(prev => {
      // Determine which path to apply to: explicit target, or the sole key in formData
      const path = targetPath ?? Object.keys(prev)[0]
      if (!path) return prev
      const existing = prev[path]
      if (!existing) return prev

      return {
        ...prev,
        [path]: {
          ...existing,
          title: c.title || existing.title,
          author: c.author || existing.author,
          series: c.series || existing.series,
          series_index: c.series_index != null ? String(c.series_index) : existing.series_index,
          description: c.description || existing.description,
          publisher: c.publisher || existing.publisher,
          year: c.year != null ? String(c.year) : existing.year,
          isbn: c.isbn || existing.isbn,
          language: c.language || existing.language,
          tags: c.tags.length > 0 ? c.tags.join(', ') : existing.tags,
          cover_url: c.cover_url || existing.cover_url,
        },
      }
    })
    // Also fill batch shared fields when applicable
    if (c.author) setBatchAuthor(prev => prev || c.author!)
    if (c.series) setBatchSeries(prev => prev || c.series!)
  }

  // Wrapper that triggers flash on candidate apply
  function applyCandidateWithFlash(c: MetadataCandidate, targetPath?: string) {
    applyCandidate(c, targetPath)
    const id = `${c.source}-${c.source_id}`
    setAppliedId(id)
    setTimeout(() => setAppliedId(null), 600)
  }

  // ---------------------------------------------------------------------------
  // Form field change
  // ---------------------------------------------------------------------------

  function updateForm(path: string, field: keyof ItemForm, value: string | number[]) {
    setFormData(prev => ({
      ...prev,
      [path]: { ...prev[path], [field]: value },
    }))
  }

  // Apply batch fields to all items
  function applyBatchSeries() {
    setFormData(prev => {
      const next = { ...prev }
      for (const item of reviewItems) {
        next[item.path] = { ...next[item.path], series: batchSeries }
      }
      return next
    })
    { const n = reviewItems.length; toast.success(t`Series applied to ${n} files`) }
  }

  function applyBatchAuthor() {
    setFormData(prev => {
      const next = { ...prev }
      for (const item of reviewItems) {
        next[item.path] = { ...next[item.path], author: batchAuthor }
      }
      return next
    })
    { const n = reviewItems.length; toast.success(t`Author applied to ${n} files`) }
  }

  function applyBatchBookType() {
    setFormData(prev => {
      const next = { ...prev }
      for (const item of reviewItems) {
        next[item.path] = { ...next[item.path], book_type_id: batchBookTypeId }
      }
      return next
    })
    { const n = reviewItems.length; toast.success(t`Book type applied to ${n} files`) }
  }

  function applyBatchLibraries() {
    setFormData(prev => {
      const next = { ...prev }
      for (const item of reviewItems) {
        next[item.path] = { ...next[item.path], library_ids: [...batchLibraryIds] }
      }
      return next
    })
    { const n = reviewItems.length; toast.success(t`Libraries applied to ${n} files`) }
  }

  // ---------------------------------------------------------------------------
  // Accept
  // ---------------------------------------------------------------------------

  async function acceptItem(path: string) {
    const form = formData[path]
    if (!form) return
    if (!form.title.trim()) {
      toast.error(t`Title is required`)
      return
    }
    setAccepting(true)
    try {
      const result = await api.post<{ accepted: { book_id: number; title: string }[]; errors: { path: string; error: string }[] }>(
        '/bindery/accept',
        { files: [formToAcceptFile(path, form)] }
      )
      if (result.errors.length > 0) {
        toast.error(result.errors[0].error)
      } else {
        { const title = result.accepted[0]?.title ?? t`book`; toast.success(t`Accepted: ${title}`) }
        setAcceptFlash(true)
        setTimeout(() => {
          setAcceptFlash(false)
          // Remove from list and move to next review item
          setItems(prev => prev.filter(i => i.path !== path))
          advanceReview(path)
        }, 400)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Accept failed`)
    } finally {
      setAccepting(false)
    }
  }

  async function acceptAll() {
    const files: BinderyAcceptFile[] = reviewItems.map(item => {
      const form = formData[item.path]
      return formToAcceptFile(item.path, form ?? itemToForm(item, bookTypes))
    })
    const invalid = files.find(f => !f.title?.trim())
    if (invalid) {
      toast.error(t`All items must have a title`)
      return
    }
    setAccepting(true)
    try {
      const result = await api.post<{ accepted: { book_id: number; title: string }[]; errors: { path: string; error: string }[] }>(
        '/bindery/accept',
        { files }
      )
      if (result.errors.length > 0) {
        const msgs = result.errors.map(e => e.error).join(', ')
        toast.error(`${result.errors.length} error(s): ${msgs}`)
      }
      if (result.accepted.length > 0) {
        toast.success(plural(result.accepted.length, { one: 'Accepted # book', other: 'Accepted # books' }))
        const acceptedPaths = new Set(result.accepted.map((_, i) => files[i]?.path).filter(Boolean))
        setItems(prev => prev.filter(i => !acceptedPaths.has(i.path)))
        transitionTo('list')
        setSelected(new Set())
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Accept failed`)
    } finally {
      setAccepting(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Quick accept (no review)
  // ---------------------------------------------------------------------------

  async function quickAccept(paths: string[]) {
    setAccepting(true)

    // Initialize progress
    const progressItems = paths.map(path => {
      const item = items.find(i => i.path === path)
      return { path, title: item?.title ?? path, status: 'pending' as QAStatus }
    })
    setQaProgress({ total: paths.length, items: progressItems })

    const updateStatus = (path: string, status: QAStatus, error?: string) => {
      setQaProgress(prev => {
        if (!prev) return prev
        return {
          ...prev,
          items: prev.items.map(i => i.path === path ? { ...i, status, error } : i),
        }
      })
    }

    const acceptedPaths: string[] = []

    for (const path of paths) {
      const item = items.find(i => i.path === path)
      if (!item) continue

      const form = itemToForm(item, bookTypes)
      form.library_ids = [...listLibraryIds]

      // Fetch metadata
      updateStatus(path, 'fetching')
      try {
        const result = await api.post<{ file_metadata: Record<string, unknown>; candidates: MetadataCandidate[] }>(
          '/bindery/preview',
          { path }
        )
        if (result.candidates.length > 0) {
          const c = result.candidates[0]
          if (c.title) form.title = c.title
          if (c.author) form.author = c.author
          if (c.series) form.series = c.series
          if (c.series_index != null) form.series_index = String(c.series_index)
          if (c.description) form.description = c.description
          if (c.publisher) form.publisher = c.publisher
          if (c.year != null) form.year = String(c.year)
          if (c.isbn) form.isbn = c.isbn
          if (c.language) form.language = c.language
          if (c.tags.length > 0) form.tags = c.tags.join(', ')
          if (c.cover_url) form.cover_url = c.cover_url
        }
      } catch {
        // Non-fatal — accept with parsed data only
      }

      // Accept
      updateStatus(path, 'accepting')
      try {
        const result = await api.post<{ accepted: { book_id: number; title: string }[]; errors: { path: string; error: string }[] }>(
          '/bindery/accept',
          { files: [formToAcceptFile(path, form)] }
        )
        if (result.accepted.length > 0) {
          updateStatus(path, 'done')
          acceptedPaths.push(path)
        } else if (result.errors.length > 0) {
          updateStatus(path, 'error', result.errors[0].error)
        }
      } catch (err) {
        updateStatus(path, 'error', err instanceof Error ? err.message : t`Failed`)
      }
    }

    // Clean up
    setListLibraryIds([])
    if (acceptedPaths.length > 0) {
      toast.success(plural(acceptedPaths.length, { one: 'Accepted # book', other: 'Accepted # books' }))
      setItems(prev => prev.filter(i => !acceptedPaths.includes(i.path)))
      setSelected(new Set())
    }
    setAccepting(false)
  }

  // ---------------------------------------------------------------------------
  // Reject
  // ---------------------------------------------------------------------------

  async function doReject(paths: string[]) {
    setRejecting(true)
    try {
      const result = await api.post<{ rejected: number; errors: { path: string; error: string }[] }>(
        '/bindery/reject',
        { paths }
      )
      if (result.rejected > 0) {
        toast.success(plural(result.rejected, { one: 'Rejected # file', other: 'Rejected # files' }))
        setItems(prev => prev.filter(i => !paths.includes(i.path)))
        setSelected(prev => {
          const next = new Set(prev)
          paths.forEach(p => next.delete(p))
          return next
        })
      }
      if (result.errors.length > 0) {
        toast.error(`${result.errors.length} error(s)`)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Reject failed`)
    } finally {
      setRejecting(false)
      setConfirmReject(null)
    }
  }

  // ---------------------------------------------------------------------------
  // Unreviewed book actions
  // ---------------------------------------------------------------------------

  async function acceptUnreviewed(bookId: number) {
    try {
      await api.put(`/bindery/review/${bookId}`)
      setUnreviewed(prev => prev.filter(b => b.id !== bookId))
      toast.success(t`Book accepted`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Accept failed`)
    }
  }

  async function doRejectUnreviewed(bookId: number) {
    setRejectingUnreviewed(true)
    try {
      await api.delete(`/bindery/reject/${bookId}`)
      setUnreviewed(prev => prev.filter(b => b.id !== bookId))
      toast.success(t`Book rejected and removed`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Reject failed`)
    } finally {
      setRejectingUnreviewed(false)
      setConfirmRejectUnreviewed(null)
    }
  }

  async function reviewAll() {
    setReviewingAll(true)
    try {
      await api.put('/bindery/review-all')
      setUnreviewed([])
      toast.success(t`All imported books accepted`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Review all failed`)
    } finally {
      setReviewingAll(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Review navigation
  // ---------------------------------------------------------------------------

  function advanceReview(acceptedPath: string) {
    const remaining = reviewItems.filter(i => i.path !== acceptedPath)
    if (remaining.length === 0) {
      transitionTo('list')
      setSelected(new Set())
      return
    }
    setReviewItems(remaining)
    setReviewIndex(0)
    fetchPreview(remaining[0])
  }

  function skipItem() {
    if (reviewItems.length === 1) {
      transitionTo('list')
      return
    }
    const nextIndex = reviewIndex + 1
    if (nextIndex >= reviewItems.length) {
      transitionTo('list')
      return
    }
    setReviewIndex(nextIndex)
    fetchPreview(reviewItems[nextIndex])
  }

  function rejectCurrentAndAdvance() {
    const item = reviewItems[reviewIndex]
    if (!item) return
    setConfirmReject([item.path])
  }

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  function renderRow(item: BinderyItem, indent = false, rowIndex = 0) {
    const isSelected = selected.has(item.path)
    return (
      <div
        key={item.path}
        className={cn(
          'flex items-center gap-3 px-4 py-2.5 border-b border-border/60 hover:bg-muted/40 transition-colors group animate-fade-in-up',
          isSelected && 'bg-primary/5',
          indent && 'pl-8'
        )}
        style={{ animationDelay: `${rowIndex * 40}ms` }}
      >
        <input
          type="checkbox"
          checked={isSelected}
          onChange={e => toggleSelect(item.path, e.nativeEvent instanceof MouseEvent ? e.nativeEvent.shiftKey : false)}
          onClick={e => e.stopPropagation()}
          className="shrink-0 rounded border-border cursor-pointer"
        />
        <button
          className="flex-1 min-w-0 flex items-center gap-3 text-left"
          onClick={() => enterReview([item.path])}
        >
          <div className="flex-1 min-w-0">
            <span className="text-sm font-medium truncate block">{item.title}</span>
            <span className="text-[11px] text-muted-foreground/60 truncate block">{item.filename}</span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <FormatBadge format={item.format} />
            <ContentTypeBadge type={item.content_type} />
            <span className="text-xs text-muted-foreground w-24 text-right hidden sm:block truncate">
              {item.series ?? <span className="text-muted-foreground/40">—</span>}
            </span>
            <span className="text-xs text-muted-foreground w-16 text-right hidden md:block">
              {formatBytes(item.size)}
            </span>
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/40 group-hover:text-muted-foreground transition-colors" />
          </div>
        </button>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // LIST VIEW
  // ---------------------------------------------------------------------------

  if (view === 'list') {
    const selectedArr = Array.from(selected)
    const hasSelection = selectedArr.length > 0

    // Compute cumulative row indices across folders and ungrouped
    let rowCounter = 0

    return (
      <AppShell
        onUploaded={fetchAll}
        actions={
          <button
            onClick={fetchAll}
            disabled={loading || unreviewedLoading}
            title={t`Rescan the bindery folder`}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', (loading || unreviewedLoading) && 'animate-spin')} />
            {/* Label stays at every size — collapsed to a bare icon it sat next
                to the global sync indicator as two identical circular-arrow
                glyphs (UX sweep finding). */}
            <span><Trans>Refresh</Trans></span>
          </button>
        }
      >
      <div className={cn('flex flex-col h-full transition-opacity duration-150', viewTransition ? 'opacity-0' : 'opacity-100')}>
        {/* eslint-disable-next-line lingui/no-unlocalized-strings -- CSS */}
        <style>{`
          @keyframes fade-in-up {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
          }
          .animate-fade-in-up { animation: fade-in-up 0.3s ease-out both; }
          @keyframes gentle-pulse {
            0%, 100% { opacity: 0.3; transform: scale(1); }
            50% { opacity: 0.45; transform: scale(1.05); }
          }
        `}</style>
        {/* Header */}
        <div className="sticky top-0 z-20 bg-background/80 backdrop-blur-sm border-b border-border px-4 pt-4 pb-4">
          <div className="flex items-center gap-3">
            <h1 className="font-display text-xl text-foreground"><Trans>Bindery</Trans></h1>
            <p className="text-xs text-muted-foreground hidden md:block">
              {(() => {
                if (loading || unreviewedLoading) return t`Loading…`
                const inc = items.length, imp = unreviewed.length
                if (imp > 0) return t`${imp} imported + ${inc} incoming`
                return plural(inc, { one: '# file waiting for review', other: '# files waiting for review' })
              })()}
            </p>
            <a
              href={docsLink(DOCS.bindery)}
              target="_blank"
              rel="noopener noreferrer"
              title={t`Bindery flow explained — open docs`}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            >
              <HelpCircle className="w-3.5 h-3.5" />
            </a>
          </div>

          {/* Toolbar — always visible when there are items */}
          {items.length > 0 && (
            <div className="mt-3 flex items-center gap-2 flex-wrap">
              <button
                onClick={hasSelection ? clearSelection : selectAll}
                className="px-2.5 py-1.5 rounded-lg text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted border border-border transition-colors"
              >
                {(() => { const n = selectedArr.length; return hasSelection ? t`${n} selected — Clear` : t`Select All` })()}
              </button>

              {hasSelection && (
                <>
                  <div className="h-4 w-px bg-border" />
                  <button
                    onClick={() => enterReview(selectedArr)}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all"
                  >
                    <Eye className="h-3 w-3" /> <Trans>Review</Trans>
                  </button>
                  <button
                    onClick={() => quickAccept(selectedArr)}
                    disabled={accepting}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border text-foreground hover:bg-muted disabled:opacity-50 transition-all"
                  >
                    {accepting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
                    <Trans>Quick Accept</Trans>
                  </button>
                  <button
                    onClick={() => setConfirmReject(selectedArr)}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-destructive hover:bg-destructive/10 border border-destructive/30 transition-all"
                  >
                    <Trash2 className="h-3 w-3" /> <Trans>Reject</Trans>
                  </button>
                  {libraries.some(l => l.can_edit) && (
                    <>
                      <div className="h-4 w-px bg-border" />
                      <LibrariesSelect
                        value={listLibraryIds}
                        onChange={setListLibraryIds}
                        libraries={libraries}
                        className="w-56 [&>button]:py-1.5 [&>button]:text-xs"
                        placeholder={t`Add to libraries…`}
                      />
                    </>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        {/* Series review — one decision per series, volumes confirmed as chips.
            Cards render for real series groups; loose singletons stay in the
            flat file list below. */}
        {!loading && groups.some(g => g.files.length > 1 || g.library_match) && (
          <div className="px-4 py-3 border-b border-border bg-muted/20 flex flex-col gap-3">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              <Trans>Series review</Trans>
            </p>
            {groups
              .filter(g => g.files.length > 1 || g.library_match)
              .map(g => (
                <SeriesGroupCard
                  key={g.key}
                  group={g}
                  bookTypes={bookTypes}
                  libraries={libraries}
                  busy={acceptingGroup === g.key}
                  onAccept={form => acceptGroup(g, form)}
                />
              ))}
          </div>
        )}

        {/* Column headers */}
        {items.length > 0 && (
          <div className="flex items-center gap-3 px-4 py-2 bg-muted/30 border-b border-border text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
            <span className="w-4 shrink-0" />
            <span className="flex-1"><Trans>Title</Trans></span>
            <div className="flex items-center gap-2 shrink-0">
              <span className="w-10"><Trans>Format</Trans></span>
              <span className="w-14"><Trans>Type</Trans></span>
              <span className="w-24 text-right hidden sm:block"><Trans>Series</Trans></span>
              <span className="w-16 text-right hidden md:block"><Trans>Size</Trans></span>
              <span className="w-3.5" />
            </div>
          </div>
        )}

        {/* Content */}
        <div className="flex-1 flex flex-col">
          {loading && (
            <div className="flex flex-1 items-center justify-center py-24">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {!loading && !unreviewedLoading && items.length === 0 && unreviewed.length === 0 && (
            <div className="flex flex-1 flex-col items-center justify-center text-center">
              <Inbox
                className="h-12 w-12 text-muted-foreground/30 mb-3"
                style={{ animation: 'gentle-pulse 3s ease-in-out infinite' }}
              />
              <p className="text-base font-medium text-muted-foreground"><Trans>No files in the bindery</Trans></p>
              <p className="text-sm text-muted-foreground/60 mt-1">
                <Trans>Drop files in the incoming folder to get started</Trans>
              </p>
            </div>
          )}

          {/* Recently Imported — unreviewed auto-imported books */}
          {!unreviewedLoading && unreviewed.length > 0 && (
            <div className="border-b border-border">
              {/* Section header */}
              <div className="flex items-center justify-between px-4 py-2.5 bg-muted/30 border-b border-border">
                <div className="flex items-center gap-2">
                  <span className="font-display text-sm text-foreground"><Trans>Recently Imported</Trans></span>
                  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-primary/10 text-primary">
                    {unreviewed.length}
                  </span>
                </div>
                <button
                  onClick={reviewAll}
                  disabled={reviewingAll}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
                >
                  {reviewingAll ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                  <Trans>Accept All</Trans>
                </button>
              </div>
              {/* Cards grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 p-4">
                {unreviewed.map(book => {
                  const format = book.format?.toUpperCase() ?? ''
                  const addedAt = new Date(book.added_at)
                  const diffMs = Date.now() - addedAt.getTime()
                  const diffHrs = Math.floor(diffMs / 1000 / 60 / 60)
                  const diffMins = Math.floor(diffMs / 1000 / 60)
                  const diffDays = Math.floor(diffHrs / 24)
                  const timeAgo = diffHrs >= 24
                    ? t`${diffDays}d ago`
                    : diffHrs >= 1
                      ? t`${diffHrs}h ago`
                      : diffMins >= 1
                        ? t`${diffMins}m ago`
                        : t`just now`

                  return (
                    <div
                      key={book.id}
                      className="flex items-center gap-3 rounded-lg border border-border bg-card p-3 cursor-pointer hover:bg-accent/50 transition-colors group animate-fade-in-up"
                      onClick={() => navigate(`/books/${book.id}`)}
                    >
                      {/* Cover */}
                      <div className="relative shrink-0 h-16 w-11 rounded overflow-hidden bg-muted">
                        <CoverImage
                          src={book.cover_path ? `/api/books/${book.id}/cover` : null}
                          alt=""
                          iconClassName="h-5 w-5"
                        />
                      </div>
                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">{book.title}</p>
                        {book.author && (
                          <p className="text-xs text-muted-foreground truncate">{book.author}</p>
                        )}
                        <p className="text-[10px] text-muted-foreground/60 mt-0.5"><Trans>Added {timeAgo}</Trans></p>
                      </div>
                      {/* Badges + Actions */}
                      <div className="shrink-0 flex flex-col items-end gap-1.5">
                        {format && <FormatBadge format={format} />}
                        <div className="flex items-center gap-1">
                          <button
                            onClick={e => { e.stopPropagation(); acceptUnreviewed(book.id) }}
                            className="p-1.5 rounded-md text-primary hover:bg-primary/10 transition-colors"
                            title={t`Accept`}
                          >
                            <Check className="h-3.5 w-3.5" />
                          </button>
                          <button
                            onClick={e => { e.stopPropagation(); setConfirmRejectUnreviewed(book.id) }}
                            className="p-1.5 rounded-md text-destructive hover:bg-destructive/10 transition-colors"
                            title={t`Reject`}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {!loading && items.length > 0 && (
            <>
              {/* Grouped by folder */}
              {folders.map(folder => {
                const folderItems = items.filter(i => i.folder === folder)
                const isCollapsed = collapsedFolders.has(folder)
                const allFolderSelected = folderItems.every(i => selected.has(i.path))
                const folderHeaderIndex = rowCounter
                rowCounter += 1
                return (
                  <div key={folder}>
                    {/* Folder header */}
                    <div
                      className="flex items-center gap-2 px-4 py-2 bg-muted/50 border-b border-border animate-fade-in-up"
                      style={{ animationDelay: `${folderHeaderIndex * 40}ms` }}
                    >
                      <input
                        type="checkbox"
                        checked={allFolderSelected}
                        onChange={() => selectFolder(folder)}
                        className="shrink-0 rounded border-border cursor-pointer"
                      />
                      <button
                        className="flex items-center gap-1.5 flex-1 text-left"
                        onClick={() => toggleFolder(folder)}
                      >
                        {isCollapsed
                          ? <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                          : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />}
                        <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
                        <span className="text-sm font-medium">{folder}</span>
                        <span className="text-xs text-muted-foreground">({folderItems.length})</span>
                      </button>
                    </div>
                    {!isCollapsed && folderItems.map(item => {
                      const idx = rowCounter
                      rowCounter += 1
                      return renderRow(item, true, idx)
                    })}
                  </div>
                )
              })}

              {/* Ungrouped items */}
              {ungrouped.length > 0 && (
                <>
                  {folders.length > 0 && (
                    <div className="flex items-center gap-2 px-4 py-2 bg-muted/50 border-b border-border">
                      <span className="w-4 shrink-0" />
                      <span className="text-sm font-medium text-muted-foreground"><Trans>Other files</Trans></span>
                      <span className="text-xs text-muted-foreground">({ungrouped.length})</span>
                    </div>
                  )}
                  {ungrouped.map(item => {
                    const idx = rowCounter
                    rowCounter += 1
                    return renderRow(item, false, idx)
                  })}
                </>
              )}
            </>
          )}
        </div>

        {/* Quick accept progress bar — fixed at bottom, above keyboard shortcut FAB (z-40) */}
        {qaProgress && (
          <div
            className="fixed bottom-0 left-0 right-0 z-50 bg-card border-t border-border pl-6 pr-16 py-3 cursor-pointer hover:bg-muted/50 transition-colors"
            onClick={() => setQaModalOpen(true)}
          >
            {(() => {
              const done = qaProgress.items.filter(i => i.status === 'done').length
              const errors = qaProgress.items.filter(i => i.status === 'error').length
              const total = qaProgress.total
              return (
                <>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs font-medium">
                      {t`Quick Accept: ${done}/${total} done` + (errors > 0 ? t`, ${errors} failed` : '')}
                    </span>
                    {qaProgress.items.every(i => i.status === 'done' || i.status === 'error') ? (
                      <button
                        onClick={e => { e.stopPropagation(); setQaProgress(null); setQaModalOpen(false) }}
                        className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                      >
                        <Trans>Dismiss</Trans>
                      </button>
                    ) : (
                      <span className="text-[10px] text-muted-foreground"><Trans>Click for details</Trans></span>
                    )}
                  </div>
                  <div className="h-1.5 bg-muted rounded-full overflow-hidden flex">
                    {done > 0 && (
                      <div
                        className="h-full bg-primary transition-all duration-300"
                        style={{ width: `${(done / total) * 100}%` }}
                      />
                    )}
                    {errors > 0 && (
                      <div
                        className="h-full bg-destructive transition-all duration-300"
                        style={{ width: `${(errors / total) * 100}%` }}
                      />
                    )}
                  </div>
                </>
              )
            })()}
          </div>
        )}

        {/* Quick accept detail modal */}
        {qaModalOpen && qaProgress && (
          <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={() => setQaModalOpen(false)} />
            <div className="relative z-10 bg-background border border-border rounded-2xl shadow-xl shadow-accent-soft w-full max-w-md mx-4 max-h-[70vh] flex flex-col">
              <div className="flex items-center justify-between px-5 py-3 border-b border-border">
                <span className="text-sm font-semibold"><Trans>Quick Accept Progress</Trans></span>
                <button onClick={() => setQaModalOpen(false)} className="text-muted-foreground hover:text-foreground transition-colors">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto px-5 py-3 space-y-1.5">
                {qaProgress.items.map(item => (
                  <div key={item.path} className="flex items-center gap-2 py-1.5">
                    <div className="shrink-0 w-4 h-4 flex items-center justify-center">
                      {item.status === 'done' ? (
                        <Check className="h-3.5 w-3.5 text-primary" />
                      ) : item.status === 'error' ? (
                        <X className="h-3.5 w-3.5 text-destructive" />
                      ) : item.status === 'pending' ? (
                        <div className="h-2 w-2 rounded-full bg-muted-foreground/30" />
                      ) : (
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <span className="text-sm truncate block">{item.title}</span>
                      {item.error && (
                        <span className="text-[11px] text-destructive">{item.error}</span>
                      )}
                    </div>
                    <span className="text-[10px] text-muted-foreground shrink-0 capitalize">
                      {item.status === 'fetching' ? t`Fetching metadata...` : item.status === 'accepting' ? t`Importing…` : ''}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Confirm reject dialog for raw files */}
        <ConfirmDialog
          open={confirmReject !== null}
          title={t`Reject files`}
          message={plural(confirmReject?.length ?? 0, { one: 'Permanently delete # file from the bindery? This cannot be undone.', other: 'Permanently delete # files from the bindery? This cannot be undone.' })}
          confirmLabel={rejecting ? t`Deleting…` : t`Delete`}
          destructive
          onConfirm={() => confirmReject && doReject(confirmReject)}
          onCancel={() => setConfirmReject(null)}
        />

        {/* Confirm reject dialog for unreviewed (imported) books */}
        <ConfirmDialog
          open={confirmRejectUnreviewed !== null}
          title={t`Reject imported book`}
          message={t`Delete this book from the library? The file will be removed and this cannot be undone.`}
          confirmLabel={rejectingUnreviewed ? t`Deleting…` : t`Delete`}
          destructive
          onConfirm={() => confirmRejectUnreviewed !== null && doRejectUnreviewed(confirmRejectUnreviewed)}
          onCancel={() => setConfirmRejectUnreviewed(null)}
        />
      </div>
      </AppShell>
    )
  }

  // ---------------------------------------------------------------------------
  // REVIEW VIEW
  // ---------------------------------------------------------------------------

  const isBatch = reviewItems.length > 1
  const currentItem = isBatch ? null : reviewItems[reviewIndex]
  const currentForm = currentItem ? formData[currentItem.path] : null

  return (
    <AppShell onUploaded={fetchAll}>
    <div className={cn('flex flex-col h-full transition-opacity duration-150', viewTransition ? 'opacity-0' : 'opacity-100')}>
      {/* Header */}
      <div className="sticky top-0 z-10 bg-background border-b border-border px-6 pt-5 pb-3">
        <div className="flex items-center gap-3">
          <button
            onClick={() => transitionTo('list')}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="h-4 w-4" /> <Trans>Back to list</Trans>
          </button>
          <span className="text-muted-foreground/40">/</span>
          <span className="text-sm font-medium">
            {isBatch
              ? (() => { const n = reviewItems.length; return t`Reviewing ${n} files` })()
              : currentItem?.filename ?? ''}
          </span>
        </div>
      </div>

      {/* Single file review */}
      {!isBatch && currentItem && currentForm && (
        <div className="flex-1 flex flex-col lg:flex-row gap-0 overflow-hidden">
          {/* Left: Form */}
          <div
            className={cn(
              'lg:w-3/5 overflow-y-auto p-6 border-r border-border transition-colors duration-300',
              acceptFlash && 'bg-primary/5'
            )}
          >
            {/* File info */}
            <div className="flex items-center gap-2 mb-5 p-3 rounded-lg bg-muted/40 border border-border">
              <div className="flex items-center gap-2 min-w-0 flex-1">
                <FormatBadge format={currentItem.format} />
                <ContentTypeBadge type={currentForm.content_type} />
                <span className="text-xs text-muted-foreground truncate">{currentItem.path}</span>
              </div>
              <span className="text-xs text-muted-foreground shrink-0">{formatBytes(currentItem.size)}</span>
            </div>

            <MetadataForm
              form={currentForm}
              onChange={(field, value) => updateForm(currentItem.path, field, value)}
              bookTypes={bookTypes}
              libraries={libraries}
            />

            {/* Action bar */}
            <div className="flex items-center gap-2 mt-6 pt-4 border-t border-border">
              <button
                onClick={() => acceptItem(currentItem.path)}
                disabled={accepting || !currentForm.title.trim()}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
              >
                {accepting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                <Trans>Accept</Trans>
              </button>
              <button
                onClick={skipItem}
                className="px-4 py-2 rounded-lg text-sm font-medium border border-border bg-card text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
              >
                <Trans>Skip</Trans>
              </button>
              <button
                onClick={rejectCurrentAndAdvance}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium border border-destructive/30 text-destructive hover:bg-destructive/5 transition-all ml-auto"
              >
                <Trash2 className="h-3.5 w-3.5" /> <Trans>Reject</Trans>
              </button>
            </div>
          </div>

          {/* Right: Candidates */}
          <div className="lg:w-2/5 overflow-y-auto p-6">
            <CandidatePanel
              candidates={candidates}
              loading={fetchingMeta}
              searchQuery={searchQuery}
              onSearchQueryChange={setSearchQuery}
              onSearch={manualSearch}
              onSelect={applyCandidateWithFlash}
              appliedId={appliedId}
            />
          </div>
        </div>
      )}

      {/* Batch review */}
      {isBatch && (
        <div className="flex-1 flex flex-col lg:flex-row gap-0 overflow-hidden">
          {/* Left: Batch form */}
          <div className="lg:w-3/5 overflow-y-auto p-6 border-r border-border space-y-5">
            {/* Shared fields */}
            <div className="rounded-lg border border-border p-4 space-y-3">
              {(() => { const n = reviewItems.length; return <h3 className="text-sm font-semibold"><Trans>Shared fields — applies to all {n} items</Trans></h3> })()}
              <div className="space-y-3">
                <div>
                  <label className={LABEL_CLS}><Trans>Series</Trans></label>
                  <div className="flex gap-2">
                    <input
                      className={cn(INPUT_CLS, 'flex-1')}
                      value={batchSeries}
                      onChange={e => setBatchSeries(e.target.value)}
                      placeholder={t`Series name`}
                    />
                    <button
                      onClick={applyBatchSeries}
                      className="shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-muted hover:bg-accent transition-colors"
                    >
                      <Trans>Apply to all</Trans>
                    </button>
                  </div>
                </div>
                <div>
                  <label className={LABEL_CLS}><Trans>Author</Trans></label>
                  <div className="flex gap-2">
                    <input
                      className={cn(INPUT_CLS, 'flex-1')}
                      value={batchAuthor}
                      onChange={e => setBatchAuthor(e.target.value)}
                      placeholder={t`Author name`}
                    />
                    <button
                      onClick={applyBatchAuthor}
                      className="shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-muted hover:bg-accent transition-colors"
                    >
                      <Trans>Apply to all</Trans>
                    </button>
                  </div>
                </div>
                <div>
                  <label className={LABEL_CLS}><Trans>Book Type</Trans></label>
                  <div className="flex gap-2">
                    <SelectMenu
                      className="flex-1"
                      value={batchBookTypeId}
                      onChange={setBatchBookTypeId}
                      options={[{ value: '', label: t`No type` },
                                ...bookTypes.map(bt => ({ value: String(bt.id), label: bt.label }))]}
                    />
                    <button
                      onClick={applyBatchBookType}
                      className="shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-muted hover:bg-accent transition-colors"
                    >
                      <Trans>Apply to all</Trans>
                    </button>
                  </div>
                </div>
                {libraries.some(l => l.can_edit) && (
                  <div>
                    <label className={LABEL_CLS}><Trans>Libraries</Trans></label>
                    <div className="flex gap-2">
                      <LibrariesSelect
                        value={batchLibraryIds}
                        onChange={setBatchLibraryIds}
                        libraries={libraries}
                        className="flex-1"
                      />
                      <button
                        onClick={applyBatchLibraries}
                        className="shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-muted hover:bg-accent transition-colors"
                      >
                        <Trans>Apply to all</Trans>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Per-item rows */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold"><Trans>Per-file details</Trans></h3>
                <button
                  onClick={matchAll}
                  disabled={matchingAll !== null || fetchingMeta}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border text-foreground hover:bg-muted disabled:opacity-50 transition-all"
                >
                  {matchingAll !== null
                    ? <Loader2 className="h-3 w-3 animate-spin" />
                    : <Zap className="h-3 w-3" />}
                  {(() => { const total = reviewItems.length; return matchingAll !== null
                    ? t`Matching ${matchingAll}/${total}…`
                    : t`Match all` })()}
                </button>
              </div>
              {reviewItems.map(item => {
                const form = formData[item.path]
                if (!form) return null
                const isExpanded = expandedRows.has(item.path)
                return (
                  <div
                    key={item.path}
                    className={cn(
                      'rounded-lg border overflow-hidden transition-colors',
                      suggestPath === item.path
                        ? 'border-primary/50 ring-1 ring-primary/25'
                        : 'border-border'
                    )}
                  >
                    {/* Compact row — clicking anywhere targets the suggestions panel */}
                    <div
                      className="flex items-center gap-3 px-3 py-2.5 bg-card"
                      onClick={() => {
                        if (suggestPath !== item.path) {
                          setSuggestPath(item.path)
                          fetchPreview(item, undefined, false)
                        }
                      }}
                    >
                      <button
                        onClick={() => setExpandedRows(prev => {
                          const next = new Set(prev)
                          if (next.has(item.path)) next.delete(item.path)
                          else next.add(item.path)
                          return next
                        })}
                        className="shrink-0 text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {isExpanded
                          ? <ChevronDown className="h-3.5 w-3.5" />
                          : <ChevronRight className="h-3.5 w-3.5" />}
                      </button>
                      <span className="text-xs text-muted-foreground truncate w-40 shrink-0" title={item.filename}>
                        {item.filename}
                      </span>
                      <input
                        className="flex-1 h-7 rounded border border-border bg-transparent px-2 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                        value={form.title}
                        onChange={e => updateForm(item.path, 'title', e.target.value)}
                        placeholder={t`Title`}
                      />
                      <input
                        className="w-16 h-7 rounded border border-border bg-transparent px-2 text-xs text-center placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                        type="number"
                        step="0.1"
                        value={form.series_index}
                        onChange={e => updateForm(item.path, 'series_index', e.target.value)}
                        placeholder="#"
                        title={t`Series index`}
                      />
                      <select
                        className="h-7 rounded border border-border bg-transparent px-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
                        value={form.content_type}
                        onChange={e => updateForm(item.path, 'content_type', e.target.value)}
                      >
                        <option value="volume">{t`Volume`}</option>
                        <option value="chapter">{t`Chapter`}</option>
                      </select>
                    </div>
                    {/* Expanded full form */}
                    {isExpanded && (
                      <div className="px-4 py-3 border-t border-border bg-muted/20">
                        <MetadataForm
                          form={form}
                          onChange={(field, value) => updateForm(item.path, field, value)}
                          bookTypes={bookTypes}
                          libraries={libraries}
                        />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            {/* Batch action bar */}
            <div className="flex items-center gap-2 pt-4 border-t border-border">
              <button
                onClick={acceptAll}
                disabled={accepting}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
              >
                {accepting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                <Trans>Accept All</Trans>
              </button>
              <button
                onClick={() => transitionTo('list')}
                className="px-4 py-2 rounded-lg text-sm font-medium border border-border bg-card text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
              >
                <Trans>Back to list</Trans>
              </button>
              <button
                onClick={() => setConfirmReject(reviewItems.map(i => i.path))}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium border border-destructive/30 text-destructive hover:bg-destructive/5 transition-all ml-auto"
              >
                <Trash2 className="h-3.5 w-3.5" /> <Trans>Reject All</Trans>
              </button>
            </div>
          </div>

          {/* Right: candidates for the targeted row — expand a row to retarget */}
          <div className="lg:w-2/5 overflow-y-auto p-6">
            <CandidatePanel
              candidates={candidates}
              loading={fetchingMeta}
              searchQuery={searchQuery}
              onSearchQueryChange={setSearchQuery}
              onSearch={manualSearch}
              onSelect={c => applyCandidateWithFlash(c, suggestPath ?? undefined)}
              appliedId={appliedId}
              targetLabel={reviewItems.find(i => i.path === suggestPath)?.filename}
            />
          </div>
        </div>
      )}

      {/* Confirm reject dialog */}
      <ConfirmDialog
        open={confirmReject !== null}
        title={t`Reject files`}
        message={plural(confirmReject?.length ?? 0, { one: 'Permanently delete # file from the bindery? This cannot be undone.', other: 'Permanently delete # files from the bindery? This cannot be undone.' })}
        confirmLabel={rejecting ? t`Deleting…` : t`Delete`}
        destructive
        onConfirm={() => {
          if (confirmReject) {
            doReject(confirmReject).then(() => {
              if (view === 'review') transitionTo('list')
            })
          }
        }}
        onCancel={() => setConfirmReject(null)}
      />
    </div>
    </AppShell>
  )
}
