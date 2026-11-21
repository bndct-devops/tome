// The designed Taste page: the all-time ratings (figure and distribution, the
// highest and lowest rated books, series, time against rating, ratings over
// time), "By type" when two or more types have ratings, and Highlights from
// five highlights. All time: a range change neither dims nor refetches it.
// Every section's box follows from fixed heights and row counts, so the
// skeleton draws each box where the loaded page has it.
// Motion: each section's entry plays once per session (it is all time; kit/motion.ts);
// figures count up, bars and tracks grow, covers rise and dots fade in. Hover:
// bars, covers, dots and tracks have tips (kit/tip.ts); rows that lead to a book
// or a series are one link each.
import { useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { Star } from 'lucide-react'
import { cn, formatDuration } from '@/lib/utils'
import { StarRating } from '@/components/StarRating'
import type { HighlightsResponse } from '@/components/stats/shared'
import type { DesignedPageProps } from '@/components/stats/kit/page'
import { Cover, Heading, Ph, Track } from '@/components/stats/kit/Kit'
import { CountUp } from '@/components/stats/kit/CountUp'
import { K, ONCE, stagger, useEntry } from '@/components/stats/kit/motion'
import { tipAttrs } from '@/components/stats/kit/tip'
import { DESKTOP_MIN, HAIR, LEVELS, dec1, fmtDay, num, useWidth } from '@/components/stats/kit/tokens'
import { useStatsGet } from '@/components/stats/kit/useStatsGet'
import { OVER_TIME_H, OverTime, TimeVsRating } from './charts'
import { lowestFour, shelfOrder, wholeStarMedians, type Ratings } from './model'

const SHELF_H = 120
const SHELF_W = 80 // 2:3 at 120 high
const SHELF_GAP = 12
const SHELF_PHONE_MAX = 20
const ROW_H = 62
const SERIES_ROWS = 6
const HL_ROWS = 5
const HL_MIN = 5
const SCATTER_H_DESKTOP = 336 // with the sentence, ends on the series list's last pixel
const SCATTER_H_PHONE = 260

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text: drawn as blocks
   while loading and never read; it only gives each line its loaded width. */
const PH = {
  avg: '4.5', foot: '35 books rated', title: 'He Who Fights with Monsters', author: 'Shirtaloon', series: 'Mushoku Tensei: Jobless',
  books: '16 books', medians: '5-star books took a median of 4h 58m, 4-star books 5h 46m.',
}
/* eslint-enable lingui/no-unlocalized-strings */

const ratingWord = (r: number) => plural(r, { one: '# star', other: '# stars' })
/** A one-decimal figure counted up in tenths ("4.3"). */
const tenths = (n: number) => dec1(n / 10)
const ratedOn = (iso: string | null) => { if (!iso) return undefined; const d = fmtDay(iso.slice(0, 10)); return t`Rated ${d}` }
/** A row that is one link: the title takes the accent and the cover lifts on hover, as the Overview's rows. */
const LINK_ROW = 'group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
const LIFT = 'transition-transform duration-200 group-hover:-translate-y-0.5'
const ACCENT = 'transition-colors group-hover:text-primary'

/** Read-only stars at a pixel size (half stars as the app draws them). */
function Stars({ value, px }: { value: number; px: number }) {
  const label = (() => { const s = ratingWord(value); return t`Rated ${s} of 5` })()
  return (
    <span role="img" aria-label={label} className="inline-flex shrink-0">
      <StarRating value={value} readOnly starClassName={px >= 20 ? 'h-5 w-5' : px >= 18 ? 'h-[18px] w-[18px]' : px >= 13 ? 'h-[13px] w-[13px]' : 'h-3 w-3'} />
    </span>
  )
}

export function TastePage({ stats }: DesignedPageProps) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const desktop = width >= DESKTOP_MIN
  const r = stats?.ratings ?? null
  const hl = useStatsGet<HighlightsResponse>('/stats/highlights')
  const half = desktop ? Math.floor((width - 16) / 2) : width
  // Sections that appear only when they have something to say; they sit last, so nothing above moves.
  const byType = r && r.by_category.length >= 2 ? r : null
  const highlights = r && hl.data && hl.data.count >= HL_MIN ? hl.data : null

  return (
    <div ref={ref} style={LEVELS} className={cn('flex flex-col', desktop ? 'gap-[52px]' : 'gap-10')} aria-busy={!r}>
      {width > 0 && (
        <>
          <RatingsFigure r={r} desktop={desktop} />
          <Favourites r={r} width={width} desktop={desktop} />
          <div className={desktop ? 'grid grid-cols-2 gap-4' : 'flex flex-col gap-10'}>
            <SeriesList r={r} />
            <TimeMatch r={r} width={half} desktop={desktop} />
          </div>
          <RatingsOverTime r={r} width={width} desktop={desktop} />
          {byType && <ByType r={byType} desktop={desktop} />}
          {highlights && <Highlights h={highlights} desktop={desktop} />}
        </>
      )}
    </div>
  )
}

