// The designed Overview: one fixed layout on the Home page's habits (section
// titles, Stat figures, real covers on hairlines). Every section's size follows
// from fixed heights and row counts, so the skeleton below draws each box where
// the loaded page will have it and nothing moves when the data arrives.
// Motion and hover come from the kit: each section has an entry (kit/motion.ts:
// range-bound sections play again for a new range, the rest once a session),
// figures count up (CountUp), and chart marks, tracks and covers have tips (kit/tip.ts).
import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Check } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { cn, formatDuration } from '@/lib/utils'
import { PRESS, SectionTitle } from '@/components/page'
import { BookLink } from '@/components/motion'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import type { CompletionEstimate, StatsResponse } from '@/components/stats/shared'
import { fmtDay as fmtShortDay, type CustomRange } from '@/components/stats/range'
import { Cover, Heading, Pending, Ph, Track } from '@/components/stats/kit/Kit'
import { CountUp } from '@/components/stats/kit/CountUp'
import { K, ONCE, rangeSig, stagger, useEntry } from '@/components/stats/kit/motion'
import { useTween } from '@/components/stats/kit/tween'
import { tipAttrs } from '@/components/stats/kit/tip'
import { DESKTOP_MIN, DIM, HAIR, LEVELS, UNDIM, fmtDay, num, useWidth } from '@/components/stats/kit/tokens'
import { BarsChart, HourHeat, YearGrid } from './charts'
import { GoalsStart } from './Goals'
import { CompactRow } from '@/components/goals/CompactRow'
import { rememberedGoalCount, useGoals } from '@/lib/goals'
import { bestDay, busiestMonth, dayDiff, emptyYear, heroBars, hourHeatHeight, hourModel, readingDayOf, yearHeight, yearModel } from './model'
import { inWeekOrder } from '@/lib/weekStart'


const HERO_CHART_H = 180
// The desktop hero's figures as one row: every cell spans the row's four lines
// (caption, figure, foot, second foot) on a shared subgrid, top-aligned. The small
// cells' figure and foot are pushed down by fixed margins (FIG_SMALL_MT, FOOT_SMALL_MT)
// so the 40 px figures sit on the 60 px total's baseline and each foot line is one line
// across the row. Baseline alignment across subgrid items did this in Chromium only:
// Firefox lines the rows up by top and WebKit offsets the feet and changes the row's
// height between layouts, so the alignment follows from the box model instead.
const FIG_ROW = 'grid grid-cols-[342px_repeat(4,minmax(0,1fr))] grid-rows-[repeat(4,auto)]'
const FIG_ROW_CELL = 'row-span-4 grid grid-rows-subgrid items-start'
// The total's figure is 60 px at line-height 1 after a 10 px margin; a 40 px figure at
// line-height 1 shares its baseline 27 px below the row's top (the display font's
// baseline sits 0.85 em into a line-height 1 box: 10 + 0.85 · (60 − 40) ≈ 27). The
// total's 14 px change line after 12 px and a 13 px foot line (both line-height 1.4)
// share a baseline when the foot starts 13 px down. The values Chromium's baseline
// alignment gave, measured.
const FIG_SMALL_MT = 'mt-[27px]'
const FOOT_SMALL_MT = 'mt-[13px]'
const COVER_SHELF_H = 120
const COVER_SHELF_W = 80 // 2:3 at 120 high
const SHELF_GAP = 12
const TOP_ROWS = 5

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text: drawn as blocks
   while loading and never read; it only gives each line its loaded width. */
const PH = {
  total: '000h 00m', delta: '8% more than the previous 90 days · 95h 50m',
  sessions: '000', sessionsFoot: 'avg 00m', pages: '00,000', pagesFoot: '000 a day',
  finished: '00', finishedFoot: '00 in the previous 90 days', finishedShorter: ['00 the 90 days before', '00 before'], streak: '000', streakFoot: 'since Dec 10, 2025', streakFoot2: 'your longest',
  title: 'He Who Fights with Monsters', author: 'Shirtaloon', pct: '00%', bookLine: 'about 4 days left · last read yesterday',
  month: 'Nov', shelfNote: '13 books in the last 90 days', dur: '00h 00m', when: 'Mostly between 01:00 and 02:00, most on Mondays.',
  active: '361 active days in the last 365', life: '000h 00m', lifeFoot: 'avg 1h 06m a reading day', lifeN: '00,000', lifeNFoot: '000 a reading day', session: '0h 0m', day: '0h 00m', pagesDay: '0,000', date: 'Sep 9, 2026', streakSpan: 'Dec 10, 2025 to Nov 15, 2026',
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
  /** The range asked for (differs from days/custom while a newer answer is on its way). */
  requested: { days: number; custom: CustomRange | null }
  onAllBooks: () => void
  /** Ask for the stats again, keeping this answer on screen until the new one lands (after a book is marked read). */
  onRefresh: () => void
  /** Open the Goals tab (on one goal). */
  onGoals: (goalId?: number) => void
}

