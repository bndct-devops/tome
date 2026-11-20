// The selected goal on the Goals tab, by kind:
//   day goals and reading days: this week large (with the legend), the last 12
//     weeks day by day, three figures (reached, best run, current run);
//   weekly goals: this week by day and the past weeks against the target;
//   month and year goals: the period so far with the pace and the projection,
//     past periods (with a note when finish dates were set in bulk), figures;
//   finish by a date: the volumes, what it takes a day against the recent pace,
//     the road to the date.
// Every block keeps a fixed height or its remembered one (shape.ts), so the
// skeleton drawn while the history is on its way has the loaded layout.
import type { ReactNode } from 'react'
import { t } from '@lingui/core/macro'
import { Trans } from '@lingui/react/macro'
import { cn, formatDuration } from '@/lib/utils'
import type { Goal, GoalHistory } from '@/lib/goals'
import { stripMarks } from '@/lib/goalsModel'
import { Cover } from '@/components/stats/kit/Kit'
import { CountUp } from '@/components/stats/kit/CountUp'
import { K, stagger, type Entry } from '@/components/stats/kit/motion'
import { tipAttrs } from '@/components/stats/kit/tip'
import { HAIR, num } from '@/components/stats/kit/tokens'
import { type Fig } from './figures'
import { BurnUp, MarkLegend, PeriodBars, PeriodLegend, Timeline, WeekBars, WeekGrid } from './charts'
import { Dot } from './GoalCard'
import { markTip } from './marks'
import { mediumDate, monthName, shortDate, weekdayShort } from './text'

function Sub({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-3">
      <span className="text-[14px] font-semibold text-foreground">{children}</span>
      {right}
    </div>
  )
}

/** Three figures on hairlines under the charts. */
export function Figures({ items, desktop, entry }: { items: Fig[]; desktop: boolean; entry: Entry }) {
  return (
    <div className={cn('grid grid-cols-3', !desktop && 'gap-3')}>
      {items.map((it, i) => (
        <div key={it.label} className={cn('min-w-0', desktop && (i > 0 ? cn('border-l px-6', HAIR) : 'pr-6'))}>
          <div className="text-[13px] text-muted-foreground">{it.label}</div>
          <div className={cn('mt-1.5 flex flex-wrap items-baseline gap-x-1.5 gap-y-1 font-display font-bold leading-none tabular-nums text-foreground', desktop ? 'text-[26px]' : 'text-[20px]')}>
            {it.text ?? <CountUp entry={entry} value={it.value} format={num} />}
            <span className={cn('font-sans font-semibold tracking-normal text-muted-foreground', desktop ? 'text-[14px]' : 'text-[12px]')}>{it.unit}</span>
          </div>
          <div className={cn('mt-1.5 leading-snug text-muted-foreground', desktop ? 'text-[13px]' : 'text-[12px]')}>{it.foot}</div>
        </div>
      ))}
    </div>
  )
}

/** "69 min" for a day's reading (seconds, when the server sends them) or the day's value. */
function dayValue(goal: Goal, value: number, seconds?: number): string {
  if (goal.metric === 'pages') { const p = num(Math.round(value)); return t`${p} pages` }
  const m = num(seconds != null ? dayMinutes(seconds) : Math.round(value))
  return t`${m} min`
}

// ── Day goals and reading days ────────────────────────────────────────────────

