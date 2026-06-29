import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { Pencil, Plus, Target, Trash2, X } from 'lucide-react'
import { ProgressRing } from '@/components/stats/ProgressRing'
import {
  ALLOWED_GOAL_KINDS,
  createGoal,
  deleteGoal,
  goalMeta,
  goalSubtext,
  goalTitle,
  listGoals,
  updateGoal,
  type Goal,
  type GoalKind,
} from '@/lib/goals'
import { useBookTypes } from '@/lib/bookTypes'
import { cn } from '@/lib/utils'

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
  /** Existing goals — used to grey out (kind, type) combos already taken. */
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
      setError(goal ? 'Could not update the goal.' : 'Could not create the goal — it may already exist.')
      setSaving(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-md rounded-2xl border border-border bg-card p-5 shadow-xl shadow-accent-soft">
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="text-base font-semibold">{goal ? 'Edit goal' : 'Set a reading goal'}</h2>
            <p className="text-xs text-muted-foreground">
              {goal ? goalTitle(goal) : 'Pick a rhythm — progress counts automatically.'}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        {!goal && (
          <>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">Goal</p>
            <div className="mb-4 grid grid-cols-2 gap-1.5">
              {ALLOWED_GOAL_KINDS.map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setKind(k)}
                  className={cn(
                    'rounded-lg border px-2.5 py-1.5 text-left text-xs transition',
                    kind === k ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:text-foreground',
                  )}
                >
                  {goalMeta(k).label}
                </button>
              ))}
            </div>

            {bookTypes.length > 0 && (
              <>
                <p className="mb-1.5 text-xs font-medium text-muted-foreground">Counts</p>
                <select
                  value={bookTypeId ?? ''}
                  onChange={(e) => setBookTypeId(e.target.value === '' ? null : Number(e.target.value))}
                  className="mb-4 w-full rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs text-foreground focus:border-primary focus:outline-none"
                >
                  <option value="">All books</option>
                  {bookTypes.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label} only
                    </option>
                  ))}
                </select>
              </>
            )}
          </>
        )}

        <p className="mb-1.5 text-xs font-medium text-muted-foreground">Target</p>
        <div className="mb-2 flex gap-1.5">
          {meta.presets.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setTarget(String(p))}
              className={cn(
                'rounded-lg border px-2.5 py-1.5 text-xs tabular-nums transition',
                target === String(p) ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:text-foreground',
              )}
            >
              {p}
            </button>
          ))}
        </div>
        <div className="mb-4 flex items-center gap-2">
          <input
            type="number"
            min={1}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && save()}
            placeholder={String(meta.placeholder)}
            className="w-24 rounded-lg border border-border bg-background px-2.5 py-1.5 text-sm tabular-nums text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none"
          />
          <span className="text-xs text-muted-foreground">{meta.inputUnit}</span>
        </div>

        {!goal && taken(kind, bookTypeId) && (
          <p className="mb-3 text-xs text-muted-foreground">You already have this goal — edit it from its tile instead.</p>
        )}
        {error && <p className="mb-3 text-xs text-destructive">{error}</p>}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground transition hover:bg-muted hover:text-foreground">
            Cancel
          </button>
          <button
            type="button"
            onClick={save}
            disabled={!valid || saving}
            className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {goal ? 'Save' : 'Set goal'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

// ── Dashboard tile body ───────────────────────────────────────────────────────

/**
 * The "Reading Goals" tile shows ALL of the user's goals — one card per goal,
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
    return <div className="flex h-full items-center justify-center text-xs text-muted-foreground">Loading…</div>
  }

  if (goals.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <Target className="h-6 w-6 text-muted-foreground/40" />
        <p className="text-xs text-muted-foreground">Set a target and watch the ring fill as you read.</p>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90"
        >
          Set a goal
        </button>
        {creating && <GoalEditorModal existing={[]} onSaved={onChanged} onClose={() => setCreating(false)} />}
      </div>
    )
  }

  return (
    <div
      // Fixed equal columns (auto-fill) so cards are uniform and rows always
      // line up — no ragged content-width wrapping. New-goal cell matches.
      className="grid items-stretch gap-2.5"
      style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(11.5rem, 1fr))' }}
    >
      {goals.map((goal) => {
        const caption = goal.period === 'year' && goal.year ? `${goal.year} Challenge` : goalMeta(goal.kind).caption
        const sub = goal.book_type_label ? `${goal.book_type_label} · ${goalSubtext(goal)}` : goalSubtext(goal)
        return (
          <div
            key={goal.id}
            className="group/goal relative flex items-center gap-2.5 overflow-hidden rounded-lg border border-border bg-muted/30 p-2.5"
          >
            <GoalRing goal={goal} size={48} compact />
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <p className="truncate text-xs font-medium leading-tight text-foreground">{caption}</p>
              <p className="truncate text-[11px] leading-tight text-muted-foreground">{sub}</p>
            </div>
            {/* edit/delete reveal on hover — New goal is the always-visible action */}
            <div className="absolute right-1 top-1 flex items-center gap-0.5 rounded-md bg-card/80 opacity-0 backdrop-blur-sm transition-opacity group-hover/goal:opacity-100">
              <button
                type="button"
                onClick={() => setEditing(goal)}
                title="Edit goal"
                className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <Pencil className="h-3 w-3" />
              </button>
              <button
                type="button"
                onClick={() => deleteGoal(goal.id).then(onChanged).catch(() => {})}
                title="Delete this goal"
                className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          </div>
        )
      })}

      {/* always-visible creation cell — matches a goal card's footprint */}
      <button
        type="button"
        onClick={() => setCreating(true)}
        className="flex min-h-[4.25rem] items-center justify-center gap-1.5 rounded-lg border border-dashed border-border text-xs text-muted-foreground transition hover:border-primary/50 hover:text-foreground"
      >
        <Plus className="h-3.5 w-3.5" />
        New goal
      </button>

      {editing && (
        <GoalEditorModal goal={editing} existing={goals} onSaved={onChanged} onClose={() => setEditing(null)} />
      )}
      {creating && <GoalEditorModal existing={goals} onSaved={onChanged} onClose={() => setCreating(false)} />}
    </div>
  )
}

