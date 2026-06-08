import { useCallback, useEffect, useRef, useState, type ReactNode, type MouseEvent } from 'react'
import ReactGridLayout, {
  useContainerWidth,
  type Layout,
} from 'react-grid-layout'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import { Plus, RotateCcw, X, FlaskConical, SlidersHorizontal, Pencil, Check, GripVertical, Calendar, ChevronLeft, ChevronRight } from 'lucide-react'
import { cn, formatDuration } from '@/lib/utils'
import { api } from '@/lib/api'
import { useChartAccent } from '@/lib/useChartAccent'
import { THEMES, applyTheme, getStoredTheme, type ThemeId } from '@/lib/theme'
import { type StatsResponse, type CompletionEstimate } from '@/components/stats/shared'
import {
  HeadlineStatBody,
  CurrentlyReading,
  ReadingTimePerDay,
  TopBooksByTime,
  ReadingActivity365,
  BooksFinishedArea,
} from '@/components/stats/widgets/overview'
import {
  HourDowCard,
  SessionTimeline,
  ReadingPaceChart,
  ReadingSpeedTrend,
  CompletionEstimatesList,
  PeriodComparison,
  MonthlyComparison,
} from '@/components/stats/widgets/habits'
import { PaceByFormat } from '@/components/stats/PaceByFormat'
import {
  YearInReview,
  CategoryBreakdown,
  GenreOverTime,
  PerBookTimeTable,
} from '@/components/stats/widgets/library'
import { SeriesCompletionGrid } from '@/components/stats/SeriesCompletionGrid'
import { AuthorAffinity } from '@/components/stats/AuthorAffinity'
import { CompletionByType } from '@/components/stats/CompletionByType'
import { LibraryGrowthChart } from '@/components/stats/LibraryGrowthChart'

/**
 * Stats Lab — look-and-feel POC for the customisable stats dashboard.
 * This pass replicates the real Stats page (starting with the Overview tab) as
 * draggable/resizable tiles, wired to live /stats data via shared widget components.
 * See docs/plans/stats-dashboard-plan.md.
 */

type ChartType = 'bar' | 'line' | 'area'
type TileConfig = { chartType: ChartType; days: number }

// ── Widget catalog (renders shared Stats components from live data) ─────────────

type WidgetCtx = { accent: string; stats: StatsResponse; estimates: CompletionEstimate[] | null }

type WidgetDef = {
  id: string
  title: string
  size: { w: number; h: number; minW: number; minH: number }
  chartTypes?: ChartType[]
  defaultConfig?: TileConfig
  render: (ctx: WidgetCtx) => ReactNode
}

const STAT_SIZE = { w: 2, h: 1, minW: 2, minH: 1 }

