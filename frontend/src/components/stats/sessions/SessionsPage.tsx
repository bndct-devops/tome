// The Sessions tab: every reading session, newest first, grouped by local
// calendar day with each day's counted total, filtered to Counted / Not counted /
// All, with the trim and delete actions of the session log. It ignores the
// Stats range (the frame hides the range pills here).
// Motion: the first page's rows fade in once per session (kit/motion.ts); rows
// from "Show more" take a quick fade of their own; a filter or sort switch only
// dims. Hover: a row washes, its cover and title lead to the book.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { ArrowUpDown, ChevronDown, Loader2, TriangleAlert } from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatDuration } from '@/lib/utils'
import { Pill } from '@/components/page'
import type { SessionEntry, SessionsPage as SessionsAnswer } from '@/components/stats/shared'
import { Cover, Ph } from '@/components/stats/kit/Kit'
import { K, ONCE, stagger, useEntry } from '@/components/stats/kit/motion'
import { DESKTOP_MIN, DIM, HAIR, UNDIM, num, useWidth } from '@/components/stats/kit/tokens'
import { ChoiceMenu } from '@/components/stats/kit/Menu'
import { dropStatsCache } from '@/components/stats/kit/useStatsGet'
import { sourceLabel, useSessionActions, type SessionActions } from './actions'
import { SessionTools, TrimPanel } from './SessionTools'
import { PAGE, groupByDay, rememberShape, rememberedShape, shapeKnown, type Filter, type Sort } from './model'
import { tipAttrs } from '@/components/stats/kit/tip'

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text: drawn as blocks
   while loading and never read; it only gives each line its loaded width. Grid
   templates are CSS. */
const PH = { day: 'Wednesday, Nov 11', total: '1h 44m counted', time: '08:27 AM', title: 'He Who Fights with Monsters 10', dur: '1h 19m', dev: 'Kindle', status: 'Imported', count: '1,332', more: 'Show more (793 remaining)' }
const COLS = 'grid-cols-[84px_minmax(0,1fr)_80px_150px_110px_72px]'
const COLS_LONGEST = 'grid-cols-[110px_minmax(0,1fr)_80px_150px_110px_72px]'
const PHONE_TOOL = 'h-8 w-7'
// A row washes on hover a little past its hairline ends (a layer behind it, so no box moves).
const ROW_HOVER = 'relative isolate before:pointer-events-none before:absolute before:inset-y-0 before:-inset-x-2 before:-z-10 before:rounded-[6px] before:transition-colors hover:before:bg-secondary/60'
// Rows past the first page: a quick fade as they arrive (not part of the entry).
const LATE_ROW = 'motion-safe:animate-[k-fade_200ms_cubic-bezier(0.2,0.8,0.2,1)_both]'
/* eslint-enable lingui/no-unlocalized-strings */

type View = { key: string; total: number; counts: SessionsAnswer['counts']; sessions: SessionEntry[]; dayTotals: Record<string, number> }

const tz = () => new Date().getTimezoneOffset()
const query = (filter: Filter, sort: Sort, offset: number, limit: number) =>
  `/stats/sessions?sort=${sort}&limit=${limit}&offset=${offset}&counted=${filter}&day_totals=true&tz_offset=${tz()}`

