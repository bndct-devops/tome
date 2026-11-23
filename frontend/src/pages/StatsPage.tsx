import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { HelpCircle, ImageDown, Loader2, MoreHorizontal, Plus } from 'lucide-react'
import { Trans } from '@lingui/react/macro'
import { t, msg } from '@lingui/core/macro'
import { i18n } from '@lingui/core'
import type { MessageDescriptor } from '@lingui/core'
import { AppShell } from '@/components/AppShell'
import { Glide, GlideGroup, PageBody, PageHeader, Pill, PRESS, PRESS_LABEL } from '@/components/page'
import { Pop } from '@/components/motion'
import { BookAnimation } from '@/components/BookAnimation'
import { DOCS, docsLink } from '@/lib/docs'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { boardsTabVisible, customBoardIds, readShowBoards } from '@/lib/statsBoards'
import type { CompletionEstimate, StatsResponse } from '@/components/stats/shared'
import { RangeControl } from '@/components/stats/RangeControl'
import { fmtDay, RANGES, type CustomRange } from '@/components/stats/range'
import { Overview } from '@/components/stats/overview/Overview'
import { FullBleedTimeline } from '@/components/stats/FullBleedTimeline'
import { HabitsPage } from '@/components/stats/habits/HabitsPage'
import { LibraryPage } from '@/components/stats/library/LibraryPage'
import { TastePage } from '@/components/stats/taste/TastePage'
import { SessionsPage } from '@/components/stats/sessions/SessionsPage'
import { GoalsTab } from '@/components/goals/GoalsTab'
import { saveSelection } from '@/components/goals/actions'
import type { StatsTab } from '@/components/stats/kit/page'
import { beginStatsVisit, dropStatsCache, statsCacheGet, statsFetch, statsFresh, statsPrefetch, useDelayed } from '@/components/stats/kit/useStatsGet'
import { TipLayer } from '@/components/stats/kit/TipLayer'
import { EASE, finishEntries, reducedMotion } from '@/components/stats/kit/motion'
import { PAGE_W, loadBoardsFlag, loadLocalDashboard, saveBoardsFlag, saveLocalDashboard, type Persisted } from '@/components/stats/boards/types'
import { tipAttrs } from '@/components/stats/kit/tip'

/**
 * Reading Stats. Designed pages (Overview, and the others as they arrive) with a
 * fixed layout that is drawn before its data, plus the user's own boards from
 * the earlier customisable dashboard under Boards. The grid behind Boards (and
 * behind the default boards that stand in for the pages not designed yet) loads
 * on demand, so the Overview never pays for it.
 */

// The grid and its tile catalog: one chunk, loaded only for the Boards tab.
const BoardsView = lazy(() => import('@/components/stats/boards/BoardsView'))

type TabId = StatsTab
// Tabs drawn from the shared /api/stats answer; Timeline and Sessions have their own requests.
const NEEDS_STATS = new Set<TabId>(['overview', 'habits', 'library', 'taste', 'boards'])
const PAGES: { id: TabId; label: MessageDescriptor }[] = [
  { id: 'overview', label: msg`Overview` },
  { id: 'goals', label: msg`Goals` },
  { id: 'habits', label: msg`Habits` },
  { id: 'library', label: msg`Library` },
  { id: 'taste', label: msg`Taste` },
  { id: 'timeline', label: msg`Timeline` },
  { id: 'sessions', label: msg`Sessions` },
  { id: 'boards', label: msg`Boards` },
]

// Stats answers by range are kept for the session (in the pages' shared cache, so
// an edit such as a deleted session drops them with the pages' own answers):
// coming back to the page is instant, the fresh answer replaces it when it arrives.
// One request per URL while it is in flight (statsFetch), also across tab switches mid-load.
const statsUrl = (key: string) => `/stats?${key}&tz_offset=${new Date().getTimezoneOffset()}`
const statsCache = {
  get: (key: string) => statsCacheGet<StatsResponse>(`/stats?${key}`),
  fresh: (key: string) => statsFresh(`/stats?${key}`),
  fetch: (key: string) => statsFetch<StatsResponse>(statsUrl(key), `/stats?${key}`),
}

