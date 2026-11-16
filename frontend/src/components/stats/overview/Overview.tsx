// The designed Overview: one fixed layout on the Home page's habits (section
// titles, Stat figures, real covers on hairlines). Every section's size follows
// from fixed heights and row counts, so the skeleton below draws each box where
// the loaded page will have it and nothing moves when the data arrives.
// Motion and hover come from the kit: each section has an entry (kit/motion.ts:
// range-bound sections play again for a new range, the rest once a session),
// figures count up (CountUp), and chart marks, tracks and covers have tips (kit/tip.ts).
import { useEffect, useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { cn, formatDuration } from '@/lib/utils'
import { SectionTitle } from '@/components/page'
import type { CompletionEstimate, StatsResponse } from '@/components/stats/shared'
import { fmtDay as fmtShortDay, type CustomRange } from '@/components/stats/range'
import { Cover, Heading, Ph, Track } from '@/components/stats/kit/Kit'
import { CountUp } from '@/components/stats/kit/CountUp'
import { K, ONCE, rangeSig, stagger, useEntry } from '@/components/stats/kit/motion'
import { tipAttrs } from '@/components/stats/kit/tip'
import { DESKTOP_MIN, DIM, HAIR, LEVELS, UNDIM, fmtDay, num, useWidth } from '@/components/stats/kit/tokens'
import { BarsChart, HourHeat, YearGrid } from './charts'
import { dayDiff, emptyYear, heroBars, hourHeatHeight, hourModel, readingDayOf, yearHeight, yearModel } from './model'


const HERO_CHART_H = 180
const COVER_SHELF_H = 120
const COVER_SHELF_W = 80 // 2:3 at 120 high
const SHELF_GAP = 12
const TOP_ROWS = 5

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text: drawn as blocks
   while loading and never read; it only gives each line its loaded width. */
const PH = {
  total: '000h 00m', delta: '8% more than the previous 90 days · 95h 50m',
  sessions: '000', sessionsFoot: 'avg 00m', pages: '00,000', pagesFoot: '000 a day',
  finished: '00', finishedFoot: '00 in the previous 90 days', streak: '000', streakFoot: 'since Dec 10, 2025', streakFoot2: 'your longest',
  title: 'He Who Fights with Monsters', author: 'Shirtaloon', pct: '00%', bookLine: 'about 4 days left · last read yesterday',
  month: 'Nov', shelfNote: '13 books in the last 90 days', dur: '00h 00m', when: 'Mostly between 01:00 and 02:00, most on Mondays.',
  active: '361 active days in the last 365', session: '0h 0m', day: '0h 00m', pagesDay: '0,000', date: 'Sep 9, 2026', streakSpan: 'Dec 10, 2025 to Nov 15, 2026',
}
/* eslint-enable lingui/no-unlocalized-strings */

export type OverviewProps = {
  /** null while the first answer is on its way: the skeleton is drawn. */
  stats: StatsResponse | null
  estimates: CompletionEstimate[] | null
  /** A newer range was asked for: the range-bound sections dim until it lands. */
  pending: boolean
  days: number
  custom: CustomRange | null
  onAllBooks: () => void
}

export function Overview({ stats, estimates, pending, days, custom, onAllBooks }: OverviewProps) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const desktop = width >= DESKTOP_MIN
  const loading = !stats
  const neverRead = !!stats && stats.headline.total_sessions === 0 && stats.heatmap_daily.every((d) => d.seconds === 0)

  // How the range reads in a sentence ("the last 90 days").
  const from = custom ? fmtShortDay(custom.start) : ''
  const to = custom ? fmtShortDay(custom.end) : ''
  const span = stats?.daily.length ?? days
  const rangePhrase = custom ? t`${from} to ${to}` : days === 0 ? t`all time` : days === 365 ? t`the last 12 months` : t`the last ${days} days`
  const prevPhrase = custom ? t`the previous ${span} days` : days === 365 ? t`the previous 12 months` : t`the previous ${days} days`

  const dim = pending ? DIM : UNDIM
  // What the entries key on: the range the answer is for (sections that follow it), or once.
  const sig = stats ? rangeSig(days, custom) : null
  const once = stats ? ONCE : null

  return (
    <div ref={ref} style={LEVELS} className={cn('flex flex-col', desktop ? 'gap-[52px]' : 'gap-10')} aria-busy={loading || pending}>
      {width > 0 && (
        <>
          <Hero stats={stats} desktop={desktop} width={width} loading={loading} neverRead={neverRead} prevPhrase={prevPhrase} days={days} dim={dim} sig={sig} />
          <ReadingNow stats={stats} estimates={estimates} desktop={desktop} loading={loading} sig={once} />
          <Finished stats={stats} width={width} desktop={desktop} loading={loading} rangePhrase={rangePhrase} dim={dim} sig={sig} />
          <div className={cn(desktop ? 'grid grid-cols-2 gap-4' : 'flex flex-col gap-10')}>
            <TopBooks stats={stats} loading={loading} desktop={desktop} rangePhrase={rangePhrase} onAllBooks={onAllBooks} dim={dim} sig={sig} />
            <WhenYouRead stats={stats} loading={loading} width={desktop ? Math.floor((width - 16) / 2) : width} desktop={desktop} dim={dim} sig={sig} />
          </div>
          <TheYear stats={stats} loading={loading} width={width} desktop={desktop} sig={once} />
          <Records stats={stats} loading={loading} desktop={desktop} sig={once} />
        </>
      )}
    </div>
  )
}

