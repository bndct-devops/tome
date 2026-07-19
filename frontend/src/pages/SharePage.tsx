// Public, read-only view of a shared shelf — /share/:token, no login.
// Deliberately minimal surface: covers, identity, tags, description, the
// owner's rating and highlights. No downloads, no reader, no links into the
// app beyond the landing page. Renders noindex so links stay unlisted.
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { BookOpen, ChevronDown, Quote, Star, StickyNote } from 'lucide-react'
import { TomeMark } from '@/components/TomeMark'
import { cn } from '@/lib/utils'
import { applyTheme, getStoredTheme } from '@/lib/theme'

interface SharedHighlight {
  text: string | null
  note: string | null
  chapter: string | null
}

interface SharedBook {
  id: number
  title: string
  author: string | null
  series: string | null
  series_index: number | null
  description: string | null
  tags: string[]
  rating: number | null
  highlights: SharedHighlight[]
}

interface ShareResponse {
  kind: 'shelf' | 'series' | 'book'
  title: string
  books: SharedBook[]
}

function Rating({ value }: { value: number }) {
  return (
    <span className="flex items-center gap-0.5" title={`${value} stars`}>
      {[1, 2, 3, 4, 5].map(i => (
        <Star
          key={i}
          className={cn(
            'h-3.5 w-3.5',
            i <= Math.round(value) ? 'fill-primary text-primary' : 'text-muted-foreground/40',
          )}
        />
      ))}
    </span>
  )
}

function SharedBookCard({ b, defaultOpen = false }: { b: SharedBook; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const hasMore = !!b.description || b.highlights.length > 0
  return (
    <li className="rounded-xl border border-border bg-card p-4">
      <div className="flex gap-4">
        <img
          src={`/api/books/${b.id}/cover`}
          alt=""
          loading="lazy"
          className="h-32 w-[85px] shrink-0 rounded-md object-cover shadow-sm"
        />
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-base leading-tight text-foreground">{b.title}</h3>
          {b.series && (
            <p className="mt-0.5 text-xs text-primary">
              {b.series}
              {b.series_index != null ? ` #${b.series_index}` : ''}
            </p>
          )}
          {b.author && <p className="mt-0.5 text-sm text-muted-foreground">{b.author}</p>}
          {b.rating != null && <div className="mt-1.5"><Rating value={b.rating} /></div>}
          {b.tags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {b.tags.map(t => (
                <span key={t} className="rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                  {t}
                </span>
              ))}
            </div>
          )}
          {hasMore && (
            <button
              onClick={() => setOpen(o => !o)}
              className="mt-2 flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')} />
              {open ? 'Less' : b.highlights.length > 0 ? `Description & ${b.highlights.length} highlight${b.highlights.length !== 1 ? 's' : ''}` : 'Description'}
            </button>
          )}
        </div>
      </div>
      {open && (
        <div className="mt-3 border-t border-border/60 pt-3">
          {b.description && (
            <p className="text-sm leading-relaxed text-muted-foreground">{b.description}</p>
          )}
          {b.highlights.length > 0 && (
            <ul className="mt-3 flex flex-col gap-2.5">
              {b.highlights.map((h, i) => (
                <li key={i} className="text-sm">
                  {h.chapter && <p className="text-[11px] text-muted-foreground/70">{h.chapter}</p>}
                  {h.text && (
                    <p className="border-l-2 border-primary/40 pl-2.5 leading-relaxed text-foreground">
                      {h.text}
                    </p>
                  )}
                  {h.note && (
                    <p className="mt-1 flex items-start gap-1.5 text-muted-foreground">
                      <StickyNote className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span className="leading-relaxed">{h.note}</span>
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  )
}

export function SharePage() {
  const { token } = useParams()
  const [data, setData] = useState<ShareResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    applyTheme(getStoredTheme())
    // Belt-and-braces with the server's X-Robots-Tag.
    const meta = document.createElement('meta')
    meta.name = 'robots'
    meta.content = 'noindex, nofollow'
    document.head.appendChild(meta)
    return () => { document.head.removeChild(meta) }
  }, [])

  useEffect(() => {
    // Plain fetch on purpose: no auth token attached, no 401 redirect logic.
    fetch(`/api/share/${token}`)
      .then(async r => {
        if (!r.ok) throw new Error((await r.json()).detail || 'Not found')
        return r.json()
      })
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : 'Not found'))
  }, [token])

  if (error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background p-6 text-center">
        <BookOpen className="h-10 w-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">{error}</p>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <p className="animate-pulse text-sm text-muted-foreground">Loading…</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/50 safe-top">
        <div className="mx-auto flex h-14 max-w-3xl items-center gap-2.5 px-4">
          <Quote className="h-4 w-4 text-primary" />
          <h1 className="font-display text-lg text-foreground">{data.title}</h1>
          <span className="text-xs text-muted-foreground">
            · a shared {data.kind}
            {data.kind !== 'book' ? ` · ${data.books.length} book${data.books.length !== 1 ? 's' : ''}` : ''}
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-6">
        {data.books.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">This shelf is empty.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {data.books.map(b => (
              <SharedBookCard key={b.id} b={b} defaultOpen={data.kind === 'book'} />
            ))}
          </ul>
        )}
        <footer className="flex items-center justify-center gap-1.5 pb-4 pt-10 text-xs text-muted-foreground/60">
          <TomeMark className="h-3.5 w-3.5" strokeWidth={7} />
          Shared from a self-hosted Tome library
        </footer>
      </main>
    </div>
  )
}
