import { FileText } from 'lucide-react'
import { cn } from '@/lib/utils'

interface SeriesCompletion {
  series: string
  total: number
  read: number
  reading: number
  pct: number
  sample_book_id: number
}

export function SeriesCompletionGrid({ data }: { data: SeriesCompletion[] }) {
  if (!data || data.length === 0) {
    return (
      <div className="bg-card border border-border rounded-xl p-8 text-center text-muted-foreground text-sm">
        No series with reading history yet.
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {data.map(s => (
        <a
          key={s.series}
          href={`/?tab=series`}
          onClick={e => {
            e.preventDefault()
            window.location.href = `/?series=${encodeURIComponent(s.series)}&tab=books`
          }}
          className="group bg-card border border-border rounded-xl p-4 flex items-start gap-3 hover:bg-accent/30 transition-colors"
        >
          <div className="w-9 h-12 rounded bg-muted flex items-center justify-center shrink-0 overflow-hidden">
            {s.sample_book_id ? (
              <img
                src={`/api/books/${s.sample_book_id}/cover`}
                alt=""
                className="w-full h-full object-cover"
                loading="lazy"
                onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
              />
            ) : (
              <FileText className="w-4 h-4 text-muted-foreground" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-foreground truncate group-hover:text-primary transition-colors">
              {s.series}
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {s.read} of {s.total} read
              {s.reading > 0 && ` · ${s.reading} in progress`}
            </p>
            <div className="mt-2 flex items-center gap-2">
              <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className={cn(
                    'h-full rounded-full transition-all',
                    s.pct >= 100 ? 'bg-emerald-500' : 'bg-indigo-500',
                  )}
                  style={{ width: `${Math.min(s.pct, 100)}%` }}
                />
              </div>
              <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">
                {s.pct}%
              </span>
            </div>
          </div>
        </a>
      ))}
    </div>
  )
}