// ── Hero: reading time, four figures, the chart ───────────────────────────────

function Hero({ stats, desktop, width, loading, neverRead, prevPhrase, days, dim, sig }: {
  stats: StatsResponse | null
  desktop: boolean
  width: number
  loading: boolean
  neverRead: boolean
  prevPhrase: string
  days: number
  dim: string
  sig: string | null
}) {
  const e = useEntry('overview/hero', sig)
  const h = stats?.headline
  const pc = stats?.period_comparison ?? null
  const chart = useMemo(() => (stats ? heroBars(stats.daily) : null), [stats])
  const totalLabel = h ? <CountUp entry={e} value={h.total_reading_seconds} format={formatDuration} /> : PH.total

  let delta: ReactNode = ' '
  if (loading) delta = <Ph on>{PH.delta}</Ph>
  else if (pc && !neverRead) {
    const prev = formatDuration(pc.previous_seconds)
    const pct = pc.pct_change == null ? null : Math.round(Math.abs(pc.pct_change))
    const up = (pc.pct_change ?? 0) > 0
    const down = (pc.pct_change ?? 0) < 0
    const tail = desktop ? <> · <span className="whitespace-nowrap font-medium text-foreground">{prev}</span></> : null
    if (pc.pct_change == null) delta = <><Trans>Nothing in {prevPhrase}</Trans>{tail}</>
    else if (up || down) {
      delta = (
        <>
          <span className={up ? 'text-success' : 'text-destructive'}>
            {up && (
              <svg className="mr-1 inline-block h-[15px] w-[15px] align-[-2px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.25} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M16 7h6v6" /><path d="m22 7-8.5 8.5-5-5L2 17" /></svg>
            )}
            <b className="font-semibold">{pct}%</b>
          </span>{' '}
          {up ? <Trans>more than {prevPhrase}</Trans> : <Trans>less than {prevPhrase}</Trans>}
          {tail}
        </>
      )
    } else delta = <><Trans>The same as {prevPhrase}</Trans>{tail}</>
  } else if (days === 0 && stats) {
    const active = stats.lifetime.active_days
    delta = plural(active, { one: '# reading day in all', other: '# reading days in all' })
  }

  const prev = (n: number | undefined, phrase: string) => (n == null ? ' ' : t`${n} in ${phrase}`)
  const longest = h ? h.streaks.longest_days : 0
  const streakStart = h?.streaks.current_start ? fmtDay(h.streaks.current_start) : null
  const longestLine = h && h.streaks.current_days > 0 && h.streaks.current_days >= longest ? t`your longest` : plural(longest, { one: 'longest # day', other: 'longest # days' })

  const figs: { label: ReactNode; value: ReactNode; unit?: ReactNode; foot: ReactNode; tone?: string; ph: string; phFoot: string }[] = [
    { label: <Trans>Sessions</Trans>, value: h ? <CountUp entry={e} value={h.total_sessions} format={num} /> : '', foot: h ? (() => { const avg = formatDuration(h.avg_session_seconds); return t`avg ${avg}` })() : '', ph: PH.sessions, phFoot: PH.sessionsFoot },
    {
      label: <Trans>Pages</Trans>,
      value: h ? <CountUp entry={e} value={h.pages_turned} format={num} /> : '',
      foot: h && stats ? (() => { const perDay = num(Math.round(h.pages_turned / Math.max(1, stats.daily.length))); return t`${perDay} a day` })() : '',
      ph: PH.pages, phFoot: PH.pagesFoot,
    },
    {
      label: <Trans context="reading">Finished</Trans>,
      value: h ? <CountUp entry={e} value={h.books_finished} format={num} /> : '',
      foot: <span className="whitespace-nowrap">{pc ? prev(pc.previous_books_finished, prevPhrase) : ' '}</span>,
      tone: 'text-success', ph: PH.finished, phFoot: PH.finishedFoot,
    },
    {
      label: <Trans>Streak</Trans>,
      value: h ? <CountUp entry={e} value={h.streaks.current_days} format={num} /> : '',
      unit: h ? plural(h.streaks.current_days, { one: 'day', other: 'days' }) : null,
      foot: h ? <>{streakStart ? t`since ${streakStart}` : ' '}<br />{longestLine}</> : '',
      tone: 'text-primary', ph: PH.streak, phFoot: PH.streakFoot,
    },
  ]

  const figures = figs.map((f, i) => (
    <div
      key={i}
      data-box={`hero-fig-${i}`}
      className={cn(
        'min-w-0',
        desktop ? cn('self-stretch border-l pl-3.5', HAIR) : cn('py-4', i % 2 === 0 ? 'pr-3' : cn('border-l pl-4', HAIR), i < 2 ? cn('border-b', HAIR) : 'pb-0'),
      )}
    >
      <div className="text-[14px] text-muted-foreground">{f.label}</div>
      <div className={cn('mt-1.5 font-display font-bold tabular-nums', desktop ? 'text-[40px]' : 'text-[32px]', 'leading-none', loading ? 'text-foreground' : f.tone ?? 'text-foreground')}>
        <Ph on={loading}>{loading ? f.ph : f.value}</Ph>
        {f.unit && <span className="ml-1.5 font-sans text-[16px] font-semibold tracking-normal">{f.unit}</span>}
      </div>
      <div className="mt-1.5 text-[13px] leading-[1.4] text-muted-foreground">
        {loading ? <Ph on>{f.phFoot}</Ph> : f.foot}
        {loading && i === 3 && <><br /><Ph on>{PH.streakFoot2}</Ph></>}
      </div>
    </div>
  ))

  const chartBox = (
    <div data-box="hero-chart" className={cn(desktop ? 'mt-8' : 'order-1 mt-5')} style={{ height: HERO_CHART_H }}>
      {neverRead ? (
        <div className="flex h-full flex-col items-center justify-center gap-2 rounded-[12px] bg-secondary/50 px-6 text-center">
          <p className="text-[15px] font-semibold text-foreground"><Trans>No reading data yet</Trans></p>
          <p className="max-w-md text-[14px] text-muted-foreground">
            <Trans>Reading stats appear once sessions arrive: synced from the TomeSync KOReader plugin, logged by hand on a book page, or imported from a KOReader statistics file.</Trans>
          </p>
          <Link to="/settings/koreader" className="text-[14px] font-medium text-primary hover:underline"><Trans>Download the plugin from Settings</Trans></Link>
        </div>
      ) : (
        <BarsChart W={width} H={HERO_CHART_H} bars={chart?.bars ?? []} avg={chart?.avg ?? 0} monthStarts={chart?.monthStarts ?? []} nDays={chart?.nDays ?? 1} weekly={chart?.weekly ?? false} loading={loading} />
      )}
    </div>
  )

  return (
    <section ref={e.ref} data-box="hero" className={cn(desktop ? cn('border-b pb-10', HAIR) : 'flex flex-col', dim)}>
      <div className={desktop ? 'grid grid-cols-[342px_repeat(4,minmax(0,1fr))] items-start' : 'contents'}>
        <div data-box="hero-total" className={cn('min-w-0', desktop ? 'pr-5' : 'order-0')}>
          <div className="text-[14px] text-muted-foreground"><Trans>Reading time</Trans></div>
          <div className={cn('mt-2.5 font-display font-bold tabular-nums text-foreground', desktop ? 'text-[60px]' : 'text-[48px]', 'leading-none')}>
            <Ph on={loading}>{totalLabel}</Ph>
          </div>
          <div className={cn('mt-3 text-[14px] leading-[1.4] text-muted-foreground', desktop ? 'whitespace-nowrap' : 'min-h-[39px]')}>{delta}</div>
        </div>
        {desktop ? figures : null}
      </div>
      {chartBox}
      {!desktop && <div className={cn('order-2 mt-5 grid grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] border-t', HAIR)}>{figures}</div>}
    </section>
  )
}

