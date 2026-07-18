// Reading Timeline — your reading life as a horizontal ribbon. One lane per
// series (standalones get their own), named in a frozen left rail; every book
// is a bar spanning its first to last active day, daily ticks inside carrying
// that day's minutes as intensity (one hue, sequential). Data is lifetime and
// reconciled (imported KOReader history included), from GET /stats/timeline.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Minus, Plus } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate, formatDuration } from '@/lib/utils'
import { useChartColors } from '@/lib/useChartAccent'
import { useAuth } from '@/contexts/AuthContext'

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
const dayNum = (d: string) => Math.floor(Date.parse(`${d}T00:00:00Z`) / DAY_MS)
const dayStr = (n: number) => new Date(n * DAY_MS).toISOString().slice(0, 10)

const RAIL_W = 200
const RAIL_W_NARROW = 96 // phones: the full rail would eat half the viewport
const NARROW_BELOW = 480
const AXIS_H = 26
const SUB_H = 22 // one sub-lane (bar row) inside a lane
const BAR_H = 16
const ZOOMS = [1.5, 3, 6, 12] // px per day

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
  // top-right corner — the first thing seen — is the reading happening now.
  lanes.sort((a, z) => z.lastDay - a.lastDay || z.firstDay - a.firstDay)
  return lanes
}

// Session-lived cache: switching tabs remounts the widget; the ribbon should
// reappear instantly, not re-fetch and flash its loading state every visit.
// Keyed by user id — logout/login and admin impersonation don't full-reload
// the SPA, so an unkeyed cache would flash the previous account's timeline.
let cachedData: TimelineResponse | null = null
let cachedForUser: number | null = null

const ZOOM_KEY = 'tome_timeline_zoom'
const initialZoom = () => {
  const raw = localStorage.getItem(ZOOM_KEY)
  if (raw === null) return 2 // Number(null) is 0 — don't let a fresh browser start zoomed out
  const z = Number(raw)
  return Number.isInteger(z) && z >= 0 && z < ZOOMS.length ? z : 2
}

