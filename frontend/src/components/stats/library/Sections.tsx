// The Library page's lower sections: Backlog, the three small charts, the
// monthly additions and the All books table.
import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'
import { cn, formatDuration } from '@/lib/utils'
import { formatEstimateDays, type BacklogSummary } from '@/lib/backlog'
import type { LibraryResponse, StatsResponse } from '@/components/stats/shared'
import { Cover, Ph } from '@/components/stats/kit/Kit'
import { HAIR, num } from '@/components/stats/kit/tokens'
import { CountUp } from '@/components/stats/kit/CountUp'
import { K, ONCE, stagger, useEntry } from '@/components/stats/kit/motion'
import { tipAttrs } from '@/components/stats/kit/tip'
import { AddedChart, ChartPlaceholder, SmallBars, type SmallBar } from './charts'
import { BOOKS_SHOWN, addedModel, fmtMonth, publishedModel, typeColor, type Range } from './model'
import { Head, Note, RangeChip, Thin } from './parts'

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text: drawn as blocks
   while loading and never read; it only gives each line its loaded width. */
const PH = {
  hours: '0,000', unit: 'hours', span: 'about 2.4 years at your pace', sub: '528 books · you read about 76 minutes a day (last 30 days)',
  rowName: 'Light Novels', rowBooks: '140 books', rowH: '717 hours · about 1.5 years', skip: 'Not estimated yet: Manga (344 books).',
  chartNote: '58 finished', foot1: 'Average 113k words · median 94k', foot2: 'Longest: He Who Fights with Monsters 9 (277k)',
  median: '0', ttfNote: 'median, first day to finish', addedNote: '588 books, Mar 2026 to Nov 2026', type: 'Light Novels',
  title: 'He Who Fights with Monsters 10', author: 'Shirtaloon', dur: '00h 00m', n: '000', tableNote: '10 books read in the last 90 days',
}
/* eslint-enable lingui/no-unlocalized-strings */

/** "about 2.4 years" from the shared estimate wording ("~2.4 years"); "under a day" stays as it is. */
function aboutDays(days: number): string {
  const f = formatEstimateDays(days)
  if (!f.startsWith('~')) return f
  const d = f.slice(1)
  return t`about ${d}`
}

// ── Backlog ───────────────────────────────────────────────────────────────────

const BL_ROW_H = 44
const BL_ROW_H_PHONE = 54