// ── Reading now ───────────────────────────────────────────────────────────────

const NOW_COUNT_KEY = 'tome_stats_now_count'
function rememberedNowCount(): number {
  try {
    const n = Number(localStorage.getItem(NOW_COUNT_KEY) ?? '2')
    return Number.isInteger(n) && n >= 0 && n <= 3 ? n : 2
  } catch {
    return 2
  }
}
function rememberNowCount(n: number) {
  try {
    localStorage.setItem(NOW_COUNT_KEY, String(n))
  } catch {
    // storage unavailable - the skeleton keeps reserving two cards
  }
}

function ReadingNow({ stats, estimates, desktop, loading, sig }: { stats: StatsResponse | null; estimates: CompletionEstimate[] | null; desktop: boolean; loading: boolean; sig: string | null }) {
  const e = useEntry('overview/now', sig)
  // The skeleton reserves as many cards as the last answer had (remembered per
  // browser), so a reader with one or three books in progress sees no shift.
  const books = stats ? stats.books_in_progress.slice(0, 3) : Array.from({ length: rememberedNowCount() }, () => null)
  useEffect(() => {
    if (stats) rememberNowCount(Math.min(3, stats.books_in_progress.length))
  }, [stats])
  const today = stats ? stats.heatmap_daily[stats.heatmap_daily.length - 1]?.date ?? readingDayOf(Date.now()) : ''
  const span = desktop ? ({ 1: 'col-span-12', 2: 'col-span-6', 3: 'col-span-4' } as Record<number, string>)[Math.max(1, books.length)] : ''

  const line = (b: StatsResponse['books_in_progress'][number]) => {
    const parts: string[] = []
    const est = estimates?.find((e) => e.book_id === b.book_id)
    if (est?.estimated_days != null) {
      const n = est.estimated_days
      parts.push(plural(n, { one: 'about a day left', other: 'about # days left' }))
    } else if (b.progress < 5) parts.push(t`just started`)
    if (b.last_read) {
      const day = readingDayOf(b.last_read)
      const diff = dayDiff(day, today)
      if (diff <= 0) parts.push(t`last read today`)
      else if (diff === 1) parts.push(t`last read yesterday`)
      else if (diff < 7) parts.push(plural(diff, { one: 'last read # day ago', other: 'last read # days ago' }))
      else { const on = fmtShortDay(day); parts.push(t`last read ${on}`) }
    }
    return parts.join(' · ')
  }

  return (
    <section ref={e.ref} data-box="now">
      <Heading><Trans>Reading now</Trans></Heading>
      {books.length === 0 ? (
        <p className="flex h-[108px] items-center text-[15px] text-muted-foreground">{stats && <Trans>Nothing in progress. Books you start show up here.</Trans>}</p>
      ) : (
        <div className={desktop ? 'grid grid-cols-12 gap-4' : 'flex flex-col gap-5'}>
          {books.map((b, i) => {
            // A card is one link to its book: the title underlines and the cover lifts on hover, as on Home.
            const body = (
              <>
                <Cover id={b ? (b.has_cover ? b.book_id : null) : null} h={108} className={b ? 'transition-transform duration-200 group-hover:-translate-y-0.5' : undefined} />
                <div className="min-w-0 flex-1">
                  <div className={cn('truncate font-display font-bold text-foreground', desktop ? 'text-[20px]' : 'text-[18px]', 'leading-[1.15]')}>
                    {b ? <span className="underline-offset-4 group-hover:underline">{b.title}</span> : <Ph on>{PH.title}</Ph>}
                  </div>
                  <div className="mt-1 truncate text-[15px] leading-[1.4] text-muted-foreground">{b ? b.author ?? ' ' : <Ph on>{PH.author}</Ph>}</div>
                  <div className="mt-3 flex h-5 items-center gap-3">
                    <Track pct={b?.progress ?? 0} className={loading ? 'opacity-40' : ''} style={stagger(i, books.length)} />
                    <span className="text-[17px] font-bold tabular-nums text-foreground">
                      <Ph on={!b}>{b ? <><CountUp entry={e} value={Math.round(b.progress)} format={String} />%</> : PH.pct}</Ph>
                    </span>
                  </div>
                  <div className="mt-2 truncate text-[14px] leading-[1.4] text-muted-foreground">{b ? line(b) || ' ' : <Ph on>{PH.bookLine}</Ph>}</div>
                </div>
              </>
            )
            const box = cn('flex min-w-0 items-center gap-5', span)
            return b ? (
              <Link key={b.book_id} to={`/books/${b.book_id}`} data-box={`now-card-${i}`} className={cn(box, 'group rounded-[8px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4 focus-visible:ring-offset-background')}>{body}</Link>
            ) : (
              <div key={i} data-box={`now-card-${i}`} className={box}>{body}</div>
            )
          })}
        </div>
      )}
    </section>
  )
}