// ── Ratings: the average and the distribution ─────────────────────────────────

function RatingsFigure({ r, desktop }: { r: Ratings | null; desktop: boolean }) {
  const loading = !r
  const none = !!r && r.count === 0
  const dist = r ? r.distribution.slice().sort((a, b) => b.rating - a.rating) : [5, 4, 3, 2, 1].map((rating) => ({ rating, count: 0 }))
  const max = Math.max(1, ...dist.map((d) => d.count))
  const fake = [0.9, 0.6, 0.2, 0.08, 0.04]
  const avg = r && r.count > 0 ? r.avg : 0
  const foot = r ? (none ? t`No books rated yet. Rate a book on its page.` : plural(r.count, { one: '# book rated', other: '# books rated' })) : PH.foot
  const e = useEntry('taste/ratings', r ? ONCE : null)
  const total = r ? r.count : 0
  return (
    <section ref={e.ref} data-box="ratings">
      <Heading note={<Trans>all time</Trans>}><Trans>Ratings</Trans></Heading>
      <div className={desktop ? 'grid grid-cols-12 items-start gap-4' : ''}>
        <div data-box="ratings-avg" className={desktop ? 'col-span-4 min-w-0' : ''}>
          <div className="text-[14px] text-muted-foreground"><Trans>Average rating</Trans></div>
          <div className="mt-2.5 flex items-center gap-4">
            <span className={cn('font-display font-bold tabular-nums text-foreground', desktop ? 'text-[60px]' : 'text-[48px]', 'leading-none')}>
              <Ph on={loading}>{loading ? PH.avg : none ? '–' : <CountUp entry={e} value={Math.round(avg * 10)} format={tenths} />}</Ph>
            </span>
            <span className={loading ? 'opacity-40' : ''}><Stars value={Math.round(avg * 2) / 2} px={desktop ? 20 : 18} /></span>
          </div>
          <div className="mt-3 text-[14px] leading-[1.4] text-muted-foreground"><Ph on={loading}>{foot}</Ph></div>
        </div>
        <div data-box="ratings-dist" className={cn('k-bars flex flex-col gap-1', desktop ? cn('col-span-8 min-w-0 border-l pl-6', HAIR) : cn('mt-5 border-t pt-5', HAIR))}>
          {dist.map((d, i) => {
            const n = d.count
            const books = plural(n, { one: '# book', other: '# books' })
            const stars = ratingWord(d.rating)
            return (
              <div
                key={d.rating}
                className={cn('k-col -mx-1.5 grid h-[22px] grid-cols-[52px_minmax(0,1fr)] items-center rounded-[4px] px-1.5', r && !none && 'data-hot:bg-secondary/60')}
                {...(r && !none ? tipAttrs({ title: stars, value: books, note: (() => { const p = total > 0 ? Math.round((n / total) * 100) : 0; return t`${p}% of your ratings` })() }) : {})}
              >
                <span className="inline-flex items-center gap-1 text-[13px] tabular-nums text-muted-foreground">
                  {d.rating}<Star className="h-[11px] w-[11px] fill-current stroke-none opacity-70" aria-hidden="true" />
                </span>
                <span className="flex min-w-0 items-center gap-2.5">
                  {loading ? (
                    <i className="h-[14px] rounded-[4px] bg-muted" style={{ width: `calc((100% - 40px) * ${fake[i]})` }} />
                  ) : n === 0 ? (
                    <>
                      <i className="h-[14px] w-[2px] rounded-[1px] bg-border" />
                      <span className="text-[13px] tabular-nums text-muted-foreground">0</span>
                    </>
                  ) : (
                    <>
                      <i className={cn('k-bar h-[14px] shrink-0 rounded-[4px]', K.growX)} style={{ width: `calc((100% - 40px) * ${(n / max).toFixed(4)})`, background: 'var(--chart-accent)', ...stagger(i, dist.length) }} />
                      <span className="text-[13px] font-semibold tabular-nums text-foreground"><CountUp entry={e} value={n} format={num} /></span>
                    </>
                  )}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}

// ── Highest rated (shelf) and lowest rated (four rows) ─────────────────────────

function Favourites({ r, width, desktop }: { r: Ratings | null; width: number; desktop: boolean }) {
  const shelf = useMemo(() => (r ? shelfOrder(r.books) : []), [r])
  const low = useMemo(() => (r ? lowestFour(r.books) : []), [r])
  const fit = Math.max(1, Math.floor((width + SHELF_GAP) / (SHELF_W + SHELF_GAP)))
  const slots = r ? (desktop ? Math.min(fit, shelf.length) : Math.min(SHELF_PHONE_MAX, shelf.length)) : desktop ? fit : 5
  const full = !desktop || slots >= fit
  const cells: ReactNode[] = []
  for (let i = 0; i < slots; i++) {
    const b = shelf[i]
    const stars = b ? ratingWord(b.rating) : ''
    const title = b?.title ?? ''
    const label = b ? t`${title}, ${stars}` : undefined
    cells.push(
      <div key={b?.book_id ?? i} className={cn('flex shrink-0 flex-col gap-2', b && K.rise)} style={b ? stagger(i, slots) : undefined}>
        {b ? (
          <Link
            to={`/books/${b.book_id}`}
            aria-label={label}
            {...tipAttrs({ title, value: stars, note: ratedOn(b.rated_at) })}
            className="group block rounded-[8px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Cover id={b.has_cover ? b.book_id : null} h={SHELF_H} className={LIFT} />
          </Link>
        ) : (
          <Cover id={null} h={SHELF_H} />
        )}
        <span className={cn('flex h-3 items-center', !b && 'opacity-40')}><Stars value={b?.rating ?? 0} px={12} /></span>
      </div>,
    )
  }
  const lowRows = r ? low : Array.from({ length: 4 }, () => null)
  const e = useEntry('taste/favourites', r ? ONCE : null)
  return (
    <section ref={e.ref} data-box="favourites">
      <Heading><Trans>Highest rated</Trans></Heading>
      <div data-box="shelf" style={{ height: SHELF_H + 20 }}>
        {r && shelf.length === 0 ? (
          <p className="flex h-full items-center text-[15px] text-muted-foreground"><Trans>No books rated yet. Your best rated books line up here.</Trans></p>
        ) : desktop ? (
          <div className={cn('flex', full ? 'justify-between' : 'gap-3')}>{cells}</div>
        ) : (
          <div className="-mx-5 overflow-x-auto px-5 [scrollbar-width:none]">
            <div className="flex w-max gap-3 pr-5">{cells}</div>
          </div>
        )}
      </div>
      <div className="mb-1 mt-8 text-[14px] text-muted-foreground"><Trans>Lowest rated</Trans></div>
      <div data-box="lowest" className={desktop ? 'grid grid-cols-2 content-start gap-x-4' : ''} style={{ height: (desktop ? 2 : 4) * ROW_H }}>
        {r && low.length === 0 ? (
          <p className={cn('flex h-[62px] items-center text-[15px] text-muted-foreground', desktop && 'col-span-2')}>
            {shelf.length === 0 ? <Trans>Nothing rated yet.</Trans> : <Trans>No book is rated below the rest.</Trans>}
          </p>
        ) : (
          lowRows.map((b, i) => {
            const row = cn('flex h-[62px] min-w-0 items-center gap-3.5', (desktop ? i >= 2 : i >= 1) && cn('border-t', HAIR))
            const body = (
              <>
                <Cover id={b ? (b.has_cover ? b.book_id : null) : null} h={44} className={cn('rounded-[4px]', b && LIFT)} />
                <div className="min-w-0 flex-1">
                  <div className={cn('truncate text-[15px] font-medium text-foreground', ACCENT)}>{b ? b.title : <Ph on>{PH.title}</Ph>}</div>
                  <div className="mt-[3px] flex items-center gap-2.5 whitespace-nowrap text-[13px] text-muted-foreground">
                    {(b?.author || !b) && <span className="truncate"><Ph on={!b}>{b?.author ?? PH.author}</Ph></span>}
                    {!desktop && b && <Stars value={b.rating} px={12} />}
                  </div>
                </div>
                {desktop && <span className={cn('shrink-0', !b && 'opacity-40')}><Stars value={b?.rating ?? 0} px={13} /></span>}
              </>
            )
            return b ? (
              <Link key={b.book_id} to={`/books/${b.book_id}`} data-box={`lowest-${i}`} className={cn(row, LINK_ROW)}>{body}</Link>
            ) : (
              <div key={i} data-box={`lowest-${i}`} className={row}>{body}</div>
            )
          })
        )}
      </div>
    </section>
  )
}

// ── Series ────────────────────────────────────────────────────────────────────

function SeriesList({ r }: { r: Ratings | null }) {
  const rows = r ? r.series.slice(0, SERIES_ROWS) : Array.from({ length: SERIES_ROWS }, () => null)
  const more = r && r.series.length > SERIES_ROWS ? r.series.length : 0
  const shownN = SERIES_ROWS
  return (
    <section data-box="series" className="min-w-0">
      <Heading note={more ? t`top ${shownN} of ${more}` : undefined}><Trans>Series</Trans></Heading>
      <div data-box="series-rows" style={{ height: SERIES_ROWS * ROW_H }}>
        {r && rows.length === 0 ? (
          <p className="flex h-[62px] items-center text-[15px] text-muted-foreground"><Trans>No series rated yet. Rate a series on its page.</Trans></p>
        ) : (
          rows.map((s, i) => {
            const n = s?.book_count ?? 0
            const row = cn('flex h-[62px] min-w-0 items-center gap-3.5', i > 0 && cn('border-t', HAIR))
            const body = (
              <>
                <Cover id={s?.sample_book_id ?? null} h={54} className={cn('rounded-[4px]', s && LIFT)} />
                <div className="min-w-0 flex-1">
                  <div className={cn('truncate text-[15px] font-semibold text-foreground', ACCENT)}><Ph on={!s}>{s?.series ?? PH.series}</Ph></div>
                  <div className="mt-[3px] flex items-center gap-2.5 whitespace-nowrap text-[13px] text-muted-foreground">
                    <span className={!s ? 'opacity-40' : ''}><Stars value={s?.rating ?? 0} px={12} /></span>
                    {s ? n > 0 && <span className="tabular-nums">{plural(n, { one: '# book', other: '# books' })}</span> : <Ph on>{PH.books}</Ph>}
                  </div>
                </div>
              </>
            )
            // A series opens in the Series view, as Home and the book page link it.
            return s ? (
              <Link key={s.series} to={`/?tab=series&series_detail=${encodeURIComponent(s.series)}`} data-box={`series-${i}`} className={cn(row, LINK_ROW)}>{body}</Link>
            ) : (
              <div key={i} data-box={`series-${i}`} className={row}>{body}</div>
            )
          })
        )}
      </div>
    </section>
  )
}

// ── Does time spent match the rating? ─────────────────────────────────────────

function TimeMatch({ r, width, desktop }: { r: Ratings | null; width: number; desktop: boolean }) {
  const H = desktop ? SCATTER_H_DESKTOP : SCATTER_H_PHONE
  const meds = useMemo(() => (r ? wholeStarMedians(r.books) : []), [r])
  let sentence: ReactNode
  if (!r) sentence = <Ph on>{PH.medians}</Ph>
  else if (r.books.length === 0) sentence = t`Rate books you have read to see this.`
  else if (meds.length === 0) sentence = t`Not enough whole-star ratings with reading time to compare yet.`
  else {
    const [a, b] = meds
    const sa = a.stars, da = formatDuration(a.seconds)
    if (!b) sentence = t`${sa}-star books took a median of ${da}.`
    else {
      const sb = b.stars, db = formatDuration(b.seconds)
      sentence = t`${sa}-star books took a median of ${da}, ${sb}-star books ${db}.`
    }
  }
  const e = useEntry('taste/scatter', r ? ONCE : null)
  return (
    <section ref={e.ref} data-box="scatter" className="min-w-0">
      <Heading><Trans>Does time spent match the rating?</Trans></Heading>
      <p data-box="scatter-text" className={cn('mb-3.5 text-foreground', desktop ? 'h-[22px] truncate text-[15px]' : 'line-clamp-2 min-h-[41px] text-[14px]', 'leading-[1.45]')}>{sentence}</p>
      <div data-box="scatter-chart" style={{ height: H }}>
        {r && r.books.length === 0 ? (
          <div className="flex h-full items-center justify-center rounded-[12px] bg-secondary/50 px-6 text-center text-[14px] text-muted-foreground"><Trans>Each rated book appears here by the hours you read it.</Trans></div>
        ) : (
          <TimeVsRating W={width} H={H} books={r?.books ?? []} loading={!r} />
        )}
      </div>
    </section>
  )
}

// ── Ratings over time ─────────────────────────────────────────────────────────

function Legend() {
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px] font-normal text-muted-foreground">
      <span className="inline-flex items-center gap-2"><b className="inline-block h-2 w-2 rounded-full" style={{ background: 'var(--chart-accent)' }} /><Trans>a rating</Trans></span>
      <span className="inline-flex items-center gap-2"><b className="inline-block h-[9px] w-[9px] rounded-full border-[1.75px]" style={{ borderColor: 'var(--chart-accent)' }} /><Trans>half star</Trans></span>
      <span className="inline-flex items-center gap-2"><i className="inline-block w-[18px] border-t-[1.5px] border-foreground/70" /><Trans>mean of the last five</Trans></span>
    </span>
  )
}

function RatingsOverTime({ r, width, desktop }: { r: Ratings | null; width: number; desktop: boolean }) {
  const dated = useMemo(() => (r ? r.books.filter((b) => b.rated_at) : []), [r])
  const e = useEntry('taste/overtime', r ? ONCE : null)
  return (
    // eslint-disable-next-line react-hooks/refs -- Entry.ref is a callback ref (a method), not a ref object
    <section ref={e.ref} data-box="overtime">
      {desktop ? (
        <Heading right note={<Legend />}><Trans>Ratings over time</Trans></Heading>
      ) : (
        <>
          <Heading className="mb-2.5"><Trans>Ratings over time</Trans></Heading>
          <div className="mb-3.5 h-[18px]"><Legend /></div>
        </>
      )}
      <div data-box="overtime-chart" style={{ height: OVER_TIME_H }}>
        {r && dated.length === 0 ? (
          <div className="flex h-full items-center justify-center rounded-[12px] bg-secondary/50 px-6 text-center text-[14px] text-muted-foreground"><Trans>Your ratings appear here in the order you gave them.</Trans></div>
        ) : (
          <OverTime W={width} books={dated} loading={!r} />
        )}
      </div>
    </section>
  )
}

// ── By type (two or more types with ratings) ──────────────────────────────────

function ByType({ r, desktop }: { r: Ratings; desktop: boolean }) {
  const e = useEntry('taste/bytype', ONCE)
  /* eslint-disable react-hooks/refs -- Entry.ref is a callback ref (a method) and CountUp reads the entry in effects */
  return (
    <section ref={e.ref} data-box="bytype">
      <Heading note={<Trans>average rating per type</Trans>}><Trans>By type</Trans></Heading>
      <div className={desktop ? 'grid grid-cols-2 gap-x-4' : ''}>
        {r.by_category.map((c, i) => (
          <div key={c.category} data-box={`bytype-${i}`} className={cn('flex h-[52px] min-w-0 items-center gap-4', (desktop ? i >= 2 : i >= 1) && cn('border-t', HAIR))}>
            <span className="min-w-0 flex-1 truncate text-[15px] font-semibold text-foreground">{c.category}</span>
            <span className={K.fade} style={stagger(i, r.by_category.length)}><Stars value={Math.round(c.avg * 2) / 2} px={13} /></span>
            <span className="w-8 text-right text-[15px] font-semibold tabular-nums text-foreground"><CountUp entry={e} value={Math.round(c.avg * 10)} format={tenths} align="end" /></span>
            <span className="w-[72px] text-right text-[13px] tabular-nums text-muted-foreground">{plural(c.count, { one: '# rated', other: '# rated' })}</span>
          </div>
        ))}
      </div>
    </section>
  )
  /* eslint-enable react-hooks/refs */
}

// ── Highlights (from five) ────────────────────────────────────────────────────

function Highlights({ h, desktop }: { h: HighlightsResponse; desktop: boolean }) {
  const max = Math.max(1, ...h.top.map((x) => x.count))
  const e = useEntry('taste/highlights', ONCE)
  return (
    <section ref={e.ref} data-box="highlights">
      <Heading note={<Trans>all time</Trans>}><Trans>Highlights</Trans></Heading>
      <div className={desktop ? 'grid grid-cols-12 items-start gap-4' : ''}>
        <div data-box="highlights-fig" className={desktop ? 'col-span-4 min-w-0' : ''}>
          <div className="text-[14px] text-muted-foreground"><Trans>Highlights</Trans></div>
          <div className="mt-1.5 font-display text-[40px] font-bold leading-none tabular-nums text-foreground">
            <CountUp entry={e} value={h.count} format={num} />
            <span className="ml-1.5 font-sans text-[16px] font-semibold tracking-normal">{plural(h.count, { one: 'highlight', other: 'highlights' })}</span>
          </div>
          <div className="mt-2 text-[14px] text-muted-foreground">{plural(h.books, { one: 'in # book', other: 'in # books' })}</div>
          <Link to="/highlights" className="mt-3 inline-block text-[15px] font-medium text-primary hover:underline underline-offset-4"><Trans>All highlights</Trans></Link>
        </div>
        <div data-box="highlights-list" className={cn('min-w-0', desktop ? cn('col-span-8 border-l pl-6', HAIR) : cn('mt-5 border-t pt-5', HAIR))}>
          <div className="mb-1 text-[14px] text-muted-foreground"><Trans>Most highlighted</Trans></div>
          <div className="k-bars" style={{ minHeight: HL_ROWS * ROW_H }}>
            {h.top.map((b, i) => {
              const n = plural(b.count, { one: '# highlight', other: '# highlights' })
              const share = h.count > 0 ? Math.round((b.count / h.count) * 100) : 0
              return (
                <Link key={b.book_id} to={`/books/${b.book_id}`} data-box={`highlights-${i}`} className={cn('flex h-[62px] min-w-0 items-center gap-3.5', i > 0 && cn('border-t', HAIR), LINK_ROW)}>
                  <Cover id={b.book_id} h={54} className={cn('rounded-[4px]', LIFT)} />
                  <div className="min-w-0 flex-1">
                    <div className={cn('truncate text-[15px] font-semibold text-foreground', ACCENT)}>{b.title}</div>
                    <div className="mt-[9px] flex items-center gap-3">
                      <Track pct={(b.count / max) * 100} style={stagger(i, h.top.length)} tip={tipAttrs({ title: b.title, value: n, note: t`${share}% of your highlights` })} />
                      <span className="min-w-4 text-right text-[13px] font-semibold tabular-nums text-foreground"><CountUp entry={e} value={b.count} format={num} align="end" /></span>
                    </div>
                  </div>
                </Link>
              )
            })}
          </div>
        </div>
      </div>
    </section>
  )
}
