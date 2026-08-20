// Public, read-only view of a shared shelf — /share/:token, no login.
// Deliberately minimal surface: covers, identity, tags, description, the
// owner's rating and highlights. No downloads, no reader, no links into the
// app beyond the landing page. Renders noindex so links stay unlisted.
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { BookOpen, ChevronDown, Quote, Star, StickyNote } from 'lucide-react'
import { TomeMark } from '@/components/TomeMark'
import { cn } from '@/lib/utils'
import { Trans, Plural } from '@lingui/react/macro'
import { t, plural, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'
import { applyTheme, getStoredTheme } from '@/lib/theme'

interface SharedHighlight {
  text: string | null
  note: string | null
  chapter: string | null
}

interface SharedBook {
  id: number
  title: string
  author: string | null
  series: string | null
  series_index: number | null
  description: string | null
  tags: string[]
  rating: number | null
  stats: SharedStats | null
  highlights: SharedHighlight[]
}

interface SharedStats {
  status: 'reading' | 'read' | null
  total_seconds: number
  reading_days: number
  first_day: string | null
  last_day: string | null
  finished_on: string | null
  activity: { date: string; seconds: number }[]
}

interface SharedArc {
  name: string
  start_index: number
  end_index: number
  description: string | null
}

interface SharedSeries {
  author: string | null
  description: string | null
  status: string | null
  rating: number | null
  arcs: SharedArc[]
}

interface ShareResponse {
  kind: 'shelf' | 'series' | 'book'
  title: string
  totals: { books: number; read: number; total_seconds: number }
  books: SharedBook[]
  series?: SharedSeries
}

/** Group a series' volumes into arc sections (same rule as the app's series
 *  page: a volume belongs to the arc whose index range contains it). */
function groupByArc(books: SharedBook[], arcs: SharedArc[]): { arc: SharedArc | null; books: SharedBook[] }[] {
  const used = new Set<number>()
  const groups: { arc: SharedArc | null; books: SharedBook[] }[] = []
  for (const arc of arcs) {
    const members = books.filter(b =>
      b.series_index != null && !used.has(b.id) &&
      b.series_index >= arc.start_index && b.series_index <= arc.end_index)
    members.forEach(b => used.add(b.id))
    if (members.length) groups.push({ arc, books: members })
  }
  const rest = books.filter(b => !used.has(b.id))
  if (rest.length) groups.push({ arc: null, books: rest })
  return groups
}

function fmtDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  if (h === 0) return t`${m}m`
  return m === 0 ? t`${h}h` : t`${h}h ${m}m`
}

function fmtDay(iso: string): string {
  return new Date(iso + 'T00:00:00Z').toLocaleDateString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
  })
}

const SERIES_STATUS_WORDS: Record<string, MessageDescriptor> = {
  ongoing: msg`ongoing`, finished: msg`finished`, hiatus: msg`hiatus`,
}

const DAY_MS = 86400000
// eslint-disable-next-line lingui/no-unlocalized-strings -- ISO suffix
const dayNum = (d: string) => Math.floor(Date.parse(`${d}T00:00:00Z`) / DAY_MS)

/** The owner's reading of this book as a day-intensity strip — the same
 *  visual language as the app's Timeline ticks. */
function ActivityStrip({ stats }: { stats: SharedStats }) {
  if (!stats.activity.length || !stats.first_day || !stats.last_day) return null
  const d0 = dayNum(stats.first_day)
  const span = Math.max(dayNum(stats.last_day) - d0 + 1, 1)
  const sorted = stats.activity.map(d => d.seconds).sort((a, z) => a - z)
  const ref = sorted[Math.floor(sorted.length * 0.9)] || 1
  return (
    <div className="mt-2.5">
      <div className="relative h-4 overflow-hidden rounded bg-primary/10">
        {stats.activity.map(d => (
          <span
            key={d.date}
            title={`${fmtDay(d.date)} · ${fmtDuration(d.seconds)}`}
            className="absolute bottom-0 top-0 bg-primary"
            style={{
              left: `${((dayNum(d.date) - d0) / span) * 100}%`,
              width: `${Math.max(100 / span, 0.6)}%`,
              minWidth: 2,
              opacity: 0.35 + 0.6 * Math.min(1, Math.sqrt(d.seconds / ref)),
            }}
          />
        ))}
      </div>
      <p className="mt-1 flex justify-between text-[10px] text-muted-foreground/60">
        <span>{fmtDay(stats.first_day)}</span>
        <span>{fmtDay(stats.last_day)}</span>
      </p>
    </div>
  )
}

