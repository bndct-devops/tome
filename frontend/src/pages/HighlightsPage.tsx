import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  Search, StickyNote, Loader2,
  CalendarHeart, Copy, X, ChevronDown, Info, ChevronsDownUp, ChevronsUpDown, Trash2, Pencil,
  Download,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import { cn } from '@/lib/utils'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { AppShell } from '@/components/AppShell'
import { CoverImage } from '@/components/CoverImage'
import { QuickLook } from '@/components/QuickLook'
import { PageBody, PageHeader, Pill, SectionTitle } from '@/components/page'

interface Highlight {
  id: number
  book_id: number
  book_title: string
  book_author: string | null
  book_cover: string | null
  highlighted_text: string | null
  note: string | null
  chapter: string | null
  color: string | null
  datetime: string | null
  synced_at: string | null
}

interface HighlightsResponse {
  total: number
  books: number
  items: Highlight[]
}

interface BookGroup {
  book_id: number
  title: string
  author: string | null
  cover: string | null
  items: Highlight[]
}

const PAGE_SIZE = 15

// KOReader highlight colours → a dot tint. Unknown colours fall back to the
// primary accent. Dots only render when the library actually uses more than one
// colour (see `showDots`), so single-colour readers don't get noise.
const COLOR_TINT: Record<string, string> = {
  red: 'bg-red-500', orange: 'bg-orange-500', yellow: 'bg-yellow-500',
  green: 'bg-green-500', blue: 'bg-blue-500', purple: 'bg-purple-500',
  gray: 'bg-gray-400', grey: 'bg-gray-400',
}

function groupByBook(items: Highlight[]): BookGroup[] {
  const order: number[] = []
  const map = new Map<number, BookGroup>()
  for (const h of items) {
    let g = map.get(h.book_id)
    if (!g) {
      g = { book_id: h.book_id, title: h.book_title, author: h.book_author, cover: h.book_cover, items: [] }
      map.set(h.book_id, g)
      order.push(h.book_id)
    }
    g.items.push(h)
  }
  return order.map(id => map.get(id)!)
}

function groupToMarkdown(g: BookGroup): string {
  const lines = [`## ${g.title}${g.author ? ` — ${g.author}` : ''}`, '']
  for (const h of g.items) {
    if (h.chapter) lines.push(`*${h.chapter}*`)
    if (h.highlighted_text) lines.push(`> ${h.highlighted_text}`)
    if (h.note) { const note = h.note; lines.push('', t`Note: ${note}`) }
    lines.push('')
  }
  return lines.join('\n').trimEnd()
}

// Wrap case-insensitive matches of `q` in <mark>, leaving the rest as text.
function mark(text: string, q: string): ReactNode {
  if (!q) return text
  const esc = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = text.split(new RegExp(`(${esc})`, 'ig'))
  return parts.map((p, i) =>
    p.toLowerCase() === q.toLowerCase()
      ? <mark key={i} className="bg-primary/25 text-foreground rounded px-0.5">{p}</mark>
      : <span key={i}>{p}</span>
  )
}

function parseKo(dt: string | null): Date | null {
  if (!dt) return null
  const d = new Date(dt.replace(' ', 'T'))
  return isNaN(d.getTime()) ? null : d
}

function shortDate(dt: string | null): string | null {
  const d = parseKo(dt)
  return d ? d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' }) : null
}

function fullInfo(h: Highlight): string {
  const parts: string[] = []
  const d = parseKo(h.datetime)
  if (d) { const when = d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); parts.push(t`Highlighted ${when}`) }
  if (h.chapter) parts.push(h.chapter)
  if (h.color) { const color = h.color; parts.push(t`${color} highlight`) }
  if (h.synced_at) {
    const s = new Date(h.synced_at)
    if (!isNaN(s.getTime())) { const when = s.toLocaleDateString(undefined, { dateStyle: 'medium' }); parts.push(t`Synced to Tome ${when}`) }
  }
  return parts.join('  ·  ')
}

const ROW_TOOL = 'opacity-100 sm:opacity-0 sm:group-hover:opacity-100 focus-visible:opacity-100 h-8 w-8'

