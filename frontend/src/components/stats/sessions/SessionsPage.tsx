// The Sessions tab. Stub: today's session log until the designed page replaces it.
import { Trans } from '@lingui/react/macro'
import { SessionLog, SessionLogHint } from '@/components/stats/SessionLog'

export function SessionsPage() {
  return (
    <section>
      <div className="mb-3 flex items-center gap-1.5">
        <h2 className="font-display text-[24px] font-bold leading-none text-foreground"><Trans>Sessions</Trans></h2>
        <SessionLogHint />
      </div>
      <SessionLog />
    </section>
  )
}
