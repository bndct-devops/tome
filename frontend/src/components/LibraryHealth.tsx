import { useState } from 'react'
import { useShiftSelect } from '@/lib/useShiftSelect'
import { FolderOpen, ArrowRight, Loader2, Check, AlertCircle, ChevronDown, ChevronRight, Trash } from 'lucide-react'
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

interface HealthData {
  total_files: number
  misplaced_count: number
  issues: HealthIssue[]
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
    groups.push({ label: `Series: ${name}`, issues: items, folderCount: dirs.size, collapsed: true })
  }

  for (const [name, items] of [...authorMap.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const dirs = new Set(items.map(i => i.current_path.split('/')[0]))
    groups.push({ label: `Author: ${name}`, issues: items, folderCount: dirs.size, collapsed: true })
  }

  if (ungrouped.length > 0) {
    groups.push({ label: 'Other', issues: ungrouped, folderCount: 1, collapsed: true })
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
      setError(e instanceof Error ? e.message : 'Purge failed')
    } finally {
      setPurging(false)
    }
  }

  async function scan() {
    setLoading(true)
    setError(null)
    setDryRunResult(null)
    setResult(null)
    setSelected(new Set())
    try {
      const data = await api.get<HealthData>('/books/library-health')
      setHealthData(data)
      setGroups(groupIssues(data.issues))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed')
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
      setError(e instanceof Error ? e.message : 'Reorganize failed')
    } finally {
      setReorganizing(false)
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
            <h2 className="text-sm font-semibold">Library Health</h2>
            {healthData && (
              <p className="text-xs text-muted-foreground mt-0.5">
                {healthData.misplaced_count === 0
                  ? `All ${healthData.total_files} files are correctly placed.`
                  : `${healthData.misplaced_count} of ${healthData.total_files} files need reorganization.`}
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
                  Dry Run All
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
                    ? `Reorganize Selected (${selected.size})`
                    : 'Reorganize All'
                  }
                </button>
              </>
            )}
            <button
              onClick={purgeEmpty}
              disabled={purging}
              className="px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent transition-colors disabled:opacity-50 flex items-center gap-1.5"
              title="Remove folders that contain only hidden files (.DS_Store, etc.)"
            >
              {purging ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash className="w-3.5 h-3.5" />}
              {purging ? 'Purging...' : 'Purge Empty Folders'}
            </button>
            <button
              onClick={scan}
              disabled={loading}
              className="px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-accent transition-colors disabled:opacity-50 flex items-center gap-1.5"
            >
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FolderOpen className="w-3.5 h-3.5" />}
              {loading ? 'Scanning...' : 'Scan Library'}
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
        <div className="rounded-xl border border-green-500/20 bg-green-500/5 p-4 text-xs space-y-1">
          <p className="font-medium text-green-600 dark:text-green-400 flex items-center gap-1.5">
            <Check className="w-3.5 h-3.5" />
            Reorganization complete
          </p>
          <p className="text-muted-foreground">
            Moved {result.moved.length} files
            {result.folders_removed.length > 0 && `, removed ${result.folders_removed.length} empty folders`}
            {result.errors.length > 0 && `, ${result.errors.length} errors`}
          </p>
          {result.errors.length > 0 && (
            <ul className="mt-1 space-y-0.5 text-destructive">
              {result.errors.map((e, i) => <li key={i}>File {e.file_id}: {e.error}</li>)}
            </ul>
          )}
        </div>
      )}

      {purgeResult !== null && (
        <div className="rounded-xl border border-green-500/20 bg-green-500/5 p-4 text-xs space-y-1">
          <p className="font-medium text-green-600 dark:text-green-400 flex items-center gap-1.5">
            <Check className="w-3.5 h-3.5" />
            {purgeResult.length === 0 ? 'No empty folders found.' : `Removed ${purgeResult.length} empty folder${purgeResult.length !== 1 ? 's' : ''}.`}
          </p>
          {purgeResult.length > 0 && (
            <ul className="mt-1 space-y-0.5 font-mono text-muted-foreground">
              {purgeResult.map((f, i) => <li key={i}>{f}</li>)}
            </ul>
          )}
        </div>
      )}

      {dryRunResult && (
        <div className="rounded-xl border border-border bg-card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold">
              Dry Run Preview — {dryRunResult.moved.length} files would be moved
            </p>
            <button
              onClick={() => setDryRunResult(null)}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              Dismiss
            </button>
          </div>
          <ul className="space-y-2 max-h-60 overflow-y-auto text-xs font-mono">
            {dryRunResult.moved.map((m, i) => (
              <li key={i} className="space-y-0.5">
                <p className="text-muted-foreground line-through">{m.from}</p>
                <p className="text-green-600 dark:text-green-400 flex items-center gap-1">
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
              Cancel
            </button>
            <button
              onClick={() => runReorganize(dryRunResult.moved.map(m => m.file_id), false)}
              disabled={reorganizing}
              className="px-3 py-1.5 text-xs rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              Confirm & Reorganize
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
                      {group.issues.length} file{group.issues.length !== 1 ? 's' : ''}
                      {group.folderCount > 1 && ` in ${group.folderCount} folders`}
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
                          <p className="text-green-600 dark:text-green-400 font-mono truncate flex items-center gap-1">
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

      {healthData && healthData.misplaced_count === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground gap-2">
          <Check className="w-8 h-8 text-green-500" />
          <p className="text-sm">All files are correctly placed.</p>
        </div>
      )}
    </div>
  )
}
