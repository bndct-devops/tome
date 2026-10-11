// "Read N chapters first?": asked before a digest job over CONFIRM_THRESHOLD
// chapters. Two cards, this book only and the whole series (when earlier read
// volumes exist), each with its chapters, time and cost estimate. The book
// page itself never shows money; this dialog is where the price is decided.
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { BookOpen, Check, Library as LibraryIcon, Loader2, X } from 'lucide-react'
import { Plural, Trans, useLingui } from '@lingui/react/macro'
import { plural } from '@lingui/core/macro'
import { ModalShell } from '@/components/ModalShell'
import { AiBadge } from '@/components/AiBadge'
import { cn } from '@/lib/utils'
import type { DigestEstimate, DigestScope } from '@/lib/ai'

export interface DigestConfirmProps {
  open: boolean
  book: DigestEstimate | null
  series: DigestEstimate | null
  /** Preselect this scope when it has chapters to read and is within budget. */
  scope?: DigestScope
  busy?: boolean
  error?: string | null
  onCancel: () => void
  onStart: (scope: DigestScope) => void
}

/** The scope the dialog opens on: the preferred one when it is usable, else
 *  this book, unless it has nothing left to read or is over budget while the
 *  series is not. */
function initialScope(
  book: DigestEstimate | null, series: DigestEstimate | null, preferred?: DigestScope,
): DigestScope {
  const pick = preferred === 'series' ? series : preferred === 'book' ? book : null
  if (preferred && pick != null && pick.chapters > 0 && !pick.over_budget) return preferred
  const bookOk = book != null && book.chapters > 0 && !book.over_budget
  if (bookOk || series == null || series.over_budget) return 'book'
  return 'series'
}

