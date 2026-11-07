// Admin → Server → Instance backup: download a consistent snapshot (DB +
// covers + manifest; library files stay on disk), and stage a restore that
// applies at the next server start — never under a live database.
import { useEffect, useRef, useState } from 'react'
import { Download, Loader2, RotateCcw, X } from 'lucide-react'
import { api } from '@/lib/api'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { Pill } from '@/components/page'
import { PanelSection } from '@/components/SectionNav'


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
      // eslint-disable-next-line lingui/no-unlocalized-strings -- filename
      a.download = resp.headers.get('Content-Disposition')?.match(/filename="?([^";]+)/)?.[1]
        ?? 'tome-backup.tar.gz'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Backup failed`)
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
      setError(e instanceof Error ? e.message : t`That file was rejected`)
    } finally {
      setStaging(false)
    }
  }

  return (
    <PanelSection
      title={<Trans>Instance backup</Trans>}
      description={<Trans>Everything Tome knows in one archive: the database (all users, statuses,
        sessions, highlights, settings) plus the cover cache. Book files are not
        included — they live on disk and belong to your own backups. Restoring
        stages the archive and applies it at the next server restart; the
        current database is kept alongside as a safety copy.</Trans>}
    >

      <div className="flex flex-wrap items-center gap-2">
        <Pill
          onClick={download}
          disabled={downloading}
          variant="secondary"
        >
          {downloading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          {downloading ? t`Preparing…` : t`Download backup`}
        </Pill>

        {!status?.staged && (
          <>
            <input
              ref={fileRef}
              type="file"
              accept=".gz,.tar.gz,application/gzip"
              className="hidden"
              onChange={e => { setPendingFile(e.target.files?.[0] ?? null); e.target.value = '' }}
            />
            <Pill
              onClick={() => fileRef.current?.click()}
              variant="secondary"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              {pendingFile ? pendingFile.name : t`Restore from backup…`}
            </Pill>
          </>
        )}
      </div>

      {pendingFile && !status?.staged && (
        <div className="mt-4 flex flex-wrap items-center gap-2 rounded-lg bg-destructive/10 px-4 py-3">
          <p className="w-full text-[13px] text-muted-foreground">
            <Trans>This replaces <span className="font-semibold text-foreground">the entire database</span> at
            the next restart. Type <span className="font-mono font-semibold text-destructive">RESTORE</span> to arm it.</Trans>
          </p>
          <input
            value={confirmText}
            onChange={e => setConfirmText(e.target.value)}
            placeholder="RESTORE"
            className="w-32 font-mono h-9 rounded-lg bg-secondary px-3 text-[14px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <Pill
            onClick={stage}
            disabled={confirmText !== 'RESTORE' || staging}
            variant="destructive"
            className="bg-destructive text-white hover:bg-destructive/90 hover:text-white"
          >
            {staging ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
            <Trans>Stage restore</Trans>
          </Pill>
          <Pill
            onClick={() => { setPendingFile(null); setConfirmText('') }}
            variant="secondary"
          >
            <Trans>Cancel</Trans>
          </Pill>
        </div>
      )}

      {status?.staged && (
        <div className="mt-4 flex flex-wrap items-center gap-2 rounded-lg bg-warning/10 px-4 py-3">
          <p className="min-w-0 flex-1 text-[13px] text-foreground">
            <Trans>Restore staged</Trans>
            {status.summary
              ? (() => { const when = status.summary.created_at; const v = status.summary.version; const users = status.summary.users; const books = status.summary.books; return t` — backup from ${when} (v${v}, ${users} users, ${books} books).` })()
              : '.'}{' '}
            <span className="text-muted-foreground"><Trans>It applies at the next server restart.</Trans></span>
          </p>
          <Pill
            onClick={() => api.delete('/admin/backup/restore').then(loadStatus)}
            variant="secondary"
          >
            <X className="h-3 w-3" /> <Trans>Cancel restore</Trans>
          </Pill>
        </div>
      )}

      {error && <p className="mt-2 text-[13px] text-destructive">{error}</p>}
    </PanelSection>
  )
}