export function Backlog({ data, failed, desktop, rows }: { data: BacklogSummary | null; failed: boolean; desktop: boolean; rows: number }) {
  const est = data ? data.by_type.filter((r) => r.seconds > 0) : null
  const skipped = data ? data.by_type.filter((r) => r.seconds === 0 && r.unestimated > 0) : []
  const list = est ?? Array.from({ length: rows }, () => null)
  const rowH = desktop ? BL_ROW_H : BL_ROW_H_PHONE
  const loading = !data
  const e = useEntry('library/backlog', data ? ONCE : null)
  const total = data?.seconds ?? 0
  const hours = Math.round(total / 3600)
  const bigN = total < 3600 ? Math.max(1, Math.round(total / 60)) : hours
  const big = loading ? PH.hours : <CountUp entry={e} value={bigN} format={num} />
  const unit = loading ? PH.unit : total < 3600 ? plural(Math.max(1, Math.round(total / 60)), { one: 'minute', other: 'minutes' }) : plural(hours, { one: 'hour', other: 'hours' })
  let span: ReactNode = null
  if (loading) span = <Ph on>{PH.span}</Ph>
  else if (data.days != null) {
    const a = aboutDays(data.days)
    span = <Trans><b className="font-semibold text-foreground">{a}</b> at your pace</Trans>
  }
  let sub: ReactNode
  if (loading) sub = <Ph on>{PH.sub}</Ph>
  else {
    const books = plural(data.books, { one: '# book', other: '# books' })
    const mpd = data.pace.minutes_per_day
    const win = data.pace.window_days
    const pace = mpd ? (() => { const m = Math.round(mpd); return t`you read about ${m} minutes a day (last ${win} days)` })() : t`not enough recent reading for a day estimate`
    sub = `${books} · ${pace}`
  }
  const rowHours = (s: number) => {
    if (s < 3600) { const m = Math.max(1, Math.round(s / 60)); return desktop ? plural(m, { one: '# minute', other: '# minutes' }) : t`${m} min` }
    const h = num(Math.round(s / 3600))
    return desktop ? plural(Math.round(s / 3600), { one: '# hour', other: '# hours' }) : t`${h} h`
  }
  const rowHoursLong = (s: number) => {
    if (s < 3600) { const m = Math.max(1, Math.round(s / 60)); return plural(m, { one: '# minute', other: '# minutes' }) }
    return plural(Math.round(s / 3600), { one: '# hour', other: '# hours' })
  }
  const skipLine = skipped.length
    ? (() => { const list = skipped.map((r) => { const b = plural(r.books, { one: '# book', other: '# books' }); return `${r.label} (${b})` }).join(', '); return t`Not estimated yet: ${list}.` })()
    : ' '

  const body = () => {
    if (failed) return <Note h={160}><Trans>Could not load the backlog.</Trans></Note>
    if (data && data.books === 0) return <Note h={160}><Trans>No unread books. Everything in the library is started or read.</Trans></Note>
    if (data && data.estimated === 0) {
      const n = data.books
      return <Note h={160}>{plural(n, { one: '# unread book, none estimated yet.', other: '# unread books, none estimated yet.' })} <Trans>Finish a couple of books of each type so Tome can measure your pace.</Trans></Note>
    }
    return (
      <>
        <div data-box="backlog-figure" className={cn(desktop ? 'flex h-[48px] items-baseline gap-x-3.5' : 'flex h-[76px] flex-col justify-between')}>
          <div className={cn('font-display font-bold tabular-nums leading-none text-foreground', desktop ? 'text-[48px]' : 'text-[40px]')}>
            <Ph on={loading}>{big}</Ph>
            <span className="ml-1.5 font-sans text-[16px] font-semibold tracking-normal"><Ph on={loading}>{unit}</Ph></span>
          </div>
          <div className="h-6 text-[16px] leading-[24px] text-muted-foreground">{span}</div>
        </div>
        <div className={cn('mt-3 text-[14px] leading-[20px] text-muted-foreground', !desktop && 'min-h-[40px]')}>{sub}</div>
        <div data-box="backlog-rows" className="mt-5">
          {list.map((r, i) => {
            const share = r && total > 0 ? r.seconds / total : 0
            const tip = r ? (() => {
              const books = plural(r.books, { one: '# unread book', other: '# unread books' })
              const pct = Math.round(share * 100)
              const left = r.days != null ? aboutDays(r.days) : null
              const note = [books, left ? t`${left} at your pace` : null, r.unestimated > 0 ? plural(r.unestimated, { one: '# not estimated', other: '# not estimated' }) : null, t`${pct}% of the backlog`].filter(Boolean).join(' · ')
              return tipAttrs({ title: r.label, value: rowHoursLong(r.seconds), note })
            })() : undefined
            const bar = { style: stagger(i, list.length), tip }
            const name = (
              <span className="min-w-0 truncate text-[15px] font-semibold text-foreground">
                <Ph on={!r}>{r?.label ?? PH.rowName}</Ph>
                <span className={cn('font-normal text-muted-foreground', desktop ? 'ml-2 text-[14px]' : 'ml-1.5 text-[13px]')}>
                  <Ph on={!r}>{r ? plural(r.books, { one: '# book', other: '# books' }) : PH.rowBooks}</Ph>
                </span>
              </span>
            )
            const value = (
              <span className={cn('whitespace-nowrap text-right text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]')}>
                {r ? (
                  <>
                    <b className="font-semibold text-foreground">{rowHours(r.seconds)}</b>
                    {r.days != null && <> · {aboutDays(r.days)}</>}
                    {r.unestimated > 0 && <> · {plural(r.unestimated, { one: '# not estimated', other: '# not estimated' })}</>}
                  </>
                ) : <Ph on>{PH.rowH}</Ph>}
              </span>
            )
            return desktop ? (
              <div key={r?.label ?? i} data-box={`backlog-row-${i}`} className={cn('grid grid-cols-[220px_minmax(0,1fr)_minmax(200px,auto)] items-center gap-x-6', i > 0 && cn('border-t', HAIR))} style={{ height: rowH }}>
                {name}
                <Thin frac={share} className={!r ? 'opacity-40' : ''} {...bar} />
                {value}
              </div>
            ) : (
              <div key={r?.label ?? i} data-box={`backlog-row-${i}`} className={cn('grid grid-cols-[minmax(0,1fr)_auto] grid-rows-[20px_10px] content-center items-center gap-x-3', i > 0 && cn('border-t', HAIR))} style={{ height: rowH }}>
                {name}
                {value}
                <Thin frac={share} className={cn('col-span-2 self-end', !r && 'opacity-40')} {...bar} />
              </div>
            )
          })}
        </div>
        <p className="mt-3 h-[18px] truncate text-[13px] leading-[18px] text-muted-foreground">{loading ? <Ph on>{PH.skip}</Ph> : skipLine}</p>
      </>
    )
  }

  return (
    <section ref={e.ref} data-box="backlog">
      <Head note={<span className="inline-flex h-[22px] items-center rounded-full bg-secondary px-2.5 text-[12px] font-medium text-muted-foreground"><Trans>All unread</Trans></span>}>
        <Trans>Backlog</Trans>
      </Head>
      {body()}
    </section>
  )
}