export function Overview({ stats, estimates, pending, days, custom, onAllBooks, onRefresh, onGoals }: OverviewProps) {
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
  const { data: goalsAnswer } = useGoals()
  const goals = goalsAnswer?.goals ?? null
  // A first visit (nothing remembered in this browser): sections whose size is not
  // known yet are not drawn as a skeleton, and nothing under them is drawn either
  // until the answer that sizes them lands, so what is drawn never moves. Whether
  // there are goals (their own quick request) decides everything under the hero;
  // on the phone, the number of books in progress (the cards stack) decides what
  // comes after Reading now. With the shape remembered, the whole skeleton is drawn.
  const [goalCount] = useState(rememberedGoalCount)
  const goalsKnown = goalCount != null
  const [nowKnown] = useState(nowCountKnown)
  const holdGoals = !goals && !goalsKnown
  const holdNow = !stats && !desktop && !nowKnown
  const topKey = custom ? null : String(days)

  return (
    <div ref={ref} style={LEVELS} className={cn('flex flex-col', desktop ? 'gap-[52px]' : 'gap-10')} aria-busy={loading || pending}>
      {width > 0 && (
        <>
          <Hero stats={stats} desktop={desktop} width={width} loading={loading} neverRead={neverRead} prevPhrase={prevPhrase} rangePhrase={rangePhrase} days={days} custom={custom} dim={dim} sig={sig} />
          {holdGoals && <Pending />}
          {!holdGoals && <CompactRow goals={goals} count={goalCount ?? 0} today={goalsAnswer?.today} desktop={desktop} open={onGoals} />}
          {!holdGoals && <ReadingNow stats={stats} estimates={estimates} desktop={desktop} loading={loading} sig={once} onRefresh={onRefresh} />}
          {!holdGoals && holdNow && <Pending />}
          {!holdGoals && !holdNow && <>
          <Finished stats={stats} width={width} desktop={desktop} loading={loading} rangePhrase={rangePhrase} allTime={days === 0 && !custom} dim={dim} sig={sig} />
          <div className={cn(desktop ? 'grid grid-cols-2 gap-4' : 'flex flex-col gap-10')}>
            <TopBooks stats={stats} loading={loading} desktop={desktop} rangeKey={topKey} rangePhrase={rangePhrase} allTime={days === 0 && !custom} onAllBooks={onAllBooks} dim={dim} sig={sig} />
            <WhenYouRead stats={stats} loading={loading} width={desktop ? Math.floor((width - 16) / 2) : width} desktop={desktop} dim={dim} sig={sig} />
          </div>
          <TheYear stats={stats} loading={loading} width={width} desktop={desktop} sig={once} />
          <AllTime stats={stats} loading={loading} desktop={desktop} sig={once} />
          <GoalsStart goals={goals} remembered={goalCount ?? 0} />
          </>}
        </>
      )}
    </div>
  )
}

// ── Hero: reading time, four figures, the chart ───────────────────────────────

