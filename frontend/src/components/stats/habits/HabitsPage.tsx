// The designed Habits page: when, how long and how fast the reader reads. One
// fixed layout; every section's box follows from fixed heights and row counts,
// so the skeleton draws each box where the loaded page has it.
import { useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { cn, formatDuration } from '@/lib/utils'
import type { HabitsResponse, StatsResponse } from '@/components/stats/shared'
import type { DesignedPageProps } from '@/components/stats/kit/page'
import { Cover, Heading, Ph } from '@/components/stats/kit/Kit'
import { DESKTOP_MIN, DIM, HAIR, LEVELS, UNDIM, fmtDay, num, useWidth } from '@/components/stats/kit/tokens'
import { rangeQuery, useStatsGet } from '@/components/stats/kit/useStatsGet'
import { hourModel, readingDayOf } from '@/components/stats/overview/model'
import { ChartEmpty, Lengths, Months, Pace, Rhythm, Timeline } from './charts'
import { bucketCounts, byWeekday, monthStarts, monthsHeight, paceModel, rhythmHeight, timelineHeight, timelineRows } from './model'

const GAP = 16
// Session length's chart height: tuned so the side column ends on the same pixel as the 14 timeline rows.
const HIST_H = 119
const HIST_H_PHONE = 150
const PACE_H = 251
const PACE_H_PHONE = 200
const WPM_ROWS = 3
const WPM_ROW_H = 64

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text: drawn as blocks
   while loading and never read; it only gives each line its loaded width. */
const PH = {
  when: 'Mostly between 01:00 and 02:00, most on Mondays.',
  fig: '00m', figFoot: '000 sessions', perDay: '0.0', perDayFoot: 'on 00 of 00 days', longest: '0h 00m', title: 'He Who Fights with Monsters', date: 'Sep 20, 2026',
  sittings: '000 sittings', pace: '0.0 pages a minute over the last 7 reading days, 0.0 across the 90 days.',
  wpm: '000', wpmFoot: 'across 00 books', wpmUnit: 'words a minute', book: 'Sentenced to Be a Hero', bookV: '000 wpm',
  arch: 'Night-Owl Constant Reader', archSum: 'Consistent · night owl · fast-paced.', pole: 'Short reads',
}
/* eslint-enable lingui/no-unlocalized-strings */

export function HabitsPage({ stats, pending, days, custom }: DesignedPageProps) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const desktop = width >= DESKTOP_MIN
  const habitsQ = useStatsGet<HabitsResponse>('/stats/habits?' + rangeQuery(days, custom))
  const habits = habitsQ.data
  const left = Math.floor(((width - 11 * GAP) / 12) * 7 + 6 * GAP)
  const right = width - left - GAP
  const statsDim = pending ? DIM : UNDIM
  // The frame hands over the range /stats answers, so /stats/habits for a new range
  // starts once /stats lands; its sections stay dimmed through both requests.
  const bothDim = pending || habitsQ.pending ? DIM : UNDIM

  const rangeWords = custom || days === 0 ? t`the range` : t`the ${days} days`

  const timeline = <LastDays habits={habits} width={desktop ? left : width} phone={!desktop} />
  const side = (
    <>
      <Sessions stats={stats} habits={habits} desktop={desktop} dim={bothDim} />
      <SessionLength habits={habits} width={desktop ? right : width} desktop={desktop} dim={bothDim} />
    </>
  )
  // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS value
  const cols = { gridTemplateColumns: `${left}px ${right}px`, columnGap: GAP }

  return (
    <div ref={ref} style={LEVELS} className={cn('flex flex-col', desktop ? 'gap-[52px]' : 'gap-10')} aria-busy={!stats || !habits || pending || habitsQ.pending}>
      {width > 0 && (
        <>
          <WhenYouRead stats={stats} width={width} phone={!desktop} dim={statsDim} />
          {desktop ? (
            <div data-box="row-a" className="grid items-start" style={cols}>
              {timeline}
              <div data-box="side" className="flex min-w-0 flex-col gap-10">{side}</div>
            </div>
          ) : (
            <>
              {timeline}
              {side}
            </>
          )}
          {desktop ? (
            <div data-box="row-b" className="grid items-start" style={cols}>
              <PaceSection stats={stats} width={left} desktop rangeWords={rangeWords} dim={statsDim} />
              <Speed stats={stats} desktop />
            </div>
          ) : (
            <>
              <PaceSection stats={stats} width={width} desktop={false} rangeWords={rangeWords} dim={statsDim} />
              <Speed stats={stats} desktop={false} />
            </>
          )}
          <MonthsSection stats={stats} width={width} phone={!desktop} />
          <Character stats={stats} desktop={desktop} />
        </>
      )}
    </div>
  )
}

