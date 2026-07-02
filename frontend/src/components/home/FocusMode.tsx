import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Play, RefreshCw, BookOpen } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { CoverImage } from '@/components/CoverImage'

// ── Types ───────────────────────────────────────────────────────────────────

interface FocusBook {
  book_id: number
  title: string
  author: string | null
  series: string | null
  series_index: number | null
  description: string | null
  has_cover: boolean
  progress: number
  device: string | null
  synced: boolean
  last_sync: string | null
}
interface FocusUpcoming {
  book_id: number
  title: string
  series_index: number | null
  has_cover: boolean
  description: string | null
}
interface FocusData {
  ready: boolean
  book: FocusBook | null
  upcoming: FocusUpcoming[]
  ahead_count: number
  reading: { book_id: number; title: string; author: string | null; has_cover: boolean; progress: number }[]
  reading_total?: number
}

/** One cover in the rotary — the current book plus each upcoming volume. */
interface RotaryItem {
  book_id: number
  series_index: number | null
  description: string | null
  isCurrent: boolean
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function relativeTime(iso: string | null): string {
  if (!iso) return ''
  const then = new Date(iso)
  const diff = Math.floor((Date.now() - then.getTime()) / 1000)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`
  if (diff < 7200) return '1 hour ago'
  if (diff < 86400) return `${Math.floor(diff / 3600)} hours ago`
  if (diff < 172800) return 'yesterday'
  // Past a month, "412 days ago" reads worse than the date itself.
  if (diff >= 30 * 86400) {
    return `on ${then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
  }
  return `${Math.floor(diff / 86400)} days ago`
}

function deviceLabel(device: string | null): string {
  if (!device) return 'Synced'
  const d = device.toLowerCase()
  if (d === 'web') return 'Read on the web'
  if (d.includes('kindle')) return 'Synced from Kindle'
  if (d.includes('kobo')) return 'Synced from Kobo'
  if (d.includes('koreader')) return 'Synced from KOReader'
  return `Synced from ${device}`
}

const cover = (id: number) => `/api/books/${id}/cover`
const fmtVol = (n: number | null) => (n == null ? '' : String(n))

// Stage geometry — a fixed, overflow-clipped box so receding covers can never
// bleed into the meta column; the far ones clip cleanly at the stage edge.
const STAGE_W = 540
const STAGE_H = 460
const CARD_W = 214
const CARD_H = CARD_W * 1.5
const CARD_LEFT = 18
// A lone cover (standalone book or last volume in a series) is centered + larger,
// since the fan is the whole composition and there isn't one.
const SOLO_W = 300
const SOLO_H = SOLO_W * 1.5

/** Compute the 3D transform for a cover given its distance from the selected one. */
function slot(offset: number) {
  if (offset === 0) {
    return { x: 0, z: 0, rot: 0, scale: 1, brightness: 1, opacity: 1, zIndex: 100 }
  }
  if (offset > 0) {
    // Upcoming volumes fan back to the right.
    return {
      x: 96 + offset * 60,
      z: -70 * offset,
      rot: -42,
      scale: Math.max(0.5, 0.74 - (offset - 1) * 0.05),
      brightness: Math.max(0.4, 0.92 - (offset - 1) * 0.13),
      opacity: offset <= 5 ? 1 : 0,
      zIndex: 100 - offset,
    }
  }
  // Already-passed volumes slide off to the left and fade out.
  return {
    x: -60 + offset * 36,
    z: -70 * -offset,
    rot: 42,
    scale: 0.7,
    brightness: 0.5,
    opacity: 0,
    zIndex: 100 + offset,
  }
}

// ── Rotary display ─────────────────────────────────────────────────────────────

function Rotary({
  items,
  selected,
  onSelect,
  onOpenHero,
  mounted,
}: {
  items: RotaryItem[]
  selected: number
  onSelect: (i: number) => void
  onOpenHero: () => void
  mounted: boolean
}) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  const [hovered, setHovered] = useState<number | null>(null)

  // Scale the fixed-size stage down to fit narrow screens (mobile), never up.
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const update = () => setScale(Math.min(1, el.clientWidth / STAGE_W))
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const solo = items.length === 1

