import { useState } from 'react'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { useShiftSelect } from '@/lib/useShiftSelect'
import { FolderOpen, ArrowRight, Loader2, Check, AlertCircle, ChevronDown, ChevronRight, Trash, FileX } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

interface HealthIssue {
  book_id: number
  file_id: number
  title: string
  author: string
  series: string
  series_index: number | null
  format: string
  current_path: string
  expected_path: string
}

interface MissingEntry {
  book_id: number
  file_id: number
  title: string
  author: string
  series: string
  format: string
  path: string
  book_file_count: number
}

interface HealthData {
  total_files: number
  misplaced_count: number
  issues: HealthIssue[]
  missing_count: number
  missing: MissingEntry[]
}

interface RemoveMissingResult {
  removed_file_rows: number
  removed_books: { book_id: number; title: string }[]
  skipped: { file_id: number; error: string }[]
}

interface ReorganizeResult {
  moved: { file_id: number; from: string; to: string }[]
  errors: { file_id: number; error: string }[]
  folders_removed: string[]
}

interface Group {
  label: string
  issues: HealthIssue[]
  folderCount: number
  collapsed: boolean
}

function groupIssues(issues: HealthIssue[]): Group[] {
  const seriesMap = new Map<string, HealthIssue[]>()
  const authorMap = new Map<string, HealthIssue[]>()
  const ungrouped: HealthIssue[] = []

  for (const issue of issues) {
    if (issue.series) {
      const key = issue.series
      if (!seriesMap.has(key)) seriesMap.set(key, [])
      seriesMap.get(key)!.push(issue)
    } else if (issue.author) {
      const key = issue.author
      if (!authorMap.has(key)) authorMap.set(key, [])
      authorMap.get(key)!.push(issue)
    } else {
      ungrouped.push(issue)
    }
  }

  const groups: Group[] = []

  for (const [name, items] of [...seriesMap.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const dirs = new Set(items.map(i => i.current_path.split('/')[0]))
    groups.push({ label: t`Series: ${name}`, issues: items, folderCount: dirs.size, collapsed: true })
  }

  for (const [name, items] of [...authorMap.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const dirs = new Set(items.map(i => i.current_path.split('/')[0]))
    groups.push({ label: t`Author: ${name}`, issues: items, folderCount: dirs.size, collapsed: true })
  }

  if (ungrouped.length > 0) {
    groups.push({ label: t`Other`, issues: ungrouped, folderCount: 1, collapsed: true })
  }

  return groups
}

export function LibraryHealthTab() {
  const [healthData, setHealthData] = useState<HealthData | null>(null)
  const [loading, setLoading] = useState(false)
  const [groups, setGroups] = useState<Group[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [reorganizing, setReorganizing] = useState(false)
  const [purging, setPurging] = useState(false)
  const [purgeResult, setPurgeResult] = useState<string[] | null>(null)
  const [removingMissing, setRemovingMissing] = useState(false)
  const [confirmRemoveMissing, setConfirmRemoveMissing] = useState(false)
  const [missingResult, setMissingResult] = useState<RemoveMissingResult | null>(null)
  const [dryRunResult, setDryRunResult] = useState<ReorganizeResult | null>(null)
  const [result, setResult] = useState<ReorganizeResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function purgeEmpty() {
    setPurging(true)
    setError(null)
    setPurgeResult(null)
    try {
      const res = await api.post<{ removed: string[] }>('/books/purge-empty-dirs', {})
      setPurgeResult(res.removed)
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Purge failed`)
    } finally {
      setPurging(false)
    }
  }

  async function scan() {
    setLoading(true)
    setError(null)
    setDryRunResult(null)
    setResult(null)
    setMissingResult(null)
    setConfirmRemoveMissing(false)
    setSelected(new Set())
    try {
      const data = await api.get<HealthData>('/books/library-health')
      setHealthData(data)
      setGroups(groupIssues(data.issues))
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Scan failed`)
    } finally {
      setLoading(false)
    }
  }

  async function runReorganize(fileIds: number[], dryRun: boolean) {
    setReorganizing(true)
    setError(null)
    try {
      const res = await api.post<ReorganizeResult>('/books/reorganize', {
        file_ids: fileIds,
        dry_run: dryRun,
      })
      if (dryRun) {
        setDryRunResult(res)
      } else {
        setResult(res)
        // Re-scan to refresh the list
        const data = await api.get<HealthData>('/books/library-health')
        setHealthData(data)
        setGroups(groupIssues(data.issues))
        setSelected(new Set())
        setDryRunResult(null)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Reorganize failed`)
    } finally {
      setReorganizing(false)
    }
  }

  async function removeMissing() {
    if (!healthData?.missing.length) return
    setRemovingMissing(true)
    setError(null)
    try {
      const res = await api.post<RemoveMissingResult>('/books/remove-missing', {
        file_ids: healthData.missing.map(m => m.file_id),
      })
      setMissingResult(res)
      setConfirmRemoveMissing(false)
      // Re-scan to refresh both lists
      const data = await api.get<HealthData>('/books/library-health')
      setHealthData(data)
      setGroups(groupIssues(data.issues))
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Removing dead entries failed`)
    } finally {
      setRemovingMissing(false)
    }
  }

  function toggleGroup(groupIdx: number, collapsed: boolean) {
    setGroups(prev => prev.map((g, i) => i === groupIdx ? { ...g, collapsed } : g))
  }

  function toggleFile(fileId: number, shiftKey: boolean) {
    setSelected(prev => {
      const index = allFileIds.indexOf(fileId)
      return handleToggle(fileId, index, shiftKey, prev)
    })
  }

  function toggleGroupSelect(issues: HealthIssue[]) {
    const ids = issues.map(i => i.file_id)
    const allSelected = ids.every(id => selected.has(id))
    setSelected(prev => {
      const next = new Set(prev)
      if (allSelected) ids.forEach(id => next.delete(id))
      else ids.forEach(id => next.add(id))
      return next
    })
  }

  const allFileIds = healthData?.issues.map(i => i.file_id) ?? []
  const { handleToggle } = useShiftSelect(allFileIds)

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-sm font-semibold"><Trans>Library Health</Trans></h2>
            {healthData && (
              <p className="text-xs text-muted-foreground mt-0.5">
                {(() => {
                  const total = healthData.total_files
                  if (healthData.misplaced_count === 0 && healthData.missing_count === 0) return t`All ${total} files are correctly placed.`
                  const misplaced = healthData.misplaced_count
                  return [
                    misplaced > 0 ? t`${misplaced} of ${total} files need reorganization` : null,
                    healthData.missing_count > 0
                      ? plural(healthData.missing_count, { one: '# entry is missing from disk', other: '# entries are missing from disk' })
                      : null,
                  ].filter(Boolean).join(' · ') + '.'
                })()}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {healthData && healthData.misplaced_count > 0 && (
              <>
                <button
                  onClick={() => runReorganize(allFileIds, true)}
                  disabled={reorganizing}
                  className="px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent transition-colors disabled:opacity-50"
                >
                  <Trans>Dry Run All</Trans>
                </button>
                <button
                  onClick={() => selected.size > 0
                    ? runReorganize([...selected], false)
                    : runReorganize(allFileIds, false)
                  }
                  disabled={reorganizing}
                  className="px-3 py-1.5 text-xs rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50"
                >
                  {reorganizing
                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : selected.size > 0
                    ? (() => { const n = selected.size; return t`Reorganize Selected (${n})` })()
                    : t`Reorganize All`
                  }
                </button>
              </>
            )}
            <button
              onClick={purgeEmpty}
              disabled={purging}
              className="px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent transition-colors disabled:opacity-50 flex items-center gap-1.5"
              title={t`Remove folders that contain only hidden files (.DS_Store, etc.)`}
            >
              {purging ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash className="w-3.5 h-3.5" />}
              {purging ? t`Purging...` : t`Purge Empty Folders`}
            </button>
            <button
              onClick={scan}
              disabled={loading}
              className="px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent transition-colors disabled:opacity-50 flex items-center gap-1.5"
            >
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FolderOpen className="w-3.5 h-3.5" />}
              {loading ? t`Scanning...` : t`Scan Library`}
            </button>
          </div>
        </div>

        {error && (
          <div className="mt-3 flex items-center gap-2 text-xs text-destructive">
            <AlertCircle className="w-3.5 h-3.5 shrink-0" />
            {error}
          </div>
        )}
      </div>

      {result && (
        <div className="rounded-xl border border-success/20 bg-success/5 p-4 text-xs space-y-1">
          <p className="font-medium text-success flex items-center gap-1.5">
            <Check className="w-3.5 h-3.5" />
            <Trans>Reorganization complete</Trans>
          </p>
          <p className="text-muted-foreground">
            {(() => { const n = result.moved.length; return <Trans>Moved {n} files</Trans> })()}
            {result.folders_removed.length > 0 && (() => { const n = result.folders_removed.length; return t`, removed ${n} empty folders` })()}
            {result.errors.length > 0 && (() => { const n = result.errors.length; return t`, ${n} errors` })()}
          </p>
          {result.errors.length > 0 && (
            <ul className="mt-1 space-y-0.5 text-destructive">
              {result.errors.map((e, i) => { const id = e.file_id; const err = e.error; return <li key={i}><Trans>File {id}: {err}</Trans></li> })}
            </ul>
          )}
        </div>
      )}

      {purgeResult !== null && (
        <div className="rounded-xl border border-success/20 bg-success/5 p-4 text-xs space-y-1">
          <p className="font-medium text-success flex items-center gap-1.5">
            <Check className="w-3.5 h-3.5" />
            {purgeResult.length === 0 ? t`No empty folders found.` : plural(purgeResult.length, { one: 'Removed # empty folder.', other: 'Removed # empty folders.' })}
          </p>
          {purgeResult.length > 0 && (
            <ul className="mt-1 space-y-0.5 font-mono text-muted-foreground">
              {purgeResult.map((f, i) => <li key={i}>{f}</li>)}
            </ul>
          )}
        </div>
      )}

      {missingResult && (
        <div className="rounded-xl border border-success/20 bg-success/5 p-4 text-xs space-y-1">
          <p className="font-medium text-success flex items-center gap-1.5">
            <Check className="w-3.5 h-3.5" />
            <Trans>Dead entries removed</Trans>
          </p>
          <p className="text-muted-foreground">
            {[
              missingResult.removed_books.length > 0
                ? plural(missingResult.removed_books.length, { one: '# book entry removed', other: '# book entries removed' })
                : null,
              missingResult.removed_file_rows > 0
                ? plural(missingResult.removed_file_rows, { one: '# file entry removed', other: '# file entries removed' })
                : null,
              missingResult.skipped.length > 0
                ? (() => { const n = missingResult.skipped.length; return t`${n} skipped (file exists on disk)` })()
                : null,
            ].filter(Boolean).join(' · ') || t`Nothing to remove.`}
          </p>
          {missingResult.removed_books.length > 0 && (
            <ul className="mt-1 space-y-0.5 text-muted-foreground">
              {missingResult.removed_books.map(b => <li key={b.book_id}>{b.title}</li>)}
            </ul>
          )}
        </div>
      )}

      {healthData && healthData.missing.length > 0 && (
        <div className="rounded-xl border border-destructive/20 bg-card overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-border bg-destructive/5">
            <div className="flex items-center gap-2 min-w-0">
              <FileX className="w-4 h-4 text-destructive shrink-0" />
              <span className="text-xs font-medium whitespace-nowrap"><Trans>Orphaned entries</Trans></span>
              <span className="text-xs text-muted-foreground truncate">
                {plural(healthData.missing.length, { one: '# file in the database but missing from disk', other: '# files in the database but missing from disk' })}
              </span>
            </div>
            <div className="flex items-center gap-2 shrink-0 ml-auto">
              {confirmRemoveMissing && (
                <button
                  onClick={() => setConfirmRemoveMissing(false)}
                  disabled={removingMissing}
                  className="px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent transition-colors disabled:opacity-50"
                >
                  <Trans>Cancel</Trans>
                </button>
              )}
              <button
                onClick={() => confirmRemoveMissing ? removeMissing() : setConfirmRemoveMissing(true)}
                disabled={removingMissing}
                className="px-3 py-1.5 text-xs rounded-lg bg-destructive text-destructive-foreground hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center gap-1.5"
                title={t`Removes the database entries only — no files are touched`}
              >
                {removingMissing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash className="w-3.5 h-3.5" />}
                {removingMissing
                  ? t`Removing...`
                  : confirmRemoveMissing
                  ? plural(healthData.missing.length, { one: 'Confirm — remove # entry', other: 'Confirm — remove # entries' })
                  : t`Remove Dead Entries`}
              </button>
            </div>
          </div>
          <ul className="divide-y divide-border">
            {healthData.missing.map(m => {
              const missingForBook = healthData.missing.filter(x => x.book_id === m.book_id).length
              const wholeBook = missingForBook >= m.book_file_count
              return (
                <li key={m.file_id} className="flex items-start gap-3 px-4 py-3 text-xs">
                  <div className="min-w-0 space-y-1 flex-1">
                    <p className="font-medium truncate">
                      {m.title}
                      {m.author && <span className="text-muted-foreground font-normal"> — {m.author}</span>}
                    </p>
                    <p className="text-muted-foreground font-mono truncate line-through">{m.path}</p>
                  </div>
                  <span className="shrink-0 text-muted-foreground uppercase tracking-wide">{m.format}</span>
                  <span className={cn(
                    'shrink-0 text-[10px] px-1.5 py-0.5 rounded border',
                    wholeBook
                      ? 'border-destructive/30 text-destructive bg-destructive/10'
                      : 'border-border text-muted-foreground bg-muted',
                  )}>
                    {wholeBook ? t`book entry will be removed` : t`file entry only`}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {dryRunResult && (
        <div className="rounded-xl border border-border bg-card p-4 space-y-3">
          <div className="flex items-center justify-between">
            {(() => { const n = dryRunResult.moved.length; return (
            <p className="text-xs font-semibold">
              <Trans>Dry Run Preview — {n} files would be moved</Trans>
            </p>
            ) })()}
            <button
              onClick={() => setDryRunResult(null)}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              <Trans>Dismiss</Trans>
            </button>
          </div>
          <ul className="space-y-2 max-h-60 overflow-y-auto text-xs font-mono">
            {dryRunResult.moved.map((m, i) => (
              <li key={i} className="space-y-0.5">
                <p className="text-muted-foreground line-through">{m.from}</p>
                <p className="text-success flex items-center gap-1">
                  <ArrowRight className="w-3 h-3 shrink-0" />
                  {m.to}
                </p>
              </li>
            ))}
          </ul>
          <div className="flex justify-end gap-2 pt-1 border-t border-border">
            <button
              onClick={() => setDryRunResult(null)}
              className="px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent transition-colors"
            >
              <Trans>Cancel</Trans>
            </button>
            <button
              onClick={() => runReorganize(dryRunResult.moved.map(m => m.file_id), false)}
              disabled={reorganizing}
              className="px-3 py-1.5 text-xs rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              <Trans>Confirm & Reorganize</Trans>
            </button>
          </div>
        </div>
      )}

      {groups.length > 0 && (
        <div className="space-y-2">
          {groups.map((group, gi) => {
            const groupIds = group.issues.map(i => i.file_id)
            const allGroupSelected = groupIds.every(id => selected.has(id))
            const someGroupSelected = groupIds.some(id => selected.has(id))

            return (
              <div key={gi} className="rounded-xl border border-border bg-card overflow-hidden">
                <button
                  onClick={() => toggleGroup(gi, !group.collapsed)}
                  className="w-full flex items-center justify-between px-4 py-3 hover:bg-accent/50 transition-colors text-left"
                >
                  <div className="flex items-center gap-2">
                    <div
                      onClick={e => { e.stopPropagation(); toggleGroupSelect(group.issues) }}
                      className={cn(
                        'w-4 h-4 rounded border flex items-center justify-center transition-colors cursor-pointer shrink-0',
                        allGroupSelected
                          ? 'bg-primary border-primary'
                          : someGroupSelected
                          ? 'bg-primary/50 border-primary/50'
                          : 'border-border'
                      )}
                    >
                      {allGroupSelected && <Check className="w-3 h-3 text-primary-foreground" />}
                    </div>
                    <span className="text-xs font-medium">{group.label}</span>
                    <span className="text-xs text-muted-foreground">
                      {plural(group.issues.length, { one: '# file', other: '# files' })}
                      {group.folderCount > 1 && (() => { const n = group.folderCount; return t` in ${n} folders` })()}
                    </span>
                  </div>
                  {group.collapsed
                    ? <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
                    : <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />}
                </button>

                {!group.collapsed && (
                  <ul className="border-t border-border divide-y divide-border">
                    {group.issues.map(issue => (
                      <li key={issue.file_id} className="flex items-start gap-3 px-4 py-3">
                        <div
                          onClick={e => toggleFile(issue.file_id, e.shiftKey)}
                          className={cn(
                            'mt-0.5 w-4 h-4 rounded border flex items-center justify-center transition-colors cursor-pointer shrink-0',
                            selected.has(issue.file_id) ? 'bg-primary border-primary' : 'border-border'
                          )}
                        >
                          {selected.has(issue.file_id) && <Check className="w-3 h-3 text-primary-foreground" />}
                        </div>
                        <div className="min-w-0 space-y-1 text-xs">
                          <p className="font-medium truncate">{issue.title}</p>
                          <p className="text-muted-foreground line-through font-mono truncate">{issue.current_path}</p>
                          <p className="text-success font-mono truncate flex items-center gap-1">
                            <ArrowRight className="w-3 h-3 shrink-0" />
                            {issue.expected_path}
                          </p>
                        </div>
                        <span className="ml-auto shrink-0 text-xs text-muted-foreground uppercase tracking-wide">{issue.format}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )
          })}
        </div>
      )}

      {healthData && healthData.misplaced_count === 0 && healthData.missing_count === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground gap-2">
          <Check className="w-8 h-8 text-success" />
          <p className="text-sm"><Trans>All files are correctly placed.</Trans></p>
        </div>
      )}
    </div>
  )
}
