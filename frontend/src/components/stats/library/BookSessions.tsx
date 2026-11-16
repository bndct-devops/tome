// One book's sessions, opened in place under its row of the All books table
// (#38, the old table's per-book drill-down): newest first, all time, counted or
// not, with the same trim and delete as the Sessions tab (sessions/actions.ts,
// sessions/SessionTools.tsx). Opened by the reader, so it may push the rows
// below it down; it never moves anything on its own.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Trans } from '@lingui/react/macro'
import { t, plural } from '@lingui/core/macro'
import { ChevronDown, Loader2, TriangleAlert } from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatDuration } from '@/lib/utils'
import type { SessionEntry, SessionsPage as SessionsAnswer } from '@/components/stats/shared'
import { Ph } from '@/components/stats/kit/Kit'
import { HAIR, num } from '@/components/stats/kit/tokens'
import { dropStatsCache } from '@/components/stats/kit/useStatsGet'
import { sourceLabel, useSessionActions } from '@/components/stats/sessions/actions'
import { SessionTools, TrimPanel } from '@/components/stats/sessions/SessionTools'

const PAGE = 10
/* eslint-disable lingui/no-unlocalized-strings -- placeholder text and CSS */
const PH = { when: 'Nov 11, 2026, 08:27 AM', dur: '1h 19m', dev: 'KOReader', status: 'Imported' }
const COLS = 'grid-cols-[190px_100px_minmax(0,1fr)_100px_72px]'
const PHONE_TOOL = 'h-8 w-7'
const ARRIVE = 'motion-safe:animate-[k-fade_200ms_cubic-bezier(0.2,0.8,0.2,1)_both]'
/* eslint-enable lingui/no-unlocalized-strings */

const tz = () => new Date().getTimezoneOffset()
const query = (bookId: number, offset: number, limit: number) =>
  `/stats/sessions?book_id=${bookId}&sort=recent&counted=all&limit=${limit}&offset=${offset}&tz_offset=${tz()}`
const whenOf = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-'

type View = { total: number; counted: number; sessions: SessionEntry[] }

/** `id` names the panel for the row's toggle (aria-controls). `onChanged` after a trim or delete. */
export function BookSessions({ id, bookId, desktop, onChanged }: { id: string; bookId: number; desktop: boolean; onChanged?: () => void }) {
  const [view, setView] = useState<View | null>(null)
  const [failed, setFailed] = useState(false)
  const [more, setMore] = useState(false)
  const seq = useRef(0)

  const load = useCallback((shown: number) => {
    const n = ++seq.current
    const want = Math.max(PAGE, shown)
    const pages: Promise<SessionsAnswer>[] = []
    for (let off = 0; off < want; off += 200) pages.push(api.get<SessionsAnswer>(query(bookId, off, Math.min(200, want - off))))
    Promise.all(pages)
      .then((rs) => {
        if (n !== seq.current) return
        const last = rs[rs.length - 1]
        setView({ total: last.total, counted: last.counts.counted, sessions: rs.flatMap((r) => r.sessions) })
      })
      .catch(() => { if (n === seq.current) setFailed(true) })
  }, [bookId])
  useEffect(() => { load(PAGE) }, [load])

  const showMore = () => {
    if (!view || more) return
    setMore(true)
    const n = seq.current
    api.get<SessionsAnswer>(query(bookId, view.sessions.length, PAGE))
      .then((r) => { if (n === seq.current) setView((v) => v && { ...v, total: r.total, sessions: [...v.sessions, ...r.sessions] }) })
      .catch(() => {})
      .finally(() => setMore(false))
  }

  const a = useSessionActions({
    onTrimmed: (s, seconds) => {
      dropStatsCache('/stats')
      setView((v) => v && { ...v, sessions: v.sessions.map((r) => (r.id === s.id ? { ...r, duration_seconds: seconds, suspect: false, suggested_seconds: null } : r)) })
      load(view?.sessions.length ?? PAGE)
      onChanged?.()
    },
    onDeleted: (s) => {
      dropStatsCache('/stats')
      const shown = view?.sessions.length ?? PAGE
      setView((v) => v && { ...v, total: v.total - 1, sessions: v.sessions.filter((r) => r.id !== s.id) })
      load(Math.max(PAGE, shown - 1))
      onChanged?.()
    },
  })

  const remaining = view ? Math.max(0, view.total - view.sessions.length) : 0
  const indent = desktop ? 'pl-[45px]' : 'pl-[41px]'
  return (
    <div id={id} role="region" aria-label={t`Sessions of this book`} aria-busy={!view && !failed} className={cn('border-t pb-4 pt-2', HAIR, indent, ARRIVE)}>
      <div className="flex h-7 items-center text-[13px] text-muted-foreground">
        {view ? (() => {
          const all = plural(view.total, { one: '# session', other: '# sessions' })
          const c = num(view.counted)
          return view.counted === view.total ? <Trans>{all}, all time, newest first</Trans> : <Trans>{all}, {c} counted, all time, newest first</Trans>
        })() : failed ? <Trans>Could not load the sessions.</Trans> : <Ph on>{PH.when}</Ph>}
      </div>
      {!view && !failed && <Row desktop={desktop} />}
      {view && view.sessions.length === 0 && <p className="flex h-11 items-center text-[14px] text-muted-foreground"><Trans>No sessions recorded for this book.</Trans></p>}
      {view?.sessions.map((s) => <Row key={s.id} s={s} desktop={desktop} a={a} />)}
      {remaining > 0 && (
        <button type="button" onClick={showMore} disabled={more} className="mt-2 inline-flex h-8 items-center gap-1.5 text-[14px] font-medium text-primary underline-offset-4 hover:underline disabled:opacity-60">
          {more ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ChevronDown className="h-3.5 w-3.5" />}
          {(() => { const left = num(remaining); return <Trans>Show more ({left} remaining)</Trans> })()}
        </button>
      )}
    </div>
  )
}

