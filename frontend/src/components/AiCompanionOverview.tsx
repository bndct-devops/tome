// Settings > AI "Companion": one row per series the reading companion knows
// (a standalone book is its own row). Each row shows a coverage strip (one
// segment per volume, filled by chapters read of total), the glossary counts,
// the recap states, the last run and this month's spend, and expands to the
// volume list. Row actions start the existing jobs on the row's anchor volume
// (the latest one in the viewer's scope): Read the missing chapters (with the
// estimate dialog over CONFIRM_THRESHOLD chapters), Merge duplicates, Write
// all (with the same dialog as the book page when it reads chapters first or
// passes the budget) and, for admins, Read again (always with its dialog).
// Actions are hidden when the reading companion is off, when the series has
// no chapter text, and on rows outside the viewer's own reading (an admin
// sees those for the coverage and readers only). A started or running job
// shows its step line with Stop and is polled until it ends.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Trans, Plural, useLingui } from '@lingui/react/macro'
import { ChevronDown, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { DigestConfirmDialog, type WriteAllConfirm } from '@/components/DigestConfirmDialog'
import {
  activeJobLine, cancelDigestJob, getCompanionOverview, getDigestEstimate, getDigestJob, getRecapVolumes,
  getRereadEstimate, startDigest, startReconcile, startReread, startWriteAll, useAiStatus, CONFIRM_THRESHOLD,
  DIGEST_JOB_ACTIVE, type DigestEstimate, type DigestJob, type DigestJobStatus, type DigestScope,
  type DigestStart, type OverviewJobKind, type OverviewSeries, type OverviewVolume, type RereadEstimate,
} from '@/lib/ai'

const POLL_MS = 3000

type Action = 'read' | 'merge' | 'write' | 'reread'

interface ConfirmState {
  key: string
  anchor: number
  mode: 'read' | 'reread'
  book: DigestEstimate | null
  series: DigestEstimate | null
  scope: DigestScope
  deletes?: RereadEstimate['deletes']
}

interface WriteConfirmState {
  key: string
  anchor: number
  writeAll: WriteAllConfirm
}

const isActive = (j: DigestJob | null | undefined): j is DigestJob => j != null && DIGEST_JOB_ACTIVE.includes(j.status)

export function AiCompanionOverview({ isAdmin, onSettled }: {
  isAdmin: boolean
  /** Called when a job started or watched here ends, so the spend meter can refresh. */
  onSettled?: () => void
}) {
  const { t, i18n } = useLingui()
  const [data, setData] = useState<OverviewSeries[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [open, setOpen] = useState(true)
  const [openRows, setOpenRows] = useState<Set<string>>(() => new Set())
  // Live job per row key: started here or reported active by the overview.
  const [jobs, setJobs] = useState<Record<string, DigestJob>>({})
  const [busy, setBusy] = useState<Record<string, Action | undefined>>({})
  const [notes, setNotes] = useState<Record<string, string | undefined>>({})
  const [stopping, setStopping] = useState<Set<string>>(() => new Set())
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [confirmBusy, setConfirmBusy] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [writeConfirm, setWriteConfirm] = useState<WriteConfirmState | null>(null)
  const { available } = useAiStatus()
  const digestOn = available('reading_digest')
  const settledRef = useRef(onSettled)
  useEffect(() => { settledRef.current = onSettled })
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const load = useCallback(async () => {
    try {
      const d = await getCompanionOverview()
      if (!alive.current) return
      setData(d.series)
      setFailed(false)
      setJobs(prev => {
        const next: Record<string, DigestJob> = {}
        for (const s of d.series) {
          const live = isActive(s.active_job) ? s.active_job : isActive(prev[s.key]) ? prev[s.key] : null
          if (live) next[s.key] = live
        }
        return next
      })
    } catch {
      if (alive.current) setFailed(true)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  // Poll every live job; when one ends, reload the overview (its row's
  // coverage, recaps and last run changed) and tell the spend meter.
  const liveIds = Object.values(jobs).map(j => j.id).sort().join(',')
  useEffect(() => {
    if (!liveIds) return
    const timer = window.setInterval(() => {
      void (async () => {
        let ended = false
        const entries = Object.entries(jobs)
        const fresh = await Promise.all(entries.map(([, j]) => getDigestJob(j.id).catch(() => j)))
        if (!alive.current) return
        entries.forEach(([key], i) => {
          const j = fresh[i]
          if (!DIGEST_JOB_ACTIVE.includes(j.status) && j.stop_reason) setNote(key, j.stop_reason)
        })
        setJobs(prev => {
          const next = { ...prev }
          entries.forEach(([key], i) => {
            const j = fresh[i]
            if (isActive(j)) next[key] = j
            else { delete next[key]; ended = true }
          })
          return next
        })
        if (fresh.some(j => !isActive(j))) ended = true
        if (ended) {
          void load()
          settledRef.current?.()
        }
      })()
    }, POLL_MS)
    return () => window.clearInterval(timer)
    // jobs is read through the closure at each tick; restart only when the set of ids changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveIds, load])

  const money = (n: number | null | undefined) => new Intl.NumberFormat(i18n.locale, {
    style: 'currency', currency: 'USD', minimumFractionDigits: 2,
    maximumFractionDigits: n != null && n > 0 && n < 1 ? 3 : 2,
  }).format(n ?? 0)
  const ago = (iso: string | null): string => {
    if (!iso) return ''
    const secs = (new Date(iso).getTime() - Date.now()) / 1000
    const rtf = new Intl.RelativeTimeFormat(i18n.locale, { numeric: 'auto', style: 'short' })
    const abs = Math.abs(secs)
    if (abs < 60) return rtf.format(Math.round(secs), 'second')
    if (abs < 3600) return rtf.format(Math.round(secs / 60), 'minute')
    if (abs < 86400) return rtf.format(Math.round(secs / 3600), 'hour')
    if (abs < 86400 * 30) return rtf.format(Math.round(secs / 86400), 'day')
    return new Date(iso).toLocaleDateString(i18n.locale, { day: 'numeric', month: 'short', year: 'numeric' })
  }
  const date = (iso: string | null) => iso
    ? new Date(iso).toLocaleDateString(i18n.locale, { day: 'numeric', month: 'short', year: 'numeric' })
    : ''
  const kindLabel = (k: OverviewJobKind): string => {
    switch (k) {
      case 'read': return t`Read`
      case 'merge': return t`Merge`
      case 'reread': return t`Read again`
      case 'write_all': return t`Write all`
    }
  }
  // Same words as the AI activity list.
  const statusLabel = (st: DigestJobStatus): string => {
    switch (st) {
      case 'queued': return t`Waiting`
      case 'running': return t`Running`
      case 'stopping': return t`Stopping`
      case 'done': return t`Done`
      case 'cancelled': return t`Stopped by you`
      case 'stopped': return t`Stopped`
      case 'error': return t`Failed`
      case 'interrupted': return t`Interrupted`
    }
  }
  const recapLabel = (v: OverviewVolume): string => {
    switch (v.recap) {
      case 'ready': return t`Recap ready`
      case 'partial': return t`Recap partial`
      case 'missing': return t`No recap`
      case 'none': return t`No chapter text`
    }
  }

  function setRowBusy(key: string, a: Action | undefined) {
    setBusy(prev => ({ ...prev, [key]: a }))
  }
  function setNote(key: string, note: string | undefined) {
    setNotes(prev => ({ ...prev, [key]: note }))
  }
  function track(key: string, job: DigestJob | null | undefined, nothing: string) {
    const reason = job?.stop_reason
    if (isActive(job)) {
      setJobs(prev => ({ ...prev, [key]: job }))
      setNote(key, undefined)
    } else {
      setNote(key, reason || nothing)
      void load()
    }
  }
  const errText = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback)

  async function begin(c: Pick<ConfirmState, 'key' | 'anchor' | 'mode'>, scope: DigestScope): Promise<string | null> {
    try {
      const res: DigestStart = c.mode === 'reread' ? await startReread(c.anchor, scope) : await startDigest(c.anchor, scope)
      if (!alive.current) return null
      track(c.key, res.job, t`Nothing is missing.`)
      if (c.mode === 'reread') void load()
      return null
    } catch (e) {
      return errText(e, t`Could not start reading the chapters`)
    }
  }

  async function readMissing(row: OverviewSeries) {
    setNote(row.key, undefined)
    setRowBusy(row.key, 'read')
    try {
      const est = await getDigestEstimate(row.anchor_book_id)
      if (!alive.current) return
      const chosen = est.series ?? est.book
      if (chosen.chapters === 0 && est.book.chapters === 0) {
        setNote(row.key, t`Nothing is missing.`)
        return
      }
      if (chosen.chapters > 0 && chosen.chapters <= CONFIRM_THRESHOLD && !chosen.over_budget) {
        const err = await begin({ key: row.key, anchor: row.anchor_book_id, mode: 'read' }, chosen.scope)
        if (err) setNote(row.key, err)
        return
      }
      setConfirmError(null)
      setConfirm({ key: row.key, anchor: row.anchor_book_id, mode: 'read', book: est.book, series: est.series, scope: chosen.scope })
    } catch (e) {
      if (alive.current) setNote(row.key, errText(e, t`Could not start reading the chapters`))
    } finally {
      if (alive.current) setRowBusy(row.key, undefined)
    }
  }

  async function readAgain(row: OverviewSeries) {
    setNote(row.key, undefined)
    setRowBusy(row.key, 'reread')
    try {
      const est = await getRereadEstimate(row.anchor_book_id)
      if (!alive.current) return
      const bookChapters = est.book?.chapters ?? 0
      const seriesChapters = est.series?.chapters ?? 0
      if (bookChapters === 0 && seriesChapters === 0) {
        setNote(row.key, t`Nothing to read again.`)
        return
      }
      setConfirmError(null)
      setConfirm({
        key: row.key, anchor: row.anchor_book_id, mode: 'reread', book: est.book, series: est.series,
        scope: seriesChapters > 0 ? 'series' : 'book', deletes: est.deletes,
      })
    } catch (e) {
      if (alive.current) setNote(row.key, errText(e, t`Could not start reading the chapters`))
    } finally {
      if (alive.current) setRowBusy(row.key, undefined)
    }
  }

  async function merge(row: OverviewSeries) {
    setNote(row.key, undefined)
    setRowBusy(row.key, 'merge')
    try {
      const res = await startReconcile(row.anchor_book_id)
      if (alive.current) track(row.key, res.job, t`Nothing to do.`)
    } catch (e) {
      if (alive.current) setNote(row.key, errText(e, t`Could not start merging`))
    } finally {
      if (alive.current) setRowBusy(row.key, undefined)
    }
  }

  /** POST write-all on the anchor. Returns an error message, or null when it started. */
  async function beginWriteAll(key: string, anchor: number): Promise<string | null> {
    try {
      const res = await startWriteAll(anchor)
      if (alive.current) track(key, res.job, t`Nothing to do.`)
      return null
    } catch (e) {
      return errText(e, t`Could not start writing the recaps`)
    }
  }

  // "Write all" asks first exactly like the book page: when it reads
  // chapters before writing, or when the whole run would pass the budget.
  async function writeAll(row: OverviewSeries) {
    setNote(row.key, undefined)
    setRowBusy(row.key, 'write')
    try {
      const vols = await getRecapVolumes(row.anchor_book_id)
      if (!alive.current) return
      const wa = vols.write_all
      if (isActive(wa.job)) { track(row.key, wa.job, t`Nothing to do.`); return }
      const readingChapters = wa.reading?.chapters ?? 0
      const remaining = wa.remaining_budget_usd ?? null
      const mergeCost = (wa.merging?.pairs ?? 0) > 0 ? wa.merging?.est_cost_usd ?? 0 : 0
      const totalCost = (wa.est_cost_usd ?? 0) + (wa.reading?.est_cost_usd ?? 0) + mergeCost
      const overBudget = remaining != null && totalCost > remaining
      if (readingChapters > 0 || overBudget) {
        setConfirmError(null)
        setWriteConfirm({
          key: row.key,
          anchor: row.anchor_book_id,
          writeAll: {
            reading: wa.reading,
            count: wa.count,
            est_cost_usd: wa.est_cost_usd,
            merging: wa.merging ?? null,
            remaining_budget_usd: remaining,
          },
        })
        return
      }
      const err = await beginWriteAll(row.key, row.anchor_book_id)
      if (err && alive.current) setNote(row.key, err)
    } catch (e) {
      if (alive.current) setNote(row.key, errText(e, t`Could not start writing the recaps`))
    } finally {
      if (alive.current) setRowBusy(row.key, undefined)
    }
  }

  async function confirmWriteAll() {
    if (!writeConfirm) return
    setConfirmBusy(true)
    setConfirmError(null)
    const err = await beginWriteAll(writeConfirm.key, writeConfirm.anchor)
    if (!alive.current) return
    setConfirmBusy(false)
    if (err) { setConfirmError(err); return }
    setWriteConfirm(null)
  }

  async function confirmStart(scope: DigestScope) {
    if (!confirm) return
    setConfirmBusy(true)
    setConfirmError(null)
    const err = await begin(confirm, scope)
    if (!alive.current) return
    setConfirmBusy(false)
    if (err) { setConfirmError(err); return }
    setConfirm(null)
  }

  async function stop(key: string, id: string) {
    setStopping(prev => new Set(prev).add(id))
    try {
      const j = await cancelDigestJob(id)
      if (alive.current && isActive(j)) setJobs(prev => ({ ...prev, [key]: j }))
    } catch { /* the next poll shows the state */ }
    if (alive.current) setStopping(prev => { const next = new Set(prev); next.delete(id); return next })
  }

  function toggleRow(key: string) {
    setOpenRows(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const linkClassName = 'text-xs font-medium text-primary hover:underline disabled:opacity-50 disabled:no-underline'

  return (
    <div className="p-5 space-y-3">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
      >
        <ChevronDown className={cn('w-3 h-3 transition-transform', !open && '-rotate-90')} />
        <Trans>Companion</Trans>
        {data != null && (
          <span className="font-normal text-muted-foreground/70">
            {' · '}<Plural value={data.length} one="# series" other="# series" />
          </span>
        )}
      </button>

      {open && (failed && !data ? (
        <p className="text-xs text-muted-foreground"><Trans>Could not load the companion overview.</Trans></p>
      ) : !data ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> <Trans>Loading…</Trans>
        </div>
      ) : data.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {isAdmin
            ? <Trans>Nothing yet. When the reading companion reads chapters of a book, its series shows up here.</Trans>
            : <Trans>Nothing yet. Series you read or have open show up here with what the reading companion knows about them.</Trans>}
        </p>
      ) : (
        <div className="rounded-lg border border-border overflow-hidden text-xs divide-y divide-border">
          {data.map(row => {
            const job = jobs[row.key] ?? null
            const rowBusy = busy[row.key]
            const disabled = rowBusy != null || isActive(job)
            const expanded = openRows.has(row.key)
            const ready = row.volumes.filter(v => v.recap === 'ready').length
            const partial = row.volumes.filter(v => v.recap === 'partial').length
            const missing = row.volumes.filter(v => v.recap === 'missing').length
            const people = row.people
            const places = row.places
            const terms = row.terms
            const spend = money(row.spend_month_usd)
            const readers = row.readers.map(r => r.username).join(', ')
            const inScope = row.in_scope !== false
            const anchorVol = row.volumes.find(v => v.book_id === row.anchor_book_id)
            const hasText = row.volumes.some(v => v.chapters > 0) && (anchorVol == null || anchorVol.chapters > 0)
            const showActions = digestOn && inScope && hasText
            const lastStatus = row.last_job && row.last_job.status !== 'done' ? statusLabel(row.last_job.status) : null
            return (
              <div key={row.key}>
                <button
                  type="button"
                  onClick={() => toggleRow(row.key)}
                  aria-expanded={expanded}
                  className="w-full grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 items-center px-3 pt-2.5 pb-2 text-left hover:bg-muted/50 transition-colors"
                >
                  <span className="flex items-center gap-1.5 min-w-0">
                    <ChevronDown className={cn('w-3 h-3 shrink-0 text-muted-foreground/60 transition-transform', !expanded && '-rotate-90')} />
                    <span className="text-sm text-foreground truncate">{row.name}</span>
                    {row.author && <span className="text-muted-foreground truncate hidden sm:inline">· {row.author}</span>}
                  </span>
                  <span className="tabular-nums text-sm text-foreground" title={t`Spent this month`}>{spend}</span>
                  <span className="pl-[18px] col-span-2">
                    <CoverageStrip volumes={row.volumes} />
                  </span>
                  <span className="pl-[18px] text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-0.5 min-w-0">
                    <span>
                      <Plural value={people} one="# person" other="# people" />
                      {' · '}<Plural value={places} one="# place" other="# places" />
                      {' · '}<Plural value={terms} one="# term" other="# terms" />
                    </span>
                    {ready > 0 && <Chip tone="ready"><Plural value={ready} one="# recap ready" other="# recaps ready" /></Chip>}
                    {partial > 0 && <Chip tone="partial"><Plural value={partial} one="# partial" other="# partial" /></Chip>}
                    {missing > 0 && <Chip tone="missing"><Plural value={missing} one="# missing" other="# missing" /></Chip>}
                  </span>
                  <span className="text-right text-muted-foreground tabular-nums whitespace-nowrap">
                    {row.last_job && (
                      <>
                        {kindLabel(row.last_job.kind)}
                        {lastStatus && <> · {lastStatus}</>}
                        {' · '}{ago(row.last_job.finished_at)} · {money(row.last_job.cost_usd)}
                      </>
                    )}
                  </span>
                  {isAdmin && readers && (
                    <span className="pl-[18px] col-span-2 text-muted-foreground/80 truncate">
                      <Trans>Readers: {readers}</Trans>
                    </span>
                  )}
                </button>

                <div className="px-3 pb-2.5 pl-[30px] space-y-1.5">
                  {isActive(job) ? (
                    <div className="flex items-center gap-2 text-muted-foreground" role="status">
                      <Loader2 className="w-3 h-3 animate-spin shrink-0" />
                      <span className="truncate flex-1">{activeJobLine(job)}</span>
                      {job.status !== 'stopping' && (
                        <button
                          type="button"
                          onClick={() => void stop(row.key, job.id)}
                          disabled={stopping.has(job.id)}
                          className="shrink-0 px-2.5 py-1 rounded-md border border-border text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
                        >
                          <Trans>Stop</Trans>
                        </button>
                      )}
                    </div>
                  ) : !inScope ? (
                    <p className="text-muted-foreground">
                      <Trans>You have not read this series; start its jobs from a volume you have read.</Trans>
                    </p>
                  ) : showActions && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                      <button type="button" className={linkClassName} disabled={disabled} onClick={() => void readMissing(row)}>
                        {rowBusy === 'read' && <Loader2 className="inline w-3 h-3 mr-1 animate-spin" />}
                        <Trans>Read the missing chapters</Trans>
                      </button>
                      <button type="button" className={linkClassName} disabled={disabled} onClick={() => void merge(row)}>
                        {rowBusy === 'merge' && <Loader2 className="inline w-3 h-3 mr-1 animate-spin" />}
                        <Trans>Merge duplicates</Trans>
                      </button>
                      <button type="button" className={linkClassName} disabled={disabled} onClick={() => void writeAll(row)}>
                        {rowBusy === 'write' && <Loader2 className="inline w-3 h-3 mr-1 animate-spin" />}
                        <Trans>Write all</Trans>
                      </button>
                      {isAdmin && (
                        <button
                          type="button"
                          className={linkClassName}
                          disabled={disabled}
                          onClick={() => void readAgain(row)}
                          title={t`Deletes the stored notes for everyone and reads the chapters again`}
                        >
                          {rowBusy === 'reread' && <Loader2 className="inline w-3 h-3 mr-1 animate-spin" />}
                          <Trans>Read again</Trans>
                        </button>
                      )}
                    </div>
                  )}
                  {notes[row.key] && <p className="text-muted-foreground">{notes[row.key]}</p>}
                </div>

                {expanded && (
                  <div className="bg-muted/30 border-t border-border/60 divide-y divide-border/60">
                    {row.volumes.map((v, i) => {
                      const idx = v.series_index ?? i + 1
                      const digested = v.digested
                      const chapters = v.chapters
                      return (
                        <div key={v.book_id} className="grid grid-cols-[1fr_auto] sm:grid-cols-[1fr_7rem_7rem_6rem] gap-x-3 gap-y-0.5 px-3 py-2 pl-[30px]">
                          <span className="text-foreground truncate">
                            {row.volumes.length > 1 && <span className="text-muted-foreground tabular-nums">#{idx} </span>}
                            {v.title}
                          </span>
                          <span className="text-muted-foreground tabular-nums text-right sm:text-left">
                            <Trans>{digested} of {chapters} read</Trans>
                          </span>
                          <span className="text-muted-foreground">{recapLabel(v)}</span>
                          <span className="text-muted-foreground tabular-nums text-right">{date(v.last_digest_at)}</span>
                        </div>
                      )
                    })}
                    {row.last_merge && (() => {
                      const merged = row.last_merge.merged
                      const candidates = row.last_merge.candidates
                      const when = ago(row.last_merge.finished_at)
                      return (
                        <p className="px-3 py-2 pl-[30px] text-muted-foreground">
                          <Trans>Last merge {when}: {merged} of {candidates} candidate pairs merged</Trans>
                        </p>
                      )
                    })()}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      ))}

      <DigestConfirmDialog
        open={confirm != null}
        book={confirm?.book ?? null}
        series={confirm?.series ?? null}
        scope={confirm?.scope}
        busy={confirmBusy}
        error={confirmError}
        onCancel={() => setConfirm(null)}
        onStart={scope => void confirmStart(scope)}
        reread={confirm?.mode === 'reread' ? confirm.deletes ?? null : null}
      />
      {writeConfirm && (
        <DigestConfirmDialog
          open
          book={null}
          series={null}
          writeAll={writeConfirm.writeAll}
          busy={confirmBusy}
          error={confirmError}
          onCancel={() => setWriteConfirm(null)}
          onStart={() => void confirmWriteAll()}
        />
      )}
    </div>
  )
}

function CoverageStrip({ volumes }: { volumes: OverviewVolume[] }) {
  const { t } = useLingui()
  return (
    <span className="flex gap-0.5 w-full" aria-hidden>
      {volumes.map((v, i) => {
        const idx = v.series_index ?? i + 1
        const digested = v.digested
        const chapters = v.chapters
        const pct = chapters > 0 ? Math.min(100, Math.round((digested / chapters) * 100)) : 0
        return (
          <span
            key={v.book_id}
            title={chapters > 0 ? t`vol ${idx}: ${digested} of ${chapters}` : t`vol ${idx}: no chapter text`}
            className={cn('flex-1 h-1.5 rounded-full overflow-hidden', chapters > 0 ? 'bg-muted' : 'bg-muted/40')}
          >
            <span className="block h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
          </span>
        )
      })}
    </span>
  )
}

function Chip({ tone, children }: { tone: 'ready' | 'partial' | 'missing'; children: React.ReactNode }) {
  return (
    <span className={cn(
      'px-1.5 py-px rounded-full border text-[11px] leading-4',
      tone === 'ready' && 'border-primary/30 text-primary',
      tone === 'partial' && 'border-border text-foreground',
      tone === 'missing' && 'border-border text-muted-foreground',
    )}>
      {children}
    </span>
  )
}

export default AiCompanionOverview