// ── When you read ─────────────────────────────────────────────────────────────

function WhenYouRead({ stats, width, phone, dim }: { stats: StatsResponse | null; width: number; phone: boolean; dim: string }) {
  const loading = !stats
  const m = useMemo(() => hourModel(stats?.hour_dow_heatmap ?? [], phone), [stats, phone])
  const dow = useMemo(() => byWeekday(stats?.hour_dow_heatmap ?? []), [stats])
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
    <section data-box="when" className={cn('min-w-0', dim)}>
      <Heading note={phone ? t`two-hour columns` : undefined}><Trans>When you read</Trans></Heading>
      <p className={cn('mb-3.5 text-[15px] leading-[1.45] text-foreground', phone ? 'line-clamp-2 min-h-[44px] [text-wrap:balance]' : 'truncate')}>{sentence}</p>
      <div data-box="when-chart" style={{ height: rhythmHeight(phone) }}>
        <Rhythm W={width} grid={m.grid} byCol={m.byCol} byDow={dow} phone={phone} loading={loading} />
      </div>
    </section>
  )
}

// ── Last 14 days (always the last 14 reading days: not range-bound) ───────────

function LastDays({ habits, width, phone }: { habits: HabitsResponse | null; width: number; phone: boolean }) {
  const rows = useMemo(() => (habits ? timelineRows(habits.last_14_days) : []), [habits])
  return (
    <section data-box="days" className="min-w-0">
      <Heading><Trans>Last 14 days</Trans></Heading>
      <div data-box="days-chart" style={{ height: timelineHeight(phone) }}>
        <Timeline W={width} rows={rows} phone={phone} loading={!habits} />
      </div>
    </section>
  )
}

// ── Sessions: three figures ───────────────────────────────────────────────────