function Hero({ stats, desktop, width, loading, neverRead, prevPhrase, rangePhrase, days, custom, dim, sig }: {
  stats: StatsResponse | null
  desktop: boolean
  width: number
  loading: boolean
  neverRead: boolean
  prevPhrase: string
  rangePhrase: string
  days: number
  custom: CustomRange | null
  dim: string
  sig: string | null
}) {
  const e = useEntry('overview/hero', sig)
  const h = stats?.headline
  const pc = stats?.period_comparison ?? null
  const chart = useMemo(() => (stats ? heroBars(stats.daily) : null), [stats])
  // The best day of the range, as a quiet line in the chart. All time: the all-time
  // record (the chart itself shows only the last 365 days).
  const best = useMemo(() => (stats ? bestDay(stats, days === 0 && !custom) : null), [stats, days, custom])
  // At 1y and All, the busiest month of the chart's twelve months beside it (the
  // server's year summary picks it the same way, from the same days).
  const month = useMemo(() => (stats && !custom && (days === 365 || days === 0) ? busiestMonth(stats.daily) : null), [stats, days, custom])
  const totalLabel = h ? <CountUp entry={e} value={h.total_reading_seconds} format={formatDuration} /> : PH.total

  let delta: ReactNode = ' '
  let deltaTail: ReactNode = null
  if (loading) delta = <Ph on>{PH.delta}</Ph>
  else if (neverRead) delta = t`No reading yet`
  else if (pc) {
    const prev = formatDuration(pc.previous_seconds)
    const pct = pc.pct_change == null ? null : Math.round(Math.abs(pc.pct_change))
    const up = (pc.pct_change ?? 0) > 0
    const down = (pc.pct_change ?? 0) < 0
    // The previous total goes in its own piece so it is the first thing dropped when
    // the line does not fit its cell (see the line's markup below).
    deltaTail = desktop ? <span className="whitespace-nowrap font-medium text-foreground">{prev}</span> : null
    if (pc.pct_change == null) delta = <><Trans>Nothing in {prevPhrase}</Trans></>
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
        </>
      )
    } else delta = <><Trans>The same as {prevPhrase}</Trans></>
  } else if (days === 0 && stats) {
    const active = stats.lifetime.active_days
    delta = plural(active, { one: '# reading day in all', other: '# reading days in all' })
  }

  const prev = (n: number, phrase: string) => { const c = num(n); return t`${c} in ${phrase}` }
  // Shorter wordings of the same line for a narrow cell, longest first (see FitLine).
  const span = stats?.daily.length ?? days
  const prevShorter = (count: number) => {
    const c = num(count)
    const n = custom ? span : days
    const mid = !custom && days === 365 ? t`${c} the year before` : t`${c} the ${n} days before`
    return [mid, t`${c} before`]
  }
  const longest = h ? h.streaks.longest_days : 0
  const streakStart = h?.streaks.current_start ? fmtDay(h.streaks.current_start) : null
  const longestLine = h && h.streaks.current_days > 0 && h.streaks.current_days >= longest ? t`your longest` : plural(longest, { one: 'longest # day', other: 'longest # days' })
  // The first foot line is never left blank. The server keeps a streak while its last
  // day is today or yesterday (reading days roll over at 04:00), so 0 means yesterday
  // was missed (and today not read yet). It does not say when the last streak ended,
  // and the heatmap's days count differently (short web sessions, imported history),
  // so no date is guessed. Short enough for the phone's narrow cell; each line truncates
  // rather than wrap, so a longer translation cannot add a third line.
  let streakLine = ''
  let streakLine2 = longestLine
  if (h) {
    if (h.streaks.current_days > 0) streakLine = streakStart ? t`since ${streakStart}` : t`running`
    else if (longest > 0) streakLine = t`missed yesterday`
    else { streakLine = t`no reading yet`; streakLine2 = t`one day starts it` }
  }

  const hasPrev = !!pc && pc.previous_books_finished != null
  const figs: { label: ReactNode; value: ReactNode; unit?: ReactNode; foot: ReactNode; shorter?: string[]; foot2?: ReactNode; tone?: string; ph: string; phFoot: string; phShorter?: string[]; phFoot2?: string }[] = [
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
      // All time has no previous window (and older servers send no previous count): the range then.
      foot: hasPrev ? prev(pc.previous_books_finished!, prevPhrase) : t`in ${rangePhrase}`,
      shorter: hasPrev ? prevShorter(pc.previous_books_finished!) : undefined,
      tone: 'text-success', ph: PH.finished, phFoot: PH.finishedFoot, phShorter: PH.finishedShorter,
    },
    {
      label: <Trans>Streak</Trans>,
      value: h ? <CountUp entry={e} value={h.streaks.current_days} format={num} /> : '',
      unit: h ? plural(h.streaks.current_days, { one: 'day', other: 'days' }) : null,
      foot: h ? <span className="block truncate">{streakLine}</span> : '',
      foot2: h ? <span className="block truncate">{streakLine2}</span> : '',
      tone: 'text-primary', ph: PH.streak, phFoot: PH.streakFoot, phFoot2: PH.streakFoot2,
    },
  ]

  const figures = figs.map((f, i) => (
    <div
      key={i}
      data-box={`hero-fig-${i}`}
      className={cn(
        'min-w-0',
        desktop ? cn(FIG_ROW_CELL, 'border-l pl-3.5', HAIR) : cn('py-4', i % 2 === 0 ? 'pr-3' : cn('border-l pl-4', HAIR), i < 2 ? cn('border-b', HAIR) : 'pb-0'),
      )}
    >
      <div className="text-[14px] text-muted-foreground">{f.label}</div>
      <div className={cn('font-display font-bold tabular-nums', desktop ? cn(FIG_SMALL_MT, 'text-[40px]') : 'mt-1.5 text-[32px]', 'leading-none', loading ? 'text-foreground' : f.tone ?? 'text-foreground')}>
        <Ph on={loading}>{loading ? f.ph : f.value}</Ph>
        {f.unit && <span className="ml-1.5 font-sans text-[16px] font-semibold tracking-normal">{f.unit}</span>}
      </div>
      {/* One line: a foot longer than a narrow cell (a desktop window just above the
          breakpoint) takes its shorter wording (FitLine), and truncates only if even the
          shortest does not fit. The clip starts 4 px further left (-ml-1 pl-1) so a first
          glyph's overhang is not cut. */}
      <div className={cn(desktop ? FOOT_SMALL_MT : 'mt-1.5', '-ml-1 truncate pl-1 text-[13px] leading-[1.4] text-muted-foreground')}>
        {loading
          ? f.phShorter ? <FitLine ways={[f.phFoot, ...f.phShorter].map((s, k) => <Ph key={k} on>{s}</Ph>)} /> : <Ph on>{f.phFoot}</Ph>
          : f.shorter ? <FitLine ways={[f.foot, ...f.shorter]} /> : f.foot}
      </div>
      {f.phFoot2 && <div className="text-[13px] leading-[1.4] text-muted-foreground">{loading ? <Ph on>{f.phFoot2}</Ph> : f.foot2}</div>}
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
        <BarsChart entry={e} W={width} H={HERO_CHART_H} bars={chart?.bars ?? []} avg={chart?.avg ?? 0} monthStarts={chart?.monthStarts ?? []} nDays={chart?.nDays ?? 1} weekly={chart?.weekly ?? false} best={best} month={month} loading={loading} />
      )}
    </div>
  )

  return (
    <section ref={e.ref} data-box="hero" className={cn(desktop ? cn('border-b pb-10', HAIR) : 'flex flex-col', dim)}>
      <div className={desktop ? FIG_ROW : 'contents'}>
        <div data-box="hero-total" className={cn('min-w-0', desktop ? cn(FIG_ROW_CELL, 'pr-5') : 'order-0')}>
          <div className="text-[14px] text-muted-foreground"><Trans>Reading time</Trans></div>
          <div className={cn('mt-2.5 font-display font-bold tabular-nums text-foreground', desktop ? 'text-[60px]' : 'text-[48px]', 'leading-none')}>
            <Ph on={loading}>{totalLabel}</Ph>
          </div>
          {/* One line on desktop that never leaves its cell: the lead truncates with an
              ellipsis, and the previous total wraps onto a second, clipped line when
              there is no room for it, so it is dropped first. Two lines at most on the phone. */}
          <div className={cn('mt-3 text-[14px] leading-[1.4] text-muted-foreground', desktop ? 'flex h-[1.4em] flex-wrap overflow-hidden' : 'line-clamp-2 min-h-[39px]')}>
            <span className={desktop ? 'min-w-0 max-w-full truncate' : undefined}>{delta}</span>
            {deltaTail && <span className="flex-none whitespace-pre">{' · '}{deltaTail}</span>}
          </div>
        </div>
        {desktop ? figures : null}
      </div>
      {chartBox}
      {!desktop && <div className={cn('order-2 mt-5 grid grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] border-t', HAIR)}>{figures}</div>}
    </section>
  )
}

