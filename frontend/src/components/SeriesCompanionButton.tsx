import { useEffect, useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { t } from '@lingui/core/macro'
import { Pill } from '@/components/page'
import { useToast } from '@/contexts/ToastContext'
import { getCompanionOptIn, setCompanionOptIn, useAiStatus, type CompanionOptIn } from '@/lib/ai'

/**
 * The reading companion's switch on the series page. One row per series, so
 * this is the same switch the book page shows; `bookId` is the volume the
 * reader is in (or the first one), which the server uses as the start when
 * turning on. Renders nothing when the companion is not available to this
 * user or the switch has not loaded yet.
 */
export function SeriesCompanionButton({ bookId }: { bookId: number | null }) {
  const { toast } = useToast()
  const { available } = useAiStatus()
  const can = available('reading_digest')
  const [state, setState] = useState<CompanionOptIn | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setState(null)
    if (!can || bookId == null) return
    let live = true
    getCompanionOptIn(bookId).then(s => { if (live) setState(s) }).catch(() => {})
    return () => { live = false }
  }, [can, bookId])

  if (!can || bookId == null || state == null) return null
  const on = state.enabled

  const toggle = async () => {
    if (busy) return
    setBusy(true)
    try {
      const next = await setCompanionOptIn(bookId, !on)
      setState(next)
      if (next.enabled) toast.success(t`Tome reads along with this series from where you are`)
    } catch (e) {
      toast.error((e as Error).message ?? t`Could not change the companion setting`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Pill
      variant={on ? 'secondary' : 'quiet'}
      onClick={toggle}
      disabled={busy}
      aria-pressed={on}
      title={on
        ? t`Reading companion is on for this series: glossary, recaps and answers from the chapters you have read. Click to turn it off.`
        : t`Turn on the reading companion for this series. Nothing is read before you turn it on, and never past your position.`}
    >
      {busy
        ? <Loader2 className="w-4 h-4 animate-spin" />
        : <Sparkles className={on ? 'w-4 h-4 text-primary' : 'w-4 h-4'} />}
      {on ? t`Companion on` : t`Companion`}
    </Pill>
  )
}
