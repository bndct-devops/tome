// The goal dialog's two pickers, in the app's own look (no native controls):
// a book or series to finish (a searchable list) and a day (a month grid in the
// reader's week order). Both open as a card under their trigger, portalled above
// the dialog, and close on a pick, a click outside or a scroll of the page.
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { t } from '@lingui/core/macro'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { monthGrid, type WeekStart } from '@/lib/goalsModel'
import { Cover } from '@/components/stats/kit/Kit'
import { num } from '@/components/stats/kit/tokens'
import { useStatsGet } from '@/components/stats/kit/useStatsGet'
import { longDate, monthName, weekdayLetter } from './text'
import { targetKey, type FinishTarget } from './pickerModel'

function usePlace(open: boolean, trigger: RefObject<HTMLElement | null>, card: RefObject<HTMLElement | null>, onClose: () => void) {
  const [pos, setPos] = useState<{ left: number; top: number; width: number } | null>(null)
  useLayoutEffect(() => {
    if (!open || !trigger.current || !card.current) return
    const r = trigger.current.getBoundingClientRect()
    const c = card.current
    const vw = document.documentElement.clientWidth, vh = window.innerHeight
    const width = Math.min(Math.max(r.width, 280), vw - 16)
    const up = r.bottom + 6 + c.offsetHeight > vh - 8 && r.top - 6 - c.offsetHeight > 8
    const left = Math.min(Math.max(8, r.left), vw - width - 8)
    // Placed from the measured trigger and card before paint, as ChoiceMenu does (its refs are its own).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPos({ left, top: up ? r.top - 6 - c.offsetHeight : r.bottom + 6, width })
  }, [open, trigger, card])
  useEffect(() => {
    if (!open) return
    const down = (e: PointerEvent) => {
      const n = e.target as Node
      if (!card.current?.contains(n) && !trigger.current?.contains(n)) onClose()
    }
    const resize = () => onClose()
    document.addEventListener('pointerdown', down, true)
    window.addEventListener('resize', resize)
    return () => { document.removeEventListener('pointerdown', down, true); window.removeEventListener('resize', resize) }
  }, [open, trigger, card, onClose])
  return pos
}

function Card({ cardRef, pos, children, label, wide }: { cardRef: RefObject<HTMLDivElement | null>; pos: { left: number; top: number; width: number } | null; children: ReactNode; label: string; wide?: boolean }) {
  return createPortal(
    <div
      ref={cardRef}
      role="dialog"
      aria-label={label}
      className="fixed z-[60] rounded-xl bg-card p-1.5 shadow-lg ring-1 ring-panel-ring"
      style={pos ? { left: pos.left, top: pos.top, width: wide ? pos.width : undefined } : { left: 0, top: 0, visibility: 'hidden' }}
    >
      {children}
    </div>,
    document.body,
  )
}

// ── Book or series ────────────────────────────────────────────────────────────

type SeriesRow = { name: string; book_count: number; cover_book_id: number | null; read_count: number; reading_count: number }
type BookRow = { id: number; title: string; series: string | null; cover_path: string | null; reading_status?: string | null }

/** The trigger's content: cover, name and what it is. */
function TargetLabel({ x }: { x: FinishTarget }) {
  const name = 'series' in x ? x.series : x.title
  return (
    <span className="flex min-w-0 items-center gap-2.5">
      <Cover id={x.cover} h={30} className="rounded-[4px]" />
      <span className="min-w-0 truncate">{name}</span>
      <span className="shrink-0 text-[13px] font-normal text-muted-foreground">
        {'series' in x ? (() => { const r = num(x.read), n = num(x.total); return t`series · ${r} of ${n} read` })() : t`book`}
      </span>
    </span>
  )
}