// What the page shows (tab, range, board) is this browser's own view state. It
// is kept apart from the saved boards so that looking around never writes them.
const VIEW_KEY = 'tome_stats_view'
type ViewState = { tab: TabId; days: number; board: string | null }
function loadView(): ViewState {
  // A link into a tab (Home's goal cards: /stats?tab=goals&goal=3) wins over the remembered view.
  const q = new URLSearchParams(window.location.search)
  const linked = q.get('tab')
  const goal = Number(q.get('goal'))
  if (Number.isInteger(goal) && goal > 0) saveSelection({ id: goal, kind: q.get('kind') ?? '' })
  // Followed once: a reload later shows the view the reader has moved on to.
  if (linked) window.history.replaceState(window.history.state, '', window.location.pathname)
  let saved: Partial<ViewState> = {}
  try {
    saved = JSON.parse(localStorage.getItem(VIEW_KEY) ?? '{}') ?? {}
  } catch {
    saved = {}
  }
  const tab = PAGES.some((p) => p.id === linked) ? (linked as TabId) : PAGES.some((p) => p.id === saved.tab) ? (saved.tab as TabId) : 'overview'
  // First visit with this key: keep the range the previous page remembered here.
  const days = typeof saved.days === 'number' ? saved.days : loadLocalDashboard()?.days ?? 30
  return { tab, days: typeof days === 'number' ? days : 30, board: typeof saved.board === 'string' ? saved.board : null }
}

const rangeKey = (days: number, custom: CustomRange | null) => (custom ? `start=${custom.start}&end=${custom.end}` : `days=${days}`)

// Waits a moment before it shows (a chunk or an answer that lands sooner shows nothing); its place is kept.
function Spinner() {
  const late = useDelayed(true)
  return (
    <div className="flex justify-center py-32">
      <span className="block h-10 w-10">{late && <BookAnimation variant="refresh" className="block h-10 w-10 text-primary" />}</span>
    </div>
  )
}

// A tab switch: the new tab is drawn at once and rises from partial opacity (opacity
// only, on the content's wrapper, which is no box), so nothing waits and nothing moves.
const TAB_SWAP: Keyframe[] = [{ opacity: 0.55 }, { opacity: 1 }]
const TAB_SWAP_MS = 160
// A changed eyebrow (range, or a tab with its own) the same way, quieter.
const LABEL_SWAP: Keyframe[] = [{ opacity: 0.35 }, { opacity: 1 }]