// ── Finished: one row of covers at a fixed height ─────────────────────────────

function Finished({ stats, width, desktop, loading, rangePhrase, dim, sig }: { stats: StatsResponse | null; width: number; desktop: boolean; loading: boolean; rangePhrase: string; dim: string; sig: string | null }) {
  const e = useEntry('overview/finished', sig)
  const books = useMemo(() => {
    if (!stats) return []
    const monthOf = (iso: string) => new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short' })
    return stats.books_finished
      .map((b, k) => ({ ...b, k }))
      .sort((a, b) => b.date.localeCompare(a.date) || b.k - a.k)
      .map((b) => ({ ...b, month: monthOf(b.date), monthKey: b.date.slice(0, 7) }))
  }, [stats])
  // Slots of 2:3 at the shelf's height; as many as fit, the last one "+N" when more are left.
  const fit = Math.max(1, Math.floor((width + SHELF_GAP) / (COVER_SHELF_W + SHELF_GAP)))
  const count = books.length
  const n = stats ? count : fit
  const shown = desktop && n > fit ? fit - 1 : n
  const full = !desktop || shown + (n > shown ? 1 : 0) >= fit

  let prevMonth: string | null = null
  const cells: ReactNode[] = []
  for (let i = 0; i < shown; i++) {
    const b = books[i]
    const label = b && b.monthKey !== prevMonth ? b.month : ''
    if (b) prevMonth = b.monthKey
    const finishedOn = b ? fmtDay(b.date) : ''
    cells.push(
      <div key={b ? `${b.book_id}-${b.k}` : i} className="flex shrink-0 flex-col gap-2">
        <div className="h-[18px] whitespace-nowrap text-[13px] font-medium leading-[18px] text-muted-foreground">{loading && i === 0 ? <Ph on>{PH.month}</Ph> : label}</div>
        {b ? (
          <Link
            to={`/books/${b.book_id}`}
            aria-label={b.title}
            {...tipAttrs({ title: b.title, value: t`Finished ${finishedOn}` })}
            className={cn('group block rounded-[8px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', K.rise)}
            style={stagger(i, shown + (n > shown ? 1 : 0))}
          >
            <Cover id={b.book_id} h={COVER_SHELF_H} className="transition-transform duration-200 group-hover:-translate-y-0.5" />
          </Link>
        ) : (
          <Cover id={null} h={COVER_SHELF_H} />
        )}
      </div>,
    )
  }
  if (n > shown) {
    const more = n - shown
    // "+N" leads to the library's read books, as Home's "+N" leads to the books being read.
    // The library has no finish dates to filter by, so the list there is every read book,
    // which the tip says.
    const moreTitle = plural(more, { one: '# more finished', other: '# more finished' })
    cells.push(
      <div key="more" className="flex shrink-0 flex-col gap-2">
        <div className="h-[18px]" />
        <Link
          to="/?tab=books&reading_status=read"
          {...tipAttrs({ title: moreTitle, note: t`Show every read book in the library` })}
          className={cn('grid place-items-center rounded-[8px] bg-secondary text-[15px] font-semibold text-muted-foreground transition-colors hover:bg-surface-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', K.rise)}
          style={{ width: COVER_SHELF_W, height: COVER_SHELF_H, ...stagger(shown, shown + 1) }}
        >
          +{more}
        </Link>
      </div>,
    )
  }
  const note = stats ? plural(count, { one: `# book in ${rangePhrase}`, other: `# books in ${rangePhrase}` }) : <Ph on>{PH.shelfNote}</Ph>

  return (
    <section ref={e.ref} data-box="finished" className={dim}>
      <Heading note={note}><Trans context="reading">Finished</Trans></Heading>
      {stats && count === 0 ? (
        <p className="flex items-center text-[15px] text-muted-foreground" style={{ height: COVER_SHELF_H + 26 }}>{t`Nothing finished in ${rangePhrase}.`}</p>
      ) : desktop ? (
        <div data-box="shelf" className={cn('flex', full ? 'justify-between' : 'gap-6')}>{cells}</div>
      ) : (
        <div className="-mx-5 overflow-x-auto px-5 [scrollbar-width:none]">
          <div className="flex w-max gap-3 pr-5">{cells}</div>
        </div>
      )}
    </section>
  )
}

