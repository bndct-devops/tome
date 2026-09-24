// "Scan a sync code": read the QR pages the TomeSync plugin shows on an
// offline KOReader device and post them to Tome. The browser never decodes the
// payload - it only reads the page header (TSC1:<id>:<i>/<n>) to know when it
// has every page, then hands the raw strings to POST /api/sync-code, which
// decodes, dedups and writes them. What landed comes back as a per-book
// overview shown here.
//
// Two ways to read: live camera (needs a secure context, so HTTPS or
// localhost) and a photo picked from the device, which works everywhere.
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import jsQR from 'jsqr'
import { Camera, CheckCircle, ImageUp, Loader2, ScanLine, X } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ModalShell } from '@/components/ModalShell'
import { Trans, Plural } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

interface SyncCodeBook {
  book_id: number
  title: string | null
  author: string | null
  series: string | null
  series_index: number | null
  has_cover: boolean
  sessions_added: number
  sessions_known: number
  seconds: number
  pages: number
  progress_before: number | null
  progress_after: number | null
  status: string | null
  position: 'applied' | 'kept' | null
  rating: number | null
}

export interface SyncCodeResult {
  device: string | null
  generated_at: string
  clock_offset_seconds: number
  sessions_added: number
  sessions_known: number
  positions_applied: number
  positions_kept: number
  ratings_applied: number
  unknown_books: number
  books: SyncCodeBook[]
}

const PAGE_RE = /^TSC1:([0-9a-fA-F]{4,16}):(\d{1,3})\/(\d{1,3}):/

interface Header { id: string; index: number; total: number }

function parseHeader(text: string): Header | null {
  const m = PAGE_RE.exec(text.trim())
  if (!m) return null
  const index = Number(m[2])
  const total = Number(m[3])
  if (index < 1 || total < 1 || index > total) return null
  return { id: m[1].toLowerCase(), index, total }
}