// ── Book length, Published, Time to finish ────────────────────────────────────

const CH = 200 // every small chart's height; Time to finish gives 48 px of it to its figure
const FIG_H = 48

function ChartFoot({ children }: { children: ReactNode }) {
  return <div className="mt-3 h-[38px] text-[13px] leading-[19px] text-muted-foreground [&>div]:truncate">{children}</div>
}

export function ChartsRow({ stats, lib, libFailed, desktop, width }: { stats: StatsResponse | null; lib: LibraryResponse | null; libFailed: boolean; desktop: boolean; width: number }) {
  const W = desktop ? Math.floor((width - 2 * 16) / 3) : width
  const parts = [
    <BookLength key="len" stats={stats} W={W} />,
    <Published key="pub" lib={lib} failed={libFailed} W={W} />,
    <TimeToFinish key="ttf" lib={lib} failed={libFailed} W={W} />,
  ]
  return desktop ? <div className="grid grid-cols-3 gap-4">{parts}</div> : <>{parts}</>
}

function BookLength({ stats, W }: { stats: StatsResponse | null; W: number }) {
  const L = stats?.book_lengths
  const e = useEntry('library/length', L ? ONCE : null)
  const k = (n: number) => { const v = Math.round(n / 1000); return t`${v}k` }
  const items: SmallBar[] = (L?.buckets ?? []).map((b) => {
    const label = b.label
    const books = plural(b.count, { one: '# finished book', other: '# finished books' })
    const pct = L && L.count > 0 ? Math.round((b.count / L.count) * 100) : 0
    return { label, value: b.count, tip: { title: t`${label} words`, value: books, note: t`${pct}% of the finished books with a word count` } }
  })
  let foot: ReactNode = <><div><Ph on>{PH.foot1}</Ph></div><div><Ph on>{PH.foot2}</Ph></div></>
  if (L) {
    if (L.count === 0) foot = <div><Trans>Counts finished books that have a word count.</Trans></div>
    else {
      const avg = k(L.avg_words), med = k(L.median_words)
      foot = (
        <>
          <div><Trans>Average <b className="font-semibold text-foreground">{avg}</b> words · median {med}</Trans></div>
          {L.longest && (() => { const title = L.longest.title, w = k(L.longest.words); return <div title={title}><Trans>Longest: {title} ({w})</Trans></div> })()}
        </>
      )
    }
  }
  return (
    <section ref={e.ref} data-box="length" className="min-w-0">
      <Head note={L ? plural(L.count, { one: '# finished', other: '# finished' }) : <Ph on>{PH.chartNote}</Ph>}><Trans>Book length</Trans></Head>
      <div data-box="length-chart" style={{ height: CH }}>
        {!L ? <ChartPlaceholder W={W} H={CH} y={18} h={CH - 18 - 22} />
          : L.count === 0 ? <Note h={CH} center><Trans>No finished books with a word count yet.</Trans></Note>
          : <SmallBars W={W} H={CH} items={items} label={t`Finished books by length in words`} />}
      </div>
      <ChartFoot>{foot}</ChartFoot>
    </section>
  )
}