// ── Where the time went ───────────────────────────────────────────────────────

function TopBooks({ stats, loading, desktop, rangePhrase, onAllBooks, dim, sig }: { stats: StatsResponse | null; loading: boolean; desktop: boolean; rangePhrase: string; onAllBooks: () => void; dim: string; sig: string | null }) {
  const e = useEntry('overview/top', sig)
  const total = stats?.headline.total_reading_seconds ?? 0
  const rows = stats ? stats.top_books.slice(0, TOP_ROWS) : Array.from({ length: TOP_ROWS }, () => null)
  const max = Math.max(1, ...(stats?.top_books.slice(0, 1).map((b) => b.seconds) ?? [1]))
  const covers = useMemo(() => new Map((stats?.per_book_time ?? []).map((b) => [b.book_id, b.has_cover])), [stats])
  return (
    <section ref={e.ref} data-box="top" className={cn('min-w-0', dim)}>
      <Heading><Trans>Where the time went</Trans></Heading>
      <div data-box="top-rows" style={{ minHeight: TOP_ROWS * 54 + (TOP_ROWS - 1) }}>
        {stats && rows.length === 0 ? (
          <p className="pt-3 text-[15px] text-muted-foreground">{t`No reading in ${rangePhrase}.`}</p>
        ) : (
          rows.map((b, i) => {
            const dur = b ? formatDuration(b.seconds) : PH.dur
            const share = b && total > 0 ? Math.round((b.seconds / total) * 100) : 0
            const sessions = b ? plural(b.sessions, { one: '# session', other: '# sessions' }) : ''
            const body = (
              <>
                <Cover id={b ? (covers.get(b.book_id) === false ? null : b.book_id) : null} h={48} className="rounded-[6px]" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-4">
                    <span className="min-w-0 flex-1 truncate text-[15px] font-semibold text-foreground transition-colors group-hover:text-primary">
                      {b ? b.title : <Ph on>{PH.title}</Ph>}
                    </span>
                    {desktop && <span className="whitespace-nowrap text-[14px] text-muted-foreground"><Ph on={!b}>{dur}</Ph></span>}
                  </div>
                  <div className={cn('flex items-center gap-3', desktop ? 'mt-[9px]' : 'mt-2')}>
                    <Track
                      pct={b ? (b.seconds / max) * 100 : 0}
                      className={loading ? 'opacity-40' : ''}
                      style={stagger(i, rows.length)}
                      tip={b ? tipAttrs({ title: b.title, value: dur, note: t`${sessions} · ${share}% of the reading in ${rangePhrase}` }) : undefined}
                    />
                    {!desktop && <span className="whitespace-nowrap text-[13px] text-muted-foreground"><Ph on={!b}>{dur}</Ph></span>}
                  </div>
                </div>
              </>
            )
            const row = cn('flex h-[54px] items-center gap-4', i > 0 && cn('border-t', HAIR))
            // A row is one link to its book; the title takes the accent on hover, as Home's rows do.
            return b ? (
              <Link key={b.book_id} to={`/books/${b.book_id}`} data-box={`top-row-${i}`} className={cn(row, 'group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring')}>{body}</Link>
            ) : (
              <div key={i} data-box={`top-row-${i}`} className={row}>{body}</div>
            )
          })
        )}
      </div>
      <div className="mt-3">
        <button type="button" onClick={onAllBooks} className="text-[15px] font-medium text-primary hover:underline underline-offset-4"><Trans>All books</Trans></button>
      </div>
    </section>
  )
}

