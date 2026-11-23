// Stats › Goals: every goal as a card ("Now"), the selected one in detail below
// (selection remembered in this browser), edit and delete on the detail, a new
// goal from the heading; without goals, a first goal from the reader's own
// suggested targets. Not range-bound: the header reads "This week, month and year".
//
// Loading: a second visit draws the cards and the selected goal's detail as a
// skeleton at their remembered heights (shape.ts); a browser's first visit draws
// the kit's Pending placeholder until the goals are in, and the detail's until
// its history is in, so nothing drawn moves.
import { useEffect, useState } from 'react'
import { AnimatePresence } from 'motion/react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { cn } from '@/lib/utils'
import { Pill } from '@/components/page'
import { historyUrl, rememberedGoalCount, suggestionsUrl, useGoals, type Goal, type GoalHistory, type GoalKind, type Suggestions } from '@/lib/goals'
import { Heading, Pending } from '@/components/stats/kit/Kit'
import { ONCE, useEntryRef } from '@/components/stats/kit/motion'
import { tipAttrs } from '@/components/stats/kit/tip'
import { DESKTOP_MIN, HAIR, num, useWidth } from '@/components/stats/kit/tokens'
import { useStatsGet } from '@/components/stats/kit/useStatsGet'
import { GoalGrid } from './GoalCard'
import { gridCols } from './marks'
import { DaysTop, FinishTop, Figures, PeriodTop, WeekTop } from './Detail'
import { daysFigures, detailKind, finishFigures, periodFigures, type DetailKind } from './figures'
import { GoalDialog, type DialogMode } from './GoalDialog'
import { saveSelection, savedSelection, useDeleteGoal } from './actions'
import { shapeKnown, useShape } from './shape'
import { goalName, kindLabel, shortDate, targetUnit } from './text'

const rowNames = (n: number, cols: number) => Array.from({ length: Math.ceil(n / cols) }, (_, i) => `row${i}`)
const detailKey = (id: number, desktop: boolean) => `detail:${id}:${desktop ? 'd' : 'p'}`

export function GoalsTab() {
  const [ref, width] = useWidth<HTMLDivElement>()
  const desktop = width >= DESKTOP_MIN
  const { data, rev } = useGoals()
  const goals = data?.goals ?? null
  const [remembered] = useState(rememberedGoalCount)
  const [saved] = useState(savedSelection)
  const [sel, setSel] = useState<number | null>(saved?.id ?? null)
  const [dialog, setDialog] = useState<DialogMode | null>(null)
  const selected = goals ? (goals.find((g) => g.id === sel) ?? goals[0] ?? null) : null
  useEffect(() => { if (selected) saveSelection(selected) }, [selected])

  const cols = gridCols(desktop)
  const cardsKey = 'goals'
  // What a skeleton can draw: the cards when their rows are remembered for this count and width.
  const cardsKnown = remembered != null && remembered > 0 && shapeKnown(`${cardsKey}:${cols}:${remembered}`, rowNames(remembered, cols))
  const [e, attach] = useEntryRef('goals/cards', goals ? ONCE : null)

  let body
  if (width === 0) body = null
  else if (!goals && !cardsKnown) body = <Pending />
  else if (goals && goals.length === 0) body = <FirstGoal desktop={desktop} onPick={setDialog} />
  else {
    body = (
      <>
        <section ref={attach} data-box="goals-now" aria-busy={!goals}>
          <Heading
            right
            note={
              <button
                type="button"
                disabled={!goals}
                onClick={() => setDialog({ kind: 'create' })}
                className="inline-flex items-center gap-1 text-[15px] font-medium text-primary underline-offset-4 hover:underline disabled:opacity-0"
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
                <Trans>New goal</Trans>
              </button>
            }
          >
            <Trans>Now</Trans>
          </Heading>
          <GoalGrid
            goals={goals}
            count={remembered ?? 0}
            desktop={desktop}
            entry={e}
            shapeKey={cardsKey}
            pick={(g) => setSel(g.id)}
            selected={selected?.id ?? null}
            today={data?.today}
            label={t`Your reading goals`}
          />
        </section>
        <GoalDetail
          key={selected?.id ?? saved?.id ?? 'none'}
          goal={selected}
          skeleton={!goals && saved ? saved : null}
          desktop={desktop}
          rev={rev}
          today={data?.today ?? ''}
          onEdit={(g) => setDialog({ kind: 'edit', goal: g })}
        />
      </>
    )
  }
  return (
    <div ref={ref} className={cn('flex flex-col', desktop ? 'gap-10' : 'gap-8')}>
      {body}
      <AnimatePresence>{dialog && goals && <GoalDialog key="goal" mode={dialog} goals={goals} onClose={() => setDialog(null)} onSaved={(g) => { setSel(g.id) }} />}</AnimatePresence>
    </div>
  )
}

