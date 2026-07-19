// Admin → Covers: which books have missing, unreadable, or low-resolution
// covers (GET /admin/covers/audit). Missing covers offer a one-click auto-fix
// that applies the first candidate from the existing cover search — safe
// because there is nothing to downgrade. Low-res covers deep-link to the book
// page instead, where the cover picker shows candidates to judge by eye.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink, ImageOff, Loader2, RefreshCw, Wand2 } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

interface FlaggedBook {
  book_id: number
  title: string
  author: string | null
  series: string | null
  series_index: number | null
  reason: 'missing' | 'low_res' | 'unreadable'
  width: number | null
  height: number | null
}

interface AuditResponse {
  scanned: number
  min_width: number
  books: FlaggedBook[]
}

type FixState = 'fixing' | 'fixed' | 'nocandidate' | 'failed'

const REASON_LABEL: Record<FlaggedBook['reason'], string> = {
  missing: 'No cover',
  low_res: 'Low resolution',
  unreadable: 'Unreadable file',
}

export function CoverAudit() {
  const [data, setData] = useState<AuditResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [fix, setFix] = useState<Record<number, FixState>>({})
  const [bulkRunning, setBulkRunning] = useState(false)

  const load = () => {
    setLoading(true)
    api
      .get<AuditResponse>('/admin/covers/audit')
      .then(setData)
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  async function autoFix(b: FlaggedBook): Promise<boolean> {
    setFix(prev => ({ ...prev, [b.book_id]: 'fixing' }))
    try {
      const candidates = await api.get<{ cover_url: string }[]>(`/books/${b.book_id}/cover-candidates`)
      const first = candidates[0]
      if (!first) {
        setFix(prev => ({ ...prev, [b.book_id]: 'nocandidate' }))
        return false
      }
      const form = new FormData()
      form.append('url', first.cover_url)
      await api.upload(`/books/${b.book_id}/cover`, form)
      setFix(prev => ({ ...prev, [b.book_id]: 'fixed' }))
      return true
    } catch {
      setFix(prev => ({ ...prev, [b.book_id]: 'failed' }))
      return false
    }
  }

  async function autoFixAllMissing() {
    if (!data || bulkRunning) return
    setBulkRunning(true)
    for (const b of data.books) {
      if (b.reason !== 'missing') continue
      if (fix[b.book_id] === 'fixed') continue
      await autoFix(b)
    }
    setBulkRunning(false)
  }

  if (!data) {
    return (
      <div className="flex items-center gap-2 py-10 justify-center text-sm text-muted-foreground">
        <Loader2 className="w-4 h-4 animate-spin" /> Scanning covers…
      </div>
    )
  }

  const missingCount = data.books.filter(b => b.reason === 'missing' && fix[b.book_id] !== 'fixed').length

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm text-muted-foreground">
          {data.books.length === 0
            ? `All ${data.scanned} covers look healthy.`
            : `${data.books.length} of ${data.scanned} books flagged (low-res = narrower than ${data.min_width}px).`}
        </p>
        <div className="ml-auto flex items-center gap-2">
          {missingCount > 0 && (
            <button
              onClick={autoFixAllMissing}
              disabled={bulkRunning}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-all"
            >
              {bulkRunning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wand2 className="w-3.5 h-3.5" />}
              Auto-fix {missingCount} missing
            </button>
          )}
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-card hover:bg-muted disabled:opacity-50 transition-all"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
            Rescan
          </button>
        </div>
      </div>

      {data.books.length > 0 && (
        <div className="rounded-xl border border-border overflow-hidden">
          <div className="grid grid-cols-[3rem_1fr_8rem_7rem_6rem] items-center gap-2 border-b border-border bg-muted/40 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            <span />
            <span>Book</span>
            <span>Problem</span>
            <span>Size</span>
            <span />
          </div>
          {data.books.map(b => {
            const state = fix[b.book_id]
            return (
              <div
                key={b.book_id}
                className="grid grid-cols-[3rem_1fr_8rem_7rem_6rem] items-center gap-2 border-b border-border/60 px-3 py-2 last:border-b-0"
              >
                <span className="flex h-12 w-8 items-center justify-center overflow-hidden rounded bg-muted">
                  {b.reason === 'missing' || state === 'fixed' ? (
                    state === 'fixed' ? (
                      <img src={`/api/books/${b.book_id}/cover?ts=${Date.now() % 100000}`} alt="" className="h-full w-full object-cover" />
                    ) : (
                      <ImageOff className="h-3.5 w-3.5 text-muted-foreground" />
                    )
                  ) : (
                    <img src={`/api/books/${b.book_id}/cover`} alt="" className="h-full w-full object-cover" />
                  )}
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm text-foreground">
                    {b.series ? `${b.series} ${b.series_index != null ? `#${b.series_index}` : ''} — ` : ''}
                    {b.title}
                  </span>
                  {b.author && <span className="block truncate text-xs text-muted-foreground">{b.author}</span>}
                </span>
                <span
                  className={cn(
                    'text-xs font-medium',
                    b.reason === 'missing' ? 'text-destructive' : 'text-warning',
                    state === 'fixed' && 'text-success',
                  )}
                >
                  {state === 'fixed' ? 'Fixed' : REASON_LABEL[b.reason]}
                </span>
                <span className="text-xs text-muted-foreground">
                  {b.width != null ? `${b.width}×${b.height}px` : '—'}
                </span>
                <span className="flex items-center justify-end gap-1.5">
                  {b.reason === 'missing' && state !== 'fixed' && (
                    <button
                      onClick={() => autoFix(b)}
                      disabled={state === 'fixing' || bulkRunning}
                      title="Apply the first cover-search candidate"
                      className="rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
                    >
                      {state === 'fixing' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
                    </button>
                  )}
                  <Link
                    to={`/books/${b.book_id}`}
                    title="Open book (pick a cover by eye)"
                    className="rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                  </Link>
                </span>
              </div>
            )
          })}
        </div>
      )}
      {Object.values(fix).some(s => s === 'nocandidate' || s === 'failed') && (
        <p className="text-xs text-muted-foreground">
          Some books had no usable candidate or failed to apply — open them and use the cover picker.
        </p>
      )}
    </div>
  )
}