// ── When you read ─────────────────────────────────────────────────────────────

function WhenYouRead({ stats, loading, width, desktop, dim, sig }: { stats: StatsResponse | null; loading: boolean; width: number; desktop: boolean; dim: string; sig: string | null }) {
  const e = useEntry('overview/when', sig)
  const two = !desktop
  const histH = desktop ? 86 : 48
  const m = useMemo(() => hourModel(stats?.hour_dow_heatmap ?? [], two), [stats, two])
  const plural7 = [t`Mondays`, t`Tuesdays`, t`Wednesdays`, t`Thursdays`, t`Fridays`, t`Saturdays`, t`Sundays`]
  let sentence: ReactNode
  if (loading) sentence = <Ph on>{PH.when}</Ph>
  else if (m.peakHour == null || m.peakDow == null) sentence = t`No reading in this range yet.`
  else {
    const fromH = `${String(m.peakHour).padStart(2, '0')}:00`
    const toH = `${String((m.peakHour + 1) % 24).padStart(2, '0')}:00`
    const day = plural7[m.peakDow]
    sentence = t`Mostly between ${fromH} and ${toH}, most on ${day}.`
  }
  return (
    <section ref={e.ref} data-box="when" className={cn('min-w-0', dim)}>
      <Heading note={two ? t`two-hour columns` : undefined}><Trans>When you read</Trans></Heading>
      <p className={cn('mb-3.5 text-[15px] leading-[1.45] text-foreground', desktop ? 'truncate' : 'line-clamp-2 min-h-[44px] [text-wrap:balance]')}>{sentence}</p>
      <div data-box="when-chart" style={{ height: hourHeatHeight(width, two, histH) }}>
        <HourHeat W={width} grid={m.grid} byCol={m.byCol} colN={m.colN} two={two} histH={histH} loading={loading} />
      </div>
    </section>
  )
}