function Rating({ value }: { value: number }) {
  return (
    <span className="flex items-center gap-0.5" title={`${value} stars`}>
      {[1, 2, 3, 4, 5].map(i => (
        <Star
          key={i}
          className={cn(
            'h-3.5 w-3.5',
            i <= Math.round(value) ? 'fill-primary text-primary' : 'text-muted-foreground/40',
          )}
        />
      ))}
    </span>
  )
}

function SharedBookCard({ b, defaultOpen = false }: { b: SharedBook; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const hasMore = !!b.description || b.highlights.length > 0 || (b.stats?.activity.length ?? 0) > 1
  return (
    <li className="rounded-xl border border-border bg-card p-4">
      <div className="flex gap-4">
        <img
          src={`/api/books/${b.id}/cover`}
          alt=""
          loading="lazy"
          className="h-32 w-[85px] shrink-0 rounded-md object-cover shadow-sm"
        />
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-base leading-tight text-foreground">{b.title}</h3>
          {b.series && (
            <p className="mt-0.5 text-xs text-primary">
              {b.series}
              {b.series_index != null ? ` #${b.series_index}` : ''}
            </p>
          )}
          {b.author && <p className="mt-0.5 text-sm text-muted-foreground">{b.author}</p>}
          {b.rating != null && <div className="mt-1.5"><Rating value={b.rating} /></div>}
          {b.stats && (
            <p className="mt-1.5 text-xs text-muted-foreground">
              {b.stats.status === 'read' ? t`Read` : b.stats.status === 'reading' ? t`Reading` : null}
              {b.stats.total_seconds > 0 && (
                <>
                  {b.stats.status ? ' · ' : ''}
                  {(() => { const dur = fmtDuration(b.stats.total_seconds); return plural(b.stats.reading_days, { one: `${dur} over # day`, other: `${dur} over # days` }) })()}
                </>
              )}
              {(() => { const d = b.stats.finished_on ? fmtDay(b.stats.finished_on) : ''; return b.stats.finished_on ? t` · finished ${d}` : '' })()}
            </p>
          )}
          {b.tags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {b.tags.map(t => (
                <span key={t} className="rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                  {t}
                </span>
              ))}
            </div>
          )}
          {hasMore && (
            <button
              onClick={() => setOpen(o => !o)}
              className="mt-2 flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')} />
              {open ? t`Less` : b.highlights.length > 0 ? plural(b.highlights.length, { one: 'Description & # highlight', other: 'Description & # highlights' }) : t`Description`}
            </button>
          )}
        </div>
      </div>
      {open && (
        <div className="mt-3 border-t border-border/60 pt-3">
          {b.stats && b.stats.activity.length > 1 && <div className="mb-3"><ActivityStrip stats={b.stats} /></div>}
          {b.description && (
            <p className="text-sm leading-relaxed text-muted-foreground">{b.description}</p>
          )}
          {b.highlights.length > 0 && (
            <ul className="mt-3 flex flex-col gap-2.5">
              {b.highlights.map((h, i) => (
                <li key={i} className="text-sm">
                  {h.chapter && <p className="text-[11px] text-muted-foreground/70">{h.chapter}</p>}
                  {h.text && (
                    <p className="border-l-2 border-primary/40 pl-2.5 leading-relaxed text-foreground">
                      {h.text}
                    </p>
                  )}
                  {h.note && (
                    <p className="mt-1 flex items-start gap-1.5 text-muted-foreground">
                      <StickyNote className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span className="leading-relaxed">{h.note}</span>
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  )
}

export function SharePage() {
  const { token } = useParams()
  const [data, setData] = useState<ShareResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    applyTheme(getStoredTheme())
    // Belt-and-braces with the server's X-Robots-Tag.
    const meta = document.createElement('meta')
    meta.name = 'robots'
    // eslint-disable-next-line lingui/no-unlocalized-strings -- robots directive
    meta.content = 'noindex, nofollow'
    document.head.appendChild(meta)
    return () => { document.head.removeChild(meta) }
  }, [])

  useEffect(() => {
    // Plain fetch on purpose: no auth token attached, no 401 redirect logic.
    fetch(`/api/share/${token}`)
      .then(async r => {
        if (!r.ok) throw new Error((await r.json()).detail || t`Not found`)
        return r.json()
      })
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : t`Not found`))
  }, [token])

  if (error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background p-6 text-center">
        <BookOpen className="h-10 w-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">{error}</p>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <p className="animate-pulse text-sm text-muted-foreground"><Trans>Loading…</Trans></p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/50 safe-top">
        <div className="mx-auto flex h-14 max-w-3xl items-center gap-2.5 px-4">
          <Quote className="h-4 w-4 text-primary" />
          <h1 className="font-display text-lg text-foreground">{data.title}</h1>
          <span className="text-xs text-muted-foreground">
            {data.kind === 'book' ? t`· a shared book` : data.kind === 'series' ? t`· a shared series` : t`· a shared shelf`}
            {data.kind !== 'book' ? ' · ' + plural(data.totals.books, { one: '# book', other: '# books' }) : ''}
            {(() => { const n = data.totals.read; return n > 0 && data.kind !== 'book' ? t` · ${n} read` : '' })()}
            {(() => { const dur = fmtDuration(data.totals.total_seconds); return data.totals.total_seconds > 0 ? t` · ${dur} read time` : '' })()}
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-6">
        {data.kind === 'series' && data.series && (
          <section className="mb-6 rounded-xl border border-border bg-card p-5">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-display text-xl text-foreground">{data.title}</h2>
              {data.series.status && (
                <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] capitalize text-muted-foreground">
                  {SERIES_STATUS_WORDS[data.series.status] ? i18n._(SERIES_STATUS_WORDS[data.series.status]) : data.series.status}
                </span>
              )}
            </div>
            {data.series.author && (
              <p className="mt-0.5 text-sm text-muted-foreground">{data.series.author}</p>
            )}
            {data.series.rating != null && (
              <div className="mt-1.5"><Rating value={data.series.rating} /></div>
            )}
            <p className="mt-3 flex items-center justify-between text-[11px] text-muted-foreground">
              {(() => { const readN = data.totals.read; const unreadN = data.totals.books - data.totals.read; return (
              <span>
                <Trans>{readN} read · {unreadN} unread</Trans>
                {(() => { const dur = fmtDuration(data.totals.total_seconds); return data.totals.total_seconds > 0 ? ` · ${dur}` : '' })()}
              </span>
              ) })()}
              <span><Plural value={data.totals.books} one="# volume" other="# volumes" /></span>
            </p>
            <div className="mt-1 h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${data.totals.books ? (data.totals.read / data.totals.books) * 100 : 0}%` }}
              />
            </div>
            {data.series.description && (
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{data.series.description}</p>
            )}
          </section>
        )}
        {data.books.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground"><Trans>Nothing here yet.</Trans></p>
        ) : data.kind === 'series' && data.series && data.series.arcs.length > 0 ? (
          groupByArc(data.books, data.series.arcs).map((g, gi) => (
            <section key={gi} className="mb-6">
              <div className="mb-2 flex items-baseline gap-2 border-b border-border/60 pb-1.5">
                <h3 className="font-display text-sm text-foreground">
                  {g.arc ? g.arc.name : t`Other volumes`}
                </h3>
                {g.arc && (() => {
                  const a = g.arc.start_index, b = g.arc.end_index
                  return (
                    <span className="text-[11px] text-muted-foreground">
                      <Trans>Vol. {a}–{b}</Trans>
                    </span>
                  )
                })()}
              </div>
              {g.arc?.description && (
                <p className="mb-3 text-xs leading-relaxed text-muted-foreground">{g.arc.description}</p>
              )}
              <ul className="flex flex-col gap-3">
                {g.books.map(b => <SharedBookCard key={b.id} b={b} />)}
              </ul>
            </section>
          ))
        ) : (
          <ul className="flex flex-col gap-3">
            {data.books.map(b => (
              <SharedBookCard key={b.id} b={b} defaultOpen={data.kind === 'book'} />
            ))}
          </ul>
        )}
        <footer className="flex items-center justify-center gap-1.5 pb-4 pt-10 text-xs text-muted-foreground/60">
          <TomeMark className="h-3.5 w-3.5" strokeWidth={7} />
          <Trans>Shared from a self-hosted Tome library</Trans>
        </footer>
      </main>
    </div>
  )
}
