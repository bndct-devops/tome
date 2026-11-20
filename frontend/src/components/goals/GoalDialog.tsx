// New goal and Edit goal. Creating: the eight kinds (the ones already set show
// their progress and are not picked twice), "Counts" (a book type) where it
// applies, then the target: three suggested from the reader's own reading
// (/api/goals/suggestions: "reached on 46 of 90 days") with what it means
// ("At 60 min a day you'd finish about 3.7 books a month"), or the presets
// without history. Finish by a date: a book or series, a day (End of November,
// End of the year, or any day) and what it takes, live from
// /api/goals/finish-preview. Editing: the target, or a finish-by goal's date.
import { useMemo, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { CalendarDays, Check, ChevronDown } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { cn, formatDuration } from '@/lib/utils'
import { ModalShell } from '@/components/ModalShell'
import { Input, Pill } from '@/components/page'
import { ChoiceMenu } from '@/components/stats/kit/Menu'
import { dec1, num } from '@/components/stats/kit/tokens'
import { useStatsGet } from '@/components/stats/kit/useStatsGet'
import { useBookTypes } from '@/lib/bookTypes'
import {
  createGoal, finishPreviewUrl, kindsUrl, suggestionsUrl, updateGoal,
  type Chip, type FinishEstimate, type Goal, type GoalKind, type KindInfo, type KindSuggestion, type Suggestions,
} from '@/lib/goals'
import { KIND_ORDER, booksAMonth, minutesADay, parseTarget, type WeekStart } from '@/lib/goalsModel'
import { DayPicker, FinishTargetPicker } from './pickers'
import { dateChips, targetKey, type FinishTarget } from './pickerModel'
import { goalName, kindLabel, kindNote, longDate, shortDate, targetUnit } from './text'

export type DialogMode =
  | { kind: 'create'; preset?: { kind: GoalKind; target?: number } }
  | { kind: 'edit'; goal: Goal }

const NEW_KINDS = new Set<GoalKind>(['days_per_week', 'finish_by'])
const BOUNDS: Record<GoalKind, { min: number; max: number | null }> = {
  books_per_year: { min: 1, max: null }, books_per_month: { min: 1, max: null },
  minutes_per_day: { min: 1, max: 1440 }, minutes_per_week: { min: 1, max: 10080 },
  pages_per_day: { min: 1, max: null }, pages_per_week: { min: 1, max: null },
  days_per_week: { min: 1, max: 7 }, finish_by: { min: 1, max: null },
}
const SCOPED = new Set<GoalKind>(['books_per_year', 'books_per_month', 'minutes_per_day', 'minutes_per_week', 'pages_per_day', 'pages_per_week'])

function Label({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) {
  return <label htmlFor={htmlFor} className="mb-1.5 block text-[13px] text-muted-foreground">{children}</label>
}

/** The figure line a kind's tile shows when the reader already has it: "58 / 60". */
const progress = (g: Goal) => `${num(Math.round(g.current))} / ${num(g.target)}`

export function GoalDialog({ mode, goals, onClose, onSaved }: { mode: DialogMode; goals: Goal[]; onClose: () => void; onSaved?: (g: Goal) => void }) {
  const editing = mode.kind === 'edit' ? mode.goal : null
  const types = useBookTypes()
  const { data: kinds } = useStatsGet<{ kinds: KindInfo[]; week_start: WeekStart }>(kindsUrl())
  const { data: sug } = useStatsGet<Suggestions>(suggestionsUrl())
  const info = useMemo(() => new Map((kinds?.kinds ?? []).map((k) => [k.kind, k])), [kinds])
  const weekStart: WeekStart = kinds?.week_start ?? 'monday'
  const today = sug?.today ?? new Date().toISOString().slice(0, 10)

  // Which (kind, book type) pairs the reader has; a kind is taken when no scope of it is free.
  const has = (k: GoalKind, type: number | null) => goals.some((g) => g.kind === k && g.book_type_id === type && g.id !== editing?.id)
  const freeScopes = (k: GoalKind): (number | null)[] => (SCOPED.has(k) ? [null, ...types.map((x) => x.id)] : [null]).filter((s) => !has(k, s))
  const taken = (k: GoalKind) => k !== 'finish_by' && freeScopes(k).length === 0

  const firstFree = KIND_ORDER.find((k) => k !== 'finish_by' && !has(k, null)) ?? KIND_ORDER.find((k) => !taken(k)) ?? 'finish_by'
  const [kind, setKind] = useState<GoalKind>(editing?.kind ?? (mode.kind === 'create' && mode.preset?.kind ? mode.preset.kind : firstFree))
  const [scope, setScope] = useState<number | null>(() => editing?.book_type_id ?? (freeScopes(kind)[0] ?? null))
  const [raw, setRaw] = useState<string | null>(editing && editing.kind !== 'finish_by' ? String(editing.target) : mode.kind === 'create' && mode.preset?.target ? String(mode.preset.target) : null)
  const [finish, setFinish] = useState<FinishTarget | null>(null)
  const [due, setDue] = useState<string | null>(editing?.due_date ?? null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const s = sug?.kinds[kind]
  const fallback = s?.suggested?.[1]?.value ?? s?.presets?.[1]?.value ?? info.get(kind)?.presets?.[1] ?? null
  const text = raw ?? (fallback != null ? String(fallback) : '')
  const bounds = BOUNDS[kind]
  const parsed = parseTarget(text, bounds.min, bounds.max)
  const chosenDue = due ?? dateChips(today).at(-1)!.date

  const pickKind = (k: GoalKind) => {
    if (taken(k)) return
    setKind(k)
    setScope(freeScopes(k)[0] ?? null)
    setRaw(null)
    setError(null)
  }

  // Finish by: what is already being finished cannot be picked twice.
  const finishTaken = useMemo(() => new Set(goals.filter((g) => g.kind === 'finish_by').map((g) => (g.target_series ? `s:${g.target_series}` : `b:${g.target_book_id}`))), [goals])
  const previewTarget = editing?.kind === 'finish_by'
    ? (editing.target_series ? { series: editing.target_series } : { bookId: editing.target_book_id! })
    : finish ? ('series' in finish ? { series: finish.series } : { bookId: finish.bookId }) : null
  const { data: preview, failed: previewFailed } = useStatsGet<FinishEstimate>(kind === 'finish_by' && previewTarget ? finishPreviewUrl(chosenDue, previewTarget) : null)

  const canSave = !saving && (kind === 'finish_by' ? (editing ? !!due && due !== editing.due_date : !!finish) : parsed.ok && !(mode.kind === 'create' && has(kind, scope)))

  async function save() {
    if (!canSave) return
    setSaving(true)
    setError(null)
    try {
      let g: Goal
      if (editing) {
        g = editing.kind === 'finish_by' ? await updateGoal(editing.id, { due_date: chosenDue }) : await updateGoal(editing.id, { target: (parsed as { value: number }).value })
      } else if (kind === 'finish_by') {
        g = await createGoal({ kind, due_date: chosenDue, ...('series' in finish! ? { series: finish!.series } : { book_id: finish!.bookId }) })
      } else {
        g = await createGoal({ kind, target: (parsed as { value: number }).value, book_type_id: scope })
      }
      onSaved?.(g)
      onClose()
    } catch (e) {
      const status = (e as { status?: number }).status
      const detail = (e as Error).message
      setError(status === 409 ? t`You already have this goal. Edit it instead.` : status === 422 && detail ? detail : t`Could not save the goal. Try again.`)
      setSaving(false)
    }
  }

  const typeLabel = (id: number | null) => (id == null ? t`All books` : (() => { const l = types.find((x) => x.id === id)?.label ?? ''; return t`${l} only` })())

  return createPortal(
    <ModalShell
      open
      onClose={onClose}
      size="md"
      title={editing ? t`Edit goal` : t`New goal`}
      subtitle={editing ? goalName(editing, today) : t`Progress counts by itself, from the reading Tome records.`}
      footer={
        <>
          <Pill variant="quiet" onClick={onClose}><Trans>Cancel</Trans></Pill>
          <Pill variant="primary" onClick={save} disabled={!canSave}>{editing ? t`Save` : t`Set goal`}</Pill>
        </>
      }
    >
      <div className="space-y-5">
        {!editing && (
          <div>
            <Label><Trans>Goal</Trans></Label>
            <div role="radiogroup" aria-label={t`Goal`} className="grid grid-cols-2 gap-2">
              {KIND_ORDER.map((k) => {
                const on = k === kind
                const mine = goals.find((g) => g.kind === k && g.book_type_id == null)
                const off = taken(k)
                return (
                  <button
                    key={k}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    aria-disabled={off}
                    onClick={() => pickKind(k)}
                    className={cn(
                      'relative min-w-0 rounded-xl px-3.5 py-2.5 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      on ? 'bg-secondary ring-2 ring-foreground' : 'bg-secondary/55 hover:bg-secondary',
                      (off || (mine && !on)) && 'opacity-55',
                      off && 'cursor-not-allowed hover:bg-secondary/55',
                    )}
                  >
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 pr-5 text-[14px] font-semibold text-foreground">
                      {kindLabel(k)}
                      {NEW_KINDS.has(k) && <span className="rounded-full bg-primary/15 px-1.5 py-px text-[10px] font-bold uppercase tracking-wide text-primary"><Trans>New</Trans></span>}
                    </span>
                    <span className="mt-0.5 block truncate text-[12px] text-muted-foreground">
                      {mine && k !== 'finish_by' ? (() => { const p = progress(mine); return t`You have this one · ${p}` })() : kindNote(k)}
                    </span>
                    {on && <Check className="absolute right-3 top-3 h-4 w-4 text-foreground" strokeWidth={2.5} aria-hidden="true" />}
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {!editing && SCOPED.has(kind) && types.length > 0 && (
          <div>
            <Label><Trans>Counts</Trans></Label>
            <ChoiceMenu
              value={String(scope)}
              options={freeScopes(kind).map((id) => ({ id: String(id), label: typeLabel(id) }))}
              onPick={(id) => setScope(id === 'null' ? null : Number(id))}
              label={t`Counts`}
              className="inline-flex h-10 items-center justify-between gap-3 rounded-xl bg-secondary px-3.5 text-[14px] font-medium text-foreground transition-colors hover:bg-surface-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {typeLabel(scope)}
              <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            </ChoiceMenu>
          </div>
        )}

        {kind === 'finish_by' ? (
          <FinishBody
            editing={editing}
            today={today}
            weekStart={weekStart}
            target={finish}
            setTarget={(x) => { setFinish(x); setError(null) }}
            taken={finishTaken}
            due={chosenDue}
            setDue={setDue}
            preview={preview}
            previewFailed={previewFailed}
          />
        ) : (
          <TargetBody kind={kind} s={s} sug={sug} text={text} setText={setRaw} parsed={parsed} onEnter={save} />
        )}

        {!editing && kind !== 'finish_by' && has(kind, scope) && (
          <p className="text-[14px] text-muted-foreground"><Trans>You already have this goal. Edit it from its card instead.</Trans></p>
        )}
        {error && <p role="alert" className="text-[14px] text-destructive">{error}</p>}
      </div>
    </ModalShell>,
    document.body,
  )
}

// ── The target of a count goal ────────────────────────────────────────────────

function intro(kind: GoalKind, s: KindSuggestion): ReactNode {
  const avg = s.average
  if (avg == null || !s.suggested) return null
  const a = kind === 'days_per_week' ? dec1(avg) : num(Math.round(avg))
  switch (kind) {
    case 'minutes_per_day': return <Trans>You've read <b className="font-semibold text-foreground">{a} min a day</b> on average over the last 90 days.</Trans>
    case 'pages_per_day': return <Trans>You've read <b className="font-semibold text-foreground">{a} pages a day</b> on average over the last 90 days.</Trans>
    case 'minutes_per_week': return <Trans>You've read <b className="font-semibold text-foreground">{a} min a week</b> on average over the last 12 weeks.</Trans>
    case 'pages_per_week': return <Trans>You've read <b className="font-semibold text-foreground">{a} pages a week</b> on average over the last 12 weeks.</Trans>
    case 'days_per_week': return <Trans>You've read on <b className="font-semibold text-foreground">{a} days a week</b> on average over the last 12 weeks.</Trans>
    case 'books_per_month': { const b = dec1(avg); return <Trans>At your average day's reading over the last 90 days you'd finish about <b className="font-semibold text-foreground">{b} books a month</b>.</Trans> }
    case 'books_per_year': return <Trans>At your average day's reading over the last 90 days you'd finish about <b className="font-semibold text-foreground">{a} books a year</b>.</Trans>
    default: return null
  }
}

function chipNote(s: KindSuggestion, c: Chip): string {
  if (c.reached == null || !c.of) return ''
  const r = num(c.reached), o = num(c.of)
  if (s.over === 'weeks') return t`reached in ${r} of ${o} weeks`
  if (s.kind === 'books_per_month' || s.kind === 'books_per_year') return t`your pace on ${r} of ${o} days`
  return t`reached on ${r} of ${o} days`
}

const CHIP_TITLE = { steady: () => t`Steady`, keep_it_up: () => t`Keep it up`, stretch: () => t`Stretch` } as const

function TargetBody({ kind, s, sug, text, setText, parsed, onEnter }: {
  kind: GoalKind
  s: KindSuggestion | undefined
  sug: Suggestions | null
  text: string
  setText: (v: string) => void
  parsed: ReturnType<typeof parseTarget>
  onEnter: () => void
}) {
  const value = parsed.ok ? parsed.value : null
  const chips = s?.suggested ?? null
  const presets = s?.presets ?? []
  const bounds = BOUNDS[kind]
  const unit = targetUnit(kind, value ?? 2)
  let why = ''
  if (!parsed.ok && text.trim()) {
    const lo = num(bounds.min)
    why = bounds.max != null ? (() => { const hi = num(bounds.max!); return t`Enter a whole number from ${lo} to ${hi}.` })() : t`Enter a whole number of at least ${lo}.`
  }
  // What the target means, from the reader's own reading.
  let consequence: ReactNode = null
  if (value != null && sug) {
    if (kind === 'minutes_per_day' || kind === 'pages_per_day' || kind === 'minutes_per_week' || kind === 'pages_per_week') {
      const per = kind.endsWith('_day') ? 'day' : 'week'
      const b = booksAMonth(value, per, kind.startsWith('minutes') ? sug.per_book.minutes : sug.per_book.pages)
      if (b != null) {
        const v = num(value), bm = dec1(b)
        consequence = kind === 'minutes_per_day' ? <Trans>At {v} min a day you'd finish about <b className="font-semibold">{bm} books a month</b>.</Trans>
          : kind === 'minutes_per_week' ? <Trans>At {v} min a week you'd finish about <b className="font-semibold">{bm} books a month</b>.</Trans>
          : kind === 'pages_per_day' ? <Trans>At {v} pages a day you'd finish about <b className="font-semibold">{bm} books a month</b>.</Trans>
          : <Trans>At {v} pages a week you'd finish about <b className="font-semibold">{bm} books a month</b>.</Trans>
      }
    } else if (kind === 'books_per_month' || kind === 'books_per_year') {
      const m = minutesADay(value, kind === 'books_per_month' ? 'month' : 'year', sug.per_book.median_minutes ?? null)
      if (m != null) {
        const v = num(value), mm = num(Math.round(m))
        consequence = kind === 'books_per_month'
          ? <Trans>{v} books a month take about <b className="font-semibold">{mm} min a day</b> at your usual time per book.</Trans>
          : <Trans>{v} books a year take about <b className="font-semibold">{mm} min a day</b> at your usual time per book.</Trans>
      }
    } else if (kind === 'days_per_week') {
      consequence = <Trans>A day counts as it does for your streak: any reading on a device, or a minute or more in the web reader.</Trans>
    }
  }
  const lead = s ? intro(kind, s) : null
  return (
    <div>
      <Label htmlFor="goal-target"><Trans>Target</Trans></Label>
      {lead && <p className="mb-3 text-[14px] leading-snug text-muted-foreground">{lead}</p>}
      {!chips && s && <p className="mb-3 text-[14px] leading-snug text-muted-foreground"><Trans>A few to start with. Once Tome has a few weeks of your reading, targets come from your own days.</Trans></p>}
      {chips ? (
        <div className="flex flex-col gap-2 sm:flex-row" role="radiogroup" aria-label={t`Suggested targets`}>
          {chips.map((c) => {
            const on = value === c.value
            return (
              <button
                key={c.label}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => setText(String(c.value))}
                className={cn('min-w-0 flex-1 rounded-xl px-3 py-2 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring', on ? 'bg-foreground text-background' : 'bg-secondary text-foreground hover:bg-surface-hover')}
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="text-[13px] font-semibold">{CHIP_TITLE[c.label ?? 'keep_it_up']()}</span>
                  <span className="text-[15px] font-bold tabular-nums">{num(c.value)}</span>
                </span>
                <span className={cn('mt-0.5 block truncate text-[12px]', on ? 'text-background/75' : 'text-muted-foreground')}>{chipNote(s!, c)}</span>
              </button>
            )
          })}
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={t`Targets`}>
          {(presets.length ? presets : []).map((c) => {
            const on = value === c.value
            return (
              <button
                key={c.value}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => setText(String(c.value))}
                className={cn('inline-flex h-9 items-center rounded-full px-4 text-[14px] font-semibold tabular-nums transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring', on ? 'bg-foreground text-background' : 'bg-secondary text-foreground hover:bg-surface-hover')}
              >
                {num(c.value)}
              </button>
            )
          })}
        </div>
      )}
      <div className="mt-3 flex items-center gap-2.5">
        <Input
          id="goal-target"
          inputMode="numeric"
          autoComplete="off"
          value={text}
          invalid={!!why}
          aria-invalid={!!why}
          aria-describedby={why ? 'goal-target-why' : undefined}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') onEnter() }}
          className="h-10 w-24 rounded-xl text-[15px] font-semibold tabular-nums"
        />
        <span className="text-[14px] text-muted-foreground">{unit}</span>
      </div>
      {why && <p id="goal-target-why" className="mt-1.5 text-[13px] text-destructive">{why}</p>}
      {consequence && <p className="mt-3 text-[14px] leading-snug text-foreground">{consequence}</p>}
    </div>
  )
}

// ── Finish by a date ──────────────────────────────────────────────────────────

function FinishBody({ editing, today, weekStart, target, setTarget, taken, due, setDue, preview, previewFailed }: {
  editing: Goal | null
  today: string
  weekStart: WeekStart
  target: FinishTarget | null
  setTarget: (x: FinishTarget) => void
  taken: Set<string>
  due: string
  setDue: (d: string) => void
  preview: FinishEstimate | null
  previewFailed: boolean
}) {
  const chips = dateChips(today)
  const chipOn = chips.find((c) => c.date === due)
  const p = preview && preview.due_date === due ? preview : null
  const showFor = editing || target
  return (
    <div className="space-y-5">
      {!editing && (
        <div>
          <Label><Trans>Book or series</Trans></Label>
          <FinishTargetPicker value={target} onPick={setTarget} taken={new Set([...taken].filter((k) => !target || k !== targetKey(target)))} />
        </div>
      )}
      <div>
        <Label><Trans>By</Trans></Label>
        <div className="flex flex-wrap items-center gap-1.5">
          {chips.map((c) => (
            <button
              key={c.id}
              type="button"
              aria-pressed={chipOn?.id === c.id}
              onClick={() => setDue(c.date)}
              className={cn('inline-flex h-9 items-center rounded-full px-4 text-[14px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring', chipOn?.id === c.id ? 'bg-foreground text-background' : 'bg-secondary text-foreground hover:bg-surface-hover')}
            >
              {c.label}
            </button>
          ))}
          <DayPicker value={due} min={today} weekStart={weekStart} onPick={setDue} active={!chipOn}>
            <CalendarDays className="h-4 w-4" aria-hidden="true" /><Trans>Pick a day</Trans>
          </DayPicker>
        </div>
        <p className="mt-2 px-2 text-[14px] text-muted-foreground">{longDate(due)}</p>
      </div>
      {showFor && (
        <div className="min-h-[88px] rounded-xl bg-secondary/55 px-4 py-3" aria-live="polite">
          {p ? <Preview p={p} today={today} /> : previewFailed ? <p className="text-[14px] text-muted-foreground"><Trans>Nothing left to read there.</Trans></p> : <p className="text-[14px] text-muted-foreground"><Trans>Working out what it takes…</Trans></p>}
        </div>
      )}
    </div>
  )
}

function Preview({ p, today }: { p: FinishEstimate; today: string }) {
  const due = shortDate(p.due_date, today)
  const days = num(p.days_left)
  if (p.volumes_left === 0) return <p className="text-[14px] text-foreground"><Trans>Everything there is read already.</Trans></p>
  const started = p.in_progress[0]
  const unstarted = p.volumes_left - p.in_progress.length
  let what: string
  if (p.scope === 'book') what = started ? (() => { const pc = num(Math.round((1 - started.progress) * 100)); return t`the ${pc}% you have left` })() : t`the whole book`
  else if (started) {
    const v = started.series_index != null ? num(started.series_index) : started.title
    const pc = num(Math.round((1 - started.progress) * 100))
    what = unstarted > 0 ? (() => { const more = plural(unstarted, { one: '# more volume', other: '# more volumes' }); return t`the rest of volume ${v} (${pc}%) and ${more}` })() : t`the rest of volume ${v} (${pc}%)`
  } else what = plural(p.volumes_left, { one: '# volume', other: '# volumes' })
  const left = p.remaining_seconds != null ? formatDuration(p.remaining_seconds) : null
  const per = p.needed_seconds_per_day != null ? num(Math.max(1, Math.round(p.needed_seconds_per_day / 60))) : null
  const recent = num(Math.round(p.recent_seconds_per_day / 60))
  const recentDays = num(p.recent_days)
  return (
    <>
      {left ? (
        <p className="text-[14px] leading-snug text-foreground"><Trans>About <b className="font-semibold">{left}</b> of reading left: {what}.</Trans></p>
      ) : (
        <p className="text-[14px] leading-snug text-foreground"><Trans>Left to read: {what}. Some of it has no estimate yet.</Trans></p>
      )}
      {per && <p className="mt-1 text-[14px] leading-snug text-foreground"><Trans>That is about <b className="font-semibold">{per} min a day</b> for the {days} days to {due}.</Trans></p>}
      <p className="mt-1.5 text-[12px] leading-snug text-muted-foreground">
        {p.basis === 'series' && p.per_volume_seconds ? (() => { const v = formatDuration(p.per_volume_seconds); return <Trans>At your {v} a volume in this series.</Trans> })() : <Trans>From the word count at your reading speed.</Trans>}{' '}
        <Trans>You've read {recent} min a day over the last {recentDays} days.</Trans>
      </p>
    </>
  )
}
