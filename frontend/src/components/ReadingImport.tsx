// Settings → Import reading history: upload a Goodreads or StoryGraph export,
// preview what matches the library, apply. Applying is fill-gaps-only server-
// side — an import can never overwrite an existing status/rating/review.
import { useRef, useState } from 'react'
import { CheckCircle2, FileUp, Loader2, Upload } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

interface MatchedRow {
  title: string
  author: string
  status: string
  rating: number | null
  finished_on: string | null
  review: string | null
  book_id: number
  matched_title: string
  match_via: 'isbn' | 'title' | 'fuzzy'
  will_apply: { status: boolean; rating: boolean; finished_on: boolean; review: boolean }
}

interface Preview {
  dialect: string
  matched: MatchedRow[]
  unmatched: { title: string; author: string }[]
  skipped_unread: number
}

const VIA_LABEL = { isbn: 'ISBN', title: 'title', fuzzy: 'fuzzy' } as const

export function ReadingImport() {
  const [preview, setPreview] = useState<Preview | null>(null)
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [phase, setPhase] = useState<'idle' | 'previewing' | 'applying' | 'done'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [summary, setSummary] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function onFile(f: File) {
    setPhase('previewing')
    setError(null)
    setSummary(null)
    const form = new FormData()
    form.append('file', f)
    try {
      const p = await api.upload<Preview>('/import/reading-csv', form)
      setPreview(p)
      setChecked(new Set(p.matched.map((_, i) => i)))
      setPhase('idle')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read that file')
      setPhase('idle')
    }
  }

  async function apply() {
    if (!preview || phase === 'applying') return
    setPhase('applying')
    try {
      const items = preview.matched
        .filter((_, i) => checked.has(i))
        .map(m => ({
          book_id: m.book_id, status: m.status, rating: m.rating,
          finished_on: m.finished_on, review: m.review,
        }))
      const r = await api.post<{ applied: Record<string, number>; skipped: number }>(
        '/import/reading-csv/apply', { items },
      )
      const a = r.applied
      setSummary(
        `Applied: ${a.status} statuses, ${a.rating} ratings, ${a.finished_on} finish dates, ${a.review} reviews.`,
      )
      setPreview(null)
      setPhase('done')
    } catch {
      setError('Applying failed — nothing was changed.')
      setPhase('idle')
    }
  }

  return (
    <div className="mt-3 rounded-xl border border-border/60 bg-card/50 p-5">
      <p className="text-xs text-muted-foreground mb-4">
        Bring your reading history from Goodreads or StoryGraph: upload their CSV
        export, review what matches your library, apply. Importing only fills
        gaps — it never overwrites a status, rating, review, or finish date you
        already have. &quot;To read&quot; shelves are skipped (that&apos;s what the wishlist
        is for).
      </p>

      <input
        ref={fileRef}
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        onChange={e => { if (e.target.files?.[0]) onFile(e.target.files[0]); e.target.value = '' }}
      />

      {!preview && (
        <div className="flex items-center gap-3">
          <button
            onClick={() => fileRef.current?.click()}
            disabled={phase === 'previewing'}
            className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium transition-all hover:bg-muted disabled:opacity-50"
          >
            {phase === 'previewing' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileUp className="h-3.5 w-3.5" />}
            Choose CSV export
          </button>
          {summary && (
            <span className="flex items-center gap-1.5 text-xs text-success">
              <CheckCircle2 className="h-3.5 w-3.5" /> {summary}
            </span>
          )}
          {error && <span className="text-xs text-destructive">{error}</span>}
        </div>
      )}

      {preview && (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Recognized a <span className="font-semibold text-foreground">{preview.dialect}</span> export:{' '}
            {preview.matched.length} matched, {preview.unmatched.length} not in your library,{' '}
            {preview.skipped_unread} to-read rows skipped.
          </p>

          {preview.matched.length > 0 && (
            <div className="max-h-72 overflow-y-auto overscroll-contain rounded-lg border border-border">
              {preview.matched.map((m, i) => {
                const noop = !m.will_apply.status && !m.will_apply.rating &&
                             !m.will_apply.finished_on && !m.will_apply.review
                return (
                  <label
                    key={i}
                    className={cn(
                      'flex cursor-pointer items-center gap-2.5 border-b border-border/50 px-3 py-2 last:border-b-0',
                      noop && 'opacity-50',
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked.has(i)}
                      onChange={() => setChecked(prev => {
                        const next = new Set(prev)
                        if (next.has(i)) next.delete(i)
                        else next.add(i)
                        return next
                      })}
                      className="accent-[var(--primary)]"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs text-foreground">{m.matched_title}</span>
                      <span className="block truncate text-[11px] text-muted-foreground">
                        {m.status}
                        {m.rating != null ? ` · ${m.rating} stars` : ''}
                        {m.finished_on ? ` · finished ${m.finished_on}` : ''}
                        {' · matched by '}{VIA_LABEL[m.match_via]}
                        {noop ? ' · nothing to fill (already tracked)' : ''}
                      </span>
                    </span>
                  </label>
                )
              })}
            </div>
          )}

          {preview.unmatched.length > 0 && (
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">Not in your library ({preview.unmatched.length})</summary>
              <ul className="mt-1 max-h-32 overflow-y-auto pl-4">
                {preview.unmatched.map((u, i) => (
                  <li key={i} className="truncate">{u.title} — {u.author}</li>
                ))}
              </ul>
            </details>
          )}

          <div className="flex items-center gap-2">
            <button
              onClick={apply}
              disabled={checked.size === 0 || phase === 'applying'}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-50"
            >
              {phase === 'applying' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
              Apply {checked.size} selected
            </button>
            <button
              onClick={() => { setPreview(null); setError(null) }}
              className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