function formatMinutes(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return t`${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? t`${hours} h ${rest} min` : t`${hours} h`
}

/** "3 min", "5 h" or "4 days": how far off the device clock was. */
function formatOffset(seconds: number): string {
  const abs = Math.abs(seconds)
  if (abs >= 86400) { const d = Math.round(abs / 86400); return t`${d} days` }
  if (abs >= 3600) { const h = Math.round(abs / 3600); return t`${h} h` }
  const m = Math.max(1, Math.round(abs / 60))
  return t`${m} min`
}

function pct(v: number | null): string {
  return v === null ? '–' : `${Math.round(v * 100)}%`
}

export function ScanSyncCodeModal({ onClose }: { onClose: () => void }) {
  const [pages, setPages] = useState<Map<number, string>>(new Map())
  const [header, setHeader] = useState<Header | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [cameraState, setCameraState] = useState<'starting' | 'live' | 'unavailable'>('starting')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<SyncCodeResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const submitted = useRef(false)
  const headerRef = useRef<Header | null>(null)
  const pagesRef = useRef<Map<number, string>>(new Map())

  const submit = useCallback(async (all: string[]) => {
    if (submitted.current) return
    submitted.current = true
    setBusy(true)
    setError(null)
    try {
      const data = await api.post<SyncCodeResult>('/sync-code', { pages: all })
      setResult(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : t`Could not apply the sync code`)
      submitted.current = false
    } finally {
      setBusy(false)
    }
  }, [])

  // One decoded QR string, from the camera or a photo. Collects pages of one
  // code; a page read twice is harmless, a page from another code resets.
  const accept = useCallback((text: string) => {
    if (submitted.current) return
    const h = parseHeader(text)
    if (!h) {
      setStatus(t`That QR code is not a Tome sync code.`)
      return
    }
    const current = headerRef.current
    const same = current !== null && current.id === h.id && current.total === h.total
    const next = same ? new Map(pagesRef.current) : new Map<number, string>()
    next.set(h.index, text.trim())
    headerRef.current = h
    pagesRef.current = next
    setPages(next)
    setHeader(h)
    setStatus(null)
    if (next.size === h.total) {
      void submit(Array.from(next.values()))
    }
  }, [submit])

  // Live camera: decode frames with jsQR a few times a second.
  useEffect(() => {
    if (result) return
    let stream: MediaStream | null = null
    let timer: number | undefined
    let stopped = false
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas || !navigator.mediaDevices?.getUserMedia) {
      setCameraState('unavailable')
      return
    }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
      .then(s => {
        if (stopped) { s.getTracks().forEach(tr => tr.stop()); return }
        stream = s
        video.srcObject = s
        void video.play().catch(() => undefined)
        setCameraState('live')
        const ctx = canvas.getContext('2d', { willReadFrequently: true })
        const tick = () => {
          if (stopped) return
          if (ctx && video.readyState >= 2 && video.videoWidth > 0) {
            // Downscale wide frames: jsQR is O(pixels) and the code fills the frame anyway.
            const scale = Math.min(1, 800 / video.videoWidth)
            canvas.width = Math.round(video.videoWidth * scale)
            canvas.height = Math.round(video.videoHeight * scale)
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
            const img = ctx.getImageData(0, 0, canvas.width, canvas.height)
            const found = jsQR(img.data, img.width, img.height, { inversionAttempts: 'dontInvert' })
            if (found?.data) accept(found.data)
          }
          timer = window.setTimeout(tick, 200)
        }
        tick()
      })
      .catch(() => setCameraState('unavailable'))
    return () => {
      stopped = true
      if (timer) window.clearTimeout(timer)
      stream?.getTracks().forEach(tr => tr.stop())
    }
  }, [accept, result])

  // A photo of the Kindle screen, for browsers without camera access (plain
  // HTTP on a home network is the common case).
  const decodeFile = async (file: File) => {
    setStatus(t`Reading the photo…`)
    try {
      const bitmap = await createImageBitmap(file)
      const canvas = document.createElement('canvas')
      const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height))
      canvas.width = Math.round(bitmap.width * scale)
      canvas.height = Math.round(bitmap.height * scale)
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('canvas')
      ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
      const img = ctx.getImageData(0, 0, canvas.width, canvas.height)
      const found = jsQR(img.data, img.width, img.height)
      if (!found?.data) {
        setStatus(t`No QR code found in that photo. Fill the frame with the code and try again.`)
        return
      }
      accept(found.data)
    } catch {
      setStatus(t`Could not read that photo.`)
    }
  }

  const reset = () => {
    submitted.current = false
    headerRef.current = null
    pagesRef.current = new Map()
    setPages(new Map())
    setHeader(null)
    setResult(null)
    setError(null)
    setStatus(null)
  }

  const collected = pages.size
  const total = header?.total ?? 0
  const missing = header ? Array.from({ length: total }, (_, i) => i + 1).filter(i => !pages.has(i)) : []
  const missingList = missing.join(', ')

  return createPortal(
    <ModalShell open onClose={onClose} className="w-full max-w-md">
      <div className="rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center gap-2 border-b border-border px-5 py-3.5">
          <ScanLine className="h-4 w-4 text-primary" />
          <h2 className="font-display text-base text-foreground">
            {result ? <Trans>Sync code applied</Trans> : <Trans>Scan a sync code</Trans>}
          </h2>
          <button
            onClick={onClose}
            aria-label={t`Close`}
            className="ml-auto rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {result ? (
          <SyncCodeOverview result={result} onAgain={reset} onDone={onClose} />
        ) : (
          <div className="px-5 py-4">
            <p className="text-xs leading-relaxed text-muted-foreground">
              <Trans>On the KOReader device open TomeSync and tap "Show sync code". Point the camera at it, or take a photo and pick it below. A code with several pages is read page by page.</Trans>
            </p>

            <div className="relative mt-4 overflow-hidden rounded-lg bg-black" style={{ aspectRatio: '4 / 3' }}>
              <video ref={videoRef} playsInline muted className={cn('h-full w-full object-cover', cameraState !== 'live' && 'hidden')} />
              <canvas ref={canvasRef} className="hidden" />
              {cameraState !== 'live' && (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center text-xs text-white/80">
                  {cameraState === 'starting'
                    ? <><Loader2 className="h-5 w-5 animate-spin" /><Trans>Starting the camera…</Trans></>
                    : <><Camera className="h-6 w-6 opacity-70" /><Trans>No camera available here. Browsers allow it only on HTTPS or localhost, and this device needs one. Take a photo of the code and pick it below instead.</Trans></>}
                </div>
              )}
              {cameraState === 'live' && (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                  <div className="h-48 w-48 rounded-2xl border-[3px] border-white/85" />
                </div>
              )}
            </div>

            <div className="mt-3 min-h-[2.5rem] text-center text-sm">
              {busy ? (
                <span className="inline-flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> <Trans>Applying…</Trans></span>
              ) : error ? (
                <span className="text-destructive">{error}</span>
              ) : header && collected < total ? (
                <span className="text-foreground">
                  <Trans>Page {collected} of {total} read. Tap the device for the next page.</Trans>
                  {missing.length > 0 && missing.length < total && (
                    <span className="ml-1 text-muted-foreground">(<Trans>missing: {missingList}</Trans>)</span>
                  )}
                </span>
              ) : status ? (
                <span className="text-muted-foreground">{status}</span>
              ) : (
                <span className="text-muted-foreground"><Trans>Waiting for a code…</Trans></span>
              )}
            </div>

            <div className="mt-2 flex items-center justify-center gap-2">
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                capture="environment"
                className="hidden"
                onChange={e => {
                  const f = e.target.files?.[0]
                  if (f) void decodeFile(f)
                  e.target.value = ''
                }}
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
              >
                <ImageUp className="h-3.5 w-3.5" /> <Trans>Use a photo</Trans>
              </button>
              {header && (
                <button
                  type="button"
                  onClick={reset}
                  disabled={busy}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                >
                  <Trans>Start over</Trans>
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </ModalShell>,
    document.body,
  )
}

/** What the scan changed, per book. Shared shape with the iOS app's overview. */
export function SyncCodeOverview({ result, onAgain, onDone }: { result: SyncCodeResult; onAgain: () => void; onDone: () => void }) {
  const nothingNew = result.sessions_added === 0 && result.positions_applied === 0 && result.ratings_applied === 0
  const deviceName = result.device ?? t`the device`
  const offset = formatOffset(result.clock_offset_seconds)
  return (
    <div className="px-5 py-4">
      <div className="flex items-start gap-3">
        <CheckCircle className={cn('mt-0.5 h-5 w-5 shrink-0', nothingNew ? 'text-muted-foreground' : 'text-success')} />
        <div className="text-sm text-foreground">
          {nothingNew ? (
            <Trans>Nothing new. Tome already had everything in this code.</Trans>
          ) : (
            <Trans>Landed from {deviceName}.</Trans>
          )}
          <p className="mt-1 text-xs text-muted-foreground">
            <Plural value={result.sessions_added} _0="No new sessions" one="# new session" other="# new sessions" />
            {result.sessions_known > 0 && <>{', '}<Plural value={result.sessions_known} one="# already known" other="# already known" /></>}
            {result.positions_applied > 0 && <>{', '}<Plural value={result.positions_applied} one="# position updated" other="# positions updated" /></>}
            {result.positions_kept > 0 && <>{', '}<Plural value={result.positions_kept} one="# position kept (newer here)" other="# positions kept (newer here)" /></>}
            {result.ratings_applied > 0 && <>{', '}<Plural value={result.ratings_applied} one="# rating" other="# ratings" /></>}
            {result.unknown_books > 0 && <>{', '}<Plural value={result.unknown_books} one="# book not in your library" other="# books not in your library" /></>}
          </p>
          {result.clock_offset_seconds !== 0 && (
            <p className="mt-1 text-xs text-muted-foreground">
              <Trans>The device clock is off by {offset}; times were corrected.</Trans>
            </p>
          )}
        </div>
      </div>

      {result.books.length > 0 && (
        <ul className="mt-4 max-h-80 space-y-2 overflow-y-auto pr-1">
          {result.books.map(b => {
            const before = pct(b.progress_before)
            const after = pct(b.progress_after)
            const rating = b.rating
            return (
            <li key={b.book_id} className="flex gap-3 rounded-lg border border-border bg-background/50 p-2.5">
              <div className="h-16 w-11 shrink-0 overflow-hidden rounded bg-muted">
                {b.has_cover && <img src={`/api/books/${b.book_id}/cover`} alt="" className="h-full w-full object-cover" />}
              </div>
              <div className="min-w-0 flex-1 text-xs">
                <p className="truncate text-sm font-medium text-foreground">{b.title}</p>
                <p className="truncate text-muted-foreground">
                  {b.series ? (b.series_index !== null ? `${b.series} #${b.series_index}` : b.series) : b.author}
                </p>
                <p className="mt-1 text-muted-foreground">
                  {b.sessions_added > 0 ? (
                    <>
                      <Plural value={b.sessions_added} one="# session" other="# sessions" />
                      {b.seconds > 0 && <>{' · '}{formatMinutes(b.seconds)}</>}
                      {b.pages > 0 && <>{' · '}<Plural value={b.pages} one="# page" other="# pages" /></>}
                    </>
                  ) : b.sessions_known > 0 ? (
                    <Trans>Sessions already known</Trans>
                  ) : null}
                </p>
                <p className="text-muted-foreground">
                  {b.progress_before !== b.progress_after
                    ? <Trans>Progress {before} → {after}</Trans>
                    : <Trans>Progress {after}</Trans>}
                  {b.position === 'kept' && <>{' · '}<Trans>position kept, newer here</Trans></>}
                  {b.status === 'read' && <>{' · '}<Trans>finished</Trans></>}
                  {rating !== null && <>{' · '}<Trans>rated {rating}</Trans></>}
                </p>
              </div>
            </li>
            )
          })}
        </ul>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onAgain} className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground">
          <Trans>Scan another</Trans>
        </button>
        <button onClick={onDone} className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90">
          <Trans>Done</Trans>
        </button>
      </div>
    </div>
  )
}