function Published({ lib, failed, W }: { lib: LibraryResponse | null; failed: boolean; W: number }) {
  const P = lib?.published
  const m = useMemo(() => (P ? publishedModel(P) : null), [P])
  const e = useEntry('library/published', m ? ONCE : null)
  const items: SmallBar[] = (m?.periods ?? []).map((p) => {
    const a = p.start, b = p.start + p.span - 1
    const label = p.span === 5 ? t`${a} to ${b}` : t`${a}s`
    const books = plural(p.count, { one: '# finished book', other: '# finished books' })
    const pct = m && m.withYear > 0 ? Math.round((p.count / m.withYear) * 100) : 0
    return { label, value: p.count, tip: { title: t`Published ${a} to ${b}`, value: books, note: t`${pct}% of the finished books with a year` } }
  })
  let foot: ReactNode = <><div><Ph on>{PH.foot1}</Ph></div><div><Ph on>{PH.foot2}</Ph></div></>
  if (P && m) {
    const per = m.span === 5 ? t`By five-year period of publication.` : t`By decade of publication.`
    const noYear = P.no_year > 0 ? plural(P.no_year, { one: '# finished book has no year.', other: '# finished books have no year.' }) : m.withYear > 0 ? t`Every finished book has a year.` : ' '
    foot = <><div>{m.withYear > 0 ? per : ' '}</div><div>{noYear}</div></>
  }
  return (
    <section ref={e.ref} data-box="published" className="min-w-0">
      <Head note={m ? plural(m.withYear, { one: '# finished', other: '# finished' }) : <Ph on>{PH.chartNote}</Ph>}><Trans>Published</Trans></Head>
      <div data-box="published-chart" style={{ height: CH }}>
        {failed ? <Note h={CH} center><Trans>Could not load this chart.</Trans></Note>
          : !m ? <ChartPlaceholder W={W} H={CH} y={18} h={CH - 18 - 22} />
          : m.periods.length === 0 ? <Note h={CH} center><Trans>No finished books with a publication year yet.</Trans></Note>
          : <SmallBars W={W} H={CH} items={items} label={m.span === 5 ? t`Finished books by five-year period of publication` : t`Finished books by decade of publication`} />}
      </div>
      <ChartFoot>{foot}</ChartFoot>
    </section>
  )
}

function TimeToFinish({ lib, failed, W }: { lib: LibraryResponse | null; failed: boolean; W: number }) {
  const T = lib?.time_to_finish
  const e = useEntry('library/ttf', T ? ONCE : null)
  const H = CH - FIG_H
  const LABELS: Record<string, [string, string]> = {
    '1_day': [t`1 day`, t`1 day`],
    '2_to_3': [t`2–3`, t`2 to 3 days`],
    '4_to_7': [t`4–7`, t`4 to 7 days`],
    '1_to_2_weeks': [t`1–2 wk`, t`1 to 2 weeks`],
    longer: [t`longer`, t`Longer than 2 weeks`],
  }
  const items: SmallBar[] = (T?.buckets ?? []).map((b) => {
    const [label, long] = LABELS[b.bucket] ?? [b.bucket, b.bucket]
    const books = plural(b.count, { one: '# finished book', other: '# finished books' })
    const pct = T && T.books > 0 ? Math.round((b.count / T.books) * 100) : 0
    return { label, value: b.count, tip: { title: long, value: books, note: t`From the first reading day to the finish · ${pct}%` } }
  })
  let foot: ReactNode = <><div><Ph on>{PH.foot1}</Ph></div><div><Ph on>{PH.foot2}</Ph></div></>
  if (T) {
    if (T.books === 0) foot = <div><Trans>Counts finished books that have reading days.</Trans></div>
    else {
      const q = T.quickest, s = T.slowest
      const qd = q ? plural(q.days, { one: '# day', other: '# days' }) : ''
      const qn = q ? plural(q.count, { one: '# book', other: '# books' }) : ''
      const sd = s ? plural(s.days, { one: '# day', other: '# days' }) : ''
      const title = s?.title ?? ''
      foot = (
        <>
          <div>{q && s ? <Trans>Quickest <b className="font-semibold text-foreground">{qd}</b> ({qn}), slowest <b className="font-semibold text-foreground">{sd}</b></Trans> : ' '}</div>
          <div title={title}>{s ? <Trans>Slowest: {title}</Trans> : ' '}</div>
        </>
      )
    }
  }
  const median = T?.median_days
  return (
    <section ref={e.ref} data-box="ttf" className="min-w-0">
      <Head note={T ? plural(T.books, { one: '# with reading days', other: '# with reading days' }) : <Ph on>{PH.chartNote}</Ph>}><Trans>Time to finish</Trans></Head>
      <div data-box="ttf-figure" className="mb-2 flex h-10 items-baseline gap-2">
        <span className="font-display text-[32px] font-bold leading-none tabular-nums text-foreground">
          {T ? median != null ? <CountUp entry={e} value={median} format={num} /> : '—' : <Ph on>{PH.median}</Ph>}
          {(!T || median != null) && <span className="ml-1.5 font-sans text-[16px] font-semibold tracking-normal"><Ph on={!T}>{median != null ? plural(median, { one: 'day', other: 'days' }) : t`days`}</Ph></span>}
        </span>
        <span className="truncate text-[14px] text-muted-foreground"><Ph on={!T}>{T ? t`median, first day to finish` : PH.ttfNote}</Ph></span>
      </div>
      <div data-box="ttf-chart" style={{ height: H }}>
        {failed ? <Note h={H} center><Trans>Could not load this chart.</Trans></Note>
          : !T ? <ChartPlaceholder W={W} H={H} y={18} h={H - 18 - 22} />
          : T.books === 0 ? <Note h={H} center><Trans>No finished books with reading days yet.</Trans></Note>
          : <SmallBars W={W} H={H} items={items} label={t`Days from the first reading day to the finish`} />}
      </div>
      <ChartFoot>{foot}</ChartFoot>
    </section>
  )
}

