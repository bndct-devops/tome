import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Plus, Trash2, ChevronDown,
  BookOpen, Loader2, Layers, ExternalLink, Search,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { useAuth, isMember } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { listWishes, deleteWish, type WishOut } from '@/lib/wishlist'
import { api } from '@/lib/api'
import { getFollows, invalidateFollows, type FollowOut } from '@/lib/follows'
import { WishlistModal } from '@/components/WishlistModal'
import { SeriesCoverageStrip } from '@/components/SeriesCoverageStrip'
import { CoverImage } from '@/components/CoverImage'
import { PageBody, PageHeader, Pill, SectionTitle } from '@/components/page'
import { docsLink, DOCS } from '@/lib/docs'
import { cn } from '@/lib/utils'
import { Trans, Plural } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'

function RowCover({ url, fallback = 'book' }: { url: string | null; fallback?: 'book' | 'series' }) {
  return (
    <div className="relative w-11 h-16 rounded-[8px] overflow-hidden bg-muted shrink-0" style={{ boxShadow: 'var(--cover-bloom-fan)' }}>
      {url ? (
        <CoverImage src={url} alt="" iconClassName="w-4 h-4" />
      ) : (
        <span className="absolute inset-0 grid place-items-center">
          {fallback === 'series'
            ? <Layers className="w-4 h-4 text-muted-foreground/60" />
            : <BookOpen className="w-4 h-4 text-muted-foreground/60" />}
        </span>
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

const openDocs = () => window.open(docsLink(DOCS.wishlist), '_blank', 'noopener,noreferrer')

// Revealed on hover / keyboard focus on pointer devices; always visible on touch.
const ROW_TOOL = 'shrink-0 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 focus-visible:opacity-100'

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
    <section>
      <SectionTitle
        size={20}
        action={<span className="text-[14px] font-normal text-muted-foreground"><Plural value={follows.length} one="# series" other="# series" /></span>}
      >
        <Trans>Following</Trans>
      </SectionTitle>
      <p className="mt-2 text-[14px] text-muted-foreground">
        <Trans>Get notified when a new volume of a followed series is released.</Trans>
      </p>

      <div className="relative mt-4">
        <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder={t`Follow a series: search Hardcover…`}
          className="w-full h-9 pl-10 pr-9 rounded-full bg-secondary text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        {searching && <Loader2 className="absolute right-3.5 top-1/2 -translate-y-1/2 w-4 h-4 animate-spin text-muted-foreground" />}
        {results.length > 0 && (
          <div className="absolute z-10 mt-2 w-full rounded-[14px] bg-card shadow-xl overflow-hidden p-1">
            {results.slice(0, 5).map(r => (
              <button
                key={r.source_id}
                onClick={() => follow(r)}
                disabled={busy}
                className="flex w-full items-center gap-3 px-3 py-2 rounded-lg text-left hover:bg-secondary transition-colors disabled:opacity-50"
              >
                {r.cover_url
                  ? <img src={r.cover_url} alt="" className="w-7 h-10 rounded-[6px] object-cover shrink-0" />
                  : <span className="w-7 h-10 rounded-[6px] bg-muted grid place-items-center shrink-0"><BookOpen className="w-3.5 h-3.5 text-muted-foreground/50" /></span>}
                <span className="min-w-0 flex-1">
                  <span className="block text-[14px] font-medium text-foreground truncate">{r.name}</span>
                  <span className="block text-[13px] text-muted-foreground truncate">
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
        <div className="mt-3">
          {follows.map((f, i) => (
            <div key={f.id} className={cn('group flex items-center gap-4 py-3.5', i > 0 && 'border-t border-border/70')}>
              <RowCover url={f.cover_url} fallback="series" />
              <div className="min-w-0 flex-1">
                <p className="text-[16px] font-semibold text-foreground truncate">{f.name}</p>
                <p className="mt-0.5 text-[14px] text-muted-foreground truncate">
                  {f.latest_known_index != null && (() => { const v = fmtVol(f.latest_known_index); return <Trans>Latest: vol {v}</Trans> })()}
                  {f.owned_max_index != null && (() => { const v = fmtVol(f.owned_max_index); return <Trans> · you have vol {v}</Trans> })()}
                </p>
              </div>
              <Pill
                variant="destructive"
                icon
                onClick={() => unfollow(f.id)}
                title={t`Unfollow`}
                aria-label={(() => { const name = f.name; return t`Unfollow ${name}` })()}
                className={ROW_TOOL}
              >
                <Trash2 className="w-4 h-4" />
              </Pill>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

export function WishlistPage() {
  const { user } = useAuth()
  const { toast } = useToast()
  const navigate = useNavigate()
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

  const openAdd = () => { setAddEpoch(e => e + 1); setAddOpen(true) }

  const openWishes = wishes.filter(w => w.status === 'open')
  const fulfilledWishes = wishes.filter(w => w.status === 'fulfilled')

  if (!canWish) {
    return (
      <AppShell>
        <PageBody width="reading">
          <PageHeader title={<Trans>Wishlist</Trans>} />
          <div className="flex flex-col items-center gap-4 py-16 text-center">
            <p className="text-[15px] text-muted-foreground"><Trans>Members can add books to their wishlist.</Trans></p>
            <Pill variant="secondary" onClick={() => navigate('/')}><Trans>Go back</Trans></Pill>
          </div>
        </PageBody>
      </AppShell>
    )
  }

  // Wishes loaded once stay on screen (dimmed) while a reload runs.
  const firstLoad = loading && wishes.length === 0

  return (
    <AppShell>
      <PageBody width="reading">
        <PageHeader
          eyebrow={!firstLoad && !error ? plural(openWishes.length, { one: '# open', other: '# open' }) : undefined}
          title={<Trans>Wishlist</Trans>}
          actions={
            <>
              <Pill variant="quiet" onClick={openDocs}>
                <Trans>Learn more</Trans>
                <ExternalLink className="w-3.5 h-3.5" />
              </Pill>
              <Pill variant="primary" onClick={openAdd}>
                <Plus className="w-4 h-4" />
                <Trans>Wish</Trans>
              </Pill>
            </>
          }
        />

        {firstLoad ? (
          <div className="flex justify-center py-16">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <p className="text-[15px] text-destructive">{error}</p>
        ) : (
          <>
            {/* Open wishes */}
            {openWishes.length === 0 ? (
              <div className="flex flex-col items-center gap-4 py-12 text-center">
                <div>
                  <p className="text-[17px] font-semibold text-foreground"><Trans>Your wishlist is empty</Trans></p>
                  <p className="mt-1.5 text-[15px] text-muted-foreground">
                    <Trans>Add books you'd like to see in the library.</Trans>
                  </p>
                </div>
                <Pill variant="secondary" onClick={openAdd}>
                  <Plus className="w-4 h-4" />
                  <Trans>Add a wish</Trans>
                </Pill>
              </div>
            ) : (
              <section className={cn('transition-opacity', loading && 'opacity-50')}>
                <SectionTitle
                  size={20}
                  action={<span className="text-[14px] font-normal text-muted-foreground tabular-nums">{openWishes.length}</span>}
                >
                  <Trans>Open</Trans>
                </SectionTitle>
                <div className="mt-3">
                  {openWishes.map((w, i) => (
                    <div key={w.id} className={cn('group flex items-start gap-4 py-4', i > 0 && 'border-t border-border/70')}>
                      <RowCover url={w.cover_url} fallback={w.series && w.series_index == null ? 'series' : 'book'} />
                      <div className="flex-1 min-w-0">
                        <p className="text-[16px] font-semibold text-foreground leading-snug line-clamp-2">{w.title}</p>
                        {w.author && <p className="mt-0.5 text-[14px] text-muted-foreground truncate">{w.author}</p>}
                        {w.series && w.series_index != null && (
                          <p className="mt-0.5 text-[13px] text-muted-foreground">
                            {w.series} #{w.series_index}
                          </p>
                        )}
                        {w.series && w.series_index == null && (
                          <span className="mt-2 inline-flex items-center gap-1 h-6 px-2.5 rounded-full bg-secondary text-[12px] font-medium text-foreground">
                            <Layers className="w-3 h-3 text-muted-foreground" />
                            <Trans>Whole series</Trans>
                          </span>
                        )}
                        {w.series && w.series_index == null && w.series_coverage && w.series_coverage.length > 0 && (
                          <SeriesCoverageStrip coverage={w.series_coverage} total={w.series_total} look="chips" />
                        )}
                        {w.note && (
                          <p className="mt-1.5 text-[14px] text-muted-foreground italic line-clamp-2">{w.note}</p>
                        )}
                        <p className="mt-1 text-[13px] text-muted-foreground">{ageLabel(w.created_at)}</p>
                      </div>
                      <Pill
                        variant="destructive"
                        icon
                        onClick={() => handleDelete(w)}
                        disabled={deleting === w.id}
                        title={t`Remove wish`}
                        aria-label={t`Remove wish`}
                        className={cn(ROW_TOOL, deleting === w.id && 'opacity-100')}
                      >
                        {deleting === w.id
                          ? <Loader2 className="w-4 h-4 animate-spin" />
                          : <Trash2 className="w-4 h-4" />}
                      </Pill>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Fulfilled wishes */}
            {fulfilledWishes.length > 0 && (
              <section className={cn('transition-opacity', loading && 'opacity-50')}>
                <SectionTitle
                  size={20}
                  action={
                    <Pill
                      variant="quiet"
                      onClick={() => setFulfilledOpen(o => !o)}
                      aria-expanded={fulfilledOpen}
                      title={fulfilledOpen ? t`Collapse` : t`Expand`}
                      className="px-3 tabular-nums"
                    >
                      {fulfilledWishes.length}
                      <ChevronDown className={cn('w-4 h-4 transition-transform', !fulfilledOpen && '-rotate-90')} />
                    </Pill>
                  }
                  className="items-center"
                >
                  <button type="button" onClick={() => setFulfilledOpen(o => !o)} className="text-left hover:text-primary transition-colors">
                    <Trans>Fulfilled</Trans>
                  </button>
                </SectionTitle>

                {fulfilledOpen && (
                  <div className="mt-3">
                    {fulfilledWishes.map((w, i) => (
                      <div key={w.id} className={cn('flex items-center gap-4 py-3.5', i > 0 && 'border-t border-border/70')}>
                        <div className="opacity-70"><RowCover url={w.cover_url} /></div>
                        <div className="flex-1 min-w-0">
                          <p className="text-[16px] font-semibold text-foreground/80 leading-snug line-clamp-2">{w.title}</p>
                          <p className="mt-0.5 text-[14px] text-muted-foreground truncate">
                            {w.author && <>{w.author} · </>}
                            <span className="text-success font-medium"><Trans>Fulfilled</Trans></span>
                          </p>
                        </div>
                        {w.fulfilled_book_id && (
                          <Link
                            to={`/books/${w.fulfilled_book_id}`}
                            className="shrink-0 inline-flex items-center h-9 px-4 rounded-full text-[14px] font-medium text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
                          >
                            <Trans>View book</Trans>
                          </Link>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            )}
          </>
        )}

        {!firstLoad && !error && <FollowingSection />}
      </PageBody>

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
