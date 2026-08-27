import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Plus, Trash2, ChevronDown, ChevronUp,
  BookOpen, Loader2, Sparkles, Layers, ExternalLink,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { useAuth, isMember } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { listWishes, deleteWish, type WishOut } from '@/lib/wishlist'
import { api } from '@/lib/api'
import { getFollows, invalidateFollows, type FollowOut } from '@/lib/follows'
import { WishlistModal } from '@/components/WishlistModal'
import { SeriesCoverageStrip } from '@/components/SeriesCoverageStrip'
import { docsLink, DOCS } from '@/lib/docs'
import { cn } from '@/lib/utils'
import { Trans, Plural } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

function WishCover({ coverUrl }: { coverUrl: string | null }) {
  return (
    <div className="w-12 h-16 rounded bg-muted shrink-0 overflow-hidden flex items-center justify-center">
      {coverUrl ? (
        <img
          src={coverUrl}
          alt=""
          className="w-full h-full object-cover"
          onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
        />
      ) : (
        <BookOpen className="w-4 h-4 text-muted-foreground" />
      )}
    </div>
  )
}

function ageLabel(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const days = Math.floor(diff / 86_400_000)
  if (days === 0) return t`Today`
  if (days === 1) return t`Yesterday`
  if (days < 30) return t`${days}d ago`
  const months = Math.floor(days / 30)
  if (months < 12) return t`${months}mo ago`
  { const years = Math.floor(months / 12); return t`${years}y ago` }
}

// ── Following (release detection) ─────────────────────────────────────────────
// Renders nothing when TOME_RELEASE_DETECTION is off (the follows endpoint 403s).

interface SeriesHit {
  source_id: string
  name: string
  author: string | null
  total: number | null
  cover_url: string | null
}

const fmtVol = (n: number | null) => (n == null ? null : (Number.isInteger(n) ? String(n) : String(n)))

