// The designed Library page: what the library holds and how much of it is read.
// One fixed layout; every section's size follows from fixed heights and row
// counts (the rows of the last answer, remembered per browser), so the skeleton
// draws each box where the loaded page has it. All-time sections stay as they
// are on a range change; the range-bound ones (Authors, By type's time column,
// the All books table) dim in place until the new answer lands.
//
// Range: the page is all time (the frame's eyebrow says "All time" on this tab);
// the three places that follow the range control carry a RangeChip with the
// range: Authors, By type's time column and the All books table.
//
// Motion and hover (stage 3): every section has its own entry (kit/motion.ts):
// all-time sections play once per session (ONCE), range-bound ones again when
// the range changes. Figures count up, tracks and bars grow, covers rise; every
// track and bar has a tip (kit/tip.ts); series, authors and books are links.
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { BookLink, SeriesLink } from '@/components/motion'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { cn, formatDuration } from '@/lib/utils'
import type { BacklogScope, BacklogSummary } from '@/lib/backlog'
import type { LibraryResponse, StatsResponse } from '@/components/stats/shared'
import { fmtDay as fmtShortDay } from '@/components/stats/range'
import type { DesignedPageProps } from '@/components/stats/kit/page'
import { Cover, Pending, Ph, Track } from '@/components/stats/kit/Kit'
import { CountUp } from '@/components/stats/kit/CountUp'
import { K, ONCE, rangeSig, stagger, useEntry } from '@/components/stats/kit/motion'
import { useTween } from '@/components/stats/kit/tween'
import { tipAttrs } from '@/components/stats/kit/tip'
import { DESKTOP_MIN, DIM, HAIR, LEVELS, UNDIM, num, useWidth } from '@/components/stats/kit/tokens'
import { useStatsGet } from '@/components/stats/kit/useStatsGet'
import { AUTHORS_MAX, AUTHORS_MAX_PHONE, BACKLOG_DEFAULT_ROWS, BOOKS_SHOWN, DEFAULT_SCOPE, SERIES_MAX, fmtMonth, langModel, libraryStart, rememberScope, rememberShape, rememberedScope, rememberedShape, seriesModel, shapeKnown, typeRows, type Range, type Series, type Shape, type TypeRow } from './model'
import { Head, Note, RangeChip, Thin } from './parts'
import { AddedSection, AllBooks, Backlog, ChartsRow } from './Sections'
import { Languages } from './Languages'

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text: drawn as blocks
   while loading and never read; it only gives each line its loaded width. */
const PH = {
  fig: '000', figFoot: '10% of your library', done2: '97% finished', since: 'since Mar 2026', unit: 'books',
  seriesNote: '3 in progress · 3 complete', series: 'He Who Fights with Monsters', seriesSub: '10 of 12 read', pct: '00%', next: 'Next: Volume 12 · on your shelf', done: '3 complete',
  type: 'Light Novels', v: '000', q: '00 of 00 started', dur: '000h 00m',
  author: 'Christopher Buehlman', authorSub: '9 books read · 10 finished all time', rangeNote: 'by reading time, the last 90 days',
}
/* eslint-enable lingui/no-unlocalized-strings */

