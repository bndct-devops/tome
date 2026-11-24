// New goal and Edit goal, kept short enough to need no scrolling. Creating: the
// eight kinds as one row of chips (the ones already set dimmed with their
// progress, and not picked twice), then the target: three suggested from the
// reader's own reading (/api/goals/suggestions: "on 46 of 90 days") or the
// presets without history, the number, the book type it counts ("All books",
// beside the number) and one line on what it means. Finish by a date: a book or
// series, a day (End of November, End of the year, or any day) and what it
// takes, live from /api/goals/finish-preview. The area under the kinds keeps
// one height for every kind. Editing: the target, or a finish-by goal's date.
// From a series or a book page the dialog comes small: that title, the day and
// what it takes (no kinds, no search). Focus goes into the dialog when it opens
// and back to whatever opened it when it closes.
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { CalendarDays, ChevronDown } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { cn, formatDuration } from '@/lib/utils'
import { ModalShell } from '@/components/ModalShell'
import { Glide, GlideGroup, Input, Pill, PillGroup } from '@/components/page'
import { GLIDE_FILL, useIndicatorSplit } from '@/components/page/glideTransition'
import { Cover } from '@/components/stats/kit/Kit'
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
import { goalName, kindLabel, kindNote, longDate, minutesText, perDayText, shortDate, targetUnit } from './text'

export type DialogMode =
  | { kind: 'create'; preset?: { kind: GoalKind; target?: number } }
  /** Finish this series or book by a day (its page's "Finish by…"): only the day and what it takes. */
  | { kind: 'finish'; target: FinishTarget }
  | { kind: 'edit'; goal: Goal }

const BOUNDS: Record<GoalKind, { min: number; max: number | null }> = {
  books_per_year: { min: 1, max: null }, books_per_month: { min: 1, max: null },
  minutes_per_day: { min: 1, max: 1440 }, minutes_per_week: { min: 1, max: 10080 },
  pages_per_day: { min: 1, max: null }, pages_per_week: { min: 1, max: null },
  days_per_week: { min: 1, max: 7 }, finish_by: { min: 1, max: null },
}
const SCOPED = new Set<GoalKind>(['books_per_year', 'books_per_month', 'minutes_per_day', 'minutes_per_week', 'pages_per_day', 'pages_per_week'])

/** The figure line a kind's tile shows when the reader already has it: "58 / 60". */
const progress = (g: Goal) => `${num(Math.round(g.current))} / ${num(g.target)}`

