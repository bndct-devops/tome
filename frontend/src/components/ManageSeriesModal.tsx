import { useEffect, useState } from 'react'
import { Trans } from '@lingui/react/macro'
import { t, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'
import { Plus, Trash2, Loader2, Save } from 'lucide-react'
import { api } from '@/lib/api'
import { ModalShell } from '@/components/ModalShell'
import type { Arc, SeriesMeta, SeriesStatus } from '@/lib/books'
import { cn } from '@/lib/utils'
import { Pill, PillGroup, Input, Select, FormLabel } from '@/components/page'

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

  const isBusy = statusSaving || arcsSaving

  const busyDisabled = isBusy || (activeTab === 'arcs' && arcsLoading) || (activeTab === 'series' && statusLoading)

  return (
    <ModalShell
      open
      onClose={isBusy ? undefined : onClose}
      closeOnBackdrop={!isBusy}
      size="lg"
      eyebrow={seriesName}
      title={<Trans>Manage series</Trans>}
      footer={
        <>
          {saveError && <p className="mr-auto text-[13px] text-destructive">{saveError}</p>}
          <Pill variant="quiet" onClick={onClose} disabled={isBusy}>
            <Trans>Cancel</Trans>
          </Pill>
          <Pill variant="primary" onClick={activeTab === 'series' ? saveStatus : saveArcs} disabled={busyDisabled}>
            {isBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            <Trans>Save</Trans>
          </Pill>
        </>
      }
    >
      <div className="min-h-[260px]">
        <PillGroup
          size="sm"
          aria-label={t`Section`}
          value={activeTab}
          onChange={tb => { setActiveTab(tb); setSaveError(null) }}
          options={[
            { value: 'series', label: t`Series` },
            { value: 'arcs', label: t`Arcs` },
          ]}
        />

        {activeTab === 'series' && (
          statusLoading ? (
            <div className="flex justify-center py-10">
              <Loader2 className="w-5 h-5 animate-spin text-primary" />
            </div>
          ) : (
            <div className="mt-5">
              <FormLabel><Trans>Publication status</Trans></FormLabel>
              <PillGroup
                aria-label={t`Publication status`}
                value={status}
                onChange={setStatus}
                options={STATUS_OPTIONS.map(o => ({ value: o.value, label: i18n._(o.label) }))}
              />
            </div>
          )
        )}

        {activeTab === 'arcs' && (
          arcsLoading ? (
            <div className="flex justify-center py-10">
              <Loader2 className="w-5 h-5 animate-spin text-primary" />
            </div>
          ) : (
            <div className="mt-4 flex flex-col">
              {sortedRows.length === 0 && volumes.length > 0 && (
                <p className="text-[14px] text-muted-foreground py-4">
                  <Trans>No arcs defined yet. Click &ldquo;Add arc&rdquo; to create one.</Trans>
                </p>
              )}
              {volumes.length === 0 && (
                <p className="text-[14px] text-muted-foreground py-4">
                  <Trans>This series has no volumes with a numeric index yet. Add
                  volumes before defining arcs.</Trans>
                </p>
              )}
              {sortedRows.map(({ originalIdx, ...row }) => {
                const warn = isInvalid(row) || hasOverlap(arcRows, originalIdx)
                return (
                  <div
                    key={originalIdx}
                    className={cn('py-3 border-b border-border/70 flex flex-col gap-2', warn && 'bg-warning/10 -mx-3 px-3 rounded-lg')}
                  >
                    <div className="flex flex-wrap items-end gap-2">
                      <div className="min-w-[160px] flex-1">
                        <FormLabel className="mb-1"><Trans>Name</Trans></FormLabel>
                        <Input
                          value={row.name}
                          onChange={e => updateRow(originalIdx, { name: e.target.value })}
                          placeholder={t`Arc name`}
                        />
                      </div>
                      <div className="w-24">
                        <FormLabel className="mb-1"><Trans>Start</Trans></FormLabel>
                        <Select
                          value={row.start_index}
                          onChange={e => updateRow(originalIdx, { start_index: Number(e.target.value) })}
                          aria-label={t`Start`}
                        >
                          {buildVolumeOptions(volumes, row.start_index).map(v => (
                            <option key={v} value={v}>{formatVol(v)}</option>
                          ))}
                        </Select>
                      </div>
                      <div className="w-24">
                        <FormLabel className="mb-1"><Trans>End</Trans></FormLabel>
                        <Select
                          value={row.end_index}
                          onChange={e => updateRow(originalIdx, { end_index: Number(e.target.value) })}
                          aria-label={t`End`}
                        >
                          {buildVolumeOptions(volumes, row.end_index).map(v => (
                            <option key={v} value={v}>{formatVol(v)}</option>
                          ))}
                        </Select>
                      </div>
                      <Pill
                        variant="destructive"
                        icon
                        onClick={() => deleteRow(originalIdx)}
                        aria-label={t`Delete arc`}
                        title={t`Delete arc`}
                      >
                        <Trash2 className="w-4 h-4" />
                      </Pill>
                    </div>
                    <Input
                      value={row.description}
                      onChange={e => updateRow(originalIdx, { description: e.target.value })}
                      placeholder={t`Description (optional)`}
                      aria-label={t`Description`}
                    />
                    {warn && (
                      <p className="text-[13px] text-warning">
                        {isInvalid(row) ? <Trans>Start is after end.</Trans> : <Trans>Overlaps another arc.</Trans>}
                      </p>
                    )}
                  </div>
                )
              })}
              <Pill
                variant="quiet"
                onClick={addRow}
                disabled={volumes.length === 0}
                className="self-start mt-3 -ml-3"
              >
                <Plus className="w-4 h-4" />
                <Trans>Add arc</Trans>
              </Pill>
            </div>
          )
        )}
      </div>
    </ModalShell>
  )
}