// ── Added to the library ──────────────────────────────────────────────────────

export function AddedSection({ stats, desktop, width }: { stats: StatsResponse | null; desktop: boolean; width: number }) {
  const m = useMemo(() => (stats ? addedModel(stats.library_growth) : null), [stats])
  const e = useEntry('library/added', m ? ONCE : null)
  const H = desktop ? 240 : 200
  let note: ReactNode = <Ph on>{PH.addedNote}</Ph>
  if (m) {
    if (!m.months.length) note = ' '
    else {
      const books = plural(m.added, { one: '# book', other: '# books' })
      const a = fmtMonth(m.months[0].month), b = fmtMonth(m.months[m.months.length - 1].month)
      note = m.months.length === 1 ? t`${books}, ${a}` : t`${books}, ${a} to ${b}`
    }
  }
  const types = m?.types ?? []
  return (
    <section ref={e.ref} data-box="added">
      <Head note={note} stack={!desktop}><Trans>Added to the library</Trans></Head>
      <div data-box="added-chart" style={{ height: H }}>
        {!m ? <ChartPlaceholder W={width} H={H} y={26} h={H - 26 - 24} />
          : m.months.length === 0 ? <Note h={H} center><Trans>No books added in the last two years.</Trans></Note>
          : <AddedChart W={width} H={H} m={m} />}
      </div>
      <div data-box="added-legend" className="mt-3 flex min-h-[18px] flex-wrap gap-x-[18px] gap-y-1.5 text-[12px] leading-[18px] text-muted-foreground">
        {m ? types.map((ty, k) => (
          <span key={ty}><i className="mr-1.5 inline-block h-[11px] w-[11px] rounded-[3px] align-[-1px]" style={{ background: typeColor(k) }} />{ty}</span>
        )) : <Ph on>{PH.type}</Ph>}
      </div>
    </section>
  )
}

// ── All books by reading time ────────────────────────────────────────────────

type SortKey = 'seconds' | 'sessions' | 'pages_turned'
const BOOK_COLS = 'grid-cols-[29px_minmax(0,1fr)_minmax(0,24%)_110px_110px_100px] gap-x-4'
const BOOK_COLS_PHONE = 'grid-cols-[29px_minmax(0,1fr)_78px] gap-x-3'
const TH = 'whitespace-nowrap text-[12px] font-bold uppercase tracking-[0.1em]'

