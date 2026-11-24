// Reading Timeline - your reading life as a horizontal ribbon. One lane per
// series (standalones get their own), named in a frozen left rail; every book
// is a bar spanning its first to last active day, daily ticks inside carrying
// that day's minutes as intensity (one hue, sequential). Data is lifetime and
// reconciled (imported KOReader history included), from GET /stats/timeline.
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { useBookNav, Pop, EASE } from '@/components/motion'
import { PRESS } from '@/components/page'
import { useHeld } from '@/lib/useHeld'
import { Minus, Plus } from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatDate, formatDuration } from '@/lib/utils'
import { useChartColors } from '@/lib/useChartAccent'
import { useAuth } from '@/contexts/AuthContext'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { coverSrc } from '@/lib/covers'

interface TimelineBook {
  book_id: number
  title: string
  author: string | null
  series: string | null
  series_index: number | null
  has_cover: boolean
  finished_on: string | null
  first_day: string
  last_day: string
  total_seconds: number
  days: { date: string; seconds: number }[]
}

interface TimelineResponse {
  books: TimelineBook[]
  today: string
}

const DAY_MS = 86400000
// eslint-disable-next-line lingui/no-unlocalized-strings -- ISO suffix
const dayNum = (d: string) => Math.floor(Date.parse(`${d}T00:00:00Z`) / DAY_MS)
const dayStr = (n: number) => new Date(n * DAY_MS).toISOString().slice(0, 10)

const RAIL_W = 200
const RAIL_W_NARROW = 96 // phones: the full rail would eat half the viewport
const NARROW_BELOW = 480
const AXIS_H = 26
const SUB_H = 22 // one sub-lane (bar row) inside a lane
const BAR_H = 16
const ZOOMS = [1.5, 3, 6, 12] // px per day
const ZOOM_MS = 260

// Every x and width is in days times the CSS variable --ppd (px per day), so a zoom moves one
// variable a frame (style and layout only, no render) instead of re-rendering every bar.
// eslint-disable-next-line lingui/no-unlocalized-strings -- CSS
const px = (days: number) => `calc(var(--ppd) * ${days}px)`

/** The app's curve (EASE, cubic-bezier) at progress p, for the zoom's frame loop. */
function ease(p: number): number {
  const [x1, y1, x2, y2] = EASE
  const at = (a: number, b: number, u: number) => 3 * a * u * (1 - u) ** 2 + 3 * b * u * u * (1 - u) + u ** 3
  let lo = 0, hi = 1, u = p
  for (let i = 0; i < 20; i++) {
    u = (lo + hi) / 2
    if (at(x1, x2, u) < p) lo = u
    else hi = u
  }
  return at(y1, y2, u)
}

interface Placed {
  book: TimelineBook
  sub: number // sub-lane within the group, 0 for almost everything
}

interface LaneGroup {
  key: string
  label: string
  author: string | null
  placed: Placed[]
  subCount: number
  firstDay: number
  lastDay: number
  totalSeconds: number
}

// Group books into series lanes; a lane grows sub-lanes only when two volumes
// were genuinely read in overlapping windows (re-reads, parallel volumes).
function buildLanes(books: TimelineBook[]): LaneGroup[] {
  const groups = new Map<string, TimelineBook[]>()
  for (const b of books) {
    const key = b.series ? `s:${b.series}` : `b:${b.book_id}`
    const g = groups.get(key)
    if (g) g.push(b)
    else groups.set(key, [b])
  }
  const lanes: LaneGroup[] = []
  for (const [key, members] of groups) {
    members.sort((a, z) => (a.first_day < z.first_day ? -1 : 1))
    const subEnds: number[] = []
    const placed: Placed[] = []
    for (const b of members) {
      const start = dayNum(b.first_day)
      const end = dayNum(b.last_day)
      // <= so finishing one volume and starting the next on the same day
      // chains onto one row; only genuinely parallel reads stack.
      let sub = subEnds.findIndex((e) => e <= start)
      if (sub === -1) {
        sub = subEnds.length
        subEnds.push(end)
      } else {
        subEnds[sub] = end
      }
      placed.push({ book: b, sub })
    }
    lanes.push({
      key,
      label: members[0].series ?? members[0].title,
      author: members[0].author,
      placed,
      subCount: subEnds.length,
      firstDay: dayNum(members[0].first_day),
      lastDay: Math.max(...members.map((b) => dayNum(b.last_day))),
      totalSeconds: members.reduce((s, b) => s + b.total_seconds, 0),
    })
  }
  // Most recently active on top: the view opens scrolled to today, so the
  // top-right corner - the first thing seen - is the reading happening now.
  lanes.sort((a, z) => z.lastDay - a.lastDay || z.firstDay - a.firstDay)
  return lanes
}