// ── The year ──────────────────────────────────────────────────────────────────

function TheYear({ stats, loading, width, desktop, sig }: { stats: StatsResponse | null; loading: boolean; width: number; desktop: boolean; sig: string | null }) {
  const e = useEntry('overview/year', sig)
  const year = useMemo(() => yearModel(stats?.heatmap_daily ?? emptyYear()), [stats])
  const weeks = desktop ? null : 26
  const active = year.activeDays
  const activeText = plural(active, { one: '# active day in the last 365', other: '# active days in the last 365' })
  const cut = (s: number) => {
    const h = Math.floor(s / 3600)
    const m = Math.round((s % 3600) / 60)
    // the same h/m notation as formatDuration
    // eslint-disable-next-line lingui/no-unlocalized-strings
    return h === 0 ? `${m}m` : m === 0 ? `${h}h` : `${h}h ${m}m`
  }
  const c = year.cuts
  const legend = c.length === 4
    ? (() => {
        const [a, b, d, e] = c.map(cut)
        return [t`under ${a}`, t`${a} to ${b}`, t`${b} to ${d}`, t`${d} to ${e}`, t`${e} and more`]
      })()
    : []
  return (
    <section ref={e.ref} data-box="year">
      {desktop ? (
        <Heading right note={<Ph on={loading}>{loading ? PH.active : activeText}</Ph>}><Trans>The year</Trans></Heading>
      ) : (
        <>
          <SectionTitle className="mb-1.5" action={<span className="text-[14px] font-normal text-muted-foreground"><Trans>last 26 weeks</Trans></span>}><Trans>The year</Trans></SectionTitle>
          <div className="mb-3.5 text-[13px] leading-[1.4] text-muted-foreground"><Ph on={loading}>{loading ? PH.active : activeText}</Ph></div>
        </>
      )}
      <div data-box="year-chart" style={{ height: yearHeight(width, weeks ?? year.cols) }}>
        <YearGrid W={width} year={year} weeks={weeks} loading={loading} />
      </div>
      <div data-box="year-legend" className="mt-3.5 flex flex-wrap items-center gap-x-[18px] gap-y-1.5 text-[12px] text-muted-foreground">
        {desktop ? (
          <>
            <span><i className="mr-1.5 inline-block h-[11px] w-[11px] rounded-[3px] align-[-1px]" style={{ background: 'var(--k-lv0)' }} /><Trans>no reading</Trans></span>
            {legend.map((l, i) => (
              <span key={i}><i className="mr-1.5 inline-block h-[11px] w-[11px] rounded-[3px] align-[-1px]" style={{ background: `var(--k-lv${i + 1})` }} />{l}</span>
            ))}
          </>
        ) : (
          <span className="flex items-center gap-1">
            <span className="mr-1"><Trans>Less</Trans></span>
            {[1, 2, 3, 4, 5].map((i) => <i key={i} className="inline-block h-[11px] w-[11px] rounded-[3px]" style={{ background: `var(--k-lv${i})` }} />)}
            <span className="ml-1"><Trans>More</Trans></span>
          </span>
        )}
      </div>
    </section>
  )
}

