// The label on every AI button and card, so nobody is surprised that a title
// or a passage left the server. Sparkles is the one icon for AI surfaces.
import { Sparkles } from 'lucide-react'
import { Trans, useLingui } from '@lingui/react/macro'
import { cn } from '@/lib/utils'

export function AiBadge({ className }: { className?: string }) {
  const { t } = useLingui()
  return (
    <span
      title={t`Uses your Anthropic key`}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-px',
        'text-[10px] font-medium uppercase tracking-wide text-muted-foreground',
        className,
      )}
    >
      <Sparkles className="h-3 w-3" aria-hidden="true" />
      <Trans>AI</Trans>
    </span>
  )
}

export default AiBadge