export function DigestConfirmDialog({ open, book, series, scope: preferredScope, busy = false, error, onCancel, onStart }: DigestConfirmProps) {
  const { t, i18n } = useLingui()
  const [scope, setScope] = useState<DigestScope>(() => initialScope(book, series, preferredScope))
  // Re-pick the scope whenever the dialog opens on new estimates.
  const [shownFor, setShownFor] = useState<{ book: DigestEstimate | null; series: DigestEstimate | null } | null>(null)
  if (open && (shownFor?.book !== book || shownFor?.series !== series)) {
    setShownFor({ book, series })
    setScope(initialScope(book, series, preferredScope))
  }

  const panelRef = useRef<HTMLDivElement>(null)
  const startRef = useRef<HTMLButtonElement>(null)
  // The key handler reads these through refs so it is attached once per open
  // (re-attaching on every parent render would steal focus back).
  const cancelRef = useRef(onCancel)
  const busyRef = useRef(busy)
  useEffect(() => {
    cancelRef.current = onCancel
    busyRef.current = busy
  })

  // Escape closes; Tab stays inside; the page's own shortcuts (Esc = back,
  // r = read, e = edit) do not fire while the dialog is open.
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const raf = window.requestAnimationFrame(() => startRef.current?.focus())
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        if (!busyRef.current) cancelRef.current()
        return
      }
      if (e.key === 'Tab' && panelRef.current) {
        const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])'))
        if (items.length === 0) return
        const first = items[0]
        const last = items[items.length - 1]
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
        return
      }
      e.stopPropagation()
    }
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.cancelAnimationFrame(raf)
      window.removeEventListener('keydown', onKey, true)
      previous?.focus?.()
    }
  }, [open])

  const money = (n: number) => {
    const fmt = (v: number) => new Intl.NumberFormat(i18n.locale, {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
    }).format(v)
    if (n > 0 && n < 0.01) { const min = fmt(0.01); return t`under ${min}` }
    return fmt(n)
  }

  const duration = (secs: number) => {
    if (secs < 60) return t`under a minute`
    const minutes = Math.round(secs / 60)
    if (minutes < 90) return plural(minutes, { one: 'about # minute', other: 'about # minutes' })
    const hours = Math.round(secs / 360) / 10
    const h = hours.toLocaleString(i18n.locale)
    return t`about ${h} hours`
  }

  const chosen = scope === 'series' && series ? series : book
  const n = chosen?.chapters ?? 0
  const remaining = (series ?? book)?.remaining_budget_usd ?? null
  const canStart = chosen != null && chosen.chapters > 0 && !chosen.over_budget && !busy

  function card(est: DigestEstimate, which: DigestScope) {
    const selected = scope === which
    const disabled = est.over_budget || est.chapters === 0
    const cost = money(est.est_cost_usd)
    const time = duration(est.est_seconds)
    const chapters = est.chapters
    return (
      <button
        type="button"
        role="radio"
        aria-checked={selected}
        disabled={disabled}
        onClick={() => setScope(which)}
        className={cn(
          'relative w-full flex flex-col items-stretch justify-start text-left rounded-xl border px-4 py-3 transition-colors',
          selected && !disabled ? 'border-primary bg-primary/5 ring-1 ring-primary' : 'border-border bg-card hover:bg-muted/60',
          disabled && 'opacity-60 cursor-not-allowed hover:bg-card',
        )}
      >
        <span className="flex items-center gap-2 text-sm font-medium text-foreground">
          {which === 'book'
            ? <BookOpen className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
            : <LibraryIcon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
          {which === 'book' ? <Trans>This book only</Trans> : <Trans>Whole series</Trans>}
          {selected && !disabled && <Check className="ml-auto w-3.5 h-3.5 text-primary shrink-0" />}
        </span>
        <span className="mt-1.5 block text-xs text-muted-foreground tabular-nums">
          <Plural value={chapters} one="# chapter" other="# chapters" /> · {time}
        </span>
        <span className="mt-0.5 block text-sm text-foreground tabular-nums">
          <Trans>about {cost}</Trans>
        </span>
        {est.chapters === 0 ? (
          <span className="mt-2 block text-xs text-muted-foreground">
            <Trans>Every chapter of this book before your position is read already.</Trans>
          </span>
        ) : est.over_budget ? (
          <span className="mt-2 block text-xs text-warning">
            <Trans>More than what is left of the monthly AI budget.</Trans>
          </span>
        ) : null}
      </button>
    )
  }

  const remainingText = remaining != null ? money(remaining) : ''

  return createPortal(
    <ModalShell open={open} onClose={busy ? undefined : onCancel} className="w-full max-w-lg">
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="digest-confirm-title"
        className="flex max-h-[90vh] flex-col rounded-2xl border border-border bg-background shadow-xl"
      >
        <div className="flex items-center gap-2 border-b border-border px-5 py-3.5">
          <h2 id="digest-confirm-title" className="font-display text-base text-foreground min-w-0">
            <Plural value={n} one="Read # chapter first?" other="Read # chapters first?" />
          </h2>
          <AiBadge />
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            aria-label={t`Close`}
            className="ml-auto rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="overflow-y-auto px-5 py-4 space-y-4">
          <p className="text-sm text-muted-foreground">
            <Trans>The recap and the glossary are built from short notes on each chapter before your position. These chapters have no notes yet, so they are read once now and reused afterwards.</Trans>
          </p>

          <div role="radiogroup" aria-label={t`What to read`} className={cn('grid gap-3', series && 'sm:grid-cols-2')}>
            {book && card(book, 'book')}
            {series && card(series, 'series')}
          </div>

          {chosen && chosen.volumes.length > 1 && (
            <ul className="rounded-lg border border-border divide-y divide-border/60 text-xs" aria-label={t`Volumes`}>
              {chosen.volumes.map(v => {
                const vc = v.chapters
                return (
                  <li key={v.book_id} className="flex items-baseline justify-between gap-3 px-3 py-1.5 text-muted-foreground">
                    <span className="min-w-0 break-words text-foreground">{v.title}</span>
                    <span className="shrink-0 tabular-nums"><Plural value={vc} one="# chapter" other="# chapters" /></span>
                  </li>
                )
              })}
            </ul>
          )}

          {remaining != null && (
            <p className="text-xs text-muted-foreground">
              <Trans>{remainingText} left of your monthly AI budget.</Trans>
            </p>
          )}
          {chosen && (() => { const model = chosen.model; return (
            <p className="text-[11px] text-muted-foreground/70">
              <Trans>Estimated for {model} from list prices and earlier runs on this server. Stop it any time; the chapters read so far are kept.</Trans>
            </p>
          ) })()}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="px-3 py-2 rounded-md border border-border text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
          >
            <Trans>Not now</Trans>
          </button>
          <button
            ref={startRef}
            type="button"
            onClick={() => { if (canStart) onStart(scope) }}
            disabled={!canStart}
            className="px-3 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity inline-flex items-center gap-2"
          >
            {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            <Plural value={n} one="Read # chapter" other="Read # chapters" />
          </button>
        </div>
      </div>
    </ModalShell>,
    document.body,
  )
}

export default DigestConfirmDialog
