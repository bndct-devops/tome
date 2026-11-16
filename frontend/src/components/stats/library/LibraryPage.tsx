// The designed Library page: what the library holds and how much of it is read.
// One fixed layout; every section's size follows from fixed heights and row
// counts (the rows of the last answer, remembered per browser), so the skeleton
// draws each box where the loaded page has it. All-time sections stay as they
// are on a range change; the range-bound ones (Authors, By type's time column,
// the All books table) dim in place until the new answer lands.
import { useEffect, useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { cn, formatDuration } from '@/lib/utils'
import type { BacklogSummary } from '@/lib/backlog'
import type { LibraryResponse, StatsResponse } from '@/components/stats/shared'
import { fmtDay as fmtShortDay } from '@/components/stats/range'
import type { DesignedPageProps } from '@/components/stats/kit/page'
import { Cover, Ph, Track } from '@/components/stats/kit/Kit'
import { DESKTOP_MIN, DIM, HAIR, LEVELS, UNDIM, num, useWidth } from '@/components/stats/kit/tokens'
import { useStatsGet } from '@/components/stats/kit/useStatsGet'
import { AUTHORS_MAX, BOOKS_SHOWN, SERIES_MAX, fmtMonth, libraryStart, rememberShape, rememberedShape, seriesModel, typeRows, type Series, type Shape } from './model'
import { Head, Note, Thin } from './parts'
import { AddedSection, AllBooks, Backlog, ChartsRow } from './Sections'

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text: drawn as blocks
   while loading and never read; it only gives each line its loaded width. */
const PH = {
  fig: '000', figFoot: '10% of your library', since: 'since Mar 2026', unit: 'books',
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
  const backlog = useStatsGet<BacklogSummary>(`/stats/backlog?scope=unread&tz_offset=${tz}`)
  const shape = useMemo(rememberedShape, [])

  useEffect(() => {
    if (!stats) return
    const { open, done } = seriesModel(stats.series_completion)
    rememberShape({
      series: Math.min(SERIES_MAX, open.length), done: done.length > 0, types: stats.tbr.by_type.length,
      authors: Math.min(AUTHORS_MAX, stats.author_affinity.length), books: Math.min(BOOKS_SHOWN, stats.per_book_time.length),
    })
  }, [stats])
  useEffect(() => {
    if (backlog.data) rememberShape({ backlog: backlog.data.by_type.filter((r) => r.seconds > 0).length })
  }, [backlog.data])

  const from = custom ? fmtShortDay(custom.start) : ''
  const to = custom ? fmtShortDay(custom.end) : ''
  const rangePhrase = custom ? t`${from} to ${to}` : days === 0 ? t`all time` : days === 365 ? t`the last 12 months` : t`the last ${days} days`
  const dim = pending ? DIM : UNDIM
  const busy = pending || !stats || (lib.data == null && !lib.failed) || (backlog.data == null && !backlog.failed)

  const common = { stats, desktop, width, shape }
  return (
    <div ref={ref} style={LEVELS} className={cn('flex flex-col', desktop ? 'gap-[52px]' : 'gap-10')} aria-busy={busy}>
      {width > 0 && (
        <>
          <Figures {...common} />
          <SeriesSection {...common} />
          {desktop ? (
            <>
              <ByType {...common} rangePhrase={rangePhrase} dim={dim} />
              <Authors {...common} rangePhrase={rangePhrase} dim={dim} />
            </>
          ) : (
            <>
              <Authors {...common} rangePhrase={rangePhrase} dim={dim} />
              <ByType {...common} rangePhrase={rangePhrase} dim={dim} />
            </>
          )}
          <Backlog data={backlog.data} failed={backlog.failed} desktop={desktop} rows={shape.backlog} />
          <ChartsRow stats={stats} lib={lib.data} libFailed={lib.failed} desktop={desktop} width={width} />
          <AddedSection stats={stats} desktop={desktop} width={width} />
          <AllBooks stats={stats} desktop={desktop} rows={shape.books} rangePhrase={rangePhrase} dim={dim} />
        </>
      )}
    </div>
  )
}

type Common = { stats: StatsResponse | null; desktop: boolean; width: number; shape: Shape }

// ── Your library: four figures ────────────────────────────────────────────────

function Figures({ stats, desktop }: Common) {
  const tb = stats?.tbr
  const start = stats ? libraryStart(stats.library_growth) : null
  const since = start ? fmtMonth(start) : null
  const started = stats?.completion_rate.started ?? 0
  const labels: ReactNode[] = [<Trans key="l">Library</Trans>, <Trans key="r">Read</Trans>, <Trans key="p">In progress</Trans>, <Trans key="w">Want to read</Trans>]
  const figs: { value: string; foot: string }[] = tb
    ? [
        { value: num(tb.owned), foot: since ? t`since ${since}` : ' ' },
        { value: num(tb.read), foot: (() => { const pct = Math.round(tb.pct); return t`${pct}% of your library` })() },
        { value: num(tb.reading), foot: started > 0 ? plural(started, { one: 'of # book started', other: 'of # books started' }) : t`nothing started yet` },
        { value: num(tb.want_to_read), foot: (() => { const n = num(tb.unread); return t`${n} unread in all` })() },
      ]
    : labels.map(() => ({ value: PH.fig, foot: PH.figFoot }))
  return (
    <section data-box="figures">
      <Head note={<Trans>all time</Trans>}><Trans>Your library</Trans></Head>
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
              <Ph on={!tb}>{f.value}</Ph>
              {i === 0 && <span className="ml-1.5 font-sans text-[16px] font-semibold tracking-normal"><Ph on={!tb}>{tb ? plural(tb.owned, { one: 'book', other: 'books' }) : PH.unit}</Ph></span>}
            </div>
            <div className={cn('mt-2 truncate leading-[1.4] text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]')}>
              <Ph on={!tb}>{tb ? f.foot : i === 0 ? PH.since : PH.figFoot}</Ph>
            </div>
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
              <Link to={`/books/${s.next_book_id}`} className="font-medium text-foreground hover:underline underline-offset-4"><Trans>Next: Volume {nextIdx}</Trans></Link>
            ) : (
              <span className="font-medium text-foreground"><Trans>Next: Volume {nextIdx}</Trans></span>
            )}
            {' · '}
            {s.next_book_id != null ? <Trans>on your shelf</Trans> : <Trans>not in your library</Trans>}
          </>
        )
      : <Ph on>{PH.next}</Ph>
    const seriesHref = s ? `/?series=${encodeURIComponent(s.series)}&tab=books` : ''
    return (
      <div key={s?.series ?? i} data-box={`series-card-${i}`} className="flex min-w-0 items-center gap-4" style={{ height: cardH }}>
        {s ? <Link to={seriesHref} className="shrink-0" aria-label={s.series}><Cover id={s.sample_book_id} h={coverH} /></Link> : <Cover id={null} h={coverH} />}
        <div className="min-w-0 flex-1">
          <div className={cn('truncate font-semibold leading-[20px] text-foreground', desktop ? 'text-[16px]' : 'text-[15px]')}>
            {s ? <Link to={seriesHref} className="hover:underline underline-offset-4">{s.series}</Link> : <Ph on>{PH.series}</Ph>}
          </div>
          <div className={cn('mt-0.5 truncate leading-[19px] text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]')}><Ph on={!s}>{sub}</Ph></div>
          <div className="mt-2 flex h-5 items-center gap-3">
            <Track pct={s?.pct ?? 0} className={!s ? 'opacity-40' : ''} />
            <span className="text-[15px] font-bold tabular-nums text-foreground"><Ph on={!s}>{s ? `${Math.round(s.pct)}%` : PH.pct}</Ph></span>
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
  const covers = (
    <div className={cn('flex gap-2', !desktop && 'w-max pr-5')}>
      {shownDone.map((s, i) => {
        if (!s) return <Cover key={i} id={null} h={DONE_COVER_H} className="rounded-[6px]" />
        const tot = num(s.total)
        const name = s.series
        const tip = t`${name}, ${tot} of ${tot} read`
        return (
          <Link key={s.series} to={`/?series=${encodeURIComponent(s.series)}&tab=books`} title={tip} aria-label={tip}>
            <Cover id={s.sample_book_id} h={DONE_COVER_H} className="rounded-[6px]" />
          </Link>
        )
      })}
      {doneList.length > shownDone.length && (
        <div className="grid place-items-center rounded-[6px] bg-secondary text-[13px] font-semibold text-muted-foreground" style={{ width: DONE_COVER_W, height: DONE_COVER_H }}>
          +{doneList.length - shownDone.length}
        </div>
      )}
    </div>
  )

  return (
    <section data-box="series">
      <Head note={note}><Trans>Series</Trans></Head>
      {model && model.open.length === 0 && model.done.length === 0 ? (
        <Note h={cardH}><Trans>No series started yet. Series you read show up here.</Trans></Note>
      ) : (
        <>
          {open.length > 0 && (
            <div data-box="series-cards" className={desktop ? 'grid grid-cols-3 gap-x-4 gap-y-6' : 'flex flex-col gap-5'}>
              {open.map(card)}
            </div>
          )}
          {model && open.length === 0 && <Note h={cardH}><Trans>Every series you started is complete.</Trans></Note>}
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
const TYPE_COLS = 'grid grid-cols-[minmax(0,18fr)_minmax(0,10fr)_minmax(0,26fr)_minmax(0,20fr)_minmax(0,26fr)] gap-x-4'
const TH = 'whitespace-nowrap text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground'

function ByType({ stats, desktop, shape, rangePhrase, dim }: Common & { rangePhrase: string; dim: string }) {
  const rows = useMemo(() => (stats ? typeRows(stats) : null), [stats])
  const list = rows ?? Array.from({ length: shape.types }, () => null)
  const def = desktop
    ? t`Read: finished books out of all you own. Finish rate: finished out of those you started. Time in range: ${rangePhrase}.`
    : t`Read: finished books out of all you own. Finish rate: finished out of those you started.`
  const quiet = (s: ReactNode) => <span className="text-[13px] leading-[20px] text-muted-foreground">{s}</span>

  if (rows && rows.length === 0) {
    return (
      <section data-box="types">
        <Head><Trans>By type</Trans></Head>
        <Note h={TYPE_ROW_H}><Trans>No books in the library yet.</Trans></Note>
      </section>
    )
  }

  if (!desktop) {
    return (
      <section data-box="types">
        <Head><Trans>By type</Trans></Head>
        <div>
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
                <span className="min-w-0 truncate text-muted-foreground"><Trans>Time, {rangePhrase}</Trans></span>
                <span className="whitespace-nowrap text-foreground">
                  {r ? r.seconds > 0 ? (() => { const share = Math.round(r.share * 100); return <>{formatDuration(r.seconds)} <span className="text-muted-foreground">· {share}%</span></> })() : <span className="text-muted-foreground"><Trans>none</Trans></span> : <Ph on>{PH.dur}</Ph>}
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
    <section data-box="types">
      <Head><Trans>By type</Trans></Head>
      <div role="table" aria-label={t`By type`}>
        <div role="row" className={cn(TYPE_COLS, 'h-[28px]')}>
          <span role="columnheader" className={th}><Trans>Type</Trans></span>
          <span role="columnheader" className={cn(th, 'text-right')}><Trans>Owned</Trans></span>
          <span role="columnheader" className={cn(th, 'text-right')}><Trans>Read</Trans></span>
          <span role="columnheader" className={cn(th, 'text-right')}><Trans>Finish rate</Trans></span>
          <span role="columnheader" className={cn(th, 'text-right', dim)}><Trans>Time in range</Trans></span>
        </div>
        {list.map((r, i) => (
          <div key={r?.type ?? i} role="row" data-box={`type-${i}`} className={cn(TYPE_COLS, 'items-start border-t pt-[9px]', HAIR)} style={{ height: TYPE_ROW_H }}>
            <span role="cell" className="truncate text-[15px] font-semibold leading-[20px] text-foreground"><Ph on={!r}>{r?.type ?? PH.type}</Ph></span>
            <span role="cell" className="text-right text-[15px] font-semibold leading-[20px] tabular-nums text-foreground"><Ph on={!r}>{r ? num(r.owned) : PH.v}</Ph></span>
            <div role="cell" className="text-right">
              <div className="text-[15px] font-semibold leading-[20px] tabular-nums text-foreground"><Ph on={!r}>{r ? num(r.read) : PH.v}</Ph></div>
              <Thin frac={r && r.owned > 0 ? r.read / r.owned : 0} className={cn('mt-[7px]', !r && 'opacity-40')} />
            </div>
            <div role="cell" className="text-right">
              {r ? r.finishPct == null ? quiet(<Trans>none started</Trans>) : (
                <>
                  <div className="text-[15px] font-semibold leading-[20px] tabular-nums text-foreground">{Math.round(r.finishPct)}%</div>
                  <div className="whitespace-nowrap text-[12px] leading-[16px] text-muted-foreground">{(() => { const f = num(r.finished), s = num(r.started); return t`${f} of ${s} started` })()}</div>
                </>
              ) : <div className="text-[15px] leading-[20px]"><Ph on>{PH.v}</Ph></div>}
            </div>
            <div role="cell" className={cn('text-right', dim)}>
              {r ? r.seconds > 0 ? (
                <>
                  <div className="text-[15px] font-semibold leading-[20px] text-foreground">{formatDuration(r.seconds)}</div>
                  <Thin frac={r.share} className="mt-[7px]" />
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

function Authors({ stats, desktop, shape, rangePhrase, dim }: Common & { rangePhrase: string; dim: string }) {
  const list = stats ? stats.author_affinity.slice(0, AUTHORS_MAX) : Array.from({ length: shape.authors }, () => null)
  const max = Math.max(1, ...(stats?.author_affinity.slice(0, 1).map((a) => a.seconds) ?? [1]))
  const cols = desktop ? 2 : 1
  const note = stats ? t`by reading time, ${rangePhrase}` : <Ph on>{PH.rangeNote}</Ph>
  return (
    <section data-box="authors" className={dim}>
      <Head note={note} stack={!desktop}><Trans>Authors</Trans></Head>
      {stats && list.length === 0 ? (
        <Note h={AUTHOR_ROW_H}>{t`No reading in ${rangePhrase}.`}</Note>
      ) : (
        <div data-box="author-rows" className={cn('grid', desktop ? 'grid-cols-2 gap-x-12' : 'grid-cols-1')}>
          {list.map((a, i) => {
            const sub = a
              ? (() => {
                  const read = plural(a.book_count, { one: '# book read', other: '# books read' })
                  const fin = a.books_finished > 0 ? plural(a.books_finished, { one: '# finished all time', other: '# finished all time' }) : t`none finished`
                  return `${read} · ${fin}`
                })()
              : PH.authorSub
            return (
              <div key={a?.author ?? i} data-box={`author-${i}`} className={cn('grid grid-cols-[minmax(0,1fr)_auto] grid-rows-[20px_17px_10px] content-center gap-x-4', i >= cols && cn('border-t', HAIR))} style={{ height: AUTHOR_ROW_H }}>
                <span className="truncate text-[15px] font-semibold leading-[20px] text-foreground"><Ph on={!a}>{a?.author ?? PH.author}</Ph></span>
                <span className="text-right text-[14px] leading-[20px] text-muted-foreground"><Ph on={!a}>{a ? formatDuration(a.seconds) : PH.dur}</Ph></span>
                <span className="col-span-2 truncate text-[13px] leading-[17px] text-muted-foreground"><Ph on={!a}>{sub}</Ph></span>
                <Thin frac={a ? a.seconds / max : 0} className={cn('col-span-2 self-end', !a && 'opacity-40')} />
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
