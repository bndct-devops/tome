import { useEffect, useState } from 'react'
import { X, Plus, Trash2, Loader2, Save } from 'lucide-react'
import { api } from '@/lib/api'
import type { Arc, SeriesMeta, SeriesStatus } from '@/lib/books'
import { cn } from '@/lib/utils'

interface Props {
  seriesName: string
  /** All series_index values that currently exist for this series (for Start/End dropdowns). */
  volumes: number[]
  onClose: () => void
  onSaved: () => void
}

type TabId = 'series' | 'arcs'

interface ArcRow {
  /** undefined = new row (no id yet) */
  id?: number
  name: string
  start_index: number
  end_index: number
  description: string
}

const STATUS_OPTIONS: { value: SeriesStatus; label: string }[] = [
  { value: 'ongoing', label: 'Ongoing' },
  { value: 'finished', label: 'Finished' },
  { value: 'hiatus', label: 'Hiatus' },
  { value: 'unknown', label: 'Unknown' },
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
      setSaveError(e instanceof Error ? e.message : 'Save failed')
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
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setArcsSaving(false)
    }
  }

  const isBusy = statusSaving || arcsSaving

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
      <div className="w-full max-w-3xl bg-card rounded-xl border border-border shadow-xl flex flex-col max-h-[90vh] min-h-[420px]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <div>
            <h2 className="text-base font-semibold text-foreground">Manage Series</h2>
            <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-sm">{seriesName}</p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 px-5 pt-3 shrink-0">
          {(['series', 'arcs'] as TabId[]).map(t => (
            <button
              key={t}
              onClick={() => { setActiveTab(t); setSaveError(null) }}
              className={cn(
                'px-3 py-1.5 rounded-md text-sm font-medium transition-colors capitalize',
                activeTab === t
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              )}
            >
              {t === 'series' ? 'Series' : 'Arcs'}
            </button>
          ))}
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
                    Publication status
                  </label>
                  <select
                    id="series-status"
                    value={status}
                    onChange={e => setStatus(e.target.value as SeriesStatus)}
                    className="w-full px-3 py-2 rounded-lg border border-border bg-background text-foreground text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  >
                    {STATUS_OPTIONS.map(o => (
                      <option key={o.value} value={o.value}>{o.label}</option>
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
                    No arcs defined yet. Click &ldquo;Add arc&rdquo; to create one.
                  </p>
                )}
                {volumes.length === 0 && (
                  <p className="text-sm text-muted-foreground text-center py-4">
                    This series has no volumes with a numeric index yet. Add
                    volumes before defining arcs.
                  </p>
                )}
                {sortedRows.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-xs text-muted-foreground border-b border-border">
                          <th className="pb-2 font-medium pr-3 min-w-[140px]">Name</th>
                          <th className="pb-2 font-medium pr-3 w-20">Start</th>
                          <th className="pb-2 font-medium pr-3 w-20">End</th>
                          <th className="pb-2 font-medium pr-3">Description</th>
                          <th className="pb-2 w-8" />
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {sortedRows.map(({ originalIdx, ...row }) => {
                          const warn = isInvalid(row) || hasOverlap(arcRows, originalIdx)
                          return (
                            <tr
                              key={originalIdx}
                              className={cn('group', warn && 'bg-amber-50 dark:bg-amber-900/20')}
                            >
                              <td className="py-1.5 pr-3">
                                <input
                                  type="text"
                                  value={row.name}
                                  onChange={e => updateRow(originalIdx, { name: e.target.value })}
                                  placeholder="Arc name"
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
                                  placeholder="Optional"
                                  className="w-full px-2 py-1 rounded border border-border bg-background text-foreground text-xs focus:outline-none focus:ring-1 focus:ring-inset focus:ring-primary"
                                />
                              </td>
                              <td className="py-1.5">
                                <button
                                  onClick={() => deleteRow(originalIdx)}
                                  className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors opacity-0 group-hover:opacity-100"
                                  aria-label="Delete arc"
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
                  Add arc
                </button>
              </div>
            )
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
              Cancel
            </button>
            <button
              onClick={activeTab === 'series' ? saveStatus : saveArcs}
              disabled={isBusy || (activeTab === 'arcs' && arcsLoading) || (activeTab === 'series' && statusLoading)}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {isBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
