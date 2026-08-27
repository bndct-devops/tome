import { useRef, useState, useCallback, useEffect } from 'react'
import { X, FileText, Layers, Upload, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useBookTypes } from '@/lib/bookTypes'
import { useToast } from '@/contexts/ToastContext'
import { ModalShell } from '@/components/ModalShell'
import { cn } from '@/lib/utils'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'

interface UploadItem {
  id: string
  file: File
  bookTypeId: string
  status: 'pending' | 'uploading' | 'done' | 'error' | 'duplicate'
  errorMsg?: string
  /** Book already holding these exact bytes (pre-upload hash check). */
  dupBookId?: number
}

// Hash in the browser and ask the server before uploading — the server would
// detect the duplicate anyway (upload attaches nothing for identical bytes),
// but only after the whole file crossed the wire.
async function sha256Hex(file: File): Promise<string | null> {
  if (file.size > 512 * 1024 * 1024) return null // don't buffer >512MB in RAM
  try {
    const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
    return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('')
  } catch {
    return null
  }
}

function formatIcon(file: File) {
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (['cbz', 'cbr'].includes(ext)) return <Layers className="w-4 h-4 text-muted-foreground shrink-0" />
  return <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
}

interface UploadResult {
  id: number
  matched_wish_ids?: number[] | null
}

interface Props {
  isOpen: boolean
  onClose: () => void
  onDone: () => void
  onUploaded?: (bookIds: number[]) => void
  /** Called with total matched wish IDs across all uploads in this session */
  onWishMatches?: (wishIds: number[], bookIds: number[]) => void
}

