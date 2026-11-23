// Cmd+K command palette: fuzzy jump to books, series, authors, and pages from
// anywhere. Books query the existing full-text search; series/authors filter
// the facets list client-side; actions are plain navigation (no side effects).
// Portals to <body> (the header that mounts it has backdrop-blur, which would
// contain a fixed overlay - same lesson as WhatsNewPanel).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, m } from 'motion/react'
import { useNavigate } from 'react-router-dom'
import {
  BookOpen, ChartColumn, Compass, Layers, Library as LibraryIcon,
  Quote, Search, Settings, Shield, Sparkles, User as UserIcon,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth, isAdmin } from '@/contexts/AuthContext'
import { cn } from '@/lib/utils'
import { t, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'
import { coverSrc } from '@/lib/covers'
import { useRegisterOverlay, useShellInset } from '@/components/ModalShell'
import { EASE, listItem, useBookNav } from '@/components/motion'
import { Glide, GlideGroup } from '@/components/page'

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
  title?: string
  author?: string | null
  series?: string | null
  seriesIndex?: number | null
}

const GROUP_LABEL: Record<string, MessageDescriptor> = {
  Books: msg`Books`, Series: msg`Series`, Authors: msg`Authors`, Actions: msg`Actions`,
}

const NAV_ACTIONS = (admin: boolean): { label: string; to: string; icon: React.ReactNode }[] => [
  { label: i18n._(msg`Home`), to: '/', icon: <Compass className="h-4 w-4" /> },
  { label: i18n._(msg`All books`), to: '/?tab=books', icon: <LibraryIcon className="h-4 w-4" /> },
  { label: i18n._(msg`Series`), to: '/?tab=series', icon: <Layers className="h-4 w-4" /> },
  { label: i18n._(msg`Reading stats`), to: '/stats', icon: <ChartColumn className="h-4 w-4" /> },
  { label: i18n._(msg`Highlights`), to: '/highlights', icon: <Quote className="h-4 w-4" /> },
  { label: i18n._(msg`Wishlist`), to: '/wishlist', icon: <Sparkles className="h-4 w-4" /> },
  { label: i18n._(msg`Settings`), to: '/settings', icon: <Settings className="h-4 w-4" /> },
  ...(admin ? [{ label: i18n._(msg`Admin`), to: '/admin', icon: <Shield className="h-4 w-4" /> }] : []),
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
  const openBook = useBookNav()
  const panelRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [books, setBooks] = useState<BookHit[]>([])
  const [facets, setFacets] = useState<Facets | null>(null)
  const [selected, setSelected] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Global hotkey - Cmd+K / Ctrl+K toggles, Esc closes.
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
        .get<BookHit[]>(`/books?q=${encodeURIComponent(query.trim())}&limit=6&include_no_file=true`)
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
          title: b.title,
          author: b.author,
          series: b.series,
          seriesIndex: b.series_index,
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

  // A book opens with its cover growing from the result's thumbnail.
  const go = useCallback(
    (item: Item) => {
      if (item.bookId != null) {
        const cover = panelRef.current?.querySelector<HTMLElement>(`[data-cover-id="${item.bookId}"]`) ?? null
        openBook({ id: item.bookId, title: item.title ?? item.label, author: item.author, series: item.series, series_index: item.seriesIndex }, cover)
      } else {
        navigate(item.to)
      }
      setOpen(false)
    },
    [navigate, openBook],
  )

  const inset = useShellInset()
  useRegisterOverlay(open)

  // eslint-disable-next-line lingui/no-unlocalized-strings -- group keys, labels via GROUP_LABEL
  const groups: Item['group'][] = ['Books', 'Series', 'Authors', 'Actions']

  // Enters and leaves like the dialogs (ModalShell): the veil fades, the panel
  // scales a little from its top edge.
  return createPortal(
    <AnimatePresence>
      {open && (
      <m.div
        key="veil"
        className="dialog-veil fixed inset-0 z-50"
        onClick={() => setOpen(false)}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.15 }}
      />
      )}
      {/* Centred over the content panel, like the dialogs; the veil still
          covers the whole viewport. */}
      {open && (
      <m.div
        key="panel"
        className="pointer-events-none fixed inset-y-0 right-0 z-50 flex justify-center px-4 pt-[15vh]"
        style={{ left: inset, transformOrigin: '50% 15vh' }}
        initial={{ opacity: 0, scale: 0.97, y: -4 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97, y: -4 }}
        transition={{ duration: 0.16, ease: EASE }}
      >
        <div ref={panelRef} className="pointer-events-auto h-fit w-full max-w-xl overflow-hidden rounded-xl bg-card shadow-lg ring-1 ring-panel-ring">
          <div className="flex items-center gap-2 border-b border-border/70 px-4">
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
              placeholder={t`Jump to a book, series, author, or page…`}
              className="h-12 w-full bg-transparent text-[15px] text-foreground placeholder:text-muted-foreground focus:outline-none"
            />
            <kbd className="hidden rounded-md bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground sm:block">
              esc
            </kbd>
          </div>
          <div className="max-h-[50vh] overflow-y-auto overscroll-contain py-1.5">
            {items.length === 0 && (
              <p className="px-4 py-6 text-center text-[14px] text-muted-foreground">{t`No matches.`}</p>
            )}
            {/* One flat list (group labels and rows), so as results arrive
                while typing the rows that stay glide to their new place
                instead of the list re-flowing; rows that no longer match fade
                out (listItem). The selection is one highlight that glides
                from row to row. */}
            <GlideGroup>
            <div className="isolate">
            <AnimatePresence initial={false}>
            {groups.flatMap((g) => {
              const groupItems = items.filter((i) => i.group === g)
              if (!groupItems.length) return []
              return [
                <m.p key={`g-${g}`} {...listItem} className="px-4 pb-1 pt-2 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
                  {i18n._(GROUP_LABEL[g])}
                </m.p>,
                ...groupItems.map((item) => {
                    const idx = items.indexOf(item)
                    return (
                      <m.button
                        key={item.key}
                        {...listItem}
                        onClick={() => go(item)}
                        onMouseMove={() => setSelected(idx)}
                        className={cn(
                          'relative flex w-full items-center gap-3 px-4 py-2 text-left text-[14px]',
                          idx === selected ? 'text-foreground' : 'text-muted-foreground',
                        )}
                      >
                        {idx === selected && <Glide behind radius={0} className="bg-secondary" layoutId="palette-sel" />}
                        {item.bookId != null ? (
                          <span data-cover-id={item.bookId} className="block h-8 w-[22px] shrink-0 overflow-hidden rounded-sm">
                            <img
                              src={coverSrc(item.bookId, { w: 160 })}
                              alt=""
                              className="h-full w-full object-cover"
                            />
                          </span>
                        ) : (
                          <span className="grid h-8 w-[22px] shrink-0 place-items-center">{item.icon}</span>
                        )}
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-foreground">{item.label}</span>
                          {item.sub && <span className="block truncate text-[13px]">{item.sub}</span>}
                        </span>
                      </m.button>
                    )
                  }),
              ]
            })}
            </AnimatePresence>
            </div>
            </GlideGroup>
          </div>
        </div>
      </m.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}
