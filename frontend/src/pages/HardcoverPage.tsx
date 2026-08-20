import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  BookMarked, ExternalLink, Layers, Loader2, RefreshCw, Search, X, AlertTriangle, ChevronLeft,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { CoverImage } from '@/components/CoverImage'
import { SeriesStackCard } from '@/components/SeriesStackCard'
import type { Book } from '@/lib/books'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import { cn } from '@/lib/utils'
import { Trans, useLingui } from '@lingui/react/macro'
import { t, plural, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'

interface HardcoverStatus {
  linked: boolean
  username?: string | null
  token_status?: string | null
  sync_enabled?: boolean
  last_synced_at?: string | null
  sync_running?: boolean
}

interface HcBook {
  book_id: number
  title: string
  series: string | null
  series_index: number | null
  author: string | null
  isbn: string | null
  cover_path: string | null
  state: 'matched' | 'unmatched' | 'excluded' | 'pending'
  slug: string | null
  method: string | null
  pages: number | null
  status: string
  rating: number | null
  progress_pct: number | null
  synced_at: string | null
  error: string | null
}

interface Candidate {
  hardcover_book_id: number
  title: string
  authors: string[]
  slug: string | null
  users_count: number
  cover_url: string | null
  series: string | null
}

type Filter = 'all' | 'matched' | 'unmatched' | 'excluded' | 'errors'

const FILTERS: { key: Filter; label: MessageDescriptor }[] = [
  { key: 'all', label: msg`All` },
  { key: 'matched', label: msg`Synced` },
  { key: 'unmatched', label: msg`Not matched` },
  { key: 'excluded', label: msg`Excluded` },
  { key: 'errors', label: msg`Errors` },
]

function displayTitle(b: HcBook): string {
  // Series-titled volumes ("Black Summoner") need the volume to be readable —
  // but a title that already carries its volume ("Berserk, Vol. 1") must not
  // get a second "· Vol. 1" appended (UX sweep finding).
  if (b.series && b.series_index != null) {
    const idx = String(b.series_index)
    const alreadyThere = new RegExp(`vol(?:ume)?\\.?\\s*0*${idx.replace('.', '\\.')}\\b`, 'i').test(b.title)
    if (alreadyThere) return b.title
    const base = b.title === b.series ? b.series : b.title
    return t`${base} · Vol. ${idx}`
  }
  return b.title
}

const STATE_LABEL: Record<HcBook['state'], MessageDescriptor> = {
  matched: msg`Synced`,
  unmatched: msg`Not matched`,
  excluded: msg`Excluded`,
  pending: msg`Not synced yet`,
}

const GROUP_KEY = 'tome_hardcover_group'

// SeriesStackCard consumes the dashboard's Book shape — adapt an HcBook group.
function toBookShape(rep: HcBook, volumes: HcBook[]): Book {
  return {
    id: rep.book_id,
    title: rep.title,
    subtitle: null,
    author: rep.author,
    series: rep.series,
    series_index: rep.series_index,
    year: null,
    language: null,
    word_count: null,
    status: 'active',
    content_type: 'volume',
    cover_path: rep.cover_path,
    added_at: '',
    files: [],
    tags: [],
    library_ids: [],
    book_type_id: null,
    stack_cover_ids: volumes.slice(1, 3).filter(v => v.cover_path).map(v => v.book_id),
  }
}

type Cell =
  | { kind: 'book'; b: HcBook }
  | { kind: 'stack'; volumes: HcBook[] }

export function HardcoverPage() {
  const { t } = useLingui()
  const { toast } = useToast()
  const [status, setStatus] = useState<HardcoverStatus | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [books, setBooks] = useState<HcBook[] | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [grouped, setGrouped] = useState(() => localStorage.getItem(GROUP_KEY) === 'true')
  const [expandedSeries, setExpandedSeries] = useState<string | null>(null)

  function persistGroup(v: boolean) {
    setGrouped(v)
    localStorage.setItem(GROUP_KEY, String(v))
    setExpandedSeries(null)
  }
  // Picker modal state
  const [pickFor, setPickFor] = useState<HcBook | null>(null)
  const [pickQuery, setPickQuery] = useState('')
  const [pickResults, setPickResults] = useState<Candidate[] | null>(null)
  const [pickBusy, setPickBusy] = useState(false)

  const loadStatus = useCallback(async () => {
    try {
      const s = await api.get<HardcoverStatus>('/hardcover/status')
      setStatus(s)
      return s
    } catch (err) {
      if (err instanceof Error && /disabled/i.test(err.message)) setUnavailable(true)
      return null
    }
  }, [])

  const loadBooks = useCallback(async () => {
    try {
      setBooks(await api.get<HcBook[]>('/hardcover/books'))
    } catch { /* status handler surfaces errors */ }
  }, [])

  const load = useCallback(async () => {
    const s = await loadStatus()
    if (s?.linked) await loadBooks()
  }, [loadStatus, loadBooks])

  useEffect(() => { void load() }, [load])

  // Poll only the cheap status while a manual sync runs; refetch the full book
  // list once when the run finishes (not every tick).
  useEffect(() => {
    if (!status?.sync_running) return
    const t = window.setInterval(async () => {
      const s = await loadStatus()
      if (s && !s.sync_running) void loadBooks()
    }, 4000)
    return () => window.clearInterval(t)
  }, [status?.sync_running, loadStatus, loadBooks])

  const filtered = useMemo(() => {
    if (!books) return []
    let out = books
    if (filter === 'errors') out = out.filter(b => b.error != null)
    else if (filter !== 'all') out = out.filter(b => b.state === filter)
    const q = query.trim().toLowerCase()
    if (q) {
      out = out.filter(b =>
        b.title.toLowerCase().includes(q)
        || (b.series ?? '').toLowerCase().includes(q)
        || (b.author ?? '').toLowerCase().includes(q))
    }
    return out
  }, [books, filter, query])

  // Grouped view: fold same-series volumes into one stacked card (matching the
  // All Books "Group series" toggle); an expanded series shows its volumes.
  const cells = useMemo<Cell[]>(() => {
    if (expandedSeries) {
      return filtered.filter(b => b.series === expandedSeries).map(b => ({ kind: 'book', b }))
    }
    if (!grouped) return filtered.map(b => ({ kind: 'book', b }))
    const bySeries = new Map<string, HcBook[]>()
    const out: Cell[] = []
    for (const b of filtered) {
      if (!b.series) { out.push({ kind: 'book', b }); continue }
      let group = bySeries.get(b.series)
      if (!group) {
        group = []
        bySeries.set(b.series, group)
        out.push({ kind: 'stack', volumes: group })
      }
      group.push(b)
    }
    // Single-volume "stacks" render as plain cards.
    return out.map(c => (c.kind === 'stack' && c.volumes.length === 1)
      ? { kind: 'book' as const, b: c.volumes[0] } : c)
  }, [filtered, grouped, expandedSeries])

  const counts = useMemo(() => {
    const c = { all: books?.length ?? 0, matched: 0, unmatched: 0, excluded: 0, errors: 0 }
    for (const b of books ?? []) {
      if (b.state === 'matched') c.matched++
      else if (b.state === 'unmatched') c.unmatched++
      else if (b.state === 'excluded') c.excluded++
      if (b.error) c.errors++
    }
    return c
  }, [books])

  async function syncNow() {
    try {
      const r = await api.post<{ started: boolean }>('/hardcover/sync-now', {})
      toast.info(r.started ? t`Sync started` : t`A sync is already running`)
      setStatus(s => s ? { ...s, sync_running: true } : s)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Could not start sync`)
    }
  }

  async function rematch(b: HcBook, mode: 'retry' | 'exclude') {
    try {
      await api.post(`/hardcover/books/${b.book_id}/rematch`, { mode })
      toast.info(mode === 'retry'
        ? t`Match cleared — re-matching on the next sync`
        : t`Excluded from Hardcover sync`)
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Action failed`)
    }
  }

  async function openPicker(b: HcBook) {
    setPickFor(b)
    const base = b.series && b.series_index != null
      // eslint-disable-next-line lingui/no-unlocalized-strings -- search query, not copy
      ? `${b.series} Vol. ${b.series_index}` : b.title
    const q = b.author ? `${base} ${b.author}` : base
    setPickQuery(q)
    setPickResults(null)
    await runPickSearch(q)
  }

  async function runPickSearch(q: string) {
    setPickBusy(true)
    try {
      setPickResults(await api.get<Candidate[]>(`/hardcover/search?q=${encodeURIComponent(q)}`))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Search failed`)
      setPickResults([])
    } finally {
      setPickBusy(false)
    }
  }

  async function pin(c: Candidate) {
    if (!pickFor) return
    setPickBusy(true)
    try {
      await api.post(`/hardcover/books/${pickFor.book_id}/match`, { hardcover_book_id: c.hardcover_book_id })
      { const title = c.title; toast.success(t`Matched to “${title}”`) }
      setPickFor(null)
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Could not set match`)
    } finally {
      setPickBusy(false)
    }
  }

  if (unavailable) {
    return (
      <AppShell>
        <div className="p-8 text-sm text-muted-foreground"><Trans>Hardcover sync is disabled on this server.</Trans></div>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="p-4 sm:p-6 lg:p-8 max-w-6xl mx-auto">
        <div className="flex items-center gap-3 mb-1">
          <BookMarked className="w-5 h-5 text-primary" />
          {/* eslint-disable-next-line lingui/no-unlocalized-strings -- product name */}
          <h1 className="font-display text-2xl text-foreground">Hardcover</h1>
        </div>
        <p className="text-sm text-muted-foreground mb-6">
          <Trans>What your books sync to on Hardcover — audit matches, fix wrong ones, exclude books.</Trans>
        </p>

        {!status ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin" /> <Trans>Loading…</Trans>
          </div>
        ) : !status.linked ? (
          <div className="rounded-xl border border-border bg-card p-6 text-sm text-foreground/90 max-w-xl">
            <p className="mb-2"><Trans>No Hardcover account linked yet.</Trans></p>
            <p className="text-muted-foreground">
              <Trans>Link your personal API token in{' '}
              <Link to="/settings" className="text-primary hover:underline">Settings → Hardcover</Link>{' '}
              — syncing starts right after.</Trans>
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 mb-5">
              <button
                onClick={() => void syncNow()}
                disabled={!!status.sync_running}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-sm font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-50"
              >
                <RefreshCw className={cn('w-4 h-4', status.sync_running && 'animate-spin')} />
                {status.sync_running ? t`Syncing…` : t`Sync now`}
              </button>
              <span className="text-xs text-muted-foreground mr-2">
                @{status.username}
                {status.last_synced_at && (() => { const when = new Date(status.last_synced_at + 'Z').toLocaleString(i18n.locale); return t` · last synced ${when}` })()}
              </span>
              <div className="flex items-center gap-1 ml-auto">
                <button
                  onClick={() => persistGroup(!grouped)}
                  title={grouped ? t`Show individual volumes` : t`Group volumes by series`}
                  aria-pressed={grouped}
                  className={cn(
                    'flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium border transition-all mr-1',
                    grouped
                      ? 'border-primary/40 bg-primary/10 text-primary'
                      : 'border-border text-muted-foreground hover:text-foreground hover:bg-muted'
                  )}
                >
                  <Layers className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline"><Trans>Group series</Trans></span>
                </button>
                {FILTERS.map(f => {
                  const n = counts[f.key]
                  if (f.key !== 'all' && n === 0) return null
                  return (
                    <button
                      key={f.key}
                      onClick={() => setFilter(f.key)}
                      className={cn(
                        'px-2.5 py-1 rounded-md text-xs font-medium border transition-colors',
                        filter === f.key
                          ? 'bg-primary/10 border-primary/40 text-primary'
                          : 'border-border text-muted-foreground hover:text-foreground hover:bg-muted'
                      )}
                    >
                      {i18n._(f.label)} <span className="opacity-60">{n}</span>
                    </button>
                  )
                })}
              </div>
              <div className="relative">
                <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder={t`Filter…`}
                  className="pl-7 pr-2 py-1.5 w-40 rounded-md border border-border bg-background text-xs text-foreground placeholder:text-muted-foreground/60 focus:border-primary focus:outline-none"
                />
              </div>
            </div>

            {expandedSeries && (
              <div className="flex items-center gap-2 mb-4">
                <button
                  onClick={() => setExpandedSeries(null)}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-border text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                >
                  <ChevronLeft className="w-3.5 h-3.5" /> <Trans>All books</Trans>
                </button>
                <span className="text-sm font-medium text-foreground">{expandedSeries}</span>
              </div>
            )}

            {books == null ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" /> <Trans>Loading books…</Trans>
              </div>
            ) : cells.length === 0 ? (
              <p className="text-sm text-muted-foreground"><Trans>No books in this view.</Trans></p>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
                {cells.map(cell => cell.kind === 'stack' ? (
                  <SeriesStackCard
                    key={`stack-${cell.volumes[0].series}`}
                    book={toBookShape(cell.volumes[0], cell.volumes)}
                    count={cell.volumes.length}
                    view="small"
                    onOpen={() => setExpandedSeries(cell.volumes[0].series)}
                  />
                ) : (() => { const b = cell.b; return (
                  <div key={b.book_id} className="rounded-xl border border-border bg-card overflow-hidden flex flex-col">
                    <Link to={`/books/${b.book_id}`} className="relative block aspect-[2/3] bg-muted">
                      <CoverImage
                        src={`/api/books/${b.book_id}/cover`}
                        alt={b.title}
                        imgClassName="absolute inset-0 w-full h-full object-cover"
                        iconClassName="w-8 h-8"
                      />
                      <span className={cn(
                        'absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded text-[10px] font-medium',
                        b.error ? 'bg-warning/90 text-black'
                          : b.state === 'matched' ? 'bg-success/90 text-black'
                          : b.state === 'excluded' ? 'bg-muted-foreground/80 text-black'
                          : 'bg-black/60 text-white'
                      )}>
                        {b.error ? t`Error` : i18n._(STATE_LABEL[b.state])}
                      </span>
                    </Link>
                    <div className="p-2.5 flex flex-col gap-1 grow">
                      <p className="text-xs font-medium text-foreground leading-snug line-clamp-2">{displayTitle(b)}</p>
                      {b.slug ? (
                        <a
                          href={`https://hardcover.app/books/${b.slug}`}
                          target="_blank" rel="noopener noreferrer"
                          className="text-[11px] text-primary hover:underline inline-flex items-center gap-0.5 truncate"
                        >
                          <span className="truncate">{b.slug}</span>
                          <ExternalLink className="w-3 h-3 shrink-0" />
                        </a>
                      ) : (
                        <p className="text-[11px] text-muted-foreground truncate">
                          {b.state === 'excluded' ? t`not syncing` : b.isbn ?? t`no ISBN`}
                        </p>
                      )}
                      {b.error && (
                        <p className="text-[11px] text-warning inline-flex items-center gap-1">
                          <AlertTriangle className="w-3 h-3 shrink-0" />
                          <span className="truncate" title={b.error}>{b.error}</span>
                        </p>
                      )}
                      <div className="mt-auto pt-1.5 flex items-center gap-2 text-[11px]">
                        <button
                          onClick={() => void openPicker(b)}
                          className="text-primary hover:underline"
                          title={t`Search Hardcover and pick the record yourself`}
                        >
                          <Trans>Pick</Trans>
                        </button>
                        {b.state === 'matched' && (
                          <button
                            onClick={() => void rematch(b, 'retry')}
                            className="text-muted-foreground hover:text-foreground"
                            title={t`Remove our entry from Hardcover and re-match automatically`}
                          >
                            <Trans>Re-match</Trans>
                          </button>
                        )}
                        {b.state === 'excluded' ? (
                          <button
                            onClick={() => void rematch(b, 'retry')}
                            className="text-muted-foreground hover:text-foreground"
                            title={t`Start matching this book again`}
                          >
                            <Trans>Include</Trans>
                          </button>
                        ) : (
                          <button
                            onClick={() => void rematch(b, 'exclude')}
                            className="text-muted-foreground hover:text-destructive"
                            title={t`Never sync this book`}
                          >
                            <Trans>Exclude</Trans>
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                ) })())}
              </div>
            )}
          </>
        )}
      </div>

      {/* Pick-a-record modal */}
      {pickFor && (
        <div
          className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center p-4 pt-[10vh]"
          onClick={() => setPickFor(null)}
        >
          <div
            className="w-full max-w-lg rounded-xl border border-border bg-card shadow-xl overflow-hidden"
            onClick={e => e.stopPropagation()}
          >
            <div className="p-4 border-b border-border">
              <div className="flex items-center justify-between gap-2 mb-3">
                <p className="text-sm font-medium text-foreground truncate">
                  {(() => { const title = displayTitle(pickFor); return <Trans>Match “{title}” to…</Trans> })()}
                </p>
                <button onClick={() => setPickFor(null)} aria-label={t`Close`}
                        className="text-muted-foreground hover:text-foreground">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="flex gap-2">
                <input
                  value={pickQuery}
                  onChange={e => setPickQuery(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') void runPickSearch(pickQuery) }}
                  autoFocus
                  placeholder={t`Search Hardcover…`}
                  className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
                />
                <button
                  onClick={() => void runPickSearch(pickQuery)}
                  disabled={pickBusy}
                  className="px-3 py-2 rounded-md border border-border text-sm text-foreground hover:bg-muted disabled:opacity-50"
                >
                  <Trans>Search</Trans>
                </button>
              </div>
            </div>
            <div className="max-h-[50vh] overflow-y-auto">
              {pickResults == null ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground p-4">
                  <Loader2 className="w-4 h-4 animate-spin" /> <Trans>Searching…</Trans>
                </div>
              ) : pickResults.length === 0 ? (
                <p className="text-sm text-muted-foreground p-4"><Trans>No results — try a different query.</Trans></p>
              ) : (
                <ul className="divide-y divide-border">
                  {pickResults.map(c => (
                    <li key={c.hardcover_book_id}>
                      <button
                        onClick={() => void pin(c)}
                        disabled={pickBusy}
                        className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-muted/60 transition-colors disabled:opacity-50"
                      >
                        {c.cover_url ? (
                          <img src={c.cover_url} alt="" className="w-9 h-14 object-cover rounded-sm shrink-0" />
                        ) : (
                          <span className="w-9 h-14 rounded-sm bg-muted shrink-0" />
                        )}
                        <span className="min-w-0">
                          <span className="block text-sm text-foreground truncate">{c.title}</span>
                          <span className="block text-xs text-muted-foreground truncate">
                            {c.authors.join(', ') || t`unknown author`}
                            {c.users_count > 0 && ' · ' + plural(c.users_count, { one: '# reader', other: '# readers' })}
                          </span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </AppShell>
  )
}