export function GoalDialog({ mode, goals, onClose, onSaved }: { mode: DialogMode; goals: Goal[]; onClose: () => void; onSaved?: (g: Goal) => void }) {
  const editing = mode.kind === 'edit' ? mode.goal : null
  const fixed = mode.kind === 'finish' ? mode.target : null
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
  const [kind, setKind] = useState<GoalKind>(editing?.kind ?? (fixed ? 'finish_by' : mode.kind === 'create' && mode.preset?.kind ? mode.preset.kind : firstFree))
  const [scope, setScope] = useState<number | null>(() => editing?.book_type_id ?? (freeScopes(kind)[0] ?? null))
  const [raw, setRaw] = useState<string | null>(editing && editing.kind !== 'finish_by' ? String(editing.target) : mode.kind === 'create' && mode.preset?.target ? String(mode.preset.target) : null)
  const [finish, setFinish] = useState<FinishTarget | null>(fixed)
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

  // Focus: into the dialog on open, back to the opener on close (when it is still there).
  const body = useRef<HTMLDivElement>(null)
  // How far the panel is moved down so its top stays where it opened while its height changes.
  const [shift, setShift] = useState<{ y: number; animate: boolean }>({ y: 0, animate: false })
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const id = requestAnimationFrame(() => {
      body.current?.querySelector<HTMLElement>('[aria-checked="true"], [aria-pressed="true"], button:not([disabled]), input')?.focus({ preventScroll: true })
    })
    return () => {
      cancelAnimationFrame(id)
      if (opener?.isConnected) opener.focus({ preventScroll: true })
    }
  }, [])

  const typeLabel = (id: number | null) => (id == null ? t`All books` : (() => { const l = types.find((x) => x.id === id)?.label ?? ''; return t`${l} only` })())

  return createPortal(
    <ModalShell
      open
      onClose={onClose}
      size={fixed ? 'sm' : 'md'}
      // The small dialog: the two day chips and "Pick a day" on one line.
      className={fixed ? 'max-w-[500px]' : undefined}
      // `top`, not `translate`: it and the area's height are both laid out on the main thread and
      // start in the same frame, so they stay in step and the top edge holds (a `translate` runs on
      // the compositor and can start frames apart from the height, and the panel bobbed by tens of px).
      panelStyle={{ top: shift.y, transition: shift.animate ? STEP_TOP : undefined }}
      eyebrow={fixed ? kindLabel('finish_by') : undefined}
      title={fixed ? <FixedTitle x={fixed} /> : editing ? t`Edit goal` : t`New goal`}
      aria-label={fixed ? (() => { const n = targetName(fixed); return t`Finish ${n} by a date` })() : editing ? t`Edit goal` : t`New goal`}
      subtitle={editing ? goalName(editing, today) : undefined}
      footer={
        <>
          <Pill variant="quiet" onClick={onClose}><Trans>Cancel</Trans></Pill>
          <Pill variant="primary" onClick={save} disabled={!canSave}>{editing ? t`Save` : t`Set goal`}</Pill>
        </>
      }
    >
      <div ref={body} className="space-y-5">
        {!editing && !fixed && (
          // The kinds as one row of chips (no descriptions): a kind already set is dimmed with its
          // progress; one with no book type left free for it cannot be picked.
          <PillGroup
            aria-label={t`Goal`}
            value={kind}
            onChange={pickKind}
            className="flex gap-2"
            pillClassName="inline-flex items-center px-3.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            options={KIND_ORDER.map((k) => {
              const on = k === kind
              const mine = k !== 'finish_by' ? goals.find((g) => g.kind === k && g.book_type_id == null) : undefined
              return {
                value: k,
                'aria-disabled': taken(k),
                dimmed: !!mine,
                title: mine ? (() => { const p = progress(mine); return t`You have this one · ${p}` })() : kindNote(k),
                label: (
                  <span className="inline-flex items-center gap-1.5">
                    {kindLabel(k)}
                    {mine && (
                      <span data-split="" style={PROGRESS_SPLIT} className={cn('text-[12px] tabular-nums', on ? 'text-background/70' : 'text-muted-foreground')}>
                        <span className="sr-only">{t`You have this one`} · </span>{progress(mine)}
                      </span>
                    )}
                  </span>
                ),
              }
            })}
          />
        )}

        {/* What the kind needs. Creating, the seven count kinds share one height and Finish by a
            date has its own: switching between the two changes the dialog's height once (Stepped). */}
        <Stepped
          on={!editing && !fixed}
          finish={kind === 'finish_by'}
          onShift={setShift}
          ruler={!editing && !fixed ? KIND_ORDER.filter((k) => k !== 'finish_by').map((k) => {
            const ks = sug?.kinds[k]
            const v = ks?.suggested?.[1]?.value ?? ks?.presets?.[1]?.value ?? info.get(k)?.presets?.[1] ?? 1
            return (
              <div key={k} className="col-start-1 row-start-1">
                <TargetBody kind={k} s={ks} sug={sug} text={String(v)} setText={() => {}} parsed={parseTarget(String(v), BOUNDS[k].min, BOUNDS[k].max)} onEnter={() => {}} counts={null} ruler />
              </div>
            )
          }) : null}
        >
          {kind === 'finish_by' ? (
            <FinishBody
              editing={editing}
              fixed={!!fixed}
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
            <TargetBody
              kind={kind}
              s={s}
              sug={sug}
              text={text}
              setText={setRaw}
              parsed={parsed}
              onEnter={save}
              counts={!editing && SCOPED.has(kind) && types.length > 0 ? (
                <ChoiceMenu
                  value={String(scope)}
                  options={freeScopes(kind).map((id) => ({ id: String(id), label: typeLabel(id) }))}
                  onPick={(id) => setScope(id === 'null' ? null : Number(id))}
                  label={t`Counts`}
                  align="end"
                  className="inline-flex h-10 items-center gap-1.5 rounded-xl px-3 text-[14px] font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {typeLabel(scope)}
                  <ChevronDown className="h-4 w-4" aria-hidden="true" />
                </ChoiceMenu>
              ) : null}
            />
          )}
          {!editing && kind !== 'finish_by' && has(kind, scope) && (
            <p className="text-[14px] text-muted-foreground"><Trans>You already have this goal. Edit it from its card instead.</Trans></p>
          )}
          {error && <p role="alert" className="text-[14px] text-destructive">{error}</p>}
        </Stepped>
      </div>
    </ModalShell>,
    document.body,
  )
}

// ── The area under the kinds ──────────────────────────────────────────────────

