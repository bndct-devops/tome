import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  Search, Quote, StickyNote, Loader2, BookOpen,
  CalendarHeart, Copy, X, ChevronDown, Info, ChevronsDownUp, ChevronsUpDown, Trash2, Pencil,
  Download,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import { cn } from '@/lib/utils'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { AppShell } from '@/components/AppShell'

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

function HighlightCard({ h, q, showDot, onDelete, onEdited }: { h: Highlight; q: string; showDot: boolean; onDelete: (h: Highlight) => void; onEdited: (h: Highlight, note: string | null) => void }) {
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
    <li className="group rounded-lg border border-border bg-muted/40 px-3.5 py-3">
      <div className="flex items-start justify-between gap-3 mb-1.5">
        <div className="flex items-center gap-1.5 min-w-0">
          {showDot && <span className={cn('w-2 h-2 rounded-full shrink-0', tint)} />}
          {h.chapter && (
            <p className="text-xs text-muted-foreground/70 truncate">{q ? mark(h.chapter, q) : h.chapter}</p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {date && (
            <span
              className="flex items-center gap-1 text-[11px] text-muted-foreground/50 cursor-help"
              title={fullInfo(h)}
            >
              {date}
              <Info className="w-3 h-3" />
            </span>
          )}
          {confirming ? (
            <span className="flex items-center gap-1.5 text-[11px]">
              <button
                onClick={() => onDelete(h)}
                className="font-medium text-destructive hover:underline"
              >
                <Trans>Delete</Trans>
              </button>
              <button
                onClick={() => setConfirming(false)}
                className="text-muted-foreground hover:text-foreground"
              >
                <Trans>Cancel</Trans>
              </button>
            </span>
          ) : (
            <>
              <button
                onClick={() => { setDraft(h.note ?? ''); setEditing(e => !e) }}
                title={h.note ? t`Edit note` : t`Add a note`}
                aria-label={h.note ? t`Edit note` : t`Add a note`}
                className="p-1 -m-1 rounded text-muted-foreground/50 hover:text-foreground transition-all opacity-60 sm:opacity-0 sm:group-hover:opacity-100 focus:opacity-100"
              >
                <Pencil className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setConfirming(true)}
                title={t`Delete this highlight`}
                aria-label={t`Delete this highlight`}
                className="p-1 -m-1 rounded text-muted-foreground/50 hover:text-destructive transition-all opacity-60 sm:opacity-0 sm:group-hover:opacity-100 focus:opacity-100"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </>
          )}
        </div>
      </div>
      {h.highlighted_text && (
        <p className="text-sm text-foreground leading-relaxed border-l-2 border-primary/40 pl-2.5">
          {q ? mark(h.highlighted_text, q) : h.highlighted_text}
        </p>
      )}
      {editing ? (
        <div className="mt-2 flex flex-col gap-1.5">
          <textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            autoFocus
            rows={2}
            placeholder={t`Your note — syncs back to KOReader on the next sync`}
            className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <span className="flex items-center gap-2 text-xs">
            <button onClick={saveNote} disabled={saving} className="font-medium text-primary hover:underline disabled:opacity-50">
              {saving ? t`Saving…` : t`Save note`}
            </button>
            <button onClick={() => setEditing(false)} className="text-muted-foreground hover:text-foreground"><Trans>Cancel</Trans></button>
          </span>
        </div>
      ) : h.note ? (
        <p className="mt-2 flex items-start gap-1.5 text-sm text-muted-foreground">
          <StickyNote className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span className="leading-relaxed">{q ? mark(h.note, q) : h.note}</span>
        </p>
      ) : null}
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

  // Reset + first page whenever the query/filter changes.
  useEffect(() => {
    const ctrl = new AbortController()
    setLoading(true)
    setError(null)
    api.get<HighlightsResponse>(`/annotations?${buildParams(0).toString()}`, ctrl.signal)
      .then(d => { setItems(d.items); setTotal(d.total); setBooks(d.books) })
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
    toast.success(note ? t`Note saved — syncs to your devices` : t`Note removed`)
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
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return
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

  return (
    <AppShell
      actions={groups.length > 0 ? (
        <div className="flex items-center gap-1.5">
          <button
            onClick={copyAll}
            title={t`Copy all as Markdown`}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border hover:bg-muted transition-all"
          >
            <Copy className="w-3.5 h-3.5" />
            <span className="hidden sm:inline"><Trans>Copy</Trans></span>
          </button>
          <button
            onClick={downloadAll}
            title={t`Download as Markdown file`}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border hover:bg-muted transition-all"
          >
            <Download className="w-3.5 h-3.5" />
            <span className="hidden sm:inline"><Trans>Export</Trans></span>
          </button>
        </div>
      ) : undefined}
    >
      <div className="max-w-3xl mx-auto px-4 py-6 space-y-5">
        <div className="flex items-baseline justify-between gap-3">
          <h1 className="font-display text-xl text-foreground"><Trans>Highlights</Trans></h1>
          {total > 0 && (
            <span className="text-xs text-muted-foreground/60">
              {plural(books, { one: `${total} from # book`, other: `${total} from # books` })}
            </span>
          )}
        </div>
        {/* Controls — on phones the search input gets its own full-width row;
            squeezed next to three labelled buttons it shrank to a ~90px
            keyhole ("Se…") (UX sweep finding). */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative basis-full sm:basis-auto sm:flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground/60" />
            <input
              ref={searchRef}
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder={t`Search your highlights…`}
              className="w-full pl-9 pr-9 py-2 rounded-lg bg-muted/50 border border-border text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 p-1 rounded text-muted-foreground hover:text-foreground"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
          <button
            onClick={() => setOnThisDay(v => !v)}
            title={t`Highlights you made on this day in past years`}
            className={cn(
              'flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border transition-all whitespace-nowrap',
              onThisDay
                ? 'bg-primary/10 border-primary/30 text-primary'
                : 'border-border text-muted-foreground hover:bg-muted'
            )}
          >
            <CalendarHeart className="w-3.5 h-3.5" />
            <Trans>On this day</Trans>
          </button>
          <button
            onClick={() => setOnlyNotes(v => !v)}
            title={t`Only highlights that carry one of your notes`}
            className={cn(
              'flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border transition-all whitespace-nowrap',
              onlyNotes
                ? 'bg-primary/10 border-primary/30 text-primary'
                : 'border-border text-muted-foreground hover:bg-muted'
            )}
          >
            <StickyNote className="w-3.5 h-3.5" />
            <span className="hidden sm:inline"><Trans>Only notes</Trans></span>
          </button>
          {groups.length > 0 && !searching && (
            <button
              onClick={toggleAll}
              title={allCollapsed ? t`Expand all` : t`Collapse all`}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-border text-muted-foreground hover:bg-muted transition-all whitespace-nowrap"
            >
              {allCollapsed ? <ChevronsUpDown className="w-3.5 h-3.5" /> : <ChevronsDownUp className="w-3.5 h-3.5" />}
              {allCollapsed ? t`Expand` : t`Collapse`}
            </button>
          )}
        </div>

        {loading ? (
          <div className="flex justify-center py-16">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="px-3 py-2 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive">
            {error}
          </div>
        ) : isEmpty ? (
          <div className="flex flex-col items-center justify-center text-center py-20 gap-3 text-muted-foreground">
            <Quote className="w-8 h-8 opacity-40" />
            {neverSynced ? (
              <>
                <p className="text-sm font-medium text-foreground"><Trans>No highlights yet</Trans></p>
                <p className="text-xs max-w-xs leading-relaxed">
                  <Trans>Highlights and notes you make in KOReader sync here automatically.
                  Highlight a passage on your device, then sync — it’ll show up in this
                  commonplace book.</Trans>
                </p>
              </>
            ) : (
              <p className="text-sm">
                {onThisDay ? t`Nothing highlighted on this day — yet.` : t`No highlights match your search.`}
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-6">
            {groups.map(g => {
              const isCollapsed = !searching && collapsed.has(g.book_id)
              return (
                <section key={g.book_id} className="animate-card-appear">
                  <div className="flex items-center gap-3 mb-3">
                    <Link to={`/books/${g.book_id}`} className="shrink-0">
                      <div className="w-10 h-14 rounded bg-muted overflow-hidden flex items-center justify-center">
                        {g.cover ? (
                          <img
                            src={g.cover}
                            alt=""
                            className="w-full h-full object-cover"
                            onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
                          />
                        ) : (
                          <BookOpen className="w-4 h-4 text-muted-foreground" />
                        )}
                      </div>
                    </Link>
                    <button
                      onClick={() => toggleBook(g.book_id)}
                      className="flex-1 min-w-0 flex items-center gap-2 text-left group"
                    >
                      <ChevronDown
                        className={cn('w-4 h-4 text-muted-foreground/60 shrink-0 transition-transform', isCollapsed && '-rotate-90')}
                      />
                      <span className="min-w-0">
                        <span className="block font-display text-base text-foreground group-hover:text-primary transition-colors line-clamp-1">
                          {g.title}
                        </span>
                        {g.author && <span className="block text-xs text-muted-foreground truncate">{g.author}</span>}
                      </span>
                      <span className="text-xs text-muted-foreground/60 shrink-0 ml-auto">
                        {g.items.length}
                      </span>
                    </button>
                    <button
                      onClick={() => copyGroup(g)}
                      title={t`Copy this book’s highlights as Markdown`}
                      className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors shrink-0"
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  {/* grid-rows 1fr↔0fr gives a smooth height collapse without measuring. */}
                  <div className={cn('grid transition-[grid-template-rows] duration-300 ease-out', isCollapsed ? 'grid-rows-[0fr]' : 'grid-rows-[1fr]')}>
                    <div className="overflow-hidden">
                      <ul className="space-y-2.5">
                        {g.items.map(h => <HighlightCard key={h.id} h={h} q={debounced} showDot={showDots} onDelete={deleteHighlight} onEdited={noteEdited} />)}
                      </ul>
                    </div>
                  </div>
                </section>
              )
            })}

            {hasMore && (
              <div className="flex justify-center pt-1">
                <button
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border border-border hover:bg-muted transition-all disabled:opacity-60"
                >
                  {loadingMore ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                  {(() => { const left = total - items.length; return t`Load more (${left} left)` })()}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </AppShell>
  )
}