const WIDGETS: WidgetDef[] = [
  // Headline figures — one tile each (separately movable / removable).
  {
    id: 'stat-time',
    title: 'Reading Time',
    size: STAT_SIZE,
    render: ({ stats }) => (
      <HeadlineStatBody value={formatDuration(stats.headline.total_reading_seconds)} sub={`avg ${formatDuration(stats.headline.avg_session_seconds)} / session`} />
    ),
  },
  {
    id: 'stat-sessions',
    title: 'Sessions',
    size: STAT_SIZE,
    render: ({ stats }) => <HeadlineStatBody value={String(stats.headline.total_sessions)} />,
  },
  {
    id: 'stat-finished',
    title: 'Books Finished',
    size: STAT_SIZE,
    render: ({ stats }) => <HeadlineStatBody value={String(stats.headline.books_finished)} />,
  },
  {
    id: 'stat-streak',
    title: 'Streak',
    size: STAT_SIZE,
    render: ({ stats }) => <HeadlineStatBody value={`${stats.headline.current_streak_days}d`} sub={`Longest: ${stats.headline.longest_streak_days}d`} />,
  },
  {
    id: 'stat-pages',
    title: 'Pages Turned',
    size: STAT_SIZE,
    render: ({ stats }) => <HeadlineStatBody value={stats.headline.pages_turned.toLocaleString()} />,
  },
  {
    id: 'stat-completion',
    title: 'Completion Rate',
    size: STAT_SIZE,
    render: ({ stats }) => <HeadlineStatBody value={`${stats.completion_rate.pct}%`} sub={`${stats.completion_rate.finished} of ${stats.completion_rate.started} started`} />,
  },
  {
    id: 'currently-reading',
    title: 'Currently Reading',
    size: { w: 6, h: 2, minW: 3, minH: 1 },
    render: ({ stats }) => <CurrentlyReading books={stats.books_in_progress} />,
  },
  {
    id: 'daily',
    title: 'Reading Time per Day',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <ReadingTimePerDay daily={stats.daily} />,
  },
  {
    id: 'top-books',
    title: 'Top Books by Reading Time',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <TopBooksByTime topBooks={stats.top_books} />,
  },
  {
    id: 'books-finished',
    title: 'Books Finished',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <BooksFinishedArea booksFinished={stats.books_finished} />,
  },
  {
    id: 'activity-365',
    title: 'Reading Activity — Last 365 Days',
    size: { w: 12, h: 2, minW: 4, minH: 2 },
    render: ({ stats }) => <ReadingActivity365 heatmap={stats.heatmap_daily} />,
  },
  // Habits tab
  {
    id: 'hour-dow',
    title: 'Reading Intensity by Hour and Day',
    size: { w: 12, h: 2, minW: 5, minH: 2 },
    render: ({ stats }) => <HourDowCard data={stats.hour_dow_heatmap} />,
  },
  {
    id: 'session-timeline',
    title: 'Session Timeline',
    size: { w: 6, h: 3, minW: 4, minH: 2 },
    render: ({ stats }) => <SessionTimeline sessions={stats.session_timeline} />,
  },
  {
    id: 'reading-pace',
    title: 'Reading Pace',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <ReadingPaceChart pace={stats.reading_pace} />,
  },
  {
    id: 'pace-by-format',
    title: 'Pace by Format',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <PaceByFormat data={stats.pace_by_format} />,
  },
  {
    id: 'speed-trend',
    title: 'Reading Speed Trend',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <ReadingSpeedTrend pace={stats.reading_pace} />,
  },
  {
    id: 'estimates',
    title: 'Completion Estimates',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ estimates }) => <CompletionEstimatesList estimates={estimates} />,
  },
  {
    id: 'period-comparison',
    title: 'Period Comparison',
    size: { w: 6, h: 1, minW: 3, minH: 1 },
    render: ({ stats }) =>
      stats.period_comparison ? <PeriodComparison comparison={stats.period_comparison} /> : <p className="text-sm text-muted-foreground">No comparison data.</p>,
  },
  {
    id: 'monthly-comparison',
    title: 'Reading Hours & Books Finished — Last 12 Months',
    size: { w: 12, h: 2, minW: 4, minH: 2 },
    render: ({ stats }) => <MonthlyComparison monthly={stats.monthly_comparison} />,
  },
  // Library tab
  {
    id: 'year-in-review',
    title: 'Year in Review',
    size: { w: 6, h: 2, minW: 4, minH: 1 },
    render: ({ stats }) => <YearInReview summary={stats.year_summary} />,
  },
  {
    id: 'series-completion',
    title: 'Series Completion',
    size: { w: 6, h: 3, minW: 3, minH: 2 },
    render: ({ stats }) => <SeriesCompletionGrid data={stats.series_completion} />,
  },
  {
    id: 'author-affinity',
    title: 'Top Authors by Reading Time',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <AuthorAffinity data={stats.author_affinity} />,
  },
  {
    id: 'completion-by-type',
    title: 'Finish Rate per Book Category',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <CompletionByType data={stats.completion_by_type} />,
  },
  {
    id: 'category-breakdown',
    title: 'Category Breakdown',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <CategoryBreakdown data={stats.by_category} />,
  },
  {
    id: 'genre-over-time',
    title: 'Reading by Category — Last 12 Months',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <GenreOverTime data={stats.genre_over_time} />,
  },
  {
    id: 'library-growth',
    title: 'Cumulative Books Added — Last 24 Months',
    size: { w: 12, h: 2, minW: 4, minH: 2 },
    render: ({ stats }) => <LibraryGrowthChart data={stats.library_growth} />,
  },
  {
    id: 'per-book-table',
    title: 'All Books by Reading Time',
    size: { w: 12, h: 3, minW: 5, minH: 2 },
    render: ({ stats }) => <PerBookTimeTable data={stats.per_book_time} />,
  },
]

const defById = (id: string) => WIDGETS.find((w) => w.id === id.replace(/--\d+$/, ''))!

const WIDGET_DESC: Record<string, string> = {
  'stat-time': 'Total time read + avg session',
  'stat-sessions': 'Number of reading sessions',
  'stat-finished': 'Books completed',
  'stat-streak': 'Current & longest daily streak',
  'stat-pages': 'Total pages turned',
  'stat-completion': 'Started vs finished ratio',
  'currently-reading': 'Books in progress, with covers',
  daily: 'Minutes read per day',
  'top-books': 'Most-read books by time',
  'books-finished': 'Cumulative finishes over time',
  'activity-365': 'A year of reading, heatmap',
  'hour-dow': 'When you read — hour × weekday',
  'session-timeline': 'Daily sessions on a 24h track',
  'reading-pace': 'Pages per minute over time',
  'pace-by-format': 'Speed by book format',
  'speed-trend': 'Are you getting faster?',
  estimates: 'Time left on books in progress',
  'period-comparison': 'This period vs the last',
  'monthly-comparison': 'Hours & finishes, last 12 months',
  'year-in-review': 'Your year, at a glance (1y/All)',
  'series-completion': 'How far through each series',
  'author-affinity': 'Most-read authors',
  'completion-by-type': 'Finish rate by book type',
  'category-breakdown': 'Time split across categories',
  'genre-over-time': 'Category mix over the year',
  'library-growth': 'Library size over time',
  'per-book-table': 'Sortable table of every book',
}

// Widgets that are a number/short text — render their preview at natural size (no scale).
const NATURAL_PREVIEW = new Set(['stat-time', 'stat-sessions', 'stat-finished', 'stat-streak', 'stat-pages', 'stat-completion', 'period-comparison'])