// eslint-disable-next-line lingui/no-unlocalized-strings -- CSS values
const PROGRESS_SPLIT = { '--split-out': 'var(--muted-foreground)', '--split-in': 'color-mix(in oklab, var(--background) 70%, var(--foreground))' } as CSSProperties
/** A chip's muted third line: its colours on and off the indicator (useIndicatorSplit). */
// eslint-disable-next-line lingui/no-unlocalized-strings -- CSS values
const NOTE_SPLIT = { '--split-out': 'var(--muted-foreground)', '--split-in': 'color-mix(in oklab, var(--background) 75%, var(--foreground))' } as CSSProperties

/* eslint-disable lingui/no-unlocalized-strings -- CSS values and a media query */
const STEP_TOP = 'top 200ms ease-out'
const STEP_HEIGHT = 'height 200ms ease-out'
const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
/* eslint-enable lingui/no-unlocalized-strings */

/**
 * Creating: the area under the kinds at one of two heights, the count kinds' (all seven
 * alike, as short as they need) or Finish by a date's. A switch between them animates the
 * height once (at once with reduced motion); `onShift` moves the panel, which is centred on
 * the screen, by half the change, so its top edge (the title and the kinds) stays put and
 * the dialog grows or shrinks at the bottom only. Editing and in the small dialog there is no
 * ruler, but a line that comes or goes (an error, a longer estimate) glides the height the same way.
 */