  return (
    <div ref={wrapRef} className="w-full mx-auto lg:mx-0" style={{ maxWidth: STAGE_W, height: STAGE_H * scale }}>
      <div
        // Clip ONLY the right edge (where the fan recedes toward the text); leave the
        // other three sides open so the hero's bloom shadow isn't sliced into a hard line.
        style={{
          position: 'relative',
          width: STAGE_W,
          height: STAGE_H,
          transform: `scale(${scale})`,
          transformOrigin: 'top left',
          perspective: 1800,
          perspectiveOrigin: '48% 50%',
          clipPath: solo ? 'none' : 'inset(-80px 0px -80px -80px)',
        }}
      >
        {solo ? (
          /* Lone cover — centered + larger, opens the series/book detail. */
          <button
            type="button"
            onClick={onOpenHero}
            className="absolute top-1/2 left-1/2 rounded-xl overflow-hidden cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            style={{
              width: SOLO_W,
              marginTop: -SOLO_H / 2,
              marginLeft: -SOLO_W / 2,
              transform: mounted ? 'scale(1)' : 'scale(.96)',
              opacity: mounted ? 1 : 0,
              boxShadow: 'var(--cover-bloom-hero)',
              transition: 'transform 500ms cubic-bezier(.22,1,.36,1), opacity 450ms ease',
            }}
          >
            {/* CoverImage: a coverless book shows the placeholder, not a broken-image glyph */}
            <div className="relative w-full aspect-[2/3]">
              <CoverImage src={cover(items[0].book_id)} alt="" iconClassName="w-12 h-12" />
            </div>
          </button>
        ) : (
          <div className="absolute top-1/2 left-0" style={{ transform: 'translateY(-50%)', transformStyle: 'preserve-3d' }}>
            {items.map((it, i) => {
              const offset = i - selected
              const s = slot(offset)
              const isHero = offset === 0
              const isHover = hovered === i && !isHero && s.opacity > 0
              // Entrance: covers start stacked at the hero spot, then fan out on mount.
              const tx = mounted ? s.x : 0
              const tz = mounted ? s.z + (isHover ? 40 : 0) : 0
              const rot = mounted ? (isHover ? s.rot + 8 : s.rot) : -16
              const sc = (mounted ? s.scale : isHero ? 1 : 0.94) * (isHover ? 1.05 : 1)
              const op = mounted ? s.opacity : isHero ? 1 : 0
              const bright = mounted ? (isHover ? Math.min(1, s.brightness + 0.3) : s.brightness) : 1
              return (
                <button
                  key={it.book_id}
                  type="button"
                  onClick={() => (isHero ? onOpenHero() : onSelect(i))}
                  onMouseEnter={() => setHovered(i)}
                  onMouseLeave={() => setHovered((h) => (h === i ? null : h))}
                  title={it.series_index != null ? `Volume ${fmtVol(it.series_index)}` : undefined}
                  className="absolute top-1/2 rounded-xl overflow-hidden cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                  style={{
                    left: CARD_LEFT,
                    width: CARD_W,
                    marginTop: -CARD_H / 2,
                    transform: `translate3d(${tx}px, 0, ${tz}px) rotateY(${rot}deg) scale(${sc})`,
                    transformStyle: 'preserve-3d',
                    transformOrigin: 'center center',
                    zIndex: isHover ? 90 : s.zIndex,
                    filter: `brightness(${bright})`,
                    opacity: op,
                    pointerEvents: op === 0 ? 'none' : 'auto',
                    // Soft all-edge bloom; theme-aware (see --cover-bloom-* in index.css):
                    // soft black on light, neutral light on dark where black is invisible.
                    boxShadow: isHero ? 'var(--cover-bloom-hero)' : 'var(--cover-bloom-fan)',
                    transition:
                      'transform 600ms cubic-bezier(.22,1,.36,1), filter 500ms ease, opacity 450ms ease',
                    transitionDelay: mounted ? `${Math.max(0, offset) * 55}ms` : '0ms',
                  }}
                >
                  <div className="relative w-full aspect-[2/3]">
                    <CoverImage src={cover(it.book_id)} alt="" iconClassName="w-10 h-10" />
                  </div>
                  {!isHero && it.series_index != null && (
                    <span className="absolute top-1.5 left-1.5 w-6 h-6 rounded-full bg-black/55 backdrop-blur-sm text-white text-[11px] font-bold grid place-items-center border border-white/20">
                      {fmtVol(it.series_index)}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main ───────────────────────────────────────────────────────────────────────

export function FocusMode() {
  const navigate = useNavigate()
  const [data, setData] = useState<FocusData | null>(null)
  const [selected, setSelected] = useState(0)
  const [mounted, setMounted] = useState(false)
  // null = let the backend auto-pick the latest-synced book; a number focuses a
  // specific in-progress book (the "Also reading" switcher).
  const [focusId, setFocusId] = useState<number | null>(null)

  // (Re)load whenever the focused book changes; replay the entrance each time.
  // Aborted on re-fire: without it, clicking strip book A then quickly book B
  // could let A's slower response land last — hero showing A while focusId is B,
  // and re-clicking A a no-op (switchTo guards on the displayed book id).
  useEffect(() => {
    setMounted(false)
    setSelected(0)
    const ctrl = new AbortController()
    const q = focusId != null ? `?book_id=${focusId}` : ''
    api.get<FocusData>(`/home/focus${q}`, ctrl.signal)
      .then(setData)
      .catch((e) => {
        if ((e as Error).name !== 'AbortError') {
          setData({ ready: false, book: null, upcoming: [], ahead_count: 0, reading: [] })
        }
      })
    return () => ctrl.abort()
  }, [focusId])

  // Kick the entrance animation one frame after data lands.
  useEffect(() => {
    if (data?.ready) {
      const t = setTimeout(() => setMounted(true), 60)
      return () => clearTimeout(t)
    }
  }, [data])

  const switchTo = (id: number) => {
    if (id !== data?.book?.book_id) setFocusId(id)
  }

  if (!data) {
    return <div className="flex items-center justify-center py-40 text-muted-foreground text-sm">Loading…</div>
  }

  if (!data.ready || !data.book) {
    return (
      <div className="flex flex-col items-center justify-center py-40 gap-4 text-center">
        <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center">
          <BookOpen className="w-8 h-8 text-primary/40" />
        </div>
        <div>
          <p className="text-base font-medium text-foreground">Nothing in progress</p>
          <p className="text-sm text-muted-foreground mt-1">Start a book and Focus mode will pick up where you left off</p>
        </div>
        <button
          type="button"
          onClick={() => navigate('/?tab=books')}
          className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          Browse library
        </button>
      </div>
    )
  }

  const b = data.book
  const items: RotaryItem[] = [
    { book_id: b.book_id, series_index: b.series_index, description: b.description, isCurrent: true },
    ...data.upcoming.map((u) => ({ book_id: u.book_id, series_index: u.series_index, description: u.description, isCurrent: false })),
  ]
  const sel = items[selected] ?? items[0]
  const onCurrent = sel.isCurrent

  // The hero cover / series title opens the series detail page (or the book detail
  // page for a standalone title with no series).
  const openHero = () => {
    if (b.series) navigate(`/?tab=series&series_detail=${encodeURIComponent(b.series)}`)
    else navigate(`/books/${b.book_id}`)
  }

  return (
    <div className="flex flex-col min-h-[calc(100vh-160px)]">
      {/* Hero composition — vertically centered so the empty space is balanced */}
      <div className="flex-1 flex items-center w-full">
      <div className="flex flex-col gap-8 lg:flex-row lg:items-center lg:justify-center lg:gap-14 w-full max-w-[1080px] mx-auto">
      {/* Display — animated rotary */}
      <div className="w-full lg:w-auto lg:shrink-0">
        <Rotary items={items} selected={selected} onSelect={setSelected} onOpenHero={openHero} mounted={mounted} />
      </div>

      {/* Meta — reflects the selected volume */}
      <div className="relative z-10 flex-1 min-w-0 max-w-[520px]">
        <div className="flex items-center gap-2 text-[12px] font-bold tracking-[0.12em] uppercase text-muted-foreground">
          {onCurrent ? 'Continue reading' : 'Up next'}
          {b.series && (
            <button type="button" onClick={openHero} className="text-primary hover:underline">
              · {b.series}
            </button>
          )}
        </div>

        <h1 className="font-display text-3xl sm:text-4xl font-bold leading-[1.05] tracking-tight mt-3 text-foreground">
          <button type="button" onClick={openHero} className="text-left hover:underline decoration-2 underline-offset-4">
            {b.series ?? b.title}
          </button>
        </h1>

        {b.series && sel.series_index != null && (
          <div className="mt-2.5 text-[15px] font-semibold text-foreground">
            Volume <span className="text-primary">{fmtVol(sel.series_index)}</span>
            {onCurrent && data.ahead_count > 0 && (
              <span className="text-muted-foreground font-medium"> · {data.ahead_count} ahead in this series</span>
            )}
          </div>
        )}
        {b.author && <div className="mt-1 text-[15px] text-muted-foreground">{b.author}</div>}

        {/* Description swaps with the selected volume. */}
        <p key={sel.book_id} className="mt-5 text-[15px] leading-relaxed text-muted-foreground line-clamp-4 min-h-[6rem] animate-[fadeIn_350ms_ease]">
          {sel.description || 'No description yet for this volume.'}
        </p>

        {/* Progress — only the in-progress current book has it. A book that's
            "reading" but at 0% reads better as "Just started" than an empty bar. */}
        {onCurrent ? (
          b.progress > 0 ? (
            <div className="mt-6">
              <div className="flex items-baseline justify-between mb-2">
                <span className="text-[13px] text-muted-foreground">Progress</span>
                <span className="text-base font-bold tabular-nums text-foreground">{Math.round(b.progress)}%</span>
              </div>
              <div className="h-[7px] rounded-full bg-muted overflow-hidden">
                <div className="h-full rounded-full bg-primary transition-all duration-500" style={{ width: `${b.progress}%` }} />
              </div>
            </div>
          ) : (
            <div className="mt-6 text-[13px] font-medium text-muted-foreground">Just started</div>
          )
        ) : (
          <div className="mt-6 text-[13px] text-muted-foreground">Not started yet</div>
        )}

        {/* Sync chip — the standout beat, current book only. */}
        {onCurrent && b.last_sync && (
          <div className="mt-5 inline-flex items-center gap-2 rounded-xl bg-primary/12 border border-primary/25 px-3.5 py-2.5 text-[14px] font-semibold text-foreground">
            <RefreshCw className="w-[15px] h-[15px] text-primary" />
            {deviceLabel(b.device)}
            <span className="text-muted-foreground font-medium">· {relativeTime(b.last_sync)}</span>
          </div>
        )}

        {/* Actions */}
        <div className="mt-6 flex items-center gap-4">
          <button
            type="button"
            onClick={() => navigate(`/reader/${sel.book_id}`)}
            className="inline-flex items-center gap-2 rounded-xl bg-primary text-primary-foreground px-6 py-3.5 text-[16px] font-bold hover:bg-primary/90 transition-colors"
          >
            <Play className="w-[17px] h-[17px] fill-current" />
            {onCurrent ? 'Resume reading' : 'Start reading'}
          </button>
          {onCurrent && items.length > 1 ? (
            <button
              type="button"
              onClick={() => setSelected(1)}
              className="text-[14px] font-semibold text-muted-foreground hover:text-foreground transition-colors"
            >
              Next: Vol {fmtVol(items[1].series_index)} →
            </button>
          ) : !onCurrent ? (
            <button
              type="button"
              onClick={() => setSelected(0)}
              className="text-[14px] font-semibold text-muted-foreground hover:text-foreground transition-colors"
            >
              ← Back to current
            </button>
          ) : null}
        </div>
      </div>
      </div>
      </div>{/* /hero composition */}

      {/* Currently reading — a STATIC filmstrip of all in-progress books; the
          active one is highlighted in place, so switching never reshuffles it. */}
      {data.reading.length > 1 && (
        <div className="w-full max-w-[1080px] mx-auto pt-8 mt-2 border-t border-border/60">
          <div className="text-[12px] font-bold tracking-[0.12em] uppercase text-muted-foreground mb-3">
            Currently reading
          </div>
          {/* px/py padding gives the active ring room — overflow-x-auto would
              otherwise clip the ring at the container's edges. */}
          <div className="flex gap-3 overflow-x-auto px-1 py-2 -mx-1">
            {data.reading.map((r) => {
              const active = r.book_id === b.book_id
              return (
                <button
                  key={r.book_id}
                  type="button"
                  onClick={() => !active && switchTo(r.book_id)}
                  title={r.title}
                  className="group shrink-0 w-[64px] text-left"
                  aria-current={active}
                >
                  <div
                    className={cn(
                      'relative w-[64px] aspect-[2/3] rounded-lg overflow-hidden bg-muted transition',
                      active
                        ? 'ring-2 ring-primary'
                        : 'ring-1 ring-border opacity-70 group-hover:opacity-100 group-hover:ring-primary/60 cursor-pointer'
                    )}
                  >
                    <CoverImage src={cover(r.book_id)} alt="" iconClassName="w-5 h-5" />
                    {r.progress > 0 && (
                      <div className="absolute inset-x-0 bottom-0 h-[3px] bg-black/35">
                        <div className="h-full bg-primary" style={{ width: `${r.progress}%` }} />
                      </div>
                    )}
                  </div>
                  <div
                    className={cn(
                      'mt-1.5 text-[11px] truncate transition-colors',
                      active ? 'text-foreground font-semibold' : 'text-muted-foreground group-hover:text-foreground'
                    )}
                  >
                    {r.title}
                  </div>
                </button>
              )
            })}
            {(data.reading_total ?? data.reading.length) > data.reading.length && (
              <button
                type="button"
                onClick={() => navigate('/?tab=books&reading_status=reading')}
                className="shrink-0 w-[64px] self-stretch"
                title="See all in-progress books"
              >
                <div className="w-[64px] aspect-[2/3] rounded-lg ring-1 ring-border bg-muted/60 grid place-items-center text-xs font-semibold text-muted-foreground hover:text-foreground hover:ring-primary/60 transition">
                  +{(data.reading_total ?? 0) - data.reading.length}
                </div>
                <div className="mt-1.5 text-[11px] text-muted-foreground text-left truncate">more</div>
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
