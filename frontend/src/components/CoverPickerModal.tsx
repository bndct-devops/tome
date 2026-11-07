import { useEffect, useRef, useState } from 'react'
import { Check, ImageOff, Loader2, Search, Upload } from 'lucide-react'
import type { BookDetail } from '@/lib/books'
import { ModalShell } from '@/components/ModalShell'
import { FormLabel, Input, Pill } from '@/components/page'
import { cn } from '@/lib/utils'
import { Trans, Plural } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

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

/* eslint-disable lingui/no-unlocalized-strings -- provider names */
const SOURCE_BADGE: Record<string, string> = {
  hardcover: 'HC',
  google_books: 'Google',
  open_library: 'OpenLib',
}
/* eslint-enable lingui/no-unlocalized-strings */

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
    // Earlier results stay (dimmed) until the new ones land.
    setLoading(true)
    setError(null)
    try {
      const qs = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''
      const r = await fetch(`${API}/api/books/${book.id}/cover-candidates${qs}`, {
        headers: authHeader(),
      })
      if (!r.ok) throw new Error(await r.text())
      const data: CoverCandidate[] = await r.json()
      setCandidates(data)
      if (data.length === 0) setError(t`No cover candidates found.`)
    } catch (e: unknown) {
      setCandidates([])
      setError(e instanceof Error ? e.message : t`Failed to fetch candidates`)
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
      setError(e instanceof Error ? e.message : t`Failed to apply cover`)
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
      setError(e instanceof Error ? e.message : t`Failed to upload cover`)
    } finally {
      setApplyingUrl(null)
    }
  }

  function handleClose() {
    // State resets on the next open; keeping it here would blank the content
    // mid-exit-animation.
    onClose()
  }

  return (
    <ModalShell
      open={open}
      onClose={handleClose}
      title={<Trans>Change cover</Trans>}
      eyebrow={book.title}
      size="lg"
      footer={<Pill variant="quiet" onClick={handleClose}><Trans>Cancel</Trans></Pill>}
    >
      <div className="flex flex-col gap-6">
        {/* Search */}
        <div className="flex gap-2">
          <Input
            ref={queryRef}
            placeholder={`${book.title}${book.author ? ` ${book.author}` : ''}`}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && fetchCandidates(query)}
            aria-label={t`Search covers`}
          />
          <Pill onClick={() => fetchCandidates(query)} disabled={loading} className="shrink-0">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            <Trans>Search</Trans>
          </Pill>
        </div>

        {/* Candidate grid */}
        {loading && candidates.length === 0 && (
          <div className="flex justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {candidates.length > 0 && (
          <div className={cn('transition-opacity', loading && 'pointer-events-none opacity-50')}>
            <p className="mb-3 text-[13px] text-muted-foreground">
              <Plural value={candidates.length} one="# cover found, click to apply" other="# covers found, click to apply" />
            </p>
            <div className="grid grid-cols-3 gap-3 sm:grid-cols-5">
              {candidates.map((c, i) => {
                const isThis = applyingUrl === c.cover_url
                return (
                  <button
                    key={i}
                    type="button"
                    disabled={applyingUrl !== null}
                    onClick={() => applyUrl(c.cover_url)}
                    className="group relative aspect-[2/3] overflow-hidden rounded-[8px] bg-secondary transition hover:ring-2 hover:ring-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed"
                    style={{ boxShadow: 'var(--cover-bloom-fan)' }}
                    title={c.label}
                  >
                    <img
                      src={c.cover_url}
                      alt={c.label}
                      className="h-full w-full object-cover"
                      onError={e => {
                        const el = e.target as HTMLImageElement
                        el.style.display = 'none'
                        el.nextElementSibling?.classList.remove('hidden')
                      }}
                    />
                    <div className="absolute inset-0 hidden items-center justify-center">
                      <ImageOff className="h-6 w-6 text-muted-foreground" />
                    </div>
                    {/* Source badge */}
                    <span className="absolute left-1.5 top-1.5 rounded-full bg-black/60 px-1.5 py-0.5 text-[10px] font-bold uppercase leading-none tracking-[0.08em] text-white">
                      {SOURCE_BADGE[c.source] ?? c.source}
                    </span>
                    {/* Applying spinner / hover overlay */}
                    {isThis ? (
                      <div className="absolute inset-0 flex items-center justify-center bg-black/50">
                        <Loader2 className="h-6 w-6 animate-spin text-white drop-shadow" />
                      </div>
                    ) : (
                      <div className="absolute inset-0 flex items-center justify-center bg-primary/20 opacity-0 transition-opacity group-hover:opacity-100">
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
          <p className="py-4 text-center text-[14px] text-muted-foreground"><Trans>No covers found. Try a different search or use a custom URL below.</Trans></p>
        )}

        {/* Other sources: rows on hairlines */}
        <div className="border-t border-border/70">
          <div className="border-b border-border/70 py-4">
            <FormLabel htmlFor="cover-custom-url"><Trans>Custom URL</Trans></FormLabel>
            <div className="flex gap-2">
              <Input
                id="cover-custom-url"
                type="url"
                value={customUrl}
                onChange={e => setCustomUrl(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && customUrl.trim() && applyUrl(customUrl.trim())}
                placeholder="https://example.com/cover.jpg"
              />
              <Pill
                disabled={!customUrl.trim() || applyingUrl !== null}
                onClick={() => applyUrl(customUrl.trim())}
                className="shrink-0"
              >
                {applyingUrl !== null && applyingUrl !== '__file__' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                <Trans>Apply</Trans>
              </Pill>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 py-4">
            <div className="min-w-0">
              <p className="text-[15px] text-foreground"><Trans>Upload from device</Trans></p>
              <p className="text-[13px] text-muted-foreground"><Trans>Any image file</Trans></p>
            </div>
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
            <Pill
              disabled={applyingUrl !== null}
              onClick={() => fileInputRef.current?.click()}
            >
              {applyingUrl === '__file__'
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Upload className="h-4 w-4" />}
              <Trans>Choose file…</Trans>
            </Pill>
          </div>
        </div>

        {error && (
          <div className="flex items-start gap-2 text-[14px] text-destructive">
            <ImageOff className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
      </div>
    </ModalShell>
  )
}