// List/table widgets whose content is a vertical list that can exceed the tile → scroll
// internally. Everything else (charts) clips (overflow-hidden) so nothing spills out.
const SCROLL_IDS = new Set([
  'currently-reading', 'estimates', 'session-timeline', 'series-completion', 'per-book-table',
  'author-affinity', 'completion-by-type', 'pace-by-format',
])

const GALLERY_GROUPS: { label: string; ids: string[] }[] = [
  {
    label: 'Overview',
    ids: ['stat-time', 'stat-sessions', 'stat-finished', 'stat-streak', 'stat-pages', 'stat-completion', 'currently-reading', 'daily', 'top-books', 'books-finished', 'activity-365'],
  },
  {
    label: 'Habits',
    ids: ['hour-dow', 'session-timeline', 'reading-pace', 'pace-by-format', 'speed-trend', 'estimates', 'period-comparison', 'monthly-comparison'],
  },
  {
    label: 'Library',
    ids: ['year-in-review', 'series-completion', 'author-affinity', 'completion-by-type', 'category-breakdown', 'genre-over-time', 'library-growth', 'per-book-table'],
  },
]

type Tile = { id: string; defId: string; config: TileConfig }

const DEFAULT_CFG: TileConfig = { chartType: 'bar', days: 30 }

// Default board mirrors the Stats Overview order; fully rearrangeable.
const STAT_IDS = ['stat-time', 'stat-sessions', 'stat-finished', 'stat-streak', 'stat-pages', 'stat-completion']
const INITIAL_POS: Record<string, { x: number; y: number; w: number; h: number }> = {
  // row 0: six stat tiles across
  ...Object.fromEntries(STAT_IDS.map((id, i) => [id, { x: i * 2, y: 0, w: 2, h: 1 }])),
  // Overview
  'currently-reading': { x: 0, y: 1, w: 6, h: 2 },
  daily: { x: 6, y: 1, w: 6, h: 2 },
  'top-books': { x: 0, y: 3, w: 6, h: 2 },
  'books-finished': { x: 6, y: 3, w: 6, h: 2 },
  'activity-365': { x: 0, y: 5, w: 12, h: 2 },
  // Habits
  'hour-dow': { x: 0, y: 7, w: 12, h: 2 },
  'session-timeline': { x: 0, y: 9, w: 6, h: 3 },
  'reading-pace': { x: 6, y: 9, w: 6, h: 2 },
  'pace-by-format': { x: 6, y: 11, w: 6, h: 2 },
  'speed-trend': { x: 0, y: 12, w: 6, h: 2 },
  'period-comparison': { x: 6, y: 13, w: 6, h: 1 },
  estimates: { x: 0, y: 14, w: 12, h: 2 },
  'monthly-comparison': { x: 0, y: 16, w: 12, h: 2 },
  // Library
  'year-in-review': { x: 0, y: 0, w: 6, h: 2 },
  'series-completion': { x: 6, y: 0, w: 6, h: 3 },
  'author-affinity': { x: 0, y: 2, w: 6, h: 2 },
  'completion-by-type': { x: 0, y: 4, w: 6, h: 2 },
  'category-breakdown': { x: 6, y: 3, w: 6, h: 2 },
  'genre-over-time': { x: 6, y: 5, w: 6, h: 2 },
  'library-growth': { x: 0, y: 6, w: 12, h: 2 },
  'per-book-table': { x: 0, y: 8, w: 12, h: 3 },
}

// Each tab is its own board (own tiles + layout), independently customizable.
type TabState = { id: string; label: string; tiles: Tile[]; layout: Layout }

const TAB_DEFS: { id: string; label: string; ids: string[] }[] = [
  { id: 'overview', label: 'Overview', ids: [...STAT_IDS, 'currently-reading', 'daily', 'top-books', 'books-finished', 'activity-365'] },
  { id: 'habits', label: 'Habits', ids: ['hour-dow', 'session-timeline', 'reading-pace', 'pace-by-format', 'speed-trend', 'estimates', 'period-comparison', 'monthly-comparison'] },
  { id: 'library', label: 'Library', ids: ['year-in-review', 'series-completion', 'author-affinity', 'completion-by-type', 'category-breakdown', 'genre-over-time', 'library-growth', 'per-book-table'] },
]

function buildTab(def: { id: string; label: string; ids: string[] }): TabState {
  if (def.ids.length === 0) return { id: def.id, label: def.label, tiles: [], layout: [] }
  // re-base y so each tab's board starts at the top
  const minY = Math.min(...def.ids.map((id) => INITIAL_POS[id].y))
  return {
    id: def.id,
    label: def.label,
    tiles: def.ids.map((id) => ({ id, defId: id, config: DEFAULT_CFG })),
    layout: def.ids.map((id) => {
      const p = INITIAL_POS[id]
      const d = defById(id)
      return { i: id, x: p.x, y: p.y - minY, w: p.w, h: p.h, minW: d.size.minW, minH: d.size.minH }
    }),
  }
}

