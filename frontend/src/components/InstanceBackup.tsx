// Admin → Server → Instance backup: download a consistent snapshot (DB +
// covers + manifest; library files stay on disk), and stage a restore that
// applies at the next server start — never under a live database.
import { useEffect, useRef, useState } from 'react'
import { Archive, Download, Loader2, RotateCcw, X } from 'lucide-react'
import { api } from '@/lib/api'

interface RestoreStatus {
  staged: boolean
  summary?: { version: string; created_at: string; users: number; books: number } | null
}

export function InstanceBackup() {
  const [downloading, setDownloading] = useState(false)
  const [status, setStatus] = useState<RestoreStatus | null>(null)
  const [confirmText, setConfirmText] = useState('')
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  const [staging, setStaging] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const loadStatus = () => {
    api.get<RestoreStatus>('/admin/backup/restore').then(setStatus).catch(() => {})
  }
  useEffect(loadStatus, [])

  async function download() {
    setDownloading(true)
    setError(null)
    try {
      const token = localStorage.getItem('tome_token')
      const resp = await fetch('/api/admin/backup/download', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!resp.ok) throw new Error('Backup failed')
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = resp.headers.get('Content-Disposition')?.match(/filename="?([^";]+)/)?.[1]
        ?? 'tome-backup.tar.gz'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Backup failed')
    } finally {
      setDownloading(false)
    }
  }

  async function stage() {
    if (!pendingFile || confirmText !== 'RESTORE' || staging) return
    setStaging(true)
    setError(null)
    const form = new FormData()
    form.append('file', pendingFile)
    form.append('confirm', confirmText)
    try {
      await api.upload('/admin/backup/restore', form)
      setPendingFile(null)
      setConfirmText('')
      loadStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That file was rejected')
    } finally {
      setStaging(false)
    }
  }

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="mb-2 flex items-center gap-2">
        <Archive className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-sm font-semibold text-foreground">Instance backup</h3>
      </div>
      <p className="mb-4 text-xs text-muted-foreground">
        Everything Tome knows in one archive: the database (all users, statuses,
        sessions, highlights, settings) plus the cover cache. Book files are not
        included — they live on disk and belong to your own backups. Restoring
        stages the archive and applies it at the next server restart; the
        current database is kept alongside as a safety copy.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={download}
          disabled={downloading}
          className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium transition-all hover:bg-muted disabled:opacity-50"
        >
          {downloading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          {downloading ? 'Preparing…' : 'Download backup'}
        </button>

        {!status?.staged && (
          <>
            <input
              ref={fileRef}
              type="file"
              accept=".gz,.tar.gz,application/gzip"
              className="hidden"
              onChange={e => { setPendingFile(e.target.files?.[0] ?? null); e.target.value = '' }}
            />
            <button
              onClick={() => fileRef.current?.click()}
              className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium transition-all hover:bg-muted"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              {pendingFile ? pendingFile.name : 'Restore from backup…'}
            </button>
          </>
        )}
      </div>

      {pendingFile && !status?.staged && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2.5">
          <p className="w-full text-xs text-muted-foreground">
            This replaces <span className="font-semibold text-foreground">the entire database</span> at
            the next restart. Type <span className="font-mono font-semibold text-destructive">RESTORE</span> to arm it.
          </p>
          <input
            value={confirmText}
            onChange={e => setConfirmText(e.target.value)}
            placeholder="RESTORE"
            className="w-32 rounded-md border border-border bg-background px-2.5 py-1.5 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <button
            onClick={stage}
            disabled={confirmText !== 'RESTORE' || staging}
            className="flex items-center gap-1.5 rounded-lg bg-destructive px-3 py-1.5 text-xs font-semibold text-white transition-all hover:bg-destructive/90 disabled:opacity-50"
          >
            {staging ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
            Stage restore
          </button>
          <button
            onClick={() => { setPendingFile(null); setConfirmText('') }}
            className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            Cancel
          </button>
        </div>
      )}

      {status?.staged && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-warning/50 bg-warning/5 px-3 py-2.5">
          <p className="min-w-0 flex-1 text-xs text-foreground">
            Restore staged
            {status.summary
              ? ` — backup from ${status.summary.created_at} (v${status.summary.version}, ${status.summary.users} users, ${status.summary.books} books).`
              : '.'}{' '}
            <span className="text-muted-foreground">It applies at the next server restart.</span>
          </p>
          <button
            onClick={() => api.delete('/admin/backup/restore').then(loadStatus)}
            className="flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <X className="h-3 w-3" /> Cancel restore
          </button>
        </div>
      )}

      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  )
}
