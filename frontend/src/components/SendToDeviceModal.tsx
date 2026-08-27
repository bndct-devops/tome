import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Send, X, Loader2, AlertTriangle } from 'lucide-react'
import { BookAnimation } from '@/components/BookAnimation'
import { ModalShell } from '@/components/ModalShell'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import type { BookFile } from '@/lib/books'
import { formatBytes } from '@/lib/books'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'

interface Device {
  id: number
  name: string
  email: string
}

interface SmtpStatus {
  configured: boolean
}

interface SendToDeviceModalProps {
  open: boolean
  onClose: () => void
  books: { id: number; title: string; files: BookFile[] }[]
}

export function SendToDeviceModal({ open, onClose, books }: SendToDeviceModalProps) {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [devices, setDevices] = useState<Device[]>([])
  const [smtpConfigured, setSmtpConfigured] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedDevice, setSelectedDevice] = useState<number | null>(null)
  const [selectedFileId, setSelectedFileId] = useState<number | null>(null)
  const [sending, setSending] = useState(false)

  const isBulk = books.length > 1
  const singleBook = books.length === 1 ? books[0] : null

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setSending(false)
    Promise.all([
      api.get<Device[]>('/devices').catch(() => [] as Device[]),
      api.get<SmtpStatus>('/smtp-status').catch(() => ({ configured: false })),
    ]).then(([devs, smtp]) => {
      setDevices(devs)
      setSmtpConfigured(smtp.configured)
      if (devs.length > 0 && !selectedDevice) {
        setSelectedDevice(devs[0].id)
      }
      if (singleBook && singleBook.files.length > 0 && !selectedFileId) {
        const preferred = ['epub', 'pdf', 'mobi', 'cbz', 'cbr']
        const best = preferred.reduce<BookFile | null>((acc, fmt) => {
          if (acc) return acc
          return singleBook.files.find(f => f.format === fmt) ?? null
        }, null)
        setSelectedFileId(best?.id ?? singleBook.files[0].id)
      }
      setLoading(false)
    })
  }, [open])

  async function handleSend() {
    if (!selectedDevice) return
    setSending(true)

    try {
      if (isBulk) {
        const res = await api.post<{ sent: number; failed: number; errors: { book_id: number; error: string }[] }>(
          '/send-to-device/bulk',
          { book_ids: books.map(b => b.id), device_id: selectedDevice },
        )
        if (res.failed === 0) {
          toast.success(plural(res.sent, { one: 'Sent # book to device', other: 'Sent # books to device' }))
        } else {
          { const sent = res.sent, total = res.sent + res.failed, failedN = res.failed; toast.info(t`Sent ${sent}/${total}. ${failedN} failed.`) }
        }
        onClose()
      } else if (singleBook && selectedFileId) {
        await api.post(`/books/${singleBook.id}/send`, {
          device_id: selectedDevice,
          file_id: selectedFileId,
        })
        { const title = singleBook.title; toast.success(t`Sent "${title}" to device`) }
        onClose()
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t`Failed to send`)
    } finally {
      setSending(false)
    }
  }

  const selectedFile = singleBook?.files.find(f => f.id === selectedFileId)

  return (
    <ModalShell open={open} onClose={onClose} className="w-full max-w-md">
        <div className="bg-card border border-border rounded-2xl shadow-2xl max-h-[90vh] overflow-y-auto">
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-border">
            <div className="flex items-center gap-2">
              <Send className="w-4 h-4 text-primary" />
              <h2 className="text-sm font-semibold text-foreground"><Trans>Send to Device</Trans></h2>
            </div>
            <button
              onClick={onClose}
              className="p-1 rounded-lg hover:bg-muted transition-colors text-muted-foreground"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Body */}
          <div className="p-5 space-y-4">
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
              </div>
            ) : sending ? (
              <div className="flex flex-col items-center justify-center py-6 gap-3">
                <BookAnimation variant="send" className="block w-16 h-16 text-primary" />
                <p className="text-sm font-medium text-foreground">
                  <Trans>Sending</Trans><span className="dots-anim"><span>.</span><span>.</span><span>.</span></span>
                </p>
              </div>
            ) : smtpConfigured === false ? (
              <div className="text-center py-4 space-y-2">
                <AlertTriangle className="w-8 h-8 text-warning mx-auto" />
                <p className="text-sm font-medium text-foreground"><Trans>Email delivery is not set up yet</Trans></p>
                <p className="text-xs text-muted-foreground">
                  <Trans>See Settings for setup instructions.</Trans>
                </p>
                <button
                  onClick={() => { onClose(); navigate('/settings') }}
                  className="mt-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all"
                >
                  <Trans>Go to Settings</Trans>
                </button>
              </div>
            ) : devices.length === 0 ? (
              <div className="text-center py-4 space-y-2">
                <Send className="w-8 h-8 text-muted-foreground mx-auto" />
                <p className="text-sm font-medium text-foreground"><Trans>No devices configured</Trans></p>
                <p className="text-xs text-muted-foreground">
                  <Trans>Add a device in Settings to start sending books.</Trans>
                </p>
                <button
                  onClick={() => { onClose(); navigate('/settings') }}
                  className="mt-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all"
                >
                  <Trans>Go to Settings</Trans>
                </button>
              </div>
            ) : (
              <>
                {/* Book info */}
                {singleBook && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1"><Trans>Book</Trans></p>
                    <p className="text-sm text-foreground truncate">{singleBook.title}</p>
                  </div>
                )}
                {isBulk && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1"><Trans>Books</Trans></p>
                    <p className="text-sm text-foreground">{books.length} book{books.length !== 1 ? 's' : ''} selected</p>
                  </div>
                )}

                {/* Device selector */}
                <div>
                  <label className="block text-xs font-medium text-muted-foreground mb-1"><Trans>Device</Trans></label>
                  <select
                    value={selectedDevice ?? ''}
                    onChange={e => setSelectedDevice(Number(e.target.value))}
                    className="w-full h-9 rounded-lg border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                  >
                    {devices.map(d => (
                      <option key={d.id} value={d.id}>{d.name} ({d.email})</option>
                    ))}
                  </select>
                </div>

                {/* Format selector (single book with multiple files) */}
                {singleBook && singleBook.files.length > 1 && (
                  <div>
                    <label className="block text-xs font-medium text-muted-foreground mb-1"><Trans>Format</Trans></label>
                    <select
                      value={selectedFileId ?? ''}
                      onChange={e => setSelectedFileId(Number(e.target.value))}
                      className="w-full h-9 rounded-lg border border-border bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                    >
                      {singleBook.files.map(f => (
                        <option key={f.id} value={f.id}>
                          {f.format.toUpperCase()} {f.file_size ? `(${formatBytes(f.file_size)})` : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                {/* File size info */}
                {selectedFile?.file_size && (
                  <p className="text-xs text-muted-foreground">
                    {(() => { const size = formatBytes(selectedFile.file_size); return <Trans>File size: {size}</Trans> })()}
                    {selectedFile.file_size > 25 * 1024 * 1024 && (
                      <span className="text-warning ml-1"><Trans>(exceeds 25 MB email limit)</Trans></span>
                    )}
                  </p>
                )}

                {/* Send button */}
                <button
                  onClick={handleSend}
                  disabled={sending || !selectedDevice}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all disabled:opacity-50"
                >
                  {sending ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <Trans>Sending…</Trans>
                    </>
                  ) : (
                    <>
                      <Send className="w-4 h-4" />
                      {isBulk ? plural(books.length, { one: 'Send # book', other: 'Send # books' }) : t`Send`}
                    </>
                  )}
                </button>
              </>
            )}
          </div>
        </div>
    </ModalShell>
  )
}
