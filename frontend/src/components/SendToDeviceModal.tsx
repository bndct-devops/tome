import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Send, Loader2, AlertTriangle } from 'lucide-react'
import { BookAnimation } from '@/components/BookAnimation'
import { ModalShell } from '@/components/ModalShell'
import { FormLabel, Pill, Select } from '@/components/page'
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
  const goSettings = () => { onClose(); navigate('/settings') }
  const blocked = !loading && (smtpConfigured === false || devices.length === 0)

  const footer = loading ? (
    <Pill variant="quiet" onClick={onClose}><Trans>Cancel</Trans></Pill>
  ) : blocked ? (
    <>
      <Pill variant="quiet" onClick={onClose}><Trans>Close</Trans></Pill>
      <Pill variant="primary" onClick={goSettings}><Trans>Go to Settings</Trans></Pill>
    </>
  ) : (
    <>
      <Pill variant="quiet" onClick={onClose} disabled={sending}><Trans>Cancel</Trans></Pill>
      <Pill variant="primary" onClick={handleSend} disabled={sending || !selectedDevice}>
        {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        {sending ? t`Sending…` : isBulk ? plural(books.length, { one: 'Send # book', other: 'Send # books' }) : t`Send`}
      </Pill>
    </>
  )

  return (
    <ModalShell
      open={open}
      onClose={onClose}
      title={<Trans>Send to device</Trans>}
      size="sm"
      footer={footer}
    >
      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : sending ? (
        <div className="flex flex-col items-center justify-center gap-3 py-6">
          <BookAnimation variant="send" className="block h-16 w-16 text-primary" />
          <p className="text-[15px] font-medium text-foreground">
            <Trans>Sending</Trans><span className="dots-anim"><span>.</span><span>.</span><span>.</span></span>
          </p>
        </div>
      ) : smtpConfigured === false ? (
        <div className="flex items-start gap-3 border-y border-border/70 py-4">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-warning" />
          <div className="min-w-0">
            <p className="text-[15px] font-medium text-foreground"><Trans>Email delivery is not set up yet</Trans></p>
            <p className="mt-0.5 text-[14px] text-muted-foreground"><Trans>See Settings for setup instructions.</Trans></p>
          </div>
        </div>
      ) : devices.length === 0 ? (
        <div className="flex items-start gap-3 border-y border-border/70 py-4">
          <Send className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            <p className="text-[15px] font-medium text-foreground"><Trans>No devices configured</Trans></p>
            <p className="mt-0.5 text-[14px] text-muted-foreground"><Trans>Add a device in Settings to start sending books.</Trans></p>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {/* Book info */}
          {singleBook && (
            <div>
              <FormLabel><Trans>Book</Trans></FormLabel>
              <p className="truncate text-[15px] text-foreground">{singleBook.title}</p>
            </div>
          )}
          {isBulk && (
            <div>
              <FormLabel><Trans>Books</Trans></FormLabel>
              <p className="text-[15px] text-foreground">{plural(books.length, { one: '# book selected', other: '# books selected' })}</p>
            </div>
          )}

          {/* Device selector */}
          <div>
            <FormLabel htmlFor="send-device"><Trans>Device</Trans></FormLabel>
            <Select id="send-device" value={selectedDevice ?? ''} onChange={e => setSelectedDevice(Number(e.target.value))}>
              {devices.map(d => (
                <option key={d.id} value={d.id}>{d.name} ({d.email})</option>
              ))}
            </Select>
          </div>

          {/* Format selector (single book with multiple files) */}
          {singleBook && singleBook.files.length > 1 && (
            <div>
              <FormLabel htmlFor="send-format"><Trans>Format</Trans></FormLabel>
              <Select id="send-format" value={selectedFileId ?? ''} onChange={e => setSelectedFileId(Number(e.target.value))}>
                {singleBook.files.map(f => (
                  <option key={f.id} value={f.id}>
                    {f.format.toUpperCase()} {f.file_size ? `(${formatBytes(f.file_size)})` : ''}
                  </option>
                ))}
              </Select>
            </div>
          )}

          {/* File size info */}
          {selectedFile?.file_size && (
            <p className="text-[13px] text-muted-foreground">
              {(() => { const size = formatBytes(selectedFile.file_size); return <Trans>File size: {size}</Trans> })()}
              {selectedFile.file_size > 25 * 1024 * 1024 && (
                <span className="ml-1 text-warning"><Trans>(exceeds 25 MB email limit)</Trans></span>
              )}
            </p>
          )}
        </div>
      )}
    </ModalShell>
  )
}
