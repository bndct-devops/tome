// Cmd+K command palette: fuzzy jump to books, series, authors, and pages from
// anywhere. Books query the existing full-text search; series/authors filter
// the facets list client-side; actions are plain navigation (no side effects).
// Portals to <body> (the header that mounts it has backdrop-blur, which would
// contain a fixed overlay — same lesson as WhatsNewPanel).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import {
  BookOpen, ChartColumn, Compass, Layers, Library as LibraryIcon,
  Quote, Search, Settings, Shield, Sparkles, User as UserIcon,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth, isAdmin } from '@/contexts/AuthContext'
import { cn } from '@/lib/utils'

interface BookHit {
  id: number
  title: string
  author: string | null
  series: string | null
  series_index: number | null
}

interface Facets {
  series: string[]
  authors: string[]
}

interface Item {
  key: string
  group: 'Actions' | 'Books' | 'Series' | 'Authors'
  label: string
  sub?: string
  icon: React.ReactNode
  to: string
  bookId?: number
}

const NAV_ACTIONS = (admin: boolean): { label: string; to: string; icon: React.ReactNode }[] => [
  { label: 'Home', to: '/', icon: <Compass className="h-4 w-4" /> },
  { label: 'All Books', to: '/?tab=books', icon: <LibraryIcon className="h-4 w-4" /> },
  { label: 'Series', to: '/?tab=series', icon: <Layers className="h-4 w-4" /> },
  { label: 'Reading Stats', to: '/stats', icon: <ChartColumn className="h-4 w-4" /> },
  { label: 'Highlights', to: '/highlights', icon: <Quote className="h-4 w-4" /> },
  { label: 'Wishlist', to: '/wishlist', icon: <Sparkles className="h-4 w-4" /> },
  { label: 'Settings', to: '/settings', icon: <Settings className="h-4 w-4" /> },
  ...(admin ? [{ label: 'Admin', to: '/admin', icon: <Shield className="h-4 w-4" /> }] : []),
]

/** startsWith beats includes; shorter names beat longer at equal rank. */
function rankFilter(pool: string[], q: string, cap: number): string[] {
  const lq = q.toLowerCase()
  return pool
    .map((s) => {
      const ls = s.toLowerCase()
      const rank = ls.startsWith(lq) ? 0 : ls.includes(lq) ? 1 : -1
      return { s, rank }
    })
    .filter((x) => x.rank >= 0)
    .sort((a, z) => a.rank - z.rank || a.s.length - z.s.length)
    .slice(0, cap)
    .map((x) => x.s)
}

export function CommandPalette() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [books, setBooks] = useState<BookHit[]>([])
  const [facets, setFacets] = useState<Facets | null>(null)
  const [selected, setSelected] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Global hotkey — Cmd+K / Ctrl+K toggles, Esc closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      } else if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Focus + lazy facets on open; reset on close.
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 30)
      if (!facets) {
        api
          .get<{ series: string[]; authors: string[] }>('/books/facets')
          .then((f) => setFacets({ series: f.series, authors: f.authors }))
          .catch(() => setFacets({ series: [], authors: [] }))
      }
    } else {
      setQuery('')
      setBooks([])
      setSelected(0)
    }
  }, [open, facets])

  // Debounced book search.
  useEffect(() => {
    if (!open) return
    if (debounceRef.current) clearTimeout(debounceRef.current)
    if (!query.trim()) {
      setBooks([])
      return
    }
    debounceRef.current = setTimeout(() => {
      api
        .get<BookHit[]>(`/books?q=${encodeURIComponent(query.trim())}&limit=6`)
        .then((r) => setBooks(Array.isArray(r) ? r : []))
        .catch(() => setBooks([]))
    }, 150)
  }, [query, open])

  const items = useMemo<Item[]>(() => {
    const q = query.trim()
    const out: Item[] = []
    const actions = NAV_ACTIONS(isAdmin(user)).filter(
      (a) => !q || a.label.toLowerCase().includes(q.toLowerCase()),
    )
    if (q) {
      for (const b of books) {
        out.push({
          key: `b${b.id}`,
          group: 'Books',
          label: b.series && b.series_index != null ? `${b.title}` : b.title,
          sub: [b.series && `${b.series} #${b.series_index ?? '?'}`, b.author].filter(Boolean).join(' · '),
          icon: <BookOpen className="h-4 w-4" />,
          to: `/books/${b.id}`,
          bookId: b.id,
        })
      }
      for (const s of rankFilter(facets?.series ?? [], q, 4)) {
        out.push({
          key: `s${s}`,
          group: 'Series',
          label: s,
          icon: <Layers className="h-4 w-4" />,
          to: `/?tab=series&series_detail=${encodeURIComponent(s)}`,
        })
      }
      for (const a of rankFilter(facets?.authors ?? [], q, 4)) {
        out.push({
          key: `a${a}`,
          group: 'Authors',
          label: a,
          icon: <UserIcon className="h-4 w-4" />,
          to: `/?tab=books&author=${encodeURIComponent(a)}`,
        })
      }
    }
    for (const a of actions) {
      out.push({ key: `n${a.to}`, group: 'Actions', label: a.label, icon: a.icon, to: a.to })
    }
    return out
  }, [query, books, facets, user])

  useEffect(() => setSelected(0), [items.length, query])

  const go = useCallback(
    (item: Item) => {
      setOpen(false)
      navigate(item.to)
    },
    [navigate],
  )

  if (!open) return null

  const groups: Item['group'][] = ['Books', 'Series', 'Authors', 'Actions']

  return createPortal(
    <>
      <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" onClick={() => setOpen(false)} />
      <div className="fixed inset-x-0 top-[15vh] z-50 mx-auto w-full max-w-xl px-4">
        <div className="overflow-hidden rounded-xl border border-border bg-card shadow-xl">
          <div className="flex items-center gap-2 border-b border-border px-4">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  setSelected((s) => Math.min(s + 1, items.length - 1))
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault()
                  setSelected((s) => Math.max(s - 1, 0))
                } else if (e.key === 'Enter' && items[selected]) {
                  e.preventDefault()
                  go(items[selected])
                }
              }}
              placeholder="Jump to a book, series, author, or page…"
              className="h-12 w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
            />
            <kbd className="hidden rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground sm:block">
              esc
            </kbd>
          </div>
          <div className="max-h-[50vh] overflow-y-auto overscroll-contain py-1.5">
            {items.length === 0 && (
              <p className="px-4 py-6 text-center text-sm text-muted-foreground">No matches.</p>
            )}
            {groups.map((g) => {
              const groupItems = items.filter((i) => i.group === g)
              if (!groupItems.length) return null
              return (
                <div key={g}>
                  <p className="px-4 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {g}
                  </p>
                  {groupItems.map((item) => {
                    const idx = items.indexOf(item)
                    return (
                      <button
                        key={item.key}
                        onClick={() => go(item)}
                        onMouseMove={() => setSelected(idx)}
                        className={cn(
                          'flex w-full items-center gap-3 px-4 py-2 text-left text-sm',
                          idx === selected ? 'bg-muted text-foreground' : 'text-muted-foreground',
                        )}
                      >
                        {item.bookId != null ? (
                          <img
                            src={`/api/books/${item.bookId}/cover`}
                            alt=""
                            className="h-8 w-[22px] shrink-0 rounded-sm object-cover"
                          />
                        ) : (
                          <span className="grid h-8 w-[22px] shrink-0 place-items-center">{item.icon}</span>
                        )}
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-foreground">{item.label}</span>
                          {item.sub && <span className="block truncate text-xs">{item.sub}</span>}
                        </span>
                      </button>
                    )
                  })}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
