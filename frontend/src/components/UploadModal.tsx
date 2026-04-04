import { useRef, useState, useCallback } from 'react'
import { X, FileText, Layers, Upload, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useBookTypes } from '@/lib/bookTypes'
import { useToast } from '@/contexts/ToastContext'
import { cn } from '@/lib/utils'

interface UploadItem {
  id: string
  file: File
  bookTypeId: string
  status: 'pending' | 'uploading' | 'done' | 'error'
  errorMsg?: string
}

function formatIcon(file: File) {
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (['cbz', 'cbr'].includes(ext)) return <Layers className="w-4 h-4 text-muted-foreground shrink-0" />
  return <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
}

interface Props {
  isOpen: boolean
  onClose: () => void
  onDone: () => void
  onUploaded?: (bookIds: number[]) => void
}

export function UploadModal({ isOpen, onClose, onDone, onUploaded }: Props) {
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

    for (const item of items) {
      setItems(prev => prev.map(it => it.id === item.id ? { ...it, status: 'uploading' } : it))
      const form = new FormData()
      form.append('file', item.file)
      if (item.bookTypeId) form.append('book_type_id', item.bookTypeId)
      try {
        const result = await api.upload<{ id: number }>('/books/upload', form)
        uploadedIds.push(result.id)
        setItems(prev => prev.map(it => it.id === item.id ? { ...it, status: 'done' } : it))
        success++
      } catch (err) {
        setItems(prev => prev.map(it =>
          it.id === item.id
            ? { ...it, status: 'error', errorMsg: err instanceof Error ? err.message : 'Upload failed' }
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
    if (success > 0) {
      onDone()
      if (failed === 0) {
        toast.success(`${success} book${success !== 1 ? 's' : ''} uploaded`)
      } else {
        toast.info(`${success} uploaded, ${failed} failed`)
      }
    } else if (failed > 0) {
      toast.error(`Upload failed for ${failed} file${failed !== 1 ? 's' : ''}`)
    }
  }

  function handleClose() {
    if (uploading) return
    setItems([])
    setBulkType('')
    setSummary(null)
    onClose()
  }

  if (!isOpen) return null

  const pendingCount = items.filter(it => it.status === 'pending').length

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onMouseDown={e => { if (e.target === e.currentTarget) handleClose() }}
    >
      <div className="bg-card text-foreground rounded-xl shadow-xl w-full max-w-lg flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <h2 className="text-base font-semibold flex items-center gap-2">
            <Upload className="w-4 h-4 text-muted-foreground" /> Upload Books
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
              Drop books here or <span className="text-primary">click to browse</span>
            </p>
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
              <span className="text-muted-foreground shrink-0">Set all to</span>
              <select
                value={bulkType}
                onChange={e => { setBulkType(e.target.value); setAllTypes(e.target.value) }}
                className="flex-1 text-sm sm:text-xs rounded-md border border-border bg-background px-1.5 py-2 sm:py-1 focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value="">No type</option>
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
                    <option value="">No type</option>
                    {bookTypes.map(bt => (
                      <option key={bt.id} value={String(bt.id)}>{bt.label}</option>
                    ))}
                  </select>
                  {/* Status */}
                  <span className="shrink-0 w-5 flex items-center justify-center">
                    {item.status === 'pending' && (
                      <button
                        onClick={() => removeItem(item.id)}
                        className="text-muted-foreground hover:text-destructive transition-colors"
                        title="Remove"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    )}
                    {item.status === 'uploading' && (
                      <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
                    )}
                    {item.status === 'done' && (
                      <CheckCircle2 className="w-4 h-4 text-green-500" />
                    )}
                    {item.status === 'error' && (
                      <AlertCircle className="w-4 h-4 text-destructive" />
                    )}
                  </span>
                  </div>
                  {item.status === 'error' && item.errorMsg && (
                    <p className="px-2.5 pb-2 text-xs text-destructive">{item.errorMsg}</p>
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
                ? 'bg-green-500/10 text-green-700 dark:text-green-400 border border-green-500/20'
                : 'bg-yellow-400/10 text-yellow-700 dark:text-yellow-400 border border-yellow-400/20'
            )}>
              {summary.failed === 0
                ? <CheckCircle2 className="w-4 h-4 shrink-0" />
                : <AlertCircle className="w-4 h-4 shrink-0" />}
              {summary.success} uploaded{summary.failed > 0 ? `, ${summary.failed} failed` : ''}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-border flex items-center justify-between gap-3 shrink-0">
          <span className="text-xs text-muted-foreground">
            {items.length > 0 ? `${items.length} file${items.length !== 1 ? 's' : ''} selected` : ''}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={handleClose}
              disabled={uploading}
              className="px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
            >
              {summary ? 'Close' : 'Cancel'}
            </button>
            <button
              onClick={uploadAll}
              disabled={uploading || pendingCount === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-all"
            >
              {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
              Upload All
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