// ── Home tab strip ────────────────────────────────────────────────────────────

/**
 * Read-only compact goal segments for the Home tab. Mirrors the quick-stats
 * panel's visual language (label over value, hairline dividers, mini ring in
 * the icon slot) so the two sit side by side as one summary zone.
 * Renders nothing without goals. Pace details live in the title tooltip and
 * on the stats dashboard tile — Home stays glanceable.
 */
export function HomeGoalRings() {
  const navigate = useNavigate()
  const [goals, setGoals] = useState<Goal[]>([])

  useEffect(() => {
    listGoals().then(setGoals).catch(() => {})
  }, [])

  if (goals.length === 0) return null

  return (
    <div className="grid w-full grid-cols-2 gap-y-3 px-4 py-4 sm:flex">
      {goals.map((goal, i) => (
        <button
          key={goal.id}
          type="button"
          onClick={() => navigate('/stats')}
          title={goalSubtext(goal)}
          className={cn(
            'text-left sm:px-5 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-md',
            i === 0 && 'sm:pl-0',
            i > 0 && 'sm:border-l sm:border-border/60',
          )}
        >
          <p className="text-xs text-muted-foreground/70">{goalTitle(goal)}</p>
          <p className="flex items-center gap-2 text-xl font-semibold tabular-nums text-foreground leading-tight">
            <ProgressRing pct={goal.pct} size={22} stroke={3.5} />
            {Math.round(goal.current)}
            <span className="text-base font-medium text-muted-foreground/70">
              /{goal.target}
              {goalMeta(goal.kind).unit}
            </span>
          </p>
        </button>
      ))}
    </div>
  )
}