export function UploadModal({ isOpen, onClose, onDone, onUploaded, onWishMatches }: Props) {
  const bookTypes = useBookTypes()
  const { toast } = useToast()
  const [items, setItems] = useState<UploadItem[]>([])
  const [bulkType, setBulkType] = useState('')
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [summary, setSummary] = useState<{ success: number; failed: number } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  function addFiles(files: File[]) {
    const newItems: UploadItem[] = files.map(f => ({
      id: `${f.name}-${f.size}-${Date.now()}-${Math.random()}`,
      file: f,
      bookTypeId: '',
      status: 'pending',
    }))
    setItems(prev => [...prev, ...newItems])
    setSummary(null)
    void flagDuplicates(newItems)
  }

  async function flagDuplicates(newItems: UploadItem[]) {
    // Sequential hashing keeps peak memory at one file's buffer.
    const hashed: { id: string; hash: string }[] = []
    for (const it of newItems) {
      const hash = await sha256Hex(it.file)
      if (hash) hashed.push({ id: it.id, hash })
    }
    if (!hashed.length) return
    try {
      const resp = await api.post<{ existing: Record<string, number> }>(
        '/books/check-hashes',
        { hashes: hashed.map(h => h.hash) },
      )
      setItems(prev => prev.map(p => {
        const h = hashed.find(x => x.id === p.id)
        const bookId = h ? resp.existing[h.hash] : undefined
        return bookId && p.status === 'pending'
          ? { ...p, status: 'duplicate', dupBookId: bookId }
          : p
      }))
    } catch {
      // Offline / older server: uploads proceed, the server still dedupes.
    }
  }

  function handleFileInput(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files) addFiles(Array.from(e.target.files))
    e.target.value = ''
  }

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const files = Array.from(e.dataTransfer.files).filter(f =>
      /\.(epub|pdf|cbz|cbr|mobi|azw3)$/i.test(f.name)
    )
    if (files.length) addFiles(files)
  }, [])

  function setItemType(id: string, bookTypeId: string) {
    setItems(prev => prev.map(it => it.id === id ? { ...it, bookTypeId } : it))
  }

  function setAllTypes(bookTypeId: string) {
    setItems(prev => prev.map(it => it.status === 'pending' ? { ...it, bookTypeId } : it))
  }

  function removeItem(id: string) {
    setItems(prev => prev.filter(it => it.id !== id))
  }

  async function uploadAll() {
    if (!items.length || uploading) return
    setUploading(true)
    setSummary(null)
    let success = 0
    let failed = 0
    const uploadedIds: number[] = []
    const allMatchedWishIds: number[] = []
    const matchedBookIds: number[] = []

    let skipped = 0
    for (const item of items) {
      if (item.status === 'duplicate') {
        skipped++
        continue
      }
      setItems(prev => prev.map(it => it.id === item.id ? { ...it, status: 'uploading' } : it))
      const form = new FormData()
      form.append('file', item.file)
      if (item.bookTypeId) form.append('book_type_id', item.bookTypeId)
      try {
        const result = await api.upload<UploadResult>('/books/upload', form)
        uploadedIds.push(result.id)
        if (result.matched_wish_ids && result.matched_wish_ids.length > 0) {
          allMatchedWishIds.push(...result.matched_wish_ids)
          matchedBookIds.push(result.id)
        }
        setItems(prev => prev.map(it => it.id === item.id ? { ...it, status: 'done' } : it))
        success++
      } catch (err) {
        setItems(prev => prev.map(it =>
          it.id === item.id
            ? { ...it, status: 'error', errorMsg: err instanceof Error ? err.message : t`Upload failed` }
            : it
        ))
        failed++
      }
    }

    setUploading(false)
    setSummary({ success, failed })
    if (onUploaded && uploadedIds.length > 0) {
      onUploaded(uploadedIds)
    }
    if (onWishMatches && allMatchedWishIds.length > 0) {
      onWishMatches(allMatchedWishIds, matchedBookIds)
    }
    if (success > 0) {
      onDone()
      const skipNote = skipped > 0 ? t`, ${skipped} already in library` : ''
      if (failed === 0) {
        toast.success(plural(success, { one: '# book uploaded', other: '# books uploaded' }) + skipNote)
      } else {
        toast.info(t`${success} uploaded, ${failed} failed` + skipNote)
      }
    } else if (failed > 0) {
      toast.error(plural(failed, { one: 'Upload failed for # file', other: 'Upload failed for # files' }))
    } else if (skipped > 0) {
      toast.info(plural(skipped, { one: 'Nothing to upload — # file is already in your library', other: 'Nothing to upload — # files are already in your library' }))
    }
  }

  // Fresh state on every open; content stays intact during the exit animation.
  useEffect(() => {
    if (isOpen) {
      setItems([])
      setBulkType('')
      setSummary(null)
    }
  }, [isOpen])

  function handleClose() {
    if (uploading) return
    onClose()
  }

  const pendingCount = items.filter(it => it.status === 'pending').length

  return (
    <ModalShell open={isOpen} onClose={handleClose} className="w-full max-w-lg">
      <div className="bg-card text-foreground rounded-2xl shadow-xl shadow-accent-soft flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <h2 className="text-base font-semibold flex items-center gap-2">
            <Upload className="w-4 h-4 text-muted-foreground" /> <Trans>Upload Books</Trans>
          </h2>
          <button
            onClick={handleClose}
            disabled={uploading}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Drop zone */}
          <div
            onDragOver={e => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={cn(
              'border-2 border-dashed rounded-xl px-6 py-8 text-center cursor-pointer transition-colors',
              dragging
                ? 'border-primary bg-primary/5'
                : 'border-border hover:border-primary/40 hover:bg-muted/40'
            )}
          >
            <Upload className="w-6 h-6 text-muted-foreground mx-auto mb-2" />
            <p className="text-sm text-muted-foreground">
              <Trans>Drop books here or <span className="text-primary">click to browse</span></Trans>
            </p>
            {/* eslint-disable-next-line lingui/no-unlocalized-strings -- file extensions */}
            <p className="text-xs text-muted-foreground/60 mt-1">epub, pdf, cbz, cbr, mobi, azw3</p>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".epub,.pdf,.cbz,.cbr,.mobi,.azw3"
            className="hidden"
            onChange={handleFileInput}
          />

          {/* Bulk type selector */}
          {items.length > 1 && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground shrink-0"><Trans>Set all to</Trans></span>
              <select
                value={bulkType}
                onChange={e => { setBulkType(e.target.value); setAllTypes(e.target.value) }}
                className="flex-1 text-sm sm:text-xs rounded-md border border-border bg-background px-1.5 py-2 sm:py-1 focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value="">{t`No type`}</option>
                {bookTypes.map(bt => (
                  <option key={bt.id} value={String(bt.id)}>{bt.label}</option>
                ))}
              </select>
            </div>
          )}

          {/* File list */}
          {items.length > 0 && (
            <div className="space-y-2">
              {items.map(item => (
                <div key={item.id} className={cn('flex flex-col rounded-lg border bg-muted/30', item.status === 'error' ? 'border-destructive/40' : 'border-border')}>
                  <div className="flex items-center gap-2 p-2.5">
                  {formatIcon(item.file)}
                  <span className="flex-1 min-w-0 text-sm truncate" title={item.file.name}>
                    {item.file.name}
                  </span>
                  {/* Type dropdown */}
                  <select
                    value={item.bookTypeId}
                    onChange={e => setItemType(item.id, e.target.value)}
                    disabled={item.status !== 'pending'}
                    className="shrink-0 text-sm sm:text-xs rounded-md border border-border bg-background px-1.5 py-2 sm:py-1 focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
                  >
                    <option value="">{t`No type`}</option>
                    {bookTypes.map(bt => (
                      <option key={bt.id} value={String(bt.id)}>{bt.label}</option>
                    ))}
                  </select>
                  {/* Status */}
                  <span className="shrink-0 w-5 flex items-center justify-center">
                    {(item.status === 'pending' || item.status === 'duplicate') && (
                      <button
                        onClick={() => removeItem(item.id)}
                        className="text-muted-foreground hover:text-destructive transition-colors"
                        title={t`Remove`}
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    )}
                    {item.status === 'uploading' && (
                      <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
                    )}
                    {item.status === 'done' && (
                      <CheckCircle2 className="w-4 h-4 text-success" />
                    )}
                    {item.status === 'error' && (
                      <AlertCircle className="w-4 h-4 text-destructive" />
                    )}
                  </span>
                  </div>
                  {item.status === 'error' && item.errorMsg && (
                    <p className="px-2.5 pb-2 text-xs text-destructive">{item.errorMsg}</p>
                  )}
                  {item.status === 'duplicate' && (
                    <p className="px-2.5 pb-2 text-xs text-warning">
                      <Trans>Already in your library — will be skipped.</Trans>{' '}
                      <a
                        href={`/books/${item.dupBookId}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="underline hover:no-underline"
                      >
                        <Trans>View book</Trans>
                      </a>
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Summary */}
          {summary && (
            <div className={cn(
              'flex items-center gap-2 px-3 py-2 rounded-lg text-sm',
              summary.failed === 0
                ? 'bg-success/10 text-success border border-success/20'
                : 'bg-warning/10 text-warning border border-warning/20'
            )}>
              {summary.failed === 0
                ? <CheckCircle2 className="w-4 h-4 shrink-0" />
                : <AlertCircle className="w-4 h-4 shrink-0" />}
              {(() => { const ok = summary.success; const failedN = summary.failed; return t`${ok} uploaded` + (failedN > 0 ? t`, ${failedN} failed` : '') })()}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-border flex items-center justify-between gap-3 shrink-0">
          <span className="text-xs text-muted-foreground">
            {items.length > 0 ? plural(items.length, { one: '# file selected', other: '# files selected' }) : ''}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={handleClose}
              disabled={uploading}
              className="px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
            >
              {summary ? t`Close` : t`Cancel`}
            </button>
            <button
              onClick={uploadAll}
              disabled={uploading || pendingCount === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
            >
              {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
              <Trans>Upload All</Trans>
            </button>
          </div>
        </div>
      </div>
    </ModalShell>
  )
}