export function DaysTop({ goal, history, desktop, entry }: { goal: Goal; history: GoalHistory; desktop: boolean; entry: Entry }) {
  const marks = stripMarks(goal.week ?? [], goal.target, goal.metric)
  const cell = desktop ? 15 : 12
  const gap = desktop ? 5 : 4
  void entry
  return (
    <div className={cn(desktop ? 'grid grid-cols-[minmax(0,1fr)_auto] gap-14' : 'flex flex-col gap-7')}>
      <div>
        <Sub>{t`This week`}</Sub>
        <div className={cn('flex', desktop ? 'gap-6' : 'justify-between')}>
          {marks.map((m, i) => {
            const w = goal.week![i]
            return (
              <div key={m.date} className="k-dot flex flex-col items-center gap-1.5 rounded-[6px]" {...tipAttrs(markTip(goal, m))}>
                <Dot m={m} size={desktop ? 30 : 26} className={K.fade} style={stagger(i, 7)} />
                <span className={cn('text-[12px]', m.state === 'today' ? 'font-bold text-foreground' : 'text-muted-foreground')}>{weekdayShort(m.date)}</span>
                <span className="h-[16px] whitespace-nowrap text-[12px] tabular-nums text-foreground">{m.state === 'future' ? '' : dayValue(goal, w.value, w.seconds)}</span>
              </div>
            )
          })}
        </div>
        <MarkLegend />
      </div>
      <div>
        <Sub>{t`Last 12 weeks`}</Sub>
        <WeekGrid goal={goal} history={history} cell={cell} gap={gap} />
      </div>
    </div>
  )
}

// ── Weekly goals ──────────────────────────────────────────────────────────────

export function WeekTop({ goal, history, desktop }: { goal: Goal; history: GoalHistory; desktop: boolean }) {
  return (
    <div className={cn(desktop ? 'grid grid-cols-2 gap-12' : 'flex flex-col gap-7')}>
      <div>
        <Sub>{t`This week`}</Sub>
        <WeekBars goal={goal} history={history} h={desktop ? 170 : 160} />
      </div>
      <div>
        <Sub right={<PeriodLegend books={false} />}>{t`Last 12 weeks`}</Sub>
        <PeriodBars goal={goal} history={history} h={desktop ? 150 : 120} />
      </div>
    </div>
  )
}

// ── Month and year goals ──────────────────────────────────────────────────────

/** "Finish dates set on one day: 15 of May's 15 on May 23, … These bars show when books were marked read, not when they were read." */
export function BulkNote({ goal, history }: { goal: Goal; history: GoalHistory }) {
  const bulk = history.summary?.bulk ?? []
  if (!bulk.length || goal.metric !== 'books') return null
  const parts = bulk.map((b) => {
    const c = num(b.count), o = num(b.of), d = shortDate(b.day, history.today)
    const p = goal.period === 'year' ? b.start.slice(0, 4) : monthName(b.start)
    return t`${c} of ${p}'s ${o} on ${d}`
  })
  const list = parts.join(', ')
  return <p className="mt-2 text-[12px] leading-snug text-muted-foreground"><Trans>Finish dates set on one day: {list}. These bars show when books were marked read, not when they were read.</Trans></p>
}

export function PeriodTop({ goal, history, desktop }: { goal: Goal; history: GoalHistory; desktop: boolean }) {
  const year = goal.period === 'year'
  return (
    <div className={cn(desktop ? 'grid grid-cols-2 gap-12' : 'flex flex-col gap-7')}>
      <div>
        <Sub>{year ? t`This year` : t`This month`}</Sub>
        <BurnUp goal={goal} history={history} h={150} />
      </div>
      <div>
        <Sub right={<PeriodLegend books={goal.metric === 'books'} />}>{year ? (() => { const n = num(history.periods.length); return t`Last ${n} years` })() : t`Last 12 months`}</Sub>
        <PeriodBars goal={goal} history={history} h={desktop ? 150 : 120} />
        <BulkNote goal={goal} history={history} />
      </div>
    </div>
  )
}

// ── Finish by a date ──────────────────────────────────────────────────────────

function perDay(secs: number | null): string {
  if (secs == null) return '–'
  return num(Math.max(1, Math.round(secs / 60)))
}

