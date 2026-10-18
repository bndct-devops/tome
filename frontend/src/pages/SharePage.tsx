// Public, read-only view of a shared shelf — /share/:token, no login.
// Deliberately minimal surface: covers, identity, tags, description, the
// owner's rating and highlights. A shelf or series is a cover grid; a cover
// opens the book in a sheet. No downloads, no reader, no links into the
// app beyond the landing page. Renders noindex so links stay unlisted.
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { BookOpen, Quote, Star, StickyNote, X } from 'lucide-react'
import { TomeMark } from '@/components/TomeMark'
import { CoverImage } from '@/components/CoverImage'
import { ModalShell } from '@/components/ModalShell'
import { PageBody, PageHeader, Pill, SectionTitle } from '@/components/page'
import { Wordmark } from '@/components/auth/AuthPanel'
import { cn } from '@/lib/utils'
import { Trans, Plural } from '@lingui/react/macro'
import { t, plural, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'
import { applyTheme, getStoredTheme } from '@/lib/theme'
import { coverSrc } from '@/lib/covers'

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
    <div>
      <div className="relative h-4 overflow-hidden rounded" style={{ background: 'color-mix(in oklch, var(--primary) 16%, var(--background))' }}>
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
      <p className="mt-1.5 flex justify-between text-[12px] text-muted-foreground">
        <span>{fmtDay(stats.first_day)}</span>
        <span>{fmtDay(stats.last_day)}</span>
      </p>
    </div>
  )
}

function Rating({ value, small = false }: { value: number; small?: boolean }) {
  return (
    <span className="flex items-center gap-0.5" title={`${value} stars`}>
      {[1, 2, 3, 4, 5].map(i => (
        <Star
          key={i}
          className={cn(
            small ? 'h-3 w-3' : 'h-3.5 w-3.5',
            i <= Math.round(value) ? 'fill-rating text-rating' : 'text-muted-foreground/40',
          )}
        />
      ))}
    </span>
  )
}

/** "Read · 4h 10m over 6 days · finished 3 Oct 2026" */
function StatsLine({ stats }: { stats: SharedStats }) {
  return (
    <>
      {stats.status === 'read' ? t({ message: 'Read', context: 'status' }) : stats.status === 'reading' ? t`Reading` : null}
      {stats.total_seconds > 0 && (
        <>
          {stats.status ? ' · ' : ''}
          {(() => { const dur = fmtDuration(stats.total_seconds); return plural(stats.reading_days, { one: `${dur} over # day`, other: `${dur} over # days` }) })()}
        </>
      )}
      {(() => { const d = stats.finished_on ? fmtDay(stats.finished_on) : ''; return stats.finished_on ? t` · finished ${d}` : '' })()}
    </>
  )
}

function seriesLabel(b: SharedBook): string {
  return `${b.series}${b.series_index != null ? ` #${b.series_index}` : ''}`
}

/** One book in full: cover, identity, rating, the owner's reading, tags,
 *  activity, description and highlights. Inline for a shared book, in a
 *  sheet for a shelf or series. */
