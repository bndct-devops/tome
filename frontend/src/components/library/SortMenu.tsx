import { useEffect, useState } from 'react'
import { ArrowUp, ArrowUpDown } from 'lucide-react'
import { useLingui } from '@lingui/react/macro'
import { Pill } from '@/components/page'
import { Pop } from '@/components/motion'
import { cn } from '@/lib/utils'
import { ReserveLabel } from './Toolbar'

/**
 * The Library's sort control as one pill: the current field and direction on
 * the pill, the fields in a small menu under it. Picking the active field again
 * flips the direction, as the old segmented control did. The pill is as wide
 * as its widest field, so a new field moves nothing beside it, and the arrow
 * turns when the direction does.
 */
export function SortMenu<F extends string>({ fields, value, order, onPick }: {
  fields: { id: F; label: string }[]
  value: F
  order: 'asc' | 'desc'
  onPick: (field: F) => void
}) {
  const { t } = useLingui()
  const [open, setOpen] = useState(false)
  const current = fields.find(f => f.id === value)?.label ?? ''
  // One arrow that turns over, in the pill and in the menu.
  const arrow = (big: boolean) => (
    <ArrowUp className={cn('w-3.5 h-3.5', big && 'text-muted-foreground', 'transition-transform duration-200 ease-(--spring) motion-reduce:transition-none', order === 'desc' && 'rotate-180')} />
  )

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <div className="relative">
      <Pill
        variant="secondary"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={t`Sort by ${current}`}
      >
        <ArrowUpDown className="w-4 h-4 text-muted-foreground" />
        <ReserveLabel labels={fields.map(f => f.label)}>{current}</ReserveLabel>
        {arrow(true)}
      </Pill>
      {open && <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />}
      <Pop
            open={open}
            role="menu"
            className="absolute left-0 sm:left-auto sm:right-0 top-full mt-2 z-40 min-w-52 rounded-xl bg-card shadow-lg ring-1 ring-panel-ring p-1.5 origin-top-left sm:origin-top-right"
          >
            {fields.map(f => {
              const active = f.id === value
              return (
                <button
                  key={f.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={active}
                  onClick={() => { onPick(f.id); setOpen(false) }}
                  className={cn(
                    'w-full h-9 px-3 rounded-lg flex items-center gap-2 text-[14px] text-left transition-colors',
                    active ? 'bg-secondary text-foreground font-medium' : 'text-muted-foreground hover:text-foreground hover:bg-secondary',
                  )}
                >
                  <span className="flex-1">{f.label}</span>
                  {active && (
                    <span className="inline-flex items-center gap-1 text-[12px] text-muted-foreground">
                      {arrow(false)}
                      {order === 'asc' ? t`Ascending` : t`Descending`}
                    </span>
                  )}
                </button>
              )
            })}
            <div className="px-3 pt-1.5 pb-1 text-[12px] text-muted-foreground">
              {t`Pick the active field again to reverse the order.`}
            </div>
      </Pop>
    </div>
  )
}
