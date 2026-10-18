// Jump list for the Settings page. One long page stays one long page: the
// list only scrolls to a section and follows the scroll position back. It
// renders through SectionNav, the same nav the Admin page uses: a left
// column at desktop width, a sideways pill row on phones.
import { useEffect, useRef, useState } from 'react'
import { useLingui } from '@lingui/react/macro'
import { SectionNav, type SectionNavItem } from '@/components/SectionNav'

export type SettingsNavSection = SectionNavItem

// A section counts as "current" once its top passes this line, measured from
// the top of the scrolling content panel (clears the phone pill row).
const TOP_LINE_PX = 72

export function SettingsNav({ sections, onActiveChange, placement }: {
  sections: SettingsNavSection[]
  onActiveChange?: (id: string | null) => void
  placement?: 'column' | 'sidebar'
}) {
  const { t } = useLingui()
  const [active, setActive] = useState<string | null>(sections[0]?.id ?? null)
  const anchorRef = useRef<HTMLSpanElement>(null)
  // A click pins the highlight on the clicked section until the user scrolls
  // on their own; the last sections can never reach the top line, so the
  // scroll rule alone would highlight the wrong one after a jump.
  const pinnedRef = useRef<string | null>(null)
  const ids = sections.map(s => s.id).join('|')

  useEffect(() => { onActiveChange?.(active) }, [active])

  // Deep link (/settings#api-tokens): the panel is not the document, so the
  // browser's own hash scroll does not reach it. Jump once on arrival.
  useEffect(() => {
    const id = window.location.hash.slice(1)
    if (!id) return
    const timer = setTimeout(() => {
      const el = document.getElementById(id)
      if (!el) return
      el.scrollIntoView({ block: 'start' })
      pinnedRef.current = id
      setActive(id)
    }, 150)
    return () => clearTimeout(timer)
  }, [])

  // Follow the scroll of the shell's content panel (<main>), not the window:
  // the shell scrolls inside it.
  useEffect(() => {
    const list = ids.split('|').filter(Boolean)
    if (list.length === 0) return
    const scroller: HTMLElement | null = anchorRef.current?.closest('main') ?? null
    let frame = 0
    function update() {
      frame = 0
      if (pinnedRef.current) return
      const top = scroller ? scroller.getBoundingClientRect().top : 0
      let current = list[0]
      for (const id of list) {
        const el = document.getElementById(id)
        if (!el) continue
        if (el.getBoundingClientRect().top - top <= TOP_LINE_PX) current = id
        else break
      }
      // At the very bottom the last section may never reach the line.
      const atBottom = scroller
        ? scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2
        : window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2
      if (atBottom && (scroller ? scroller.scrollTop > 0 : window.scrollY > 0)) current = list[list.length - 1]
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
    const target: HTMLElement | Window = scroller ?? window
    update()
    target.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    window.addEventListener('wheel', unpin, { passive: true })
    window.addEventListener('touchmove', unpin, { passive: true })
    window.addEventListener('keydown', unpin)
    return () => {
      target.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      window.removeEventListener('wheel', unpin)
      window.removeEventListener('touchmove', unpin)
      window.removeEventListener('keydown', unpin)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [ids])

  function jump(id: string, e: React.MouseEvent) {
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
      <span ref={anchorRef} hidden />
      <SectionNav
        items={sections}
        active={active}
        onSelect={jump}
        label={t`Settings sections`}
        hrefFor={id => `#${id}`}
        placement={placement}
      />
    </>
  )
}
