import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ExternalLink, Layers, Loader2, RefreshCw, Search, X, AlertTriangle, ChevronLeft,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { PageBody, PageHeader, Pill } from '@/components/page'
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
import { coverSrc } from '@/lib/covers'

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

  // eslint-disable-next-line lingui/no-unlocalized-strings -- product name
  const pageTitle = 'Hardcover'
  const subtitle = t`What your books sync to on Hardcover. Audit matches, fix wrong ones, exclude books.`

  if (unavailable) {
    return (
      <AppShell>
        <PageBody width="wide">
          <PageHeader title={pageTitle} subtitle={subtitle} />
          <p className="py-16 text-center text-[15px] text-muted-foreground"><Trans>Hardcover sync is disabled on this server.</Trans></p>
        </PageBody>
      </AppShell>
    )
  }

  const linked = !!status?.linked
  const eyebrow = linked && status
    ? (() => {
        const user = status.username ?? ''
        if (!status.last_synced_at) return t`@${user}`
        const when = new Date(status.last_synced_at + 'Z').toLocaleDateString(i18n.locale)
        return t`@${user} · last synced ${when}`
      })()
    : undefined

  return (
    <AppShell>
      <PageBody width="wide">
        <PageHeader
          eyebrow={eyebrow}
          title={pageTitle}
          className="[&>div:last-child]:shrink [&>div:last-child]:min-w-0"
          subtitle={subtitle}
          actions={linked && status ? (
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                <input
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder={t`Filter…`}
                  aria-label={t`Filter books`}
                  className="h-9 w-44 rounded-full bg-secondary pl-9 pr-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </div>
              <Pill
                variant={grouped ? 'secondary' : 'quiet'}
                onClick={() => persistGroup(!grouped)}
                title={grouped ? t`Show individual volumes` : t`Group volumes by series`}
                aria-pressed={grouped}
                className={cn(grouped && 'text-primary')}
              >
                <Layers className="w-4 h-4" />
                <Trans>Group series</Trans>
              </Pill>
              <Pill variant="primary" onClick={() => void syncNow()} disabled={!!status.sync_running}>
                <RefreshCw className={cn('w-4 h-4', status.sync_running && 'animate-spin')} />
                {status.sync_running ? t`Syncing…` : t`Sync now`}
              </Pill>
            </div>
          ) : undefined}
        />

        {!status ? (
          <div className="flex items-center gap-2 text-[15px] text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin" /> <Trans>Loading…</Trans>
          </div>
        ) : !status.linked ? (
          <div className="py-16 flex flex-col items-center gap-4 text-center">
            <p className="text-[15px] text-muted-foreground max-w-[46ch]">
              <Trans>No Hardcover account linked yet. Link your personal API token in Settings and syncing starts right after.</Trans>
            </p>
            <Link
              to="/settings"
              className="inline-flex items-center h-9 px-4 rounded-full bg-primary text-primary-foreground text-[14px] font-semibold hover:bg-primary/90 transition-colors"
            >
              <Trans>Open Settings</Trans>
            </Link>
          </div>
        ) : (
          <div className="flex flex-col gap-6 -mt-1">
            <div className="flex flex-wrap items-center gap-1.5">
              {FILTERS.map(f => {
                const n = counts[f.key]
                if (f.key !== 'all' && n === 0) return null
                const active = filter === f.key
                return (
                  <Pill
                    key={f.key}
                    variant={active ? 'secondary' : 'quiet'}
                    onClick={() => setFilter(f.key)}
                    aria-pressed={active}
                  >
                    {i18n._(f.label)}
                    <span className="tabular-nums text-muted-foreground">{n}</span>
                  </Pill>
                )
              })}
            </div>

            {expandedSeries && (
              <div className="flex items-center gap-2 -mt-2 min-w-0">
                <Pill variant="quiet" onClick={() => setExpandedSeries(null)} className="-ml-3 shrink-0">
                  <ChevronLeft className="w-4 h-4" /> <Trans>All books</Trans>
                </Pill>
                <span className="font-display text-[20px] font-bold text-foreground truncate">{expandedSeries}</span>
              </div>
            )}

            {books == null ? (
              <div className="flex items-center gap-2 text-[15px] text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" /> <Trans>Loading books…</Trans>
              </div>
            ) : cells.length === 0 ? (
              <p className="py-16 text-center text-[15px] text-muted-foreground"><Trans>No books in this view.</Trans></p>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-x-6 gap-y-8">
                {cells.map(cell => cell.kind === 'stack' ? (
                  <SeriesStackCard
                    key={`stack-${cell.volumes[0].series}`}
                    book={toBookShape(cell.volumes[0], cell.volumes)}
                    count={cell.volumes.length}
                    view="small"
                    onOpen={() => setExpandedSeries(cell.volumes[0].series)}
                  />
                ) : (() => { const b = cell.b; return (
                  <div key={b.book_id} className="flex flex-col min-w-0">
                    <Link
                      to={`/books/${b.book_id}`}
                      className="relative block aspect-[2/3] rounded-[8px] overflow-hidden bg-muted"
                      style={{ boxShadow: 'var(--cover-bloom-fan)' }}
                    >
                      <CoverImage
                        src={coverSrc(b.book_id, { w: 400 })}
                        alt={b.title}
                        imgClassName="absolute inset-0 w-full h-full object-cover"
                        iconClassName="w-8 h-8"
                      />
                      <span className={cn(
                        'absolute top-2 left-2 inline-flex items-center h-6 px-2.5 rounded-full text-[12px] font-semibold backdrop-blur-sm',
                        b.error ? 'bg-warning text-black'
                          : b.state === 'matched' ? 'bg-success text-black'
                          : b.state === 'excluded' ? 'bg-black/55 text-white/80'
                          : 'bg-black/60 text-white'
                      )}>
                        {b.error ? t`Error` : i18n._(STATE_LABEL[b.state])}
                      </span>
                    </Link>
                    <div className="pt-3 flex flex-col gap-1 grow min-w-0">
                      <Link
                        to={`/books/${b.book_id}`}
                        className="text-[15px] font-semibold text-foreground leading-snug line-clamp-2 hover:text-primary transition-colors"
                      >
                        {displayTitle(b)}
                      </Link>
                      {b.slug ? (
                        <a
                          href={`https://hardcover.app/books/${b.slug}`}
                          target="_blank" rel="noopener noreferrer"
                          className="text-[12px] text-muted-foreground hover:text-primary inline-flex items-center gap-1 min-w-0"
                        >
                          <span className="truncate">{b.slug}</span>
                          <ExternalLink className="w-3 h-3 shrink-0" />
                        </a>
                      ) : (
                        <p className="text-[12px] text-muted-foreground truncate">
                          {b.state === 'excluded' ? t`not syncing` : b.isbn ?? t`no ISBN`}
                        </p>
                      )}
                      {b.error && (
                        <p className="text-[12px] text-warning inline-flex items-center gap-1 min-w-0">
                          <AlertTriangle className="w-3 h-3 shrink-0" />
                          <span className="truncate" title={b.error}>{b.error}</span>
                        </p>
                      )}
                      <div className="mt-auto pt-1 flex items-center gap-3 text-[13px] font-medium">
                        <button
                          onClick={() => void openPicker(b)}
                          className="text-primary hover:underline underline-offset-4"
                          title={t`Search Hardcover and pick the record yourself`}
                        >
                          <Trans>Pick</Trans>
                        </button>
                        {b.state === 'matched' && (
                          <button
                            onClick={() => void rematch(b, 'retry')}
                            className="text-muted-foreground hover:text-foreground"
                            title={t`Remove the entry Tome created on Hardcover and re-match automatically. An entry that was already on your profile is kept.`}
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
                            title={t`Never sync this book. Nothing is removed from Hardcover.`}
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
          </div>
        )}
      </PageBody>

      {/* Pick-a-record modal */}
      {pickFor && (
        <div
          className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center p-4 pt-[10vh]"
          onClick={() => setPickFor(null)}
        >
          <div
            className="w-full max-w-lg rounded-[var(--radius-panel)] bg-card shadow-xl overflow-hidden"
            onClick={e => e.stopPropagation()}
          >
            <div className="p-5 pb-4">
              <div className="flex items-center justify-between gap-2 mb-4">
                <p className="font-display text-[20px] font-bold text-foreground truncate">
                  {(() => { const title = displayTitle(pickFor); return <Trans>Match “{title}” to…</Trans> })()}
                </p>
                <Pill variant="quiet" icon onClick={() => setPickFor(null)} aria-label={t`Close`} className="-mr-2 shrink-0">
                  <X className="w-4 h-4" />
                </Pill>
              </div>
              <div className="flex gap-2">
                <input
                  value={pickQuery}
                  onChange={e => setPickQuery(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') void runPickSearch(pickQuery) }}
                  autoFocus
                  placeholder={t`Search Hardcover…`}
                  className="flex-1 min-w-0 h-9 rounded-full bg-secondary px-4 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
                <Pill variant="primary" onClick={() => void runPickSearch(pickQuery)} disabled={pickBusy}>
                  <Trans>Search</Trans>
                </Pill>
              </div>
            </div>
            <div className="max-h-[50vh] overflow-y-auto px-5 pb-3 border-t border-border/70">
              {pickResults == null ? (
                <div className="flex items-center gap-2 text-[14px] text-muted-foreground py-4">
                  <Loader2 className="w-4 h-4 animate-spin" /> <Trans>Searching…</Trans>
                </div>
              ) : pickResults.length === 0 ? (
                <p className="text-[14px] text-muted-foreground py-4"><Trans>No results. Try a different query.</Trans></p>
              ) : (
                <ul>
                  {pickResults.map((c, i) => (
                    <li key={c.hardcover_book_id} className={cn(i > 0 && 'border-t border-border/70')}>
                      <button
                        onClick={() => void pin(c)}
                        disabled={pickBusy}
                        className="w-full flex items-center gap-4 py-3 text-left rounded-lg hover:bg-secondary/60 transition-colors disabled:opacity-50"
                      >
                        {c.cover_url ? (
                          <img src={c.cover_url} alt="" className="w-10 h-[60px] object-cover rounded-md shrink-0" />
                        ) : (
                          <span className="w-10 h-[60px] rounded-md bg-muted shrink-0" />
                        )}
                        <span className="min-w-0">
                          <span className="block text-[15px] font-semibold text-foreground truncate">{c.title}</span>
                          <span className="block text-[13px] text-muted-foreground truncate">
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