function Row({ s, desktop, a }: { s?: SessionEntry; desktop: boolean; a?: ReturnType<typeof useSessionActions> }) {
  if (!s || !a) {
    return desktop ? (
      <div className={cn('grid h-11 items-center gap-x-4 border-t text-[14px]', HAIR, COLS)}>
        <span><Ph on>{PH.when}</Ph></span><span><Ph on>{PH.dur}</Ph></span><span><Ph on>{PH.dev}</Ph></span><span><Ph on>{PH.status}</Ph></span><span />
      </div>
    ) : (
      <div className={cn('flex h-[52px] items-center border-t text-[13px]', HAIR)}><Ph on>{`${PH.when} · ${PH.dur}`}</Ph></div>
    )
  }
  const dur = s.duration_seconds != null ? formatDuration(s.duration_seconds) : '-'
  const when = whenOf(s.started_at)
  const status = s.kind === 'imported' ? t`Imported` : !s.counted ? t`Not counted` : ''
  const statusTitle = s.kind === 'imported'
    ? t`Imported from KOReader reading history`
    : !s.counted ? t`Not in totals: this book's imported KOReader history already covers this reading` : undefined
  const warn = s.suspect && <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-warning" aria-label={t`Unusually long session`} />
  const device = sourceLabel(s.device)
  const pages = s.pages_turned ? plural(s.pages_turned, { one: '# page', other: '# pages' }) : null
  return (
    <div className={cn('border-t', HAIR, ARRIVE)}>
      {desktop ? (
        <div className={cn('grid h-11 items-center gap-x-4 text-[14px]', COLS)}>
          <span className="whitespace-nowrap tabular-nums text-muted-foreground">{when}</span>
          <span className="inline-flex items-center gap-1.5 whitespace-nowrap tabular-nums text-foreground">{dur}{warn}</span>
          <span className="truncate text-muted-foreground">{pages ? `${device} · ${pages}` : device}</span>
          <span className="whitespace-nowrap text-[13px] text-muted-foreground/70" title={statusTitle}>{status}</span>
          <span className="flex justify-end gap-0.5"><SessionTools s={s} a={a} keepSlot /></span>
        </div>
      ) : (
        <div className="flex h-[52px] items-center gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-[14px] tabular-nums text-foreground">{dur}{warn}<span className="truncate text-[12px] text-muted-foreground">{device}{status && <> · <span className="text-muted-foreground/70" title={statusTitle}>{status}</span></>}</span></div>
            <div className="mt-[2px] truncate text-[12px] tabular-nums text-muted-foreground">{when}</div>
          </div>
          <span className="flex shrink-0"><SessionTools s={s} a={a} keepSlot buttonClass={PHONE_TOOL} /></span>
        </div>
      )}
      <TrimPanel s={s} a={a} />
    </div>
  )
}