function Stepped({ on, finish, onShift, ruler, children }: {
  on: boolean
  finish: boolean
  onShift: (s: { y: number; animate: boolean }) => void
  /** Every count kind's body, unseen in one grid cell: its height is the count kinds' height. */
  ruler: ReactNode
  children: ReactNode
}) {
  const outer = useRef<HTMLDivElement>(null)
  const inner = useRef<HTMLDivElement>(null)
  const rulerRef = useRef<HTMLDivElement>(null)
  const [countH, setCountH] = useState<number | null>(null)
  useLayoutEffect(() => {
    if (!on || !rulerRef.current) return
    const el = rulerRef.current
    // Layout sizes (offsetHeight): the panel is scaled while it opens.
    const read = () => setCountH(el.offsetHeight)
    read()
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [on])
  const [h, setH] = useState<number | null>(null)
  const [moving, setMoving] = useState(false)
  const last = useRef<number | null>(null)
  // The panel's height before any change: the height its top edge was centred for.
  const base = useRef<number | null>(null)
  useLayoutEffect(() => {
    if (!inner.current) return
    const el = inner.current
    const apply = () => {
      const next = el.offsetHeight
      const prev = last.current
      last.current = next
      if (prev === next) return
      const panel = el.closest<HTMLElement>('[role="dialog"]')
      if (prev == null) base.current = panel?.offsetHeight ?? null
      else if (panel && outer.current && base.current != null) {
        // The panel's height once this change is done, from layout sizes: a change can come while
        // an earlier one is still under way (the suggestions arriving, then a kind picked), and the
        // panel's drawn size or place then is neither the old one nor the new.
        const to = panel.offsetHeight - outer.current.offsetHeight + next
        // Centred, the panel's top would move up by half the change: move it down as much, so the
        // top stays; growing, never so far that its bottom leaves the screen.
        const room = (window.innerHeight - to) / 2 - 16
        const y = room <= 0 ? 0 : Math.max(-room, Math.min((to - base.current) / 2, room))
        const animate = !reducedMotion()
        setMoving(animate)
        onShift({ y, animate })
      }
      setH(next)
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [onShift])
  return (
    <div
      ref={outer}
      style={h != null ? { height: h, transition: moving ? STEP_HEIGHT : undefined } : undefined}
      // Clipped only while it moves (focus rings at its edges show otherwise).
      className={cn('relative', moving && 'overflow-hidden')}
      onTransitionEnd={(e) => { if (e.target === outer.current) setMoving(false) }}
    >
      <div
        ref={inner}
        style={on && !finish && countH != null ? { minHeight: countH } : undefined}
        className={cn('space-y-3', on && finish && 'min-h-[296px] sm:min-h-[220px]')}
      >
        {children}
      </div>
      {/* Unseen, unreachable and out of the reading order: only measured (data-ruler marks it). */}
      {on && (
        <div ref={rulerRef} data-ruler="" className="pointer-events-none invisible absolute inset-x-0 top-0 grid" aria-hidden="true" inert>
          {ruler}
        </div>
      )}
    </div>
  )
}

// ── The target of a count goal ────────────────────────────────────────────────

function chipNote(s: KindSuggestion, c: Chip): string {
  if (c.reached == null || !c.of) return ''
  const r = num(c.reached), o = num(c.of)
  return s.over === 'weeks' ? t`in ${r} of ${o} weeks` : t`on ${r} of ${o} days`
}

const CHIP_TITLE = { steady: () => t`Steady`, keep_it_up: () => t`Keep it up`, stretch: () => t`Stretch` } as const

function TargetBody({ kind, s, sug, text, setText, parsed, onEnter, counts, ruler }: {
  kind: GoalKind
  s: KindSuggestion | undefined
  sug: Suggestions | null
  text: string
  setText: (v: string) => void
  parsed: ReturnType<typeof parseTarget>
  onEnter: () => void
  /** The book type it counts ("All books"), beside the number; null where it does not apply. */
  counts: ReactNode
  /** An unseen copy that only measures (no id). */
  ruler?: boolean
}) {
  const value = parsed.ok ? parsed.value : null
  const cardsRef = useRef<HTMLDivElement>(null)
  useIndicatorSplit(cardsRef, ruler ? null : value)
  // The server's three can share a number (steady reading, rounding to a step, a 7-day cap):
  // each number is offered once, under the first of its names (Steady before Keep it up before
  // Stretch); a lone one keeps no name.
  const chips = s?.suggested ? s.suggested.filter((c, i, all) => all.findIndex((o) => o.value === c.value) === i) : null
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
      // Minutes per book finished in the 90 days, else the median time per finished book.
      const b = booksAMonth(value, per, kind.startsWith('minutes') ? (sug.per_book.minutes ?? sug.per_book.median_minutes ?? null) : sug.per_book.pages)
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
        const v = num(value), mm = minutesText(m)
        consequence = kind === 'books_per_month'
          ? <Trans>{v} books a month take about <b className="font-semibold">{mm} a day</b> at your usual time per book.</Trans>
          : <Trans>{v} books a year take about <b className="font-semibold">{mm} a day</b> at your usual time per book.</Trans>
      }
    } else if (kind === 'days_per_week') {
      consequence = <Trans>A day counts as it does for your streak.</Trans>
    }
  }
  return (
    <div className="space-y-3">
      {chips ? (
        // Three across at every width: the number first, what it is, how often you reached it.
        // One indicator per kind (keyed): a new kind's chips draw it in place, a pick within them glides it.
        <GlideGroup key={kind} value={ruler ? undefined : value}>
        <div ref={ruler ? undefined : cardsRef} className="isolate grid grid-cols-3 gap-2" role="radiogroup" aria-label={t`Suggested targets`}>
          {chips.map((c) => {
            const on = value === c.value
            return (
              <button
                key={c.label}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => setText(String(c.value))}
                className="group/chip relative min-w-0 rounded-xl px-3 py-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {/* Nothing in a chip is cut: a long note wraps and the chip grows (the three stay one height). */}
                <span data-split="" className={cn('relative z-[1] block text-[17px] font-bold leading-6 tabular-nums [overflow-wrap:anywhere]', on ? 'text-background' : 'text-foreground')}>{num(c.value)}</span>
                {chips.length > 1 && <span data-split="" className={cn('relative z-[1] block text-[13px] font-semibold leading-5', on ? 'text-background' : 'text-foreground')}>{CHIP_TITLE[c.label ?? 'keep_it_up']()}</span>}
                <span data-split="" style={NOTE_SPLIT} className={cn('relative z-[1] block text-[12px] leading-snug [overflow-wrap:anywhere]', on ? 'text-background/75' : 'text-muted-foreground')}>{chipNote(s!, c)}</span>
                {/* Last, so a chip's first span is still its number. */}
                {/* 14 px: the card's rounded-xl, so its corners match the card it leaves and the one it reaches. */}
                {on && !ruler && <Glide className="bg-foreground" radius={14} />}
                <span aria-hidden className={cn(GLIDE_FILL, 'group-hover/chip:bg-surface-hover')} />
              </button>
            )
          })}
        </div>
        </GlideGroup>
      ) : (
        <PillGroup
          key={kind}
          aria-label={t`Targets`}
          value={value != null ? String(value) : ''}
          onChange={(v) => setText(v)}
          options={presets.map((c) => ({ value: String(c.value), label: <span className="font-semibold tabular-nums">{num(c.value)}</span> }))}
        />
      )}
      <div className="flex items-center gap-2.5">
        <Input
          id={ruler ? undefined : 'goal-target'}
          inputMode="numeric"
          autoComplete="off"
          aria-label={t`Target`}
          value={text}
          invalid={!!why}
          aria-invalid={!!why}
          aria-describedby={why ? 'goal-target-why' : undefined}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') onEnter() }}
          className="h-10 w-20 shrink-0 rounded-xl text-[15px] font-semibold tabular-nums"
        />
        <span className="min-w-0 text-[14px] text-muted-foreground">{unit}</span>
        {counts && <span className="ml-auto shrink-0">{counts}</span>}
      </div>
      {why ? <p id="goal-target-why" className="text-[13px] text-destructive">{why}</p>
        : consequence ? <p className="text-[14px] leading-snug text-foreground">{consequence}</p> : null}
    </div>
  )
}

