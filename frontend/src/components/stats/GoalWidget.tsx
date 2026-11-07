import { useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { ModalShell } from '@/components/ModalShell'
import { FormLabel, Input, Pill, PillGroup, Select } from '@/components/page'
import { ProgressRing } from '@/components/stats/ProgressRing'
import {
  ALLOWED_GOAL_KINDS,
  createGoal,
  deleteGoal,
  goalMeta,
  goalSubtext,
  goalTitle,
  updateGoal,
  type Goal,
  type GoalKind,
} from '@/lib/goals'
import { useBookTypes } from '@/lib/bookTypes'
import { cn } from '@/lib/utils'
import { Trans } from '@lingui/react/macro'
import { t } from '@lingui/core/macro'

// ── Ring card (shared by the dashboard tile and the Home strip) ───────────────

function GoalRing({ goal, size, compact }: { goal: Goal; size: number; compact?: boolean }) {
  const meta = goalMeta(goal.kind)
  return (
    <ProgressRing pct={goal.pct} size={size} stroke={compact ? 5 : 7}>
      <div className="flex flex-col items-center leading-none">
        <span className={cn('font-bold tabular-nums text-foreground', compact ? 'text-sm' : 'text-lg')}>
          {Math.round(goal.current)}
        </span>
        <span className={cn('text-muted-foreground', compact ? 'text-[9px]' : 'text-[10px]')}>
          /{goal.target}
          {meta.unit}
        </span>
      </div>
    </ProgressRing>
  )
}

// ── Editor modal (create or edit) ─────────────────────────────────────────────

/**
 * Create mode: pick a kind (curated set), a preset or custom target, and an
 * optional book type ("20 manga this year" alongside "20 books this year").
 * Edit mode: the goal's identity (kind + type) is fixed; only the target moves.
 */
export function GoalEditorModal({
  goal,
  existing,
  onSaved,
  onClose,
}: {
  /** Set = edit this goal; unset = create a new one. */
  goal?: Goal
  /** Existing goals, used to grey out (kind, type) combos already taken. */
  existing: Goal[]
  onSaved: (saved: Goal) => void
  onClose: () => void
}) {
  const bookTypes = useBookTypes()
  const [kind, setKind] = useState<GoalKind>((goal?.kind as GoalKind) ?? 'books_per_year')
  const [bookTypeId, setBookTypeId] = useState<number | null>(goal?.book_type_id ?? null)
  const [target, setTarget] = useState<string>(goal ? String(goal.target) : '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const meta = goalMeta(kind)
  const taken = (k: GoalKind, typeId: number | null) =>
    existing.some((g) => g.kind === k && g.book_type_id === typeId && g.id !== goal?.id)
  const parsed = parseInt(target, 10)
  const valid = Number.isFinite(parsed) && parsed > 0 && (goal != null || !taken(kind, bookTypeId))

  async function save() {
    if (!valid || saving) return
    setSaving(true)
    setError(null)
    try {
      const saved = goal ? await updateGoal(goal.id, parsed) : await createGoal(kind, parsed, bookTypeId)
      onSaved(saved)
      onClose()
    } catch {
      setError(goal ? t`Could not update the goal.` : t`Could not create the goal. It may already exist.`)
      setSaving(false)
    }
  }

  return (
    <ModalShell
      open
      onClose={onClose}
      size="sm"
      title={goal ? t`Edit goal` : t`Set a reading goal`}
      subtitle={goal ? goalTitle(goal) : t`Pick a rhythm. Progress counts automatically.`}
      footer={
        <>
          <Pill variant="quiet" onClick={onClose}>
            <Trans>Cancel</Trans>
          </Pill>
          <Pill variant="primary" onClick={save} disabled={!valid || saving}>
            {goal ? t`Save` : t`Set goal`}
          </Pill>
        </>
      }
    >
      <div className="space-y-5">
        {!goal && (
          <>
            <div>
              <FormLabel><Trans>Goal</Trans></FormLabel>
              <PillGroup
                size="sm"
                value={kind}
                onChange={setKind}
                options={ALLOWED_GOAL_KINDS.map((k) => ({ value: k, label: goalMeta(k).label }))}
              />
            </div>

            {bookTypes.length > 0 && (
              <div>
                <FormLabel htmlFor="goal-counts"><Trans>Counts</Trans></FormLabel>
                <Select
                  id="goal-counts"
                  value={bookTypeId ?? ''}
                  onChange={(e) => setBookTypeId(e.target.value === '' ? null : Number(e.target.value))}
                >
                  <option value="">{t`All books`}</option>
                  {bookTypes.map((bt) => (
                    <option key={bt.id} value={bt.id}>
                      {(() => { const label = bt.label; return t`${label} only` })()}
                    </option>
                  ))}
                </Select>
              </div>
            )}
          </>
        )}

        <div>
          <FormLabel htmlFor="goal-target"><Trans>Target</Trans></FormLabel>
          <PillGroup
            size="sm"
            value={target}
            onChange={setTarget}
            options={meta.presets.map((p) => ({ value: String(p), label: <span className="tabular-nums">{p}</span> }))}
          />
          <div className="mt-2.5 flex items-center gap-2">
            <Input
              id="goal-target"
              type="number"
              min={1}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && save()}
              placeholder={String(meta.placeholder)}
              className="w-28 tabular-nums"
            />
            <span className="text-[14px] text-muted-foreground">{meta.inputUnit}</span>
          </div>
        </div>

        {!goal && taken(kind, bookTypeId) && (
          <p className="text-[14px] text-muted-foreground"><Trans>You already have this goal. Edit it from its tile instead.</Trans></p>
        )}
        {error && <p className="text-[14px] text-destructive">{error}</p>}
      </div>
    </ModalShell>
  )
}

// ── Dashboard tile body ───────────────────────────────────────────────────────

/**
 * The "Reading Goals" tile shows ALL of the user's goals, one card per goal,
 * managed in place. One surface, local effects: the trash on a card deletes
 * exactly that goal, the add-card creates one. No per-tile goal pointers.
 */
export function GoalWidgetBody({
  goals,
  onChanged,
}: {
  goals: Goal[] | null
  onChanged: () => void
}) {
  const [editing, setEditing] = useState<Goal | null>(null)
  const [creating, setCreating] = useState(false)

  if (goals === null) {
    return <div className="flex h-full items-center justify-center text-[14px] text-muted-foreground"><Trans>Loading…</Trans></div>
  }

  if (goals.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <p className="text-[14px] text-muted-foreground"><Trans>Set a target and watch the ring fill as you read.</Trans></p>
        <Pill onClick={() => setCreating(true)}>
          <Trans>Set a goal</Trans>
        </Pill>
        {creating && <GoalEditorModal existing={[]} onSaved={onChanged} onClose={() => setCreating(false)} />}
      </div>
    )
  }

  return (
    <div
      // Fixed equal columns (auto-fill) so cards are uniform and rows always
      // line up, no ragged content-width wrapping. New-goal cell matches.
      className="grid items-stretch gap-2.5"
      style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(11.5rem, 1fr))' }}
    >
      {goals.map((goal) => {
        const caption = goal.period === 'year' && goal.year ? (() => { const y = goal.year; return t`${y} challenge` })() : goalMeta(goal.kind).caption
        const sub = goal.book_type_label ? `${goal.book_type_label} · ${goalSubtext(goal)}` : goalSubtext(goal)
        return (
          <div
            key={goal.id}
            className="group/goal relative flex items-center gap-3 overflow-hidden rounded-lg bg-secondary/50 p-2.5"
          >
            <GoalRing goal={goal} size={48} compact />
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <p className="truncate text-[14px] font-medium leading-tight text-foreground">{caption}</p>
              <p className="truncate text-[13px] leading-tight text-muted-foreground">{sub}</p>
            </div>
            {/* edit/delete reveal on hover; New goal is the always-visible action */}
            <div className="absolute right-1 top-1 flex items-center gap-0.5 rounded-full bg-card/90 opacity-0 transition-opacity group-hover/goal:opacity-100 focus-within:opacity-100">
              <button
                type="button"
                onClick={() => setEditing(goal)}
                title={t`Edit goal`}
                className="inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
              >
                <Pencil className="h-3 w-3" />
              </button>
              <button
                type="button"
                onClick={() => deleteGoal(goal.id).then(onChanged).catch(() => {})}
                title={t`Delete this goal`}
                className="inline-flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          </div>
        )
      })}

      {/* always-visible creation cell, matches a goal card's footprint */}
      <button
        type="button"
        onClick={() => setCreating(true)}
        className="flex min-h-[4.25rem] items-center justify-center gap-1.5 rounded-lg bg-secondary/50 text-[13px] text-muted-foreground transition hover:bg-secondary hover:text-foreground"
      >
        <Plus className="h-3.5 w-3.5" />
        <Trans>New goal</Trans>
      </button>

      {editing && (
        <GoalEditorModal goal={editing} existing={goals} onSaved={onChanged} onClose={() => setEditing(null)} />
      )}
      {creating && <GoalEditorModal existing={goals} onSaved={onChanged} onClose={() => setCreating(false)} />}
    </div>
  )
}

// ── Home tab strip ────────────────────────────────────────────────────────────
