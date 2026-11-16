// The designed Habits page: when, how long and how fast the reader reads. One
// fixed layout; every section's box follows from fixed heights and row counts,
// so the skeleton draws each box where the loaded page has it.
// Motion and hover come from the kit: each section has an entry (kit/motion.ts:
// range-bound sections play again for a new range, the rest once a session),
// figures count up (CountUp), chart marks and rows have tips (kit/tip.ts).
import { useCallback, useMemo, type CSSProperties, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { cn, formatDuration } from '@/lib/utils'
import type { HabitsResponse, StatsResponse } from '@/components/stats/shared'
import type { DesignedPageProps } from '@/components/stats/kit/page'
import { fmtDay as fmtShortDay } from '@/components/stats/range'
import { Cover, Heading, Ph } from '@/components/stats/kit/Kit'
import { CountUp } from '@/components/stats/kit/CountUp'
import { K, ONCE, rangeSig, stagger, useEntry, type Entry } from '@/components/stats/kit/motion'
import { tipAttrs, useTips } from '@/components/stats/kit/tip'
import { DESKTOP_MIN, DIM, HAIR, LEVELS, UNDIM, fmtDay, num, useWidth } from '@/components/stats/kit/tokens'
import { rangeQuery, useStatsGet } from '@/components/stats/kit/useStatsGet'
import { hourModel, readingDayOf } from '@/components/stats/overview/model'
import { ChartEmpty, Lengths, Months, Pace, Rhythm, Timeline } from './charts'
import { bucketCounts, byWeekday, formatShares, monthsHeight, paceChange, paceModel, paceTicks, rhythmHeight, timelineHeight, timelineRows, type FormatShare } from './model'

const GAP = 16
// Session length's chart height: tuned so the side column ends on the same pixel as the 14 timeline rows.
const HIST_H = 119
const HIST_H_PHONE = 150
// Pace's chart and the one-line format slot under it (desktop: together the chart's former 251 px).
const FMT_LINE = 26
const PACE_H = 251 - FMT_LINE
const PACE_H_PHONE = 200
const WPM_ROWS = 3
const WPM_ROW_H = 64

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text: drawn as blocks
   while loading and never read; it only gives each line its loaded width. */
const PH = {
  when: 'Mostly between 01:00 and 02:00, most on Mondays.',
  fig: '00m', figFoot: '000 sessions', perDay: '0.0', perDayFoot: 'on 00 of 00 days', longest: '0h 00m', title: 'He Who Fights with Monsters', date: 'Sep 20, 2026',
  sittings: '000 sittings', pace: '0.0 pages a minute over the last 7 reading days, 0.0 across the 90 days.', formats: 'By format: EPUB 0.0 pg/min, 00 % · CBZ 00.0 pg/min, 00 %',
  wpm: '000', wpmFoot: 'across 00 books', wpmUnit: 'words a minute', book: 'Sentenced to Be a Hero', bookV: '000 wpm',
  arch: 'Night-Owl Constant Reader', archSum: 'Consistent · night owl · fast-paced.', pole: 'Short reads',
}
/* eslint-enable lingui/no-unlocalized-strings */

export function HabitsPage({ stats, pending, days, custom, requested }: DesignedPageProps) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const desktop = width >= DESKTOP_MIN
  const habitsQ = useStatsGet<HabitsResponse>('/stats/habits?' + rangeQuery(requested.days, requested.custom))
  const habits = habitsQ.data
  const left = Math.floor(((width - 11 * GAP) / 12) * 7 + 6 * GAP)
  const right = width - left - GAP
  const statsDim = pending ? DIM : UNDIM
  // /stats/habits starts with /stats for the requested range; sections drawing from
  // both stay dimmed until both have answered for it.
  const bothDim = pending || habitsQ.pending ? DIM : UNDIM
  const allTime = days === 0 && !custom

  // What the entries key on: the range an answer is for (sections that follow it), or once.
  const sig = stats ? rangeSig(days, custom) : null
  const sigBoth = stats && habits && !habitsQ.pending ? rangeSig(days, custom) : null
  const once = stats ? ONCE : null

  // The Pace chart's days: /stats keeps a year of days for All, so All reads "the last 12 months".
  let rangeWords: string
  if (custom) {
    const from = fmtShortDay(custom.start), to = fmtShortDay(custom.end)
    rangeWords = t`${from} to ${to}`
  } else if (allTime) rangeWords = t`the last 12 months`
  else rangeWords = t`the ${days} days`

  const timeline = <LastDays habits={habits} width={desktop ? left : width} phone={!desktop} />
  const side = (
    <>
      <Sessions stats={stats} habits={habits} desktop={desktop} allTime={allTime} dim={bothDim} sig={sigBoth} />
      <SessionLength habits={habits} width={desktop ? right : width} desktop={desktop} dim={bothDim} sig={sigBoth} />
    </>
  )
  // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS value
  const cols = { gridTemplateColumns: `${left}px ${right}px`, columnGap: GAP }

  return (
    <div ref={ref} style={LEVELS} className={cn('flex flex-col', desktop ? 'gap-[52px]' : 'gap-10')} aria-busy={!stats || !habits || pending || habitsQ.pending}>
      {width > 0 && (
        <>
          <WhenYouRead stats={stats} width={width} phone={!desktop} dim={statsDim} sig={sig} />
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
              <PaceSection stats={stats} width={left} desktop rangeWords={rangeWords} dim={statsDim} sig={sig} />
              <Speed stats={stats} desktop sig={once} />
            </div>
          ) : (
            <>
              <PaceSection stats={stats} width={width} desktop={false} rangeWords={rangeWords} dim={statsDim} sig={sig} />
              <Speed stats={stats} desktop={false} sig={once} />
            </>
          )}
          <MonthsSection stats={stats} width={width} phone={!desktop} sig={once} />
          <Character stats={stats} desktop={desktop} sig={once} />
        </>
      )}
    </div>
  )
}

