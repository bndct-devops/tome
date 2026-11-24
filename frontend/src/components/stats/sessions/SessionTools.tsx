// The trim and delete buttons of a session row and the trim panel under it,
// shared by the session log and the Sessions tab.
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { Trash2, Scissors } from 'lucide-react'
import { cn, formatDuration } from '@/lib/utils'
import { InfoHint } from '@/components/InfoHint'
import { Input, Pill, PRESS } from '@/components/page'
import { Unfold } from '@/components/motion'
import type { SessionEntry } from '@/components/stats/shared'
import type { SessionActions } from './actions'

/**
 * Scissors (live sessions only) and the bin. With `keepSlot`, an imported row
 * keeps an empty space where the scissors would be, so the bins line up.
 */
// eslint-disable-next-line lingui/no-unlocalized-strings -- default size classes
export function SessionTools({ s, a, keepSlot = false, className, buttonClass = 'h-8 w-8' }: { s: SessionEntry; a: SessionActions; keepSlot?: boolean; className?: string; buttonClass?: string }) {
  return (
    <>
      {s.kind !== 'imported' ? (
        <button
          type="button"
          onClick={() => a.toggleTrim(s)}
          aria-expanded={a.trimId === s.id}
          className={cn('touch-feedback grid place-items-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground', PRESS, buttonClass, className)}
          title={t`Trim session`}
          aria-label={t`Trim session`}
        >
          <Scissors className="h-3.5 w-3.5" />
        </button>
      ) : keepSlot ? (
        <span className={cn('block', buttonClass)} aria-hidden="true" />
      ) : null}
      <button
        type="button"
        onClick={() => a.remove(s)}
        disabled={a.deleting.has(s.id)}
        className={cn('touch-feedback grid place-items-center rounded-full text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-40', PRESS, buttonClass, className)}
        title={s.kind === 'imported' ? t`Delete this imported sitting` : t`Delete session`}
        aria-label={s.kind === 'imported' ? t`Delete this imported sitting` : t`Delete session`}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </>
  )
}

/** The new-duration form under a row whose scissors are open: unfolds in place, folds away on Cancel or Trim. */
export function TrimPanel({ s, a, className }: { s: SessionEntry; a: SessionActions; className?: string }) {
  return (
    <Unfold open={a.trimId === s.id}>
    <div className={cn('flex flex-wrap items-center gap-2 pb-3 text-[14px]', className)}>
      <span className="text-muted-foreground"><Trans>New duration</Trans></span>
      <InfoHint text={t`Trimming only shortens this session's length and end time. Page turns, progress and every other session stay untouched, and the previous duration is kept in the audit log. The suggestion re-prices this session's page turns at your usual reading pace.`} />
      <Input
        type="number"
        min={1}
        value={a.trimMinutes}
        onChange={(e) => a.setTrimMinutes(e.target.value)}
        aria-label={t`New duration in minutes`}
        className="w-20 text-right tabular-nums"
      />
      <span className="text-muted-foreground"><Trans>min</Trans></span>
      {s.suggested_seconds != null && (
        <button
          type="button"
          onClick={() => a.setTrimMinutes(String(Math.max(1, Math.round(s.suggested_seconds! / 60))))}
          className="text-[13px] text-primary transition-colors hover:underline underline-offset-4"
          title={t`Your page turns at your usual reading pace`}
        >
          {(() => { const d = formatDuration(s.suggested_seconds); return t`Suggested: ${d}` })()}
        </button>
      )}
      <div className="ml-auto flex gap-1.5">
        <Pill variant="quiet" onClick={a.closeTrim}><Trans>Cancel</Trans></Pill>
        <Pill variant="primary" onClick={() => a.applyTrim(s)} disabled={a.trimBusy}>
          <Trans>Trim</Trans>
        </Pill>
      </div>
    </div>
    </Unfold>
  )
}
