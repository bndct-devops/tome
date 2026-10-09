// Calendar: a month as a proper calendar grid, hairline cells with the
// day number in the corner and the neighbouring months greyed into the corners.
// Each book read that day sits in its cell as an event-style chip (title and
// time, the accent fading with how long), as on a desktop calendar; on a phone
// the cells are too narrow for chips, so a bar under the number carries the
// time instead. One headline number for the month, the streaks as a quiet line
// of text. Clicking a day opens a card with what was read that day (books,
// chapters, pages, pace, when). Its own page on purpose, not a stats tile.
// Motion: the page arrives as one piece; the grid is its own skeleton and fills
// in place when data lands; a month change slides the grid a few pixels in the
// direction of travel; a day change dims the old card until the new one loads.
// Nothing fades up from zero inside a painted container, which is what reads
// as flicker. All off under reduced motion. Data: GET /stats/calendar + /stats/calendar/day.
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Trans, useLingui } from '@lingui/react/macro'
import { plural } from '@lingui/core/macro'
import {
  BookOpen, Check, ChevronLeft, ChevronRight, CircleCheck, Clock, FileText,
  Flame, Gauge, List, Moon, Trophy, X, type LucideIcon,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { api } from '@/lib/api'
import { cn, formatDuration } from '@/lib/utils'
import type { StreakSummary } from '@/components/stats/shared'

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
  totals: { seconds: number; pages: number; reading_days: number; books_finished: number; best_day: string | null }
  // Seconds that shade a day at full strength: your 90th-percentile day, all time.
  scale_seconds: number
  streaks: StreakSummary
}