export function TimelineRibbon({ standalone = false }: { standalone?: boolean } = {}) {
  const { user } = useAuth()
  const cacheHit = user != null && user.id === cachedForUser ? cachedData : null
  const [data, setData] = useState<TimelineResponse | null>(cacheHit)
  const [failed, setFailed] = useState(false)
  const [zoom, setZoomState] = useState(initialZoom)
  const setZoom = (fn: (z: number) => number) =>
    setZoomState((z) => {
      const next = fn(z)
      localStorage.setItem(ZOOM_KEY, String(next))
      return next
    })
  const [hover, setHover] = useState<{
    b: TimelineBook
    x: number
    y: number
    day: { date: string; seconds: number } | null
  } | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const colors = useChartColors()
  const navigate = useNavigate()
  // Touch: first tap on a bar shows the tooltip, second tap navigates —
  // tap-navigating instantly made "inspect" impossible on phones.
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

  // Panel width, live — zooming out floors at fit-width so the ribbon never
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

  // Phones get a narrow rail — the full 200px rail ate half the viewport.
  const railW = containerW > 0 && containerW < NARROW_BELOW ? RAIL_W_NARROW : RAIL_W

  const model = useMemo(() => {
    if (!data || !lanes) return null
    const minDay = Math.min(...lanes.map((l) => l.firstDay)) - 2
    const maxDay = dayNum(data.today) + 2
    const fit = containerW > railW + 100 ? (containerW - railW - 1) / (maxDay - minDay) : 0
    const ppd = Math.max(ZOOMS[zoom], fit)
    const months: { x: number; label: string }[] = []
    const first = new Date(minDay * DAY_MS)
    const cur = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 1))
    while (Math.floor(cur.getTime() / DAY_MS) < maxDay) {
      const m = cur.getUTCMonth()
      months.push({
        x: (Math.floor(cur.getTime() / DAY_MS) - minDay) * ppd,
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
      width: (maxDay - minDay) * ppd,
      todayX: (dayNum(data.today) - minDay) * ppd,
      months,
      ref,
    }
  }, [data, lanes, zoom, containerW, railW])

  // Land on the recent end — that's where the reading is.
  useEffect(() => {
    const el = scrollRef.current
    if (el && model) el.scrollLeft = el.scrollWidth
  }, [model === null]) // eslint-disable-line react-hooks/exhaustive-deps

  if (failed) return <Empty text="Couldn't load the timeline." />
  if (!data) return <Empty text="Loading your reading life…" pulse />
  if (!model || !lanes) return <Empty text="No reading history yet — sessions and imported KOReader history land here." />

  const ppd = model.ppd
  const atFitFloor = ppd > ZOOMS[zoom] + 0.001 // preset already below fit — zooming out changes nothing

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
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Reading Timeline</h3>
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">lifetime</span>
          </div>
        )}
        <button
          onClick={() => setZoom((z) => Math.max(0, z - 1))}
          disabled={zoom === 0 || atFitFloor}
          aria-label="Zoom out"
          className="rounded-md border border-border p-1 text-muted-foreground hover:text-foreground disabled:opacity-40"
        >
          <Minus className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={() => setZoom((z) => Math.min(ZOOMS.length - 1, z + 1))}
          disabled={zoom === ZOOMS.length - 1}
          aria-label="Zoom in"
          className="rounded-md border border-border p-1 text-muted-foreground hover:text-foreground disabled:opacity-40"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-auto rounded-lg border border-border"
        onMouseLeave={clearHover}
        onScroll={clearHover}
        onClick={(e) => {
          // Tap on empty ribbon space (not a bar) dismisses the touch tooltip.
          if (!(e.target as HTMLElement).closest('button')) clearHover()
        }}
      >
        <div style={{ width: railW + model.width }}>
          {/* axis row — sticky against vertical scroll; corner sticky both ways */}
          <div className="sticky top-0 z-30 flex border-b border-border bg-card" style={{ height: AXIS_H }}>
            <div className="sticky left-0 z-10 shrink-0 border-r border-border bg-card" style={{ width: railW }} />
            <div className="relative" style={{ width: model.width }}>
              {model.months
                .filter((m) => m.x < model.width - 42)
                .map((m) => (
                  <span
                    key={m.x}
                    className={`absolute top-1.5 pl-1.5 text-[10px] leading-none ${m.label.length === 4 ? 'font-semibold text-foreground' : 'text-muted-foreground'}`}
                    style={{ left: m.x }}
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
              <div key={lane.key} className="flex border-b border-border/60" style={{ height: laneH }}>
                <div
                  className="sticky left-0 z-20 flex shrink-0 flex-col justify-center overflow-hidden border-r border-border bg-card px-2.5"
                  style={{ width: railW }}
                >
                  <span className="truncate text-[11px] font-medium leading-tight">{lane.label}</span>
                  <span className="truncate text-[10px] leading-tight text-muted-foreground">
                    {lane.placed.length > 1 ? `${lane.placed.length} vols · ` : ''}
                    {formatDuration(lane.totalSeconds)}
                  </span>
                </div>
                <div className="relative" style={{ width: model.width }}>
                  {/* month grid + today, per lane so the rail stays clean */}
                  {model.months.map((m) => (
                    <div key={m.x} className="absolute bottom-0 top-0 w-px bg-border/60" style={{ left: m.x }} />
                  ))}
                  <div className="absolute bottom-0 top-0 w-px" style={{ left: model.todayX, background: colors.accent, opacity: 0.5 }} />

                  {lane.placed.map(({ book: b, sub }) => {
                    const d0 = dayNum(b.first_day)
                    const x = (d0 - model.minDay) * ppd
                    const barW = Math.max((dayNum(b.last_day) - d0 + 1) * ppd, 8)
                    const showIndex = b.series && b.series_index != null && barW >= 22
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
                          navigate(`/books/${b.book_id}`)
                        }}
                        onMouseMove={(e) => {
                          if (!coarse) setHover(hoverFor(b, e.clientX, e.clientY, e.currentTarget))
                        }}
                        onMouseLeave={() => {
                          if (!coarse) setHover(null)
                        }}
                        aria-label={`${b.title} — ${formatDuration(b.total_seconds)} between ${b.first_day} and ${b.last_day}`}
                        className="group absolute block cursor-pointer overflow-hidden rounded-[4px] border transition-transform hover:-translate-y-px"
                        style={{
                          left: x,
                          top: padTop + sub * SUB_H + (SUB_H - BAR_H) / 2,
                          width: barW,
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
                              left: (dayNum(d.date) - d0) * ppd,
                              width: Math.max(ppd, 2),
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

      {hover && (
        <div
          className="pointer-events-none fixed z-50 flex max-w-[300px] gap-2.5 rounded-lg border border-border bg-card px-3 py-2 text-xs shadow-xl"
          style={{ left: Math.min(hover.x + 14, window.innerWidth - 320), top: Math.min(hover.y + 16, window.innerHeight - 110) }}
        >
          {hover.b.has_cover && (
            <img src={`/api/books/${hover.b.book_id}/cover`} alt="" className="h-14 w-10 shrink-0 rounded object-cover" />
          )}
          <div className="min-w-0">
            <div className="truncate font-semibold">{hover.b.title}</div>
            {hover.b.author && <div className="truncate text-muted-foreground">{hover.b.author}</div>}
            <div className="mt-1 text-muted-foreground">
              {formatDate(hover.b.first_day)} – {formatDate(hover.b.last_day)} · {hover.b.days.length}{' '}
              {hover.b.days.length === 1 ? 'day' : 'days'}
            </div>
            <div className="text-muted-foreground">
              {formatDuration(hover.b.total_seconds)}
              {hover.b.finished_on ? ` · finished ${formatDate(hover.b.finished_on)}` : ''}
            </div>
            {hover.day && (
              <div className="mt-1 border-t border-border/60 pt-1" style={{ color: colors.accent }}>
                {formatDate(hover.day.date)} ·{' '}
                {hover.day.seconds > 0 ? formatDuration(hover.day.seconds) : 'no reading'}
              </div>
            )}
          </div>
        </div>
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