/** A section's entry and the callback ref for its element (the entry runs on it). */
function useSection(scope: string, sig: string | null): [Entry, (el: Element | null) => void] {
  const e = useEntry(scope, sig)
  const enter = useCallback((el: Element | null) => e.ref(el), [e])
  return [e, enter]
}

// ── When you read ─────────────────────────────────────────────────────────────

function WhenYouRead({ stats, width, phone, dim, sig }: { stats: StatsResponse | null; width: number; phone: boolean; dim: string; sig: string | null }) {
  const [, enter] = useSection('habits/when', sig)
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
    <section ref={enter} data-box="when" className={cn('min-w-0', dim)}>
      <Heading note={phone ? t`two-hour columns` : undefined}><Trans>When you read</Trans></Heading>
      <p className={cn('mb-3.5 text-[15px] leading-[1.45] text-foreground', phone ? 'line-clamp-2 min-h-[44px] [text-wrap:balance]' : 'truncate')}>{sentence}</p>
      <div data-box="when-chart" style={{ height: rhythmHeight(phone) }}>
        <Rhythm W={width} grid={m.grid} byCol={m.byCol} colN={m.colN} byDow={dow} phone={phone} loading={loading} />
      </div>
    </section>
  )
}

// ── Last 14 days (always the last 14 reading days: not range-bound) ───────────

function LastDays({ habits, width, phone }: { habits: HabitsResponse | null; width: number; phone: boolean }) {
  const [, enter] = useSection('habits/days', habits ? ONCE : null)
  const rows = useMemo(() => (habits ? timelineRows(habits.last_14_days) : []), [habits])
  return (
    <section ref={enter} data-box="days" className="min-w-0">
      <Heading><Trans>Last 14 days</Trans></Heading>
      <div data-box="days-chart" style={{ height: timelineHeight(phone) }}>
        <Timeline W={width} rows={rows} phone={phone} loading={!habits} />
      </div>
    </section>
  )
}

// ── Sessions: three figures ───────────────────────────────────────────────────

const tenths = (n: number) => (n / 10).toFixed(1)

