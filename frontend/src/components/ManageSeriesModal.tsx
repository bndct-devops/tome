import { useEffect, useState, type ReactNode } from 'react'
import { Trans } from '@lingui/react/macro'
import { t, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'
import { X, Plus, Trash2, Loader2, Save, Check, ArrowRight, RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { ModalShell } from '@/components/ModalShell'
import type { Arc, SeriesMeta, SeriesStatus } from '@/lib/books'
import { cn } from '@/lib/utils'
import { AiBadge } from '@/components/AiBadge'
import {
  applySeriesCleanup, proposeSeriesCleanup, useAiStatus,
  type SeriesCleanupApply, type SeriesCleanupArc, type SeriesCleanupProposal,
} from '@/lib/ai'

interface Props {
  seriesName: string
  /** All series_index values that currently exist for this series (for Start/End dropdowns). */
  volumes: number[]
  onClose: () => void
  /** renamedTo is set when an AI cleanup renamed the series. */
  onSaved: (renamedTo?: string) => void
}

type TabId = 'series' | 'arcs' | 'ai'

/** Which parts of an AI cleanup proposal the user kept checked. */
interface CleanupSelection {
  name: boolean
  books: Record<number, boolean>
  status: boolean
  /** By index into proposal.arcs.proposed. */
  arcs: Record<number, boolean>
}

interface ArcRow {
  /** undefined = new row (no id yet) */
  id?: number
  name: string
  start_index: number
  end_index: number
  description: string
}

const STATUS_OPTIONS: { value: SeriesStatus; label: MessageDescriptor }[] = [
  { value: 'ongoing', label: msg`Ongoing` },
  { value: 'finished', label: msg({ message: 'Finished', context: 'series' }) },
  { value: 'hiatus', label: msg`Hiatus` },
  { value: 'unknown', label: msg`Unknown` },
]

function hasOverlap(rows: ArcRow[], idx: number): boolean {
  const r = rows[idx]
  return rows.some((other, i) => {
    if (i === idx) return false
    return r.start_index <= other.end_index && r.end_index >= other.start_index
  })
}

function isInvalid(row: ArcRow): boolean {
  return row.start_index > row.end_index
}

function formatVol(n: number): string {
  return Number.isInteger(n) ? String(n) : String(n)
}

function arcRange(a: SeriesCleanupArc): string {
  return a.start_index === a.end_index
    ? formatVol(a.start_index)
    : `${formatVol(a.start_index)}-${formatVol(a.end_index)}`
}

function statusLabel(value: SeriesStatus): string {
  const opt = STATUS_OPTIONS.find(o => o.value === value)
  return opt ? i18n._(opt.label) : value
}

function initialSelection(p: SeriesCleanupProposal): CleanupSelection {
  return {
    name: p.series_name.proposed != null && p.permissions.series_name,
    books: Object.fromEntries(p.books.map(r => [r.book_id, r.editable])),
    status: p.status.proposed != null && p.permissions.status,
    arcs: Object.fromEntries((p.arcs.proposed ?? []).map((_, i) => [i, p.permissions.arcs])),
  }
}

function selectionToBody(p: SeriesCleanupProposal, sel: CleanupSelection): SeriesCleanupApply {
  const body: SeriesCleanupApply = {}
  if (sel.name && p.series_name.proposed != null) body.series_name = p.series_name.proposed
  const books = p.books
    .filter(r => sel.books[r.book_id] && r.editable)
    .map(r => ({ book_id: r.book_id, title: r.proposed.title, series_index: r.proposed.series_index }))
  if (books.length > 0) body.books = books
  if (sel.status && p.status.proposed != null) body.status = p.status.proposed
  const arcs = (p.arcs.proposed ?? []).filter((_, i) => sel.arcs[i])
  if (arcs.length > 0 && p.permissions.arcs) body.arcs = arcs
  return body
}

function countSelected(body: SeriesCleanupApply): number {
  return (body.series_name != null ? 1 : 0) + (body.books?.length ?? 0)
    + (body.status != null ? 1 : 0) + (body.arcs?.length ?? 0)
}

/** "old -> new", with the old value struck through. */
function Change({ from, to }: { from: string; to: string }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className="text-muted-foreground line-through decoration-muted-foreground/60">{from}</span>
      <ArrowRight className="w-3 h-3 text-muted-foreground shrink-0" aria-hidden="true" />
      <span className="font-medium text-foreground">{to}</span>
    </span>
  )
}

function CleanupGroup({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
        {note && <p className="text-xs text-muted-foreground mt-0.5">{note}</p>}
      </div>
      <div className="rounded-lg border border-border divide-y divide-border">{children}</div>
    </section>
  )
}

function CleanupRow({ checked, disabled, disabledNote, onToggle, evidence, children }: {
  checked: boolean
  disabled?: boolean
  disabledNote?: string
  onToggle: () => void
  evidence?: string
  children: ReactNode
}) {
  return (
    <label className={cn('flex items-start gap-3 px-3 py-2.5', disabled ? 'opacity-60' : 'cursor-pointer hover:bg-muted/50')}>
      <input
        type="checkbox"
        checked={checked && !disabled}
        disabled={disabled}
        onChange={onToggle}
        className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
      />
      <div className="min-w-0 flex-1">
        <div className="text-sm text-foreground">{children}</div>
        {evidence && <p className="text-xs text-muted-foreground mt-0.5">{evidence}</p>}
        {disabled && disabledNote && <p className="text-xs text-warning mt-0.5">{disabledNote}</p>}
      </div>
    </label>
  )
}

/**
 * Build the options list for a Start/End dropdown.
 * Includes all known volume indexes, plus the current value so historical arcs
 * referencing a now-missing volume still render correctly. Sorted ascending.
 */
function buildVolumeOptions(volumes: number[], current: number): number[] {
  const set = new Set<number>(volumes)
  set.add(current)
  return [...set].sort((a, b) => a - b)
}

export function ManageSeriesModal({ seriesName, volumes, onClose, onSaved }: Props) {
  const [activeTab, setActiveTab] = useState<TabId>('series')

  // Series tab state
  const [status, setStatus] = useState<SeriesStatus>('unknown')
  const [statusLoading, setStatusLoading] = useState(true)
  const [statusSaving, setStatusSaving] = useState(false)

  // Arcs tab state
  const [arcRows, setArcRows] = useState<ArcRow[]>([])
  const [arcsLoading, setArcsLoading] = useState(true)
  const [arcsSaving, setArcsSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  // AI cleanup state
  const { available: aiAvailable } = useAiStatus()
  const showAi = aiAvailable('series_cleanup')
  const [proposal, setProposal] = useState<SeriesCleanupProposal | null>(null)
  const [selection, setSelection] = useState<CleanupSelection | null>(null)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)

  // Load both on mount
  useEffect(() => {
    setStatusLoading(true)
    api.get<SeriesMeta>(`/series/${encodeURIComponent(seriesName)}/meta`)
      .then(m => setStatus(m.status))
      .catch(() => {})
      .finally(() => setStatusLoading(false))

    setArcsLoading(true)
    api.get<Arc[]>(`/series/${encodeURIComponent(seriesName)}/arcs`)
      .then(arcs => setArcRows(arcs.map(a => ({
        id: a.id,
        name: a.name,
        start_index: a.start_index,
        end_index: a.end_index,
        description: a.description ?? '',
      }))))
      .catch(() => {})
      .finally(() => setArcsLoading(false))
  }, [seriesName])

  function addRow() {
    const sortedVols = [...volumes].sort((a, b) => a - b)
    const first = sortedVols[0] ?? 1
    const last = sortedVols[sortedVols.length - 1] ?? 1
    const maxEnd = arcRows.reduce((m, r) => Math.max(m, r.end_index), -Infinity)
    // Next start: first volume after the last arc's end, else first volume
    const nextStart = arcRows.length === 0
      ? first
      : sortedVols.find(v => v > maxEnd) ?? last
    setArcRows(prev => [...prev, {
      name: '',
      start_index: nextStart,
      end_index: nextStart,
      description: '',
    }])
  }

  function updateRow(idx: number, patch: Partial<ArcRow>) {
    setArcRows(prev => prev.map((r, i) => i === idx ? { ...r, ...patch } : r))
  }

  function deleteRow(idx: number) {
    setArcRows(prev => prev.filter((_, i) => i !== idx))
  }

  // Sorted view (by start_index) for rendering
  const sortedRows = [...arcRows].map((r, originalIdx) => ({ ...r, originalIdx }))
    .sort((a, b) => a.start_index - b.start_index)

  async function saveStatus() {
    setStatusSaving(true)
    setSaveError(null)
    try {
      await api.put(`/series/${encodeURIComponent(seriesName)}/meta`, { status })
      onSaved()
      onClose()
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : t`Save failed`)
    } finally {
      setStatusSaving(false)
    }
  }

  async function saveArcs() {
    setArcsSaving(true)
    setSaveError(null)
    try {
      await api.post(`/series/${encodeURIComponent(seriesName)}/arcs/bulk`,
        arcRows.map(r => ({
          series_name: seriesName,
          name: r.name,
          start_index: r.start_index,
          end_index: r.end_index,
          description: r.description || null,
        }))
      )
      onSaved()
      onClose()
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : t`Save failed`)
    } finally {
      setArcsSaving(false)
    }
  }

  async function runCleanup() {
    setActiveTab('ai')
    setSaveError(null)
    setAiError(null)
    setAiLoading(true)
    try {
      const p = await proposeSeriesCleanup(seriesName)
      setProposal(p)
      setSelection(initialSelection(p))
    } catch (e: unknown) {
      setAiError(e instanceof Error ? e.message : t`AI cleanup failed`)
    } finally {
      setAiLoading(false)
    }
  }

  function openCleanup() {
    if (proposal || aiLoading) {
      setActiveTab('ai')
      setSaveError(null)
      return
    }
    void runCleanup()
  }

  const cleanupBody = proposal && selection ? selectionToBody(proposal, selection) : null
  const selectedCount = cleanupBody ? countSelected(cleanupBody) : 0

  async function applyCleanup() {
    if (!cleanupBody || selectedCount === 0) return
    setApplying(true)
    setSaveError(null)
    try {
      const res = await applySeriesCleanup(seriesName, cleanupBody)
      onSaved(res.renamed ? res.series_name : undefined)
      onClose()
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : t`Save failed`)
    } finally {
      setApplying(false)
    }
  }

  function toggle(patch: (s: CleanupSelection) => CleanupSelection) {
    setSelection(prev => (prev ? patch(prev) : prev))
  }

  const isBusy = statusSaving || arcsSaving || applying

  return (
    <ModalShell open className="w-full max-w-3xl">
      <div className="bg-card rounded-2xl border border-border shadow-xl shadow-accent-soft flex flex-col max-h-[90vh] min-h-[420px]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <div>
            <h2 className="text-base font-semibold text-foreground"><Trans>Manage Series</Trans></h2>
            <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-sm">{seriesName}</p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            aria-label={t`Close`}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 px-5 pt-3 shrink-0">
          {(['series', 'arcs'] as TabId[]).map(tb => (
            <button
              key={tb}
              onClick={() => { setActiveTab(tb); setSaveError(null) }}
              className={cn(
                'px-3 py-1.5 rounded-md text-sm font-medium transition-colors capitalize',
                activeTab === tb
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              )}
            >
              {tb === 'series' ? t`Series` : t`Arcs`}
            </button>
          ))}
          {showAi && (
            <button
              onClick={openCleanup}
              title={t`Sends the titles, volume numbers and file names of the books you can see in this series to Anthropic, which proposes fixes for you to check`}
              className={cn(
                'ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors',
                activeTab === 'ai'
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              )}
            >
              {aiLoading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              <Trans>Clean up with AI</Trans>
              <AiBadge className={activeTab === 'ai' ? 'border-primary-foreground/40 text-primary-foreground' : undefined} />
            </button>
          )}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {activeTab === 'series' && (
            statusLoading ? (
              <div className="flex justify-center py-10">
                <Loader2 className="w-5 h-5 animate-spin text-primary" />
              </div>
            ) : (
              <div className="flex flex-col gap-4">
                <div className="flex flex-col gap-1.5">
                  <label className="text-sm font-medium text-foreground" htmlFor="series-status">
                    <Trans>Publication status</Trans>
                  </label>
                  <select
                    id="series-status"
                    value={status}
                    onChange={e => setStatus(e.target.value as SeriesStatus)}
                    className="w-full px-3 py-2 rounded-lg border border-border bg-background text-foreground text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  >
                    {STATUS_OPTIONS.map(o => (
                      <option key={o.value} value={o.value}>{i18n._(o.label)}</option>
                    ))}
                  </select>
                </div>
              </div>
            )
          )}

          {activeTab === 'arcs' && (
            arcsLoading ? (
              <div className="flex justify-center py-10">
                <Loader2 className="w-5 h-5 animate-spin text-primary" />
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {sortedRows.length === 0 && volumes.length > 0 && (
                  <p className="text-sm text-muted-foreground text-center py-4">
                    <Trans>No arcs defined yet. Click &ldquo;Add arc&rdquo; to create one.</Trans>
                  </p>
                )}
                {volumes.length === 0 && (
                  <p className="text-sm text-muted-foreground text-center py-4">
                    <Trans>This series has no volumes with a numeric index yet. Add
                    volumes before defining arcs.</Trans>
                  </p>
                )}
                {sortedRows.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-xs text-muted-foreground border-b border-border">
                          <th className="pb-2 font-medium pr-3 min-w-[140px]"><Trans>Name</Trans></th>
                          <th className="pb-2 font-medium pr-3 w-20"><Trans>Start</Trans></th>
                          <th className="pb-2 font-medium pr-3 w-20"><Trans>End</Trans></th>
                          <th className="pb-2 font-medium pr-3"><Trans>Description</Trans></th>
                          <th className="pb-2 w-8" />
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {sortedRows.map(({ originalIdx, ...row }) => {
                          const warn = isInvalid(row) || hasOverlap(arcRows, originalIdx)
                          return (
                            <tr
                              key={originalIdx}
                              className={cn('group', warn && 'bg-warning/10')}
                            >
                              <td className="py-1.5 pr-3">
                                <input
                                  type="text"
                                  value={row.name}
                                  onChange={e => updateRow(originalIdx, { name: e.target.value })}
                                  placeholder={t`Arc name`}
                                  className="w-full px-2 py-1 rounded border border-border bg-background text-foreground text-xs focus:outline-none focus:ring-1 focus:ring-inset focus:ring-primary"
                                />
                              </td>
                              <td className="py-1.5 pr-3">
                                <select
                                  value={row.start_index}
                                  onChange={e => updateRow(originalIdx, { start_index: Number(e.target.value) })}
                                  className="w-full px-2 py-1 rounded border border-border bg-background text-foreground text-xs focus:outline-none focus:ring-1 focus:ring-inset focus:ring-primary"
                                >
                                  {buildVolumeOptions(volumes, row.start_index).map(v => (
                                    <option key={v} value={v}>{formatVol(v)}</option>
                                  ))}
                                </select>
                              </td>
                              <td className="py-1.5 pr-3">
                                <select
                                  value={row.end_index}
                                  onChange={e => updateRow(originalIdx, { end_index: Number(e.target.value) })}
                                  className="w-full px-2 py-1 rounded border border-border bg-background text-foreground text-xs focus:outline-none focus:ring-1 focus:ring-inset focus:ring-primary"
                                >
                                  {buildVolumeOptions(volumes, row.end_index).map(v => (
                                    <option key={v} value={v}>{formatVol(v)}</option>
                                  ))}
                                </select>
                              </td>
                              <td className="py-1.5 pr-3">
                                <input
                                  type="text"
                                  value={row.description}
                                  onChange={e => updateRow(originalIdx, { description: e.target.value })}
                                  placeholder={t`Optional`}
                                  className="w-full px-2 py-1 rounded border border-border bg-background text-foreground text-xs focus:outline-none focus:ring-1 focus:ring-inset focus:ring-primary"
                                />
                              </td>
                              <td className="py-1.5">
                                <button
                                  onClick={() => deleteRow(originalIdx)}
                                  className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors opacity-0 group-hover:opacity-100"
                                  aria-label={t`Delete arc`}
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                </button>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
                <button
                  onClick={addRow}
                  disabled={volumes.length === 0}
                  className="flex items-center gap-1.5 text-xs text-primary hover:text-primary/80 transition-colors self-start disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <Trans>Add arc</Trans>
                </button>
              </div>
            )
          )}

          {activeTab === 'ai' && (
            aiLoading ? (
              <div className="flex flex-col items-center gap-2 py-10 text-sm text-muted-foreground">
                <Loader2 className="w-5 h-5 animate-spin text-primary" />
                <Trans>Asking Anthropic about this series. This can take a minute.</Trans>
              </div>
            ) : aiError ? (
              <div className="flex flex-col items-center gap-3 py-10">
                <p className="text-sm text-destructive text-center">{aiError}</p>
                <button
                  onClick={() => void runCleanup()}
                  className="flex items-center gap-1.5 text-xs text-primary hover:text-primary/80 transition-colors"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  <Trans>Try again</Trans>
                </button>
              </div>
            ) : proposal && selection ? (() => {
              const pct = Math.round(proposal.confidence * 100)
              const sure = proposal.confidence >= proposal.threshold
              const proposedArcs = proposal.arcs.proposed ?? []
              const nothing = proposal.series_name.proposed == null && proposal.books.length === 0
                && proposal.status.proposed == null && proposal.arcs.proposed == null
              const adminOnly = t`Only admins can change this.`
              const currentArcList = proposal.arcs.current.map(a => `${a.name} ${arcRange(a)}`).join(', ')
              return (
                <div className="flex flex-col gap-5">
                  <div className="rounded-lg border border-border bg-card px-3 py-2.5 text-sm space-y-1">
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          'shrink-0 inline-flex items-center rounded-full border px-1.5 py-px text-[10px] font-semibold tabular-nums',
                          sure
                            ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
                            : 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400',
                        )}
                      >
                        <Trans>{pct}% confident</Trans>
                      </span>
                      <span className="text-xs text-muted-foreground">
                        <Trans>Proposed by Anthropic. Nothing changes until you apply.</Trans>
                      </span>
                      <button
                        onClick={() => void runCleanup()}
                        className="ml-auto flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors shrink-0"
                      >
                        <RefreshCw className="w-3 h-3" />
                        <Trans>Ask again</Trans>
                      </button>
                    </div>
                    <p className="text-xs text-foreground">{proposal.evidence}</p>
                  </div>

                  {nothing && (
                    <p className="text-sm text-muted-foreground text-center py-4">
                      <Trans>No changes proposed. This series looks consistent.</Trans>
                    </p>
                  )}

                  {proposal.series_name.proposed != null && (
                    <CleanupGroup title={t`Series name`}>
                      <CleanupRow
                        checked={selection.name}
                        disabled={!proposal.permissions.series_name}
                        disabledNote={t`Renaming needs edit rights on every book in the series.`}
                        onToggle={() => toggle(s => ({ ...s, name: !s.name }))}
                        evidence={proposal.series_name.evidence}
                      >
                        <Change from={proposal.series_name.current} to={proposal.series_name.proposed} />
                      </CleanupRow>
                    </CleanupGroup>
                  )}

                  {proposal.books.length > 0 && (
                    <CleanupGroup title={t`Books`}>
                      {proposal.books.map(row => {
                        const cur = row.current
                        const next = row.proposed
                        const noNumber = t`no number`
                        const curVol = cur.series_index != null ? formatVol(cur.series_index) : noNumber
                        return (
                          <CleanupRow
                            key={row.book_id}
                            checked={!!selection.books[row.book_id]}
                            disabled={!row.editable}
                            disabledNote={t`You can only edit books you uploaded.`}
                            onToggle={() => toggle(s => ({ ...s, books: { ...s.books, [row.book_id]: !s.books[row.book_id] } }))}
                            evidence={row.evidence}
                          >
                            <div className="flex flex-col gap-0.5">
                              {next.series_index != null ? (
                                <span className="text-xs">
                                  <Trans>Volume</Trans>{' '}
                                  <Change
                                    from={curVol}
                                    to={formatVol(next.series_index)}
                                  />
                                </span>
                              ) : (
                                <span className="text-xs text-muted-foreground">
                                  <Trans>Volume {curVol}</Trans>
                                </span>
                              )}
                              {next.title != null ? (
                                <Change from={cur.title} to={next.title} />
                              ) : (
                                <span className="text-foreground">{cur.title}</span>
                              )}
                            </div>
                          </CleanupRow>
                        )
                      })}
                    </CleanupGroup>
                  )}

                  {proposal.status.proposed != null && (
                    <CleanupGroup title={t`Status`}>
                      <CleanupRow
                        checked={selection.status}
                        disabled={!proposal.permissions.status}
                        disabledNote={adminOnly}
                        onToggle={() => toggle(s => ({ ...s, status: !s.status }))}
                        evidence={proposal.status.evidence}
                      >
                        <Change from={statusLabel(proposal.status.current)} to={statusLabel(proposal.status.proposed)} />
                      </CleanupRow>
                    </CleanupGroup>
                  )}

                  {proposal.arcs.proposed != null && (
                    <CleanupGroup
                      title={t`Arcs`}
                      note={currentArcList
                        ? t`Applying replaces the current arcs (${currentArcList}) with the checked ones.`
                        : t`This series has no arcs yet. Applying adds the checked ones.`}
                    >
                      {proposedArcs.map((arc, i) => {
                        const range = arcRange(arc)
                        return (
                          <CleanupRow
                            key={`${arc.name}-${i}`}
                            checked={!!selection.arcs[i]}
                            disabled={!proposal.permissions.arcs}
                            disabledNote={adminOnly}
                            onToggle={() => toggle(s => ({ ...s, arcs: { ...s.arcs, [i]: !s.arcs[i] } }))}
                            evidence={arc.description ?? undefined}
                          >
                            <span className="font-medium">{arc.name}</span>{' '}
                            <span className="text-xs text-muted-foreground tabular-nums">
                              <Trans>Volumes {range}</Trans>
                            </span>
                          </CleanupRow>
                        )
                      })}
                      <p className="px-3 py-2 text-xs text-muted-foreground">{proposal.arcs.evidence}</p>
                    </CleanupGroup>
                  )}
                </div>
              )
            })() : null
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-border shrink-0 gap-3">
          <div className="flex-1">
            {saveError && (
              <p className="text-xs text-destructive">{saveError}</p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              disabled={isBusy}
              className="px-4 py-2 rounded-lg border border-border bg-background text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50 transition-colors"
            >
              <Trans>Cancel</Trans>
            </button>
            {activeTab === 'ai' ? (
              <button
                onClick={() => void applyCleanup()}
                disabled={isBusy || aiLoading || selectedCount === 0}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {applying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                <Trans>Apply selected ({selectedCount})</Trans>
              </button>
            ) : (
              <button
                onClick={activeTab === 'series' ? saveStatus : saveArcs}
                disabled={isBusy || (activeTab === 'arcs' && arcsLoading) || (activeTab === 'series' && statusLoading)}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {isBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                <Trans>Save</Trans>
              </button>
            )}
          </div>
        </div>
      </div>
    </ModalShell>
  )
}