export function StatsPage() {
  // A new visit: answers kept from earlier visits show at once and are asked for again.
  const [initial] = useState(() => { beginStatsVisit(); return loadView() })
  const [tab, setTab] = useState<TabId>(initial.tab)
  const [days, setDays] = useState(initial.days)
  const [custom, setCustom] = useState<CustomRange | null>(null)
  const [board, setBoard] = useState<string | null>(initial.board)
  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify({ tab, days, board }))
    } catch {
      // storage unavailable - the view starts on the Overview next time
    }
  }, [tab, days, board])

  // ── /api/stats: one request per range, the range known before it starts ──────
  const key = rangeKey(days, custom)
  const [tab0] = useState(initial.tab)
  // Asked for once a tab that shows it has been opened (not on Timeline or Sessions).
  const [wantStats, setWantStats] = useState(NEEDS_STATS.has(tab0))
  useEffect(() => { if (NEEDS_STATS.has(tab)) setWantStats(true) }, [tab])
  // Each answer keeps the range it was asked for, so what is on screen is described
  // by its own range while a newer one is on its way.
  type Answer = { key: string; data: StatsResponse; days: number; custom: CustomRange | null }
  const [stats, setStats] = useState<Answer | null>(() => {
    const cached = statsCache.get(key)
    return cached ? { key, data: cached, days, custom } : null
  })
  const [failed, setFailed] = useState(false)
  // Bumped to ask again for the same range (an edit on a page, e.g. a book marked read on the Overview).
  const [rev, setRev] = useState(0)
  const refresh = useCallback(() => {
    dropStatsCache('/stats')
    setRev((r) => r + 1)
  }, [])
  // A range asked for during this visit is not asked for again. Coming back to a stats
  // tab asks again only when its answer was dropped meanwhile (an edit on the Sessions tab).
  useEffect(() => {
    if (!wantStats || statsCache.fresh(key)) return
    setFailed(false)
    // A slow answer for a range the user has already left must not paint over
    // the current one: only the latest request may set state.
    let current = true
    statsCache
      .fetch(key)
      .then((r) => { if (current) setStats({ key, data: r, days, custom }) })
      .catch(() => { if (current) setFailed(true) })
    return () => { current = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key names days and custom
  }, [key, wantStats, tab, rev])
  // A cached range shows from the first frame; any other keeps the previous answer on
  // screen, dimmed (after a short wait), until its own lands.
  const hit = wantStats ? statsCache.get(key) : undefined
  const shown: Answer | null = hit ? { key, data: hit, days, custom } : stats
  const pending = !hit && !!stats && stats.key !== key
  const showPending = useDelayed(pending)
  const data = shown?.data ?? null

  // Once the range on screen has its answer: the other preset ranges, quietly, one at a
  // time, so switching to them later is instant. Started again after an edit drops them.
  const ready = !!hit
  useEffect(() => {
    if (!wantStats || !ready) return
    const ctl = new AbortController()
    const others = RANGES.map((p) => `days=${p.days}`).filter((k) => k !== key)
    statsPrefetch(others.map((k) => ({ url: statsUrl(k), key: `/stats?${k}` })), ctl.signal)
    return () => ctl.abort()
  }, [wantStats, ready, key, rev])

  // Second wave, started with the main request rather than after it.
  const [estimates, setEstimates] = useState<CompletionEstimate[] | null>(null)
  const wantEstimates = tab === 'overview' || tab === 'boards'
  useEffect(() => {
    if (!wantEstimates || estimates) return
    // Kept even when the tab changed meanwhile: StatsPage stays mounted, and the answer is the same.
    statsFetch<CompletionEstimate[]>(`/stats/completion-estimates?tz_offset=${new Date().getTimezoneOffset()}`).then(setEstimates).catch(() => {})
  }, [wantEstimates, estimates])

  // ── Saved boards: read here, written only by edits under Boards ──────────────
  // The local copy answers at once (so the Boards tab is there on the first
  // frame); the server copy replaces it when it is the newer one. Nothing is
  // written back on load.
  const [dash, setDash] = useState<{ data: Persisted | null; shown: string[] }>(() => {
    const local = loadLocalDashboard()
    return { data: local, shown: customBoardIds(local?.tabs) }
  })
  const [dashReady, setDashReady] = useState(false)
  // The "Show Boards" switch (Settings › Appearance): this browser's last known
  // state first (set on, set off or never set), then the server's, from the same
  // answer as the boards.
  const [boardsFlag, setBoardsFlag] = useState(loadBoardsFlag)
  useEffect(() => {
    api
      .get<{ data: Persisted | null }>('/stats/dashboard')
      .then((res) => {
        const p = res.data
        const flag = readShowBoards(p)
        setBoardsFlag(flag)
        saveBoardsFlag(flag)
        const local = loadLocalDashboard()
        if (p && Array.isArray(p.tabs) && p.tabs.length > 0 && (p.savedAt ?? 0) >= (local?.savedAt ?? 0)) {
          setDash({ data: p, shown: customBoardIds(p.tabs) })
        }
      })
      .catch(() => {})
      .finally(() => setDashReady(true))
  }, [])
  const boardsShown = boardsTabVisible(boardsFlag, dash.shown)
  const shownTab: TabId = tab === 'boards' && !boardsShown ? 'overview' : tab
  // A remembered Boards tab that is gone (switch set off, or never set and nothing customised)
  // is drawn as the Overview from the first frame; once the server has answered,
  // the remembered tab becomes the Overview too, so it cannot come back later.
  useEffect(() => {
    if (dashReady && tab === 'boards' && !boardsShown) setTab('overview')
  }, [dashReady, tab, boardsShown])
  const [renameFirst, setRenameFirst] = useState<string | null>(null)
  const onDashboardChange = useCallback((next: Persisted, shown: string[]) => setDash({ data: next, shown }), [])

  // "New board" from the header menu: the way to a first board when every saved
  // board is a default (and the Boards tab is therefore not shown).
  const [menuOpen, setMenuOpen] = useState(false)
  const createBoard = useCallback(() => {
    setMenuOpen(false)
    const id = `tab--${Date.now().toString(36)}0`
    const base = dash.data
    const next: Persisted = {
      tabs: [...(base?.tabs ?? []), { id, label: t`New board`, tiles: [], layout: [] }],
      activeTabId: id,
      pad: base?.pad ?? 'lot',
      days,
      savedAt: Date.now(),
      ...(boardsFlag !== null ? { showBoards: boardsFlag } : {}),
    }
    saveLocalDashboard(next)
    api.put('/stats/dashboard', { data: next }).catch(() => {})
    setDash({ data: next, shown: [...dash.shown, id] })
    setBoard(id)
    setRenameFirst(id)
    setTab('boards')
  }, [dash, days, boardsFlag])

  // ── Image export of what is on screen ────────────────────────────────────────
  const contentRef = useRef<HTMLDivElement>(null)
  const boardRef = useRef<HTMLDivElement>(null)
  const [shotBusy, setShotBusy] = useState(false)
  const exportImage = useCallback(() => {
    const node = (shownTab === 'boards' ? boardRef.current : null) ?? contentRef.current
    if (!node || shotBusy) return
    setShotBusy(true)
    // Sections still waiting to be scrolled to (or mid-entry) are drawn final in the image.
    finishEntries()
    for (const a of contentRef.current?.getAnimations() ?? []) a.finish()
    const bg = getComputedStyle(document.body).backgroundColor
    import('html-to-image')
      .then(({ toPng }) => toPng(node, { pixelRatio: 2, backgroundColor: bg }))
      .then((png) => {
        const a = document.createElement('a')
        a.href = png
        a.download = `tome-stats-${shownTab}-${new Date().toISOString().slice(0, 10)}.png`
        a.click()
      })
      .catch(() => {})
      .finally(() => setShotBusy(false))
  }, [shownTab, shotBusy])

  // Switch tab and, once the target exists, bring an element on it into view.
  const [anchor, setAnchor] = useState<string | null>(null)
  const goto = useCallback((next: TabId, id?: string) => {
    setTab(next)
    setAnchor(id ?? null)
  }, [])
  useEffect(() => {
    if (!anchor) return
    let tries = 0
    const tick = () => {
      const el = document.getElementById(anchor)
      if (el) { el.scrollIntoView({ block: 'start', behavior: 'smooth' }); setAnchor(null) }
      else if (tries++ < 60) raf = requestAnimationFrame(tick)
      else setAnchor(null)
    }
    let raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [anchor, shownTab, data])

  const rangeLabel = custom
    ? (() => { const from = fmtDay(custom.start), to = fmtDay(custom.end); return t`${from} to ${to}` })()
    : days === 0 ? t`All time` : days === 365 ? t`Last 12 months` : t`Last ${days} days`
  // Sessions lists every session; Taste is all time. Neither follows the range.
  // Library is all time too, except three sections that carry the range as a chip; it keeps the range control.
  const rangeless = shownTab === 'sessions'
  const allTime = shownTab === 'taste'
  // Goals follow calendar periods, not the range.
  const periods = shownTab === 'goals'

  const tabs = useMemo(() => PAGES.filter((p) => p.id !== 'boards' || boardsShown), [boardsShown])

  // The tab-switch rise (TAB_SWAP); not on the page's first frame.
  const lastTab = useRef(shownTab)
  useLayoutEffect(() => {
    if (lastTab.current === shownTab) return
    lastTab.current = shownTab
    const el = contentRef.current
    if (!el || reducedMotion()) return
    for (const a of el.getAnimations()) a.cancel()
    el.animate(TAB_SWAP, { duration: TAB_SWAP_MS, easing: EASE })
  }, [shownTab])
  const eyebrowText = rangeless ? 'sessions' : periods ? 'periods' : allTime || shownTab === 'library' ? 'all' : rangeLabel
  const eyebrowRef = useRef<HTMLSpanElement>(null)
  const lastEyebrow = useRef(eyebrowText)
  useLayoutEffect(() => {
    if (lastEyebrow.current === eyebrowText) return
    lastEyebrow.current = eyebrowText
    if (!eyebrowRef.current || reducedMotion()) return
    eyebrowRef.current.animate(LABEL_SWAP, { duration: TAB_SWAP_MS, easing: EASE })
  }, [eyebrowText])

  let content: ReactNode
  if (failed && !data) {
    content = <p className="py-32 text-center text-[14px] text-muted-foreground"><Trans>Couldn’t load stats.</Trans></p>
  } else if (shownTab === 'overview') {
    content = <Overview stats={data} estimates={estimates} pending={pending} days={shown?.days ?? days} custom={shown ? shown.custom : custom} requested={{ days, custom }} onAllBooks={() => goto('library', 'stats-all-books')} onRefresh={refresh} onGoals={(id) => { if (id != null) saveSelection({ id, kind: '' }); goto('goals') }} />
  } else if (shownTab === 'goals') {
    content = <GoalsTab />
  } else if (shownTab === 'timeline') {
    content = <FullBleedTimeline />
  } else if (shownTab === 'sessions') {
    content = <SessionsPage />
  } else if (shownTab === 'habits' || shownTab === 'library' || shownTab === 'taste') {
    const props = { stats: data, pending, days: shown?.days ?? days, custom: shown ? shown.custom : custom, requested: { days, custom }, goto }
    content = shownTab === 'habits' ? <HabitsPage {...props} /> : shownTab === 'library' ? <LibraryPage {...props} /> : <TastePage {...props} />
  } else if (!data) {
    content = <Spinner />
  } else if (shownTab === 'boards') {
    content = dashReady && (dash.data || boardsFlag === true) ? (
      <Suspense fallback={<Spinner />}>
        <BoardsView
          stats={data}
          estimates={estimates}
          dashboard={dash.data ?? { tabs: [], activeTabId: '', pad: 'lot', days }}
          shownIds={dash.shown}
          showBoards={boardsFlag}
          activeBoardId={board}
          onActiveBoard={setBoard}
          onDashboardChange={onDashboardChange}
          days={days}
          renameFirst={renameFirst}
          boardRef={boardRef}
        />
      </Suspense>
    ) : <Spinner />
  }

  return (
    <AppShell>
      {/* eslint-disable-next-line lingui/no-unlocalized-strings -- CSS */}
      <style>{`@keyframes stats-pending { from { transform: translateX(-100%) } to { transform: translateX(270%) } }`}</style>
      <TipLayer />
      <PageBody width="wide" className={cn(shownTab === 'boards' && dash.data && PAGE_W[dash.data.pad ?? 'lot'])}>
        <div className="flex flex-col gap-5">
          <div className="relative">
            <PageHeader
              eyebrow={<span ref={eyebrowRef} className="inline-block">{rangeless ? <Trans>All sessions</Trans> : periods ? <Trans>This week, month and year</Trans> : allTime || shownTab === 'library' ? <Trans>All time</Trans> : rangeLabel}</span>}
              title={
                <span className="inline-flex items-center gap-2">
                  <Trans>Stats</Trans>
                  <a
                    href={docsLink(DOCS.stats)}
                    target="_blank"
                    rel="noopener noreferrer"
                    {...tipAttrs({ title: t`What do these mean? Read the stats docs` })}
                    aria-label={t`Stats docs`}
                    className="grid h-8 w-8 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                  >
                    <HelpCircle className="h-4 w-4" />
                  </a>
                </span>
              }
              actions={
                <div className="flex max-w-[calc(100vw-2.5rem)] flex-wrap items-center gap-1" data-stats-controls="">
                  {!rangeless && !allTime && !periods && (
                    <RangeControl
                      days={days}
                      custom={custom}
                      onPreset={(d) => {
                        setDays(d)
                        setCustom(null)
                      }}
                      onCustom={setCustom}
                    />
                  )}
                  {!rangeless && <Pill
                    variant="quiet"
                    icon
                    onClick={exportImage}
                    disabled={shotBusy}
                    {...tipAttrs({ title: t`Save this page as an image` })}
                    aria-label={t`Save page as image`}
                  >
                    {shotBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ImageDown className="h-4 w-4" />}
                  </Pill>}
                  {/* Its one entry makes a board; with Show Boards set off there is no Boards tab to put one in. */}
                  {boardsFlag !== false && <div className="relative">
                    <Pill variant={menuOpen ? 'secondary' : 'quiet'} icon onClick={() => setMenuOpen((o) => !o)} aria-label={t`More`} aria-expanded={menuOpen}>
                      <MoreHorizontal className="h-4 w-4" />
                    </Pill>
                    {menuOpen && <div className="fixed inset-0 z-40" onPointerDown={() => setMenuOpen(false)} />}
                    <Pop open={menuOpen} origin="top-right" className="absolute right-0 top-full z-50 mt-1.5 w-56 rounded-xl bg-card p-1.5 text-[14px] shadow-lg ring-1 ring-panel-ring">
                      <button
                        type="button"
                        onClick={createBoard}
                        disabled={!dashReady}
                        className={cn('touch-feedback flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-foreground hover:bg-secondary disabled:opacity-50', PRESS)}
                      >
                        <Plus className="h-3.5 w-3.5 text-muted-foreground" /> <Trans>New board</Trans>
                      </button>
                      <p className="px-2.5 pb-1 pt-0.5 text-[12px] leading-snug text-muted-foreground">
                        <Trans>A board of your own, with tiles you place yourself.</Trans>
                      </p>
                    </Pop>
                  </div>}
                </div>
              }
            />
            {/* A range change in flight: a thin line under the header, placed so it moves nothing. */}
            {showPending && (
              <div className="pointer-events-none absolute inset-x-0 -bottom-[11px] h-[2px] overflow-hidden rounded-full" style={{ background: 'color-mix(in oklch, var(--primary) 22%, transparent)' }} role="progressbar" aria-label={t`Loading the new range`}>
                <div className="h-full w-[38%] rounded-full bg-primary" style={{ animation: 'stats-pending 1.1s ease-in-out infinite' }} />
              </div>
            )}
          </div>

          <GlideGroup>
          <div className="isolate flex min-w-0 items-center gap-1 overflow-x-auto max-sm:-ml-5 max-sm:pl-5 max-sm:-mr-5 max-sm:pr-10 max-sm:[mask-image:linear-gradient(to_right,#000_calc(100%-40px),transparent)] [scrollbar-width:none]" role="tablist" aria-label={t`Stats pages`} data-stats-controls="">
            {tabs.map((p) => (
              <button
                key={p.id}
                type="button"
                role="tab"
                aria-selected={shownTab === p.id}
                onClick={() => setTab(p.id)}
                className={cn(
                  'group/pg relative h-9 shrink-0 whitespace-nowrap rounded-full px-4 text-[14px] font-medium transition-colors',
                  shownTab === p.id ? 'text-foreground' : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                )}
              >
                {shownTab === p.id && <Glide behind className="bg-secondary" />}
                <span className={PRESS_LABEL}>{i18n._(p.label)}</span>
              </button>
            ))}
          </div>
          </GlideGroup>
        </div>

        <div ref={contentRef} className="min-w-0">{content}</div>
      </PageBody>
    </AppShell>
  )
}

export default StatsPage