export function SessionsPage() {
  const [ref, width] = useWidth<HTMLDivElement>()
  const desktop = width >= DESKTOP_MIN
  const [filter, setFilter] = useState<Filter>('counted')
  const [sort, setSort] = useState<Sort>('recent')
  const [view, setView] = useState<View | null>(null)
  const [more, setMore] = useState(false)
  const key = `${filter}|${sort}`
  const seq = useRef(0)
  // A first visit (no shape remembered in this browser): the days' grouping is not
  // known, so the skeleton draws only the first day's heading and first row (where
  // any answer puts them) and no "Show more"; the rest is drawn when the page lands.
  const [known] = useState(shapeKnown)
  const shape = useMemo(() => (known ? rememberedShape() : [1]), [known])
  // "Show more" is there when the last first page was full.
  const moreLikely = known && shape.reduce((a, n) => a + n, 0) >= PAGE

  // First page of the current filter and sort; the previous list stays (dimmed) until it lands.
  useEffect(() => {
    const n = ++seq.current
    api.get<SessionsAnswer>(query(filter, sort, 0, PAGE))
      .then((r) => {
        if (n !== seq.current) return
        setView({ key: `${filter}|${sort}`, total: r.total, counts: r.counts, sessions: r.sessions, dayTotals: r.day_totals ?? {} })
        if (filter === 'counted' && sort === 'recent') rememberShape(groupByDay(r.sessions))
      })
      .catch(() => {})
  }, [filter, sort])

  const showMore = () => {
    if (!view || more) return
    setMore(true)
    const n = seq.current
    api.get<SessionsAnswer>(query(filter, sort, view.sessions.length, PAGE))
      .then((r) => {
        if (n !== seq.current) return
        setView((v) => v && { ...v, total: r.total, counts: r.counts, sessions: [...v.sessions, ...r.sessions], dayTotals: { ...v.dayTotals, ...(r.day_totals ?? {}) } })
      })
      .catch(() => {})
      .finally(() => setMore(false))
  }

  // After a trim or delete: the rows shown so far again, for fresh counts and day totals.
  const refresh = useCallback((shown: number) => {
    const n = ++seq.current
    const want = Math.max(PAGE, shown)
    const pages: Promise<SessionsAnswer>[] = []
    for (let off = 0; off < want; off += 200) pages.push(api.get<SessionsAnswer>(query(filter, sort, off, Math.min(200, want - off))))
    Promise.all(pages)
      .then((rs) => {
        if (n !== seq.current) return
        const last = rs[rs.length - 1]
        setView({
          key: `${filter}|${sort}`, total: last.total, counts: last.counts,
          sessions: rs.flatMap((r) => r.sessions),
          dayTotals: Object.assign({}, ...rs.map((r) => r.day_totals ?? {})),
        })
      })
      .catch(() => {})
  }, [filter, sort])

  const actions = useSessionActions({
    onTrimmed: (s, seconds) => {
      dropStatsCache('/stats')
      setView((v) => v && { ...v, sessions: v.sessions.map((row) => (row.id === s.id ? { ...row, duration_seconds: seconds, suspect: false, suggested_seconds: null } : row)) })
      refresh(view?.sessions.length ?? PAGE)
    },
    onDeleted: (s) => {
      dropStatsCache('/stats')
      const shown = view?.sessions.length ?? PAGE
      setView((v) => v && { ...v, total: v.total - 1, sessions: v.sessions.filter((row) => row.id !== s.id) })
      refresh(Math.max(PAGE, shown - 1))
    },
  })

  const loading = !view
  const pending = !!view && view.key !== key
  const c = view?.counts
  const chips: { id: Filter; label: ReactNode; n: number | undefined }[] = [
    { id: 'counted', label: <Trans>Counted</Trans>, n: c?.counted },
    { id: 'not_counted', label: <Trans>Not counted</Trans>, n: c?.not_counted },
    { id: 'all', label: <Trans>All</Trans>, n: c?.all },
  ]
  const longest = (pending ? view!.key.endsWith('longest') : sort === 'longest')
  const remaining = view ? Math.max(0, view.total - view.sessions.length) : 0
  // One entry, on the first data of the session; a filter or sort switch only dims.
  const e = useEntry('sessions/log', view ? ONCE : null)

  return (
    <div ref={ref} aria-busy={loading || pending}>
      {width > 0 && (
        // eslint-disable-next-line react-hooks/refs -- Entry.ref is a callback ref (a method), not a ref object
        <section ref={e.ref} data-box="sessions">
          <p data-box="sessions-explain" className={cn('text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]', 'leading-[1.5]')}>
            <Trans><span className="font-medium text-foreground">Imported</span>: from KOReader&apos;s synced reading history. Delete only: KOReader already caps idle time, so there is nothing to trim.</Trans>{' '}
            <Trans><span className="font-medium text-foreground">Not counted</span>: a live device session whose sitting is also described by imported history. Listed for completeness, excluded from the totals so nothing is counted twice.</Trans>
          </p>
          <div data-box="sessions-chips" data-stats-controls="" className={cn('mt-5 flex items-center', desktop ? 'gap-1.5' : 'flex-wrap gap-1')}>
            <div role="radiogroup" aria-label={t`Which sessions`} className={cn('flex items-center', desktop ? 'gap-1.5' : 'gap-1')}>
              {chips.map((ch) => {
                const on = ch.id === filter
                return (
                  <button
                    key={ch.id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    onClick={() => setFilter(ch.id)}
                    className={cn(
                      'inline-flex items-center whitespace-nowrap rounded-full font-medium transition-colors',
                      desktop ? 'h-[34px] gap-[7px] px-3.5 text-[14px]' : 'h-8 gap-1.5 px-3 text-[13px]',
                      on ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                    )}
                  >
                    {ch.label}
                    <span className="text-[13px] font-normal tabular-nums text-muted-foreground"><Ph on={ch.n == null}>{ch.n == null ? PH.count : num(ch.n)}</Ph></span>
                  </button>
                )
              })}
            </div>
            {/* The order as the app's choice menu (kit/Menu.tsx), as the Library's sort pill. */}
            <div className={cn(desktop && 'ml-auto')}>
              <ChoiceMenu
                value={sort}
                options={[{ id: 'recent', label: t`Newest first` }, { id: 'longest', label: t`Longest first` }]}
                onPick={(id) => setSort(id as typeof sort)}
                label={t`Order`}
                align="end"
                className="inline-flex h-[30px] items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-[13px] text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-expanded:bg-secondary aria-expanded:text-foreground"
              >
                <ArrowUpDown className="h-3.5 w-3.5" aria-hidden="true" />
                {sort === 'recent' ? t`Newest first` : t`Longest first`}
              </ChoiceMenu>
            </div>
          </div>

          <div data-box="sessions-log" className={cn('mt-6', pending ? DIM : UNDIM)}>
            {loading ? (
              <Skeleton shape={shape} desktop={desktop} />
            ) : view.sessions.length === 0 ? (
              <p className="flex h-[88px] items-center text-[15px] text-muted-foreground">
                {view.counts.all === 0
                  ? <Trans>No sessions recorded. Sessions arrive from the TomeSync KOReader plugin, the web reader, or by hand on a book page.</Trans>
                  : <Trans>No sessions in this view.</Trans>}
              </p>
            ) : longest ? (
              <div>
                {view.sessions.map((s, i) => <Row key={s.id} s={s} i={i} desktop={desktop} a={actions} withDate />)}
              </div>
            ) : (
              <Days view={view} desktop={desktop} a={actions} />
            )}
          </div>

          {((loading && moreLikely) || remaining > 0) && (
            <div data-box="sessions-more" className="mt-5 flex justify-center">
              {loading ? (
                <span className="inline-flex h-9 items-center rounded-full bg-secondary px-4 text-[14px]"><Ph on>{PH.more}</Ph></span>
              ) : (
                <Pill variant="secondary" onClick={showMore} disabled={more || pending}>
                  {more ? <Loader2 className="h-4 w-4 animate-spin" /> : <ChevronDown className="h-4 w-4" />}
                  {(() => { const left = num(remaining); return <span><Trans>Show more ({left} remaining)</Trans></span> })()}
                </Pill>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  )
}

// ── Days and rows ─────────────────────────────────────────────────────────────

function dayLabel(day: string) {
  if (!day) return t`Unknown date`
  // eslint-disable-next-line lingui/no-unlocalized-strings -- ISO time part
  const d = new Date(day + 'T00:00:00')
  const thisYear = d.getFullYear() === new Date().getFullYear()
  return d.toLocaleDateString(undefined, thisYear ? { weekday: 'long', month: 'short', day: 'numeric' } : { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric' })
}

function DayHead({ i, label, total }: { i: number; label: ReactNode; total: ReactNode }) {
  return (
    <div data-box={`sday-${i}`} className={cn('flex h-8 items-baseline gap-3 border-b', HAIR)}>
      <span className="text-[15px] font-semibold text-foreground">{label}</span>
      <span className="ml-auto whitespace-nowrap text-[14px] tabular-nums text-muted-foreground">{total}</span>
    </div>
  )
}

function Days({ view, desktop, a }: { view: View; desktop: boolean; a: SessionActions }) {
  const groups = groupByDay(view.sessions)
  let k = 0
  return (
    <div className="flex flex-col gap-7">
      {groups.map((g, gi) => {
        const secs = view.dayTotals[g.day]
        const dur = secs != null ? formatDuration(secs) : null
        return (
          <div key={g.day || gi}>
            <DayHead
              i={gi}
              label={dayLabel(g.day)}
              total={dur != null ? <Trans><b className="font-semibold text-foreground">{dur}</b> counted</Trans> : ' '}
            />
            {g.rows.map((s) => <Row key={s.id} s={s} i={k++} desktop={desktop} a={a} />)}
          </div>
        )
      })}
    </div>
  )
}

const timeOf = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '-')
const dateOf = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '-')

function Row({ s, i, desktop, a, withDate = false }: { s: SessionEntry; i: number; desktop: boolean; a: SessionActions; withDate?: boolean }) {
  const dur = s.duration_seconds != null ? formatDuration(s.duration_seconds) : '-'
  const when = withDate ? dateOf(s.started_at) : timeOf(s.started_at)
  const status = s.kind === 'imported' ? t`Imported` : !s.counted ? t`Not counted` : ''
  const statusTitle = s.kind === 'imported'
    ? t`Imported from KOReader reading history`
    : !s.counted ? t`Not in totals: this book's imported KOReader history already covers this reading` : undefined
  const warn = s.suspect && <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-warning" aria-label={t`Unusually long session`} />
  const cover = s.book_id ? (
    <Link to={`/books/${s.book_id}`} className="group/cover shrink-0 rounded-[4px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={s.book_title} tabIndex={-1}>
      <Cover id={s.book_id} h={40} className="rounded-[4px] transition-transform duration-200 group-hover/cover:-translate-y-0.5" />
    </Link>
  ) : <Cover id={null} h={40} className="rounded-[4px]" />
  const title = s.book_id
    ? <Link to={`/books/${s.book_id}`} className="transition-colors hover:text-primary focus-visible:underline focus-visible:outline-none underline-offset-4">{s.book_title}</Link>
    : <span className="text-muted-foreground">{s.book_title}</span>
  const device = sourceLabel(s.device)
  // The first page fades in with the entry (opacity only: the row's box stays put); later pages fade as they arrive.
  const motion = i < PAGE ? { className: K.fade, style: stagger(i, PAGE) } : { className: LATE_ROW, style: undefined }
  return (
    <div className={cn('border-b', HAIR, ROW_HOVER, motion.className)} style={motion.style}>
      {desktop ? (
        <div data-box={`srow-${i}`} className={cn('grid h-14 items-center gap-x-4', withDate ? COLS_LONGEST : COLS)}>
          <span className="whitespace-nowrap text-[14px] tabular-nums text-muted-foreground" {...(withDate ? tipAttrs({ title: timeOf(s.started_at) }) : {})}>{when}</span>
          <span className="flex min-w-0 items-center gap-3">
            {cover}
            <span className="truncate text-[15px] font-medium text-foreground">{title}</span>
          </span>
          <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[14px] tabular-nums text-foreground">{dur}{warn}</span>
          <span className="truncate text-[14px] text-muted-foreground">{device}</span>
          <span className="whitespace-nowrap text-[13px] text-muted-foreground/70" {...(statusTitle ? tipAttrs({ title: statusTitle }) : {})}>{status}</span>
          <span className="flex justify-end gap-0.5"><SessionTools s={s} a={a} keepSlot /></span>
        </div>
      ) : (
        <div data-box={`srow-${i}`} className="flex h-[62px] items-center gap-2.5">
          {cover}
          <div className="min-w-0 flex-1">
            <div className="truncate text-[15px] font-medium text-foreground">{title}</div>
            <div className="mt-[3px] flex items-center gap-1 truncate whitespace-nowrap text-[12px] tabular-nums text-muted-foreground">
              <span className="truncate">
                {when} · {dur} · {device}
                {status && <> · <span className="text-muted-foreground/70" {...(statusTitle ? tipAttrs({ title: statusTitle }) : {})}>{status}</span></>}
              </span>
              {warn}
            </div>
          </div>
          <span className="flex shrink-0"><SessionTools s={s} a={a} keepSlot buttonClass={PHONE_TOOL} /></span>
        </div>
      )}
      <TrimPanel s={s} a={a} />
    </div>
  )
}

/** The first page before it arrives, in the shape the last one had. */
function Skeleton({ shape, desktop }: { shape: number[]; desktop: boolean }) {
  let k = 0
  // No sessions last time: the place of the line that says so.
  if (!shape.length) return <div className="h-[88px]" />
  return (
    <div className="flex flex-col gap-7">
      {shape.map((n, gi) => (
        <div key={gi}>
          <DayHead i={gi} label={<Ph on>{PH.day}</Ph>} total={<Ph on>{PH.total}</Ph>} />
          {Array.from({ length: n }, () => k++).map((i) => (
            <div key={i} className={cn('border-b', HAIR)}>
              {desktop ? (
                <div data-box={`srow-${i}`} className={cn('grid h-14 items-center gap-x-4', COLS)}>
                  <span className="text-[14px]"><Ph on>{PH.time}</Ph></span>
                  <span className="flex min-w-0 items-center gap-3"><Cover id={null} h={40} className="rounded-[4px]" /><span className="truncate text-[15px]"><Ph on>{PH.title}</Ph></span></span>
                  <span className="text-[14px]"><Ph on>{PH.dur}</Ph></span>
                  <span className="text-[14px]"><Ph on>{PH.dev}</Ph></span>
                  <span className="text-[13px]"><Ph on>{PH.status}</Ph></span>
                  <span />
                </div>
              ) : (
                <div data-box={`srow-${i}`} className="flex h-[62px] items-center gap-2.5">
                  <Cover id={null} h={40} className="rounded-[4px]" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[15px]"><Ph on>{PH.title}</Ph></div>
                    <div className="mt-[3px] truncate text-[12px]"><Ph on>{`${PH.time} · ${PH.dur} · ${PH.dev}`}</Ph></div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

