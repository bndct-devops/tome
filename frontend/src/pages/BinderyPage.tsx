import { startTransition, useEffect, useLayoutEffect, useState, useCallback, useMemo, useRef } from 'react'
import { AnimatePresence, LayoutGroup, m } from 'motion/react'
import {
  RefreshCw, Loader2, BookOpen, Check, X, Trash2, Search,
  ChevronRight, ChevronDown, ArrowLeft, FolderOpen,
  Zap, Eye, HelpCircle, Clock, Library as LibraryIcon,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Checkbox, PageBody, PageHeader, SectionTitle, Pill, Select } from '@/components/page'
import { ModalShell } from '@/components/ModalShell'
import { QuickLook, type QuickLookPreview } from '@/components/QuickLook'
import { SameWidth } from '@/components/FormMotion'
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
import { coverSrc } from '@/lib/covers'
import { BookLink, CoverLift, EASE, Pop, RunningNumber, Unfold, listItem } from '@/components/motion'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UnreviewedBook {
  id: number
  updated_at?: string | null
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

const BADGE_CLS = 'inline-flex items-center h-6 px-2.5 rounded-full bg-secondary text-[12px] font-semibold uppercase tracking-wide shrink-0'

function FormatBadge({ format }: { format: string }) {
  return <span className={cn(BADGE_CLS, 'text-muted-foreground')}>{format}</span>
}

const CONTENT_TYPE_LABELS: Record<string, MessageDescriptor> = { volume: msg`Volume`, chapter: msg`Chapter` }

function ContentTypeBadge({ type }: { type: string }) {
  const { i18n } = useLingui()
  return (
    <span className={cn(BADGE_CLS, 'normal-case tracking-normal', type === 'chapter' ? 'text-warning' : 'text-muted-foreground')}>
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
    <span className="shrink-0 text-[12px] px-2 py-0.5 rounded-full text-muted-foreground bg-secondary">
      {label}
    </span>
  )
}

// Shared input class: filled field, the refreshed shell's input look
const INPUT_CLS =
  'flex h-9 w-full rounded-lg bg-secondary px-3 py-1 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring'
const TEXTAREA_CLS =
  'w-full rounded-lg bg-secondary px-3 py-2 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring resize-none'
const LABEL_CLS = 'block text-[13px] text-muted-foreground mb-1'

// A new view (list, review, the next file in a review) rises into place from
// part opacity; the one it replaces goes at once, so nothing waits.
const STEP_IN = { opacity: 0.5, y: 8 }
const STEP_TRANSITION = { duration: 0.18, ease: EASE }

// The chevron of a section that folds, on the app's curve.
const BAR = 'h-full bg-primary transition-[width] duration-300 ease-(--spring) motion-reduce:transition-none'

const CHEVRON = 'h-4 w-4 shrink-0 transition-transform duration-[240ms] ease-(--spring) motion-reduce:transition-none'

/** Holds the page's shape while the bindery is read for the first time. */
function BinderySkeleton() {
  return (
    <section aria-busy="true" className="animate-pulse motion-reduce:animate-none">
      <div className="h-5 w-40 rounded-md bg-muted" />
      <div className="mt-3 divide-y divide-border/70">
        {[0, 1, 2, 3, 4].map(i => (
          <div key={i} className="flex items-center gap-4 py-3 px-2">
            <div className="h-[18px] w-[18px] rounded bg-muted shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="h-4 w-1/2 rounded bg-muted" />
              <div className="mt-2 h-3 w-1/3 rounded bg-muted" />
            </div>
            <div className="h-6 w-12 rounded-full bg-muted shrink-0" />
          </div>
        ))}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// LibrariesSelect - same idea as the Book page's "Libraries" dropdown (#103)
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
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      window.removeEventListener('keydown', onKey, true)
    }
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
      <Pop open={open} origin="top" className="absolute left-0 right-0 top-full mt-1 z-40 rounded-xl bg-card shadow-lg ring-1 ring-panel-ring py-1 max-h-56 overflow-y-auto">
          {editable.map(lib => {
            const selected = value.includes(lib.id)
            return (
              <button
                key={lib.id}
                type="button"
                onClick={() => toggle(lib.id)}
                className="flex items-center gap-2 w-full px-3 py-2 text-[14px] hover:bg-secondary transition-colors text-left"
              >
                <LibraryIcon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                <span className="flex-1 truncate">{lib.name}</span>
                <Check className={cn('w-3.5 h-3.5 shrink-0 transition-colors', selected ? 'text-primary' : 'text-muted-foreground/30')} />
              </button>
            )
          })}
      </Pop>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SelectMenu: the shared filled Select with an options array
// ---------------------------------------------------------------------------

function SelectMenu({ value, onChange, options, className }: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  className?: string
}) {
  return (
    <Select value={value} onChange={e => onChange(e.target.value)} className={cn('cursor-pointer', className)}>
      {options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
    </Select>
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
          <label className={LABEL_CLS}><Trans>Content type</Trans></label>
          <SelectMenu
            value={form.content_type}
            onChange={v => onChange('content_type', v)}
            options={[{ value: 'volume', label: t`Volume` }, { value: 'chapter', label: t`Chapter` }]}
          />
        </div>
        <div>
          <label className={LABEL_CLS}><Trans>Book type</Trans></label>
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
        <p className="mt-1 text-[13px] text-muted-foreground">
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
            className="mt-2 h-24 w-16 object-cover rounded-[8px]"
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
        <h2 className="font-display text-[20px] font-bold leading-none"><Trans>Metadata suggestions</Trans></h2>
        {targetLabel && (
          <span className="block mt-1.5 text-[13px] text-muted-foreground truncate">
            <Trans>for {targetLabel}. Click a file to switch.</Trans>
          </span>
        )}
      </div>
      <form
        className="flex gap-1.5 mb-3"
        onSubmit={e => { e.preventDefault(); onSearch(searchQuery) }}
      >
        <input
          className="flex-1 min-w-0 h-9 rounded-full bg-secondary px-4 text-[14px] placeholder:text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={searchQuery}
          onChange={e => onSearchQueryChange(e.target.value)}
          placeholder={t`Search query...`}
        />
        <button
          type="submit"
          disabled={loading || !searchQuery.trim()}
          aria-label={t`Search`}
          className="shrink-0 h-9 w-9 rounded-full bg-secondary flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-surface-hover transition-colors disabled:opacity-50 touch-feedback"
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
        </button>
      </form>
      {/* A new search keeps the old results on screen, dimmed and inert,
          until the new ones land; the spinner shows only when there were none. */}
      {loading && candidates.length === 0 && (
        <div className="flex-1 flex items-center justify-center py-12">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      )}
      {!loading && candidates.length === 0 && (
        <div className="flex-1 flex flex-col items-center justify-center py-12 text-center">
          <BookOpen className="h-8 w-8 text-muted-foreground/40 mb-2" />
          <p className="text-sm text-muted-foreground"><Trans>No metadata found</Trans></p>
          <p className="text-[13px] text-muted-foreground/60 mt-1"><Trans>Try editing the title or series fields</Trans></p>
        </div>
      )}
      {candidates.length > 0 && (
        <div className={cn('relative flex-1 transition-opacity duration-150', loading && 'opacity-50 pointer-events-none')} aria-busy={loading || undefined}>
          <p className="text-[13px] text-muted-foreground mb-1">
            <Plural value={candidates.length} one="# result. Click one to fill the form." other="# results. Click one to fill the form." />
          </p>
          <AnimatePresence mode="popLayout" initial={false}>
          {candidates.map(c => (
            <m.button
              {...listItem}
              key={`${c.source}-${c.source_id}`}
              className={cn(
                'w-full text-left py-3 px-2 border-t border-border/70 first-of-type:border-t-0 hover:bg-secondary/50 transition-colors flex gap-3 touch-feedback',
                appliedId === `${c.source}-${c.source_id}` && 'bg-primary/5'
              )}
              onClick={() => onSelect(c)}
            >
              {c.cover_url ? (
                <img src={c.cover_url} alt="" className="h-20 w-14 object-cover rounded-md shrink-0" style={{ boxShadow: 'var(--cover-bloom-fan)' }} />
              ) : (
                <div className="h-20 w-14 rounded-md bg-muted flex items-center justify-center shrink-0">
                  <BookOpen className="h-5 w-5 text-muted-foreground" />
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-semibold text-[15px] line-clamp-2">{c.title}</p>
                  <SourceBadge source={c.source} />
                </div>
                {c.author && <p className="text-[14px] text-muted-foreground mt-0.5">{c.author}</p>}
                {c.year && <p className="text-[13px] text-muted-foreground">{c.year}</p>}
                {c.description && (
                  <p className="text-[13px] text-muted-foreground line-clamp-2 mt-1">{c.description}</p>
                )}
                {c.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    {c.tags.slice(0, 3).map(t => (
                      <span key={t} className="text-[12px] bg-secondary rounded-full px-2 py-0.5">{t}</span>
                    ))}
                    {c.tags.length > 3 && (
                      <span className="text-[12px] text-muted-foreground">+{c.tags.length - 3}</span>
                    )}
                  </div>
                )}
              </div>
            </m.button>
          ))}
          </AnimatePresence>
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

function SeriesReviewEntry({ group, bookTypes, libraries, state, onAccept, onReject, onReviewFile }: {
  group: SeriesGroup
  bookTypes: BookType[]
  libraries: LibraryOption[]
  /** 'queued': waits for the entry ahead of it; 'running': being accepted now.
   *  Either way the entry is frozen: the queued request carries these values. */
  state: 'idle' | 'queued' | 'running'
  /** content_type '' keeps each file's detected type. */
  onAccept: (form: { series: string; author: string; book_type_id: string; library_ids: number[]; content_type: string }) => void
  /** Reject every file of the group (through the page's confirm dialog). */
  onReject: () => void
  /** Open one volume in the file review (edit, accept or reject it alone). */
  onReviewFile: (path: string) => void
}) {
  const { t } = useLingui()
  const [series, setSeries] = useState(group.series ?? '')
  const [author, setAuthor] = useState(group.author ?? '')
  const [typeId, setTypeId] = useState(group.book_type_id != null ? String(group.book_type_id) : '')
  const [libraryIds, setLibraryIds] = useState<number[]>(group.library_ids)
  // One content type for the group; a mixed group starts on "As detected" ('').
  const detectedTypes = new Set(group.files.map(f => f.content_type))
  const mixed = detectedTypes.size > 1
  const [contentType, setContentType] = useState(mixed ? '' : (group.files[0]?.content_type ?? 'volume'))
  const typeOf = (f: SeriesGroup['files'][number]) => contentType || f.content_type
  const allChapters = group.files.length > 0 && group.files.every(f => typeOf(f) === 'chapter')
  const allVolumes = group.files.every(f => typeOf(f) !== 'chapter')
  const destFolder = group.files[0]?.dest_preview.split('/').slice(0, -1).join('/')
  const hasLibraries = libraries.some(l => l.can_edit)
  const busy = state !== 'idle'

  return (
    <div className="py-5 flex flex-col gap-3.5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        {/* 16rem basis: on a narrow screen the buttons wrap under the title
            instead of squeezing it. */}
        <div className="min-w-0 flex-[1_1_16rem]">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[17px] font-semibold text-foreground truncate">
              {series || t`Ungrouped files`}
            </span>
            {group.library_match && (
              <span
                className="inline-flex items-center gap-1 h-6 px-2.5 rounded-full bg-secondary text-[12px] font-medium text-success whitespace-nowrap"
                title={(() => { const name = group.library_match.series; return t`Matches "${name}" already in your library. Name, author, type and libraries are adopted from it.` })()}
              >
                <Check className="w-3 h-3" />
                {plural(group.library_match.volume_count, { one: 'In library · # vol', other: 'In library · # vols' })}
                {group.library_match.from_reviewed ? t` · reviewed` : ''}
              </span>
            )}
            {!group.library_match && group.series && (
              <span className="inline-flex items-center h-6 px-2.5 rounded-full bg-secondary text-[12px] font-medium text-muted-foreground whitespace-nowrap">
                <Trans>New series</Trans>
              </span>
            )}
          </div>
          {destFolder && (
            <p className="mt-1 text-[13px] text-muted-foreground font-mono truncate" title={destFolder}>
              → {destFolder}/
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Pill
            variant="secondary"
            onClick={() => onAccept({ series, author, book_type_id: typeId, library_ids: libraryIds, content_type: contentType })}
            disabled={busy}
            title={state === 'queued' ? t`Waits for the series ahead of it` : undefined}
          >
            {state === 'running' ? <Loader2 className="h-4 w-4 animate-spin" /> : state === 'queued' ? <Clock className="h-4 w-4" /> : <Check className="h-4 w-4" />}
            {state === 'queued' ? <Trans>Queued</Trans> : allChapters
              ? <Plural value={group.files.length} one="Accept # chapter" other="Accept # chapters" />
              : allVolumes
                ? <Plural value={group.files.length} one="Accept # volume" other="Accept # volumes" />
                : <Plural value={group.files.length} one="Accept # file" other="Accept # files" />}
          </Pill>
          <Pill variant="destructive" onClick={onReject} disabled={busy}>
            <Trash2 className="h-4 w-4" /> <Trans>Reject</Trans>
          </Pill>
        </div>
      </div>

      {/* One identity decision for the whole group */}
      <fieldset
        disabled={busy}
        className={cn('min-w-0 grid grid-cols-1 sm:grid-cols-2 gap-2 disabled:opacity-60', hasLibraries ? 'lg:grid-cols-5' : 'lg:grid-cols-4')}
      >
        <input
          value={series}
          onChange={e => setSeries(e.target.value)}
          placeholder={t`Series`}
          aria-label={t`Series`}
          className={INPUT_CLS}
        />
        <input
          value={author}
          onChange={e => setAuthor(e.target.value)}
          placeholder={t`Author`}
          aria-label={t`Author`}
          className={INPUT_CLS}
        />
        <Select
          value={typeId}
          onChange={e => setTypeId(e.target.value)}
          aria-label={t`Book type`}
          className={cn('cursor-pointer', !typeId && 'text-muted-foreground')}
        >
          <option value="">{t`No type`}</option>
          {bookTypes.map(bt => <option key={bt.id} value={String(bt.id)}>{bt.label}</option>)}
        </Select>
        <Select
          value={contentType}
          onChange={e => setContentType(e.target.value)}
          aria-label={t`Content type`}
          className="cursor-pointer"
        >
          {mixed && <option value="">{t`As detected`}</option>}
          <option value="volume">{t`Volumes`}</option>
          <option value="chapter">{t`Chapters`}</option>
        </Select>
        {/* Fifth control: full width on the two-column sm grid, one cell at lg. */}
        <LibrariesSelect
          value={libraryIds}
          onChange={setLibraryIds}
          libraries={libraries}
          className="sm:col-span-2 lg:col-span-1"
        />
      </fieldset>

      {/* Per-volume confirmation strip */}
      <div className="flex flex-wrap gap-1.5">
        {/* A volume chip opens that file alone: the group's files are listed
            only here, not again under Incoming files. */}
        {group.files.map(f => (
          <button
            type="button"
            key={f.path}
            onClick={() => onReviewFile(f.path)}
            disabled={busy}
            title={`${f.filename} → ${f.dest_preview}`}
            className="inline-flex items-center gap-1.5 h-7 px-3 rounded-full bg-secondary text-[13px] text-foreground hover:bg-surface-hover disabled:hover:bg-secondary disabled:opacity-60 transition-colors touch-feedback"
          >
            {(() => {
              const idx = f.series_index
              if (idx == null) return f.title || f.filename
              return typeOf(f) === 'chapter' ? t`Ch ${idx}` : t`Vol ${idx}`
            })()}
            <span className="text-muted-foreground uppercase text-[12px] font-semibold tracking-wide">{f.format}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

function ConfirmDialog({ open, title, message, confirmLabel, destructive, onConfirm, onCancel }: ConfirmDialogProps) {
  const { t } = useLingui()
  const resolvedConfirmLabel = confirmLabel ?? t`Confirm`
  return (
    <ModalShell
      open={open}
      onClose={onCancel}
      size="sm"
      title={title}
      footer={
        <>
          <Pill variant="quiet" onClick={onCancel}>
            <Trans>Cancel</Trans>
          </Pill>
          <Pill
            variant={destructive ? 'destructive' : 'primary'}
            onClick={onConfirm}
            className={cn(destructive && 'text-destructive')}
          >
            {resolvedConfirmLabel}
          </Pill>
        </>
      }
    >
      <p className="text-[15px] text-muted-foreground">{message}</p>
    </ModalShell>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function BinderyPage() {
  const { t } = useLingui()
  const { toast } = useToast()
  const bookTypes = useBookTypes()
  const [peekId, setPeekId] = useState<number | null>(null)
  // The cover the quick look grows out of (and shrinks back into).
  const [peekFrom, setPeekFrom] = useState<{ origin?: DOMRect; cover?: string | null; preview?: QuickLookPreview }>({})
  const [libraries, setLibraries] = useState<LibraryOption[]>([])
  useEffect(() => {
    api.get<LibraryOption[]>('/libraries').then(setLibraries).catch(() => {})
  }, [])

  // List view state
  const [view, setView] = useState<View>('list')
  // Set once the view has changed: the first view shows as is, later ones rise in.
  const [viewMoved, setViewMoved] = useState(false)
  // Set when a single review moves on to the next file (rises in), cleared on entering review.
  const [reviewStepped, setReviewStepped] = useState(false)
  const [items, setItems] = useState<BinderyItem[]>([])
  const [loading, setLoading] = useState(true)
  // After the first read, a refresh dims what is there instead of a skeleton.
  const [loadedOnce, setLoadedOnce] = useState(false)
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
  // Libraries picked in the LIST toolbar - consumed by the next Quick Accept
  // or Review run, then reset so the next batch doesn't inherit it silently
  const [listLibraryIds, setListLibraryIds] = useState<number[]>([])
  const [matchingAll, setMatchingAll] = useState<number | null>(null)
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set())
  // Which file the suggestions panel searches/applies for in batch review  - 
  // expanding a row retargets the panel (it used to silently hit file #1 only)
  const [suggestPath, setSuggestPath] = useState<string | null>(null)

  // ---------------------------------------------------------------------------
  // View transition helper
  // ---------------------------------------------------------------------------

  function transitionTo(newView: View) {
    if (newView !== view) setViewMoved(true)
    setView(newView)
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
      // Non-fatal - endpoint may not exist yet or returns empty
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
    setLoadedOnce(true)
  }, [fetchItems, fetchUnreviewed])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  // ---------------------------------------------------------------------------
  // Series-group accept - one identity decision covers every volume in a group
  // ---------------------------------------------------------------------------

  // Series entries accept one at a time (#338). A click while another entry
  // runs queues the entry instead of starting a second request: the server
  // works through one series, the list refreshes between them, and no refresh
  // lands while a series is half moved (which used to show its untouched half
  // as a fresh entry that invited a second click on the same files).
  type GroupForm = { series: string; author: string; book_type_id: string; library_ids: number[]; content_type: string }
  const [acceptingGroup, setAcceptingGroup] = useState<string | null>(null)
  const [queuedGroups, setQueuedGroups] = useState<string[]>([])
  const groupQueue = useRef<{ group: SeriesGroup; form: GroupForm }[]>([])
  const groupQueueRunning = useRef<string | null>(null)

  function queueGroupAccept(group: SeriesGroup, form: GroupForm) {
    if (groupQueueRunning.current === group.key || groupQueue.current.some(j => j.group.key === group.key)) return
    groupQueue.current.push({ group, form })
    setQueuedGroups(groupQueue.current.map(j => j.group.key))
    if (groupQueueRunning.current === null) void runGroupQueue()
  }

  async function runGroupQueue() {
    let job = groupQueue.current.shift()
    while (job) {
      groupQueueRunning.current = job.group.key
      setAcceptingGroup(job.group.key)
      setQueuedGroups(groupQueue.current.map(j => j.group.key))
      await acceptGroup(job.group, job.form)
      job = groupQueue.current.shift()
    }
    groupQueueRunning.current = null
    setAcceptingGroup(null)
    setQueuedGroups([])
  }

  async function acceptGroup(group: SeriesGroup, form: GroupForm) {
    try {
      const files: BinderyAcceptFile[] = group.files.map(f => ({
        path: f.path,
        title: f.title || f.filename.replace(/\.[^.]+$/, ''),
        author: form.author || null,
        series: form.series || null,
        series_index: f.series_index,
        content_type: form.content_type || f.content_type,
        book_type_id: form.book_type_id ? parseInt(form.book_type_id, 10) : null,
        language: group.language,
        library_ids: form.library_ids,
      }))
      const res = await api.post<{ accepted: unknown[]; errors: { path: string; error: string }[] }>(
        '/bindery/accept', { files })
      if (res.errors?.length) {
        { const failedN = res.errors.length, totalN = files.length, firstErr = res.errors[0].error; toast.error(t`${failedN} of ${totalN} failed: ${firstErr}`) }
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
    }
  }

  // ---------------------------------------------------------------------------
  // Grouping
  // ---------------------------------------------------------------------------

  // Series review shows real series groups (several files, or one that matches
  // a series already in the library). Their files are listed there only; every
  // other file stays under Incoming files, so each file appears exactly once.
  // If /bindery/groups failed, groups is empty and every file is listed below.
  const reviewGroups = groups.filter(g => g.files.length > 1 || g.library_match)
  const groupedPaths = new Set(reviewGroups.flatMap(g => g.files.map(f => f.path)))
  const listItems = items.filter(i => !groupedPaths.has(i.path))
  const folders = Array.from(
    new Set(listItems.map(i => i.folder).filter((f): f is string => f !== null))
  ).sort()
  const ungrouped = listItems.filter(i => i.folder === null)

  // ---------------------------------------------------------------------------
  // Selection
  // ---------------------------------------------------------------------------

  const { handleToggle } = useShiftSelect(listItems.map(i => i.path))

  // The rows measure their place only when the list itself changes (or a
  // leaving row has just gone), not on every render: selecting in a long
  // list would otherwise measure every row on each click.
  const [listEpoch, setListEpoch] = useState(0)
  const bumpList = useCallback(() => setListEpoch(e => e + 1), [])
  // eslint-disable-next-line react-hooks/exhaustive-deps -- a token that changes with these
  const listDep = useMemo(() => ({}), [items, unreviewed, groups, collapsedFolders, listEpoch])

  // Many rows changing at once snap instead of fading their colours: hundreds
  // of colour transitions starting in one frame cost more than they show.
  // data-snap stops the rows' and the checkboxes' (data-check-box)
  // transitions; the selectors name only those so toggling it restyles little.
  const listRef = useRef<HTMLDivElement>(null)
  const snapRows = () => listRef.current?.setAttribute('data-snap', '')
  useLayoutEffect(() => {
    const el = listRef.current
    if (!el?.hasAttribute('data-snap')) return
    let timer = 0
    const frame = requestAnimationFrame(() => { timer = window.setTimeout(() => el.removeAttribute('data-snap')) })
    return () => { cancelAnimationFrame(frame); clearTimeout(timer) }
  }, [selected])

  function toggleSelect(path: string, shiftKey: boolean) {
    if (shiftKey) snapRows()
    setSelected(prev => {
      const index = listItems.findIndex(i => i.path === path)
      return handleToggle(path, index, shiftKey, prev)
    })
  }

  // Selecting or clearing many rows at once renders in slices (a transition),
  // so a long list never holds up a frame.
  function selectAll() {
    const all = new Set(listItems.map(i => i.path))
    snapRows()
    startTransition(() => setSelected(all))
  }

  function clearSelection() {
    snapRows()
    startTransition(() => setSelected(new Set()))
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
    const folderPaths = listItems.filter(i => i.folder === folder).map(i => i.path)
    snapRows()
    startTransition(() => setSelected(prev => {
      const next = new Set(prev)
      const allSelected = folderPaths.every(p => next.has(p))
      if (allSelected) folderPaths.forEach(p => next.delete(p))
      else folderPaths.forEach(p => next.add(p))
      return next
    }))
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
    // A shared field is prefilled only when every file in the batch agrees;
    // otherwise it stays empty with its placeholder.
    const agreed = (key: 'series' | 'author' | 'book_type_id') => {
      const vals = new Set(toReview.map(i => (forms[i.path]?.[key] ?? '').trim()))
      return vals.size === 1 ? [...vals][0] : ''
    }
    setBatchSeries(agreed('series'))
    setBatchAuthor(agreed('author'))
    setBatchBookTypeId(agreed('book_type_id'))
    setBatchLibraryIds([...listLibraryIds])
    setListLibraryIds([])
    setExpandedRows(new Set())
    setSuggestPath(toReview[0]?.path ?? null)
    setReviewStepped(false)
    transitionTo('review')

    // Fetch metadata for the first item
    fetchPreview(toReview[0])
  }

  async function fetchPreview(item: BinderyItem, queryOverride?: string, autoApply = true) {
    const itemPath = item.path
    setFetchingMeta(true)
    try {
      const body: { path: string; query?: string } = { path: itemPath }
      if (queryOverride !== undefined) body.query = queryOverride
      const result = await api.post<{ file_metadata: Record<string, unknown>; candidates: MetadataCandidate[]; query_used: string }>(
        '/bindery/preview',
        body
      )
      setCandidates(result.candidates)
      setSearchQuery(result.query_used)
      // Embedded metadata (calibre:series etc.) beats the filename guess  - 
      // same precedence as auto-import. Candidate apply still wins on top.
      if (autoApply && !queryOverride) {
        applyEmbedded(result.file_metadata, itemPath)
        if (result.candidates.length > 0) {
          applyCandidate(result.candidates[0], itemPath)
        }
      }
    } catch {
      // Non-fatal: no suggestions for this file
      setCandidates([])
    } finally {
      setFetchingMeta(false)
    }
  }

  // Trust-the-matcher tier for batch review: fetch and apply the best
  // candidate to EVERY file's form, then the user eyeballs the rows and
  // Accept-Alls. Explicit button - it does overwrite hand edits.
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
          // Non-fatal - leave this file's parsed data in place
        }
      }
    } finally {
      setMatchingAll(null)
    }
    if (matched === reviewItems.length) {
      toast.success(t`Best match applied to all ${matched} files`)
    } else {
      { const totalN = reviewItems.length; toast.success(t`Best match applied to ${matched} of ${totalN} files. No match for the rest.`) }
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
  // into a form row. Embedded values win over the filename parse - the
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
    // Batch shared fields are not filled from one file's metadata: the other
    // files may disagree (prefill happens in enterReview only on agreement).
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
        // Non-fatal - accept with parsed data only
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
        // Series review lists its files from groups, not items: drop them there too.
        setGroups(prev => prev
          .map(g => ({ ...g, files: g.files.filter(f => !paths.includes(f.path)) }))
          .filter(g => g.files.length > 0))
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
    setReviewStepped(true)
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
    setReviewStepped(true)
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

  function renderRow(item: BinderyItem, indent = false) {
    const isSelected = selected.has(item.path)
    const itemTitle = item.title
    return (
      <m.div
        {...listItem} layoutDependency={listDep}
        key={item.path}
        className={cn(
          'group flex items-center gap-4 py-3 px-2 transition-colors',
          isSelected && 'bg-primary/5',
          indent && 'pl-9'
        )}
      >
        <Checkbox
          checked={isSelected}
          onChange={e => toggleSelect(item.path, e.nativeEvent instanceof MouseEvent ? e.nativeEvent.shiftKey : false)}
          onClick={e => e.stopPropagation()}
          aria-label={t`Select ${itemTitle}`}
          className="shrink-0 [&>span]:mt-0"
        />
        <button
          className="flex-1 min-w-0 flex items-center gap-4 text-left touch-feedback"
          onClick={() => enterReview([item.path])}
          title={t`Review this file`}
        >
          <div className="flex-1 min-w-0">
            <span className="text-[16px] font-semibold text-foreground truncate block group-hover:text-primary transition-colors">{item.title}</span>
            <span className="text-[13px] text-muted-foreground truncate block mt-0.5">
              {item.filename}
              {item.series && <span className="sm:hidden"> · {item.series}</span>}
            </span>
          </div>
          <span className="text-[14px] text-muted-foreground w-32 text-right hidden sm:block truncate">
            {item.series ?? ''}
          </span>
          <div className="flex items-center gap-1.5 shrink-0">
            <FormatBadge format={item.format} />
            <ContentTypeBadge type={item.content_type} />
          </div>
          <span className="text-[13px] text-muted-foreground w-16 text-right hidden md:block tabular-nums">
            {formatBytes(item.size)}
          </span>
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground/50 group-hover:text-muted-foreground transition-colors" />
        </button>
      </m.div>
    )
  }

  const refreshing = loading || unreviewedLoading
  const firstLoad = !loadedOnce

  // ---------------------------------------------------------------------------
  // LIST VIEW
  // ---------------------------------------------------------------------------

  if (view === 'list') {
    // Only files listed below can be selected; a selection kept from before a
    // refresh never acts on files now shown under Series review.
    const listPaths = new Set(listItems.map(i => i.path))
    const selectedArr = Array.from(selected).filter(p => listPaths.has(p))
    const hasSelection = selectedArr.length > 0
    const eyebrow = firstLoad
      ? t`Loading…`
      : (() => {
          const imp = unreviewed.length
          return (
            <RunningNumber
              value={items.length}
              format={n => {
                const inc = Math.round(n)
                if (imp > 0) return t`${imp} imported · ${inc} incoming`
                return plural(inc, { one: '# file waiting for review', other: '# files waiting for review' })
              }}
            />
          )
        })()

    return (
      <AppShell onUploaded={fetchAll}>
      <m.div key="list" initial={viewMoved ? STEP_IN : false} animate={{ opacity: 1, y: 0 }} transition={STEP_TRANSITION}>
      {/* One group: when a leaving row is gone, everything below glides up. */}
      <LayoutGroup>
      <PageBody width="wide">
        <PageHeader
          eyebrow={eyebrow}
          title={<Trans>Bindery</Trans>}
          className="[&>div:last-child]:shrink [&>div:last-child]:min-w-0"
          actions={
            <div className="flex flex-wrap items-center gap-1.5">
              <a
                href={docsLink(DOCS.bindery)}
                target="_blank"
                rel="noopener noreferrer"
                title={t`Bindery flow explained: open the docs`}
                aria-label={t`Bindery flow explained: open the docs`}
                className="inline-flex items-center justify-center h-9 w-9 rounded-full text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors touch-feedback"
              >
                <HelpCircle className="w-4 h-4" />
              </a>
              <Pill
                variant="quiet"
                onClick={fetchAll}
                disabled={refreshing}
                title={t`Rescan the bindery folder`}
              >
                <RefreshCw className={cn('h-4 w-4', refreshing && 'animate-spin')} />
                <Trans>Refresh</Trans>
              </Pill>
              {listItems.length > 0 && (
                <Pill variant="quiet" onClick={hasSelection ? clearSelection : selectAll} className="tabular-nums">
                  {/* As wide as its widest label, so Accept all never moves. */}
                  <SameWidth active={hasSelection ? 1 : 0}>{[
                    t`Select all`,
                    // Starts where the reserved width starts: a growing count only extends to the right.
                    <span key="n" className="w-full text-left"><RunningNumber value={selectedArr.length} format={x => { const n = Math.round(x); return t`${n} selected · Clear` }} /></span>,
                    (() => { const n = '8'.repeat(String(listItems.length).length); return t`${n} selected · Clear` })(),
                  ]}</SameWidth>
                </Pill>
              )}
              {unreviewed.length > 0 && (
                <Pill
                  variant="primary"
                  onClick={reviewAll}
                  disabled={reviewingAll}
                  title={t`Accept every book waiting for review`}
                >
                  {reviewingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                  <Trans>Accept all</Trans>
                </Pill>
              )}
            </div>
          }
        />

        {firstLoad && <BinderySkeleton />}

        {/* The sections come and go with their content: when the last file
            of one is accepted, it fades and the sections below glide up. */}
        <AnimatePresence initial={false} onExitComplete={bumpList}>
        {!firstLoad && items.length === 0 && unreviewed.length === 0 && (
          <m.div
            key="empty"
            initial={STEP_IN}
            animate={{ opacity: refreshing ? 0.6 : 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={STEP_TRANSITION}
            className="flex flex-col items-center justify-center text-center gap-4 py-24"
          >
            <div>
              <p className="text-[17px] font-semibold text-foreground"><Trans>No files in the bindery</Trans></p>
              <p className="text-[15px] text-muted-foreground mt-1">
                <Trans>Drop files in the incoming folder to get started</Trans>
              </p>
            </div>
            <Pill variant="secondary" onClick={fetchAll} disabled={refreshing}>
              <RefreshCw className={cn('h-4 w-4', refreshing && 'animate-spin')} />
              <Trans>Refresh</Trans>
            </Pill>
          </m.div>
        )}

        {/* Series review: one decision per series, volumes confirmed as chips.
            Entries render for real series groups; loose singletons stay in the
            flat file list below. */}
        {!firstLoad && reviewGroups.length > 0 && (
          <m.section key="series" {...listItem} layoutDependency={listDep} className={cn('transition-opacity', loading && 'opacity-60')}>
            <SectionTitle size={20}><Trans>Series review</Trans></SectionTitle>
            <div className="relative mt-2 divide-y divide-border/70">
              <AnimatePresence initial={false} onExitComplete={bumpList}>
              {reviewGroups.map(g => (
                <m.div key={g.key} {...listItem} layoutDependency={listDep}>
                  <SeriesReviewEntry
                    group={g}
                    bookTypes={bookTypes}
                    libraries={libraries}
                    state={acceptingGroup === g.key ? 'running' : queuedGroups.includes(g.key) ? 'queued' : 'idle'}
                    onAccept={form => queueGroupAccept(g, form)}
                    onReject={() => setConfirmReject(g.files.map(f => f.path))}
                    onReviewFile={path => enterReview([path])}
                  />
                </m.div>
              ))}
              </AnimatePresence>
            </div>
          </m.section>
        )}

        {/* Waiting for review: auto-imported books not yet reviewed */}
        {unreviewed.length > 0 && (
          <m.section key="unreviewed" {...listItem} layoutDependency={listDep} className={cn('transition-opacity', unreviewedLoading && 'opacity-60')}>
            <SectionTitle
              size={20}
              action={<span className="text-[14px] font-normal text-muted-foreground tabular-nums"><RunningNumber value={unreviewed.length} /></span>}
            >
              <Trans>Waiting for review</Trans>
            </SectionTitle>
            <div className="relative mt-3 divide-y divide-border/70">
              <AnimatePresence initial={false} onExitComplete={bumpList}>
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

                const cover = book.cover_path ? coverSrc(book.id, { w: 400, v: book.updated_at }) : null
                return (
                  <m.div key={book.id} {...listItem} layoutDependency={listDep} className="group flex items-center gap-4 py-3.5 px-2">
                    <CoverLift>
                      <button
                        type="button"
                        data-cover-id={book.id}
                        onClick={e => { setPeekFrom({ origin: e.currentTarget.getBoundingClientRect(), cover, preview: { title: book.title, author: book.author, series: book.series, series_index: book.series_index } }); setPeekId(book.id) }}
                        aria-label={t`Quick look`}
                        className="relative block w-11 h-16 rounded-md overflow-hidden bg-muted"
                        style={{ boxShadow: 'var(--cover-bloom-fan)' }}
                      >
                        <CoverImage
                          src={cover}
                          alt=""
                          iconClassName="h-4 w-4"
                        />
                      </button>
                    </CoverLift>
                    {/* The title opens the book; its cover beside it grows into the page. */}
                    <BookLink
                      book={{ id: book.id, title: book.title, author: book.author, series: book.series, series_index: book.series_index, cover }}
                      className="flex-1 min-w-0 text-left touch-feedback"
                    >
                      <span className="block text-[16px] font-semibold text-foreground truncate hover:text-primary transition-colors">{book.title}</span>
                      {book.author && (
                        <span className="block text-[14px] text-muted-foreground truncate mt-0.5">{book.author}</span>
                      )}
                      <span className="block text-[13px] text-muted-foreground mt-0.5"><Trans>Added {timeAgo}</Trans></span>
                    </BookLink>
                    {format && <FormatBadge format={format} />}
                    <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100">
                      <Pill
                        variant="quiet"
                        icon
                        onClick={() => acceptUnreviewed(book.id)}
                        title={t`Accept`}
                        aria-label={t`Accept`}
                        className="hover:text-primary"
                      >
                        <Check className="h-4 w-4" />
                      </Pill>
                      <Pill
                        variant="destructive"
                        icon
                        onClick={() => setConfirmRejectUnreviewed(book.id)}
                        title={t`Reject`}
                        aria-label={t`Reject`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Pill>
                    </div>
                  </m.div>
                )
              })}
              </AnimatePresence>
            </div>
          </m.section>
        )}

        {/* Incoming files not under Series review, grouped by folder */}
        {listItems.length > 0 && (
          <m.section key="incoming" {...listItem} layoutDependency={listDep} className={cn('transition-opacity', loading && 'opacity-60')}>
            <SectionTitle
              size={20}
              action={<span className="text-[14px] font-normal text-muted-foreground tabular-nums"><RunningNumber value={listItems.length} /></span>}
            >
              <Trans>Incoming files</Trans>
            </SectionTitle>

            <Unfold open={hasSelection}>
              <div className="pt-4 flex items-center gap-2 flex-wrap">
                <Pill variant="secondary" onClick={() => enterReview(selectedArr)}>
                  <Eye className="h-4 w-4" /> <Trans>Review</Trans>
                </Pill>
                <Pill variant="secondary" onClick={() => quickAccept(selectedArr)} disabled={accepting}>
                  {accepting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
                  <Trans>Quick accept</Trans>
                </Pill>
                <Pill variant="destructive" onClick={() => setConfirmReject(selectedArr)}>
                  <Trash2 className="h-4 w-4" /> <Trans>Reject</Trans>
                </Pill>
                {libraries.some(l => l.can_edit) && (
                  <LibrariesSelect
                    value={listLibraryIds}
                    onChange={setListLibraryIds}
                    libraries={libraries}
                    className="w-56"
                    placeholder={t`Add to libraries…`}
                  />
                )}
              </div>
            </Unfold>

            <div ref={listRef} className="relative mt-3 divide-y divide-border/70 data-snap:[&_.group]:transition-none data-snap:[&_[data-check-box]]:transition-none">
              <AnimatePresence initial={false} onExitComplete={bumpList}>
              {folders.map(folder => {
                const folderItems = listItems.filter(i => i.folder === folder)
                const isCollapsed = collapsedFolders.has(folder)
                const allFolderSelected = folderItems.every(i => selected.has(i.path))
                return (
                  <m.div key={folder} {...listItem} layoutDependency={listDep}>
                    <div className="flex items-center gap-4 py-3 px-2">
                      <Checkbox
                        checked={allFolderSelected}
                        onChange={() => selectFolder(folder)}
                        aria-label={t`Select folder ${folder}`}
                        className="shrink-0 [&>span]:mt-0"
                      />
                      <button
                        className="flex items-center gap-2 flex-1 min-w-0 text-left touch-feedback"
                        onClick={() => toggleFolder(folder)}
                        aria-expanded={!isCollapsed}
                      >
                        <ChevronDown className={cn(CHEVRON, 'text-muted-foreground', isCollapsed && '-rotate-90')} />
                        <FolderOpen className="h-4 w-4 text-muted-foreground shrink-0" />
                        <span className="text-[15px] font-semibold text-foreground truncate">{folder}</span>
                        <span className="text-[14px] text-muted-foreground tabular-nums"><RunningNumber value={folderItems.length} /></span>
                      </button>
                    </div>
                    <Unfold open={!isCollapsed}>
                      <div className="relative divide-y divide-border/70 border-t border-border/70">
                        <AnimatePresence initial={false} onExitComplete={bumpList}>
                          {folderItems.map(item => renderRow(item, true))}
                        </AnimatePresence>
                      </div>
                    </Unfold>
                  </m.div>
                )
              })}

              {ungrouped.length > 0 && (
                <m.div key="__ungrouped" {...listItem} layoutDependency={listDep} className="relative divide-y divide-border/70">
                  {folders.length > 0 && (
                    <div className="flex items-center gap-2 py-3 px-2">
                      <span className="text-[15px] font-semibold text-muted-foreground"><Trans>Other files</Trans></span>
                      <span className="text-[14px] text-muted-foreground tabular-nums"><RunningNumber value={ungrouped.length} /></span>
                    </div>
                  )}
                  <AnimatePresence initial={false} onExitComplete={bumpList}>
                    {ungrouped.map(item => renderRow(item, false))}
                  </AnimatePresence>
                </m.div>
              )}
              </AnimatePresence>
            </div>
          </m.section>
        )}
        </AnimatePresence>
      </PageBody>
      </LayoutGroup>
      </m.div>

        {/* Quick accept progress bar, fixed at bottom, above keyboard shortcut FAB (z-40) */}
        {/* It slides up from the bottom edge and back down when dismissed. */}
        <AnimatePresence>
        {qaProgress && (
          <m.div
            key="qa"
            initial={{ y: '100%' }}
            animate={{ y: 0, transition: { duration: 0.24, ease: EASE } }}
            exit={{ y: '100%', transition: { duration: 0.18, ease: EASE } }}
            className="fixed bottom-0 left-0 right-0 z-50 bg-card border-t border-border/70 pl-6 pr-16 py-3 cursor-pointer"
            onClick={() => setQaModalOpen(true)}
          >
            {(() => {
              const done = qaProgress.items.filter(i => i.status === 'done').length
              const errors = qaProgress.items.filter(i => i.status === 'error').length
              const total = qaProgress.total
              return (
                <>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[14px] font-semibold">
                      <RunningNumber value={done} format={x => { const done = Math.round(x); return t`Quick accept: ${done}/${total} done` + (errors > 0 ? t`, ${errors} failed` : '') }} />
                    </span>
                    {qaProgress.items.every(i => i.status === 'done' || i.status === 'error') ? (
                      <Pill
                        variant="quiet"
                        className="h-7 px-3 text-[13px]"
                        onClick={e => { e.stopPropagation(); setQaProgress(null); setQaModalOpen(false) }}
                      >
                        <Trans>Dismiss</Trans>
                      </Pill>
                    ) : (
                      <span className="text-[13px] text-muted-foreground"><Trans>Click for details</Trans></span>
                    )}
                  </div>
                  <div
                    className="h-[6px] rounded-full overflow-hidden flex"
                    style={{ background: 'var(--rail)' }}
                  >
                    {/* Always rendered, so the first done file grows from 0 too. */}
                    <div className={BAR} style={{ width: `${(done / total) * 100}%` }} />
                    <div className={cn(BAR, 'bg-destructive')} style={{ width: `${(errors / total) * 100}%` }} />
                  </div>
                </>
              )
            })()}
          </m.div>
        )}
        </AnimatePresence>

        {/* Quick accept detail modal */}
        <ModalShell
          open={qaModalOpen && !!qaProgress}
          onClose={() => setQaModalOpen(false)}
          size="sm"
          title={<Trans>Quick accept progress</Trans>}
        >
              <div className="divide-y divide-border/70">
                {(qaProgress?.items ?? []).map(item => (
                  <div key={item.path} className="flex items-center gap-3 py-2.5">
                    <div className="shrink-0 w-4 h-4 flex items-center justify-center">
                      {item.status === 'done' ? (
                        <Check className="h-4 w-4 text-primary" />
                      ) : item.status === 'error' ? (
                        <X className="h-4 w-4 text-destructive" />
                      ) : item.status === 'pending' ? (
                        <div className="h-2 w-2 rounded-full bg-muted-foreground/30" />
                      ) : (
                        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <span className="text-[15px] truncate block">{item.title}</span>
                      {item.error && (
                        <span className="text-[13px] text-destructive">{item.error}</span>
                      )}
                    </div>
                    <span className="text-[13px] text-muted-foreground shrink-0">
                      {item.status === 'fetching' ? t`Fetching metadata...` : item.status === 'accepting' ? t`Importing…` : ''}
                    </span>
                  </div>
                ))}
              </div>
        </ModalShell>

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
          confirmLabel={rejectingUnreviewed ? t`Rejecting…` : t`Reject`}
          destructive
          onConfirm={() => confirmRejectUnreviewed !== null && doRejectUnreviewed(confirmRejectUnreviewed)}
          onCancel={() => setConfirmRejectUnreviewed(null)}
        />

        <AnimatePresence>
          {peekId != null && <QuickLook key={peekId} bookId={peekId} origin={peekFrom.origin} cover={peekFrom.cover} preview={peekFrom.preview} onClose={() => setPeekId(null)} />}
        </AnimatePresence>
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
    <m.div key="review" initial={viewMoved ? STEP_IN : false} animate={{ opacity: 1, y: 0 }} transition={STEP_TRANSITION}>
    <PageBody width="wide">
      <PageHeader
        eyebrow={<Trans>Bindery</Trans>}
        title={
          <span className="break-all">
            {isBatch
              ? (() => { const n = reviewItems.length; return t`Reviewing ${n} files` })()
              : currentItem?.filename ?? ''}
          </span>
        }
        actions={
          <Pill variant="quiet" onClick={() => transitionTo('list')}>
            <ArrowLeft className="h-4 w-4" /> <Trans>Back to list</Trans>
          </Pill>
        }
      />

      {/* Single file review */}
      {!isBatch && currentItem && currentForm && (
        <m.div
          key={currentItem.path}
          initial={reviewStepped ? STEP_IN : false}
          animate={{ opacity: 1, y: 0 }}
          transition={STEP_TRANSITION}
          className="grid grid-cols-1 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-10"
        >
          {/* Left: Form */}
          <div
            className={cn(
              'min-w-0 rounded-[var(--radius-panel)] transition-colors duration-300',
              acceptFlash && 'bg-primary/5'
            )}
          >
            {/* File info */}
            <div className="flex items-center gap-2 mb-5 pb-4 border-b border-border/70">
              <div className="flex items-center gap-2 min-w-0 flex-1">
                <FormatBadge format={currentItem.format} />
                <ContentTypeBadge type={currentForm.content_type} />
                <span className="text-[13px] text-muted-foreground truncate">{currentItem.path}</span>
              </div>
              <span className="text-[13px] text-muted-foreground shrink-0 tabular-nums">{formatBytes(currentItem.size)}</span>
            </div>

            <MetadataForm
              form={currentForm}
              onChange={(field, value) => updateForm(currentItem.path, field, value)}
              bookTypes={bookTypes}
              libraries={libraries}
            />

            {/* Action bar */}
            <div className="flex items-center gap-2 mt-6 pt-5 border-t border-border/70">
              <Pill
                variant="primary"
                onClick={() => acceptItem(currentItem.path)}
                disabled={accepting || !currentForm.title.trim()}
              >
                {accepting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                <Trans>Accept</Trans>
              </Pill>
              <Pill variant="secondary" onClick={skipItem}>
                <Trans>Skip</Trans>
              </Pill>
              <Pill variant="destructive" onClick={rejectCurrentAndAdvance} className="ml-auto">
                <Trash2 className="h-4 w-4" /> <Trans>Reject</Trans>
              </Pill>
            </div>
          </div>

          {/* Right: Candidates */}
          <div className="min-w-0">
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
        </m.div>
      )}

      {/* Batch review */}
      {isBatch && (
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-10">
          {/* Left: Batch form */}
          <div className="min-w-0 flex flex-col gap-7">
            {/* Shared fields */}
            <section>
              {(() => { const n = reviewItems.length; return <SectionTitle size={20}><Trans>Shared fields, applied to all {n} items</Trans></SectionTitle> })()}
              <div className="mt-4 space-y-3">
                <div>
                  <label className={LABEL_CLS}><Trans>Series</Trans></label>
                  <div className="flex gap-2">
                    <input
                      className={cn(INPUT_CLS, 'flex-1')}
                      value={batchSeries}
                      onChange={e => setBatchSeries(e.target.value)}
                      placeholder={t`Series name`}
                    />
                    <Pill variant="secondary" onClick={applyBatchSeries} className="shrink-0">
                      <Trans>Apply to all</Trans>
                    </Pill>
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
                    <Pill variant="secondary" onClick={applyBatchAuthor} className="shrink-0">
                      <Trans>Apply to all</Trans>
                    </Pill>
                  </div>
                </div>
                <div>
                  <label className={LABEL_CLS}><Trans>Book type</Trans></label>
                  <div className="flex gap-2">
                    <SelectMenu
                      className="flex-1"
                      value={batchBookTypeId}
                      onChange={setBatchBookTypeId}
                      options={[{ value: '', label: t`No type` },
                                ...bookTypes.map(bt => ({ value: String(bt.id), label: bt.label }))]}
                    />
                    <Pill variant="secondary" onClick={applyBatchBookType} className="shrink-0">
                      <Trans>Apply to all</Trans>
                    </Pill>
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
                      <Pill variant="secondary" onClick={applyBatchLibraries} className="shrink-0">
                        <Trans>Apply to all</Trans>
                      </Pill>
                    </div>
                  </div>
                )}
              </div>
            </section>

            {/* Per-item rows */}
            <section>
              <SectionTitle
                size={20}
                action={
                  <Pill variant="quiet" onClick={matchAll} disabled={matchingAll !== null || fetchingMeta} className="-mr-3">
                    {matchingAll !== null
                      ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Zap className="h-4 w-4" />}
                    {(() => { const total = reviewItems.length; return matchingAll !== null
                      ? t`Matching ${matchingAll}/${total}…`
                      : t`Match all` })()}
                  </Pill>
                }
              >
                <Trans>Per-file details</Trans>
              </SectionTitle>
              <div className="mt-3 divide-y divide-border/70">
                {reviewItems.map(item => {
                  const form = formData[item.path]
                  if (!form) return null
                  const isExpanded = expandedRows.has(item.path)
                  return (
                    <div
                      key={item.path}
                      className={cn(
                        'transition-colors',
                        suggestPath === item.path && 'bg-primary/5'
                      )}
                    >
                      {/* Compact row: clicking anywhere targets the suggestions panel */}
                      <div
                        className="flex items-center gap-3 py-2.5 px-2"
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
                          aria-expanded={isExpanded}
                          aria-label={t`Show all fields`}
                          className="shrink-0 text-muted-foreground hover:text-foreground transition-colors touch-feedback"
                        >
                          <ChevronDown className={cn(CHEVRON, !isExpanded && '-rotate-90')} />
                        </button>
                        <span className="text-[13px] text-muted-foreground truncate w-36 shrink-0 hidden sm:block" title={item.filename}>
                          {item.filename}
                        </span>
                        <input
                          className={cn(INPUT_CLS, 'flex-1 min-w-0')}
                          value={form.title}
                          onChange={e => updateForm(item.path, 'title', e.target.value)}
                          placeholder={t`Title`}
                        />
                        <input
                          className={cn(INPUT_CLS, 'w-16 text-center')}
                          type="number"
                          step="0.1"
                          value={form.series_index}
                          onChange={e => updateForm(item.path, 'series_index', e.target.value)}
                          placeholder="#"
                          title={t`Series index`}
                        />
                        <Select
                          className="w-auto cursor-pointer"
                          value={form.content_type}
                          onChange={e => updateForm(item.path, 'content_type', e.target.value)}
                          aria-label={t`Content type`}
                        >
                          <option value="volume">{t`Volume`}</option>
                          <option value="chapter">{t`Chapter`}</option>
                        </Select>
                      </div>
                      {/* Expanded full form */}
                      <Unfold open={isExpanded}>
                        <div className="px-2 pb-4 pt-1">
                          <MetadataForm
                            form={form}
                            onChange={(field, value) => updateForm(item.path, field, value)}
                            bookTypes={bookTypes}
                            libraries={libraries}
                          />
                        </div>
                      </Unfold>
                    </div>
                  )
                })}
              </div>
            </section>

            {/* Batch action bar */}
            <div className="flex items-center gap-2 pt-5 border-t border-border/70">
              <Pill variant="primary" onClick={acceptAll} disabled={accepting}>
                {accepting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                <Trans>Accept all</Trans>
              </Pill>
              <Pill variant="secondary" onClick={() => transitionTo('list')}>
                <Trans>Back to list</Trans>
              </Pill>
              <Pill variant="destructive" onClick={() => setConfirmReject(reviewItems.map(i => i.path))} className="ml-auto">
                <Trash2 className="h-4 w-4" /> <Trans>Reject all</Trans>
              </Pill>
            </div>
          </div>

          {/* Right: candidates for the targeted row; expand a row to retarget */}
          <div className="min-w-0">
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
    </PageBody>
    </m.div>

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
    </AppShell>
  )
}