function BookDetail({ b, hero = false, onClose }: { b: SharedBook; hero?: boolean; onClose?: () => void }) {
  return (
    <div className="flex flex-col gap-7">
      <div className={cn('flex gap-6', hero ? 'flex-col sm:flex-row sm:items-end' : 'items-start')}>
        <div
          className={cn('shrink-0 overflow-hidden bg-muted', hero ? 'w-[180px] rounded-[12px] mx-auto sm:mx-0' : 'w-[104px] rounded-[8px]')}
          style={{ boxShadow: hero ? 'var(--cover-bloom-hero)' : 'var(--cover-bloom-fan)' }}
        >
          <div className="relative aspect-[2/3]"><CoverImage src={coverSrc(b.id, { w: 400 })} alt="" iconClassName="w-8 h-8" /></div>
        </div>
        <div className="min-w-0 flex-1">
          {b.series && <div className="text-[12px] font-bold uppercase tracking-[0.1em] text-primary">{seriesLabel(b)}</div>}
          <h2 className={cn('font-display font-bold leading-[1.1] text-foreground mt-1.5', hero ? 'text-[32px]' : 'text-[22px]')}>{b.title}</h2>
          {b.author && <p className="mt-1.5 text-[16px] text-muted-foreground">{b.author}</p>}
          {b.rating != null && <div className="mt-2.5"><Rating value={b.rating} /></div>}
          {b.stats && <p className="mt-2 text-[14px] text-muted-foreground"><StatsLine stats={b.stats} /></p>}
        </div>
        {onClose && (
          <Pill variant="quiet" icon onClick={onClose} aria-label={t`Close`} className="-mr-2 -mt-2">
            <X className="h-4 w-4" />
          </Pill>
        )}
      </div>

      {b.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {b.tags.map(tag => (
            <span key={tag} className="rounded-full bg-secondary px-2.5 py-1 text-[13px] text-muted-foreground">{tag}</span>
          ))}
        </div>
      )}

      {b.stats && b.stats.activity.length > 1 && <ActivityStrip stats={b.stats} />}

      {b.description && (
        <section>
          <SectionTitle size={20}><Trans>Description</Trans></SectionTitle>
          <p className="mt-3 text-[15px] leading-relaxed text-muted-foreground max-w-[70ch]">{b.description}</p>
        </section>
      )}

      {b.highlights.length > 0 && (
        <section>
          <SectionTitle size={20}><Plural value={b.highlights.length} one="# highlight" other="# highlights" /></SectionTitle>
          <ul className="mt-2 flex flex-col divide-y divide-border/70">
            {b.highlights.map((h, i) => (
              <li key={i} className="py-3.5 text-[15px]">
                {h.chapter && <p className="mb-1 text-[13px] text-muted-foreground">{h.chapter}</p>}
                {h.text && (
                  <p className="border-l-2 border-primary/40 pl-3 leading-relaxed text-foreground">{h.text}</p>
                )}
                {h.note && (
                  <p className="mt-1.5 flex items-start gap-1.5 text-[14px] text-muted-foreground">
                    <StickyNote className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span className="leading-relaxed">{h.note}</span>
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

/** A cover tile in the Library's grid style. The whole tile opens the book. */
function BookTile({ b, onOpen }: { b: SharedBook; onOpen: () => void }) {
  return (
    <li>
      <button type="button" onClick={onOpen} className="group block w-full text-left">
        <div
          className="relative aspect-[2/3] overflow-hidden rounded-[8px] bg-muted transition-transform group-hover:-translate-y-0.5"
          style={{ boxShadow: 'var(--cover-bloom-fan)' }}
        >
          <CoverImage src={coverSrc(b.id, { w: 400 })} alt="" iconClassName="w-6 h-6" />
        </div>
        <div className="mt-2.5 text-[14px] font-medium leading-snug text-foreground line-clamp-2 group-hover:text-primary">{b.title}</div>
        {b.series && <div className="mt-0.5 text-[13px] text-primary truncate">{seriesLabel(b)}</div>}
        {b.author && <div className="mt-0.5 text-[13px] text-muted-foreground truncate">{b.author}</div>}
        {(b.rating != null || b.stats?.status || b.highlights.length > 0) && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] text-muted-foreground">
            {b.rating != null && <Rating value={b.rating} small />}
            {b.stats?.status === 'read' && <span>{t({ message: 'Read', context: 'status' })}</span>}
            {b.stats?.status === 'reading' && <span>{t`Reading`}</span>}
            {b.highlights.length > 0 && (
              <span className="flex items-center gap-1"><Quote className="h-3 w-3" />{b.highlights.length}</span>
            )}
          </div>
        )}
      </button>
    </li>
  )
}

function BookGrid({ books, onOpen }: { books: SharedBook[]; onOpen: (b: SharedBook) => void }) {
  return (
    <ul className="grid grid-cols-2 gap-x-5 gap-y-8 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
      {books.map(b => <BookTile key={b.id} b={b} onOpen={() => onOpen(b)} />)}
    </ul>
  )
}

/** Minimal top bar: the wordmark, nothing else. */
function TopBar() {
  return (
    <header className="safe-top">
      <div className="mx-auto flex h-16 max-w-[1180px] items-center px-5 sm:px-6 lg:px-10">
        <Wordmark />
      </div>
    </header>
  )
}

export function SharePage() {
  const { token } = useParams()
  const [data, setData] = useState<ShareResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [openBook, setOpenBook] = useState<SharedBook | null>(null)

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

  useEffect(() => {
    if (!openBook) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpenBook(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [openBook])

  if (error) {
    return (
      <div className="min-h-screen bg-background">
        <TopBar />
        <div className="flex flex-col items-center justify-center gap-3 px-6 py-24 text-center">
          <BookOpen className="h-10 w-10 text-muted-foreground" />
          <p className="text-[15px] text-muted-foreground">{error}</p>
        </div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="min-h-screen bg-background">
        <TopBar />
        <p className="animate-pulse py-24 text-center text-[15px] text-muted-foreground"><Trans>Loading…</Trans></p>
      </div>
    )
  }

  const kindLabel = data.kind === 'book' ? t`Shared book` : data.kind === 'series' ? t`Shared series` : t`Shared shelf`
  const totalsParts: string[] = []
  if (data.kind !== 'book') totalsParts.push(plural(data.totals.books, { one: '# book', other: '# books' }))
  if (data.totals.read > 0 && data.kind !== 'book') { const n = data.totals.read; totalsParts.push(t`${n} read`) }
  if (data.totals.total_seconds > 0) { const dur = fmtDuration(data.totals.total_seconds); totalsParts.push(t`${dur} read time`) }
  const series = data.kind === 'series' ? data.series : undefined
  const readPct = data.totals.books ? (data.totals.read / data.totals.books) * 100 : 0

  return (
    <div className="min-h-screen bg-background">
      <TopBar />
      <PageBody width="wide" className="pt-4">
        {data.kind === 'book' && data.books[0] ? (
          <>
            <div className="text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">{kindLabel}</div>
            <div className="max-w-[820px] -mt-3">
              <BookDetail b={data.books[0]} hero />
            </div>
          </>
        ) : (
          <>
            <PageHeader
              eyebrow={kindLabel}
              title={data.title}
              subtitle={series ? series.author : totalsParts.join(' · ')}
            />

            {series && (
              <section className="flex flex-col gap-4 max-w-[820px]">
                {(series.status || series.rating != null) && (
                  <div className="flex flex-wrap items-center gap-3">
                    {series.status && (
                      <span className="rounded-full bg-secondary px-2.5 py-1 text-[13px] capitalize text-muted-foreground">
                        {SERIES_STATUS_WORDS[series.status] ? i18n._(SERIES_STATUS_WORDS[series.status]) : series.status}
                      </span>
                    )}
                    {series.rating != null && <Rating value={series.rating} />}
                  </div>
                )}
                <div>
                  <p className="flex items-center justify-between gap-3 text-[14px] text-muted-foreground">
                    {(() => { const readN = data.totals.read; const unreadN = data.totals.books - data.totals.read; return (
                    <span>
                      <Trans>{readN} read · {unreadN} unread</Trans>
                      {(() => { const dur = fmtDuration(data.totals.total_seconds); return data.totals.total_seconds > 0 ? ` · ${dur}` : '' })()}
                    </span>
                    ) })()}
                    <span><Plural value={data.totals.books} one="# volume" other="# volumes" /></span>
                  </p>
                  <div className="mt-2 flex items-center gap-3">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full" style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--background))' }}>
                      <div className="h-full rounded-full bg-primary" style={{ width: `${readPct}%` }} />
                    </div>
                    <span className="text-[14px] font-bold tabular-nums text-foreground">{Math.round(readPct)}%</span>
                  </div>
                </div>
                {series.description && (
                  <p className="text-[15px] leading-relaxed text-muted-foreground max-w-[70ch]">{series.description}</p>
                )}
              </section>
            )}

            {data.books.length === 0 ? (
              <p className="py-12 text-center text-[15px] text-muted-foreground"><Trans>Nothing here yet.</Trans></p>
            ) : series && series.arcs.length > 0 ? (
              groupByArc(data.books, series.arcs).map((g, gi) => (
                <section key={gi} className="flex flex-col gap-4">
                  <div>
                    <SectionTitle
                      size={20}
                      action={g.arc ? (() => {
                        const a = g.arc.start_index, b = g.arc.end_index
                        return <span className="text-[14px] font-normal text-muted-foreground"><Trans>Vol. {a}–{b}</Trans></span>
                      })() : undefined}
                    >
                      {g.arc ? g.arc.name : t`Other volumes`}
                    </SectionTitle>
                    {g.arc?.description && (
                      <p className="mt-2 text-[14px] leading-relaxed text-muted-foreground max-w-[70ch]">{g.arc.description}</p>
                    )}
                  </div>
                  <BookGrid books={g.books} onOpen={setOpenBook} />
                </section>
              ))
            ) : (
              <BookGrid books={data.books} onOpen={setOpenBook} />
            )}
          </>
        )}

        <footer className="flex items-center justify-center gap-1.5 pt-6 text-[13px] text-muted-foreground">
          <TomeMark className="h-3.5 w-3.5" strokeWidth={7} />
          <Trans>Shared from a self-hosted Tome library</Trans>
        </footer>
      </PageBody>

      <ModalShell open={!!openBook} onClose={() => setOpenBook(null)} className="w-full max-w-[600px]">
        {openBook && (
          <div className="max-h-[85vh] overflow-y-auto rounded-[var(--radius-panel)] bg-background p-6 sm:p-8 shadow-2xl">
            <BookDetail b={openBook} onClose={() => setOpenBook(null)} />
          </div>
        )}
      </ModalShell>
    </div>
  )
}