function FollowingSection() {
  // undefined = not resolved yet: render NOTHING (optimistic render flickered
  // on instances with release detection disabled); null = disabled.
  const [follows, setFollows] = useState<FollowOut[] | null | undefined>(undefined)
  const [q, setQ] = useState('')
  const [results, setResults] = useState<SeriesHit[]>([])
  const [searching, setSearching] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = () => { getFollows().then(setFollows) }
  useEffect(load, [])

  useEffect(() => {
    const query = q.trim()
    if (query.length < 2) { setResults([]); return }
    setSearching(true)
    const t = setTimeout(() => {
      api.get<SeriesHit[]>(`/wishlist/search-series?q=${encodeURIComponent(query)}`)
        .then(setResults)
        .catch(() => setResults([]))
        .finally(() => setSearching(false))
    }, 350)
    return () => clearTimeout(t)
  }, [q])

  const follow = async (r: SeriesHit) => {
    if (busy) return
    setBusy(true)
    try {
      await api.post('/wishlist/follow', {
        name: r.name, source_id: r.source_id, author: r.author, cover_url: r.cover_url,
      })
      setQ(''); setResults([])
      invalidateFollows()
      load()
    } catch { /* dup or offline — list stays as-is */ }
    finally { setBusy(false) }
  }

  const unfollow = async (id: number) => {
    try { await api.delete(`/wishlist/${id}`); invalidateFollows(); load() } catch { /* keep row */ }
  }

  if (follows == null) return null

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-sm font-semibold text-foreground"><Trans>Following</Trans></h2>
        <span className="text-xs text-muted-foreground"><Plural value={follows.length} one="# series" other="# series" /></span>
      </div>
      <p className="text-xs text-muted-foreground mb-3">
        <Trans>Get notified when a new volume of a followed series is released.</Trans>
      </p>

      <div className="relative mb-3">
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder={t`Follow a series — search Hardcover…`}
          className="w-full h-9 px-3 rounded-lg bg-muted border border-border text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        />
        {searching && <Loader2 className="absolute right-3 top-2.5 w-4 h-4 animate-spin text-muted-foreground" />}
        {results.length > 0 && (
          <div className="absolute z-10 mt-1 w-full rounded-lg border border-border bg-card shadow-xl overflow-hidden">
            {results.slice(0, 5).map(r => (
              <button
                key={r.source_id}
                onClick={() => follow(r)}
                disabled={busy}
                className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-muted transition-colors disabled:opacity-50"
              >
                {r.cover_url
                  ? <img src={r.cover_url} alt="" className="w-7 h-10 rounded object-cover shrink-0" />
                  : <span className="w-7 h-10 rounded bg-muted grid place-items-center shrink-0"><BookOpen className="w-3.5 h-3.5 text-muted-foreground/50" /></span>}
                <span className="min-w-0 flex-1">
                  <span className="block text-sm text-foreground truncate">{r.name}</span>
                  <span className="block text-xs text-muted-foreground truncate">
                    {r.author ?? t`Unknown author`}{(() => { const n = r.total; return n ? t` · ${n} volumes` : '' })()}
                  </span>
                </span>
                <Plus className="w-4 h-4 text-muted-foreground shrink-0" />
              </button>
            ))}
          </div>
        )}
      </div>

      {follows.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          {follows.map(f => (
            <div key={f.id} className="flex items-center gap-3 px-3 py-2.5 rounded-xl border border-border bg-card">
              {f.cover_url
                ? <img src={f.cover_url} alt="" className="w-9 h-[54px] rounded object-cover shrink-0" />
                : <span className="w-9 h-[54px] rounded bg-muted grid place-items-center shrink-0"><Layers className="w-4 h-4 text-muted-foreground/50" /></span>}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-foreground truncate">{f.name}</p>
                <p className="text-xs text-muted-foreground truncate">
                  {f.latest_known_index != null && (() => { const v = fmtVol(f.latest_known_index); return <Trans>Latest: vol {v}</Trans> })()}
                  {f.owned_max_index != null && (() => { const v = fmtVol(f.owned_max_index); return <Trans> · you have vol {v}</Trans> })()}
                </p>
              </div>
              <button
                onClick={() => unfollow(f.id)}
                title={t`Unfollow`}
                aria-label={(() => { const name = f.name; return t`Unfollow ${name}` })()}
                className="p-1.5 rounded text-muted-foreground/60 hover:text-destructive transition-colors"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function WishlistPage() {
  const { user } = useAuth()
  const { toast } = useToast()
  const [wishes, setWishes] = useState<WishOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  // Bumped on each open so the always-mounted modal remounts with fresh state
  // (it used to reset by conditional unmount, which killed the exit animation).
  const [addEpoch, setAddEpoch] = useState(0)
  const [fulfilledOpen, setFulfilledOpen] = useState(true)
  const [deleting, setDeleting] = useState<number | null>(null)

  const canWish = isMember(user)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const all = await listWishes()
      setWishes(all)
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Failed to load wishlist`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (canWish) {
      load()
    } else {
      setLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canWish])

  async function handleDelete(wish: WishOut) {
    setDeleting(wish.id)
    try {
      await deleteWish(wish.id)
      setWishes(prev => prev.filter(w => w.id !== wish.id))
      toast.success(t`Wish removed`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t`Failed to remove wish`)
    } finally {
      setDeleting(null)
    }
  }

  const openWishes = wishes.filter(w => w.status === 'open')
  const fulfilledWishes = wishes.filter(w => w.status === 'fulfilled')

  if (!canWish) {
    return (
      <AppShell>
        <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
          <Sparkles className="w-10 h-10 text-muted-foreground" />
          <p className="text-sm text-muted-foreground"><Trans>Members can add books to their wishlist.</Trans></p>
          <Link to="/" className="text-sm text-primary hover:underline"><Trans>Go back</Trans></Link>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell
      actions={
        <button
          onClick={() => { setAddEpoch(e => e + 1); setAddOpen(true) }}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all"
        >
          <Plus className="w-3.5 h-3.5" />
          <span className="hidden sm:inline"><Trans>Wish</Trans></span>
        </button>
      }
    >
      <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
        <h1 className="font-display text-xl text-foreground"><Trans>Wishlist</Trans></h1>
        {loading ? (
          <div className="flex justify-center py-16">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="px-3 py-2 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive">
            {error}
          </div>
        ) : (
          <>
            {/* Open wishes */}
            <div>
              {/* The top bar already says "Wishlist" — only label the section when there's a list to label */}
              {openWishes.length > 0 && (
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-semibold text-foreground">
                    <Trans>Open</Trans>
                    <span className="ml-2 text-xs font-normal text-muted-foreground">({openWishes.length})</span>
                  </h2>
                  <a
                    href={docsLink(DOCS.wishlist)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary transition-colors"
                  >
                    <Trans>Learn more</Trans> <ExternalLink className="w-3 h-3" />
                  </a>
                </div>
              )}

              {openWishes.length === 0 ? (
                <div className="flex flex-col items-center gap-4 py-12 text-center">
                  <Sparkles className="w-12 h-12 text-muted-foreground/30" />
                  <div>
                    <p className="text-sm font-medium text-foreground mb-1"><Trans>Your wishlist is empty</Trans></p>
                    <p className="text-xs text-muted-foreground">
                      <Trans>Add books you'd like to see in the library.</Trans>{' '}
                      <a
                        href={docsLink(DOCS.wishlist)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="underline underline-offset-2 hover:text-primary transition-colors"
                      >
                        <Trans>Learn more</Trans>
                      </a>
                    </p>
                  </div>
                  <button
                    onClick={() => { setAddEpoch(e => e + 1); setAddOpen(true) }}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all"
                  >
                    <Plus className="w-4 h-4" />
                    <Trans>Add a wish</Trans>
                  </button>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  {openWishes.map(w => (
                    <div key={w.id} className="flex items-start gap-3 p-3 rounded-xl border border-border bg-card">
                      <WishCover coverUrl={w.cover_url} />
                      <div className="flex-1 min-w-0 flex flex-col gap-0.5">
                        <p className="text-sm font-medium text-foreground leading-snug line-clamp-2">{w.title}</p>
                        {w.author && <p className="text-xs text-muted-foreground truncate">{w.author}</p>}
                        {w.series && w.series_index != null && (
                          <p className="text-xs text-muted-foreground/70">
                            {w.series} #{w.series_index}
                          </p>
                        )}
                        {w.series && w.series_index == null && (
                          <span className="mt-0.5 self-start inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-muted border border-border text-muted-foreground font-medium">
                            <Layers className="w-2.5 h-2.5" />
                            <Trans>Whole series</Trans>
                          </span>
                        )}
                        {w.series && w.series_index == null && w.series_coverage && w.series_coverage.length > 0 && (
                          <SeriesCoverageStrip coverage={w.series_coverage} total={w.series_total} />
                        )}
                        {w.note && (
                          <p className="text-xs text-muted-foreground italic mt-0.5 line-clamp-2">{w.note}</p>
                        )}
                        <p className="text-[10px] text-muted-foreground/60 mt-1">{ageLabel(w.created_at)}</p>
                      </div>
                      <button
                        onClick={() => handleDelete(w)}
                        disabled={deleting === w.id}
                        className="p-1.5 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors shrink-0 disabled:opacity-40"
                        title={t`Remove wish`}
                        aria-label={t`Remove wish`}
                      >
                        {deleting === w.id
                          ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          : <Trash2 className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Fulfilled wishes */}
            {fulfilledWishes.length > 0 && (
              <div>
                <button
                  onClick={() => setFulfilledOpen(o => !o)}
                  className="flex items-center gap-2 text-sm font-semibold text-muted-foreground hover:text-foreground transition-colors mb-3"
                >
                  {fulfilledOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                  <Trans>Fulfilled</Trans>
                  <span className="text-xs font-normal">({fulfilledWishes.length})</span>
                </button>

                {fulfilledOpen && (
                  <div className="flex flex-col gap-2">
                    {fulfilledWishes.map(w => (
                      <div
                        key={w.id}
                        className={cn(
                          'flex items-start gap-3 p-3 rounded-xl border border-border bg-card',
                          'opacity-70'
                        )}
                      >
                        <WishCover coverUrl={w.cover_url} />
                        <div className="flex-1 min-w-0 flex flex-col gap-0.5">
                          <p className="text-sm font-medium text-foreground leading-snug line-clamp-2">{w.title}</p>
                          {w.author && <p className="text-xs text-muted-foreground truncate">{w.author}</p>}
                          <span className="mt-1 self-start text-[10px] px-1.5 py-0.5 rounded bg-success/10 text-success border border-success/20 font-medium">
                            <Trans>Fulfilled</Trans>
                          </span>
                          {w.fulfilled_book_id && (
                            <Link
                              to={`/books/${w.fulfilled_book_id}`}
                              className="mt-1 text-xs text-primary hover:underline"
                            >
                              <Trans>View book</Trans>
                            </Link>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {!loading && !error && <FollowingSection />}
      </div>

      <WishlistModal
        key={addEpoch}
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onCreated={() => {
          load()
          toast.success(t`Wish added`)
        }}
      />
    </AppShell>
  )
}