/** "the selected goal · 30 min to go today": what the selected goal still takes, beside its heading. */
function takes(g: Goal, today: string): string {
  if (g.kind === 'finish_by') {
    if (g.status === 'completed') return t`finished`
    const n = num(g.to_go), d = shortDate(g.due_date ?? g.period_end, today)
    return t`${n} to go by ${d}`
  }
  if (g.reached) return t`reached`
  if (g.period === 'day') {
    const n = num(Math.ceil(g.to_go))
    return g.metric === 'minutes' ? t`${n} min to go today` : t`${n} pages to go today`
  }
  const n = num(Math.ceil(g.to_go)), d = shortDate(g.period_end, today)
  return t`${n} to go by ${d}`
}

function GoalDetail({ goal, skeleton, desktop, rev, today, onEdit }: {
  goal: Goal | null
  /** The remembered selection while the goals are on their way. */
  skeleton: { id: number; kind: string } | null
  desktop: boolean
  rev: number
  today: string
  onEdit: (g: Goal) => void
}) {
  const id = goal?.id ?? skeleton?.id ?? 0
  const kind: DetailKind | null = goal ? detailKind(goal) : skeleton ? detailKind({ kind: skeleton.kind as GoalKind, period: skeleton.kind.endsWith('_day') ? 'day' : skeleton.kind.endsWith('_week') ? 'week' : 'month' }) : null
  const wantsHistory = !!goal && kind !== 'finish'
  const { data: history } = useStatsGet<GoalHistory>(wantsHistory ? historyUrl(goal!.id, rev) : null)
  const ready = !!goal && (kind === 'finish' || !!history)
  const shape = useShape(detailKey(id, desktop))
  const known = shape.has(['head', 'top', 'figs'])
  const [e, attach] = useEntryRef(`goals/detail/${id}`, ready ? ONCE : null)
  const del = useDeleteGoal()
  if (!kind) return null
  // The detail's size is known only once drawn: without a remembered one, wait quietly (the page's last element).
  if (!ready && !known) return <Pending />

  const name = goal ? goalName(goal, today) : ''
  let top = null, figs = null
  if (ready && goal) {
    if (kind === 'days') { top = <DaysTop goal={goal} history={history!} desktop={desktop} entry={e} />; figs = daysFigures(goal, history!) }
    else if (kind === 'week') { top = <WeekTop goal={goal} history={history!} desktop={desktop} />; figs = periodFigures(goal, history!) }
    else if (kind === 'period') { top = <PeriodTop goal={goal} history={history!} desktop={desktop} />; figs = periodFigures(goal, history!) }
    else { top = <FinishTop goal={goal} desktop={desktop} />; figs = finishFigures(goal, today) }
  }
  return (
    <section ref={attach} data-box="goal-detail" aria-busy={!ready} aria-label={name || undefined}>
      {/* The kit's heading look (Heading), with a title that may be long (a series): one line with an
          ellipsis on a desktop, wrapping on the phone, never under the actions. */}
      <div ref={ready ? shape.measure('head') : undefined} style={!ready ? { height: shape.h('head') } : undefined} className={cn('mb-5 flex justify-between gap-4', desktop ? 'items-baseline' : 'items-start')}>
        {/* A fixed line box (24 px on a desktop, 30 px a line on the phone), whatever the font and
            whether the title is in yet: the placeholder sits top-aligned in that line (so it neither
            grows the line nor changes the heading's baseline), not middle-aligned on the font's metrics. */}
        <h2 className={cn('min-w-0 font-display text-[24px] font-bold text-foreground', desktop ? 'h-6 truncate leading-6' : 'min-h-[30px] leading-[30px] [overflow-wrap:anywhere]')} {...(goal && desktop ? tipAttrs({ title: name }) : {})}>
          {goal ? name : <span className={cn('relative inline-block h-6 w-56 rounded-[8px] bg-muted align-top', !desktop && 'top-[3px]')} aria-hidden="true" />}
        </h2>
        {goal ? (
          // The phone's buttons (no text to share a baseline with) sit centred on the title's first
          // 30 px line, whatever the font: a baseline row moved the title when the font came in.
          <span className={cn('flex shrink-0 items-center gap-1 text-[14px] text-muted-foreground', !desktop && '-mt-[3px]')}>
            {desktop && <span className="mr-2 whitespace-nowrap">{t`the selected goal`} · {takes(goal, today)}</span>}
            {goal.kind !== 'finish_by' || goal.status !== 'completed' ? (
              <Pill variant="quiet" icon onClick={() => onEdit(goal)} aria-label={t`Edit goal`} {...tipAttrs({ title: t`Edit goal` })}><Pencil className="h-4 w-4" /></Pill>
            ) : null}
            <Pill variant="destructive" icon onClick={() => del(goal)} aria-label={t`Delete this goal`} {...tipAttrs({ title: t`Delete this goal` })}><Trash2 className="h-4 w-4" /></Pill>
          </span>
        ) : (
          // Until the goal is in: the same row end, unseen, so the title sits on the baseline it will keep.
          <span className={cn('invisible flex shrink-0 items-center gap-1 text-[14px]', !desktop && '-mt-[3px]')} aria-hidden="true">
            {desktop && <span className="mr-2 whitespace-nowrap">{'\u00a0'}</span>}
            <span className="inline-flex h-9 w-9" />
          </span>
        )}
      </div>
      <div ref={ready ? shape.measure('top') : undefined} style={!ready ? { height: shape.h('top') } : undefined}>
        {top ?? <div className="h-full rounded-[12px] bg-muted/40" aria-hidden="true" />}
      </div>
      <div data-box="goal-detail-figs" className={cn('mt-5 border-t pt-4', HAIR)}>
        <div ref={ready ? shape.measure('figs') : undefined} style={!ready ? { height: shape.h('figs') } : undefined}>
          {figs ? <Figures items={figs} desktop={desktop} entry={e} /> : <div className="h-full rounded-[10px] bg-muted/30" aria-hidden="true" />}
        </div>
      </div>
    </section>
  )
}