function HighlightRow({ h, q, showDot, onDelete, onEdited }: { h: Highlight; q: string; showDot: boolean; onDelete: (h: Highlight) => void; onEdited: (h: Highlight, note: string | null) => void }) {
  const tint = h.color ? (COLOR_TINT[h.color.toLowerCase()] ?? 'bg-primary/50') : 'bg-primary/40'
  const date = shortDate(h.datetime)
  const [confirming, setConfirming] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  async function saveNote() {
    if (saving) return
    setSaving(true)
    try {
      const note = draft.trim() || null
      await api.put(`/annotations/${h.id}`, { note })
      onEdited(h, note)
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  return (
    <li className="group border-t border-border/70 py-4">
      {h.highlighted_text && (
        <p className="text-[15px] text-foreground leading-relaxed">
          {q ? mark(h.highlighted_text, q) : h.highlighted_text}
        </p>
      )}
      {editing ? (
        <div className="mt-3 flex flex-col gap-2">
          <textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            autoFocus
            rows={2}
            placeholder={t`Your note. It syncs back to KOReader on the next sync.`}
            className="w-full rounded-lg bg-secondary px-3 py-2 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <div className="flex items-center gap-1">
            <Pill variant="secondary" onClick={saveNote} disabled={saving}>
              {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {saving ? t`Saving…` : t`Save note`}
            </Pill>
            <Pill variant="quiet" onClick={() => setEditing(false)}><Trans>Cancel</Trans></Pill>
          </div>
        </div>
      ) : h.note ? (
        <p className="mt-2 flex items-start gap-2 text-[14px] text-muted-foreground">
          <StickyNote className="w-3.5 h-3.5 mt-[3px] shrink-0" />
          <span className="leading-relaxed">{q ? mark(h.note, q) : h.note}</span>
        </p>
      ) : null}
      <div className="mt-2 flex items-center justify-between gap-3 min-h-8">
        <div className="flex items-center gap-1.5 min-w-0 text-[12px] text-muted-foreground">
          {showDot && <span className={cn('w-2 h-2 rounded-full shrink-0', tint)} />}
          {h.chapter && <span className="truncate">{q ? mark(h.chapter, q) : h.chapter}</span>}
          {h.chapter && date && <span aria-hidden="true">·</span>}
          {date && (
            <span className="inline-flex items-center gap-1 shrink-0 cursor-help" title={fullInfo(h)}>
              {date}
              <Info className="w-3 h-3" />
            </span>
          )}
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          {confirming ? (
            <>
              <Pill variant="destructive" className="h-8 px-3 text-[13px] text-destructive" onClick={() => onDelete(h)}>
                <Trans>Delete</Trans>
              </Pill>
              <Pill variant="quiet" className="h-8 px-3 text-[13px]" onClick={() => setConfirming(false)}>
                <Trans>Cancel</Trans>
              </Pill>
            </>
          ) : (
            <>
              <Pill
                variant="quiet"
                icon
                onClick={() => { setDraft(h.note ?? ''); setEditing(e => !e) }}
                title={h.note ? t`Edit note` : t`Add a note`}
                aria-label={h.note ? t`Edit note` : t`Add a note`}
                className={ROW_TOOL}
              >
                <Pencil className="w-3.5 h-3.5" />
              </Pill>
              <Pill
                variant="destructive"
                icon
                onClick={() => setConfirming(true)}
                title={t`Delete this highlight`}
                aria-label={t`Delete this highlight`}
                className={ROW_TOOL}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </Pill>
            </>
          )}
        </div>
      </div>
    </li>
  )
}

export function HighlightsPage() {
  const { toast } = useToast()
  const [items, setItems] = useState<Highlight[]>([])
  const [total, setTotal] = useState(0)
  const [books, setBooks] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')
  const [onThisDay, setOnThisDay] = useState(false)
  const [onlyNotes, setOnlyNotes] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const [peek, setPeek] = useState<number | null>(null)
  const [loadedOnce, setLoadedOnce] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 250)
    return () => clearTimeout(t)
  }, [search])

  const buildParams = useCallback((offset: number) => {
    const p = new URLSearchParams()
    if (debounced) p.set('q', debounced)
    if (onThisDay) p.set('on_this_day', '1')
    if (onlyNotes) p.set('only_notes', '1')
    p.set('limit', String(PAGE_SIZE))
    p.set('offset', String(offset))
    return p
  }, [debounced, onThisDay, onlyNotes])

  // Reset + first page whenever the query/filter changes. The old list stays
  // on screen, dimmed, until the new one lands.
  useEffect(() => {
    const ctrl = new AbortController()
    setLoading(true)
    setError(null)
    api.get<HighlightsResponse>(`/annotations?${buildParams(0).toString()}`, ctrl.signal)
      .then(d => { setItems(d.items); setTotal(d.total); setBooks(d.books); setLoadedOnce(true) })
      .catch(e => { if (e.name !== 'AbortError') setError(e.message ?? t`Failed to load`) })
      .finally(() => setLoading(false))
    return () => ctrl.abort()
  }, [buildParams])

  async function loadMore() {
    setLoadingMore(true)
    try {
      const d = await api.get<HighlightsResponse>(`/annotations?${buildParams(items.length).toString()}`)
      setItems(prev => [...prev, ...d.items])
      setTotal(d.total)
    } catch (e) {
      toast.error((e as Error).message ?? t`Failed to load more`)
    } finally {
      setLoadingMore(false)
    }
  }

  const groups = useMemo(() => groupByBook(items), [items])
  const hasMore = items.length < total
  // While searching, force every book open so matches are never hidden behind a
  // collapsed header. Manual collapse state is preserved and returns on clear.
  const searching = debounced.length > 0
  // Only show colour dots when the loaded highlights actually use >1 colour.
  const showDots = useMemo(
    () => new Set(items.map(i => i.color).filter(Boolean)).size >= 2,
    [items],
  )
  const allCollapsed = groups.length > 0 && groups.every(g => collapsed.has(g.book_id))

  function toggleBook(id: number) {
    setCollapsed(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }
  function toggleAll() {
    setCollapsed(allCollapsed ? new Set() : new Set(groups.map(g => g.book_id)))
  }

  // Export everything that matches the current filters, not just loaded pages.
  // 10000 matches the server's limit cap — anything above it is a 422, which
  // is how the old limit=2000 silently broke this export against the previous
  // le=1000 cap.
  async function fetchAllMatching(): Promise<HighlightsResponse> {
    const p = new URLSearchParams()
    if (debounced) p.set('q', debounced)
    if (onThisDay) p.set('on_this_day', '1')
    if (onlyNotes) p.set('only_notes', '1')
    p.set('limit', '10000')
    return api.get<HighlightsResponse>(`/annotations?${p.toString()}`)
  }

  async function copyAll() {
    try {
      const d = await fetchAllMatching()
      const md = groupByBook(d.items).map(groupToMarkdown).join('\n\n')
      await navigator.clipboard.writeText(md)
      { const n = d.items.length; toast.success(t`Copied ${n} highlights as Markdown`) }
    } catch (e) {
      toast.error((e as Error).message ?? t`Export failed`)
    }
  }

  async function downloadAll() {
    try {
      const d = await fetchAllMatching()
      const md = groupByBook(d.items).map(groupToMarkdown).join('\n\n') + '\n'
      const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `tome-highlights-${new Date().toISOString().slice(0, 10)}.md`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      { const n = d.items.length; toast.success(t`Downloaded ${n} highlights`) }
    } catch (e) {
      toast.error((e as Error).message ?? t`Export failed`)
    }
  }

  async function copyGroup(g: BookGroup) {
    await navigator.clipboard.writeText(groupToMarkdown(g))
    { const n = g.items.length; const title = g.title; toast.success(t`Copied ${n} from “${title}”`) }
  }

  function noteEdited(h: Highlight, note: string | null) {
    setItems(prev => prev.map(x => (x.id === h.id ? { ...x, note } : x)))
    toast.success(note ? t`Note saved. It syncs to your devices.` : t`Note removed`)
  }

  async function deleteHighlight(h: Highlight) {
    // Was this the last loaded highlight for its book? If so the book drops off too.
    const lastOfBook = !items.some(x => x.book_id === h.book_id && x.id !== h.id)
    // With unloaded pages the book may still have highlights beyond what we see,
    // so a local decrement would under-count — refetch the counts instead.
    const fullyLoaded = items.length >= total
    try {
      await api.delete(`/annotations/${h.id}`)
      setItems(prev => prev.filter(x => x.id !== h.id))
      setTotal(t => Math.max(0, t - 1))
      if (lastOfBook) {
        if (fullyLoaded) {
          setBooks(b => Math.max(0, b - 1))
        } else {
          const p = buildParams(0)
          p.set('limit', '1')
          api.get<HighlightsResponse>(`/annotations?${p.toString()}`)
            .then(d => { setBooks(d.books); setTotal(d.total) })
            .catch(() => {})
        }
      }
      toast.success(t`Highlight deleted`)
    } catch (e) {
      toast.error((e as Error).message ?? t`Failed to delete highlight`)
    }
  }

  // Keyboard shortcuts: "/" focuses search, Esc clears it, "c" toggles
  // collapse-all, "n" only-notes, "e" downloads the export. Plain keys are
  // ignored while typing (Esc still works, matching the other pages).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement
      const typing = el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable
      if (e.key === 'Escape' && typing) {
        setSearch('')
        searchRef.current?.blur()
        return
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey || peek != null) return
      if (e.key === '/') {
        e.preventDefault()
        searchRef.current?.focus()
      } else if (e.key === 'c') {
        toggleAll()
      } else if (e.key === 'n') {
        setOnlyNotes(v => !v)
      } else if (e.key === 'e') {
        void downloadAll()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const isEmpty = !loading && !error && groups.length === 0
  const neverSynced = isEmpty && !debounced && !onThisDay && !onlyNotes

  function clearFilters() {
    setSearch('')
    setDebounced('')
    setOnThisDay(false)
    setOnlyNotes(false)
  }

  return (
    <AppShell>
      <PageBody width="reading">
        <PageHeader
          eyebrow={total > 0 ? plural(books, { one: `${total} from # book`, other: `${total} from # books` }) : undefined}
          title={<Trans>Highlights</Trans>}
          actions={groups.length > 0 ? (
            <>
              <Pill variant="quiet" onClick={copyAll} title={t`Copy all as Markdown`}>
                <Copy className="w-4 h-4" />
                <Trans>Copy</Trans>
              </Pill>
              <Pill variant="quiet" onClick={downloadAll} title={t`Download as Markdown file`}>
                <Download className="w-4 h-4" />
                <Trans>Export</Trans>
              </Pill>
            </>
          ) : undefined}
        />

        {/* Controls. On phones the search gets its own full-width row; squeezed
            next to three labelled pills it shrank to a keyhole. */}
        <div className="flex flex-wrap items-center gap-1.5">
          <div className="relative basis-full sm:basis-auto sm:flex-1 sm:mr-1.5">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
            <input
              ref={searchRef}
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder={t`Search your highlights…`}
              className="w-full h-9 pl-10 pr-9 rounded-full bg-secondary text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                aria-label={t`Clear search`}
                className="absolute right-2 top-1/2 -translate-y-1/2 h-6 w-6 grid place-items-center rounded-full text-muted-foreground hover:text-foreground"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
          <Pill
            variant={onThisDay ? 'secondary' : 'quiet'}
            onClick={() => setOnThisDay(v => !v)}
            aria-pressed={onThisDay}
            title={t`Highlights you made on this day in past years`}
            className={cn(onThisDay && 'text-foreground')}
          >
            <CalendarHeart className="w-4 h-4" />
            <Trans>On this day</Trans>
          </Pill>
          <Pill
            variant={onlyNotes ? 'secondary' : 'quiet'}
            onClick={() => setOnlyNotes(v => !v)}
            aria-pressed={onlyNotes}
            title={t`Only highlights that carry one of your notes`}
          >
            <StickyNote className="w-4 h-4" />
            <Trans>Only notes</Trans>
          </Pill>
          {groups.length > 0 && !searching && (
            <Pill
              variant={allCollapsed ? 'secondary' : 'quiet'}
              onClick={toggleAll}
              aria-pressed={allCollapsed}
              title={allCollapsed ? t`Expand all` : t`Collapse all`}
            >
              {allCollapsed ? <ChevronsUpDown className="w-4 h-4" /> : <ChevronsDownUp className="w-4 h-4" />}
              {allCollapsed ? t`Expand all` : t`Collapse all`}
            </Pill>
          )}
        </div>

        {loading && !loadedOnce ? (
          <div className="flex justify-center py-16">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <p className="text-[15px] text-destructive">{error}</p>
        ) : isEmpty ? (
          <div className="flex flex-col items-center text-center py-16 gap-4">
            {neverSynced ? (
              <div className="max-w-sm">
                <p className="text-[17px] font-semibold text-foreground"><Trans>No highlights yet</Trans></p>
                <p className="mt-1.5 text-[15px] text-muted-foreground leading-relaxed">
                  <Trans>Highlights and notes you make in KOReader sync here automatically.
                  Highlight a passage on your device, then sync. It shows up in this
                  commonplace book.</Trans>
                </p>
              </div>
            ) : (
              <>
                <p className="text-[15px] text-muted-foreground">
                  {onThisDay ? t`Nothing highlighted on this day yet.` : t`No highlights match your search.`}
                </p>
                <Pill variant="secondary" onClick={clearFilters}><Trans>Show all highlights</Trans></Pill>
              </>
            )}
          </div>
        ) : (
          <div className={cn('flex flex-col gap-9 transition-opacity', loading && 'opacity-50')}>
            {groups.map(g => {
              const isCollapsed = !searching && collapsed.has(g.book_id)
              return (
                <section key={g.book_id}>
                  <div className="flex items-center gap-4">
                    <button
                      type="button"
                      onClick={() => setPeek(g.book_id)}
                      aria-label={(() => { const title = g.title; return t`Quick look at ${title}` })()}
                      className="relative w-11 h-16 rounded-[8px] overflow-hidden bg-muted shrink-0"
                      style={{ boxShadow: 'var(--cover-bloom-fan)' }}
                    >
                      <CoverImage src={g.cover} alt="" iconClassName="w-4 h-4" />
                    </button>
                    <div className="min-w-0 flex-1">
                      <SectionTitle
                        size={20}
                        className="items-center"
                        action={
                          <div className="flex items-center">
                            <Pill
                              variant="quiet"
                              onClick={() => toggleBook(g.book_id)}
                              aria-expanded={!isCollapsed}
                              title={isCollapsed ? t`Expand` : t`Collapse`}
                              className="px-3 tabular-nums"
                            >
                              {g.items.length}
                              <ChevronDown className={cn('w-4 h-4 transition-transform', isCollapsed && '-rotate-90')} />
                            </Pill>
                            <Pill
                              variant="quiet"
                              icon
                              onClick={() => copyGroup(g)}
                              title={t`Copy this book’s highlights as Markdown`}
                              aria-label={t`Copy this book’s highlights as Markdown`}
                            >
                              <Copy className="w-4 h-4" />
                            </Pill>
                          </div>
                        }
                      >
                        <button
                          type="button"
                          onClick={() => toggleBook(g.book_id)}
                          className="text-left leading-tight line-clamp-2 hover:text-primary transition-colors"
                        >
                          {g.title}
                        </button>
                      </SectionTitle>
                      {g.author && <p className="mt-1 text-[14px] text-muted-foreground truncate">{g.author}</p>}
                    </div>
                  </div>
                  {/* grid-rows 1fr↔0fr gives a smooth height collapse without measuring. */}
                  <div className={cn('grid transition-[grid-template-rows] duration-300 ease-out', isCollapsed ? 'grid-rows-[0fr]' : 'grid-rows-[1fr]')}>
                    <div className="overflow-hidden">
                      <ul className="mt-3">
                        {g.items.map(h => <HighlightRow key={h.id} h={h} q={debounced} showDot={showDots} onDelete={deleteHighlight} onEdited={noteEdited} />)}
                      </ul>
                    </div>
                  </div>
                </section>
              )
            })}

            {hasMore && (
              <div className="flex justify-center">
                <Pill variant="secondary" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore && <Loader2 className="w-4 h-4 animate-spin" />}
                  {(() => { const left = total - items.length; return t`Load more (${left} left)` })()}
                </Pill>
              </div>
            )}
          </div>
        )}
      </PageBody>
      {peek != null && <QuickLook bookId={peek} onClose={() => setPeek(null)} />}
    </AppShell>
  )
}