export function LibraryPage({ stats, pending, days, custom }: DesignedPageProps) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const desktop = width >= DESKTOP_MIN
  const tz = new Date().getTimezoneOffset()
  // All time, so the same URL on every range: these never dim.
  const lib = useStatsGet<LibraryResponse>(`/stats/library?tz_offset=${tz}`)
  // Backlog scope (#32): the choice is remembered per browser; a change keeps the
  // previous answer on screen, dimmed (useStatsGet's pending), until the new one lands.
  const [scope, setScope] = useState(rememberedScope)
  const scopes = useStatsGet<BacklogScope[]>('/stats/backlog-scopes')
  // A remembered library or shelf that is gone (deleted, no longer visible): back to all unread.
  const scopeId = scopes.data && !scopes.data.some((s) => s.id === scope.id) ? DEFAULT_SCOPE : scope.id
  const backlog = useStatsGet<BacklogSummary>(`/stats/backlog?scope=${encodeURIComponent(scopeId)}&tz_offset=${tz}`)
  const pickScope = (id: string, label: string | null) => { setScope({ id, label }); rememberScope(id, label) }
  const shape = useMemo(rememberedShape, [])
  // A first visit (no shape remembered in this browser): the sections sized by the
  // stats answer are not drawn as a skeleton, so the page draws its figures and the
  // rest when that answer lands; the backlog (its own answer) and what follows it
  // wait for the backlog's answer when this scope's rows are not known. Nothing that
  // is drawn moves. With the shape remembered, the whole skeleton is drawn.
  const [known] = useState(shapeKnown)
  const holdStats = !known && !stats
  const holdBacklog = holdStats || (shape.backlogs[scopeId] === undefined && backlog.data == null && !backlog.failed)

  useEffect(() => {
    if (!stats) return
    const { open, done } = seriesModel(stats.series_completion)
    rememberShape({
      series: Math.min(SERIES_MAX, open.length), done: done.length > 0, types: stats.tbr.by_type.length,
      authors: Math.min(AUTHORS_MAX, stats.author_affinity.length), books: Math.min(BOOKS_SHOWN, stats.per_book_time.length),
      langs: langModel(stats.language)?.rows.length ?? 0,
      booksMore: stats.per_book_time.length > BOOKS_SHOWN,
    })
  }, [stats])
  // The rows each scope answered with (-1: a message instead of rows), for the skeleton of the next visit.
  useEffect(() => {
    if (backlog.failed) rememberShape({ backlogs: { [scopeId]: -1 } })
    else if (backlog.data && !backlog.pending) {
      const d = backlog.data
      rememberShape({ backlogs: { [scopeId]: d.books === 0 || d.estimated === 0 ? -1 : d.by_type.filter((r) => r.seconds > 0).length } })
    }
  }, [backlog.data, backlog.pending, backlog.failed, scopeId])

  const from = custom ? fmtShortDay(custom.start) : ''
  const to = custom ? fmtShortDay(custom.end) : ''
  const rangePhrase = custom ? t`${from} to ${to}` : days === 0 ? t`all time` : days === 365 ? t`the last 12 months` : t`the last ${days} days`
  const rangeLabel = custom ? t`${from} to ${to}` : days === 0 ? t`All time` : days === 365 ? t`Last 12 months` : t`Last ${days} days`
  const range: Range = { phrase: rangePhrase, label: rangeLabel, sig: stats ? rangeSig(days, custom) : null }
  const dim = pending ? DIM : UNDIM
  const busy = pending || !stats || (lib.data == null && !lib.failed) || (backlog.data == null && !backlog.failed)

  const common = { stats, desktop, width, shape }
  return (
    <div ref={ref} style={LEVELS} className={cn('flex flex-col', desktop ? 'gap-[52px]' : 'gap-10')} aria-busy={busy}>
      {width > 0 && (
        <>
          <Figures {...common} />
          {holdStats && <Pending />}
          {!holdStats && <>
          <SeriesSection {...common} />
          {desktop ? (
            <>
              <ByType {...common} range={range} dim={dim} />
              <Languages stats={stats} desktop={desktop} rows={shape.langs} />
              <Authors {...common} range={range} dim={dim} />
            </>
          ) : (
            <>
              <Authors {...common} range={range} dim={dim} />
              <ByType {...common} range={range} dim={dim} />
              <Languages stats={stats} desktop={desktop} rows={shape.langs} />
            </>
          )}
          </>}
          {!holdStats && holdBacklog && <Pending />}
          {!holdBacklog && <>
          <Backlog
            data={backlog.data} failed={backlog.failed} pending={backlog.pending} desktop={desktop}
            rows={shape.backlogs[scopeId] ?? BACKLOG_DEFAULT_ROWS}
            scope={scopeId} scopeLabel={scope.id === scopeId ? scope.label : null} scopes={scopes.data} onScope={pickScope}
          />
          <ChartsRow stats={stats} lib={lib.data} libFailed={lib.failed} desktop={desktop} width={width} />
          <AddedSection stats={stats} desktop={desktop} width={width} />
          <AllBooks stats={stats} desktop={desktop} rows={shape.books} more={shape.booksMore} range={range} dim={dim} />
          </>}
        </>
      )}
    </div>
  )
}

