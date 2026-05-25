import { useEffect, useState } from 'react'

interface Heading { id: string; text: string; level: number }

export function DocsToc() {
  const [headings, setHeadings] = useState<Heading[]>([])
  const [active, setActive] = useState<string | null>(null)

  // Collect all h2/h3 in the article on mount + give them IDs if missing
  useEffect(() => {
    const article = document.querySelector('.docs-article')
    if (!article) return
    const nodes = Array.from(article.querySelectorAll('h2, h3')) as HTMLElement[]
    const items: Heading[] = nodes.map(n => {
      if (!n.id) {
        const rawText = Array.from(n.childNodes).filter(c => !(c instanceof HTMLAnchorElement && c.classList.contains('heading-anchor'))).map(c => c.textContent).join('')
        n.id = rawText
          .toLowerCase()
          .replace(/[^a-z0-9\s-]/g, '')
          .trim()
          .replace(/\s+/g, '-')
      }
      const text = Array.from(n.childNodes).filter(c => !(c instanceof HTMLAnchorElement && c.classList.contains('heading-anchor'))).map(c => c.textContent).join('').trim()
      return { id: n.id, text, level: n.tagName === 'H2' ? 2 : 3 }
    })
    setHeadings(items)

    if (!items.length) return
    const io = new IntersectionObserver(
      entries => {
        const visible = entries.filter(e => e.isIntersecting)
        if (visible.length) setActive(visible[0].target.id)
      },
      { rootMargin: '-80px 0px -70% 0px', threshold: 0.1 },
    )
    nodes.forEach(n => io.observe(n))
    return () => io.disconnect()
  }, [])

  if (!headings.length) return null

  return (
    <aside className="docs-toc" aria-label="On this page">
      <div className="docs-toc-label">On this page</div>
      <ul>
        {headings.map(h => (
          <li key={h.id} className={`docs-toc-item docs-toc-level-${h.level}`}>
            <a
              href={`#${h.id}`}
              className={`docs-toc-link ${active === h.id ? 'is-active' : ''}`}
              onClick={(e) => {
                e.preventDefault()
                document.getElementById(h.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                history.replaceState(null, '', `#${h.id}`)
              }}
            >
              {h.text}
            </a>
          </li>
        ))}
      </ul>
    </aside>
  )
}
