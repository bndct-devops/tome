// Calendar: a month as a proper calendar grid, hairline cells with the
// day number in the corner and the neighbouring months greyed into the corners.
// Each book read that day sits in its cell as an event-style chip (title and
// time, the accent fading with how long), as on a desktop calendar; on a phone
// the cells are too narrow for chips, so a bar under the number carries the
// time instead. One headline number for the month, the streaks as a quiet line
// of text. Clicking a day opens a card with what was read that day (books,
// chapters, pages, pace, when). Its own page on purpose, not a stats tile.
// Data: GET /stats/calendar + /stats/calendar/day, cached for the session and
// asked once per visit; the neighbouring months and days are fetched while
// idle, so a step is usually served from memory.
// Motion: the grid is its own skeleton and fills in place when data lands; a
// month change slides the new grid in from the side it comes from (no fade);
// the selection outline glides from day to day (160 ms, instant on a held
// key); a day change shows the new day at once. Only an answer that is not in
// memory keeps the old month or day on screen, dimmed after 150 ms so a fast
// one shows nothing. Figures of different days never run into each other.
// Nothing fades up from zero inside a painted container, which is what reads
// as flicker. All off under reduced motion.
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Trans, useLingui } from '@lingui/react/macro'
import { plural } from '@lingui/core/macro'
import { AnimatePresence, m } from 'motion/react'
import {
  BookOpen, Check, ChevronLeft, ChevronRight, CircleCheck, Clock, FileText,
  Flame, Gauge, List, Moon, Trophy, X, type LucideIcon,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { BookLink, EASE } from '@/components/motion'
import { Glide, GlideGroup, PageBody, PageHeader, Pill } from '@/components/page'
import { api } from '@/lib/api'
import { cn, formatDuration } from '@/lib/utils'
import type { StreakSummary } from '@/components/stats/shared'
import { AttributedPagesMark } from '@/components/stats/AttributedPagesMark'
import { coverSrc } from '@/lib/covers'
import { firstJsDay, rowOfJsDay } from '@/lib/weekStart'

interface BookRef {
  book_id: number
  title: string
  author: string | null
  series: string | null
  series_index: number | null
  has_cover: boolean
}

interface MonthDay {
  date: string
  seconds: number
  pages: number
  books: (BookRef & { seconds: number })[]
  finished: BookRef[]
}

interface MonthResponse {
  month: string
  today: string
  first_day: string | null
  days: MonthDay[]
  // Days a reader was read on before it synced to Tome: reading days with no time or books.
  reader_days?: string[]
  totals: { seconds: number; pages: number; reading_days: number; books_finished: number; best_day: string | null }
  // Seconds that shade a day at full strength: your 90th-percentile day, all time.
  scale_seconds: number
  streaks: StreakSummary
}

interface DayBook extends BookRef {
  seconds: number
  pages: number
  // Some of the pages were attributed from a CrossInk reader's page counter.
  pages_attributed?: boolean
  progress_from: number | null
  progress_to: number | null
  chapters: string[]
  pages_per_hour: number | null
  usual_pages_per_hour: number | null
  finished: boolean
  started: boolean
  sittings: number
}

interface DayResponse {
  date: string
  seconds: number
  pages: number
  reader_day?: boolean
  books: DayBook[]
  finished_elsewhere: BookRef[]
  sittings: { book_id: number; start: number; end: number }[]
  streak: { day: number; length: number; start: string; end: string; is_best: boolean } | null
  is_month_best: boolean
}

const tzOffset = () => new Date().getTimezoneOffset()
// eslint-disable-next-line lingui/no-unlocalized-strings -- ISO time suffix
const parseDay = (iso: string) => new Date(iso + 'T00:00:00')
const isoOf = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const addDays = (iso: string, n: number) => { const d = parseDay(iso); d.setDate(d.getDate() + n); return isoOf(d) }
const shiftMonth = (m: string, n: number) => { const d = new Date(+m.slice(0, 4), +m.slice(5) - 1 + n, 1); return isoOf(d).slice(0, 7) }
const shortDay = (iso: string) => parseDay(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
const coverUrl = (id: number) => coverSrc(id, { w: 160 })

// Continuous shading by minutes read (no fixed steps), against a fixed personal
// scale (your 90th-percentile day) rather than this month's longest day, so a
// month of equal short days stays light instead of all going full colour.
// Continuous shading by minutes read (no fixed steps), against a fixed personal
// scale (your 90th-percentile day) rather than this month's longest day, so a
// month of equal short days stays short instead of all going full length.
function heat(seconds: number, scale: number): number | null {
  if (!seconds || !scale) return null
  return Math.pow(Math.min(seconds / scale, 1), 0.7)
}

function bookLabel(b: BookRef): string {
  return b.series && b.series_index != null ? `${b.series} #${+b.series_index}` : b.title
}

// Session caches. A month or day seen once paints at once on every later
// visit; each URL is asked once per visit to the page, so an entry from an
// earlier visit is refreshed once, underneath, without dimming anything.
const monthCache = new Map<string, MonthResponse>()
const dayCache = new Map<string, DayResponse>()
const inflight = new Map<string, Promise<unknown>>()
let asked = new Set<string>()
let cacheRev = 0
const cacheSubs = new Set<() => void>()
const subscribeCache = (f: () => void) => { cacheSubs.add(f); return () => { cacheSubs.delete(f) } }
const readCacheRev = () => cacheRev

function ask<T>(path: string, store: (r: T) => void): Promise<unknown> {
  if (asked.has(path)) return inflight.get(path) ?? Promise.resolve()
  asked.add(path)
  const req = api.get<T>(path)
    .then(r => { store(r); cacheRev++; cacheSubs.forEach(f => f()) })
    // A failed answer may be asked again.
    .catch(e => { asked.delete(path); throw e })
    .finally(() => inflight.delete(path))
  inflight.set(path, req)
  return req
}
const loadMonth = (month: string) =>
  ask<MonthResponse>(`/stats/calendar?month=${month}&tz_offset=${tzOffset()}`, r => monthCache.set(month, r))
const loadDay = (day: string) =>
  ask<DayResponse>(`/stats/calendar/day?day=${day}&tz_offset=${tzOffset()}`, r => dayCache.set(day, r))

// Background work waits for an idle moment, so it never competes with a step.
function whenIdle(f: () => void): () => void {
  if ('requestIdleCallback' in window) {
    const id = window.requestIdleCallback(f, { timeout: 800 })
    return () => window.cancelIdleCallback(id)
  }
  const id = setTimeout(f, 120)
  return () => clearTimeout(id)
}

/** The month's latest reading day: the day the desktop panel opens on. */
const latestDay = (r: MonthResponse) => [...r.days].reverse().find(d => d.date.startsWith(r.month))?.date ?? null

/** True once `on` has held for `ms`: a dim that a fast answer never shows. */
function useDelayed(on: boolean, ms: number): boolean {
  const [late, setLate] = useState(false)
  useEffect(() => {
    if (!on) return
    const id = setTimeout(() => setLate(true), ms)
    return () => { clearTimeout(id); setLate(false) }
  }, [on, ms])
  return on && late
}

// One day card for the layout on screen: the desktop panel or the phone sheet.
const WIDE = '(min-width: 1024px)'
const subscribeWide = (f: () => void) => { const q = window.matchMedia(WIDE); q.addEventListener('change', f); return () => q.removeEventListener('change', f) }
const readWide = () => window.matchMedia(WIDE).matches

const DIM_MS = 150
// The selection outline: quick, and instant while an arrow key repeats.
const OUTLINE = { duration: 0.16, ease: EASE }
const OUTLINE_HELD = { duration: 0 }

export function ReadingCalendarPage() {
  // A visit starts with nothing asked: what is cached paints, then refreshes once.
  useState(() => { asked = new Set() })
  const [params, setParams] = useSearchParams()
  useSyncExternalStore(subscribeCache, readCacheRev)
  const wide = useSyncExternalStore(subscribeWide, readWide, () => true)
  const [error, setError] = useState(false)
  const nowMonth = isoOf(new Date()).slice(0, 7)
  const month = /^\d{4}-\d{2}$/.test(params.get('month') ?? '') ? params.get('month')! : nowMonth
  const selected = params.get('day')

  useEffect(() => {
    loadMonth(month).then(() => setError(false), () => { if (!monthCache.has(month)) setError(true) })
  }, [month])

  // While a month that is not in memory loads, the one on screen stays (dimmed
  // after a moment), so nothing flashes empty; the skeleton is only for the very first open.
  const shown = monthCache.get(month) ?? null
  const [painted, setPainted] = useState<MonthResponse | null>(shown)
  if (shown && shown !== painted) setPainted(shown)
  const view = shown ?? painted
  const viewMonth = view?.month ?? month
  const dim = useDelayed(!!view && !shown, DIM_MS)
  // Desktop: open on the latest reading day of the month, so the panel is never empty.
  const panelDay = selected ?? (view ? latestDay(view) : null)
  const today = view?.today ?? isoOf(new Date())
  const firstMonth = view?.first_day?.slice(0, 7) ?? null
  const canPrev = !!firstMonth && firstMonth < month
  const canNext = month < today.slice(0, 7)

  // The months either side, and the day each would open on, while idle.
  const shownMonth = shown?.month
  useEffect(() => {
    if (!shownMonth) return
    return whenIdle(() => {
      const prev = shiftMonth(shownMonth, -1), next = shiftMonth(shownMonth, 1)
      const want = [firstMonth && prev >= firstMonth ? prev : null, next <= today.slice(0, 7) ? next : null]
      for (const mo of want) {
        if (!mo) continue
        loadMonth(mo).then(() => { const r = monthCache.get(mo); const d = r && latestDay(r); if (d) loadDay(d) }, () => {})
      }
    })
  }, [shownMonth, firstMonth, today])

  // Direction of the last month change, for the grid's slide; null until the
  // first change so the first fill does not slide.
  const [dir, setDir] = useState<1 | -1 | null>(null)
  // A held arrow key: the outline jumps with it instead of trailing behind.
  const [held, setHeld] = useState(false)

  function go(next: { month?: string; day?: string | null }, repeat = false) {
    setHeld(repeat)
    const p = new URLSearchParams(params)
    if (next.month && next.month !== month) { setDir(next.month > month ? 1 : -1); p.set('month', next.month) }
    if (next.day === null) p.delete('day')
    else if (next.day) p.set('day', next.day)
    setParams(p, { replace: true })
  }

  // Keyboard: arrows step a day (Shift: a month), T is today, Esc closes the day.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault()
        const step = e.key === 'ArrowRight' ? 1 : -1
        if (e.shiftKey) {
          if (step > 0 ? canNext : canPrev) go({ month: shiftMonth(month, step), day: null })
          return
        }
        const base = selected ?? panelDay
        if (!base) return
        const next = addDays(base, step)
        if (next > today) return
        go({ month: next.slice(0, 7), day: next }, e.repeat)
      } else if (e.key === 't' || e.key === 'T') {
        go({ month: today.slice(0, 7), day: null })
      } else if (e.key === 'Escape' && selected) {
        go({ day: null })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // eslint-disable-next-line lingui/no-unlocalized-strings -- class names
  const slide = dir == null ? '' : dir > 0 ? 'animate-in slide-in-from-right-8 duration-[240ms] ease-(--spring)' : 'animate-in slide-in-from-left-8 duration-[240ms] ease-(--spring)'
  const jump = (iso: string) => go({ month: iso.slice(0, 7), day: iso })
  const closeDay = () => go({ day: null })

  return (
    <AppShell>
      {/* Desktop: the page is one viewport tall; the grid stays put and the day card
          scrolls in its own lane, so a long day never scrolls the calendar away. */}
      <PageBody width="wide" className="lg:h-full lg:pb-6">
        {/* The title follows the month on screen, so it changes together with
            the grid when the new month lands, not on the click. */}
        <MonthNav month={view ? viewMonth : month} isCurrent={month === today.slice(0, 7)} canPrev={canPrev} canNext={canNext}
          onPrev={() => go({ month: shiftMonth(month, -1), day: null })}
          onNext={() => go({ month: shiftMonth(month, 1), day: null })}
          onToday={() => go({ month: today.slice(0, 7), day: null })} />

        {error && <p className="mt-3 text-center text-sm text-muted-foreground"><Trans>Could not load your reading calendar.</Trans></p>}
        {!error && (
          <div className={cn('grid gap-8 transition-opacity duration-150 lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,1fr)_380px] lg:grid-rows-[minmax(0,1fr)]', dim && 'opacity-50')}>
            <div className="min-w-0 space-y-8 lg:min-h-0 lg:overflow-y-auto lg:pb-2">
              {view
                ? <Headline data={view} month={viewMonth} />
                : <div className="h-[88px]" aria-hidden />}
              <div key={`grid-${viewMonth}`} className={cn('motion-reduce:animate-none', slide)}>
                <MonthGrid data={view} month={viewMonth} today={today} selected={selected} fallback={panelDay}
                  held={held} onSelect={d => go({ day: d })} onJump={jump} />
              </div>
            </div>
            <aside className="hidden lg:block lg:min-h-0 lg:overflow-y-auto lg:pb-2">
              {/* One card that stays mounted across month switches: it keeps the
                  last day while the next one loads, and shows the empty-month
                  line in the same frame. */}
              {wide && view ? <DayCard day={panelDay} today={today} /> : null}
            </aside>
          </div>
        )}
      </PageBody>
      <AnimatePresence>
        {!wide && selected && (
          <MobileSheet key="sheet" onClose={closeDay}>
            <DayCard day={selected} today={today} onClose={closeDay} />
          </MobileSheet>
        )}
      </AnimatePresence>
    </AppShell>
  )
}

function MonthNav({ month, isCurrent, canPrev, canNext, onPrev, onNext, onToday }: {
  month: string; isCurrent: boolean; canPrev: boolean; canNext: boolean; onPrev: () => void; onNext: () => void; onToday: () => void
}) {
  const { t } = useLingui()
  const label = new Date(+month.slice(0, 4), +month.slice(5) - 1, 1).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
  return (
    <PageHeader
      eyebrow={<Trans>Calendar</Trans>}
      title={<span className="tabular-nums">{label}</span>}
      actions={
        <>
          {/* Keeps its slot on the current month (faded out, out of the tab
              order), so the arrows never shift when it comes and goes. */}
          <span className={cn('transition-opacity duration-150', isCurrent && 'pointer-events-none opacity-0')} aria-hidden={isCurrent || undefined}>
            <Pill variant="quiet" onClick={onToday} tabIndex={isCurrent ? -1 : undefined}>
              <Trans>Today</Trans>
            </Pill>
          </span>
          <Pill variant="quiet" icon onClick={onPrev} disabled={!canPrev} aria-label={t`Previous month`}><ChevronLeft className="h-5 w-5" /></Pill>
          <Pill variant="quiet" icon onClick={onNext} disabled={!canNext} aria-label={t`Next month`}><ChevronRight className="h-5 w-5" /></Pill>
        </>
      }
    />
  )
}

// One number for the month, the rest as a line of text. No tiles: the page's
// subject is the grid, and four boxed figures above it fought it for attention.
// The figures belong to the month, so a month change shows the new ones as they
// are rather than running from the old month's.
function Headline({ data, month }: { data: MonthResponse; month: string }) {
  const { t } = useLingui()
  const s = data.streaks
  const monthName = new Date(+month.slice(0, 4), +month.slice(5) - 1, 1).toLocaleDateString(undefined, { month: 'long' })
  const days = data.totals.reading_days
  const notes: string[] = []
  if (s.current_days > 0 && s.current_start) {
    const from = shortDay(s.current_start)
    notes.push(plural(s.current_days, { one: `#-day streak since ${from}`, other: `#-day streak since ${from}` }))
  } else if (s.longest_days > 0) {
    notes.push(plural(s.longest_days, { one: 'Best streak # day', other: 'Best streak # days' }))
  }
  if (s.current_weeks > 1) notes.push(plural(s.current_weeks, { one: '#-week streak', other: '#-week streak' }))
  if (data.totals.books_finished > 0) notes.push(plural(data.totals.books_finished, { one: '# book finished', other: '# books finished' }))
  return (
    <div>
      <p className="font-display text-4xl leading-none text-primary tabular-nums sm:text-5xl">{formatDuration(data.totals.seconds)}</p>
      <p className="mt-2 text-sm text-muted-foreground">
        {days > 0
          ? plural(days, { one: `across # reading day in ${monthName}`, other: `across # reading days in ${monthName}` })
          : t`No reading in ${monthName} yet`}
      </p>
      {notes.length > 0 && (
        <p className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-sm text-foreground">
          {notes.map(n => <span key={n}>{n}</span>)}
        </p>
      )}
    </div>
  )
}

// The desktop day card paints its own frame only once it has something to show.
// No fade: it appears whole, and a switch only dims what is there.
const CARD = 'rounded-[22px] bg-card'
// The reader's week order (Settings › Week starts on); 2024-01-01 is a Monday, Dec 31, 2023 a Sunday.
const weekdays = () => Array.from({ length: 7 }, (_, i) => new Date(2024, 0, 1 + i - (firstJsDay() === 0 ? 1 : 0)).toLocaleDateString(undefined, { weekday: 'short' }))
const CELL = 'min-h-14 p-1.5 sm:min-h-24 sm:p-2'
// Weekends shaded, as on a desktop calendar. Opaque on purpose: the grid's
// backdrop is the hairline colour, so a translucent tint would show that
// through and come out far lighter than meant.
const WEEKEND = 'bg-[color-mix(in_oklch,var(--muted)_60%,var(--background))] dark:bg-[color-mix(in_oklch,var(--muted)_38%,var(--background))]'
const cellBg = (col: number) => (col >= 5 ? WEEKEND : 'bg-background')

// With no data yet this draws the empty cells, so the page never shows a
// spinner where the calendar will be; the contents fade in when they land.
function MonthGrid({ data, month, today, selected, fallback, held, onSelect, onJump }: {
  data: MonthResponse | null; month: string; today: string; selected: string | null; fallback: string | null
  held: boolean; onSelect: (d: string) => void; onJump: (d: string) => void
}) {
  const { t } = useLingui()
  const byDay = useMemo(() => new Map((data?.days ?? []).map(d => [d.date, d])), [data])
  const readerDays = useMemo(() => new Set(data?.reader_days ?? []), [data])
  const scale = data?.scale_seconds ?? 0
  // Cell contents appear with the month; nothing fades in from zero.
  const land = ''
  const first = parseDay(`${month}-01`)
  const lead = rowOfJsDay(first.getDay()) // the reader's week, like the weekly streak
  const count = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate()
  const prevMonth = shiftMonth(month, -1), nextMonth = shiftMonth(month, 1)
  const prevCount = new Date(first.getFullYear(), first.getMonth(), 0).getDate()
  const trail = (7 - (lead + count) % 7) % 7
  // Days of the neighbouring months that complete the first and last week; they jump there.
  const outside = (iso: string, n: number, col: number) => (
    <button key={iso} onClick={() => onJump(iso)} disabled={iso > today} aria-label={shortDay(iso)}
      className={cn(CELL, cellBg(col), 'flex flex-col items-stretch gap-1 text-left transition-colors enabled:hover:bg-muted/40 enabled:touch-feedback disabled:cursor-default')}>
      {/* Same wrapper and number box as an in-month day, so the numbers line up across a row. */}
      <span className="flex items-start justify-between gap-1">
        <span className="grid h-6 w-6 place-items-center rounded-full font-display text-xs leading-none text-muted-foreground/70 sm:text-sm">{n}</span>
      </span>
    </button>
  )

  return (
    <div>
      <div className="mb-1.5 grid grid-cols-7">
        {weekdays().map(w => <div key={w} className="pl-1.5 text-[11px] uppercase tracking-wide text-muted-foreground sm:pl-2">{w}</div>)}
      </div>
      {/* One selection outline per month; it glides from day to day within it. */}
      <GlideGroup>
      <div className="grid grid-cols-7 gap-px overflow-hidden rounded-xl border border-border bg-border">
        {Array.from({ length: lead }, (_, i) => { const n = prevCount - lead + 1 + i; return outside(`${prevMonth}-${String(n).padStart(2, '0')}`, n, i) })}
        {Array.from({ length: count }, (_, i) => {
          const iso = `${month}-${String(i + 1).padStart(2, '0')}`
          const day = byDay.get(iso)
          const col = (lead + i) % 7
          const future = iso > today
          const isToday = iso === today
          const isSelected = selected === iso || (!selected && fallback === iso)
          const finishedIds = new Set(day?.finished.map(b => b.book_id))
          const books = day?.books ?? []
          const tt = heat(day?.seconds ?? 0, scale)
          const onReader = !day && readerDays.has(iso)
          const label = [shortDay(iso), day ? formatDuration(day.seconds) : null, onReader ? t`read on your reader` : null,
            finishedIds.size ? t`finished a book` : null].filter(Boolean).join(', ')
          return (
            <button
              key={iso}
              disabled={future}
              onClick={() => onSelect(iso)}
              aria-label={label}
              aria-pressed={isSelected}
              className={cn(
                CELL, cellBg(col),
                'group relative flex flex-col items-stretch gap-1 text-left transition-colors focus-visible:outline-none',
                // The highlights are marked important so they beat the weekend tint,
                // whose theme variant would otherwise sort after them.
                future ? 'cursor-default' : !isSelected && 'hover:bg-muted/50!',
                // Selected: full muted plus an inset hairline, so it stays apart
                // from the weekend tint (which is itself a muted mix) in every theme.
                // The inset hairline is the gliding outline below.
                selected === iso && 'bg-muted!',
                // The desktop panel opens on a default day; only mark it where the panel shows.
                !selected && fallback === iso && 'lg:bg-muted!',
                'focus-visible:bg-muted/70!',
              )}
            >
              {/* A 160 ms glide instead of the spring: it keeps up with a held arrow key. */}
              {isSelected && (
                <Glide layoutId="cal-sel" radius={0} transition={held ? OUTLINE_HELD : OUTLINE}
                  className={cn('z-[1] shadow-[inset_0_0_0_1.5px_color-mix(in_oklch,var(--foreground)_28%,transparent)]', !selected && 'hidden lg:block')} />
              )}
              <span className="flex items-start justify-between gap-1">
                <span className={cn(
                  'grid h-6 w-6 place-items-center rounded-full font-display text-xs leading-none sm:text-sm',
                  future ? 'text-muted-foreground/70' : 'text-foreground',
                  // Today is the accent circle, as on every calendar.
                  isToday && 'bg-primary text-primary-foreground',
                )}>{i + 1}</span>
                {day && <span className={cn('hidden pt-1.5 text-[11px] leading-none text-muted-foreground sm:inline', land)}>{formatDuration(day.seconds)}</span>}
              </span>
              {books.length > 0 && (
                <span className={cn('hidden flex-col gap-1 sm:flex', land)}>
                  {books.slice(0, 2).map(b => {
                    const done = finishedIds.has(b.book_id)
                    return (
                      <span key={b.book_id}
                        className={cn('flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] leading-tight',
                          done ? 'bg-primary text-primary-foreground' : 'bg-primary/15 text-foreground')}>
                        {done && <Check className="h-3 w-3 shrink-0" strokeWidth={3} />}
                        <span className="flex min-w-0 flex-1 gap-1 font-medium" title={`${bookLabel(b)}, ${formatDuration(b.seconds)}`}>
                          <span className="min-w-0 truncate">{b.series && b.series_index != null ? b.series : b.title}</span>
                          {b.series && b.series_index != null && <span className="shrink-0">#{+b.series_index}</span>}
                        </span>
                      </span>
                    )
                  })}
                  {books.length > 2 && (
                    <span className="px-1.5 text-[11px] leading-tight text-muted-foreground">{plural(books.length - 2, { one: '+# more', other: '+# more' })}</span>
                  )}
                </span>
              )}
              {onReader && (
                <>
                  {/* A reading day the reader kept no details for: an outlined chip, no time. */}
                  <span className={cn('hidden items-center gap-1 rounded-md border border-dashed border-primary/40 px-1.5 py-0.5 text-[11px] leading-tight text-muted-foreground sm:flex', land)}>
                    <BookOpen className="h-3 w-3 shrink-0" />
                    <span className="min-w-0 truncate"><Trans>Read</Trans></span>
                  </span>
                  <span className={cn('mt-auto flex h-[3px] sm:hidden', land)} aria-hidden>
                    <span className="h-full w-1/5 rounded-full bg-primary/40" />
                  </span>
                </>
              )}
              {tt != null && (
                <span className={cn('mt-auto flex h-[3px] sm:hidden', land)} aria-hidden>
                  <span className="h-full rounded-full bg-primary" style={{ width: `${Math.round((0.2 + 0.8 * tt) * 100)}%` }} />
                </span>
              )}
            </button>
          )
        })}
        {Array.from({ length: trail }, (_, i) => outside(`${nextMonth}-${String(i + 1).padStart(2, '0')}`, i + 1, (lead + count + i) % 7))}
      </div>
      </GlideGroup>
    </div>
  )
}

// The phone's day sheet: slides up over a fading veil, and back down when closed.
function MobileSheet({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <>
      <m.div className="fixed inset-0 z-40 bg-black/40" onClick={onClose}
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18, ease: EASE }} />
      <m.div role="dialog" aria-modal="true"
        initial={{ y: '100%' }} animate={{ y: 0, transition: { duration: 0.28, ease: EASE } }} exit={{ y: '100%', transition: { duration: 0.2, ease: EASE } }}
        className="fixed inset-x-0 bottom-0 z-50 max-h-[86vh] overflow-y-auto rounded-t-[22px] bg-card pb-[env(safe-area-inset-bottom)] shadow-2xl">
        <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-border" />
        {children}
      </m.div>
    </>
  )
}

// "Chapter 31 to Chapter 44" → "Chapter 31 to 44": collapse a run of numbered
// chapters that share a prefix; otherwise first to last as titled.
function chapterRange(chapters: string[], to: (a: string, b: string) => string): string | null {
  if (chapters.length === 0) return null
  const numbered = chapters.map(c => c.match(/^(.*?)(\d+)\s*$/)).filter((m): m is RegExpMatchArray => !!m)
  const prefix = numbered[0]?.[1]
  if (numbered.length >= 2 && numbered.every(m => m[1] === prefix)) {
    const nums = numbered.map(m => +m[2])
    return `${prefix}${to(String(Math.min(...nums)), String(Math.max(...nums)))}`
  }
  if (chapters.length === 1) return chapters[0]
  return to(chapters[0], chapters[chapters.length - 1])
}

function DayCard({ day: dayProp, today, onClose }: { day: string | null; today: string; onClose?: () => void }) {
  const { t } = useLingui()
  useSyncExternalStore(subscribeCache, readCacheRev)
  const fresh = dayProp ? dayCache.get(dayProp) ?? null : null
  // The last day drawn: held while a day that is not in memory loads.
  const [painted, setPainted] = useState<DayResponse | null>(fresh)
  if (fresh && fresh !== painted) setPainted(fresh)
  useEffect(() => {
    if (!dayProp) return
    loadDay(dayProp).catch(() => {})
    // The days either side, so the next arrow press is served from memory.
    return whenIdle(() => {
      for (const d of [addDays(dayProp, -1), addDays(dayProp, 1)]) if (d <= today) loadDay(d).catch(() => {})
    })
  }, [dayProp, today])
  const data = fresh ?? painted
  // A different day is on its way: keep this one, dimmed after a moment, rather than flashing empty.
  const dim = useDelayed(!!data && !!dayProp && data.date !== dayProp, DIM_MS)
  if (!dayProp) {
    return <p className={cn(CARD, 'px-6 py-16 text-center text-sm text-muted-foreground')}><Trans>No reading this month yet.</Trans></p>
  }
  const day = dayProp

  const title = parseDay(day).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
  const header = (
    <div className="flex items-start justify-between gap-3">
      <div>
        <h2 className="font-display text-xl leading-tight text-foreground">{title}</h2>
        <p className="text-xs text-muted-foreground">{day.slice(0, 4)}</p>
      </div>
      {onClose && (
        <button onClick={onClose} aria-label={t`Close`} className="grid h-8 w-8 place-items-center rounded-full bg-muted text-foreground touch-feedback">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  )
  // In the sheet (onClose set) the sheet is the frame, so the header can show
  // while the day loads; on desktop nothing is drawn until there is a day.
  if (!data) return onClose ? <div className="space-y-4 p-6">{header}</div> : null
  const body = cn('transition-opacity duration-150', dim && 'opacity-40', !onClose && CARD)
  if (data.books.length === 0) {
    const n = data.streak?.day ?? 0, len = data.streak?.length ?? 0
    const streakLine = data.streak?.is_best ? t`Day ${n} of your best streak (${len} days)` : t`Day ${n} of a ${len}-day streak`
    return (
      <div className={cn('space-y-4 p-6', body)}>
        {header}
        {data.reader_day ? (
          <div className="flex flex-col items-center gap-2 py-10 text-center text-sm text-muted-foreground">
            <BookOpen className="h-5 w-5" />
            <Trans>You read on this day. Your reader kept no details from before it synced to Tome.</Trans>
            {data.streak && data.streak.length > 1 && (
              <span className="flex items-center gap-1.5 text-xs">
                <Flame className="h-3.5 w-3.5" />
                {streakLine}
              </span>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 py-10 text-sm text-muted-foreground">
            <Moon className="h-5 w-5" />
            {data.seconds > 0 ? <Trans>Only a brief open this day, under a minute.</Trans> : <Trans>No reading this day.</Trans>}
          </div>
        )}
      </div>
    )
  }

  // Sittings on a 04:00 → 04:00 axis: the reading day, with its 4h rollover.
  const axisStart = new Date(+day.slice(0, 4), +day.slice(5, 7) - 1, +day.slice(8), 4).getTime() / 1000
  const midnight = axisStart + 20 * 3600
  const pos = (e: number) => Math.min(100, Math.max(0, ((e - axisStart) / 86400) * 100))
  const hhmm = (e: number) => new Date(e * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  const bookIdx = new Map(data.books.map((b, i) => [b.book_id, i]))
  const pastMidnight = data.sittings.some(s => s.end > midnight)
  const main = data.books[0]
  const finished = data.books.filter(b => b.finished)
  const started = data.books.filter(b => b.started && !b.finished)

  const quip = (() => {
    const mainTitle = main.title
    // A duration never breaks across lines ("2h / 0m").
    const dur = formatDuration(data.seconds).replace(/ /g, '\u00a0')
    if (finished[0] && started[0]) { const a = finished[0].title, b = started[0].title; return t`Finished ${a} and went straight into ${b}.` }
    if (finished[0]) { const a = finished[0].title; return t`Finished ${a}.` }
    if (data.is_month_best) return t`Your biggest reading day this month: ${dur} with ${mainTitle}.`
    if (pastMidnight) return t`Up past midnight with ${mainTitle}.`
    if (data.sittings.length >= 3) { const n = data.sittings.length; return t`Kept coming back to it: ${n} sittings across the day.` }
    if (data.seconds < 15 * 60 && data.pages > 0) { const p = data.pages; return t`A short visit. ${p} pages still count.` }
    if (started[0]) { const a = started[0].title; return t`Opened ${a} for the first time.` }
    if (main.chapters.length >= 2) { const n = main.chapters.length; return t`${n} chapters of ${mainTitle}.` }
    return t`${dur} with ${mainTitle}.`
  })()

  const badges: { icon: LucideIcon; label: string }[] = []
  if (data.is_month_best) badges.push({ icon: Trophy, label: t`Biggest day this month` })
  if (data.streak && data.streak.length > 1) {
    const n = data.streak.day, len = data.streak.length
    badges.push({ icon: Flame, label: data.streak.is_best ? t`Day ${n} of your best streak (${len} days)` : t`Day ${n} of a ${len}-day streak` })
  }
  for (const b of finished) { const l = bookLabel(b); badges.push({ icon: CircleCheck, label: t`Finished ${l}` }) }
  for (const b of started) { const l = bookLabel(b); badges.push({ icon: BookOpen, label: t`Started ${l}` }) }
  if (pastMidnight) badges.push({ icon: Moon, label: t`Past midnight` })

  // No page count for the day (web reader sessions have none): leave pages
  // and speed out rather than show a 0 that reads like a measurement.
  const hasPages = data.pages > 0
  const pph = data.seconds ? Math.round(data.pages / (data.seconds / 3600)) : 0
  const stats: { n: number; fmt: (n: number) => string; label: string }[] = [
    { n: data.seconds, fmt: formatDuration, label: t`Time` },
    ...(hasPages ? [{ n: data.pages, fmt: (n: number) => n.toLocaleString(), label: t`Pages` }] : []),
    { n: data.sittings.length, fmt: String, label: t`Sittings` },
    ...(hasPages ? [{ n: pph, fmt: (n: number) => t`${n} p/h`, label: t`Speed` }] : []),
  ]
  // eslint-disable-next-line lingui/no-unlocalized-strings -- CSS colours
  const shades = ['var(--primary)', 'color-mix(in oklch, var(--primary) 45%, var(--muted-foreground))', 'var(--muted-foreground)']

  return (
    <div className={cn('space-y-6 p-6', body)}>
      {header}
      <p className="font-display text-lg leading-snug text-foreground">{quip}</p>
      {badges.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {badges.map(b => (
            <span key={b.label} className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs text-foreground">
              <b.icon className="h-3.5 w-3.5 text-primary" />{b.label}
            </span>
          ))}
        </div>
      )}
      <div className="grid grid-cols-4 gap-3">
        {stats.map(s => (
          <div key={s.label} className="min-w-0">
            <p className="whitespace-nowrap font-display text-lg leading-tight text-foreground tabular-nums">{s.fmt(s.n)}</p>
            <p className="truncate text-[11px] text-muted-foreground">{s.label}</p>
          </div>
        ))}
      </div>

      <div>
        <p className="mb-2 text-[11px] uppercase tracking-wide text-muted-foreground"><Trans>What you read</Trans></p>
        <div className="space-y-2">
          {data.books.map(b => <DayBookRow key={b.book_id} b={b} />)}
        </div>
      </div>

      <div>
        <p className="mb-2 text-[11px] uppercase tracking-wide text-muted-foreground"><Trans>When</Trans></p>
        <div className="relative h-7 rounded-full bg-muted">
          {data.sittings.map(s => (
            <span key={`${s.book_id}-${s.start}`} className="absolute top-1.5 bottom-1.5 min-w-[3px] rounded-sm"
              title={`${hhmm(s.start)} - ${hhmm(s.end)}`}
              style={{ left: `${pos(s.start)}%`, width: `${Math.max(0.6, pos(s.end) - pos(s.start))}%`, background: shades[Math.min(bookIdx.get(s.book_id) ?? 0, 2)] }} />
          ))}
        </div>
        <div className="relative mt-1 h-4 text-[10.5px] text-muted-foreground proportional-nums">
          {[2, 8, 14, 20].map(h => (
            <span key={h} className="absolute -translate-x-1/2" style={{ left: `${(h / 24) * 100}%` }}>{hhmm(axisStart + h * 3600)}</span>
          ))}
        </div>
        <p className="mt-1 text-xs text-muted-foreground proportional-nums">
          {data.sittings.map(s => `${hhmm(s.start)}\u00a0(${formatDuration(s.end - s.start).replace(/ /g, '\u00a0')})`).join(', ')}
        </p>
      </div>
    </div>
  )
}

function DayBookRow({ b }: { b: DayBook }) {
  const { t } = useLingui()
  const range = chapterRange(b.chapters, (a, z) => t`${a} to ${z}`)
  const from = b.progress_from != null ? Math.round(b.progress_from * 100) : null
  const to = b.progress_to != null ? Math.round(b.progress_to * 100) : null
  const pace = (() => {
    if (!b.pages_per_hour || !b.usual_pages_per_hour) return null
    const diff = Math.round((b.pages_per_hour / b.usual_pages_per_hour - 1) * 100)
    if (Math.abs(diff) < 8) return t`About your usual pace for this book`
    const n = Math.abs(diff)
    return diff > 0 ? t`${n}% faster than your usual for this book` : t`${n}% slower than your usual for this book`
  })()
  return (
    <BookLink book={{ id: b.book_id, title: b.title, author: b.author, series: b.series, series_index: b.series_index }}
      className="grid grid-cols-[48px_minmax(0,1fr)] gap-3 rounded-2xl bg-background p-3 transition-colors hover:bg-muted/40 touch-feedback">
      {b.has_cover
        ? <span data-cover-id={b.book_id} className="block h-[72px] w-12 overflow-hidden rounded-md shadow"><img src={coverUrl(b.book_id)} alt="" className="h-full w-full object-cover" /></span>
        : <span className="grid h-[72px] w-12 place-items-center rounded-md bg-muted"><BookOpen className="h-4 w-4 text-muted-foreground" /></span>}
      <div className="min-w-0">
        <p className="truncate font-display text-sm text-foreground">{b.title}</p>
        {b.series && <p className="truncate text-xs text-muted-foreground">{b.series}{b.series_index != null && ` #${+b.series_index}`}</p>}
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-foreground tabular-nums">
          <span className="inline-flex items-center gap-1"><Clock className="h-3 w-3 text-muted-foreground" />{formatDuration(b.seconds)}</span>
          {b.pages > 0 && <span className="inline-flex items-center gap-1"><FileText className="h-3 w-3 text-muted-foreground" />{plural(b.pages, { one: '# page', other: '# pages' })}{b.pages_attributed && <AttributedPagesMark includes />}</span>}
          {b.pages > 0 && b.pages_per_hour != null && <span className="inline-flex items-center gap-1"><Gauge className="h-3 w-3 text-muted-foreground" />{(() => { const pph = Math.round(b.pages_per_hour); return t`${pph} p/h` })()}</span>}
          {range && <span className="inline-flex min-w-0 items-center gap-1"><List className="h-3 w-3 shrink-0 text-muted-foreground" /><span className="truncate">{range}</span></span>}
        </div>
        {/* A position that did not move says nothing worth a bar ("0% to 0%"). */}
        {from != null && to != null && (to !== from || b.finished) && (
          <>
            <div className="relative mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
              <span className="absolute inset-y-0 left-0 rounded-full bg-primary/30" style={{ width: `${from}%` }} />
              <span className="absolute inset-y-0 rounded-full bg-primary" style={{ left: `${from}%`, width: `${Math.max(1, to - from)}%` }} />
            </div>
            <div className="mt-0.5 flex justify-between text-[11px] text-muted-foreground tabular-nums">
              <span>{t`${from}% to ${to}%`}</span>
              <span>{b.finished ? t({ message: 'Finished', context: 'reading' }) : (() => { const gain = to - from; return t`+${gain}% today` })()}</span>
            </div>
          </>
        )}
        {pace && <p className="mt-1 text-xs text-muted-foreground">{pace}</p>}
      </div>
    </BookLink>
  )
}