interface DayBook extends BookRef {
  seconds: number
  pages: number
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
const coverUrl = (id: number) => `/api/books/${id}/cover`

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

export function ReadingCalendarPage() {
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<MonthResponse | null>(null)
  const [error, setError] = useState(false)
  const nowMonth = isoOf(new Date()).slice(0, 7)
  const month = /^\d{4}-\d{2}$/.test(params.get('month') ?? '') ? params.get('month')! : nowMonth
  const selected = params.get('day')

  useEffect(() => {
    let live = true
    api.get<MonthResponse>(`/stats/calendar?month=${month}&tz_offset=${tzOffset()}`)
      .then(r => { if (live) { setData(r); setError(false) } })
      .catch(() => { if (live) setError(true) })
    return () => { live = false }
  }, [month])

  // Direction of the last month change, for the grid's slide; null until the
  // first change so the first fill does not slide.
  const [dir, setDir] = useState<1 | -1 | null>(null)

  function go(next: { month?: string; day?: string | null }) {
    const p = new URLSearchParams(params)
    if (next.month && next.month !== month) { setDir(next.month > month ? 1 : -1); p.set('month', next.month) }
    if (next.day === null) p.delete('day')
    else if (next.day) p.set('day', next.day)
    setParams(p, { replace: true })
  }

  // While the next month loads, the one on screen stays, dimmed, so nothing
  // flashes empty; the skeleton is only for the very first open.
  const shown = data?.month === month ? data : null
  const view = shown ?? data
  const viewMonth = view?.month ?? month
  const stale = !!view && !shown
  // Desktop: open on the latest reading day of the month, so the panel is never empty.
  const panelDay = selected ?? (view ? [...view.days].reverse().find(d => d.date.startsWith(viewMonth))?.date ?? null : null)
  const today = view?.today ?? isoOf(new Date())
  const canPrev = !!shown?.first_day && shown.first_day.slice(0, 7) < month
  const canNext = month < today.slice(0, 7)

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
        go({ month: next.slice(0, 7), day: next })
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
  const slide = dir == null ? '' : dir > 0 ? 'animate-in slide-in-from-right-2 duration-200 ease-out' : 'animate-in slide-in-from-left-2 duration-200 ease-out'
  const jump = (iso: string) => go({ month: iso.slice(0, 7), day: iso })

  return (
    <AppShell>
      {/* Desktop: the page is one viewport tall; the grid stays put and the day card
          scrolls in its own lane, so a long day never scrolls the calendar away. */}
      <div className="mx-auto max-w-6xl px-4 py-6 animate-in fade-in slide-in-from-bottom-1 duration-300 motion-reduce:animate-none lg:flex lg:h-full lg:flex-col">
        <MonthNav month={month} isCurrent={month === today.slice(0, 7)} canPrev={canPrev} canNext={canNext}
          onPrev={() => go({ month: shiftMonth(month, -1), day: null })}
          onNext={() => go({ month: shiftMonth(month, 1), day: null })}
          onToday={() => go({ month: today.slice(0, 7), day: null })} />

        {error && <p className="mt-10 text-center text-sm text-muted-foreground"><Trans>Could not load your reading calendar.</Trans></p>}
        {!error && (
          <div className={cn('mt-8 grid gap-8 transition-opacity duration-200 lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,1fr)_380px] lg:grid-rows-[minmax(0,1fr)]', stale && 'opacity-50')}>
            <div className="min-w-0 space-y-8 lg:min-h-0 lg:overflow-y-auto lg:pb-2">
              {view
                ? <div key={viewMonth} className="animate-in fade-in duration-300 motion-reduce:animate-none"><Headline data={view} month={viewMonth} /></div>
                : <div className="h-[88px]" aria-hidden />}
              <div key={`grid-${viewMonth}`} className={cn('motion-reduce:animate-none', slide)}>
                <MonthGrid data={view} month={viewMonth} today={today} selected={selected} fallback={panelDay}
                  onSelect={d => go({ day: d })} onJump={jump} />
              </div>
            </div>
            <aside className="hidden lg:block lg:min-h-0 lg:overflow-y-auto lg:pb-2">
              {view && panelDay ? <DayCard day={panelDay} /> : view ? (
                <p className={cn(CARD, 'px-6 py-16 text-center text-sm text-muted-foreground')}><Trans>No reading this month yet.</Trans></p>
              ) : null}
            </aside>
          </div>
        )}
      </div>
      {selected && <MobileSheet onClose={() => go({ day: null })}><DayCard day={selected} onClose={() => go({ day: null })} /></MobileSheet>}
    </AppShell>
  )
}

function MonthNav({ month, isCurrent, canPrev, canNext, onPrev, onNext, onToday }: {
  month: string; isCurrent: boolean; canPrev: boolean; canNext: boolean; onPrev: () => void; onNext: () => void; onToday: () => void
}) {
  const { t } = useLingui()
  const label = new Date(+month.slice(0, 4), +month.slice(5) - 1, 1).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
  // eslint-disable-next-line lingui/no-unlocalized-strings -- class names
  const btn = 'grid h-8 w-8 place-items-center rounded-full text-foreground transition-colors hover:bg-muted disabled:opacity-30 disabled:hover:bg-transparent'
  return (
    <div className="flex flex-wrap items-center gap-2">
      <h1 className="mr-1 font-display text-2xl leading-none text-foreground tabular-nums">{label}</h1>
      <button className={btn} onClick={onPrev} disabled={!canPrev} aria-label={t`Previous month`}><ChevronLeft className="h-4 w-4" /></button>
      <button className={btn} onClick={onNext} disabled={!canNext} aria-label={t`Next month`}><ChevronRight className="h-4 w-4" /></button>
      {!isCurrent && (
        <button onClick={onToday} className="ml-1 rounded-full bg-muted px-3 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted/70">
          <Trans>Today</Trans>
        </button>
      )}
    </div>
  )
}

// One number for the month, the rest as a line of text. No tiles: the page's
// subject is the grid, and four boxed figures above it fought it for attention.
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

// The desktop day card paints its own frame only once it has something to show,
// fading in as a whole; a frame that fills in afterwards reads as a flash.
const CARD = 'rounded-[22px] bg-card animate-in fade-in duration-300 motion-reduce:animate-none'
const WEEKDAYS = Array.from({ length: 7 }, (_, i) => new Date(2024, 0, 1 + i).toLocaleDateString(undefined, { weekday: 'short' })) // Monday-first (2024-01-01 is a Monday)
const CELL = 'min-h-14 p-1.5 sm:min-h-24 sm:p-2'
// Weekends shaded, as on a desktop calendar. Opaque on purpose: the grid's
// backdrop is the hairline colour, so a translucent tint would show that
// through and come out far lighter than meant.
const WEEKEND = 'bg-[color-mix(in_oklch,var(--muted)_60%,var(--background))] dark:bg-[color-mix(in_oklch,var(--muted)_38%,var(--background))]'
const cellBg = (col: number) => (col >= 5 ? WEEKEND : 'bg-background')

// With no data yet this draws the empty cells, so the page never shows a
// spinner where the calendar will be; the contents fade in when they land.
function MonthGrid({ data, month, today, selected, fallback, onSelect, onJump }: {
  data: MonthResponse | null; month: string; today: string; selected: string | null; fallback: string | null
  onSelect: (d: string) => void; onJump: (d: string) => void
}) {
  const { t } = useLingui()
  const byDay = useMemo(() => new Map((data?.days ?? []).map(d => [d.date, d])), [data])
  const scale = data?.scale_seconds ?? 0
  // eslint-disable-next-line lingui/no-unlocalized-strings -- class names
  const land = 'animate-in fade-in duration-300 motion-reduce:animate-none'
  const first = parseDay(`${month}-01`)
  const lead = (first.getDay() + 6) % 7 // Monday-first, like the weekly streak
  const count = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate()
  const prevMonth = shiftMonth(month, -1), nextMonth = shiftMonth(month, 1)
  const prevCount = new Date(first.getFullYear(), first.getMonth(), 0).getDate()
  const trail = (7 - (lead + count) % 7) % 7
  // Days of the neighbouring months that complete the first and last week; they jump there.
  const outside = (iso: string, n: number, col: number) => (
    <button key={iso} onClick={() => onJump(iso)} disabled={iso > today} aria-label={shortDay(iso)}
      className={cn(CELL, cellBg(col), 'flex flex-col items-start text-left transition-colors enabled:hover:bg-muted/40 disabled:cursor-default')}>
      <span className="font-display text-xs leading-none text-muted-foreground/40 sm:text-sm">{n}</span>
    </button>
  )

  return (
    <div>
      <div className="mb-1.5 grid grid-cols-7">
        {WEEKDAYS.map(w => <div key={w} className="pl-1.5 text-[11px] uppercase tracking-wide text-muted-foreground sm:pl-2">{w}</div>)}
      </div>
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
          const label = [shortDay(iso), day ? formatDuration(day.seconds) : null, finishedIds.size ? t`finished a book` : null].filter(Boolean).join(', ')
          return (
            <button
              key={iso}
              disabled={future}
              onClick={() => onSelect(iso)}
              aria-label={label}
              aria-pressed={isSelected}
              className={cn(
                CELL, cellBg(col),
                'group flex flex-col items-stretch gap-1 text-left transition-colors focus-visible:outline-none',
                // The highlights are marked important so they beat the weekend tint,
                // whose theme variant would otherwise sort after them.
                future ? 'cursor-default' : !isSelected && 'hover:bg-muted/50!',
                selected === iso && 'bg-muted/70!',
                // The desktop panel opens on a default day; only mark it where the panel shows.
                !selected && fallback === iso && 'lg:bg-muted/70!',
                'focus-visible:bg-muted/70!',
              )}
            >
              <span className="flex items-start justify-between gap-1">
                <span className={cn(
                  'grid h-6 w-6 place-items-center rounded-full font-display text-xs leading-none sm:text-sm',
                  future ? 'text-muted-foreground/40' : 'text-foreground',
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
    </div>
  )
}

function MobileSheet({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="lg:hidden">
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} />
      <div role="dialog" aria-modal="true"
        className="fixed inset-x-0 bottom-0 z-50 max-h-[86vh] overflow-y-auto rounded-t-[22px] bg-card pb-[env(safe-area-inset-bottom)] shadow-2xl animate-in slide-in-from-bottom-4">
        <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-border" />
        {children}
      </div>
    </div>
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

function DayCard({ day, onClose }: { day: string; onClose?: () => void }) {
  const { t } = useLingui()
  const [data, setData] = useState<DayResponse | null>(null)
  useEffect(() => {
    let live = true
    api.get<DayResponse>(`/stats/calendar/day?day=${day}&tz_offset=${tzOffset()}`).then(r => { if (live) setData(r) }).catch(() => {})
    return () => { live = false }
  }, [day])

  const title = parseDay(day).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
  const header = (
    <div className="flex items-start justify-between gap-3">
      <div>
        <h2 className="font-display text-xl leading-tight text-foreground">{title}</h2>
        <p className="text-xs text-muted-foreground">{day.slice(0, 4)}</p>
      </div>
      {onClose && (
        <button onClick={onClose} aria-label={t`Close`} className="grid h-8 w-8 place-items-center rounded-full bg-muted text-foreground">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  )
  // In the sheet (onClose set) the sheet is the frame, so the header can show
  // while the day loads; on desktop nothing is drawn until there is a day.
  if (!data) return onClose ? <div className="space-y-4 p-6">{header}</div> : null
  // A different day is on its way: keep this one, dimmed, rather than flashing empty.
  const stale = data.date !== day
  const body = cn('transition-opacity duration-200', stale && 'opacity-40', !onClose && CARD)
  if (data.books.length === 0) {
    return (
      <div className={cn('space-y-4 p-6', body)}>
        {header}
        <div className="flex flex-col items-center gap-2 py-10 text-sm text-muted-foreground">
          <Moon className="h-5 w-5" />
          {data.seconds > 0 ? <Trans>Only a brief open this day, under a minute.</Trans> : <Trans>No reading this day.</Trans>}
        </div>
      </div>
    )
  }

  // Sittings on a 04:00 → 04:00 axis: the reading day, with its 4h rollover.
  const axisStart = new Date(+day.slice(0, 4), +day.slice(5, 7) - 1, +day.slice(8), 4).getTime() / 1000
  const midnight = axisStart + 20 * 3600
  const pos = (e: number) => Math.min(100, Math.max(0, ((e - axisStart) / 86400) * 100))
  const hhmm = (e: number) => new Date(e * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  const bookIdx = new Map(data.books.map((b, i) => [b.book_id, i]))
  const pastMidnight = data.sittings.some(s => s.end > midnight)
  const main = data.books[0]
  const finished = data.books.filter(b => b.finished)
  const started = data.books.filter(b => b.started && !b.finished)

  const quip = (() => {
    const mainTitle = main.title
    const dur = formatDuration(data.seconds)
    if (finished[0] && started[0]) { const a = finished[0].title, b = started[0].title; return t`Finished ${a} and went straight into ${b}.` }
    if (finished[0]) { const a = finished[0].title; return t`Finished ${a}.` }
    if (data.is_month_best) return t`Your biggest reading day this month: ${dur} with ${mainTitle}.`
    if (pastMidnight) return t`Up past midnight with ${mainTitle}.`
    if (data.sittings.length >= 3) { const n = data.sittings.length; return t`Kept coming back to it: ${n} sittings across the day.` }
    if (data.seconds < 15 * 60) { const p = data.pages; return t`A short visit. ${p} pages still count.` }
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

  const pph = data.seconds ? Math.round(data.pages / (data.seconds / 3600)) : 0
  const stats = [
    { value: formatDuration(data.seconds), label: t`Time` },
    { value: data.pages.toLocaleString(), label: t`Pages` },
    { value: String(data.sittings.length), label: t`Sittings` },
    { value: t`${pph} p/h`, label: t`Speed` },
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
            <p className="font-display text-lg leading-tight text-foreground tabular-nums">{s.value}</p>
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
        <div className="relative mt-1 h-4 text-[10.5px] text-muted-foreground tabular-nums">
          {[2, 8, 14, 20].map(h => (
            <span key={h} className="absolute -translate-x-1/2" style={{ left: `${(h / 24) * 100}%` }}>{hhmm(axisStart + h * 3600)}</span>
          ))}
        </div>
        <p className="mt-1 text-xs text-muted-foreground tabular-nums">
          {data.sittings.map(s => `${hhmm(s.start)} (${formatDuration(s.end - s.start)})`).join(', ')}
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
    <Link to={`/books/${b.book_id}`} className="grid grid-cols-[48px_minmax(0,1fr)] gap-3 rounded-2xl bg-background p-3 transition-colors hover:bg-muted/40">
      {b.has_cover
        ? <img src={coverUrl(b.book_id)} alt="" className="h-[72px] w-12 rounded-md object-cover shadow" />
        : <span className="grid h-[72px] w-12 place-items-center rounded-md bg-muted"><BookOpen className="h-4 w-4 text-muted-foreground" /></span>}
      <div className="min-w-0">
        <p className="truncate font-display text-sm text-foreground">{b.title}</p>
        {b.series && <p className="truncate text-xs text-muted-foreground">{b.series}{b.series_index != null && ` #${+b.series_index}`}</p>}
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-foreground tabular-nums">
          <span className="inline-flex items-center gap-1"><Clock className="h-3 w-3 text-muted-foreground" />{formatDuration(b.seconds)}</span>
          <span className="inline-flex items-center gap-1"><FileText className="h-3 w-3 text-muted-foreground" />{plural(b.pages, { one: '# page', other: '# pages' })}</span>
          {b.pages_per_hour != null && <span className="inline-flex items-center gap-1"><Gauge className="h-3 w-3 text-muted-foreground" />{(() => { const pph = Math.round(b.pages_per_hour); return t`${pph} p/h` })()}</span>}
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
    </Link>
  )
}
