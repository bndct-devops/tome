// Reading goals on the Overview, directly under the hero, shown when the reader
// has a goal. Content and editing as the old Goals tile had them (GoalWidget):
// progress toward each goal, edit and delete on the goal (on hover or focus;
// always shown on touch), a new goal from the section's heading, the editor
// modal itself reused from GoalWidget. Drawn in the A2 look of the Records row:
// figures on hairlines, a progress track under each.
//
// Reserved space: goals come from their own request (/api/goals), so the
// skeleton draws as many cells as the last answer had in this browser
// (tome_stats_goal_count). With none remembered (a first visit, or a reader
// without goals) nothing is reserved: a reader without goals sees no shift; a
// reader with goals on a first visit sees the section appear when its answer
// lands (the sections below move down by its height, once).
//
// Without goals the section is absent, so a new goal starts from a quiet line at
// the end of the page (GoalsStart), where appearing moves nothing.
import { useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'
import { cn } from '@/lib/utils'
import { deleteGoal, goalMeta, goalSubtext, goalTitle, type Goal } from '@/lib/goals'
import { rememberedGoalCount } from './goalsData'
import { GoalEditorModal } from '@/components/stats/GoalWidget'
import { Heading, Ph, Track } from '@/components/stats/kit/Kit'
import { CountUp } from '@/components/stats/kit/CountUp'
import { ONCE, stagger, useEntry } from '@/components/stats/kit/motion'
import { tipAttrs } from '@/components/stats/kit/tip'
import { HAIR, num } from '@/components/stats/kit/tokens'

/* eslint-disable lingui/no-unlocalized-strings -- placeholder text, drawn as blocks */
const PH = { label: '2026 challenge', value: '00', unit: '/ 00 books', foot: '0 ahead of pace' }
/* eslint-enable lingui/no-unlocalized-strings */

export function Goals({ goals, reload, desktop }: { goals: Goal[] | null; reload: () => void; desktop: boolean }) {
  const e = useEntry('overview/goals', goals ? ONCE : null)
  const [editing, setEditing] = useState<Goal | null>(null)
  const [creating, setCreating] = useState(false)
  const [skeleton] = useState(rememberedGoalCount)
  const n = goals ? goals.length : skeleton
  if (n === 0) return null
  const cols = desktop ? 4 : 2
  const cells = goals ?? Array.from({ length: n }, () => null)
  return (
    <section ref={e.ref} data-box="goals" aria-busy={!goals}>
      <Heading
        right
        note={
          <button
            type="button"
            onClick={() => setCreating(true)}
            disabled={!goals}
            className="inline-flex items-center gap-1 text-[15px] font-medium text-primary underline-offset-4 hover:underline disabled:opacity-0"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            <Trans>New goal</Trans>
          </button>
        }
      >
        <Trans>Reading goals</Trans>
      </Heading>
      <div data-box="goal-cells" className={cn('k-bars grid', desktop ? 'grid-cols-4' : 'grid-cols-[minmax(0,1fr)_minmax(0,1fr)]')}>
        {cells.map((g, i) => {
          const col = i % cols
          const row = Math.floor(i / cols)
          const lastRow = row === Math.floor((n - 1) / cols)
          const meta = g ? goalMeta(g.kind) : null
          const label = g ? (g.period === 'year' && g.year ? (() => { const y = g.year; return t`${y} challenge` })() : meta!.caption) : PH.label
          const type = g?.book_type_label
          const reached = !!g && g.current >= g.target
          const sub = g ? goalSubtext(g) : PH.foot
          const target = g ? num(g.target) : ''
          const unit = g ? `/ ${target} ${meta!.inputUnit}` : PH.unit
          const pct = g ? Math.round(g.pct) : 0
          return (
            <div
              key={g ? g.id : i}
              data-box={`goal-${i}`}
              className={cn(
                'group/goal relative min-w-0',
                desktop
                  ? cn('px-6 py-0.5', col === 0 && 'pl-0', col > 0 && cn('border-l', HAIR), row > 0 && 'pt-5', !lastRow && 'pb-5')
                  : cn('py-4', col === 0 ? 'pr-3' : cn('border-l pl-4', HAIR), row === 0 && 'pt-0', !lastRow ? cn('border-b', HAIR) : 'pb-0'),
                desktop && row > 0 && cn('border-t', HAIR),
              )}
            >
              <div className={cn('truncate pr-14 text-muted-foreground', desktop ? 'text-[14px]' : 'text-[13px]')}>
                <Ph on={!g}>{label}</Ph>
                {type && <span> · {type}</span>}
              </div>
              <div className={cn('mt-1.5 truncate font-display font-bold tabular-nums leading-none', desktop ? 'text-[30px]' : 'text-[26px]', reached ? 'text-success' : 'text-foreground')}>
                <Ph on={!g}>{g ? <CountUp entry={e} value={Math.round(g.current)} format={num} /> : PH.value}</Ph>
                <span className="ml-1.5 font-sans text-[15px] font-semibold tracking-normal text-muted-foreground"><Ph on={!g}>{unit}</Ph></span>
              </div>
              <div className="mt-3 flex h-[6px]">
                <Track
                  pct={g ? g.pct : 0}
                  className={!g ? 'opacity-40' : ''}
                  style={stagger(i, n)}
                  tip={g ? tipAttrs({ title: goalTitle(g), value: t`${pct}% of the goal`, note: sub }) : undefined}
                />
              </div>
              <div className={cn('mt-2 truncate text-muted-foreground', desktop ? 'text-[14px]' : 'text-[12px]', 'leading-[1.4]')}>
                <Ph on={!g}>{sub}</Ph>
              </div>
              {g && (
                // Edit and delete, as the old tile had them: shown on hover or focus, always on touch screens.
                <div className="absolute right-0 top-0 flex items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover/goal:opacity-100 [@media(hover:none)]:opacity-100">
                  <button
                    type="button"
                    onClick={() => setEditing(g)}
                    title={t`Edit goal`}
                    aria-label={t`Edit goal`}
                    className="inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteGoal(g.id).then(reload).catch(() => {})}
                    title={t`Delete this goal`}
                    aria-label={t`Delete this goal`}
                    className="inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </div>
      <Editors goals={goals ?? []} editing={editing} creating={creating} onClose={() => { setEditing(null); setCreating(false) }} reload={reload} />
    </section>
  )
}

function Editors({ goals, editing, creating, onClose, reload }: { goals: Goal[]; editing: Goal | null; creating: boolean; onClose: () => void; reload: () => void }) {
  if (editing) return <GoalEditorModal goal={editing} existing={goals} onSaved={reload} onClose={onClose} />
  if (creating) return <GoalEditorModal existing={goals} onSaved={reload} onClose={onClose} />
  return null
}

/** The way to a first goal when there is none: a quiet line at the end of the page. */
export function GoalsStart({ goals, reload }: { goals: Goal[] | null; reload: () => void }) {
  const [creating, setCreating] = useState(false)
  // Drawn in the skeleton too when the last answer had no goals, so it is there from the first frame.
  const [remembered] = useState(rememberedGoalCount)
  if (goals ? goals.length > 0 : remembered > 0) return null
  return (
    <div data-box="goals-start" className={cn('-mt-4', !goals && 'invisible')} aria-hidden={!goals || undefined}>
      <button
        type="button"
        disabled={!goals}
        onClick={() => setCreating(true)}
        className="inline-flex items-center gap-1.5 text-[15px] font-medium text-primary underline-offset-4 hover:underline"
      >
        <Plus className="h-4 w-4" aria-hidden="true" />
        <Trans>Set a reading goal</Trans>
      </button>
      <span className="ml-2 text-[14px] text-muted-foreground"><Trans>Progress counts automatically.</Trans></span>
      {creating && <GoalEditorModal existing={[]} onSaved={reload} onClose={() => setCreating(false)} />}
    </div>
  )
}
