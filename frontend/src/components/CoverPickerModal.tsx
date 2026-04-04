import { useEffect, useRef, useState } from 'react'
import { Camera, Check, ImageOff, Loader2, Search, Upload, X } from 'lucide-react'
import type { BookDetail } from '@/lib/books'

const API = import.meta.env.VITE_API_URL ?? ''

function authHeader(): Record<string, string> {
  const token = localStorage.getItem('tome_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

interface CoverCandidate {
  source: string
  label: string
  cover_url: string
}

interface Props {
  book: BookDetail
  open: boolean
  onClose: () => void
  onApplied: (updated: BookDetail) => void
}

const SOURCE_BADGE: Record<string, string> = {
  hardcover: 'HC',
  google_books: 'Google',
  open_library: 'OpenLib',
}

export function CoverPickerModal({ book, open, onClose, onApplied }: Props) {
  const [candidates, setCandidates] = useState<CoverCandidate[]>([])
  const [loading, setLoading] = useState(false)
  const [applyingUrl, setApplyingUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [customUrl, setCustomUrl] = useState('')
  const [query, setQuery] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const queryRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setCandidates([])
      setError(null)
      setCustomUrl('')
      setQuery('')
      fetchCandidates('')
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  async function fetchCandidates(q: string) {
    setLoading(true)
    setError(null)
    setCandidates([])
    try {
      const qs = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''
      const r = await fetch(`${API}/api/books/${book.id}/cover-candidates${qs}`, {
        headers: authHeader(),
      })
      if (!r.ok) throw new Error(await r.text())
      const data: CoverCandidate[] = await r.json()
      setCandidates(data)
      if (data.length === 0) setError('No cover candidates found.')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch candidates')
    } finally {
      setLoading(false)
    }
  }

  async function applyUrl(url: string) {
    setApplyingUrl(url)
    setError(null)
    try {
      const form = new FormData()
      form.append('url', url)
      const r = await fetch(`${API}/api/books/${book.id}/cover`, {
        method: 'POST',
        headers: authHeader(),
        body: form,
      })
      if (!r.ok) throw new Error(await r.text())
      const updated: BookDetail = await r.json()
      onApplied(updated)
      handleClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to apply cover')
    } finally {
      setApplyingUrl(null)
    }
  }

  async function applyFile(file: File) {
    setApplyingUrl('__file__')
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const r = await fetch(`${API}/api/books/${book.id}/cover`, {
        method: 'POST',
        headers: authHeader(),
        body: form,
      })
      if (!r.ok) throw new Error(await r.text())
      const updated: BookDetail = await r.json()
      onApplied(updated)
      handleClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to upload cover')
    } finally {
      setApplyingUrl(null)
    }
  }

  function handleClose() {
    setCandidates([])
    setError(null)
    setCustomUrl('')
    setQuery('')
    onClose()
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={handleClose} />

      <div className="relative z-10 bg-background border border-border rounded-xl shadow-xl w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border sticky top-0 bg-background z-10">
          <div className="flex items-center gap-2 font-semibold text-sm">
            <Camera className="h-4 w-4 text-primary" />
            Change Cover
            <span className="text-muted-foreground font-normal">— {book.title}</span>
          </div>
          <button onClick={handleClose} className="text-muted-foreground hover:text-foreground transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-6 space-y-5">
          {/* Search */}
          <div className="flex gap-2">
            <input
              ref={queryRef}
              className="flex h-9 w-full rounded-md border border-border bg-transparent px-3 py-1 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder={`${book.title}${book.author ? ` ${book.author}` : ''}`}
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && fetchCandidates(query)}
            />
            <button
              onClick={() => fetchCandidates(query)}
              disabled={loading}
              className="shrink-0 flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
            >
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
            </button>
          </div>

          {/* Candidate grid */}
          {loading && (
            <div className="flex justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {!loading && candidates.length > 0 && (
            <div>
              <p className="text-xs text-muted-foreground mb-3">
                {candidates.length} cover{candidates.length !== 1 ? 's' : ''} found — click to apply
              </p>
              <div className="grid grid-cols-3 sm:grid-cols-4 gap-3">
                {candidates.map((c, i) => {
                  const isThis = applyingUrl === c.cover_url
                  return (
                    <button
                      key={i}
                      disabled={applyingUrl !== null}
                      onClick={() => applyUrl(c.cover_url)}
                      className="group relative rounded-lg overflow-hidden border border-border bg-muted aspect-[2/3] hover:border-primary/60 hover:shadow-md transition-all duration-200 disabled:cursor-not-allowed"
                      title={c.label}
                    >
                      <img
                        src={c.cover_url}
                        alt={c.label}
                        className="w-full h-full object-cover"
                        onError={e => {
                          const el = e.target as HTMLImageElement
                          el.style.display = 'none'
                          el.nextElementSibling?.classList.remove('hidden')
                        }}
                      />
                      <div className="hidden absolute inset-0 flex items-center justify-center">
                        <ImageOff className="h-6 w-6 text-muted-foreground" />
                      </div>
                      {/* Source badge */}
                      <span className="absolute top-1 left-1 text-[9px] font-medium px-1 py-0.5 rounded bg-black/60 text-white leading-none">
                        {SOURCE_BADGE[c.source] ?? c.source}
                      </span>
                      {/* Applying spinner / hover overlay */}
                      {isThis ? (
                        <div className="absolute inset-0 bg-black/50 flex items-center justify-center">
                          <Loader2 className="h-6 w-6 text-white animate-spin drop-shadow" />
                        </div>
                      ) : (
                        <div className="absolute inset-0 bg-primary/20 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                          <Check className="h-6 w-6 text-white drop-shadow" />
                        </div>
                      )}
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {!loading && candidates.length === 0 && !error && (
            <p className="text-xs text-muted-foreground text-center py-4">No covers found — try a different search or use a custom URL below.</p>
          )}

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div className="flex-1 border-t border-border" />
            <span className="text-xs text-muted-foreground shrink-0">or use a custom source</span>
            <div className="flex-1 border-t border-border" />
          </div>

          {/* Custom URL */}
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-2">Custom URL</p>
            <div className="flex gap-2">
              <input
                type="url"
                value={customUrl}
                onChange={e => setCustomUrl(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && customUrl.trim() && applyUrl(customUrl.trim())}
                placeholder="https://example.com/cover.jpg"
                className="flex h-9 w-full rounded-md border border-border bg-transparent px-3 py-1 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              />
              <button
                disabled={!customUrl.trim() || applyingUrl !== null}
                onClick={() => applyUrl(customUrl.trim())}
                className="shrink-0 flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-40 transition-all"
              >
                {applyingUrl !== null ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                Apply
              </button>
            </div>
          </div>

          {/* File upload */}
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-2">Upload from device</p>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={e => {
                const f = e.target.files?.[0]
                if (f) applyFile(f)
                e.target.value = ''
              }}
            />
            <button
              disabled={applyingUrl !== null}
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border border-border bg-card text-foreground hover:bg-muted disabled:opacity-40 transition-all"
            >
              {applyingUrl !== null
                ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                : <Upload className="h-3.5 w-3.5 text-muted-foreground" />}
              Choose file…
            </button>
          </div>

          {error && (
            <div className="flex items-start gap-2 text-sm text-destructive">
              <ImageOff className="h-4 w-4 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