// ── Records ───────────────────────────────────────────────────────────────────

function Records({ stats, loading, desktop, sig }: { stats: StatsResponse | null; loading: boolean; desktop: boolean; sig: string | null }) {
  const e = useEntry('overview/records', sig)
  const r = stats?.records
  const s = stats?.headline.streaks
  const dash = '—'
  const cells: { label: ReactNode; value: ReactNode; unit?: string; sub: string; ph: string; phSub: string }[] = [
    { label: <Trans>Longest session</Trans>, value: r ? <CountUp entry={e} value={r.longest_session_seconds} format={formatDuration} /> : '', sub: r?.longest_session_title ?? dash, ph: PH.session, phSub: PH.title },
    { label: <Trans>Biggest reading day</Trans>, value: r ? <CountUp entry={e} value={r.biggest_day_seconds} format={formatDuration} /> : '', sub: r?.biggest_day_date ? fmtDay(r.biggest_day_date) : dash, ph: PH.day, phSub: PH.date },
    { label: <Trans>Most pages in a day</Trans>, value: r ? <CountUp entry={e} value={r.most_pages_day} format={num} /> : '', unit: r ? plural(r.most_pages_day, { one: 'page', other: 'pages' }) : undefined, sub: r?.most_pages_date ? fmtDay(r.most_pages_date) : dash, ph: PH.pagesDay, phSub: PH.date },
    {
      label: <Trans>Longest streak</Trans>,
      value: s ? <CountUp entry={e} value={s.longest_days} format={num} /> : '',
      unit: s ? plural(s.longest_days, { one: 'day', other: 'days' }) : undefined,
      sub: s?.longest_start && s.longest_end ? (() => { const a = fmtDay(s.longest_start!), b = fmtDay(s.longest_end!); return t`${a} to ${b}` })() : dash,
      ph: PH.streak, phSub: PH.streakSpan,
    },
  ]
  return (
    <section ref={e.ref} data-box="records">
      <Heading note={<Trans>all time</Trans>}><Trans>Records</Trans></Heading>
      <div className={cn('grid', desktop ? 'grid-cols-4' : 'grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]')}>
        {cells.map((c, i) => (
          <div
            key={i}
            data-box={`record-${i}`}
            className={cn(
              'min-w-0',
              desktop ? cn('px-6 py-0.5', i === 0 && 'pl-0', i > 0 && cn('border-l', HAIR)) : cn('py-4', i % 2 === 0 ? 'pr-2.5' : cn('border-l pl-3.5', HAIR), i < 2 ? cn('border-b pt-0', HAIR) : 'pb-0'),
            )}
          >
            <div className={cn('text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]')}>{c.label}</div>
            <div className={cn('mt-1.5 font-display font-bold tabular-nums text-foreground', desktop ? 'text-[30px]' : 'text-[26px]', 'leading-none')}>
              <Ph on={loading}>{loading ? c.ph : c.value}</Ph>
              {c.unit && <span className="ml-1.5 font-sans text-[16px] font-semibold tracking-normal">{c.unit}</span>}
            </div>
            <div className={cn('mt-1.5 truncate text-muted-foreground', desktop ? 'text-[14px]' : 'text-[12px]', 'leading-[1.4]', !desktop && i === 3 && 'whitespace-normal')} title={c.sub}>
              <Ph on={loading}>{loading ? c.phSub : c.sub}</Ph>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