function Sessions({ stats, habits, desktop, allTime, dim, sig }: { stats: StatsResponse | null; habits: HabitsResponse | null; desktop: boolean; allTime: boolean; dim: string; sig: string | null }) {
  const [e, enter] = useSection('habits/sessions', sig)
  const loading = !stats || !habits
  const h = stats?.headline
  const ls = habits?.longest_session ?? null
  const dash = '—'

  // Reading days: /stats keeps only a year of days for All, so All counts the lifetime's;
  // a custom range that runs past today counts the days up to today.
  let activeDays = 0, perDayFoot = ''
  if (stats && allTime) {
    activeDays = stats.lifetime.active_days
    perDayFoot = activeDays > 0 ? plural(activeDays, { one: 'on # reading day', other: 'on # reading days' }) : t`no reading days`
  } else if (stats) {
    const today = readingDayOf(Date.now())
    const upTo = stats.daily.filter((d) => d.date <= today)
    activeDays = upTo.filter((d) => d.seconds > 0).length
    const nDays = upTo.length
    perDayFoot = activeDays > 0 ? t`on ${activeDays} of ${nDays} days` : t`no reading days`
  }

  const avgFoot = h ? plural(h.total_sessions, { one: '# session', other: '# sessions' }) : ''
  const perDay10 = h && activeDays > 0 ? Math.round((h.total_sessions / activeDays) * 10) : null
  const lsDate = ls ? fmtDay(readingDayOf(ls.started_at)) : ''
  const figs: { key: string; label: ReactNode; value: ReactNode; foot: ReactNode; ph: string; phFoot: ReactNode }[] = [
    {
      key: 'avg', label: <Trans>Average length</Trans>,
      value: h && h.total_sessions > 0 ? <CountUp entry={e} value={h.avg_session_seconds} format={formatDuration} /> : dash,
      foot: avgFoot, ph: PH.fig, phFoot: <Ph on>{PH.figFoot}</Ph>,
    },
    {
      key: 'day', label: <Trans>Per reading day</Trans>,
      value: perDay10 != null ? <CountUp entry={e} value={perDay10} format={tenths} /> : dash,
      foot: perDayFoot, ph: PH.perDay, phFoot: <Ph on>{PH.perDayFoot}</Ph>,
    },
    {
      key: 'long',
      label: allTime ? <Trans>Longest ever</Trans> : <Trans>Longest in range</Trans>,
      value: ls ? <CountUp entry={e} value={ls.seconds} format={formatDuration} /> : dash,
      foot: ls ? (
        <>
          <span className="block truncate">
            {ls.book_id != null ? (
              <Link to={`/books/${ls.book_id}`} title={ls.title ?? undefined} className="underline-offset-4 transition-colors hover:text-primary hover:underline focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{ls.title}</Link>
            ) : ls.title ?? ' '}
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
    <section ref={enter} data-box="sessions" className={cn('min-w-0', dim)}>
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
              {loading ? <Ph on>{f.ph}</Ph> : f.value}
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

function SessionLength({ habits, width, desktop, dim, sig }: { habits: HabitsResponse | null; width: number; desktop: boolean; dim: string; sig: string | null }) {
  const [, enter] = useSection('habits/lengths', sig)
  const counts = bucketCounts(habits)
  const H = desktop ? HIST_H : HIST_H_PHONE
  const note = habits ? plural(habits.sessions, { one: '# sitting', other: '# sittings' }) : <Ph on>{PH.sittings}</Ph>
  return (
    <section ref={enter} data-box="lengths" className={cn('min-w-0', dim)}>
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

/** The one line under Pace when the range has two or more formats: each format's pages a
 *  minute and share of the reading time ("EPUB 3.7 pg/min · 82 %"), the top `cap` of them
 *  (desktop 3, phone 2) and "+N" for the rest; full numbers in each mark's tip. One line
 *  always: the slot never grows. */
function FormatLine({ stats, desktop }: { stats: StatsResponse | null; desktop: boolean }) {
  const fs = useMemo(() => formatShares(stats?.pace_by_format ?? []), [stats])
  const cap = desktop ? 3 : 2
  const shown = fs.slice(0, cap)
  const rest = fs.slice(shown.length)
  const share = (f: FormatShare) => {
    const n = f.pct
    return n > 0 ? t`${n} %` : t`<1 %`
  }
  const tipOf = (f: FormatShare) => {
    const ppm = f.ppm.toFixed(1)
    const pct = share(f)
    const sessions = plural(f.sessions, { one: '# session', other: '# sessions' })
    const pages = plural(f.pages, { one: '# page', other: '# pages' })
    const dur = formatDuration(f.seconds)
    return { title: f.format, value: t`${ppm} pages a minute · ${pct} of the time`, note: t`${pages} in ${dur} · ${sessions}` }
  }
  const tips = useTips((i) => {
    if (i < shown.length) return tipOf(shown[i])
    if (rest.length === 1) return tipOf(rest[0])
    if (!rest.length) return null
    const n = rest.length
    const list = rest.map((f) => `${f.format} ${f.ppm.toFixed(1)} · ${share(f)}`).join(', ')
    return { title: t`${n} more formats`, value: list, note: t`pages a minute · share of the time` }
  })
  // eslint-disable-next-line lingui/no-unlocalized-strings -- class names
  const fig = 'font-medium tabular-nums text-foreground'
  let body: ReactNode = ' '
  if (!stats) body = <Ph on>{PH.formats}</Ph>
  else if (fs.length === 1) {
    const f = fs[0].format
    body = t`All of it read as ${f}.`
  } else if (fs.length > 1) {
    body = (
      <>
        {desktop && <><Trans>By format:</Trans>{' '}</>}
        {shown.map((f, i) => (
          <span key={f.format}>
            {i > 0 && <span className="px-1.5 opacity-60">·</span>}
            <span data-tip-i={i} className={cn('rounded-sm data-[hot]:text-foreground', K.fade)} style={stagger(i, fs.length)}>
              {f.format} <span className={fig}>{f.ppm.toFixed(1)}</span> <Trans>pg/min</Trans>, <span className={fig}>{share(f)}</span>
            </span>
          </span>
        ))}
        {rest.length > 0 && (
          <>
            <span className="px-1.5 opacity-60">·</span>
            <span data-tip-i={shown.length} className={cn('rounded-sm data-[hot]:text-foreground', K.fade)} style={stagger(shown.length, fs.length)}>
              +{rest.length}
            </span>
          </>
        )}
      </>
    )
  }
  const many = fs.length > 1
  return (
    <p
      {...(many ? tips : {})}
      data-box="pace-formats"
      className="mt-2 truncate whitespace-nowrap text-[13px] text-muted-foreground"
      style={{ height: FMT_LINE - 8, lineHeight: `${FMT_LINE - 8}px` }}
    >
      {body}
    </p>
  )
}

function PaceSection({ stats, width, desktop, rangeWords, dim, sig }: { stats: StatsResponse | null; width: number; desktop: boolean; rangeWords: string; dim: string; sig: string | null }) {
  const [, enter] = useSection('habits/pace', sig)
  const p = useMemo(() => (stats ? paceModel(stats.daily) : null), [stats])
  const ticks = useMemo(() => paceTicks(stats?.daily ?? []), [stats])
  const H = desktop ? PACE_H : PACE_H_PHONE
  let lead: ReactNode
  if (!p) lead = <Ph on>{PH.pace}</Ph>
  else if (p.dots.length === 0) lead = t`No pages read in this range yet.`
  else {
    const recent = p.recent.toFixed(1)
    const all = p.all.toFixed(1)
    // The trend is the change between the two figures as printed, so it cannot disagree with
    // them; a short "(up 27 %)" after the recent figure, the comparison spelled out in its tip.
    const ch = p.dots.length > 7 ? paceChange(recent, all) : null
    const pct = Math.abs(ch ?? 0)
    let tip: { 'data-tip': string } | null = null
    if (ch != null) {
      let title: string
      if (ch >= 3) title = t`${pct} % faster lately`
      else if (ch <= -3) title = t`${pct} % slower lately`
      else title = t`About as fast as usual`
      tip = tipAttrs({ title, value: t`${recent} against ${all} pages a minute`, note: t`the last 7 reading days against ${rangeWords}` })
    }
    // eslint-disable-next-line lingui/no-unlocalized-strings -- class names
    const tc = 'rounded-sm text-muted-foreground data-[hot]:text-foreground'
    if (p.dots.length <= 7) lead = t`${all} pages a minute across ${rangeWords}.`
    else if (ch == null) lead = t`${recent} pages a minute over the last 7 reading days, ${all} across ${rangeWords}.`
    else if (ch >= 3) lead = <Trans>{recent} pages a minute over the last 7 reading days <span {...tip} className={tc}>(up {pct} %)</span>, {all} across {rangeWords}.</Trans>
    else if (ch <= -3) lead = <Trans>{recent} pages a minute over the last 7 reading days <span {...tip} className={tc}>(down {pct} %)</span>, {all} across {rangeWords}.</Trans>
    else lead = <Trans>{recent} pages a minute over the last 7 reading days <span {...tip} className={tc}>(steady)</span>, {all} across {rangeWords}.</Trans>
  }
  return (
    <section ref={enter} data-box="pace" className={cn('min-w-0', dim)}>
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
          <Pace W={width} H={H} dots={p?.dots ?? []} nDays={p?.nDays ?? 1} ticks={ticks} loading={!p} />
        )}
      </div>
      <FormatLine stats={stats} desktop={desktop} />
    </section>
  )
}

// ── Reading speed (all time) ──────────────────────────────────────────────────

type WpmBook = StatsResponse['wpm']['books'][number]

function WpmRow({ b, first, style }: { b: WpmBook | null; first: boolean; style?: CSSProperties }) {
  const wpm = b ? num(Math.round(b.wpm)) : ''
  const row = cn('flex items-center gap-3', !first && cn('border-t', HAIR))
  if (!b) {
    return (
      <div className={row} style={{ height: WPM_ROW_H }}>
        <Cover id={null} h={52} className="rounded-[6px]" />
        <div className="min-w-0 flex-1">
          <div className="line-clamp-2 text-[14px] font-semibold leading-[1.25] text-foreground"><Ph on>{PH.book}</Ph></div>
          <div className="mt-[3px] text-[13px] tabular-nums text-muted-foreground"><Ph on>{PH.bookV}</Ph></div>
        </div>
      </div>
    )
  }
  const words = num(b.words)
  const dur = formatDuration(b.seconds)
  // A row is one link to its book; the title takes the accent on hover, as Home's rows do.
  return (
    <Link
      to={`/books/${b.book_id}`}
      {...tipAttrs({ title: b.title, value: t`${wpm} words a minute`, note: t`${words} words in ${dur}` })}
      className={cn(row, 'group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', K.rise)}
      style={{ height: WPM_ROW_H, ...style }}
    >
      <Cover id={b.has_cover ? b.book_id : null} h={52} className="rounded-[6px] transition-transform duration-200 group-hover:-translate-y-0.5" />
      <div className="min-w-0 flex-1">
        <div className="line-clamp-2 text-[14px] font-semibold leading-[1.25] text-foreground transition-colors group-hover:text-primary">{b.title}</div>
        <div className="mt-[3px] text-[13px] tabular-nums text-muted-foreground">{t`${wpm} wpm`}</div>
      </div>
    </Link>
  )
}

function Speed({ stats, desktop, sig }: { stats: StatsResponse | null; desktop: boolean; sig: string | null }) {
  const [e, enter] = useSection('habits/speed', sig)
  const w = stats?.wpm
  const none = !!w && w.books_counted === 0
  const blank = Array.from({ length: WPM_ROWS }, () => null)
  const fast: (WpmBook | null)[] = w ? w.books.slice(0, WPM_ROWS) : blank
  const slow: (WpmBook | null)[] = w ? (w.books.length > 6 ? w.books.slice(-WPM_ROWS).reverse() : []) : blank
  const list = (title: ReactNode, rows: (WpmBook | null)[], key: string, from: number) => (
    <div data-box={`speed-${key}`} className="min-w-0">
      <div className="mb-2.5 h-[18px] text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">{title}</div>
      <div style={{ minHeight: WPM_ROWS * WPM_ROW_H }}>
        {rows.map((b, i) => <WpmRow key={b?.book_id ?? i} b={b} first={i === 0} style={stagger(from + i, 2 * WPM_ROWS)} />)}
      </div>
    </div>
  )
  // Phone stacks the lists; with fewer than seven timed books there is no Slowest list.
  const showSlow = slow.length > 0 || desktop
  return (
    <section ref={enter} data-box="speed" className="min-w-0">
      <Heading note={<Trans>all time</Trans>}><Trans>Reading speed</Trans></Heading>
      <div data-box="speed-fig">
        <div className="font-display text-[40px] font-bold leading-none tabular-nums text-foreground">
          {w ? (none ? '—' : <CountUp entry={e} value={Math.round(w.overall)} format={num} />) : <Ph on>{PH.wpm}</Ph>}
          <span className="ml-1.5 font-sans text-[16px] font-semibold tracking-normal">{w ? <Trans>words a minute</Trans> : <Ph on>{PH.wpmUnit}</Ph>}</span>
        </div>
        <div className="mt-1.5 text-[14px] leading-[1.4] text-muted-foreground">
          {w ? (none ? t`No timed reading with word counts yet.` : plural(w.books_counted, { one: 'across # book', other: 'across # books' })) : <Ph on>{PH.wpmFoot}</Ph>}
        </div>
      </div>
      <div className={cn('mt-6 grid gap-x-6', desktop ? 'grid-cols-2' : 'grid-cols-1 gap-y-5')}>
        {list(fast.length > 0 || !w ? <Trans>Fastest</Trans> : ' ', fast, 'fast', 0)}
        {showSlow && list(slow.length > 0 ? <Trans>Slowest</Trans> : ' ', slow, 'slow', WPM_ROWS)}
      </div>
    </section>
  )
}

// ── The last 12 months (all time) ─────────────────────────────────────────────

function MonthsSection({ stats, width, phone, sig }: { stats: StatsResponse | null; width: number; phone: boolean; sig: string | null }) {
  const [, enter] = useSection('habits/months', sig)
  const months = useMemo(
    () => (stats?.monthly_comparison ?? []).map((m) => ({
      key: m.month,
      label: new Date(m.month + '-01T00:00:00').toLocaleDateString(undefined, { month: 'short' }),
      seconds: m.reading_seconds,
      sessions: m.sessions,
      books: m.books_finished,
    })),
    [stats],
  )
  const empty = !!stats && months.every((m) => m.seconds === 0 && m.books === 0)
  const H = monthsHeight(phone)
  return (
    <section ref={enter} data-box="months" className="min-w-0">
      <Heading><Trans>The last 12 months</Trans></Heading>
      <div data-box="months-chart" style={{ height: H }}>
        {empty ? <ChartEmpty H={H}>{t`No reading in the last 12 months.`}</ChartEmpty> : <Months W={width} months={months} phone={phone} loading={!stats} />}
      </div>
    </section>
  )
}

// ── Reading character (all time) ──────────────────────────────────────────────

// Markers land as the fills finish growing.
const MARKER_D = { '--k-d': '380ms' } as CSSProperties

function Character({ stats, desktop, sig }: { stats: StatsResponse | null; desktop: boolean; sig: string | null }) {
  const [, enter] = useSection('habits/character', sig)
  const dna = stats?.reading_dna
  const loading = !stats
  const ready = !!dna?.ready
  const traits = ready && dna ? dna.traits : Array.from({ length: 5 }, () => null)
  const tips = useTips((i) => {
    const tr = traits[i]
    if (!tr) return null
    const s = Math.round(Math.max(0, Math.min(100, tr.score)))
    const pole = s >= 50 ? tr.high : tr.low
    return { title: `${tr.low} – ${tr.high}`, value: t`${s} of 100`, note: s === 50 ? t`Right in the middle` : t`Closer to ${pole}` }
  })
  return (
    <section ref={enter} data-box="character" className="min-w-0">
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
        <div
          {...(ready ? tips : {})}
          data-box="character-traits"
          className={cn('grid min-w-0', desktop ? 'col-span-8 grid-cols-5 gap-x-4' : 'mt-5 grid-cols-1 gap-y-4')}
          aria-label={ready ? t`Reading character traits` : undefined}
        >
          {traits.map((tr, i) => {
            const s = tr ? Math.max(0, Math.min(100, tr.score)) : 50
            const high = s >= 50
            const fill = Math.max(Math.abs(s - 50), 1.5)
            return (
              <div key={tr?.key ?? i} className="group min-w-0" data-tip-i={tr ? i : undefined}>
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
                <div
                  className="relative h-1.5 rounded-full ring-offset-2 ring-offset-background group-data-[hot]:ring-2 group-data-[hot]:ring-primary/40"
                  style={{ background: 'color-mix(in oklch, var(--primary) 28%, var(--background))', opacity: tr ? 1 : 0.4 }}
                >
                  <i className="absolute -bottom-[3px] -top-[3px] left-1/2 block w-px bg-border" />
                  {tr && (
                    <>
                      {/* The fill grows out from the middle toward its pole. */}
                      <i
                        className={cn('absolute bottom-0 top-0 block rounded-full bg-primary', K.growX)}
                        style={{ ...(high ? { left: '50%', width: `${fill}%` } : { right: '50%', width: `${fill}%`, transformOrigin: '100% 50%' }), ...stagger(i, traits.length) }}
                      />
                      <i className={cn('absolute -bottom-1 -top-1 -ml-px block w-0.5 rounded-[1px] bg-foreground', K.fade)} style={{ left: `${s}%`, ...MARKER_D }} />
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
