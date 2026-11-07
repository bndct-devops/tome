import { useRef, useState, useCallback, useEffect } from 'react'
import { X, FileText, Layers, Upload, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useBookTypes } from '@/lib/bookTypes'
import { useToast } from '@/contexts/ToastContext'
import { ModalShell } from '@/components/ModalShell'
import { cn } from '@/lib/utils'
import { FormLabel, Pill, Select } from '@/components/page'
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

// Hash in the browser and ask the server before uploading; the server would
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
      toast.info(plural(skipped, { one: 'Nothing to upload, # file is already in your library', other: 'Nothing to upload, # files are already in your library' }))
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

  const summaryText = summary
    ? (() => { const ok = summary.success; const failedN = summary.failed; return t`${ok} uploaded` + (failedN > 0 ? t`, ${failedN} failed` : '') })()
    : null

  return (
    <ModalShell
      open={isOpen}
      onClose={handleClose}
      hideClose={uploading}
      closeOnBackdrop={!uploading}
      title={<Trans>Upload books</Trans>}
      size="md"
      footer={
        <>
          <span className="mr-auto text-[13px] text-muted-foreground">
            {items.length > 0 ? plural(items.length, { one: '# file selected', other: '# files selected' }) : ''}
          </span>
          <Pill variant="quiet" onClick={handleClose} disabled={uploading}>
            {summary ? t`Close` : t`Cancel`}
          </Pill>
          <Pill variant="primary" onClick={uploadAll} disabled={uploading || pendingCount === 0}>
            {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            <Trans>Upload all</Trans>
          </Pill>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {/* Drop zone */}
        <div
          role="button"
          tabIndex={0}
          onDragOver={e => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInputRef.current?.click() } }}
          className={cn(
            'cursor-pointer rounded-[14px] border border-dashed px-6 py-7 text-center transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            dragging
              ? 'border-primary bg-primary/5'
              : 'border-border bg-secondary/40 hover:border-primary/40 hover:bg-secondary/70',
          )}
        >
          <Upload className="mx-auto mb-2 h-5 w-5 text-muted-foreground" />
          <p className="text-[15px] text-foreground">
            <Trans>Drop books here or <span className="text-primary">click to browse</span></Trans>
          </p>
          {/* eslint-disable-next-line lingui/no-unlocalized-strings -- file extensions */}
          <p className="mt-1 text-[13px] text-muted-foreground">epub, pdf, cbz, cbr, mobi, azw3</p>
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
          <div className="flex items-center gap-3">
            <FormLabel htmlFor="upload-set-all" className="mb-0 shrink-0"><Trans>Set all to</Trans></FormLabel>
            <Select
              id="upload-set-all"
              value={bulkType}
              onChange={e => { setBulkType(e.target.value); setAllTypes(e.target.value) }}
              className="flex-1"
            >
              <option value="">{t`No type`}</option>
              {bookTypes.map(bt => (
                <option key={bt.id} value={String(bt.id)}>{bt.label}</option>
              ))}
            </Select>
          </div>
        )}

        {/* File list: rows on hairlines */}
        {items.length > 0 && (
          <ul className="border-t border-border/70">
            {items.map(item => (
              <li key={item.id} className="border-b border-border/70 py-3">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                  <div className="flex min-w-0 flex-1 items-center gap-2.5">
                    {formatIcon(item.file)}
                    <span className="min-w-0 flex-1 truncate text-[15px] text-foreground" title={item.file.name}>
                      {item.file.name}
                    </span>
                    {/* Status (phones: at the end of the name line) */}
                    <span className="flex w-8 shrink-0 items-center justify-center sm:hidden">
                      <ItemStatus item={item} onRemove={removeItem} />
                    </span>
                  </div>
                  <Select
                    value={item.bookTypeId}
                    onChange={e => setItemType(item.id, e.target.value)}
                    disabled={item.status !== 'pending'}
                    aria-label={t`Book type`}
                    className="sm:w-40 sm:shrink-0"
                  >
                    <option value="">{t`No type`}</option>
                    {bookTypes.map(bt => (
                      <option key={bt.id} value={String(bt.id)}>{bt.label}</option>
                    ))}
                  </Select>
                  <span className="hidden w-8 shrink-0 items-center justify-center sm:flex">
                    <ItemStatus item={item} onRemove={removeItem} />
                  </span>
                </div>
                {item.status === 'error' && item.errorMsg && (
                  <p className="mt-1.5 pl-[26px] text-[13px] text-destructive">{item.errorMsg}</p>
                )}
                {item.status === 'duplicate' && (
                  <p className="mt-1.5 pl-[26px] text-[13px] text-warning">
                    <Trans>Already in your library, will be skipped.</Trans>{' '}
                    <a
                      href={`/books/${item.dupBookId}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline underline-offset-2 hover:no-underline"
                    >
                      <Trans>View book</Trans>
                    </a>
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}

        {/* Summary */}
        {summary && (
          <p className={cn('flex items-center gap-2 text-[14px]', summary.failed === 0 ? 'text-success' : 'text-warning')}>
            {summary.failed === 0
              ? <CheckCircle2 className="h-4 w-4 shrink-0" />
              : <AlertCircle className="h-4 w-4 shrink-0" />}
            {summaryText}
          </p>
        )}
      </div>
    </ModalShell>
  )
}

function ItemStatus({ item, onRemove }: { item: UploadItem; onRemove: (id: string) => void }) {
  if (item.status === 'pending' || item.status === 'duplicate') {
    return (
      <Pill variant="destructive" icon onClick={() => onRemove(item.id)} title={t`Remove`} aria-label={t`Remove`} className="h-8 w-8">
        <X className="h-4 w-4" />
      </Pill>
    )
  }
  if (item.status === 'uploading') return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
  if (item.status === 'done') return <CheckCircle2 className="h-4 w-4 text-success" />
  return <AlertCircle className="h-4 w-4 text-destructive" />
}