export function FinishTargetPicker({ value, onPick, taken }: { value: FinishTarget | null; onPick: (x: FinishTarget) => void; taken: Set<string> }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const trigger = useRef<HTMLButtonElement>(null)
  const card = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const close = () => { setOpen(false); setQ('') }
  const pos = usePlace(open, trigger, card, close)
  const { data: series } = useStatsGet<SeriesRow[]>('/books/series')
  const { data: reading } = useStatsGet<BookRow[]>('/books?reading_status=reading&sort=status_updated&order=desc&limit=30')
  const [found, setFound] = useState<BookRow[]>([])
  useEffect(() => {
    if (q.trim().length < 2) { setFound([]); return }
    const id = window.setTimeout(() => {
      api.get<BookRow[]>(`/books?q=${encodeURIComponent(q.trim())}&limit=12`).then(setFound).catch(() => setFound([]))
    }, 200)
    return () => window.clearTimeout(id)
  }, [q])
  useEffect(() => { if (open) input.current?.focus({ preventScroll: true }) }, [open, pos])

  const options = useMemo(() => {
    const term = q.trim().toLowerCase()
    const rows = (series ?? []).filter((s) => s.name !== '__unserialized__' && s.read_count < s.book_count)
    const ser: FinishTarget[] = rows
      .filter((s) => !term || s.name.toLowerCase().includes(term))
      // Series being read first (a volume in progress, or some read), then the rest by name.
      .sort((a, b) => Number(b.reading_count > 0) - Number(a.reading_count > 0) || Number(b.read_count > 0) - Number(a.read_count > 0) || a.name.localeCompare(b.name))
      .slice(0, term ? 20 : 12)
      .map((s) => ({ series: s.name, cover: s.cover_book_id, read: s.read_count, total: s.book_count }))
    const bookRows = term ? found : (reading ?? [])
    const books: FinishTarget[] = bookRows.slice(0, 10).map((b) => ({ bookId: b.id, title: b.title, cover: b.cover_path ? b.id : null }))
    return [...ser.map((x) => ({ x, group: 'series' as const })), ...books.map((x) => ({ x, group: 'book' as const }))]
  }, [series, reading, found, q])
  useEffect(() => { setActive(0) }, [q])

  const pick = (x: FinishTarget) => { if (taken.has(targetKey(x))) return; onPick(x); close(); trigger.current?.focus() }
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(options.length - 1, a + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)) }
    else if (e.key === 'Enter') { e.preventDefault(); const o = options[active]; if (o) pick(o.x) }
    else if (e.key === 'Tab') { close() }
  }
  useEffect(() => { card.current?.querySelector(`[data-i="${active}"]`)?.scrollIntoView({ block: 'nearest' }) }, [active])

  return (
    <>
      <button
        ref={trigger}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={t`Book or series`}
        onClick={() => (open ? close() : setOpen(true))}
        className="inline-flex h-11 w-full items-center justify-between gap-3 rounded-xl bg-secondary px-3 text-[14px] font-medium text-foreground transition-colors hover:bg-surface-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {value ? <TargetLabel x={value} /> : <span className="text-muted-foreground">{t`Pick a book or a series`}</span>}
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      </button>
      {open && (
        <Card cardRef={card} pos={pos} label={t`Book or series`} wide>
          <div className="flex items-center gap-2 rounded-lg bg-secondary px-2.5">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <input
              ref={input}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={onKey}
              placeholder={t`Search series and books`}
              aria-label={t`Search series and books`}
              role="combobox"
              aria-expanded
              aria-controls="goal-target-list"
              aria-activedescendant={options[active] ? `goal-target-${active}` : undefined}
              className="h-9 min-w-0 flex-1 bg-transparent text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none"
            />
          </div>
          <div id="goal-target-list" role="listbox" aria-label={t`Book or series`} className="mt-1.5 max-h-[300px] overflow-y-auto overscroll-contain [scrollbar-width:thin]">
            {options.length === 0 && <p className="px-3 py-3 text-[13px] text-muted-foreground">{series ? t`Nothing found.` : t`Loading…`}</p>}
            {options.map((o, i) => {
              const k = targetKey(o.x)
              const sel = value != null && targetKey(value) === k
              const has = taken.has(k)
              const first = i === 0 || options[i - 1].group !== o.group
              return (
                <div key={k}>
                  {first && <div className="px-3 pb-1 pt-2 text-[12px] font-medium text-muted-foreground">{o.group === 'series' ? t`Series` : q.trim() ? t`Books` : t`Books you are reading`}</div>}
                  <button
                    type="button"
                    role="option"
                    id={`goal-target-${i}`}
                    data-i={i}
                    aria-selected={sel}
                    aria-disabled={has}
                    tabIndex={-1}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => pick(o.x)}
                    className={cn('flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-[14px]', i === active ? 'bg-secondary text-foreground' : 'text-foreground', has && 'opacity-50')}
                  >
                    <Cover id={o.x.cover} h={30} className="rounded-[4px]" />
                    <span className="min-w-0 flex-1 truncate">{'series' in o.x ? o.x.series : o.x.title}</span>
                    <span className="shrink-0 text-[12px] text-muted-foreground">
                      {has ? t`has a goal` : 'series' in o.x ? (() => { const r = num(o.x.read), n = num(o.x.total); return t`${r} of ${n} read` })() : ''}
                    </span>
                    {sel && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
                  </button>
                </div>
              )
            })}
          </div>
        </Card>
      )}
    </>
  )
}