/** No goals yet: a first one from the reader's own reading (or the presets without history). */
function FirstGoal({ desktop, onPick }: { desktop: boolean; onPick: (m: DialogMode) => void }) {
  const { data: sug } = useStatsGet<Suggestions>(suggestionsUrl())
  const tiles: { kind: GoalKind; value: number | null; note: string }[] = (['minutes_per_day', 'books_per_month', 'days_per_week'] as GoalKind[]).map((kind) => {
    const k = sug?.kinds[kind]
    const chip = k?.suggested?.[1] ?? k?.presets?.[1] ?? null
    let note = ''
    if (chip?.reached != null && chip.of) {
      const r = num(chip.reached), o = num(chip.of)
      note = k?.over === 'weeks' ? t`reached in ${r} of ${o} weeks` : t`your pace on ${r} of ${o} days`
    }
    return { kind, value: chip?.value ?? null, note: k?.suggested ? note : t`a start` }
  })
  return (
    <section data-box="goals-first">
      <Heading><Trans>Set your first reading goal</Trans></Heading>
      <p className="-mt-2 mb-5 max-w-[640px] text-[15px] leading-relaxed text-muted-foreground">
        <Trans>Progress counts by itself, from the reading Tome records. Start from what you already read:</Trans>
      </p>
      <div className={cn('grid gap-2', desktop ? 'grid-cols-4' : 'grid-cols-1')}>
        {tiles.map((tile) => (
          <button
            key={tile.kind}
            type="button"
            onClick={() => onPick({ kind: 'create', preset: { kind: tile.kind, target: tile.value ?? undefined } })}
            className="rounded-xl bg-secondary/55 px-4 py-3 text-left transition-colors hover:bg-secondary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <div className="text-[14px] font-semibold text-foreground">{kindLabel(tile.kind)}</div>
            <div className="mt-1 text-[22px] font-bold tabular-nums text-foreground">
              {tile.value != null ? num(tile.value) : <span className="inline-block h-[22px] w-10 rounded-[6px] bg-muted align-middle" />}
              <span className="ml-1.5 text-[13px] font-semibold text-muted-foreground">{targetUnit(tile.kind, tile.value ?? 2)}</span>
            </div>
            <div className="mt-0.5 h-[18px] truncate text-[12px] text-muted-foreground">{sug ? tile.note : ''}</div>
          </button>
        ))}
        <button
          type="button"
          onClick={() => onPick({ kind: 'create', preset: { kind: 'finish_by' } })}
          className="rounded-xl bg-secondary/55 px-4 py-3 text-left transition-colors hover:bg-secondary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <div className="text-[14px] font-semibold text-foreground">{kindLabel('finish_by')}</div>
          <div className="mt-1 text-[15px] leading-snug text-muted-foreground"><Trans>Finish a book or a series by a day you pick</Trans></div>
        </button>
      </div>
      <div className="mt-4">
        <Pill variant="primary" onClick={() => onPick({ kind: 'create' })}><Plus className="h-4 w-4" aria-hidden="true" /><Trans>New goal</Trans></Pill>
      </div>
    </section>
  )
}