function Sessions({ stats, habits, desktop, dim }: { stats: StatsResponse | null; habits: HabitsResponse | null; desktop: boolean; dim: string }) {
  const loading = !stats || !habits
  const h = stats?.headline
  const activeDays = stats ? stats.daily.filter((d) => d.seconds > 0).length : 0
  const nDays = stats?.daily.length ?? 0
  const ls = habits?.longest_session ?? null
  const dash = '—'

  const avgFoot = h ? plural(h.total_sessions, { one: '# session', other: '# sessions' }) : ''
  const perDay = h && activeDays > 0 ? (h.total_sessions / activeDays).toFixed(1) : dash
  const perDayFoot = activeDays > 0 ? t`on ${activeDays} of ${nDays} days` : t`no reading days`
  const lsDate = ls ? fmtDay(readingDayOf(ls.started_at)) : ''
  const figs: { key: string; label: ReactNode; value: string; foot: ReactNode; ph: string; phFoot: ReactNode }[] = [
    { key: 'avg', label: <Trans>Average length</Trans>, value: h && h.total_sessions > 0 ? formatDuration(h.avg_session_seconds) : dash, foot: avgFoot, ph: PH.fig, phFoot: <Ph on>{PH.figFoot}</Ph> },
    { key: 'day', label: <Trans>Per reading day</Trans>, value: perDay, foot: perDayFoot, ph: PH.perDay, phFoot: <Ph on>{PH.perDayFoot}</Ph> },
    {
      key: 'long',
      label: <Trans>Longest in range</Trans>,
      value: ls ? formatDuration(ls.seconds) : dash,
      foot: ls ? (
        <>
          <span className="block truncate" title={ls.title ?? undefined}>
            {ls.book_id != null ? <Link to={`/books/${ls.book_id}`} className="hover:underline underline-offset-4">{ls.title}</Link> : ls.title ?? ' '}
          </span>
          <span className="block">{lsDate}</span>
        </>
      ) : (
        <>
          <span className="block"><Trans>No sittings in this range.</Trans></span>
          <span className="block"> </span>
        </>
      ),
      ph: PH.longest,
      phFoot: (
        <>
          <span className="block"><Ph on>{PH.title}</Ph></span>
          <span className="block"><Ph on>{PH.date}</Ph></span>
        </>
      ),
    },
  ]
  return (
    <section data-box="sessions" className={cn('min-w-0', dim)}>
      <Heading><Trans>Sessions</Trans></Heading>
      <div className={desktop ? '' : 'grid grid-cols-2'}>
        {figs.map((f, i) => (
          <div
            key={f.key}
            data-box={`sessions-${f.key}`}
            className={cn(
              'min-w-0',
              desktop
                ? i > 0 && cn('mt-4 border-t pt-4', HAIR)
                : i === 1 ? cn('border-l pl-4', HAIR) : i === 2 ? cn('col-span-2 mt-4 border-t pt-4', HAIR) : 'pr-3',
            )}
          >
            <div className="text-[14px] text-muted-foreground">{f.label}</div>
            <div className={cn('mt-1.5 font-display font-bold tabular-nums text-foreground', desktop ? 'text-[30px]' : 'text-[28px]', 'leading-none')}>
              <Ph on={loading}>{loading ? f.ph : f.value}</Ph>
            </div>
            <div className={cn('mt-1.5 text-muted-foreground tabular-nums', desktop ? 'text-[14px]' : 'text-[13px]', 'leading-[1.4]')}>
              {loading ? f.phFoot : f.foot}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

// ── Session length ────────────────────────────────────────────────────────────

function SessionLength({ habits, width, desktop, dim }: { habits: HabitsResponse | null; width: number; desktop: boolean; dim: string }) {
  const counts = bucketCounts(habits)
  const H = desktop ? HIST_H : HIST_H_PHONE
  const note = habits ? plural(habits.sessions, { one: '# sitting', other: '# sittings' }) : <Ph on>{PH.sittings}</Ph>
  return (
    <section data-box="lengths" className={cn('min-w-0', dim)}>
      <Heading note={note}><Trans>Session length</Trans></Heading>
      <div data-box="lengths-chart" style={{ height: H }}>
        {habits && habits.sessions === 0 ? (
          <ChartEmpty H={H}>{t`No sittings in this range.`}</ChartEmpty>
        ) : (
          <Lengths W={width} H={H} counts={counts} loading={!habits} />
        )}
      </div>
    </section>
  )
}

// ── Pace ──────────────────────────────────────────────────────────────────────

function PaceSection({ stats, width, desktop, rangeWords, dim }: { stats: StatsResponse | null; width: number; desktop: boolean; rangeWords: string; dim: string }) {
  const p = useMemo(() => (stats ? paceModel(stats.daily) : null), [stats])
  const months = useMemo(() => monthStarts(stats?.daily ?? []), [stats])
  const H = desktop ? PACE_H : PACE_H_PHONE
  let lead: ReactNode
  if (!p) lead = <Ph on>{PH.pace}</Ph>
  else if (p.dots.length === 0) lead = t`No pages read in this range yet.`
  else {
    const recent = p.recent.toFixed(1)
    const all = p.all.toFixed(1)
    lead = p.dots.length > 7
      ? t`${recent} pages a minute over the last 7 reading days, ${all} across ${rangeWords}.`
      : t`${all} pages a minute across ${rangeWords}.`
  }
  return (
    <section data-box="pace" className={cn('min-w-0', dim)}>
      <Heading><Trans>Pace</Trans></Heading>
      <p data-box="pace-lead" className={cn('mb-2 text-[15px] leading-[1.45] text-foreground', desktop ? 'truncate' : 'line-clamp-2 min-h-[44px]')}>{lead}</p>
      <div className="mb-3 flex gap-[18px] text-[12px] text-muted-foreground">
        <span><i className="mr-1.5 inline-block h-[7px] w-[7px] rounded-full align-middle opacity-50" style={{ background: 'var(--chart-accent)' }} /><Trans>a reading day</Trans></span>
        <span><i className="mr-1.5 inline-block h-[2px] w-4 rounded-[1px] align-middle" style={{ background: 'var(--chart-accent)' }} /><Trans>7-day mean</Trans></span>
      </div>
      <div data-box="pace-chart" style={{ height: H }}>
        {p && p.dots.length === 0 ? (
          <ChartEmpty H={H}>{t`Pace appears once reading days with page turns arrive.`}</ChartEmpty>
        ) : (
          <Pace W={width} H={H} dots={p?.dots ?? []} nDays={p?.nDays ?? 1} months={months} loading={!p} />
        )}
      </div>
    </section>
  )
}

// ── Reading speed (all time) ──────────────────────────────────────────────────

type WpmBook = StatsResponse['wpm']['books'][number]

function WpmRow({ b, first }: { b: WpmBook | null; first: boolean }) {
  const wpm = b ? num(Math.round(b.wpm)) : ''
  return (
    <div className={cn('flex items-center gap-3', !first && cn('border-t', HAIR))} style={{ height: WPM_ROW_H }}>
      {b ? (
        <Link to={`/books/${b.book_id}`} className="shrink-0" aria-label={b.title}><Cover id={b.has_cover ? b.book_id : null} h={52} className="rounded-[6px]" /></Link>
      ) : (
        <Cover id={null} h={52} className="rounded-[6px]" />
      )}
      <div className="min-w-0 flex-1">
        <div className="line-clamp-2 text-[14px] font-semibold leading-[1.25] text-foreground">
          {b ? <Link to={`/books/${b.book_id}`} className="hover:underline underline-offset-4">{b.title}</Link> : <Ph on>{PH.book}</Ph>}
        </div>
        <div className="mt-[3px] text-[13px] tabular-nums text-muted-foreground">{b ? t`${wpm} wpm` : <Ph on>{PH.bookV}</Ph>}</div>
      </div>
    </div>
  )
}

function Speed({ stats, desktop }: { stats: StatsResponse | null; desktop: boolean }) {
  const w = stats?.wpm
  const none = !!w && w.books_counted === 0
  const blank = Array.from({ length: WPM_ROWS }, () => null)
  const fast: (WpmBook | null)[] = w ? w.books.slice(0, WPM_ROWS) : blank
  const slow: (WpmBook | null)[] = w ? (w.books.length > 6 ? w.books.slice(-WPM_ROWS).reverse() : []) : blank
  const list = (title: ReactNode, rows: (WpmBook | null)[], key: string) => (
    <div data-box={`speed-${key}`} className="min-w-0">
      <div className="mb-2.5 h-[18px] text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">{title}</div>
      <div style={{ minHeight: WPM_ROWS * WPM_ROW_H }}>
        {rows.map((b, i) => <WpmRow key={b?.book_id ?? i} b={b} first={i === 0} />)}
      </div>
    </div>
  )
  // Phone stacks the lists; with fewer than seven timed books there is no Slowest list.
  const showSlow = slow.length > 0 || desktop
  return (
    <section data-box="speed" className="min-w-0">
      <Heading note={<Trans>all time</Trans>}><Trans>Reading speed</Trans></Heading>
      <div data-box="speed-fig">
        <div className="font-display text-[40px] font-bold leading-none tabular-nums text-foreground">
          <Ph on={!w}>{w ? (none ? '—' : num(Math.round(w.overall))) : PH.wpm}</Ph>
          <span className="ml-1.5 font-sans text-[16px] font-semibold tracking-normal">{w ? <Trans>words a minute</Trans> : <Ph on>{PH.wpmUnit}</Ph>}</span>
        </div>
        <div className="mt-1.5 text-[14px] leading-[1.4] text-muted-foreground">
          {w ? (none ? t`No timed reading with word counts yet.` : plural(w.books_counted, { one: 'across # book', other: 'across # books' })) : <Ph on>{PH.wpmFoot}</Ph>}
        </div>
      </div>
      <div className={cn('mt-6 grid gap-x-6', desktop ? 'grid-cols-2' : 'grid-cols-1 gap-y-5')}>
        {list(fast.length > 0 || !w ? <Trans>Fastest</Trans> : ' ', fast, 'fast')}
        {showSlow && list(slow.length > 0 ? <Trans>Slowest</Trans> : ' ', slow, 'slow')}
      </div>
    </section>
  )
}

// ── The last 12 months (all time) ─────────────────────────────────────────────

function MonthsSection({ stats, width, phone }: { stats: StatsResponse | null; width: number; phone: boolean }) {
  const months = useMemo(
    () => (stats?.monthly_comparison ?? []).map((m) => ({
      label: new Date(m.month + '-01T00:00:00').toLocaleDateString(undefined, { month: 'short' }),
      seconds: m.reading_seconds,
      books: m.books_finished,
    })),
    [stats],
  )
  const empty = !!stats && months.every((m) => m.seconds === 0 && m.books === 0)
  const H = monthsHeight(phone)
  return (
    <section data-box="months" className="min-w-0">
      <Heading><Trans>The last 12 months</Trans></Heading>
      <div data-box="months-chart" style={{ height: H }}>
        {empty ? <ChartEmpty H={H}>{t`No reading in the last 12 months.`}</ChartEmpty> : <Months W={width} months={months} phone={phone} loading={!stats} />}
      </div>
    </section>
  )
}

// ── Reading character (all time) ──────────────────────────────────────────────

function Character({ stats, desktop }: { stats: StatsResponse | null; desktop: boolean }) {
  const dna = stats?.reading_dna
  const loading = !stats
  const ready = !!dna?.ready
  const traits = ready && dna ? dna.traits : Array.from({ length: 5 }, () => null)
  return (
    <section data-box="character" className="min-w-0">
      <Heading><Trans>Reading character</Trans></Heading>
      <div className={desktop ? 'grid grid-cols-12 items-center gap-4' : 'flex flex-col'}>
        <div data-box="character-name" className={cn('min-w-0', desktop && 'col-span-4')}>
          <div className={cn('font-display font-bold tracking-[-0.02em] text-foreground', desktop ? 'text-[24px]' : 'text-[22px]', 'leading-[1.15]')}>
            {loading ? <Ph on>{PH.arch}</Ph> : ready && dna ? dna.archetype ?? ' ' : <Trans>Not yet</Trans>}
          </div>
          <div className="mt-2 truncate text-[15px] text-muted-foreground">
            {loading ? <Ph on>{PH.archSum}</Ph> : ready && dna ? dna.summary ?? ' ' : <Trans>Not enough reading history yet.</Trans>}
          </div>
        </div>
        <div data-box="character-traits" className={cn('grid min-w-0', desktop ? 'col-span-8 grid-cols-5 gap-x-4' : 'mt-5 grid-cols-1 gap-y-4')}>
          {traits.map((tr, i) => {
            const s = tr ? Math.max(0, Math.min(100, tr.score)) : 50
            const high = s >= 50
            const fill = Math.max(Math.abs(s - 50), 1.5)
            return (
              <div key={tr?.key ?? i} className="min-w-0" title={tr ? `${tr.low} – ${tr.high}: ${s}` : undefined}>
                <div className={cn('mb-2 flex justify-between gap-1.5 whitespace-nowrap text-muted-foreground', desktop ? 'text-[11px]' : 'text-[12px]', 'leading-[1.4]')}>
                  {tr ? (
                    <>
                      <span className={cn('truncate', !high && 'font-semibold text-foreground')}>{tr.low}</span>
                      <span className={cn('truncate', high && 'font-semibold text-foreground')}>{tr.high}</span>
                    </>
                  ) : (
                    <>
                      <Ph on={loading}>{loading ? PH.pole : ' '}</Ph>
                      <Ph on={loading}>{loading ? PH.pole : ' '}</Ph>
                    </>
                  )}
                </div>
                <div className="relative h-1.5 rounded-full" style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--background))', opacity: tr ? 1 : 0.4 }}>
                  <i className="absolute -bottom-[3px] -top-[3px] left-1/2 block w-px bg-border" />
                  {tr && (
                    <>
                      <i className="absolute bottom-0 top-0 block rounded-full bg-primary" style={high ? { left: '50%', width: `${fill}%` } : { right: '50%', width: `${fill}%` }} />
                      <i className="absolute -bottom-1 -top-1 -ml-px block w-0.5 rounded-[1px] bg-foreground" style={{ left: `${s}%` }} />
                    </>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}
