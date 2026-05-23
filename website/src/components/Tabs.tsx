import { useState } from 'react'

// Tabs with a code-block panel. Each tab is { label, code }; the code string
// is rendered inside <pre><code> by Tabs itself. Keeps the prop a plain JSON
// shape so Astro's frontmatter doesn't have to parse React JSX.
interface Tab { label: string; code: string }
interface Props { tabs: Tab[]; initial?: number }

export function Tabs({ tabs, initial = 0 }: Props) {
  const [active, setActive] = useState(initial)
  const current = tabs[active]
  return (
    <div className="docs-tabs">
      <div className="docs-tabs-list" role="tablist">
        {tabs.map((t, i) => (
          <button
            key={i}
            type="button"
            role="tab"
            aria-selected={active === i}
            className={`docs-tabs-trigger ${active === i ? 'is-active' : ''}`}
            onClick={() => setActive(i)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="docs-tabs-panel" role="tabpanel">
        <pre><code>{current?.code}</code></pre>
      </div>
    </div>
  )
}
