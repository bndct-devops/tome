// Jump list for the Settings page. One long page stays one long page: the
// list only scrolls to a section and follows the scroll position back. At
// desktop width it is a sticky column on the left; on phones it is a row of
// chips under the header that scrolls sideways.
import { useEffect, useRef, useState } from 'react'
import { useLingui } from '@lingui/react/macro'
import { cn } from '@/lib/utils'

export interface SettingsNavSection {
  id: string
  label: string
}

// Keep in step with the sticky header (h-14) plus breathing room: a section
// counts as "current" once its top passes this line.
const TOP_LINE_PX = 96

export function SettingsNav({ sections }: { sections: SettingsNavSection[] }) {
  const { t } = useLingui()
  const [active, setActive] = useState<string | null>(sections[0]?.id ?? null)
  const chipRefs = useRef<Record<string, HTMLAnchorElement | null>>({})
  // A click pins the highlight on the clicked section until the user scrolls
  // on their own; the last sections can never reach the top line, so the
  // scroll rule alone would highlight the wrong one after a jump.
  const pinnedRef = useRef<string | null>(null)
  const ids = sections.map(s => s.id).join('|')

  // Follow the scroll: the current section is the last one whose top is
  // above the top line. Cheaper and steadier than an IntersectionObserver
  // with many thresholds, and it handles sections taller than the viewport.
  useEffect(() => {
    const list = ids.split('|').filter(Boolean)
    if (list.length === 0) return
    let frame = 0
    function update() {
      frame = 0
      if (pinnedRef.current) return
      let current = list[0]
      for (const id of list) {
        const el = document.getElementById(id)
        if (!el) continue
        if (el.getBoundingClientRect().top <= TOP_LINE_PX) current = id
        else break
      }
      // At the very bottom the last section may never reach the line.
      const doc = document.documentElement
      if (window.innerHeight + window.scrollY >= doc.scrollHeight - 2) current = list[list.length - 1]
      setActive(prev => (prev === current ? prev : current))
    }
    function onScroll() {
      if (!frame) frame = requestAnimationFrame(update)
    }
    function unpin() {
      if (!pinnedRef.current) return
      pinnedRef.current = null
      onScroll()
    }
    update()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    window.addEventListener('wheel', unpin, { passive: true })
    window.addEventListener('touchmove', unpin, { passive: true })
    window.addEventListener('keydown', unpin)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      window.removeEventListener('wheel', unpin)
      window.removeEventListener('touchmove', unpin)
      window.removeEventListener('keydown', unpin)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [ids])

  // Keep the active chip in view on phones.
  useEffect(() => {
    if (!active) return
    const chip = chipRefs.current[active]
    chip?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' })
  }, [active])

  function jump(e: React.MouseEvent<HTMLAnchorElement>, id: string) {
    e.preventDefault()
    const el = document.getElementById(id)
    if (!el) return
    el.scrollIntoView({ block: 'start', behavior: 'smooth' })
    window.history.replaceState(null, '', `#${id}`)
    pinnedRef.current = id
    setActive(id)
  }

  if (sections.length === 0) return null

  return (
    <>
      {/* Desktop: sticky column */}
      <nav aria-label={t`Settings sections`} className="hidden lg:block sticky top-24 self-start">
        <ul className="space-y-0.5 border-l border-border">
          {sections.map(s => {
            const isActive = s.id === active
            return (
              <li key={s.id}>
                <a
                  href={`#${s.id}`}
                  onClick={e => jump(e, s.id)}
                  aria-current={isActive ? 'location' : undefined}
                  className={cn(
                    '-ml-px block border-l-2 py-1 pl-3 pr-2 text-sm transition-colors',
                    isActive
                      ? 'border-primary text-foreground font-medium'
                      : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border',
                  )}
                >
                  {s.label}
                </a>
              </li>
            )
          })}
        </ul>
      </nav>

      {/* Phone and tablet: chip row under the header */}
      <nav
        aria-label={t`Settings sections`}
        className="lg:hidden sticky top-14 z-10 -mx-4 border-b border-border bg-background/90 backdrop-blur-sm"
      >
        <div className="flex gap-1.5 overflow-x-auto px-4 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {sections.map(s => {
            const isActive = s.id === active
            return (
              <a
                key={s.id}
                ref={el => { chipRefs.current[s.id] = el }}
                href={`#${s.id}`}
                onClick={e => jump(e, s.id)}
                aria-current={isActive ? 'location' : undefined}
                className={cn(
                  'shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-xs transition-colors',
                  isActive
                    ? 'border-primary bg-primary text-primary-foreground'
                    : 'border-border bg-card text-muted-foreground hover:text-foreground',
                )}
              >
                {s.label}
              </a>
            )
          })}
        </div>
      </nav>
    </>
  )
}