/**
 * One line that shows the longest of its wordings (longest first) that fits its box,
 * chosen by layout alone, so it is right on the first paint and on every resize. Inside
 * a one-line, clipping box (the hero's foot): a column of wordings that wraps into
 * further columns, four lines high, each wording the box's width and allowed to wrap,
 * each after a three-line spacer, every column zero wide (all at the box's left edge).
 * A wording that fits (one line) shares its spacer's column and sits on its fourth line;
 * one that wraps (up to three lines) does not fit beside a spacer and gets a column of
 * its own, at the top. The column is lifted three lines, so its fourth line is the box's
 * line, and every wording that fits is on it. They come shortest first, so the longest
 * that fits is drawn last, its page background covering the shorter ones under it; the
 * shortest truncates if even it does not fit. No transform: a wording that fits is drawn
 * as a plain line is. Screen readers get the first wording only.
 */
function FitLine({ ways }: { ways: ReactNode[] }) {
  const last = ways.length - 1
  // Lifted three lines; 2 px taller than four lines (rounding), given back below.
  return (
    <span className="-mb-[2px] -mt-[4.2em] flex h-[calc(5.6em+2px)] flex-col flex-wrap content-start">
      {ways.map((_, i) => {
        const k = last - i
        return (
          <Fragment key={k}>
            <span aria-hidden="true" className="-mr-[100%] h-[4.2em] w-full shrink-0" />
            <span aria-hidden={k > 0 || undefined} className={cn('-mr-[100%] w-full shrink-0', k === last ? 'truncate' : 'whitespace-normal bg-background')}>{ways[k]}</span>
          </Fragment>
        )
      })}
    </span>
  )
}

// ── Reading now ───────────────────────────────────────────────────────────────

const NOW_COUNT_KEY = 'tome_stats_now_count'
function nowCountKnown(): boolean {
  try {
    return localStorage.getItem(NOW_COUNT_KEY) != null
  } catch {
    return false
  }
}
/**
 * Cards the skeleton draws: as many as the last answer had. Not known (a first
 * visit): none on desktop, where the row is one height whatever the count but the
 * cards' widths follow it; one on the phone, where the first card stands where
 * any count puts it (what comes after waits for the answer, see Overview).
 */
function rememberedNowCount(desktop: boolean): number {
  try {
    const raw = localStorage.getItem(NOW_COUNT_KEY)
    const n = Number(raw)
    return raw != null && Number.isInteger(n) && n >= 0 && n <= 3 ? n : desktop ? 0 : 1
  } catch {
    return desktop ? 0 : 1
  }
}
function rememberNowCount(n: number) {
  try {
    localStorage.setItem(NOW_COUNT_KEY, String(n))
  } catch {
    // storage unavailable - the skeleton keeps reserving two cards
  }
}

// At or above this a book is as good as finished (devices round the last page
// differently); its card offers "Mark as read", as the old Currently reading tile did.
const FINISHED_PCT = 99
const NOW_CARD_H = 108
const NOW_GAP_PHONE = 20

