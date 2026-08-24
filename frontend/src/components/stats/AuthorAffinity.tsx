import { ProgressRow } from './ProgressRow'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'

interface AuthorAffinityEntry {
  author: string
  seconds: number
  sessions: number
  book_count: number
  books_finished: number
}

function formatDuration(seconds: number): string {
  if (seconds === 0) return '0m'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h === 0) return `${m}m`
  return t`${h}h ${m}m`
}

export function AuthorAffinity({ data }: { data: AuthorAffinityEntry[] }) {
  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-4"><Trans>No author data.</Trans></p>
    )
  }

  const maxSeconds = Math.max(...data.map(a => a.seconds), 1)

  return (
    <div className="flex flex-col gap-3">
      {data.map(a => (
        <ProgressRow
          key={a.author}
          label={a.author}
          value={formatDuration(a.seconds)}
          pct={(a.seconds / maxSeconds) * 100}
          sub={(() => { const fin = a.books_finished; return plural(a.book_count, { one: `${fin} finished · # book read`, other: `${fin} finished · # books read` }) })()}
        />
      ))}
    </div>
  )
}
