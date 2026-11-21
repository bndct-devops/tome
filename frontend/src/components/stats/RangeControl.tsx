// The Stats page's range picker: presets plus a custom date range on a themed
// calendar. Moved out of the old StatsPage unchanged.
import { useEffect, useState } from 'react'
import { Calendar, ChevronLeft, ChevronRight } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import { Pill } from '@/components/page'
import { cn } from '@/lib/utils'
import { RANGES, fmtDay, type CustomRange } from './range'
import { inWeekOrder, rowOfJsDay } from '@/lib/weekStart'

const isoOf = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

// Themed month calendar with range selection - uses theme CSS vars, so it recolors
// with the theme switcher (unlike the browser's native date picker).
function RangeCalendar({ from, to, onPick }: { from: string; to: string; onPick: (iso: string) => void }) {
  const [view, setView] = useState(() => (from ? new Date(from + 'T00:00:00') : new Date()))
  const y = view.getFullYear()
  const m = view.getMonth()
  const firstDow = rowOfJsDay(new Date(y, m, 1).getDay()) // the reader's first weekday = 0
  const daysInMonth = new Date(y, m + 1, 0).getDate()
  const cells: (Date | null)[] = [...Array(firstDow).fill(null), ...Array.from({ length: daysInMonth }, (_, i) => new Date(y, m, i + 1))]
  const monthLabel = view.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <button type="button" onClick={() => setView(new Date(y, m - 1, 1))} className="inline-flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground transition hover:bg-secondary hover:text-foreground" aria-label={t`Previous month`}>
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="text-[14px] font-semibold text-foreground">{monthLabel}</span>
        <button type="button" onClick={() => setView(new Date(y, m + 1, 1))} className="inline-flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground transition hover:bg-secondary hover:text-foreground" aria-label={t`Next month`}>
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
      <div className="grid grid-cols-7 gap-0.5">
        {inWeekOrder([t`Mo`, t`Tu`, t`We`, t`Th`, t`Fr`, t`Sa`, t`Su`]).map((w) => (
          <div key={w} className="py-1 text-center text-[12px] font-medium text-muted-foreground">{w}</div>
        ))}
        {cells.map((d, i) => {
          if (!d) return <div key={i} />
          const iso = isoOf(d)
          const isEnd = iso === from || iso === to
          const between = !!from && !!to && iso > from && iso < to
          return (
            <button
              key={i}
              type="button"
              onClick={() => onPick(iso)}
              className={cn(
                'flex h-8 items-center justify-center rounded-full text-[13px] transition',
                isEnd ? 'bg-primary font-semibold text-primary-foreground' : between ? 'bg-primary/15 text-foreground' : 'text-foreground hover:bg-secondary',
              )}
            >
              {d.getDate()}
            </button>
          )
        })}
      </div>
    </div>
  )
}

export function RangeControl({
  days,
  custom,
  onPreset,
  onCustom,
}: {
  days: number
  custom: CustomRange | null
  onPreset: (days: number) => void
  onCustom: (range: CustomRange | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [from, setFrom] = useState(custom?.start ?? '')
  const [to, setTo] = useState(custom?.end ?? '')
  useEffect(() => {
    setFrom(custom?.start ?? '')
    setTo(custom?.end ?? '')
  }, [custom])

  const pick = (iso: string) => {
    if (!from || (from && to)) {
      setFrom(iso)
      setTo('')
    } else if (iso < from) {
      setFrom(iso)
    } else {
      setTo(iso)
    }
  }

  const valid = !!from && !!to && from <= to
  const customStart = custom ? fmtDay(custom.start) : ''
  const customEnd = custom ? fmtDay(custom.end) : ''
  const fromDay = from ? fmtDay(from) : ''
  const toDay = to ? fmtDay(to) : ''
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) } }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open])

  return (
    <div className="relative flex items-center gap-0.5">
      {RANGES.map((r) => (
        <Pill
          key={r.days}
          variant={!custom && days === r.days ? 'secondary' : 'quiet'}
          onClick={() => onPreset(r.days)}
          className="max-sm:px-3"
        >
          {i18n._(r.label)}
        </Pill>
      ))}
      <Pill
        variant={custom ? 'secondary' : 'quiet'}
        onClick={() => setOpen((o) => !o)}
        className="max-sm:px-3"
      >
        <Calendar className="h-4 w-4" />
        {custom ? t`${customStart} to ${customEnd}` : t`Custom`}
      </Pill>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onPointerDown={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-50 mt-1.5 w-72 max-w-[calc(100vw-32px)] rounded-xl bg-card p-3 shadow-lg ring-1 ring-panel-ring">
            <RangeCalendar from={from} to={to} onPick={pick} />
            <div className="mt-3 border-t border-border/70 pt-3">
              <p className="text-[13px] text-muted-foreground">
                {from ? (to ? t`${fromDay} to ${toDay}` : t`${fromDay} to …`) : t`Pick a start and end`}
              </p>
              <div className="mt-2 flex items-center justify-end gap-1.5">
                {custom && (
                  <Pill
                    variant="quiet"
                    onClick={() => {
                      onCustom(null)
                      setOpen(false)
                    }}
                  >
                    <Trans>Clear</Trans>
                  </Pill>
                )}
                <Pill
                  variant="primary"
                  disabled={!valid}
                  onClick={() => {
                    onCustom({ start: from, end: to })
                    setOpen(false)
                  }}
                >
                  <Trans>Apply</Trans>
                </Pill>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