function ReadingNow({ stats, estimates, desktop, loading, sig, onRefresh }: { stats: StatsResponse | null; estimates: CompletionEstimate[] | null; desktop: boolean; loading: boolean; sig: string | null; onRefresh: () => void }) {
  const e = useEntry('overview/now', sig)
  const tw = useTween(e, stats ? stats.books_in_progress : null, desktop)
  const { toast } = useToast()
  // Marked read here: gone from the list at once, while the stats are asked for again.
  const [marked, setMarked] = useState<ReadonlySet<number>>(() => new Set())
  const [marking, setMarking] = useState<number | null>(null)
  // After a mark the list keeps the height it had for the rest of the visit, so
  // nothing under it moves when a card leaves (only the phone stacks cards; on
  // desktop the row's height never changes).
  const [hold, setHold] = useState(0)
  const all = stats ? stats.books_in_progress.filter((b) => !marked.has(b.book_id)) : []
  // The skeleton reserves as many cards as the last answer had (remembered per
  // browser), so a reader with one or three books in progress sees no shift.
  const [skeletonCount] = useState(() => rememberedNowCount(desktop))
  const [countKnown] = useState(nowCountKnown)
  const books = stats ? all.slice(0, 3) : Array.from({ length: skeletonCount }, () => null)
  useEffect(() => {
    if (stats) rememberNowCount(Math.min(3, stats.books_in_progress.length))
  }, [stats])
  const today = stats ? stats.heatmap_daily[stats.heatmap_daily.length - 1]?.date ?? readingDayOf(Date.now()) : ''
  const span = desktop ? ({ 1: 'col-span-12', 2: 'col-span-6', 3: 'col-span-4' } as Record<number, string>)[Math.max(1, books.length)] : ''
  const minH = !desktop && hold > 0 ? hold * NOW_CARD_H + (hold - 1) * NOW_GAP_PHONE : undefined

  async function markRead(id: number, title: string) {
    setMarking(id)
    try {
      await api.put(`/books/${id}/status`, { status: 'read' })
      setHold((h) => Math.max(h, books.length))
      setMarked((m) => new Set(m).add(id))
      toast.success(t`Marked as read: ${title}`)
      // The finished count, the shelf and the rest follow from a fresh answer, drawn in place.
      onRefresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Failed to update status`)
    } finally {
      setMarking(null)
    }
  }

  const line = (b: StatsResponse['books_in_progress'][number]) => {
    const parts: string[] = []
    const est = estimates?.find((e) => e.book_id === b.book_id)
    if (b.progress >= FINISHED_PCT) { /* the button says it */ }
    else if (est?.estimated_days != null) {
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

  const more = all.length > 3 ? all.length : 0
  return (
    <section ref={e.ref} data-box="now">
      <Heading
        right
        note={more > 0 ? (
          // As Home's "+N": the library filtered to the books being read.
          <Link to="/?tab=books&reading_status=reading" className="text-[15px] font-medium text-primary underline-offset-4 hover:underline">
            {t`All ${more} in progress`}
          </Link>
        ) : undefined}
      >
        <Trans>Reading now</Trans>
      </Heading>
      {!stats && !countKnown && books.length === 0 ? (
        // A first visit on desktop: the row's height is known, its cards are not. One quiet block holds the row.
        <div aria-hidden="true" className="rounded-[12px] bg-muted/50" style={{ height: NOW_CARD_H }} />
      ) : books.length === 0 ? (
        <p className="flex items-center text-[15px] text-muted-foreground" style={{ height: Math.max(NOW_CARD_H, minH ?? 0) }}>{stats && <Trans>Nothing in progress. Books you start show up here.</Trans>}</p>
      ) : (
        // relative: the tween's ghosts of leaving cards are placed against this root.
        <div ref={tw} className={desktop ? 'relative grid grid-cols-12 gap-4' : 'relative flex flex-col gap-5'} style={{ minHeight: minH }}>
          {books.map((b, i) => {
            const done = !!b && b.progress >= FINISHED_PCT
            const rest = b ? line(b) : ''
            // A card is one link to its book (the title stretches over the card): the
            // title underlines and the cover lifts on hover, as on Home. "Mark as read"
            // sits above that link.
            return (
              <div
                key={b ? b.book_id : i}
                data-box={`now-card-${i}`}
                className={cn('relative flex min-w-0 items-center gap-5', span, b && 'group')}
              >
                {b ? (
                  // Above the title's stretched link, so the cover lifts under the pointer; the same way to the book.
                  <BookLink book={{ id: b.book_id, title: b.title, author: b.author }} tabIndex={-1} aria-hidden="true" className="relative z-10 shrink-0 rounded-[8px] focus-visible:outline-none">
                    <Cover id={b.has_cover ? b.book_id : null} book={b.book_id} h={NOW_CARD_H} lift />
                  </BookLink>
                ) : <Cover id={null} h={NOW_CARD_H} />}
                <div className="min-w-0 flex-1">
                  <div className={cn('truncate font-display font-bold text-foreground', desktop ? 'text-[20px]' : 'text-[18px]', 'leading-[1.15]')}>
                    {b ? (
                      <BookLink
                        book={{ id: b.book_id, title: b.title, author: b.author }}
                        className="underline-offset-4 after:absolute after:inset-0 after:rounded-[8px] group-hover:underline focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-ring focus-visible:after:ring-offset-4 focus-visible:after:ring-offset-background"
                      >
                        {b.title}
                      </BookLink>
                    ) : <Ph on>{PH.title}</Ph>}
                  </div>
                  <div className="mt-1 truncate text-[15px] leading-[1.4] text-muted-foreground">{b ? b.author ?? ' ' : <Ph on>{PH.author}</Ph>}</div>
                  <div className="mt-3 flex h-5 items-center gap-3">
                    <Track pct={b?.progress ?? 0} k={b ? `now-${b.book_id}` : undefined} className={loading ? 'opacity-40' : ''} style={stagger(i, books.length)} />
                    <span className="text-[17px] font-bold tabular-nums text-foreground">
                      <Ph on={!b}>{b ? <><CountUp entry={e} value={Math.round(b.progress)} format={String} />%</> : PH.pct}</Ph>
                    </span>
                  </div>
                  <div className="mt-2 h-5 truncate text-[14px] leading-5 text-muted-foreground">
                    {!b ? <Ph on>{PH.bookLine}</Ph> : done ? (
                      <>
                        <button
                          type="button"
                          onClick={() => markRead(b.book_id, b.title)}
                          disabled={marking === b.book_id}
                          className={cn('touch-feedback relative z-10 inline-flex items-center gap-1 font-medium text-primary underline-offset-4 hover:underline disabled:opacity-50', PRESS)}
                        >
                          <Check className="h-3.5 w-3.5" aria-hidden="true" />
                          <Trans>Mark as read</Trans>
                        </button>
                        {rest && <> · {rest}</>}
                      </>
                    ) : rest || ' '}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

// ── Finished: one row of covers at a fixed height ─────────────────────────────

function Finished({ stats, width, desktop, loading, rangePhrase, allTime, dim, sig }: { stats: StatsResponse | null; width: number; desktop: boolean; loading: boolean; rangePhrase: string; allTime: boolean; dim: string; sig: string | null }) {
  const e = useEntry('overview/finished', sig)
  // A cover in both ranges slides to its new slot; the others fade out or rise in.
  const tw = useTween(e, stats ? stats.books_finished : null, `${width}:${desktop}`)
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
          <BookLink
            book={{ id: b.book_id, title: b.title }}
            aria-label={b.title}
            {...tipAttrs({ title: b.title, value: t`Finished ${finishedOn}` })}
            data-k={`${b.book_id}-${b.date}`}
            data-kt="move"
            className={cn('group block rounded-[8px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', K.rise)}
            style={stagger(i, shown + (n > shown ? 1 : 0))}
          >
            <Cover id={b.book_id} h={COVER_SHELF_H} lift />
          </BookLink>
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
          data-k="more"
          data-kt="move"
          className={cn('grid place-items-center rounded-[8px] bg-secondary text-[15px] font-semibold text-muted-foreground transition-colors hover:bg-surface-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', K.rise)}
          style={{ width: COVER_SHELF_W, height: COVER_SHELF_H, ...stagger(shown, shown + 1) }}
        >
          +{more}
        </Link>
      </div>,
    )
  }
  const note = !stats ? <Ph on>{PH.shelfNote}</Ph> : allTime ? plural(count, { one: '# book, all time', other: '# books, all time' }) : plural(count, { one: `# book in ${rangePhrase}`, other: `# books in ${rangePhrase}` })

  return (
    <section ref={e.ref} data-box="finished" className={dim}>
      <Heading note={note}><Trans context="reading">Finished</Trans></Heading>
      {stats && count === 0 ? (
        <p className="flex items-center text-[15px] text-muted-foreground" style={{ height: COVER_SHELF_H + 26 }}>{allTime ? t`Nothing finished yet.` : t`Nothing finished in ${rangePhrase}.`}</p>
      ) : desktop ? (
        <div ref={tw} data-box="shelf" className={cn('relative flex', full ? 'justify-between' : 'gap-6')}>{cells}</div>
      ) : (
        <div className="-mx-5 overflow-x-auto px-5 [scrollbar-width:none]">
          <div ref={tw} className="relative flex w-max gap-3 pr-5">{cells}</div>
        </div>
      )}
    </section>
  )
}

// ── Where the time went ───────────────────────────────────────────────────────

const TOP_ROWS_KEY = 'tome_stats_top_rows'
function topRowsMemory(): Record<string, number> {
  try {
    const v = JSON.parse(localStorage.getItem(TOP_ROWS_KEY) ?? '{}')
    return v && typeof v === 'object' ? v : {}
  } catch {
    return {}
  }
}
function rememberedTopRows(rangeKey: string | null): number {
  const n = rangeKey ? topRowsMemory()[rangeKey] : undefined
  return Number.isInteger(n) && n! >= 0 && n! <= TOP_ROWS ? n! : TOP_ROWS
}
function rememberTopRows(rangeKey: string, n: number) {
  try {
    localStorage.setItem(TOP_ROWS_KEY, JSON.stringify({ ...topRowsMemory(), [rangeKey]: n }))
  } catch {
    // storage unavailable: the skeleton keeps drawing every row
  }
}

function TopBooks({ stats, loading, desktop, rangeKey, rangePhrase, allTime, onAllBooks, dim, sig }: { stats: StatsResponse | null; loading: boolean; desktop: boolean; rangeKey: string | null; rangePhrase: string; allTime: boolean; onAllBooks: () => void; dim: string; sig: string | null }) {
  const e = useEntry('overview/top', sig)
  // A book that stays slides to its new rank and its bar and time move to the new figures.
  const tw = useTween(e, stats && !loading ? stats.top_books : null, desktop)
  const total = stats?.headline.total_reading_seconds ?? 0
  // The skeleton draws as many rows as this range had last time (the box keeps its
  // height either way), so a short range does not draw rows that then go.
  const rows = stats ? stats.top_books.slice(0, TOP_ROWS) : Array.from({ length: rememberedTopRows(rangeKey) }, () => null)
  useEffect(() => {
    if (stats && !loading && rangeKey) rememberTopRows(rangeKey, Math.min(TOP_ROWS, stats.top_books.length))
  }, [stats, loading, rangeKey])
  const max = Math.max(1, ...(stats?.top_books.slice(0, 1).map((b) => b.seconds) ?? [1]))
  const covers = useMemo(() => new Map((stats?.per_book_time ?? []).map((b) => [b.book_id, b.has_cover])), [stats])
  return (
    <section ref={e.ref} data-box="top" className={cn('min-w-0', dim)}>
      <Heading><Trans>Where the time went</Trans></Heading>
      <div ref={tw} data-box="top-rows" className="k-bars relative" style={{ minHeight: TOP_ROWS * 54 + (TOP_ROWS - 1) }}>
        {stats && rows.length === 0 ? (
          <p className="pt-3 text-[15px] text-muted-foreground">{allTime ? t`No reading yet.` : t`No reading in ${rangePhrase}.`}</p>
        ) : (
          rows.map((b, i) => {
            const dur = b ? formatDuration(b.seconds) : PH.dur
            const durFig = b ? <CountUp entry={e} value={b.seconds} format={formatDuration} align="end" /> : dur
            const share = b && total > 0 ? Math.round((b.seconds / total) * 100) : 0
            const sessions = b ? plural(b.sessions, { one: '# session', other: '# sessions' }) : ''
            const body = (
              <div data-k={b ? `row-${b.book_id}` : undefined} data-kt="move" className="flex h-full min-w-0 flex-1 items-center gap-4">
                <Cover id={b ? (covers.get(b.book_id) === false ? null : b.book_id) : null} book={b?.book_id} h={48} className="rounded-[6px]" lift={!!b} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-4">
                    <span className="min-w-0 flex-1 truncate text-[15px] font-semibold text-foreground transition-colors group-hover:text-primary">
                      {b ? b.title : <Ph on>{PH.title}</Ph>}
                    </span>
                    {desktop && <span className="whitespace-nowrap text-[14px] tabular-nums text-muted-foreground"><Ph on={!b}>{durFig}</Ph></span>}
                  </div>
                  <div className={cn('flex items-center gap-3', desktop ? 'mt-[9px]' : 'mt-2')}>
                    <Track
                      pct={b ? (b.seconds / max) * 100 : 0}
                      k={b ? `bar-${b.book_id}` : undefined}
                      className={loading ? 'opacity-40' : ''}
                      style={stagger(i, rows.length)}
                      tip={b ? tipAttrs({ title: b.title, value: dur, note: allTime ? t`${sessions} · ${share}% of all your reading` : t`${sessions} · ${share}% of the reading in ${rangePhrase}` }) : undefined}
                    />
                    {!desktop && <span className="whitespace-nowrap text-[13px] tabular-nums text-muted-foreground"><Ph on={!b}>{durFig}</Ph></span>}
                  </div>
                </div>
              </div>
            )
            const row = cn('flex h-[54px] items-center gap-4', i > 0 && cn('border-t', HAIR))
            // A row is one link to its book; the title takes the accent on hover, as Home's rows do.
            return b ? (
              <BookLink key={b.book_id} book={{ id: b.book_id, title: b.title }} data-box={`top-row-${i}`} className={cn(row, 'group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring')}>{body}</BookLink>
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
  const plural7 = inWeekOrder([t`Mondays`, t`Tuesdays`, t`Wednesdays`, t`Thursdays`, t`Fridays`, t`Saturdays`, t`Sundays`])
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
        <HourHeat entry={e} W={width} grid={m.grid} byCol={m.byCol} colN={m.colN} two={two} histH={histH} loading={loading} />
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
        <YearGrid entry={e} W={width} year={year} weeks={weeks} loading={loading} />
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

// ── All time: lifetime totals, then the records ──────────────────────────────
// One lifetime block at the foot of the page under one heading: a row of totals
// (the longest streak is left to the records below it), then the records. Both
// rows are five columns on desktop, so their dividers line up, and the totals'
// cells share one baseline grid (caption, figure, foot), as the hero's do.

const SUB = 'mb-3 h-[18px] text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground'
const LIFE_ROW = 'grid grid-cols-5 grid-rows-[repeat(3,auto)]'
const LIFE_CELL = 'row-span-3 grid grid-rows-subgrid items-baseline'

function AllTime({ stats, loading, desktop, sig }: { stats: StatsResponse | null; loading: boolean; desktop: boolean; sig: string | null }) {
  const e = useEntry('overview/alltime', sig)
  const lt = stats?.lifetime
  const started = stats?.completion_rate.started ?? 0
  const perDay = (n: number) => (lt && lt.active_days > 0 ? n / lt.active_days : 0)
  const life: { label: ReactNode; value: ReactNode; foot: string; tip?: { 'data-tip': string }; ph: string; phFoot: string }[] = [
    {
      label: <Trans>Reading time</Trans>,
      value: lt ? <CountUp entry={e} value={lt.seconds} format={formatDuration} /> : '',
      foot: lt ? (() => { const avg = formatDuration(Math.round(perDay(lt.seconds))); return t`avg ${avg} a reading day` })() : '',
      // How long that is around the clock, for a sense of the total.
      tip: lt && lt.seconds >= 86400 ? tipAttrs({ title: t`All your reading`, value: formatDuration(lt.seconds), note: plural(Math.round(lt.seconds / 86400), { one: 'about # day around the clock', other: 'about # days around the clock' }) }) : undefined,
      ph: PH.life, phFoot: PH.lifeFoot,
    },
    {
      label: <Trans>Sessions</Trans>,
      value: lt ? <CountUp entry={e} value={lt.sessions} format={num} /> : '',
      foot: lt ? (() => { const avg = formatDuration(lt.sessions > 0 ? Math.round(lt.seconds / lt.sessions) : 0); return t`avg ${avg}` })() : '',
      ph: PH.sessions, phFoot: PH.sessionsFoot,
    },
    {
      label: <Trans>Pages</Trans>,
      value: lt ? <CountUp entry={e} value={lt.pages} format={num} /> : '',
      foot: lt ? (() => { const n = num(Math.round(perDay(lt.pages))); return t`${n} a reading day` })() : '',
      ph: PH.lifeN, phFoot: PH.lifeNFoot,
    },
    {
      label: <Trans context="reading">Finished</Trans>,
      value: lt ? <CountUp entry={e} value={lt.books_finished} format={num} /> : '',
      // The server's started count: books marked read or reading now.
      foot: lt ? (started > 0 ? plural(started, { one: 'of # book started', other: 'of # books started' }) : t`nothing started yet`) : '',
      ph: PH.finished, phFoot: PH.finishedFoot,
    },
    {
      label: <Trans>Active days</Trans>,
      value: lt ? <CountUp entry={e} value={lt.active_days} format={num} /> : '',
      foot: lt ? t`days with any reading` : '',
      ph: PH.sessions, phFoot: PH.lifeNFoot,
    },
  ]
  const totals = (
    <div data-box="lifetime" className={cn(desktop ? LIFE_ROW : 'grid grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]')}>
      {life.map((c, i) => (
        <div
          key={i}
          data-box={`life-${i}`}
          className={cn(
            'min-w-0',
            desktop
              ? cn(LIFE_CELL, 'px-4 py-0.5', i === 0 && 'pl-0', i === life.length - 1 && 'pr-0', i > 0 && cn('border-l', HAIR))
              : i === 4
                ? 'col-span-2 pb-0 pt-4'
                : cn('py-4', i % 2 === 0 ? 'pr-2.5' : cn('border-l pl-3.5', HAIR), cn('border-b', HAIR), i < 2 && 'pt-0'),
          )}
        >
          <div className={cn('text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]')}>{c.label}</div>
          <div className={cn('mt-1.5 font-display font-bold tabular-nums text-foreground', desktop ? 'text-[30px]' : 'text-[26px]', 'leading-none')}>
            <Ph on={loading}>{loading ? c.ph : <span {...c.tip}>{c.value}</span>}</Ph>
          </div>
          <div className={cn('mt-1.5 truncate text-muted-foreground', desktop ? 'text-[14px]' : 'text-[12px]', 'leading-[1.4]')}>
            <Ph on={loading}>{loading ? c.phFoot : c.foot}</Ph>
          </div>
        </div>
      ))}
    </div>
  )
  // Reading a KOSync reader (CrossInk) counted before its first sync is part of the totals.
  const earlier = !loading && lt?.reader_earlier && lt.reader_earlier.seconds > 0 ? lt.reader_earlier : null
  const earlierTime = earlier ? formatDuration(earlier.seconds) : ''
  const earlierPages = earlier ? num(earlier.pages) : ''
  const r = stats?.records
  const s = stats?.headline.streaks
  const dash = '—'
  // A longest streak that is the running one reads "since …" (as the hero's Streak does), which keeps it on one line.
  const spanText = (from: string, to: string, running: boolean) => {
    const a = fmtDay(from)
    if (running) return t`since ${a}`
    const b = fmtDay(to)
    return t`${a} to ${b}`
  }
  const cells: { label: ReactNode; value: ReactNode; unit?: string; sub: string; ph: string; phSub: string }[] = [
    { label: <Trans>Longest session</Trans>, value: r ? <CountUp entry={e} value={r.longest_session_seconds} format={formatDuration} /> : '', sub: r?.longest_session_title ?? dash, ph: PH.session, phSub: PH.title },
    { label: <Trans>Biggest reading day</Trans>, value: r ? <CountUp entry={e} value={r.biggest_day_seconds} format={formatDuration} /> : '', sub: r?.biggest_day_date ? fmtDay(r.biggest_day_date) : dash, ph: PH.day, phSub: PH.date },
    { label: <Trans>Most pages in a day</Trans>, value: r ? <CountUp entry={e} value={r.most_pages_day} format={num} /> : '', unit: r ? plural(r.most_pages_day, { one: 'page', other: 'pages' }) : undefined, sub: r?.most_pages_date ? fmtDay(r.most_pages_date) : dash, ph: PH.pagesDay, phSub: PH.date },
    {
      label: <Trans>Longest streak</Trans>,
      value: s ? <CountUp entry={e} value={s.longest_days} format={num} /> : '',
      unit: s ? plural(s.longest_days, { one: 'day', other: 'days' }) : undefined,
      sub: s?.longest_start && s.longest_end ? spanText(s.longest_start, s.longest_end, s.current_days > 0 && s.current_start === s.longest_start) : dash,
      ph: PH.streak, phSub: PH.streakSpan,
    },
    {
      // The weekly streak (weeks with any reading, Monday-based), as the old Personal records tile showed it.
      label: <Trans>Longest weekly streak</Trans>,
      value: s ? <CountUp entry={e} value={s.longest_weeks} format={num} /> : '',
      unit: s ? plural(s.longest_weeks, { one: 'week', other: 'weeks' }) : undefined,
      sub: s?.longest_weeks_start && s.longest_weeks_end ? spanText(s.longest_weeks_start, s.longest_weeks_end, s.current_weeks > 0 && s.current_weeks_start === s.longest_weeks_start) : dash,
      ph: PH.streak, phSub: PH.streakSpan,
    },
  ]
  return (
    <section ref={e.ref} data-box="alltime">
      <Heading><Trans>All time</Trans></Heading>
      <div className={SUB}><Trans>Totals</Trans></div>
      {totals}
      {earlier && (
        <p data-box="lifetime-reader" className={cn('mt-3 text-muted-foreground', desktop ? 'text-[14px]' : 'text-[12px]', 'leading-[1.4]')}>
          <Trans>Includes {earlierTime} and {earlierPages} pages your reader counted before it synced to Tome.</Trans>
        </p>
      )}
      <div className={cn(SUB, desktop ? 'mt-9' : 'mt-8')}><Trans>Records</Trans></div>
      <div data-box="records" className={cn('grid', desktop ? 'grid-cols-5' : 'grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]')}>
        {cells.map((c, i) => (
          <div
            key={i}
            data-box={`record-${i}`}
            className={cn(
              'min-w-0',
              // Desktop: one row of five. Phone: two by two, the weekly streak across the last row.
              desktop
                ? cn('px-4 py-0.5', i === 0 && 'pl-0', i === cells.length - 1 && 'pr-0', i > 0 && cn('border-l', HAIR))
                : i === 4
                  ? 'col-span-2 pb-0 pt-4'
                  : cn('py-4', i % 2 === 0 ? 'pr-2.5' : cn('border-l pl-3.5', HAIR), cn('border-b', HAIR), i < 2 && 'pt-0'),
            )}
          >
            <div className={cn('text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]')}>{c.label}</div>
            <div className={cn('mt-1.5 font-display font-bold tabular-nums text-foreground', desktop ? 'text-[30px]' : 'text-[26px]', 'leading-none')}>
              <Ph on={loading}>{loading ? c.ph : c.value}</Ph>
              {c.unit && <span className="ml-1.5 font-sans text-[16px] font-semibold tracking-normal">{c.unit}</span>}
            </div>
            <div className={cn('mt-1.5 truncate text-muted-foreground', desktop ? 'text-[14px]' : 'text-[12px]', 'leading-[1.4]', (desktop ? i >= 3 : i === 3) && 'line-clamp-2 h-[2.8em] whitespace-normal')} {...(i === 0 && r?.longest_session_title ? tipAttrs({ title: c.sub }) : {})}>
              <Ph on={loading}>{loading ? c.phSub : c.sub}</Ph>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
