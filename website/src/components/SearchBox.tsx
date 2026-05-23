import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Search, X } from 'lucide-react'

interface Result { url: string; meta: { title?: string }; excerpt: string }

export function SearchBox() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Result[]>([])
  const [pagefind, setPagefind] = useState<any>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Cmd+K to open
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setOpen(o => !o)
      } else if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => { if (open) inputRef.current?.focus() }, [open])

  // Lazy-load Pagefind index (built into the deployed site under /_pagefind/).
  // Constructed at runtime so Vite doesn't try to resolve it during dev — the
  // file only exists after `pagefind --site dist` runs as part of the build.
  useEffect(() => {
    if (!open || pagefind) return
    ;(async () => {
      try {
        const base = (import.meta.env.BASE_URL || '/').replace(/\/$/, '')
        const path = base + '/' + '_pagefind/pagefind.js'  // split to dodge Vite static analysis
        const m = await import(/* @vite-ignore */ path)
        setPagefind(m)
      } catch {
        // Index isn't available in dev — graceful degrade
      }
    })()
  }, [open, pagefind])

  useEffect(() => {
    if (!pagefind || !query) { setResults([]); return }
    let cancelled = false
    ;(async () => {
      const search = await pagefind.search(query)
      if (cancelled) return
      const data = await Promise.all(search.results.slice(0, 8).map((r: any) => r.data()))
      if (!cancelled) setResults(data)
    })()
    return () => { cancelled = true }
  }, [pagefind, query])

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}
        aria-label="Search docs (Cmd+K)"
        className="inline-flex items-center gap-2 h-9 px-3 rounded-md text-sm border bg-[var(--card)] text-[var(--muted)] hover:text-[var(--fg)] hover:bg-[var(--bg)] transition-colors">
        <Search className="w-3.5 h-3.5" />
        <span className="hidden md:inline">Search</span>
        <kbd className="kbd hidden md:inline-block">⌘K</kbd>
      </button>
      {open && createPortal(
        <>
          <div className="search-backdrop" onClick={() => setOpen(false)} />
          <div className="search-panel-wrap">
            <div className="search-panel" onClick={e => e.stopPropagation()}>
              <div className="search-input-wrap">
                <Search className="w-4 h-4 text-[var(--muted)]" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder={pagefind ? 'Search the docs…' : 'Loading index…'}
                  className="search-input"
                />
                <button onClick={() => setOpen(false)} aria-label="Close" className="text-[var(--muted)] hover:text-[var(--fg)]">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="search-results">
                {!pagefind && (
                  <div className="search-empty">
                    Search runs against the built site. In dev mode it's empty —
                    the production build runs <code>pagefind</code> to index everything.
                  </div>
                )}
                {pagefind && !query && (
                  <div className="search-empty">Type to search the docs.</div>
                )}
                {results.map(r => (
                  <a key={r.url} href={r.url} className="search-result">
                    <div className="search-result-title">{r.meta?.title || r.url}</div>
                    <div className="search-result-excerpt" dangerouslySetInnerHTML={{ __html: r.excerpt }} />
                  </a>
                ))}
              </div>
            </div>
          </div>
        </>,
        document.body
      )}
    </>
  )
}