type Common = { stats: StatsResponse | null; desktop: boolean; width: number; shape: Shape }

// ── Your library: four figures ────────────────────────────────────────────────

function Figures({ stats, desktop }: Common) {
  const tb = stats?.tbr
  const e = useEntry('library/figures', tb ? ONCE : null)
  const start = stats ? libraryStart(stats.library_growth) : null
  const since = start ? fmtMonth(start) : null
  const started = stats?.completion_rate.started ?? 0
  const labels: ReactNode[] = [<Trans key="l">Library</Trans>, <Trans key="r">Read</Trans>, <Trans key="p">In progress</Trans>, <Trans key="w">Want to read</Trans>]
  const figs: { value: number; foot: string; foot2?: string }[] = tb
    ? [
        { value: tb.owned, foot: since ? t`since ${since}` : ' ' },
        { value: tb.read, foot: (() => { const pct = Math.round(tb.pct); return t`${pct}% of your library` })() },
        // The server's completion rate: of the books marked read or reading, the share
        // marked read. One line on desktop; the phone's narrow cell takes it on a second.
        (() => {
          if (started === 0) return { value: tb.reading, foot: t`nothing started yet`, foot2: desktop ? undefined : '\u00a0' }
          const of = plural(started, { one: 'of # book started', other: 'of # books started' })
          const pct = Math.round(stats.completion_rate.pct)
          const done = t`${pct}% finished`
          return desktop ? { value: tb.reading, foot: `${of} · ${done}` } : { value: tb.reading, foot: of, foot2: done }
        })(),
        { value: tb.want_to_read, foot: (() => { const n = num(tb.unread); return t`${n} unread in all` })() },
      ]
    : labels.map(() => ({ value: 0, foot: PH.figFoot }))
  return (
    <section ref={e.ref} data-box="figures">
      <Head><Trans>Your library</Trans></Head>
      <div className={cn('grid', desktop ? 'grid-cols-4' : 'grid-cols-2')}>
        {figs.map((f, i) => (
          <div
            key={i}
            data-box={`fig-${i}`}
            className={cn(
              'min-w-0',
              desktop ? cn('px-6 py-0.5', i === 0 && 'pl-0', i === 3 && 'pr-0', i > 0 && cn('border-l', HAIR)) : cn('py-4', i % 2 === 0 ? 'pr-3' : cn('border-l pl-4', HAIR), i < 2 ? cn('border-b pt-0', HAIR) : 'pb-0'),
            )}
          >
            <div className={cn('text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]')}>{labels[i]}</div>
            <div className={cn('mt-1.5 font-display font-bold tabular-nums leading-none text-foreground', desktop ? 'text-[40px]' : 'text-[32px]')}>
              {tb ? <CountUp entry={e} value={f.value} format={num} /> : <Ph on>{PH.fig}</Ph>}
              {i === 0 && <span className="ml-1.5 font-sans text-[16px] font-semibold tracking-normal"><Ph on={!tb}>{tb ? plural(tb.owned, { one: 'book', other: 'books' }) : PH.unit}</Ph></span>}
            </div>
            <div className={cn('mt-2 truncate leading-[1.4] text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]')}>
              <Ph on={!tb}>{tb ? f.foot : i === 0 ? PH.since : PH.figFoot}</Ph>
            </div>
            {!desktop && i === 2 && (
              <div className="truncate text-[13px] leading-[1.4] text-muted-foreground"><Ph on={!tb}>{tb ? f.foot2 ?? '\u00a0' : PH.done2}</Ph></div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

// ── Series: cards in rows of three, the completed ones as a row of covers ─────

const CARD_H = 96
const CARD_H_PHONE = 100
const DONE_COVER_H = 48
const DONE_COVER_W = 32 // 2:3 at 48 high
const DONE_GAP = 8

function SeriesSection({ stats, desktop, width, shape }: Common) {
  const model = useMemo(() => (stats ? seriesModel(stats.series_completion) : null), [stats])
  const e = useEntry('library/series', model ? ONCE : null)
  const open: (Series | null)[] = model ? model.open.slice(0, SERIES_MAX) : Array.from({ length: shape.series }, () => null)
  const done = model ? model.done : null
  const showDone = model ? model.done.length > 0 : shape.done
  const nOpen = model?.open.length ?? 0
  const nDone = model?.done.length ?? 0
  const note = model
    ? nOpen + nDone === 0 ? ' ' : (() => { const a = plural(nOpen, { one: '# in progress', other: '# in progress' }); const b = plural(nDone, { one: '# complete', other: '# complete' }); return `${a} · ${b}` })()
    : <Ph on>{PH.seriesNote}</Ph>
  const cardH = desktop ? CARD_H : CARD_H_PHONE
  const coverH = desktop ? 96 : 88

  const card = (s: Series | null, i: number) => {
    const sub = s ? (() => {
      const r = num(s.read), tot = num(s.total)
      const base = t`${r} of ${tot} read`
      if (!s.reading) return base
      const rd = num(s.reading)
      return t`${base} · ${rd} in progress`
    })() : PH.seriesSub
    const nextIdx = s?.next_index != null ? num(s.next_index) : null
    const next = s
      ? nextIdx == null ? ' ' : (
          <>
            {s.next_book_id != null ? (
              <BookLink book={{ id: s.next_book_id, title: s.series, series: s.series, series_index: s.next_index }} cover={null} className="pointer-events-auto relative font-medium text-foreground underline-offset-4 hover:text-primary hover:underline"><Trans>Next: Volume {nextIdx}</Trans></BookLink>
            ) : (
              <span className="font-medium text-foreground"><Trans>Next: Volume {nextIdx}</Trans></span>
            )}
            {' · '}
            {s.next_book_id != null ? <Trans>on your shelf</Trans> : <Trans>not in your library</Trans>}
          </>
        )
      : <Ph on>{PH.next}</Ph>
    const trackTip = s ? (() => {
      const r = num(s.read), tot = num(s.total)
      const left = s.total - s.read
      const note = s.reading ? (() => { const rd = num(s.reading); return t`${rd} in progress` })() : plural(left, { one: '# volume to go', other: '# volumes to go' })
      return tipAttrs({ title: s.series, value: t`${r} of ${tot} read`, note })
    })() : undefined
    // The card is one link to the series (a link stretched under the content);
    // the content lets the pointer through, except the track (its tip) and the
    // next volume's own link.
    return (
      <div key={s?.series ?? i} data-box={`series-card-${i}`} className={cn('relative flex min-w-0 items-center gap-4', s && 'group')} style={{ height: cardH }}>
        {s && <SeriesLink name={s.series} aria-label={s.series} className="absolute inset-0 rounded-[8px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4 focus-visible:ring-offset-background" />}
        <Cover id={s ? s.sample_book_id : null} series={s?.series} h={coverH} className={cn('pointer-events-none', s && 'transition-transform duration-200 group-hover:-translate-y-0.5')} />
        <div className="pointer-events-none relative min-w-0 flex-1">
          <div className={cn('truncate font-semibold leading-[20px] text-foreground transition-colors group-hover:text-primary', desktop ? 'text-[16px]' : 'text-[15px]')}>
            {s ? s.series : <Ph on>{PH.series}</Ph>}
          </div>
          <div className={cn('mt-0.5 truncate leading-[19px] text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]')}><Ph on={!s}>{sub}</Ph></div>
          <div className="mt-2 flex h-5 items-center gap-3">
            <Track pct={s?.pct ?? 0} className={cn('pointer-events-auto', !s && 'opacity-40')} style={stagger(i, open.length)} tip={trackTip} />
            <span className="text-[15px] font-bold tabular-nums text-foreground">{s ? <><CountUp entry={e} value={Math.round(s.pct)} format={String} align="end" />%</> : <Ph on>{PH.pct}</Ph>}</span>
          </div>
          <div className="mt-[7px] truncate text-[13px] leading-[17px] text-muted-foreground">{next}</div>
        </div>
      </div>
    )
  }

  // The completed row: as many covers as fit on one line, "+N" for the rest.
  const doneLabel = done ? plural(done.length, { one: '# complete', other: '# complete' }) : PH.done
  const fit = Math.max(1, Math.floor((width - 120 + DONE_GAP) / (DONE_COVER_W + DONE_GAP)))
  const doneList = done ?? Array.from({ length: 3 }, () => null)
  const shownDone = desktop && doneList.length > fit ? doneList.slice(0, fit - 1) : doneList
  const doneCount = shownDone.length + (doneList.length > shownDone.length ? 1 : 0)
  const covers = (
    <div className={cn('flex gap-2', !desktop && 'w-max pr-5')}>
      {shownDone.map((s, i) => {
        if (!s) return <Cover key={i} id={null} h={DONE_COVER_H} className="rounded-[6px]" />
        const tot = num(s.total)
        const name = s.series
        const read = t`${tot} of ${tot} read`
        return (
          <SeriesLink
            key={s.series}
            name={s.series}
            aria-label={t`${name}, ${read}`}
            {...tipAttrs({ title: name, value: read, note: t`Complete` })}
            className={cn('group block rounded-[6px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', K.rise)}
            style={stagger(i, doneCount)}
          >
            <Cover id={s.sample_book_id} series={s.series} h={DONE_COVER_H} className="rounded-[6px]" lift />
          </SeriesLink>
        )
      })}
      {doneList.length > shownDone.length && (() => {
        const rest = doneList.slice(shownDone.length).flatMap((s) => (s ? [s.series] : []))
        const more = plural(rest.length, { one: '# more complete series', other: '# more complete series' })
        return (
          <div
            {...tipAttrs({ title: more, note: rest.join(', ') })}
            className={cn('grid place-items-center rounded-[6px] bg-secondary text-[13px] font-semibold text-muted-foreground', K.rise)}
            style={{ width: DONE_COVER_W, height: DONE_COVER_H, ...stagger(shownDone.length, doneCount) }}
          >
            +{rest.length}
          </div>
        )
      })()}
    </div>
  )

  return (
    <section ref={e.ref} data-box="series">
      <Head note={note}><Trans>Series</Trans></Head>
      {model && model.open.length === 0 && model.done.length === 0 ? (
        <Note h={cardH}><Trans>No series started yet. Series you read show up here.</Trans></Note>
      ) : (
        <>
          {open.length > 0 && (
            <div data-box="series-cards" className={cn('k-bars', desktop ? 'grid grid-cols-3 gap-x-4 gap-y-6' : 'flex flex-col gap-5')}>
              {open.map(card)}
            </div>
          )}
          {/* The skeleton keeps the line's place too when the last answer had no series in progress. */}
          {open.length === 0 && <Note h={cardH}>{model && <Trans>Every series you started is complete.</Trans>}</Note>}
          {showDone && (
            <div data-box="series-done" className={cn('mt-5 border-t pt-4', HAIR, desktop ? 'flex items-center gap-4' : 'block')}>
              <span className={cn('whitespace-nowrap text-[14px] text-muted-foreground', !desktop && 'mb-2.5 block')}><Ph on={!done}>{doneLabel}</Ph></span>
              {desktop ? covers : <div className="-mx-5 overflow-x-auto px-5 [scrollbar-width:none]">{covers}</div>}
            </div>
          )}
        </>
      )}
    </section>
  )
}

// ── By type: owned, read, finish rate, time in range ─────────────────────────

const TYPE_ROW_H = 54
/** A type's reading time; a few seconds would read "0m". */
const dur = (s: number) => (s < 60 ? t`under 1m` : formatDuration(s))
const TYPE_COLS = 'grid grid-cols-[minmax(0,18fr)_minmax(0,10fr)_minmax(0,26fr)_minmax(0,20fr)_minmax(0,26fr)] gap-x-4'
const TH = 'whitespace-nowrap text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground'

function ByType({ stats, desktop, shape, range, dim }: Common & { range: Range; dim: string }) {
  const rangePhrase = range.phrase
  const rows = useMemo(() => (stats ? typeRows(stats) : null), [stats])
  const list = rows ?? Array.from({ length: shape.types }, () => null)
  // Mostly all time: the section plays once; the time column follows the range without replaying the rest.
  const e = useEntry('library/types', rows ? ONCE : null)
  const def = desktop
    ? t`Read: finished books out of all you own. Finish rate: finished out of those you started. Time: reading time in ${rangePhrase}.`
    : t`Read: finished books out of all you own. Finish rate: finished out of those you started.`
  const readTip = (r: TypeRow) => {
    const rd = num(r.read), ow = num(r.owned), pct = r.owned > 0 ? Math.round((r.read / r.owned) * 100) : 0, ty = r.type
    return tipAttrs({ title: ty, value: t`${rd} read of ${ow} owned`, note: t`${pct}% of your ${ty}` })
  }
  const timeTip = (r: TypeRow) => {
    const share = Math.round(r.share * 100)
    return tipAttrs({ title: r.type, value: dur(r.seconds), note: t`${share}% of the reading in ${rangePhrase}` })
  }
  const quiet = (s: ReactNode) => <span className="text-[13px] leading-[20px] text-muted-foreground">{s}</span>

  if (rows && rows.length === 0) {
    return (
      <section ref={e.ref} data-box="types">
        <Head><Trans>By type</Trans></Head>
        <Note h={TYPE_ROW_H}><Trans>No books in the library yet.</Trans></Note>
      </section>
    )
  }

  if (!desktop) {
    return (
      <section ref={e.ref} data-box="types">
        <Head note={<span className="inline-flex items-center gap-2"><span><Trans>reading time</Trans></span><RangeChip>{range.label}</RangeChip></span>}><Trans>By type</Trans></Head>
        <div className="k-bars">
          {list.map((r, i) => (
            <div key={r?.type ?? i} data-box={`type-${i}`} className={cn('py-3', i === 0 && 'pt-0', i > 0 && cn('border-t', HAIR))}>
              <div className="mb-1 truncate text-[15px] font-semibold leading-[20px] text-foreground"><Ph on={!r}>{r?.type ?? PH.type}</Ph></div>
              <div className="flex justify-between gap-3 text-[13px] leading-[21px]">
                <span className="text-muted-foreground"><Trans>Read</Trans></span>
                <span className="whitespace-nowrap tabular-nums text-foreground">
                  {r ? (() => { const rd = num(r.read), ow = num(r.owned); return <Trans>{rd} <span className="text-muted-foreground">of {ow} owned</span></Trans> })() : <Ph on>{PH.q}</Ph>}
                </span>
              </div>
              <div className="flex justify-between gap-3 text-[13px] leading-[21px]">
                <span className="text-muted-foreground"><Trans>Finish rate</Trans></span>
                <span className="whitespace-nowrap tabular-nums text-foreground">
                  {r ? r.finishPct == null ? <span className="text-muted-foreground"><Trans>none started</Trans></span> : (() => { const p = Math.round(r.finishPct), f = num(r.finished), s = num(r.started); return <>{p}% <span className="text-muted-foreground"><Trans>· {f} of {s} started</Trans></span></> })() : <Ph on>{PH.q}</Ph>}
                </span>
              </div>
              <div className={cn('flex justify-between gap-3 text-[13px] leading-[21px]', dim)}>
                <span className="min-w-0 truncate text-muted-foreground"><Trans>Reading time</Trans></span>
                <span className="whitespace-nowrap text-foreground">
                  {r ? r.seconds > 0 ? (() => { const share = Math.round(r.share * 100); return <>{dur(r.seconds)} <span className="text-muted-foreground">· {share}%</span></> })() : <span className="text-muted-foreground"><Trans>none</Trans></span> : <Ph on>{PH.dur}</Ph>}
                </span>
              </div>
            </div>
          ))}
        </div>
        <p className="mt-3 min-h-[36px] text-[13px] leading-[18px] text-muted-foreground">{def}</p>
      </section>
    )
  }

  const th = TH
  return (
    <section ref={e.ref} data-box="types">
      <Head><Trans>By type</Trans></Head>
      <div role="table" className="k-bars" aria-label={t`By type`}>
        <div role="row" className={cn(TYPE_COLS, 'h-[28px]')}>
          <span role="columnheader" className={th}><Trans>Type</Trans></span>
          <span role="columnheader" className={cn(th, 'text-right')}><Trans>Owned</Trans></span>
          <span role="columnheader" className={cn(th, 'text-right')}><Trans>Read</Trans></span>
          <span role="columnheader" className={cn(th, 'text-right')}><Trans>Finish rate</Trans></span>
          <span role="columnheader" className={cn(th, 'flex items-start justify-end gap-2', dim)}><Trans>Time</Trans><RangeChip>{range.label}</RangeChip></span>
        </div>
        {list.map((r, i) => (
          <div key={r?.type ?? i} role="row" data-box={`type-${i}`} className={cn(TYPE_COLS, 'items-start border-t pt-[9px]', HAIR)} style={{ height: TYPE_ROW_H }}>
            <span role="cell" className="truncate text-[15px] font-semibold leading-[20px] text-foreground"><Ph on={!r}>{r?.type ?? PH.type}</Ph></span>
            <span role="cell" className="text-right text-[15px] font-semibold leading-[20px] tabular-nums text-foreground">{r ? <CountUp entry={e} value={r.owned} format={num} align="end" /> : <Ph on>{PH.v}</Ph>}</span>
            <div role="cell" className="text-right">
              <div className="text-[15px] font-semibold leading-[20px] tabular-nums text-foreground">{r ? <CountUp entry={e} value={r.read} format={num} align="end" /> : <Ph on>{PH.v}</Ph>}</div>
              <Thin frac={r && r.owned > 0 ? r.read / r.owned : 0} className={cn('mt-[7px]', !r && 'opacity-40')} style={stagger(i, list.length)} tip={r ? readTip(r) : undefined} />
            </div>
            <div role="cell" className="text-right">
              {r ? r.finishPct == null ? quiet(<Trans>none started</Trans>) : (
                <>
                  <div className="text-[15px] font-semibold leading-[20px] tabular-nums text-foreground"><CountUp entry={e} value={Math.round(r.finishPct)} format={String} align="end" />%</div>
                  <div className="whitespace-nowrap text-[12px] leading-[16px] text-muted-foreground">{(() => { const f = num(r.finished), s = num(r.started); return t`${f} of ${s} started` })()}</div>
                </>
              ) : <div className="text-[15px] leading-[20px]"><Ph on>{PH.v}</Ph></div>}
            </div>
            <div role="cell" className={cn('text-right', dim)}>
              {r ? r.seconds > 0 ? (
                <>
                  <div className="text-[15px] font-semibold leading-[20px] text-foreground">{dur(r.seconds)}</div>
                  <Thin frac={r.share} className="mt-[7px]" style={stagger(i, list.length)} tip={timeTip(r)} />
                </>
              ) : quiet(<Trans>none</Trans>) : <div className="text-[15px] leading-[20px]"><Ph on>{PH.dur}</Ph></div>}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 h-[18px] truncate text-[13px] leading-[18px] text-muted-foreground">{def}</p>
    </section>
  )
}

// ── Authors: two columns of rows by reading time in the range ─────────────────

const AUTHOR_ROW_H = 54

function Authors({ stats, desktop, shape, range, dim }: Common & { range: Range; dim: string }) {
  const rangePhrase = range.phrase
  const e = useEntry('library/authors', range.sig)
  // An author who stays slides to the new place; the bar and time move to the new figures.
  const tw = useTween(e, stats ? stats.author_affinity : null, desktop)
  const cap = desktop ? AUTHORS_MAX : AUTHORS_MAX_PHONE
  const list = stats ? stats.author_affinity.slice(0, cap) : Array.from({ length: Math.min(cap, shape.authors) }, () => null)
  const max = Math.max(1, ...(stats?.author_affinity.slice(0, 1).map((a) => a.seconds) ?? [1]))
  const cols = desktop ? 2 : 1
  const note = stats ? <span className="inline-flex items-center gap-2"><span><Trans>by reading time</Trans></span><RangeChip>{range.label}</RangeChip></span> : <Ph on>{PH.rangeNote}</Ph>
  return (
    <section ref={e.ref} data-box="authors" className={dim}>
      <Head note={note} stack={!desktop}><Trans>Authors</Trans></Head>
      {list.length === 0 ? (
        <Note h={AUTHOR_ROW_H}>{stats && t`No reading in ${rangePhrase}.`}</Note>
      ) : (
        <div ref={tw} data-box="author-rows" className={cn('k-bars relative grid', desktop ? 'grid-cols-2 gap-x-12' : 'grid-cols-1')}>
          {list.map((a, i) => {
            const read = a ? plural(a.book_count, { one: '# book read', other: '# books read' }) : ''
            const sub = a
              ? (() => {
                  const fin = a.books_finished > 0 ? plural(a.books_finished, { one: '# finished all time', other: '# finished all time' }) : t`none finished`
                  return `${read} · ${fin}`
                })()
              : PH.authorSub
            const tip = a ? (() => {
              const dur = formatDuration(a.seconds)
              const sessions = plural(a.sessions, { one: '# session', other: '# sessions' })
              return tipAttrs({ title: a.author, value: t`${dur} in ${rangePhrase}`, note: `${read} · ${sessions}` })
            })() : undefined
            const row = cn('block', i >= cols && cn('border-t', HAIR))
            const body = (
              <div data-k={a ? `a:${a.author}` : undefined} data-kt="move" data-k-col={a ? String(i % cols) : undefined} className="grid h-full grid-cols-[minmax(0,1fr)_auto] grid-rows-[20px_17px_10px] content-center gap-x-4">
                <span className="truncate text-[15px] font-semibold leading-[20px] text-foreground transition-colors group-hover:text-primary"><Ph on={!a}>{a?.author ?? PH.author}</Ph></span>
                <span className="text-right text-[14px] leading-[20px] text-muted-foreground"><Ph on={!a}>{a ? <CountUp entry={e} value={a.seconds} format={formatDuration} align="end" /> : PH.dur}</Ph></span>
                <span className="col-span-2 truncate text-[13px] leading-[17px] text-muted-foreground"><Ph on={!a}>{sub}</Ph></span>
                <Thin frac={a ? a.seconds / max : 0} k={a ? `t:${a.author}` : undefined} className={cn('col-span-2 self-end', !a && 'opacity-40')} style={stagger(i, list.length)} tip={tip} />
              </div>
            )
            // A row is one link to the author's books in the library, as the book page and the search link authors.
            return a ? (
              <Link key={a.author} to={`/?tab=books&author=${encodeURIComponent(a.author)}`} data-box={`author-${i}`} className={cn(row, 'group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring')} style={{ height: AUTHOR_ROW_H }}>{body}</Link>
            ) : (
              <div key={i} data-box={`author-${i}`} className={row} style={{ height: AUTHOR_ROW_H }}>{body}</div>
            )
          })}
        </div>
      )}
    </section>
  )
}
