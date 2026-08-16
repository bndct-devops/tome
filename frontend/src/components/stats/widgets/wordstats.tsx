// Phase 4 word-count stats widgets — words read, true WPM, book length.
// All all-time (range-independent), powered by Book.word_count + reconciled read-time.
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip,
} from 'recharts'
import { FileText } from 'lucide-react'
import { useChartColors } from '@/lib/useChartAccent'
import { ChartTooltip, type StatsResponse } from '@/components/stats/shared'

function Empty({ text = 'No word counts yet.' }: { text?: string }) {
  return <p className="py-10 text-center text-sm text-muted-foreground">{text}</p>
}

// Compact number: 1_240_000 -> "1.2M", 84_300 -> "84k", 932 -> "932".
function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`
  return String(n)
}

function Cover({ id, has, alt }: { id: number | null; has: boolean; alt: string }) {
  return (
    <div className="flex h-12 w-8 shrink-0 items-center justify-center overflow-hidden rounded bg-muted">
      {id && has ? (
        <img src={`/api/books/${id}/cover`} alt={alt} className="h-full w-full object-cover" loading="lazy"
          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }} />
      ) : (
        <FileText className="h-3.5 w-3.5 text-muted-foreground" />
      )}
    </div>
  )
}

// 1 ── Words Read: lifetime total + per-year bars (auto-fit, no fill-the-box chart)
export function WordsRead({ data }: { data: StatsResponse['words'] }) {
  const { accent } = useChartColors()
  if (!data || data.books_counted === 0) return <Empty />
  const years = data.by_year
  const maxYear = Math.max(...years.map((y) => y.words), 1)
  const avgPerBook = Math.round(data.total_words / data.books_counted)
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <span className="text-3xl font-bold leading-none text-foreground">{compact(data.total_words)}</span>
        <span className="text-xs text-muted-foreground">words read · {data.books_counted} book{data.books_counted !== 1 ? 's' : ''}</span>
      </div>
      {years.length >= 2 && (
        <div className="flex flex-col gap-1.5">
          {years.map((y) => (
            <div key={y.year}>
              <div className="mb-0.5 flex items-center justify-between text-xs">
                <span className="font-medium text-foreground">{y.year}</span>
                <span className="tabular-nums text-muted-foreground">{compact(y.words)}</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full" style={{ width: `${(y.words / maxYear) * 100}%`, backgroundColor: accent }} />
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="text-xs text-muted-foreground">avg <span className="font-medium text-foreground">{compact(avgPerBook)}</span> words per book</p>
    </div>
  )
}

function WpmRow({ b }: { b: StatsResponse['wpm']['books'][number] }) {
  return (
    <div className="flex items-center gap-2.5">
      <Cover id={b.book_id} has={b.has_cover} alt="" />
      <div className="min-w-0 flex-1">
        <a href={`/books/${b.book_id}`} className="line-clamp-1 text-sm font-medium text-foreground transition-colors hover:text-primary">{b.title}</a>
        {b.author && <p className="truncate text-xs text-muted-foreground">{b.author}</p>}
      </div>
      <span className="shrink-0 text-sm font-bold tabular-nums text-foreground">{b.wpm.toLocaleString()}<span className="ml-0.5 text-[10px] font-normal text-muted-foreground">wpm</span></span>
    </div>
  )
}

// 2 ── True WPM: words ÷ reconciled read-time, overall + fastest/slowest ─────────
// The fastest/slowest lists go side-by-side once the tile is wide enough
// (container query, not viewport) — so a wide tile fills with two columns
// instead of one tall list with dead space on the right.
export function TrueWpm({ data }: { data: StatsResponse['wpm'] }) {
  if (!data || data.books_counted === 0) return <Empty text="No timed reading with word counts yet." />
  const fast = data.books.slice(0, 3)
  const slow = data.books.length > 6 ? data.books.slice(-3).reverse() : []
  return (
    <div className="@container flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <span className="text-3xl font-bold leading-none text-foreground">{data.overall.toLocaleString()}</span>
        <span className="text-xs text-muted-foreground">words/min · across {data.books_counted} book{data.books_counted !== 1 ? 's' : ''}</span>
      </div>
      <div className="grid grid-cols-1 gap-x-8 gap-y-2.5 @md:grid-cols-2">
        <div className="flex flex-col gap-2.5">
          {fast.length > 0 && slow.length > 0 && <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Fastest</p>}
          {fast.map((b) => <WpmRow key={b.book_id} b={b} />)}
        </div>
        {slow.length > 0 && (
          <div className="flex flex-col gap-2.5">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Slowest</p>
            {slow.map((b) => <WpmRow key={b.book_id} b={b} />)}
          </div>
        )}
      </div>
    </div>
  )
}

// 4 ── Re-reads: books whose pages you've revisited on a later day ──────────────
export function ReReads({ data }: { data: StatsResponse['rereads'] }) {
  if (!data || data.books.length === 0) return <Empty text="No re-reads yet — pages you revisit on a later day show up here." />
  return (
    <div className="flex flex-col gap-2.5">
      {data.books.slice(0, 8).map((b) => (
        <div key={b.book_id} className="flex items-center gap-2.5">
          <Cover id={b.book_id} has={b.has_cover} alt="" />
          <div className="min-w-0 flex-1">
            <a href={`/books/${b.book_id}`} className="line-clamp-1 text-sm font-medium text-foreground transition-colors hover:text-primary">{b.title}</a>
            {b.author && <p className="truncate text-xs text-muted-foreground">{b.author}</p>}
          </div>
          <span className="shrink-0 text-right text-sm font-bold tabular-nums text-foreground">
            {b.reread_pages.toLocaleString()}
            <span className="ml-0.5 text-[10px] font-normal text-muted-foreground">pages{b.pct ? ` · ${b.pct}%` : ''}</span>
          </span>
        </div>
      ))}
    </div>
  )
}

// 3 ── Book Length: word-count distribution of finished books + avg/median ───────
export function BookLength({ data }: { data: StatsResponse['book_lengths'] }) {
  const { accent, tick, cursor } = useChartColors()
  if (!data || data.count === 0) return <Empty />
  return (
    <div className="flex h-full flex-col gap-2">
      <div className="min-h-0 flex-1">
        <ResponsiveContainer initialDimension={{ width: 1, height: 1 }} width="100%" height="100%">
          <BarChart data={data.buckets} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: tick }} axisLine={false} tickLine={false} />
            <YAxis allowDecimals={false} tick={{ fontSize: 9, fill: tick }} width={26} axisLine={false} tickLine={false} />
            <Tooltip
              cursor={cursor} wrapperStyle={{ outline: 'none' }} isAnimationActive={false}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const d = payload[0].payload
                return <ChartTooltip><div className="font-medium">{d.label} words</div><div className="text-muted-foreground">{d.count} book{d.count !== 1 ? 's' : ''}</div></ChartTooltip>
              }}
            />
            <Bar dataKey="count" fill={accent} radius={[4, 4, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="truncate text-[11px] text-muted-foreground">
        avg <span className="font-medium text-foreground">{compact(data.avg_words)}</span> · median {compact(data.median_words)}
        {data.longest && <> · longest <span className="text-foreground">{data.longest.title}</span> ({compact(data.longest.words)})</>}
      </p>
    </div>
  )
}
