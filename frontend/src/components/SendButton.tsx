import { useState, useEffect, useRef } from 'react'
import { Send, ChevronDown, Loader2, Mail } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import type { BookFile } from '@/lib/books'
import { SendToDeviceModal } from '@/components/SendToDeviceModal'

interface SendStatus {
  configured: boolean
  koreader: boolean
}

interface SendButtonProps {
  books: { id: number; title: string; files: BookFile[] }[]
  /** 'rail' = full-width book-detail action; 'bulk' = compact dashboard action. */
  variant?: 'rail' | 'bulk'
  disabled?: boolean
}

/**
 * Send books to a device. When the Send-to-KOReader inbox (beta) is enabled on
 * the server, this is a split button: the primary action queues to KOReader,
 * the caret offers "Send via email…" (the existing SMTP modal). When the inbox
 * is off, it is the plain "Send to Device" button (email modal only).
 */
export function SendButton({ books, variant = 'rail', disabled }: SendButtonProps) {
  const { toast } = useToast()
  const [status, setStatus] = useState<SendStatus | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [sending, setSending] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.get<SendStatus>('/smtp-status')
      .then(setStatus)
      .catch(() => setStatus({ configured: false, koreader: false }))
  }, [])

  useEffect(() => {
    if (!menuOpen) return
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [menuOpen])

  async function sendToKoreader() {
    setMenuOpen(false)
    if (books.length === 0) return
    setSending(true)
    try {
      const res = await api.post<{ queued: number; skipped: number }>(
        '/send-to-device/koreader',
        { book_ids: books.map(b => b.id) },
      )
      if (res.queued > 0) {
        toast.success(
          `Queued ${res.queued} to KOReader — arrives next time KOReader syncs`,
        )
      } else {
        toast.info('Already in your KOReader inbox')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to queue for KOReader')
    } finally {
      setSending(false)
    }
  }

  // Base button styling. The hover lift is deliberately NOT here: on the split
  // button it would lift each half independently and break the seam. The plain
  // (non-split) rail button adds the lift back below for parity with the old
  // Send-to-Device button.
  const btn = variant === 'bulk'
    ? 'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted disabled:opacity-50 transition-all'
    : 'flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium border border-border bg-card text-foreground hover:bg-muted disabled:opacity-50 transition-all duration-200'

  // Feature off (or unknown) → plain email button, current behaviour.
  if (!status?.koreader) {
    return (
      <>
        <button
          onClick={() => setModalOpen(true)}
          disabled={disabled}
          className={variant === 'rail' ? `mt-2 w-full ${btn} hover:-translate-y-0.5 hover:shadow-sm` : btn}
        >
          <Send className="w-3.5 h-3.5 text-muted-foreground" />
          Send to Device
        </button>
        <SendToDeviceModal open={modalOpen} onClose={() => setModalOpen(false)} books={books} />
      </>
    )
  }

  // Feature on → split button: primary = KOReader, caret = email.
  return (
    <>
      <div ref={wrapRef} className={`relative flex ${variant === 'rail' ? 'mt-2 w-full' : ''}`}>
        <button
          onClick={sendToKoreader}
          disabled={disabled || sending}
          className={`${btn} rounded-r-none ${variant === 'rail' ? 'flex-1' : ''}`}
        >
          {sending
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <Send className="w-3.5 h-3.5 text-muted-foreground" />}
          Send to KOReader
          <span className="ml-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground border border-border rounded px-1 leading-tight">
            Beta
          </span>
        </button>
        <button
          onClick={() => setMenuOpen(o => !o)}
          disabled={disabled}
          aria-label="More send options"
          className={`${btn} rounded-l-none border-l-0 px-2`}
        >
          <ChevronDown className="w-3.5 h-3.5" />
        </button>
        {menuOpen && (
          <div className="absolute right-0 top-full mt-1 z-20 min-w-[12rem] rounded-lg border border-border bg-card shadow-lg py-1">
            <button
              onClick={() => { setMenuOpen(false); setModalOpen(true) }}
              className="w-full flex items-center gap-2 px-3 py-2 text-xs text-foreground hover:bg-muted transition-colors"
            >
              <Mail className="w-3.5 h-3.5 text-muted-foreground" />
              Send via email…
            </button>
          </div>
        )}
      </div>
      <SendToDeviceModal open={modalOpen} onClose={() => setModalOpen(false)} books={books} />
    </>
  )
}