const buildTabs = (): TabState[] => TAB_DEFS.map(buildTab)

const RANGES = [
  { days: 7, label: '7d' },
  { days: 30, label: '30d' },
  { days: 90, label: '90d' },
  { days: 365, label: '1y' },
  { days: 0, label: 'All' },
]
const TIMEFRAMES = [7, 14, 30]

// ── Config popover ────────────────────────────────────────────────────────────

function ConfigPopover({
  def,
  config,
  onChange,
  onClose,
}: {
  def: WidgetDef
  config: TileConfig
  onChange: (partial: Partial<TileConfig>) => void
  onClose: () => void
}) {
  return (
    <>
      <div className="fixed inset-0 z-40" onPointerDown={onClose} />
      <div
        className="no-drag absolute right-2 top-9 z-50 w-48 rounded-lg border border-border bg-card p-3 text-xs shadow-xl"
        onPointerDown={(e) => e.stopPropagation()}
      >
        <p className="mb-1.5 font-medium text-muted-foreground">Chart type</p>
        <div className="mb-3 flex rounded-md border border-border p-0.5">
          {def.chartTypes!.map((ct) => (
            <button
              key={ct}
              type="button"
              onClick={() => onChange({ chartType: ct })}
              className={cn(
                'flex-1 rounded px-1.5 py-1 capitalize transition',
                config.chartType === ct ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {ct}
            </button>
          ))}
        </div>
        <p className="mb-1.5 font-medium text-muted-foreground">Timeframe</p>
        <div className="flex gap-1">
          {TIMEFRAMES.map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => onChange({ days: d })}
              className={cn(
                'flex-1 rounded-md border px-1.5 py-1 transition',
                config.days === d ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:text-foreground',
              )}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>
    </>
  )
}

// ── Add-widget gallery ─────────────────────────────────────────────────────────

function AddWidgetModal({
  ctx,
  present,
  onAdd,
  onClose,
}: {
  ctx: WidgetCtx
  present: Set<string>
  onAdd: (defId: string) => void
  onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-3xl max-h-[85vh] overflow-y-auto rounded-2xl border border-border bg-card p-5 shadow-xl shadow-accent-soft">
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="text-base font-semibold">Add a widget</h2>
            <p className="text-xs text-muted-foreground">Removed a tile? Add it back — or add another copy.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-col gap-5">
          {GALLERY_GROUPS.map((group) => (
            <div key={group.label}>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{group.label}</h3>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {group.ids.map((id) => {
                  const w = defById(id)
                  return (
                    <button
                      key={id}
                      type="button"
                      onClick={() => onAdd(id)}
                      title={WIDGET_DESC[id]}
                      className="group/card flex flex-col gap-1.5 rounded-lg border border-border bg-background p-2 text-left transition hover:border-primary/50 hover:bg-muted"
                    >
                      {/* live mini-preview. Number/text widgets render at natural size so
                          they fill the box; charts render at a real tile size and scale down
                          so they keep their proportions instead of squishing. */}
                      <div className="pointer-events-none h-[94px] overflow-hidden rounded-md border border-border/50 bg-card">
                        {NATURAL_PREVIEW.has(id) ? (
                          <div className="h-full w-full p-3">{w.render(ctx)}</div>
                        ) : (
                          <div className="origin-top-left p-2.5" style={{ width: 360, height: 152, transform: 'scale(0.62)' }}>
                            {w.render(ctx)}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 px-0.5">
                        <span className="truncate text-xs font-medium text-foreground">{w.title}</span>
                        {present.has(id) && (
                          <span className="ml-auto shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[9px] text-muted-foreground">on board</span>
                        )}
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Tile frame ────────────────────────────────────────────────────────────────

function TileShell({
  def,
  config,
  editMode,
  children,
  onRemove,
  onConfigChange,
  dragHandleProps,
  dragging,
}: {
  def: WidgetDef
  config: TileConfig
  editMode: boolean
  children: ReactNode
  onRemove: () => void
  onConfigChange: (partial: Partial<TileConfig>) => void
  dragHandleProps?: Record<string, unknown>
  dragging?: boolean
}) {
  const [cfgOpen, setCfgOpen] = useState(false)
  const [hovering, setHovering] = useState(false)
  const [glare, setGlare] = useState({ x: 50, y: 50 })
  const tiltRef = useRef<HTMLDivElement>(null)
  const configurable = !!def.chartTypes
  const tiltOn = editMode && !dragging && !cfgOpen

  function onMove(e: MouseEvent<HTMLDivElement>) {
    if (!tiltOn || !tiltRef.current) return
    const r = e.currentTarget.getBoundingClientRect()
    const x = (e.clientX - r.left) / r.width
    const y = (e.clientY - r.top) / r.height
    setGlare({ x: x * 100, y: y * 100 })
    tiltRef.current.style.transform = `rotateY(${(x * 2 - 1) * 5}deg) rotateX(${(y * 2 - 1) * -5}deg) translateY(-5px)`
  }
  function onLeave() {
    setHovering(false)
    if (tiltRef.current) tiltRef.current.style.transform = ''
  }

  return (
    <div
      ref={tiltRef}
      onMouseEnter={tiltOn ? () => setHovering(true) : undefined}
      onMouseMove={tiltOn ? onMove : undefined}
      onMouseLeave={editMode ? onLeave : undefined}
      style={{
        transition: hovering
          ? 'transform 0.06s ease-out, box-shadow 0.2s ease-out'
          : 'transform 0.3s ease-out, box-shadow 0.2s ease-out',
      }}
      className={cn(
        'group/tile relative flex h-full w-full flex-col rounded-xl border bg-card p-4',
        dragging ? 'border-border shadow-2xl ring-1 ring-primary/30' : 'shadow-sm',
        editMode ? 'border-primary/30' : 'border-border',
        editMode && !dragging && 'hover:z-10 hover:shadow-lg hover:shadow-accent-soft',
      )}
    >
      {editMode && (
        <div
          className="pointer-events-none absolute inset-0 z-20 rounded-xl transition-opacity duration-300"
          style={{
            opacity: hovering ? 1 : 0,
            background: `radial-gradient(circle at ${glare.x}% ${glare.y}%, rgba(255,255,255,0.045) 0%, transparent 60%)`,
          }}
        />
      )}
      <div
        {...(editMode ? dragHandleProps : {})}
        className={cn('mb-3 flex items-center gap-1.5', editMode && 'tile-drag-handle cursor-grab active:cursor-grabbing')}
      >
        {editMode && <GripVertical className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50" />}
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{def.title}</h3>
        {editMode && (
          <div className="ml-auto flex items-center gap-0.5">
            {configurable && (
              <button
                type="button"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => setCfgOpen((o) => !o)}
                className={cn(
                  'no-drag -my-1 rounded-md p-1 transition hover:bg-muted hover:text-foreground',
                  cfgOpen ? 'bg-muted text-foreground' : 'text-muted-foreground',
                )}
                aria-label="Configure"
              >
                <SlidersHorizontal className="h-3.5 w-3.5" />
              </button>
            )}
            <button
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={onRemove}
              className="no-drag -my-1 rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
              aria-label={`Remove ${def.title}`}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>
      {cfgOpen && editMode && configurable && (
        <ConfigPopover def={def} config={config} onChange={onConfigChange} onClose={() => setCfgOpen(false)} />
      )}
      <div className={cn('min-h-0 flex-1', SCROLL_IDS.has(def.id) ? 'overflow-y-auto' : 'overflow-hidden')}>{children}</div>
    </div>
  )
}

// ── Engine A: react-grid-layout ──────────────────────────────────────────────

function FreeGrid({
  tiles,
  layout,
  ctx,
  editMode,
  onLayoutChange,
  onRemove,
  onConfigChange,
}: {
  tiles: Tile[]
  layout: Layout
  ctx: WidgetCtx
  editMode: boolean
  onLayoutChange: (l: Layout) => void
  onRemove: (id: string) => void
  onConfigChange: (id: string, partial: Partial<TileConfig>) => void
}) {
  const { width, containerRef, mounted } = useContainerWidth()
  return (
    <div ref={containerRef} className={cn('w-full', editMode && 'lab-editing')}>
      {mounted && width > 0 && (
        <ReactGridLayout
          width={width}
          layout={layout}
          gridConfig={{ cols: 12, rowHeight: 104, margin: [16, 16], containerPadding: [0, 0] }}
          dragConfig={{ enabled: editMode, handle: '.tile-drag-handle', cancel: '.no-drag' }}
          resizeConfig={{ enabled: editMode, handles: ['se', 'e', 's'] }}
          onLayoutChange={(l: Layout) => onLayoutChange(l)}
        >
          {tiles.map((t) => {
            const def = defById(t.defId)
            return (
              <div key={t.id}>
                <TileShell def={def} config={t.config} editMode={editMode} onRemove={() => onRemove(t.id)} onConfigChange={(p) => onConfigChange(t.id, p)}>
                  {def.render(ctx)}
                </TileShell>
              </div>
            )
          })}
        </ReactGridLayout>
      )}
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

type CustomRange = { start: string; end: string }

// Side-padding setting. Percentage gutters (not max-width caps) so the three levels
// always differ proportionally, even on a narrow window.
type PadWidth = 'none' | 'bit' | 'lot'
const PAD_X: Record<PadWidth, string> = {
  none: 'px-4',
  bit: 'px-[7%]',
  lot: 'px-[16%]',
}
const PAD_LABEL: Record<PadWidth, string> = { none: 'None', bit: 'A bit', lot: 'A lot' }

const fmtDay = (s: string) => {
  try {
    return new Date(s + 'T00:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  } catch {
    return s
  }
}

const isoOf = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

// Themed month calendar with range selection — uses theme CSS vars, so it recolors
// with the theme switcher (unlike the browser's native date picker).
function RangeCalendar({ from, to, onPick }: { from: string; to: string; onPick: (iso: string) => void }) {
  const [view, setView] = useState(() => (from ? new Date(from + 'T00:00:00') : new Date()))
  const y = view.getFullYear()
  const m = view.getMonth()
  const firstDow = (new Date(y, m, 1).getDay() + 6) % 7 // Mon = 0
  const daysInMonth = new Date(y, m + 1, 0).getDate()
  const cells: (Date | null)[] = [...Array(firstDow).fill(null), ...Array.from({ length: daysInMonth }, (_, i) => new Date(y, m, i + 1))]
  const monthLabel = view.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <button type="button" onClick={() => setView(new Date(y, m - 1, 1))} className="rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground" aria-label="Previous month">
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="text-xs font-semibold text-foreground">{monthLabel}</span>
        <button type="button" onClick={() => setView(new Date(y, m + 1, 1))} className="rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground" aria-label="Next month">
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
      <div className="grid grid-cols-7 gap-0.5">
        {['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map((w) => (
          <div key={w} className="py-1 text-center text-[10px] font-medium text-muted-foreground">{w}</div>
        ))}
        {cells.map((d, i) => {
          if (!d) return <div key={i} />
          const iso = isoOf(d)
          const isEnd = iso === from || iso === to
          const between = !!from && !!to && iso > from && iso < to
          return (
            <button
              key={i}
              type="button"
              onClick={() => onPick(iso)}
              className={cn(
                'flex h-7 items-center justify-center rounded-md text-xs tabular-nums transition',
                isEnd ? 'bg-primary font-semibold text-primary-foreground' : between ? 'bg-primary/15 text-foreground' : 'text-foreground hover:bg-muted',
              )}
            >
              {d.getDate()}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function RangeControl({
  days,
  custom,
  onPreset,
  onCustom,
}: {
  days: number
  custom: CustomRange | null
  onPreset: (days: number) => void
  onCustom: (range: CustomRange | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [from, setFrom] = useState(custom?.start ?? '')
  const [to, setTo] = useState(custom?.end ?? '')
  useEffect(() => {
    setFrom(custom?.start ?? '')
    setTo(custom?.end ?? '')
  }, [custom])

  const pick = (iso: string) => {
    if (!from || (from && to)) {
      setFrom(iso)
      setTo('')
    } else if (iso < from) {
      setFrom(iso)
    } else {
      setTo(iso)
    }
  }

  const valid = !!from && !!to && from <= to

  return (
    <div className="relative ml-auto flex items-center gap-1 rounded-lg bg-muted p-0.5">
      {RANGES.map((r) => (
        <button
          key={r.days}
          type="button"
          onClick={() => onPreset(r.days)}
          className={cn(
            'rounded-md px-2.5 py-1 text-xs font-medium transition',
            !custom && days === r.days ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {r.label}
        </button>
      ))}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition',
          custom ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
        )}
      >
        <Calendar className="h-3.5 w-3.5" />
        {custom ? `${fmtDay(custom.start)} – ${fmtDay(custom.end)}` : 'Custom'}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onPointerDown={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-50 mt-1.5 w-64 rounded-lg border border-border bg-card p-3 shadow-xl shadow-accent-soft">
            <RangeCalendar from={from} to={to} onPick={pick} />
            <div className="mt-2 flex items-center justify-between border-t border-border pt-2 text-xs">
              <span className="text-muted-foreground">
                {from ? (to ? `${fmtDay(from)} – ${fmtDay(to)}` : `${fmtDay(from)} – …`) : 'Pick a start & end'}
              </span>
              <div className="flex items-center gap-1.5">
                {custom && (
                  <button
                    type="button"
                    onClick={() => {
                      onCustom(null)
                      setOpen(false)
                    }}
                    className="rounded-md px-2 py-1 text-muted-foreground transition hover:text-foreground"
                  >
                    Clear
                  </button>
                )}
                <button
                  type="button"
                  disabled={!valid}
                  onClick={() => {
                    onCustom({ start: from, end: to })
                    setOpen(false)
                  }}
                  className="rounded-md bg-primary px-3 py-1 font-medium text-primary-foreground transition hover:bg-primary/90 disabled:opacity-40"
                >
                  Apply
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export function StatsLabPage() {
  const accent = useChartAccent()
  const [theme, setTheme] = useState<ThemeId>(getStoredTheme)
  const [editMode, setEditMode] = useState(false)
  const [pad, setPad] = useState<PadWidth>('bit')
  const [addOpen, setAddOpen] = useState(false)
  const [days, setDays] = useState(30)
  const [custom, setCustom] = useState<CustomRange | null>(null)
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [estimates, setEstimates] = useState<CompletionEstimate[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [tabs, setTabs] = useState<TabState[]>(buildTabs)
  const [activeTabId, setActiveTabId] = useState('overview')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const nextN = useRef(1)
  const nextTab = useRef(1)
  const active = tabs.find((t) => t.id === activeTabId) ?? tabs[0]

  const updateActive = useCallback(
    (fn: (t: TabState) => TabState) => setTabs((prev) => prev.map((t) => (t.id === activeTabId ? fn(t) : t))),
    [activeTabId],
  )
  const setActiveLayout = useCallback((l: Layout) => updateActive((t) => ({ ...t, layout: l })), [updateActive])

  useEffect(() => {
    setLoading(true)
    const tzOffset = new Date().getTimezoneOffset()
    const range = custom ? `start=${custom.start}&end=${custom.end}` : `days=${days}`
    api
      .get<StatsResponse>(`/stats?${range}&tz_offset=${tzOffset}`)
      .then(setStats)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [days, custom])

  useEffect(() => {
    api.get<CompletionEstimate[]>('/stats/completion-estimates').then(setEstimates).catch(() => {})
  }, [])

  const addWidget = useCallback(
    (defId: string) => {
      const def = defById(defId)
      const id = `${defId}--${nextN.current}`
      nextN.current += 1
      updateActive((t) => {
        const maxY = t.layout.reduce((m, it) => Math.max(m, it.y + it.h), 0)
        return {
          ...t,
          tiles: [...t.tiles, { id, defId, config: def.defaultConfig ?? DEFAULT_CFG }],
          layout: [...t.layout, { i: id, x: 0, y: maxY, ...def.size }],
        }
      })
      setAddOpen(false)
    },
    [updateActive],
  )

  const removeTile = useCallback(
    (id: string) => updateActive((t) => ({ ...t, tiles: t.tiles.filter((x) => x.id !== id), layout: t.layout.filter((it) => it.i !== id) })),
    [updateActive],
  )

  const setConfig = useCallback(
    (id: string, partial: Partial<TileConfig>) =>
      updateActive((t) => ({ ...t, tiles: t.tiles.map((x) => (x.id === id ? { ...x, config: { ...x.config, ...partial } } : x)) })),
    [updateActive],
  )

  const reset = useCallback(() => {
    const def = TAB_DEFS.find((d) => d.id === activeTabId)
    if (def) setTabs((prev) => prev.map((t) => (t.id === activeTabId ? buildTab(def) : t)))
  }, [activeTabId])

  const addTab = useCallback(() => {
    const id = `tab-${nextTab.current++}`
    setTabs((prev) => [...prev, { id, label: 'New board', tiles: [], layout: [] }])
    setActiveTabId(id)
    setRenamingId(id) // open straight into rename
  }, [])

  const renameTab = useCallback((id: string, label: string) => {
    setTabs((prev) => prev.map((t) => (t.id === id ? { ...t, label } : t)))
  }, [])

  const deleteTab = useCallback(
    (id: string) => {
      if (tabs.length <= 1) return
      const idx = tabs.findIndex((t) => t.id === id)
      const remaining = tabs.filter((t) => t.id !== id)
      setTabs(remaining)
      setActiveTabId((cur) => (cur === id ? (remaining[Math.max(0, idx - 1)] ?? remaining[0]).id : cur))
    },
    [tabs],
  )

  const isEmpty = stats && stats.headline.total_sessions === 0

  return (
    <div className={cn('py-6 transition-[padding] duration-200', PAD_X[pad])}>
      <style>{`
        .react-grid-item.react-grid-placeholder {
          background: var(--primary, #6366f1) !important;
          opacity: 0.1 !important;
          border-radius: 0.75rem;
        }
        .react-grid-item > .react-resizable-handle { display: none; }
        .lab-editing .react-grid-item > .react-resizable-handle { display: block; z-index: 30; opacity: 0; transition: opacity 120ms ease; }
        .lab-editing .react-grid-item:hover > .react-resizable-handle { opacity: 1; }
        .lab-editing .react-grid-item > .react-resizable-handle::after { border-color: var(--primary, #6366f1); border-width: 0 2px 2px 0; width: 9px; height: 9px; }
        .lab-editing .react-grid-item > .react-resizable-handle-se { width: 22px; height: 22px; right: 0; bottom: 0; cursor: se-resize; }
        .lab-editing .react-grid-item > .react-resizable-handle-e { width: 12px; cursor: e-resize; top: 0; height: 100%; right: 0; margin: 0; }
        .lab-editing .react-grid-item > .react-resizable-handle-s { height: 12px; cursor: s-resize; left: 0; width: 100%; bottom: 0; margin: 0; }
        .lab-editing .react-grid-item > .react-resizable-handle-e::after, .lab-editing .react-grid-item > .react-resizable-handle-s::after { display: none; }
        .lab-editing .react-grid-item { perspective: 1000px; }
      `}</style>

      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <FlaskConical className="h-5 w-5 text-primary" />
          <h1 className="text-xl font-semibold">Stats Lab</h1>
          <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">look + feel POC</span>
        </div>

        {/* range selector — presets + custom date range; refetches /stats */}
        <RangeControl
          days={days}
          custom={custom}
          onPreset={(d) => {
            setDays(d)
            setCustom(null)
          }}
          onCustom={setCustom}
        />

        {/* theme switcher — preview the dashboard in all built-in themes */}
        <div className="flex items-center gap-1.5">
          {THEMES.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                applyTheme(t.id)
                setTheme(t.id)
              }}
              title={t.label}
              aria-label={`${t.label} theme`}
              className={cn(
                'flex h-7 w-7 items-center justify-center rounded-full border-2 transition',
                theme === t.id ? 'border-primary' : 'border-transparent hover:border-border',
              )}
              style={{ background: t.preview.bg }}
            >
              <span className="h-3 w-3 rounded-full" style={{ background: t.preview.primary }} />
            </button>
          ))}
        </div>

        {editMode && (
          <>
            <div className="flex items-center gap-0.5 rounded-lg border border-border bg-card p-0.5 text-xs">
              <span className="px-1.5 text-muted-foreground">Padding</span>
              {(['none', 'bit', 'lot'] as const).map((w) => (
                <button
                  key={w}
                  type="button"
                  onClick={() => setPad(w)}
                  className={cn(
                    'rounded-md px-2 py-1 font-medium transition',
                    pad === w ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  {PAD_LABEL[w]}
                </button>
              ))}
            </div>
            <button type="button" onClick={() => setAddOpen(true)} className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1.5 text-sm font-medium text-foreground transition hover:bg-muted">
              <Plus className="h-4 w-4" /> Add tile
            </button>
            <button type="button" onClick={reset} className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1.5 text-sm font-medium text-muted-foreground transition hover:bg-muted">
              <RotateCcw className="h-4 w-4" /> Reset
            </button>
          </>
        )}
        <button
          type="button"
          onClick={() => setEditMode((e) => !e)}
          className={cn(
            'flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium transition',
            editMode
              ? 'border-primary bg-primary text-primary-foreground hover:bg-primary/90'
              : 'border-border bg-card text-foreground hover:bg-muted',
          )}
        >
          {editMode ? <Check className="h-4 w-4" /> : <Pencil className="h-4 w-4" />}
          {editMode ? 'Done' : 'Edit'}
        </button>
      </div>

      {/* tabs — each is its own customizable board; add / rename / delete in edit mode */}
      <div className="mb-4 flex flex-wrap items-center gap-1 border-b border-border">
        {tabs.map((t) => (
          <div
            key={t.id}
            className={cn('group/tab -mb-px flex items-center border-b-2', activeTabId === t.id ? 'border-primary' : 'border-transparent')}
          >
            {renamingId === t.id ? (
              <input
                autoFocus
                defaultValue={t.label}
                onFocus={(e) => e.target.select()}
                onBlur={(e) => {
                  renameTab(t.id, e.target.value.trim() || t.label)
                  setRenamingId(null)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                  if (e.key === 'Escape') setRenamingId(null)
                }}
                className="w-28 bg-transparent px-2 py-1.5 text-sm font-medium text-foreground outline-none"
              />
            ) : (
              <button
                type="button"
                onClick={() => setActiveTabId(t.id)}
                onDoubleClick={() => editMode && setRenamingId(t.id)}
                title={editMode ? 'Double-click to rename' : undefined}
                className={cn(
                  'px-3 py-1.5 text-sm font-medium transition',
                  activeTabId === t.id ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {t.label}
              </button>
            )}
            {editMode && tabs.length > 1 && renamingId !== t.id && (
              <button
                type="button"
                onClick={() => deleteTab(t.id)}
                aria-label={`Delete ${t.label}`}
                className="mr-1 rounded p-0.5 text-muted-foreground opacity-0 transition hover:text-foreground group-hover/tab:opacity-100"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>
        ))}
        {editMode && (
          <button
            type="button"
            onClick={addTab}
            aria-label="Add board"
            title="Add board"
            className="-mb-px ml-1 flex items-center gap-1 px-2 py-1.5 text-sm text-muted-foreground transition hover:text-foreground"
          >
            <Plus className="h-4 w-4" />
          </button>
        )}
      </div>

      <p className="mb-5 text-sm text-muted-foreground">
        {editMode
          ? 'Editing — drag tiles to rearrange and resize from the edges. Each tab is its own board.'
          : `Your ${active.label} board. Hit Edit to move, resize, add, or remove tiles — per tab.`}
      </p>

      {loading && !stats ? (
        <div className="flex justify-center py-32">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        </div>
      ) : isEmpty ? (
        <div className="flex flex-col items-center gap-3 py-32 text-muted-foreground">
          <FlaskConical className="h-12 w-12 opacity-20" />
          <p className="text-sm">No reading data in this range.</p>
        </div>
      ) : stats ? (
        active.tiles.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-24 text-muted-foreground">
            <Plus className="h-10 w-10 opacity-20" />
            <p className="text-sm">This board is empty.</p>
            <p className="text-xs">{editMode ? 'Use “Add tile” to build your ' + active.label + ' board.' : 'Hit Edit, then Add tile.'}</p>
          </div>
        ) : (
          <FreeGrid tiles={active.tiles} layout={active.layout} ctx={{ accent, stats, estimates }} editMode={editMode} onLayoutChange={setActiveLayout} onRemove={removeTile} onConfigChange={setConfig} />
        )
      ) : (
        <p className="py-32 text-center text-sm text-muted-foreground">Couldn’t load stats.</p>
      )}

      {addOpen && stats && (
        <AddWidgetModal ctx={{ accent, stats, estimates }} present={new Set(active.tiles.map((t) => t.defId))} onAdd={addWidget} onClose={() => setAddOpen(false)} />
      )}
    </div>
  )
}

export default StatsLabPage
