// A choice menu in the app's own look (the Library's SortMenu: a card with
// rounded rows, the current one on bg-secondary), for the Stats pages' pickers in
// place of a native <select>. Options may come in groups with a small heading.
// The menu is portalled and placed under its trigger (above it when there is no
// room), so no panel edge clips it; it closes on a pick, Escape, a click outside,
// a scroll or a resize. Keyboard: Enter, Space or Down on the trigger opens it on
// the current choice; Up/Down/Home/End move, Enter or Space picks, Escape or Tab
// closes (focus back on the trigger).
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Pop } from '@/components/motion'
import { PRESS } from '@/components/page'

export type MenuOption = { id: string; label: string; group?: string | null }

export function ChoiceMenu({ value, options, groups = [], onPick, label, className, children, align = 'start' }: {
  value: string
  options: MenuOption[]
  /** Group keys in order, with their headings; options without a group come first. */
  groups?: { key: string; label: string }[]
  onPick: (id: string) => void
  /** Accessible name of the trigger and the menu. */
  label: string
  className?: string
  /** The trigger's content. */
  children: ReactNode
  align?: 'start' | 'end'
}) {
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number; up: boolean } | null>(null)

  const sections: { key: string | null; label: string | null; items: MenuOption[] }[] = [
    { key: null, label: null, items: options.filter((o) => !o.group) },
    ...groups.map((g) => ({ key: g.key, label: g.label, items: options.filter((o) => o.group === g.key) })),
  ].filter((s) => s.items.length > 0)

  const close = (focus = true) => { setOpen(false); setPos(null); if (focus) trigger.current?.focus() }

  // Place under the trigger, or above when the window has no room below.
  useLayoutEffect(() => {
    if (!open || !trigger.current || !menu.current) return
    const r = trigger.current.getBoundingClientRect()
    const m = menu.current
    const vw = document.documentElement.clientWidth, vh = window.innerHeight
    const up = r.bottom + 6 + m.offsetHeight > vh - 8 && r.top - 6 - m.offsetHeight > 8
    let left = align === 'end' ? r.right - m.offsetWidth : r.left
    left = Math.min(Math.max(8, left), vw - m.offsetWidth - 8)
    setPos({ left, top: up ? r.top - 6 - m.offsetHeight : r.bottom + 6, up })
  }, [open, align])
  // Focus the current choice once the menu is placed (a hidden element takes no focus).
  const placed = pos != null
  useLayoutEffect(() => {
    if (!placed || !menu.current) return
    const items = Array.from(menu.current.querySelectorAll<HTMLElement>('[role="menuitemradio"]'))
    ;(items.find((el) => el.getAttribute('aria-checked') === 'true') ?? items[0])?.focus({ preventScroll: true })
  }, [placed])

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (!menu.current?.contains(t) && !trigger.current?.contains(t)) close(false)
    }
    const onScroll = (e: Event) => { if (!menu.current?.contains(e.target as Node)) close(false) }
    const onResize = () => close(false)
    document.addEventListener('pointerdown', onDown, true)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onResize)
    return () => {
      document.removeEventListener('pointerdown', onDown, true)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onResize)
    }
  }, [open])

  const onMenuKey = (e: KeyboardEvent) => {
    const items = Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? [])
    const at = items.indexOf(document.activeElement as HTMLElement)
    let next = -1
    if (e.key === 'ArrowDown') next = Math.min(items.length - 1, at + 1)
    else if (e.key === 'ArrowUp') next = Math.max(0, at - 1)
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = items.length - 1
    else if (e.key === 'Escape') { e.preventDefault(); close(); return }
    else if (e.key === 'Tab') { e.preventDefault(); close(); return }
    else return
    e.preventDefault()
    items[next]?.focus()
  }

  return (
    <>
      <button
        ref={trigger}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        onClick={() => (open ? close() : setOpen(true))}
        onKeyDown={(e) => {
          if (!open && e.key === 'ArrowDown') { e.preventDefault(); setOpen(true) }
          else if (open && e.key === 'Escape') { e.preventDefault(); close() }
        }}
        className={className}
      >
        {children}
      </button>
      {createPortal(
        // Opens from the trigger's side (down, or up when placed above) and leaves the same way (Pop).
        <Pop
          open={open}
          origin={`${pos?.up ? 'bottom' : 'top'}-${align === 'end' ? 'right' : 'left'}`}
          // Placed inside the viewport by the layout effect above.
          fit={false}
          ref={menu}
          role="menu"
          aria-label={label}
          data-escape-layer=""
          onKeyDown={onMenuKey}
          className="fixed z-50 max-h-[min(420px,calc(100vh-16px))] min-w-52 max-w-[min(320px,calc(100vw-16px))] overflow-y-auto overscroll-contain rounded-xl bg-card p-1.5 shadow-lg ring-1 ring-panel-ring [scrollbar-width:thin]"
          style={pos ? { left: pos.left, top: pos.top } : { left: 0, top: 0, visibility: 'hidden' }}
        >
          {sections.map((s, k) => (
            <div key={s.key ?? '_'} role="group" aria-label={s.label ?? undefined} className={cn(k > 0 && 'mt-1 border-t border-border/70 pt-1')}>
              {s.label && <div className="px-3 pb-1 pt-1.5 text-[12px] font-medium text-muted-foreground" aria-hidden="true">{s.label}</div>}
              {s.items.map((o) => {
                const active = o.id === value
                return (
                  <button
                    key={o.id}
                    type="button"
                    role="menuitemradio"
                    aria-checked={active}
                    tabIndex={-1}
                    onClick={() => { close(); if (!active) onPick(o.id) }}
                    className={cn(
                      'touch-feedback flex h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-[14px] outline-none focus-visible:ring-2 focus-visible:ring-ring', PRESS,
                      active ? 'bg-secondary font-medium text-foreground' : 'text-muted-foreground hover:bg-secondary hover:text-foreground focus:bg-secondary focus:text-foreground',
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate">{o.label}</span>
                    {active && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
                  </button>
                )
              })}
            </div>
          ))}
        </Pop>,
        document.body,
      )}
    </>
  )
}