// ── Finish by a date ──────────────────────────────────────────────────────────

const targetName = (x: FinishTarget) => ('series' in x ? x.series : x.title)

/** The small dialog's title: the cover and the name of what is to be finished. */
function FixedTitle({ x }: { x: FinishTarget }) {
  return (
    <span className="flex items-center gap-3">
      <Cover id={x.cover} h={42} className="rounded-[5px]" />
      <span className="min-w-0">{targetName(x)}</span>
    </span>
  )
}

function FinishBody({ editing, fixed, today, weekStart, target, setTarget, taken, due, setDue, preview, previewFailed }: {
  editing: Goal | null
  /** The book or series came with the dialog: no picker. */
  fixed: boolean
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
  const dayRow = useRef<HTMLDivElement>(null)
  useIndicatorSplit(dayRow, chipOn?.id ?? 'day')
  return (
    <div className="space-y-4">
      {!editing && !fixed && (
        <FinishTargetPicker value={target} onPick={setTarget} taken={new Set([...taken].filter((k) => !target || k !== targetKey(target)))} />
      )}
      <GlideGroup value={chipOn?.id ?? 'day'}>
      <div ref={dayRow} className="isolate flex flex-wrap items-center gap-1.5" role="group" aria-label={t`By`}>
        {chips.map((c) => {
          const on = chipOn?.id === c.id
          return (
            <button
              key={c.id}
              type="button"
              aria-pressed={on}
              title={longDate(c.date)}
              onClick={() => setDue(c.date)}
              className={cn('group/chip relative inline-flex h-9 items-center rounded-full px-4 text-[14px] font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-ring', on ? 'text-background' : 'text-foreground')}
            >
              {on && <Glide className="bg-foreground" />}
              <span data-split="" className="relative z-[1]">{c.label}</span>
              <span aria-hidden className={cn(GLIDE_FILL, 'group-hover/chip:bg-surface-hover')} />
            </button>
          )
        })}
        {/* A day of your own shows on this chip itself. */}
        <DayPicker value={due} min={today} weekStart={weekStart} onPick={setDue} active={!chipOn} indicator={<Glide className="bg-foreground" />}>
          <CalendarDays className="h-4 w-4" aria-hidden="true" />{chipOn ? <Trans>Pick a day</Trans> : shortDate(due, today)}
        </DayPicker>
      </div>
      </GlideGroup>
      {/* What it takes, in a box of one size whatever is in it (also before a book or series is picked). */}
      <div className="min-h-[96px] rounded-xl bg-secondary/55 px-4 py-3" aria-live="polite">
        {!(editing || target) ? <p className="text-[14px] text-muted-foreground"><Trans>Pick a book or a series to see what it takes a day.</Trans></p>
          : p ? <Preview p={p} today={today} /> : previewFailed ? <p className="text-[14px] text-muted-foreground"><Trans>Nothing left to read there.</Trans></p> : <p className="text-[14px] text-muted-foreground"><Trans>Working out what it takes…</Trans></p>}
      </div>
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
  const per = p.needed_seconds_per_day != null ? perDayText(p.needed_seconds_per_day) : null
  const recent = minutesText(p.recent_seconds_per_day / 60)
  const recentDays = num(p.recent_days)
  return (
    <>
      {left ? (
        <p className="text-[14px] leading-snug text-foreground"><Trans>About <b className="font-semibold">{left}</b> of reading left: {what}.</Trans></p>
      ) : (
        <p className="text-[14px] leading-snug text-foreground"><Trans>Left to read: {what}. Some of it has no estimate yet.</Trans></p>
      )}
      {per && <p className="mt-1 text-[14px] leading-snug text-foreground"><Trans>That is about <b className="font-semibold">{per} a day</b> for the {days} days to {due}.</Trans></p>}
      <p className="mt-1.5 text-[12px] leading-snug text-muted-foreground">
        {p.basis === 'series' && p.per_volume_seconds ? (() => { const v = formatDuration(p.per_volume_seconds); return <Trans>At your {v} a volume in this series.</Trans> })() : <Trans>From the word count at your reading speed.</Trans>}{' '}
        <Trans>You've read {recent} a day over the last {recentDays} days.</Trans>
      </p>
    </>
  )
}
