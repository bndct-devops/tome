// Marks a page count that a KOSync reader (CrossInk) did not count per
// session: Tome attributed it from the reader's lifetime page counter, which
// grew by that much between two syncs, shared out by reading time.
import { useLingui } from '@lingui/react/macro'
import { EqualApproximately } from 'lucide-react'

// ``includes``: the count it follows may also hold pages a source counted
// itself (a day's or a book's total), so the tooltip says "includes".
export function AttributedPagesMark({ includes = false }: { includes?: boolean }) {
  const { t } = useLingui()
  const label = includes
    ? t`Includes pages attributed from the reader's page counter: what it grew by between two syncs, shared out over those sessions by reading time`
    : t`Attributed from the reader's page counter: what it grew by between two syncs, shared out over those sessions by reading time`
  return (
    <span title={label} aria-label={label} className="inline-flex items-center align-middle">
      <EqualApproximately className="h-3 w-3 text-muted-foreground" aria-hidden />
    </span>
  )
}