// Session-lived cache: switching tabs remounts the widget; the ribbon should
// reappear instantly, not re-fetch and flash its loading state every visit.
// Keyed by user id - logout/login and admin impersonation don't full-reload
// the SPA, so an unkeyed cache would flash the previous account's timeline.
let cachedData: TimelineResponse | null = null
let cachedForUser: number | null = null

const ZOOM_KEY = 'tome_timeline_zoom'
const initialZoom = () => {
  const raw = localStorage.getItem(ZOOM_KEY)
  if (raw === null) return 2 // Number(null) is 0 - don't let a fresh browser start zoomed out
  const z = Number(raw)
  return Number.isInteger(z) && z >= 0 && z < ZOOMS.length ? z : 2
}

export function TimelineRibbon({ standalone = false }: { standalone?: boolean } = {}) {
  const { user } = useAuth()
  const cacheHit = user != null && user.id === cachedForUser ? cachedData : null
  const [data, setData] = useState<TimelineResponse | null>(cacheHit)
  const [failed, setFailed] = useState(false)
  const [zoom, setZoomState] = useState(initialZoom)
  // Set by the zoom buttons: the next change of px per day is a zoom (glides, keeps its place).
  const zooming = useRef(false)
  const setZoom = (fn: (z: number) => number) => {
    zooming.current = true
    setZoomState((z) => {
      const next = fn(z)
      localStorage.setItem(ZOOM_KEY, String(next))
      return next
    })
  }
  const [hover, setHover] = useState<{
    b: TimelineBook
    x: number
    y: number
    day: { date: string; seconds: number } | null
  } | null>(null)
  // The card's last content, kept while it fades out.
  const held = useHeld(hover)
  const scrollRef = useRef<HTMLDivElement>(null)
  const colors = useChartColors()
  const openBook = useBookNav()
  // Touch: first tap on a bar shows the tooltip, second tap navigates  - 
  // tap-navigating instantly made "inspect" impossible on phones.
  // eslint-disable-next-line lingui/no-unlocalized-strings -- media query
  const coarse = useMemo(() => window.matchMedia('(pointer: coarse)').matches, [])
  const lastTapRef = useRef<number | null>(null)

  useEffect(() => {
    api
      .get<TimelineResponse>(`/stats/timeline?tz_offset=${new Date().getTimezoneOffset()}`)
      .then((d) => {
        cachedData = d
        cachedForUser = user?.id ?? null
        setData(d)
      })
      .catch(() => {
        if (!cacheHit) setFailed(true)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const lanes = useMemo(() => (data && data.books.length ? buildLanes(data.books) : null), [data])

  // Panel width, live - zooming out floors at fit-width so the ribbon never
  // leaves dead space to the right of today on a wide screen.
  const [containerW, setContainerW] = useState(0)
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setContainerW(el.clientWidth))
    ro.observe(el)
    setContainerW(el.clientWidth)
    return () => ro.disconnect()
  }, [data])

  // Phones get a narrow rail - the full 200px rail ate half the viewport.
  const railW = containerW > 0 && containerW < NARROW_BELOW ? RAIL_W_NARROW : RAIL_W

  const model = useMemo(() => {
    if (!data || !lanes) return null
    const minDay = Math.min(...lanes.map((l) => l.firstDay)) - 2
    const maxDay = dayNum(data.today) + 2
    const fit = containerW > railW + 100 ? (containerW - railW - 1) / (maxDay - minDay) : 0
    const ppd = Math.max(ZOOMS[zoom], fit)
    const months: { d: number; label: string }[] = []
    const first = new Date(minDay * DAY_MS)
    const cur = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 1))
    while (Math.floor(cur.getTime() / DAY_MS) < maxDay) {
      const m = cur.getUTCMonth()
      months.push({
        d: Math.floor(cur.getTime() / DAY_MS) - minDay,
        label: m === 0 ? String(cur.getUTCFullYear()) : cur.toLocaleString(undefined, { month: 'short', timeZone: 'UTC' }),
      })
      cur.setUTCMonth(m + 1)
    }
    // Intensity reference: p90 of daily totals, so one marathon day doesn't
    // flatten every other tick to invisible.
    const daySecs = data.books.flatMap((b) => b.days.map((d) => d.seconds)).sort((a, z) => a - z)
    const ref = daySecs[Math.floor(daySecs.length * 0.9)] || 1
    return {
      minDay,
      ppd,
      days: maxDay - minDay,
      todayD: dayNum(data.today) - minDay,
      months,
      ref,
    }
  }, [data, lanes, zoom, containerW, railW])

  // Land on the recent end - that's where the reading is.
  useEffect(() => {
    const el = scrollRef.current
    if (el && model) el.scrollLeft = el.scrollWidth
  }, [model === null]) // eslint-disable-line react-hooks/exhaustive-deps

  // A zoom glides --ppd from what is on screen to the new value in ZOOM_MS, keeping the day in the
  // middle of the view in the middle (or the recent end at the end). A second zoom starts from
  // wherever the first one is. Other changes (the panel's width) apply at once, as before.
  const contentRef = useRef<HTMLDivElement>(null)
  const shownPpd = useRef<number | null>(null)
  const raf = useRef(0)
  const ppdNow = model?.ppd ?? null
  useLayoutEffect(() => {
    const el = contentRef.current, sc = scrollRef.current
    if (ppdNow == null || !el || !sc) return
    const from = shownPpd.current
    const zoom = zooming.current
    zooming.current = false
    if (from == null || from === ppdNow || !zoom) {
      cancelAnimationFrame(raf.current)
      shownPpd.current = ppdNow
      return
    }
    cancelAnimationFrame(raf.current)
    // Back to what is on screen before anything is read, so the scroll is read at that scale.
    el.style.setProperty('--ppd', String(from))
    const view = sc.clientWidth - railW
    const atEnd = sc.scrollLeft >= sc.scrollWidth - sc.clientWidth - 2
    const day = (sc.scrollLeft + view / 2) / from
    const to = ppdNow
    const show = (v: number) => {
      el.style.setProperty('--ppd', String(v))
      sc.scrollLeft = atEnd ? sc.scrollWidth : day * v - view / 2
      shownPpd.current = v
    }
    // eslint-disable-next-line lingui/no-unlocalized-strings -- media query
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { show(to); return }
    const start = performance.now()
    const step = (now: number) => {
      const p = Math.min(1, (now - start) / ZOOM_MS)
      show(p < 1 ? from + (to - from) * ease(p) : to)
      if (p < 1) raf.current = requestAnimationFrame(step)
    }
    raf.current = requestAnimationFrame(step)
  }, [ppdNow, railW])
  useEffect(() => () => cancelAnimationFrame(raf.current), [])

  if (failed) return <Empty text={t`Couldn't load the timeline.`} />
  if (!data) return <Empty text={t`Loading your reading life…`} pulse />
  if (!model || !lanes) return <Empty text={t`No reading history yet. Sessions and imported KOReader history land here.`} />

  const ppd = model.ppd
  const atFitFloor = ppd > ZOOMS[zoom] + 0.001 // preset already below fit - zooming out changes nothing

  // Resolve the hovered/tapped position to the day under the cursor, so the
  // tooltip answers at day resolution, not just book totals.
  const hoverFor = (b: TimelineBook, clientX: number, clientY: number, barEl: HTMLElement) => {
    const d0 = dayNum(b.first_day)
    const idx = Math.floor((clientX - barEl.getBoundingClientRect().left) / ppd)
    const date = dayStr(d0 + Math.max(0, Math.min(idx, dayNum(b.last_day) - d0)))
    const day = b.days.find((d) => d.date === date) ?? { date, seconds: 0 }
    return { b, x: clientX, y: clientY, day }
  }
  const clearHover = () => {
    setHover(null)
    lastTapRef.current = null
  }

  return (
    // Standalone hugs its content (the page shows background below a short
    // ribbon); as a tile it fills the card body it was given.
    <div className={`relative flex min-h-0 flex-col gap-1.5 ${standalone ? 'h-auto max-h-full' : 'h-full'}`}>
      <div className="flex items-center justify-end gap-1">
        {standalone && (
          <div className="mr-auto flex items-baseline gap-2">
            <h3 className="font-display text-[20px] font-bold leading-6 text-foreground"><Trans>Reading timeline</Trans></h3>
            <span className="text-[13px] text-muted-foreground"><Trans>lifetime</Trans></span>
          </div>
        )}
        <button
          onClick={() => setZoom((z) => Math.max(0, z - 1))}
          disabled={zoom === 0 || atFitFloor}
          aria-label={t`Zoom out`}
          className={cn('touch-feedback grid h-8 w-8 place-items-center rounded-full bg-secondary text-muted-foreground transition-colors hover:bg-surface-hover hover:text-foreground disabled:opacity-40', PRESS)}
        >
          <Minus className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={() => setZoom((z) => Math.min(ZOOMS.length - 1, z + 1))}
          disabled={zoom === ZOOMS.length - 1}
          aria-label={t`Zoom in`}
          className={cn('touch-feedback grid h-8 w-8 place-items-center rounded-full bg-secondary text-muted-foreground transition-colors hover:bg-surface-hover hover:text-foreground disabled:opacity-40', PRESS)}
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-auto"
        onMouseLeave={clearHover}
        onScroll={clearHover}
        onClick={(e) => {
          // Tap on empty ribbon space (not a bar) dismisses the touch tooltip.
          if (!(e.target as HTMLElement).closest('button')) clearHover()
        }}
      >
        <div ref={contentRef} style={{ '--ppd': ppd, width: `calc(${railW}px + var(--ppd) * ${model.days}px)` } as CSSProperties}>
          {/* axis row - sticky against vertical scroll; corner sticky both ways */}
          <div className="sticky top-0 z-30 flex border-b border-border/70 bg-background" style={{ height: AXIS_H }}>
            <div className="sticky left-0 z-10 shrink-0 bg-background" style={{ width: railW }} />
            <div className="relative" style={{ width: px(model.days) }}>
              {model.months
                .filter((m) => m.d * ppd < model.days * ppd - 42)
                .map((m) => (
                  <span
                    key={m.d}
                    className={`absolute top-1.5 pl-1.5 text-[10px] leading-none ${m.label.length === 4 ? 'font-semibold text-foreground' : 'text-muted-foreground'}`}
                    style={{ left: px(m.d) }}
                  >
                    {m.label}
                  </span>
                ))}
            </div>
          </div>

          {lanes.map((lane) => {
            const laneH = Math.max(lane.subCount * SUB_H + 6, 36)
            const padTop = (laneH - lane.subCount * SUB_H) / 2
            return (
              <div key={lane.key} className="flex border-b border-border/70" style={{ height: laneH }}>
                <div
                  className="sticky left-0 z-20 flex shrink-0 flex-col justify-center overflow-hidden bg-background pr-2.5"
                  style={{ width: railW }}
                >
                  <span className="truncate text-[13px] font-medium leading-tight">{lane.label}</span>
                  <span className="truncate text-[12px] leading-tight text-muted-foreground">
                    {lane.placed.length > 1 ? (() => { const n = lane.placed.length; return t`${n} vols · ` })() : ''}
                    {formatDuration(lane.totalSeconds)}
                  </span>
                </div>
                <div className="relative" style={{ width: px(model.days) }}>
                  {/* month grid + today, per lane so the rail stays clean */}
                  {model.months.map((m) => (
                    <div key={m.d} className="absolute bottom-0 top-0 w-px bg-border/40" style={{ left: px(m.d) }} />
                  ))}
                  <div className="absolute bottom-0 top-0 w-px" style={{ left: px(model.todayD), background: colors.accent, opacity: 0.5 }} />

                  {lane.placed.map(({ book: b, sub }) => {
                    const d0 = dayNum(b.first_day)
                    const span = dayNum(b.last_day) - d0 + 1
                    const showIndex = b.series && b.series_index != null && Math.max(span * ppd, 8) >= 22
                    return (
                      <button
                        key={b.book_id}
                        onClick={(e) => {
                          if (coarse && lastTapRef.current !== b.book_id) {
                            // First tap inspects; the second tap opens the book.
                            lastTapRef.current = b.book_id
                            setHover(hoverFor(b, e.clientX, e.clientY, e.currentTarget))
                            return
                          }
                          // The book page paints its hero at once; a bar has no cover to grow.
                          openBook({ id: b.book_id, title: b.title, author: b.author, series: b.series, series_index: b.series_index }, null)
                        }}
                        onMouseMove={(e) => {
                          if (!coarse) setHover(hoverFor(b, e.clientX, e.clientY, e.currentTarget))
                        }}
                        onMouseLeave={() => {
                          if (!coarse) setHover(null)
                        }}
                        aria-label={(() => { const title = b.title; const dur = formatDuration(b.total_seconds); const from = b.first_day; const to = b.last_day; return t`${title}, ${dur} between ${from} and ${to}` })()}
                        className="group absolute block cursor-pointer overflow-hidden rounded-[4px] border transition-transform hover:-translate-y-px"
                        style={{
                          left: px(d0 - model.minDay),
                          top: padTop + sub * SUB_H + (SUB_H - BAR_H) / 2,
                          width: `max(${px(span)}, 8px)`,
                          height: BAR_H,
                          background: `color-mix(in oklab, ${colors.accent} 15%, transparent)`,
                          borderColor: `color-mix(in oklab, ${colors.accent} 40%, transparent)`,
                        }}
                      >
                        {b.days.map((d) => (
                          <span
                            key={d.date}
                            className="absolute bottom-0 top-0"
                            style={{
                              left: px(dayNum(d.date) - d0),
                              width: `max(${px(1)}, 2px)`,
                              background: colors.accent,
                              opacity: 0.3 + 0.65 * Math.min(1, Math.sqrt(d.seconds / model.ref)),
                            }}
                          />
                        ))}
                        {showIndex && (
                          <span className="absolute left-1 top-1/2 -translate-y-1/2 text-[9px] font-semibold leading-none text-foreground/80">
                            {String(b.series_index)}
                          </span>
                        )}
                      </button>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {createPortal(
          // Portaled to <body>: inside a dashboard tile an ancestor transform would
          // re-anchor position:fixed to the tile and trap z-index in its stacking
          // context, putting the tooltip far from the cursor and behind siblings.
          // One card while the pointer moves between bars (it follows, never re-mounts);
          // it fades in once and out when the pointer leaves the bars.
          <Pop
            open={!!hover}
            origin="top-left"
            className="pointer-events-none fixed z-50 flex max-w-[300px] gap-2.5 rounded-lg bg-card px-3 py-2 text-[13px] shadow-lg ring-1 ring-panel-ring"
            style={held ? { left: Math.min(held.x + 14, window.innerWidth - 320), top: Math.min(held.y + 16, window.innerHeight - 110) } : undefined}
          >
            {held && <>
            {held.b.has_cover && (
              <img src={coverSrc(held.b.book_id, { w: 160 })} alt="" className="h-14 w-10 shrink-0 rounded object-cover" />
            )}
            <div className="min-w-0">
              <div className="truncate font-semibold">{held.b.title}</div>
              {held.b.author && <div className="truncate text-muted-foreground">{held.b.author}</div>}
              <div className="mt-1 text-muted-foreground">
                {(() => { const first = formatDate(held.b.first_day), last = formatDate(held.b.last_day); return t`${first} to ${last}` })()} · {plural(held.b.days.length, { one: '# day', other: '# days' })}
              </div>
              <div className="text-muted-foreground">
                {formatDuration(held.b.total_seconds)}
                {held.b.finished_on ? (() => { const d = formatDate(held.b.finished_on); return t` · finished ${d}` })() : ''}
              </div>
              {held.day && (
                <div className="mt-1 border-t border-border/60 pt-1" style={{ color: colors.accent }}>
                  {formatDate(held.day.date)} ·{' '}
                  {held.day.seconds > 0 ? formatDuration(held.day.seconds) : t`no reading`}
                </div>
              )}
            </div>
            </>}
          </Pop>,
          document.body,
        )}
    </div>
  )
}

function Empty({ text, pulse }: { text: string; pulse?: boolean }) {
  return (
    <div className={`flex h-full items-center justify-center text-sm text-muted-foreground ${pulse ? 'animate-pulse' : ''}`}>
      {text}
    </div>
  )
}