export function AllBooks({ stats, desktop, rows, range, dim }: { stats: StatsResponse | null; desktop: boolean; rows: number; range: Range; dim: string }) {
  const rangePhrase = range.phrase
  const e = useEntry('library/books', range.sig)
  const [key, setKey] = useState<SortKey>('seconds')
  const [asc, setAsc] = useState(false)
  const [all, setAll] = useState(false)
  const data = stats?.per_book_time ?? null
  const sorted = useMemo(() => (data ? [...data].sort((a, b) => (asc ? a[key] - b[key] : b[key] - a[key]) || a.title.localeCompare(b.title)) : null), [data, key, asc])
  const list = sorted ? (all ? sorted : sorted.slice(0, BOOKS_SHOWN)) : Array.from({ length: rows }, () => null)
  const sort = (k: SortKey) => {
    if (k === key) setAsc(!asc)
    else { setKey(k); setAsc(false) }
  }
  const cols = desktop ? BOOK_COLS : BOOK_COLS_PHONE
  const th = TH
  const sortHead = (k: SortKey, children: ReactNode) => {
    const on = key === k
    const Icon = on ? (asc ? ArrowUp : ArrowDown) : ArrowUpDown
    return (
      <button type="button" onClick={() => sort(k)} className={cn(th, 'inline-flex items-center justify-end gap-1 text-right', on ? 'text-foreground' : 'text-muted-foreground hover:text-foreground')} aria-sort={on ? (asc ? 'ascending' : 'descending') : 'none'}>
        {children}
        <Icon className={cn('h-[13px] w-[13px]', !on && 'opacity-60')} strokeWidth={on ? 2.25 : 2} aria-hidden="true" />
      </button>
    )
  }
  const n = data?.length ?? 0
  const note = stats ? <span className="inline-flex items-center gap-2"><span>{plural(n, { one: '# book read', other: '# books read' })}</span><RangeChip>{range.label}</RangeChip></span> : <Ph on>{PH.tableNote}</Ph>
  const rowH = desktop ? 60 : 64

  return (
    <section ref={e.ref} id="stats-all-books" data-box="books" className={cn('scroll-mt-6', dim)}>
      <Head note={note} stack={!desktop}><Trans>All books by reading time</Trans></Head>
      {stats && n === 0 ? (
        <Note h={rowH}>{t`No reading in ${rangePhrase}.`}</Note>
      ) : (
        <div role="table" aria-label={t`All books by reading time`}>
          <div role="row" className={cn('grid h-[28px] items-center pb-1.5', cols)}>
            <span />
            <span role="columnheader" className={cn(th, 'text-muted-foreground')}><Trans>Title</Trans></span>
            {desktop && <span role="columnheader" className={cn(th, 'text-muted-foreground')}><Trans>Author</Trans></span>}
            <span role="columnheader" className="text-right">{sortHead('seconds', <Trans>Time</Trans>)}</span>
            {desktop && <span role="columnheader" className="text-right">{sortHead('sessions', <Trans>Sessions</Trans>)}</span>}
            {desktop && <span role="columnheader" className="text-right">{sortHead('pages_turned', <Trans>Pages</Trans>)}</span>}
          </div>
          {list.map((b, i) => {
            const row = cn('grid items-center border-t text-[14px]', HAIR, cols)
            const style = { height: rowH }
            const box = i < BOOKS_SHOWN ? `book-${i}` : undefined
            const cells = (
              <>
                <span role="cell">
                  <Cover id={b ? (b.has_cover ? b.book_id : null) : null} h={44} className={cn('rounded-[6px]', b && cn(K.fade, 'transition-transform duration-200 group-hover:-translate-y-0.5'))} />
                </span>
                <span role="cell" className="min-w-0">
                  <span className={cn('block truncate font-semibold text-foreground transition-colors group-hover:text-primary', desktop ? 'text-[15px]' : 'text-[14px] leading-[19px]')}>
                    {b ? b.title : <Ph on>{PH.title}</Ph>}
                  </span>
                  {!desktop && <span className="mt-0.5 block truncate text-[13px] text-muted-foreground"><Ph on={!b}>{b ? b.author ?? ' ' : PH.author}</Ph></span>}
                </span>
                {desktop && <span role="cell" className="truncate text-muted-foreground"><Ph on={!b}>{b ? b.author ?? '—' : PH.author}</Ph></span>}
                <span role="cell" className="whitespace-nowrap text-right text-foreground"><Ph on={!b}>{b ? formatDuration(b.seconds) : PH.dur}</Ph></span>
                {desktop && <span role="cell" className="text-right tabular-nums text-foreground"><Ph on={!b}>{b ? num(b.sessions) : PH.n}</Ph></span>}
                {desktop && <span role="cell" className="text-right tabular-nums text-foreground"><Ph on={!b}>{b ? num(b.pages_turned) : PH.n}</Ph></span>}
              </>
            )
            // A row is one link to its book: the row washes and the title takes the accent, as the old table did.
            return b ? (
              <Link key={b.book_id} to={`/books/${b.book_id}`} role="row" title={b.title} data-box={box} className={cn(row, 'group transition-colors hover:bg-accent/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring')} style={style}>{cells}</Link>
            ) : (
              <div key={i} role="row" data-box={box} className={row} style={style}>{cells}</div>
            )
          })}
        </div>
      )}
      {n > BOOKS_SHOWN && (
        <div className="mt-3">
          <button type="button" onClick={() => setAll(!all)} className="text-[15px] font-medium text-primary hover:underline underline-offset-4">
            {all ? <Trans>Show fewer</Trans> : (() => { const total = num(n); return <Trans>Show all {total}</Trans> })()}
          </button>
        </div>
      )}
    </section>
  )
}