export function FinishTop({ goal, desktop }: { goal: Goal; desktop: boolean }) {
  const f = goal.finish
  if (!f) return null
  const total = f.volumes_total
  const progress = new Map(f.in_progress.map((p) => [p.book_id, p.progress]))
  const blocks = Array.from({ length: total }, (_, i) => (i < f.volumes_read ? 1 : i === f.volumes_read && f.in_progress.length ? (f.in_progress[0].progress ?? 0) : 0))
  void progress
  const need = f.needed_seconds_per_day
  const recent = f.recent_seconds_per_day
  const max = Math.max(need ?? 0, recent, 60)
  const done = goal.status === 'completed'
  const started = f.in_progress[0]
  return (
    <div className={cn(desktop ? 'grid grid-cols-2 gap-12' : 'flex flex-col gap-7')}>
      <div>
        <Sub>{t`Volumes`}</Sub>
        <div className="flex items-center gap-4">
          <Cover id={f.next_book_id ?? goal.target_book_id ?? null} h={72} className="rounded-[6px]" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap gap-[5px]" role="img" aria-label={(() => { const r = num(f.volumes_read), n = num(total); return t`${r} of ${n} read` })()}>
              {blocks.map((p, i) => (
                <span
                  key={i}
                  className="k-dot relative block h-[22px] w-[16px] overflow-hidden rounded-[4px]"
                  style={{ boxShadow: p >= 1 ? undefined : 'inset 0 0 0 1px color-mix(in oklch, var(--foreground) 30%, var(--background))' }}
                  {...tipAttrs({ title: (() => { const v = num(i + 1); return t`Volume ${v}` })(), value: p >= 1 ? t`Read` : p > 0 ? (() => { const pc = num(Math.round(p * 100)); return t`${pc}% read` })() : t`Not started` })}
                >
                  {p > 0 && <span className={cn('k-grow absolute inset-x-0 bottom-0 block', p >= 1 ? 'bg-success' : 'bg-primary')} style={{ height: `${Math.round(p * 100)}%`, ...stagger(i, total) }} />}
                </span>
              ))}
            </div>
            <div className="mt-2 text-[14px] text-foreground">
              {(() => { const r = num(f.volumes_read), n = num(total); return t`${r} of ${n} read` })()}
              {started && <span className="text-muted-foreground">{' · '}{(() => { const v = started.series_index != null ? num(started.series_index) : started.title; const pc = num(Math.round(started.progress * 100)); return t`volume ${v} at ${pc}%` })()}</span>}
            </div>
          </div>
        </div>
        <div className="mt-6"><Timeline goal={goal} /></div>
      </div>
      <div>
        <Sub>{t`What it takes`}</Sub>
        {done ? (
          <p className="text-[14px] text-foreground">{(() => { const d = goal.completed_at ? mediumDate(goal.completed_at.slice(0, 10)) : ''; return t`Finished on ${d}.` })()}</p>
        ) : (
          <div className="flex flex-col gap-3">
            {[
              { k: 'need', label: t`Needs a day`, secs: need, cls: 'bg-primary' },
              { k: 'recent', label: (() => { const n = num(f.recent_days); return t`You read a day (last ${n} days)` })(), secs: recent, cls: need != null && recent >= need ? 'bg-success' : 'bg-warning' },
            ].map((r, i) => (
              <div key={r.k}>
                <div className="flex items-baseline justify-between gap-3 text-[13px]">
                  <span className="text-muted-foreground">{r.label}</span>
                  <span className="font-semibold tabular-nums text-foreground">{(() => { const m = perDay(r.secs); return t`${m} min` })()}</span>
                </div>
                <div className="k-track mt-1.5 h-[6px] overflow-hidden rounded-full" style={{ background: 'color-mix(in oklch, var(--foreground) 10%, var(--background))' }} {...tipAttrs({ title: r.label, value: (() => { const m = perDay(r.secs); return t`${m} min` })() })}>
                  <div className={cn('k-grow-x h-full rounded-full', r.cls)} style={{ width: `${r.secs == null ? 0 : Math.min(100, (r.secs / max) * 100)}%`, ...stagger(i, 2) }} />
                </div>
              </div>
            ))}
            <p className="text-[12px] leading-snug text-muted-foreground">
              {f.remaining_seconds == null
                ? t`Some volumes have no estimate yet (no word count and no finished volume to go by).`
                : f.basis === 'series' && f.per_volume_seconds
                  ? (() => { const v = formatDuration(f.per_volume_seconds); return t`At your ${v} a volume in this series.` })()
                  : t`Estimated from the word count at your reading speed.`}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}


function dayMinutes(secs: number) { return Math.round(secs / 60) }