// ── A day ─────────────────────────────────────────────────────────────────────

export function DayPicker({ value, min, weekStart, onPick, children, active }: { value: string | null; min: string; weekStart: WeekStart; onPick: (iso: string) => void; children: ReactNode; active: boolean }) {
  const [open, setOpen] = useState(false)
  const start = value ?? min
  const [view, setView] = useState({ y: Number(start.slice(0, 4)), m: Number(start.slice(5, 7)) })
  const trigger = useRef<HTMLButtonElement>(null)
  const card = useRef<HTMLDivElement>(null)
  const pos = usePlace(open, trigger, card, () => setOpen(false))
  const grid = monthGrid(view.y, view.m, weekStart)
  const first = `${view.y}-${String(view.m).padStart(2, '0')}-01`
  const canBack = first > min
  const shift = (d: number) => setView((v) => { const m = v.m + d; return m < 1 ? { y: v.y - 1, m: 12 } : m > 12 ? { y: v.y + 1, m: 1 } : { y: v.y, m } })
  const focusDay = (iso: string) => card.current?.querySelector<HTMLButtonElement>(`[data-day="${iso}"]`)?.focus()
  useEffect(() => { if (open && pos) focusDay(value && value.slice(0, 7) === first.slice(0, 7) ? value : (grid.flat().find((d) => d && d >= min) ?? first)) }, [open, pos]) // eslint-disable-line react-hooks/exhaustive-deps
  const onKey = (e: KeyboardEvent, iso: string) => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowDown' ? 7 : e.key === 'ArrowUp' ? -7 : 0
    if (!step) return
    e.preventDefault()
    // eslint-disable-next-line lingui/no-unlocalized-strings -- ISO time suffix
    const d = new Date(iso + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + step)
    const next = d.toISOString().slice(0, 10)
    if (next < min) return
    if (next.slice(0, 7) !== iso.slice(0, 7)) { setView({ y: Number(next.slice(0, 4)), m: Number(next.slice(5, 7)) }); requestAnimationFrame(() => focusDay(next)) }
    else focusDay(next)
  }
  return (
    <>
      <button
        ref={trigger}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={cn('inline-flex h-9 items-center gap-1.5 rounded-full px-4 text-[14px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring', active ? 'bg-foreground text-background' : 'bg-secondary text-foreground hover:bg-surface-hover')}
      >
        {children}
      </button>
      {open && (
        <Card cardRef={card} pos={pos} label={t`Pick a day`}>
          <div className="w-[268px] p-1.5">
            <div className="mb-2 flex items-center justify-between">
              <button type="button" onClick={() => shift(-1)} disabled={!canBack} aria-label={t`Previous month`} className="grid h-8 w-8 place-items-center rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-30"><ChevronLeft className="h-4 w-4" /></button>
              <span className="text-[14px] font-semibold text-foreground">{monthName(first, true)}</span>
              <button type="button" onClick={() => shift(1)} aria-label={t`Next month`} className="grid h-8 w-8 place-items-center rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground"><ChevronRight className="h-4 w-4" /></button>
            </div>
            <div role="grid" aria-label={monthName(first, true)}>
              <div role="row" className="grid grid-cols-7 text-center text-[11px] text-muted-foreground">
                {grid[0].map((_, i) => { const any = grid.flat().find((d, k) => d && k % 7 === i)!; return <span key={i} role="columnheader" className="py-1">{weekdayLetter(any)}</span> })}
              </div>
              {grid.map((row, r) => (
                <div key={r} role="row" className="grid grid-cols-7">
                  {row.map((d, i) => d ? (
                    <button
                      key={d}
                      type="button"
                      role="gridcell"
                      data-day={d}
                      aria-selected={d === value}
                      aria-label={longDate(d)}
                      disabled={d < min}
                      tabIndex={-1}
                      onKeyDown={(e) => onKey(e, d)}
                      onClick={() => { onPick(d); setOpen(false); trigger.current?.focus() }}
                      className={cn('m-[1px] h-8 rounded-full text-[13px] tabular-nums focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-30', d === value ? 'bg-foreground font-semibold text-background' : 'text-foreground hover:bg-secondary')}
                    >
                      {Number(d.slice(8))}
                    </button>
                  ) : <span key={`e${i}`} />)}
                </div>
              ))}
            </div>
          </div>
        </Card>
      )}
    </>
  )
}

