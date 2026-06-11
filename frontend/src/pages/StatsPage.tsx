import { useCallback, useEffect, useRef, useState, type ReactNode, type MouseEvent, useMemo } from 'react'
import ReactGridLayout, {
  useContainerWidth,
  type Layout,
} from 'react-grid-layout'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import { Link } from 'react-router-dom'
import { toPng } from 'html-to-image'
import { ArrowLeft, Plus, RotateCcw, X, BarChart3, HelpCircle, Sparkles, SlidersHorizontal, Pencil, Check, GripVertical, Calendar, ChevronLeft, ChevronRight, Clock, Activity, BookCheck, Flame, FileText, Target, Gauge, Search, Copy, Loader2, CloudOff, Download, Upload, ImageDown, type LucideIcon } from 'lucide-react'
import { cn, formatDate, formatDuration } from '@/lib/utils'
import { api } from '@/lib/api'
import { SyncStatusBadge } from '@/components/SyncStatusBadge'
import { BookAnimation } from '@/components/BookAnimation'
import { DOCS, docsLink } from '@/lib/docs'
import { type StatsResponse, type CompletionEstimate } from '@/components/stats/shared'
import {
  type ChartKind,
  HeadlineStatBody,
  CurrentlyReading,
  ReadingTimePerDay,
  TopBooksByTime,
  ReadingActivity365,
  BooksFinishedArea,
  SessionLog,
  RecentlyFinished,
  StreakCalendar,
} from '@/components/stats/widgets/overview'
import {
  HourDowCard,
  SessionTimeline,
  ReadingPaceChart,
  ReadingSpeedTrend,
  CompletionEstimatesList,
  PeriodComparison,
  MonthlyComparison,
  DayOfWeekBar,
  TimeOfDaySplit,
  TimeByFormat,
} from '@/components/stats/widgets/habits'
import { PaceByFormat } from '@/components/stats/PaceByFormat'
import {
  YearInReview,
  CategoryBreakdown,
  GenreOverTime,
  PerBookTimeTable,
  SeriesSpotlight,
} from '@/components/stats/widgets/library'
import { SeriesCompletionGrid } from '@/components/stats/SeriesCompletionGrid'
import { AuthorAffinity } from '@/components/stats/AuthorAffinity'
import { CompletionByType } from '@/components/stats/CompletionByType'
import { LibraryGrowthChart } from '@/components/stats/LibraryGrowthChart'

/**
 * Reading Stats — a fully customisable dashboard. Every chart is a tile on a
 * drag/resize grid; boards (tabs) are per-user and persisted server-side. The
 * default boards replicate the classic stats layout.
 */

// Per-tile settings: chart style, timeframe and (for the Custom Stat tile) the
// metric. days = 0 follows the page range; days = N shows the last N days of the
// fetched window (sliced client-side, no extra fetches) — so two copies of one
// widget can show different timeframes.
type TileConfig = { chartType: ChartKind; days: number; metric?: string; series?: string }

// ── Widget catalog (renders shared Stats components from live data) ─────────────

type WidgetCtx = { stats: StatsResponse; estimates: CompletionEstimate[] | null }

type WidgetDef = {
  id: string
  title: string
  icon?: LucideIcon
  size: { w: number; h: number; minW: number; minH: number }
  chartTypes?: ChartKind[]
  metrics?: { id: string; label: string }[]
  /** Config picks a series (options come from the user's series_completion). */
  seriesPicker?: boolean
  defaultConfig?: TileConfig
  /** Dynamic tile-header title, e.g. the picked metric's name. */
  titleFor?: (config: TileConfig) => string
  /** Set when the widget ignores the page range picker (e.g. "12 mo") — rendered
      as a small chip so the fixed window is visible, not a surprise. */
  fixedWindow?: string
  /** List-like content with intrinsic height (not a fill-the-box chart): in view
      mode the tile shrinks to fit what's actually there, so two in-progress books
      don't rattle around a five-row tile. Edit mode shows the true template. */
  autoH?: boolean
  render: (ctx: WidgetCtx, config: TileConfig) => ReactNode
}

// Unique tile/tab ids — Date.now alone collides on rapid actions (duplicates).
let _uid = 0
const newId = (prefix: string) => `${prefix}--${Date.now().toString(36)}${(_uid++).toString(36)}`

// Metrics the Custom Stat tile can show — all derived from the /stats payload.
const METRICS = [
  { id: 'avg-session', label: 'Avg Session' },
  { id: 'time-per-day', label: 'Time / Day' },
  { id: 'pages-per-day', label: 'Pages / Day' },
  { id: 'pages-per-hour', label: 'Pages / Hour' },
  { id: 'best-day', label: 'Best Day' },
  { id: 'longest-session', label: 'Longest Session' },
  { id: 'books-started', label: 'Books Started' },
  { id: 'longest-streak', label: 'Longest Streak' },
]

function metricValue(stats: StatsResponse, id: string): { value: string; sub?: string } {
  const days = Math.max(1, stats.daily.length)
  const secs = stats.headline.total_reading_seconds
  switch (id) {
    case 'time-per-day':
      return { value: formatDuration(Math.round(secs / days)), sub: `over ${days} days` }
    case 'pages-per-day':
      return { value: String(Math.round(stats.headline.pages_turned / days)), sub: `over ${days} days` }
    case 'pages-per-hour':
      return { value: secs > 0 ? String(Math.round(stats.headline.pages_turned / (secs / 3600))) : '0', sub: 'average pace' }
    case 'best-day': {
      const best = stats.daily.reduce((a, b) => (b.seconds > a.seconds ? b : a), { date: '', seconds: 0, sessions: 0, pages: 0 })
      return best.seconds > 0 ? { value: formatDuration(best.seconds), sub: formatDate(best.date) } : { value: '0m', sub: 'no reading yet' }
    }
    case 'longest-session': {
      let top: StatsResponse['session_timeline'][number] | null = null
      for (const s of stats.session_timeline) if (!top || s.duration_seconds > top.duration_seconds) top = s
      return top ? { value: formatDuration(top.duration_seconds), sub: top.title } : { value: '0m', sub: 'no sessions yet' }
    }
    case 'books-started':
      return { value: String(stats.completion_rate.started), sub: `${stats.completion_rate.finished} finished` }
    case 'longest-streak':
      return { value: `${stats.headline.longest_streak_days}d` }
    case 'avg-session':
    default:
      return { value: formatDuration(stats.headline.avg_session_seconds), sub: `${stats.headline.total_sessions} sessions` }
  }
}

// Per-tile timeframe slicing. `daily` is gap-filled (one row per day), so the last
// N rows are the last N days; books_finished is sparse, so cut by the same date.
const lastDays = (stats: StatsResponse, days: number) => (days > 0 ? stats.daily.slice(-days) : stats.daily)
const finishedSince = (stats: StatsResponse, days: number) => {
  if (days <= 0) return stats.books_finished
  const cutoff = stats.daily.slice(-days)[0]?.date ?? ''
  return stats.books_finished.filter((b) => b.date >= cutoff)
}

const STAT_SIZE = { w: 2, h: 1, minW: 2, minH: 1 }

const WIDGETS: WidgetDef[] = [
  // Headline figures — one tile each (separately movable / removable).
  {
    id: 'stat-time',
    title: 'Reading Time',
    icon: Clock,
    size: STAT_SIZE,
    render: ({ stats }) => (
      <HeadlineStatBody value={formatDuration(stats.headline.total_reading_seconds)} sub={`avg ${formatDuration(stats.headline.avg_session_seconds)} / session`} />
    ),
  },
  {
    id: 'stat-sessions',
    title: 'Sessions',
    icon: Activity,
    size: STAT_SIZE,
    render: ({ stats }) => <HeadlineStatBody value={String(stats.headline.total_sessions)} />,
  },
  {
    id: 'stat-finished',
    title: 'Books Finished',
    icon: BookCheck,
    size: STAT_SIZE,
    render: ({ stats }) => <HeadlineStatBody value={String(stats.headline.books_finished)} />,
  },
  {
    id: 'stat-streak',
    title: 'Streak',
    icon: Flame,
    size: STAT_SIZE,
    render: ({ stats }) => <HeadlineStatBody value={`${stats.headline.current_streak_days}d`} sub={`Longest: ${stats.headline.longest_streak_days}d`} />,
  },
  {
    id: 'stat-pages',
    title: 'Pages Turned',
    icon: FileText,
    size: STAT_SIZE,
    render: ({ stats }) => <HeadlineStatBody value={stats.headline.pages_turned.toLocaleString()} />,
  },
  {
    id: 'stat-completion',
    title: 'Completion Rate',
    icon: Target,
    size: STAT_SIZE,
    render: ({ stats }) => <HeadlineStatBody value={`${stats.completion_rate.pct}%`} sub={`${stats.completion_rate.finished} of ${stats.completion_rate.started} started`} />,
  },
  {
    id: 'stat-metric',
    title: 'Custom Stat',
    icon: Gauge,
    size: STAT_SIZE,
    metrics: METRICS,
    defaultConfig: { chartType: 'bar', days: 0, metric: 'avg-session' },
    titleFor: (cfg) => METRICS.find((m) => m.id === cfg.metric)?.label ?? 'Custom Stat',
    render: ({ stats }, cfg) => {
      const v = metricValue(stats, cfg.metric ?? 'avg-session')
      return <HeadlineStatBody value={v.value} sub={v.sub} />
    },
  },
  {
    id: 'currently-reading',
    title: 'Currently Reading',
    size: { w: 6, h: 2, minW: 3, minH: 1 },
    autoH: true,
    render: ({ stats }) => <CurrentlyReading books={stats.books_in_progress} />,
  },
  {
    id: 'daily',
    title: 'Reading Time per Day',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    chartTypes: ['bar', 'line', 'area'],
    defaultConfig: { chartType: 'bar', days: 0 },
    render: ({ stats }, cfg) => <ReadingTimePerDay daily={lastDays(stats, cfg.days)} chartType={cfg.chartType} />,
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
    chartTypes: ['area', 'line', 'bar'],
    defaultConfig: { chartType: 'area', days: 0 },
    render: ({ stats }, cfg) => <BooksFinishedArea booksFinished={finishedSince(stats, cfg.days)} chartType={cfg.chartType} />,
  },
  {
    id: 'activity-365',
    title: 'Reading Activity — Last 365 Days',
    size: { w: 12, h: 2, minW: 4, minH: 2 },
    fixedWindow: '365 d',
    render: ({ stats }) => <ReadingActivity365 heatmap={stats.heatmap_daily} />,
  },
  {
    id: 'session-log',
    title: 'Recent Sessions',
    size: { w: 12, h: 6, minW: 5, minH: 2 },
    autoH: true,
    render: () => <SessionLog />,
  },
  {
    id: 'recently-finished',
    title: 'Recently Finished',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    autoH: true,
    render: ({ stats }) => <RecentlyFinished booksFinished={stats.books_finished} />,
  },
  {
    id: 'streak-calendar',
    title: 'This Month',
    size: { w: 3, h: 2, minW: 2, minH: 2 },
    fixedWindow: 'current',
    render: ({ stats }) => <StreakCalendar heatmap={stats.heatmap_daily} />,
  },
  {
    id: 'dow-bar',
    title: 'Reading by Weekday',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <DayOfWeekBar data={stats.hour_dow_heatmap} />,
  },
  {
    id: 'time-of-day',
    title: 'Time of Day Split',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <TimeOfDaySplit data={stats.hour_dow_heatmap} />,
  },
  {
    id: 'time-by-format',
    title: 'Time by Format',
    size: { w: 6, h: 2, minW: 3, minH: 2 },
    render: ({ stats }) => <TimeByFormat data={stats.pace_by_format} />,
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
    autoH: true,
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
    fixedWindow: '12 mo',
    chartTypes: ['bar', 'line', 'area'],
    defaultConfig: { chartType: 'bar', days: 0 },
    render: ({ stats }, cfg) => <MonthlyComparison monthly={stats.monthly_comparison} chartType={cfg.chartType} />,
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
    autoH: true,
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
    fixedWindow: '12 mo',
    chartTypes: ['area', 'bar'],
    defaultConfig: { chartType: 'area', days: 0 },
    render: ({ stats }, cfg) => <GenreOverTime data={stats.genre_over_time} chartType={cfg.chartType === 'bar' ? 'bar' : 'area'} />,
  },
  {
    id: 'library-growth',
    title: 'Cumulative Books Added — Last 24 Months',
    size: { w: 12, h: 2, minW: 4, minH: 2 },
    fixedWindow: '24 mo',
    chartTypes: ['area', 'bar'],
    defaultConfig: { chartType: 'area', days: 0 },
    render: ({ stats }, cfg) => <LibraryGrowthChart data={stats.library_growth} height="100%" chartType={cfg.chartType === 'bar' ? 'bar' : 'area'} />,
  },
  {
    id: 'per-book-table',
    title: 'All Books by Reading Time',
    size: { w: 12, h: 3, minW: 5, minH: 2 },
    autoH: true,
    render: ({ stats }) => <PerBookTimeTable data={stats.per_book_time} />,
  },
  {
    id: 'series-spotlight',
    title: 'Series Spotlight',
    size: { w: 3, h: 2, minW: 2, minH: 2 },
    seriesPicker: true,
    titleFor: (cfg) => cfg.series ?? 'Series Spotlight',
    render: ({ stats }, cfg) => <SeriesSpotlight data={stats.series_completion} series={cfg.series} />,
  },
]

const defById = (id: string) => WIDGETS.find((w) => w.id === id.replace(/--[a-z0-9]+$/, ''))!

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
  'session-log': 'Every session — paginated, deletable',
  'stat-metric': 'Pick your own metric — avg session, pages/day, best day…',
  'recently-finished': 'Latest finishes, newest first',
  'streak-calendar': 'This month, read days filled',
  'dow-bar': 'Which weekday you read most',
  'time-of-day': 'Morning vs evening reader?',
  'time-by-format': 'EPUB vs CBZ time split',
  'series-spotlight': 'One series front and center — you pick which',
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
const NATURAL_PREVIEW = new Set(['stat-time', 'stat-sessions', 'stat-finished', 'stat-streak', 'stat-pages', 'stat-completion', 'stat-metric', 'period-comparison'])

// List/table widgets whose content is a vertical list that can exceed the tile → scroll
// internally. Everything else (charts) clips (overflow-hidden) so nothing spills out.
const SCROLL_IDS = new Set([
  'currently-reading', 'estimates', 'session-timeline', 'series-completion', 'per-book-table',
  'author-affinity', 'completion-by-type', 'pace-by-format', 'session-log', 'recently-finished',
])

const GALLERY_GROUPS: { label: string; ids: string[] }[] = [
  {
    label: 'Overview',
    ids: ['stat-time', 'stat-sessions', 'stat-finished', 'stat-streak', 'stat-pages', 'stat-completion', 'stat-metric', 'currently-reading', 'recently-finished', 'streak-calendar', 'daily', 'top-books', 'books-finished', 'activity-365', 'session-log'],
  },
  {
    label: 'Habits',
    ids: ['hour-dow', 'session-timeline', 'reading-pace', 'pace-by-format', 'dow-bar', 'time-of-day', 'time-by-format', 'speed-trend', 'estimates', 'period-comparison', 'monthly-comparison'],
  },
  {
    label: 'Library',
    ids: ['year-in-review', 'series-completion', 'series-spotlight', 'author-affinity', 'completion-by-type', 'category-breakdown', 'genre-over-time', 'library-growth', 'per-book-table'],
  },
]

type Tile = { id: string; defId: string; config: TileConfig }

const DEFAULT_CFG: TileConfig = { chartType: 'bar', days: 0 }

// Default boards replicate the current Stats page 1:1 (sizes mapped from its
// measured card heights, ~120px per grid row); fully rearrangeable from there.
const STAT_IDS = ['stat-time', 'stat-sessions', 'stat-finished', 'stat-streak', 'stat-pages', 'stat-completion']
const INITIAL_POS: Record<string, { x: number; y: number; w: number; h: number }> = {
  // row 0: six stat tiles across
  ...Object.fromEntries(STAT_IDS.map((id, i) => [id, { x: i * 2, y: 0, w: 2, h: 1 }])),
  // Overview — full-width Currently Reading, Daily | Top Books pair, the rest stacked
  'currently-reading': { x: 0, y: 1, w: 12, h: 5 },
  daily: { x: 0, y: 6, w: 6, h: 3 },
  'top-books': { x: 6, y: 6, w: 6, h: 3 },
  'activity-365': { x: 0, y: 9, w: 12, h: 2 },
  'books-finished': { x: 0, y: 11, w: 12, h: 2 },
  'session-log': { x: 0, y: 13, w: 12, h: 7 },
  // Habits — all full width except the Pace | Pace-by-Format pair
  'hour-dow': { x: 0, y: 0, w: 12, h: 2 },
  'session-timeline': { x: 0, y: 2, w: 12, h: 3 },
  'reading-pace': { x: 0, y: 5, w: 6, h: 3 },
  'pace-by-format': { x: 6, y: 5, w: 6, h: 3 },
  'speed-trend': { x: 0, y: 8, w: 12, h: 2 },
  estimates: { x: 0, y: 10, w: 12, h: 3 },
  'period-comparison': { x: 0, y: 13, w: 12, h: 1 },
  'monthly-comparison': { x: 0, y: 14, w: 12, h: 3 },
  // Library — all full width, stacked in page order
  'year-in-review': { x: 0, y: 0, w: 12, h: 2 },
  'series-completion': { x: 0, y: 2, w: 12, h: 4 },
  'author-affinity': { x: 0, y: 6, w: 12, h: 3 },
  'completion-by-type': { x: 0, y: 9, w: 12, h: 2 },
  'category-breakdown': { x: 0, y: 11, w: 12, h: 2 },
  'genre-over-time': { x: 0, y: 13, w: 12, h: 3 },
  'library-growth': { x: 0, y: 16, w: 12, h: 3 },
  'per-book-table': { x: 0, y: 19, w: 12, h: 6 },
}

// Each tab is its own board (own tiles + layout), independently customizable.
type TabState = { id: string; label: string; tiles: Tile[]; layout: Layout }

const TAB_DEFS: { id: string; label: string; ids: string[] }[] = [
  { id: 'overview', label: 'Overview', ids: [...STAT_IDS, 'currently-reading', 'daily', 'top-books', 'books-finished', 'activity-365', 'session-log'] },
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
    tiles: def.ids.map((id) => ({ id, defId: id, config: defById(id).defaultConfig ?? DEFAULT_CFG })),
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
// Per-tile timeframe options. 0 = follow the page's range picker.
const TIMEFRAMES = [
  { days: 0, label: 'Range' },
  { days: 7, label: '7d' },
  { days: 14, label: '14d' },
  { days: 30, label: '30d' },
]

// ── Config popover ────────────────────────────────────────────────────────────

function ConfigPopover({
  def,
  config,
  seriesOptions,
  onChange,
  onClose,
}: {
  def: WidgetDef
  config: TileConfig
  seriesOptions?: string[]
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
        {def.seriesPicker && (
          <>
            <p className="mb-1.5 font-medium text-muted-foreground">Series</p>
            {seriesOptions && seriesOptions.length > 0 ? (
              <select
                value={config.series ?? seriesOptions[0]}
                onChange={(e) => onChange({ series: e.target.value })}
                className="w-full rounded-md border border-border bg-background px-1.5 py-1 text-xs text-foreground focus:border-primary focus:outline-none"
              >
                {seriesOptions.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            ) : (
              <p className="text-muted-foreground/70">No series with reading progress yet.</p>
            )}
          </>
        )}
        {def.metrics && (
          <>
            <p className="mb-1.5 font-medium text-muted-foreground">Metric</p>
            <div className="grid grid-cols-2 gap-1">
              {def.metrics.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => onChange({ metric: m.id })}
                  className={cn(
                    'rounded-md border px-1.5 py-1 text-left transition',
                    config.metric === m.id ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:text-foreground',
                  )}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </>
        )}
        {def.chartTypes && (
          <>
            <p className="mb-1.5 font-medium text-muted-foreground">Chart type</p>
            <div className="mb-3 flex rounded-md border border-border p-0.5">
              {def.chartTypes.map((ct) => (
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
              {TIMEFRAMES.map((t) => (
                <button
                  key={t.days}
                  type="button"
                  onClick={() => onChange({ days: t.days })}
                  className={cn(
                    'flex-1 rounded-md border px-1.5 py-1 transition',
                    config.days === t.days ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:text-foreground',
                  )}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <p className="mt-2 text-[10px] leading-snug text-muted-foreground/70">Range follows the page's range picker; days show the newest slice of it.</p>
          </>
        )}
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
  const [q, setQ] = useState('')
  const [boardFilter, setBoardFilter] = useState<'all' | 'on' | 'off'>('all')
  const needle = q.trim().toLowerCase()
  const matches = (id: string) => {
    if (boardFilter === 'on' && !present.has(id)) return false
    if (boardFilter === 'off' && present.has(id)) return false
    if (!needle) return true
    const w = defById(id)
    return w.title.toLowerCase().includes(needle) || (WIDGET_DESC[id] ?? '').toLowerCase().includes(needle)
  }
  const groups = GALLERY_GROUPS.map((g) => ({ ...g, ids: g.ids.filter(matches) })).filter((g) => g.ids.length > 0)

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-3xl max-h-[85vh] overflow-y-auto rounded-2xl border border-border bg-card p-5 shadow-xl shadow-accent-soft">
        <div className="mb-3 flex items-start justify-between">
          <div>
            <h2 className="text-base font-semibold">Add a widget</h2>
            <p className="text-xs text-muted-foreground">Removed a tile? Add it back — or add another copy.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mb-4 flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search widgets…"
              className="w-full rounded-lg border border-border bg-background py-1.5 pl-8 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none"
            />
          </div>
          {/* quick filter: every widget vs only those already on / not yet on this board */}
          <div className="flex shrink-0 items-center gap-0.5 rounded-lg bg-muted p-0.5 text-xs">
            {([['all', 'All'], ['on', 'On board'], ['off', 'Not on board']] as const).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setBoardFilter(id)}
                className={cn(
                  'rounded-md px-2 py-1 font-medium transition',
                  boardFilter === id ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {groups.length === 0 && (
          <p className="py-10 text-center text-sm text-muted-foreground">{needle ? `No widgets match “${q}”.` : boardFilter === 'on' ? 'No widgets on this board yet.' : 'Every widget is already on this board.'}</p>
        )}

        <div className="flex flex-col gap-5">
          {groups.map((group) => (
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
                          <div className="h-full w-full p-3">{w.render(ctx, w.defaultConfig ?? DEFAULT_CFG)}</div>
                        ) : (
                          <div className="origin-top-left p-2.5" style={{ width: 360, height: 152, transform: 'scale(0.62)' }}>
                            {w.render(ctx, w.defaultConfig ?? DEFAULT_CFG)}
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
  onDuplicate,
  onConfigChange,
  seriesOptions,
  dragHandleProps,
  dragging,
  stacked,
  onMeasure,
}: {
  def: WidgetDef
  config: TileConfig
  editMode: boolean
  children: ReactNode
  onRemove: () => void
  onDuplicate?: () => void
  onConfigChange: (partial: Partial<TileConfig>) => void
  seriesOptions?: string[]
  dragHandleProps?: Record<string, unknown>
  dragging?: boolean
  /** Narrow-screen fallback: tiles render in a plain column, so no drag affordances. */
  stacked?: boolean
  /** autoH widgets report the grid rows their content actually needs. */
  onMeasure?: (rows: number) => void
}) {
  const [cfgOpen, setCfgOpen] = useState(false)
  const [hovering, setHovering] = useState(false)
  const [glare, setGlare] = useState({ x: 50, y: 50 })
  const tiltRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  // Measure after every commit: data loads, range/config changes, and grid width
  // changes all re-render this tree, and each can change the content's height.
  // scrollHeight sees the intrinsic height even when the box clips it.
  useEffect(() => {
    const box = contentRef.current
    if (!onMeasure || !box || !tiltRef.current) return
    // scrollHeight can't see underflow (it's floored at the box height), so take
    // the children's real extent: stretched children just reproduce the box and
    // the measurement becomes a no-op, intrinsic content reveals its true height.
    const boxTop = box.getBoundingClientRect().top
    const kids = Array.from(box.children) as HTMLElement[]
    const extent = kids.length
      ? Math.max(...kids.map((el) => el.getBoundingClientRect().bottom)) - boxTop
      : 0
    const chrome = tiltRef.current.offsetHeight - box.offsetHeight
    const desired = chrome + Math.max(extent, box.scrollHeight > box.clientHeight ? box.scrollHeight : 0)
    // grid: rowHeight 104 + margin 16 => h rows span 120h - 16 px
    onMeasure(Math.max(1, Math.ceil((desired + 16) / 120)))
  })
  const configurable = !!def.chartTypes || !!def.metrics || !!def.seriesPicker
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
        // marks the grid item so it stacks above siblings while the popover is open
        // (each grid item is transformed = its own stacking context, so the
        // popover's own z-index can't escape without raising the item itself)
        cfgOpen && 'cfg-popover-open',
      )}
    >
      {editMode && (
        <div
          className="pointer-events-none absolute inset-0 z-20 rounded-xl transition-opacity duration-300"
          style={{
            opacity: hovering ? 1 : 0,
            background: `radial-gradient(circle at ${glare.x}% ${glare.y}%, color-mix(in oklab, var(--foreground) 5%, transparent) 0%, transparent 60%)`,
          }}
        />
      )}
      <div
        {...(editMode && !stacked ? dragHandleProps : {})}
        className={cn('mb-3 flex items-center gap-1.5', editMode && !stacked && 'tile-drag-handle cursor-grab active:cursor-grabbing')}
      >
        {editMode && !stacked && <GripVertical className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50" />}
        {def.icon && <def.icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />}
        {/* truncate, don't wrap — a wrapped title pushes the body out of 1-row tiles */}
        <h3 className="min-w-0 truncate text-xs font-semibold uppercase tracking-wide text-muted-foreground">{def.titleFor?.(config) ?? def.title}</h3>
        {def.fixedWindow && (
          <span title="This tile uses a fixed window and ignores the range picker" className="shrink-0 rounded bg-muted px-1 py-px text-[9px] font-medium text-muted-foreground">
            {def.fixedWindow}
          </span>
        )}
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
            {onDuplicate && (
              <button
                type="button"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={onDuplicate}
                className="no-drag -my-1 rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                aria-label={`Duplicate ${def.title}`}
                title="Duplicate tile"
              >
                <Copy className="h-3.5 w-3.5" />
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
        <ConfigPopover def={def} config={config} seriesOptions={seriesOptions} onChange={onConfigChange} onClose={() => setCfgOpen(false)} />
      )}
      <div
        ref={contentRef}
        className={cn('min-h-0 flex-1', SCROLL_IDS.has(def.id) ? 'overflow-y-auto' : 'overflow-hidden')}
        // edit mode: tiles are objects being arranged, not content — swallow clicks
        // so links/buttons inside (covers, table sorting, …) can't navigate away.
        // Capture phase, so inner handlers never fire; scrolling still works.
        onClickCapture={editMode ? (e) => { e.preventDefault(); e.stopPropagation() } : undefined}
      >
        {children}
      </div>
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
  onDuplicate,
  onConfigChange,
}: {
  tiles: Tile[]
  layout: Layout
  ctx: WidgetCtx
  editMode: boolean
  onLayoutChange: (l: Layout) => void
  onRemove: (id: string) => void
  onDuplicate: (id: string) => void
  onConfigChange: (id: string, partial: Partial<TileConfig>) => void
}) {
  const { width, containerRef, mounted } = useContainerWidth()
  // live "6 × 3" badge during resize — updated imperatively (no re-renders)
  const badgeRef = useRef<HTMLDivElement>(null)
  const seriesOptions = ctx.stats.series_completion.map((x) => x.series)

  // autoH: list-like tiles report the rows their content needs; in view mode the
  // tile shrinks to that (never below minH, never above the saved size) and RGL's
  // vertical compaction pulls the rest of the board up. Edit mode shows the saved
  // template untouched, and only edit-mode layouts are ever persisted — so this
  // stays a render-time fit, reactive to whatever the data does next.
  const [autoRows, setAutoRows] = useState<Record<string, number>>({})
  const reportRows = useCallback((id: string, rows: number) => {
    setAutoRows((prev) => (prev[id] === rows ? prev : { ...prev, [id]: rows }))
  }, [])
  const displayLayout = useMemo(() => {
    if (editMode) return layout
    return layout.map((it) => {
      const rows = autoRows[it.i]
      if (!rows || rows >= it.h) return it
      return { ...it, h: Math.max(rows, it.minH ?? 1) }
    })
  }, [layout, autoRows, editMode])

  // RGL doesn't scroll the window when a drag/resize gesture reaches the viewport
  // edge, which makes a bottom-of-page tile impossible to grow — the pointer just
  // hits the screen edge. Track grid gestures and auto-scroll near the edges.
  useEffect(() => {
    if (!editMode) return
    let pointerY = -1
    let active = false
    let resizingEl: HTMLElement | null = null
    let raf = 0
    let lastT = performance.now()
    const BOTTOM_EDGE = 80
    const TOP_EDGE = 150 // sticky header + margin
    // time-based (not per-frame) so the speed is identical at any frame rate:
    // ramps from ~120px/s at the zone edge to ~600px/s pressed against the screen
    const speedFor = (depth: number) => 120 + Math.min(1, depth / BOTTOM_EDGE) * 480
    const loop = (now: number) => {
      const dt = Math.min((now - lastT) / 1000, 0.05)
      lastT = now
      if (active && pointerY >= 0) {
        const h = window.innerHeight
        if (pointerY > h - BOTTOM_EDGE) {
          window.scrollBy(0, speedFor(pointerY - (h - BOTTOM_EDGE)) * dt)
        } else if (pointerY < TOP_EDGE && window.scrollY > 0) {
          window.scrollBy(0, -speedFor(TOP_EDGE - pointerY) * dt)
        }
      }
      // live size badge: read the resizing item's box and snap it to grid units
      const badge = badgeRef.current
      if (badge) {
        if (active && resizingEl && containerRef.current) {
          const r = resizingEl.getBoundingClientRect()
          const colW = (containerRef.current.getBoundingClientRect().width - 11 * 16) / 12
          const gw = Math.max(1, Math.round((r.width + 16) / (colW + 16)))
          const gh = Math.max(1, Math.round((r.height + 16) / 120))
          badge.textContent = `${gw} × ${gh}`
          badge.style.left = `${r.right - 8}px`
          badge.style.top = `${r.bottom - 8}px`
          badge.classList.remove('hidden')
        } else {
          badge.classList.add('hidden')
        }
      }
      raf = requestAnimationFrame(loop)
    }
    const down = (e: PointerEvent) => {
      const t = e.target as HTMLElement
      const handle = t.closest?.('.react-resizable-handle')
      if (handle || t.closest?.('.tile-drag-handle')) {
        active = true
        pointerY = e.clientY
        resizingEl = handle ? (handle.closest('.react-grid-item') as HTMLElement) : null
      }
    }
    const move = (e: PointerEvent) => {
      pointerY = e.clientY
    }
    const stop = () => {
      active = false
      resizingEl = null
    }
    window.addEventListener('pointerdown', down, true)
    window.addEventListener('pointermove', move, true)
    window.addEventListener('pointerup', stop, true)
    window.addEventListener('pointercancel', stop, true)
    raf = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('pointerdown', down, true)
      window.removeEventListener('pointermove', move, true)
      window.removeEventListener('pointerup', stop, true)
      window.removeEventListener('pointercancel', stop, true)
    }
  }, [editMode, containerRef])

  // Narrow screens: a 12-col drag grid is unusable, so stack the tiles in layout
  // order (top-to-bottom, left-to-right) at their configured heights instead.
  if (mounted && width > 0 && width < 640) {
    const posOf = (id: string) => layout.find((l) => l.i === id)
    const ordered = [...tiles].sort((a, b) => {
      const la = posOf(a.id)
      const lb = posOf(b.id)
      return (la?.y ?? 0) - (lb?.y ?? 0) || (la?.x ?? 0) - (lb?.x ?? 0)
    })
    return (
      <div ref={containerRef} className="flex w-full flex-col gap-4">
        {ordered.map((t) => {
          const def = defById(t.defId)
          const h = posOf(t.id)?.h ?? def.size.h
          return (
            <div key={t.id} style={{ height: h * 104 + (h - 1) * 16 }}>
              <TileShell def={def} config={t.config} editMode={editMode} stacked seriesOptions={seriesOptions} onRemove={() => onRemove(t.id)} onDuplicate={() => onDuplicate(t.id)} onConfigChange={(p) => onConfigChange(t.id, p)}>
                {def.render(ctx, t.config)}
              </TileShell>
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <div ref={containerRef} className={cn('w-full', editMode && 'lab-editing')}>
      {mounted && width > 0 && (
        <ReactGridLayout
          width={width}
          layout={displayLayout}
          gridConfig={{ cols: 12, rowHeight: 104, margin: [16, 16], containerPadding: [0, 0] }}
          dragConfig={{ enabled: editMode, handle: '.tile-drag-handle', cancel: '.no-drag' }}
          resizeConfig={{ enabled: editMode, handles: ['se', 'e', 's'] }}
          // view mode renders the auto-fitted layout — persisting that would bake
          // a transient content size into the saved board, so edit-mode only
          onLayoutChange={(l: Layout) => { if (editMode) onLayoutChange(l) }}
        >
          {tiles.map((t) => {
            const def = defById(t.defId)
            return (
              <div key={t.id}>
                <TileShell def={def} config={t.config} editMode={editMode} seriesOptions={seriesOptions} onRemove={() => onRemove(t.id)} onDuplicate={() => onDuplicate(t.id)} onConfigChange={(p) => onConfigChange(t.id, p)} onMeasure={def.autoH ? (rows: number) => reportRows(t.id, rows) : undefined}>
                  {def.render(ctx, t.config)}
                </TileShell>
              </div>
            )
          })}
        </ReactGridLayout>
      )}
      <div
        ref={badgeRef}
        className="pointer-events-none fixed z-[70] hidden -translate-x-full -translate-y-full rounded-md border border-border bg-card px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-foreground shadow-md"
      />
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

// ── Persistence (localStorage for the POC; server-side comes later) ─────────────

// v2: defaults became the real-Stats-page replica — bumping the key discards
// boards saved under the old defaults so everyone starts from the replica.
const LS_KEY = 'tome_stats_lab_v2'

type Persisted = { tabs: TabState[]; activeTabId: string; pad: PadWidth; days: number }

// Drop tiles whose widget no longer exists in the catalog, and orphaned layout
// entries — saved state (localStorage or server) must never crash the page.
function sanitizePersisted(p: Persisted): Persisted | null {
  if (!Array.isArray(p.tabs) || p.tabs.length === 0) return null
  const tabs: TabState[] = p.tabs.map((t) => {
    const tiles = (t.tiles ?? []).filter((x) => WIDGETS.some((w) => w.id === x.defId))
    const ids = new Set(tiles.map((x) => x.id))
    return {
      id: String(t.id),
      label: String(t.label ?? 'Board'),
      tiles: tiles.map((x) => ({ ...x, config: { ...DEFAULT_CFG, ...(x.config ?? {}) } })),
      layout: (t.layout ?? []).filter((l) => ids.has(l.i)),
    }
  })
  return { ...p, tabs }
}

function loadPersisted(): Persisted | null {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return null
    return sanitizePersisted(JSON.parse(raw) as Persisted)
  } catch {
    return null
  }
}

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

export function StatsPage() {
  const [editMode, setEditMode] = useState(false)
  const [pad, setPad] = useState<PadWidth>(() => loadPersisted()?.pad ?? 'lot')
  const [addOpen, setAddOpen] = useState(false)
  const [days, setDays] = useState(() => loadPersisted()?.days ?? 30)
  const [custom, setCustom] = useState<CustomRange | null>(null)
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [estimates, setEstimates] = useState<CompletionEstimate[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [tabs, setTabs] = useState<TabState[]>(() => loadPersisted()?.tabs ?? buildTabs())
  const [activeTabId, setActiveTabId] = useState(() => loadPersisted()?.activeTabId ?? 'overview')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [tabMenuOpen, setTabMenuOpen] = useState(false)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [undo, setUndo] = useState<{ label: string; restore: () => void } | null>(null)
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // one-time discovery hint — the page looks like the old static stats page,
  // so first-timers need a single nudge that everything is editable
  const [hintDismissed, setHintDismissed] = useState(() => {
    try {
      return localStorage.getItem('tome_stats_hint') === '1'
    } catch {
      return true
    }
  })
  const dismissHint = useCallback(() => {
    setHintDismissed(true)
    try {
      localStorage.setItem('tome_stats_hint', '1')
    } catch {
      // storage unavailable — hint just reappears next visit
    }
  }, [])
  const active = tabs.find((t) => t.id === activeTabId) ?? tabs[0]
  // genuinely no reading history (not just a quiet range): full onboarding state
  const neverRead = !!stats && stats.headline.total_sessions === 0 && stats.heatmap_daily.every((d) => d.seconds === 0)

  const pushUndo = useCallback((label: string, restore: () => void) => {
    if (undoTimer.current) clearTimeout(undoTimer.current)
    setUndo({ label, restore })
    undoTimer.current = setTimeout(() => setUndo(null), 6000)
  }, [])

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

  // Server is the source of truth across browsers/devices; localStorage is a
  // fast-boot cache. Apply the server copy once on mount, and only start
  // pushing local changes after that (so a slow GET can't be clobbered).
  const serverReady = useRef(false)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const latest = useRef<Persisted>({ tabs, activeTabId, pad, days })

  useEffect(() => {
    api
      .get<{ data: Persisted | null }>('/stats/dashboard')
      .then((res) => {
        const p = res.data ? sanitizePersisted(res.data) : null
        if (p) {
          setTabs(p.tabs)
          setActiveTabId(p.activeTabId)
          if (p.pad) setPad(p.pad)
          if (p.days != null) setDays(p.days)
        }
      })
      .catch(() => {})
      .finally(() => {
        serverReady.current = true
      })
  }, [])

  // Persist boards + view settings: localStorage immediately, server debounced.
  useEffect(() => {
    const state: Persisted = { tabs, activeTabId, pad, days }
    latest.current = state
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(state))
    } catch {
      // storage unavailable — the dashboard still works, it just won't persist
    }
    if (!serverReady.current) return
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      setSaveState('saving')
      api
        .put('/stats/dashboard', { data: latest.current })
        .then(() => {
          setSaveState('saved')
          setTimeout(() => setSaveState((s) => (s === 'saved' ? 'idle' : s)), 1600)
        })
        .catch(() => setSaveState('error'))
    }, 800)
  }, [tabs, activeTabId, pad, days])

  // Flush a pending save when leaving the page (route change) so the last edit
  // isn't lost to the debounce window.
  useEffect(
    () => () => {
      if (saveTimer.current) {
        clearTimeout(saveTimer.current)
        api.put('/stats/dashboard', { data: latest.current }).catch(() => {})
      }
    },
    [],
  )

  const addWidget = useCallback(
    (defId: string) => {
      const def = defById(defId)
      const id = newId(defId)
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
    (id: string) => {
      const tabId = activeTabId
      const tab = tabs.find((t) => t.id === tabId)
      const tile = tab?.tiles.find((x) => x.id === id)
      const lay = tab?.layout.find((it) => it.i === id)
      updateActive((t) => ({ ...t, tiles: t.tiles.filter((x) => x.id !== id), layout: t.layout.filter((it) => it.i !== id) }))
      if (tile && lay) {
        const def = defById(tile.defId)
        pushUndo(`Removed “${def.titleFor?.(tile.config) ?? def.title}”`, () => {
          setTabs((prev) => prev.map((t) => (t.id === tabId ? { ...t, tiles: [...t.tiles, tile], layout: [...t.layout, lay] } : t)))
        })
      }
    },
    [tabs, activeTabId, updateActive, pushUndo],
  )

  // Clone a tile (config included) right below the original.
  const duplicateTile = useCallback(
    (id: string) => {
      updateActive((t) => {
        const tile = t.tiles.find((x) => x.id === id)
        const lay = t.layout.find((it) => it.i === id)
        if (!tile || !lay) return t
        const cloneId = newId(tile.defId)
        return {
          ...t,
          tiles: [...t.tiles, { ...tile, id: cloneId, config: { ...tile.config } }],
          layout: [...t.layout, { ...lay, i: cloneId, y: lay.y + lay.h }],
        }
      })
    },
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

  const addTabWith = useCallback((label: string, tiles: Tile[], layout: Layout, rename = false) => {
    const id = newId('tab')
    setTabs((prev) => [...prev, { id, label, tiles, layout }])
    setActiveTabId(id)
    if (rename) setRenamingId(id) // open straight into rename
    setTabMenuOpen(false)
  }, [])

  // ── Share: board file export/import + board-as-image ──────────────────────────

  const boardRef = useRef<HTMLDivElement>(null)
  const importInputRef = useRef<HTMLInputElement>(null)
  const [shotBusy, setShotBusy] = useState(false)

  const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'board'

  const downloadFile = (name: string, href: string) => {
    const a = document.createElement('a')
    a.href = href
    a.download = name
    a.click()
  }

  const exportBoard = useCallback(() => {
    const payload = { kind: 'tome-board', version: 1, label: active.label, tiles: active.tiles, layout: active.layout }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    downloadFile(`tome-board-${slug(active.label)}.json`, url)
    URL.revokeObjectURL(url)
  }, [active])

  const importBoard = useCallback(
    (file: File) => {
      file.text().then((text) => {
        try {
          const p = JSON.parse(text)
          if (p?.kind !== 'tome-board' || !Array.isArray(p.tiles) || !Array.isArray(p.layout)) throw new Error('not a board file')
          // unknown widgets (older/newer instance) are dropped, orphans pruned
          const tiles = (p.tiles as Tile[]).filter((x) => WIDGETS.some((w) => w.id === x.defId)).map((x) => ({ ...x, config: { ...DEFAULT_CFG, ...(x.config ?? {}) } }))
          const ids = new Set(tiles.map((x) => x.id))
          const layout = (p.layout as Layout).filter((l) => ids.has(l.i))
          addTabWith(String(p.label ?? 'Imported board'), tiles, layout)
        } catch {
          pushUndo('That file is not a Tome board export.', () => {})
        }
      })
    },
    [addTabWith, pushUndo],
  )

  const exportImage = useCallback(() => {
    const node = boardRef.current
    if (!node || shotBusy) return
    setShotBusy(true)
    const bg = getComputedStyle(document.body).backgroundColor
    toPng(node, { pixelRatio: 2, backgroundColor: bg })
      .then((png) => downloadFile(`tome-stats-${slug(active.label)}-${new Date().toISOString().slice(0, 10)}.png`, png))
      .catch(() => {})
      .finally(() => setShotBusy(false))
  }, [active.label, shotBusy])

  const renameTab = useCallback((id: string, label: string) => {
    setTabs((prev) => prev.map((t) => (t.id === id ? { ...t, label } : t)))
  }, [])

  const deleteTab = useCallback(
    (id: string) => {
      if (tabs.length <= 1) return
      const idx = tabs.findIndex((t) => t.id === id)
      const removed = tabs[idx]
      const remaining = tabs.filter((t) => t.id !== id)
      setTabs(remaining)
      setActiveTabId((cur) => (cur === id ? (remaining[Math.max(0, idx - 1)] ?? remaining[0]).id : cur))
      pushUndo(`Deleted board “${removed.label}”`, () => {
        setTabs((prev) => {
          const next = [...prev]
          next.splice(Math.min(idx, next.length), 0, removed)
          return next
        })
        setActiveTabId(removed.id)
      })
    },
    [tabs, pushUndo],
  )

  // Esc backs out: open modal first, then edit mode itself.
  useEffect(() => {
    if (!editMode) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (addOpen) setAddOpen(false)
      else if (tabMenuOpen) setTabMenuOpen(false)
      else setEditMode(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editMode, addOpen, tabMenuOpen])

  return (
    <div className="min-h-screen bg-background">
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
        /* corner handle sits above the edge strips so diagonal resize stays grabbable */
        .lab-editing .react-grid-item > .react-resizable-handle-se { width: 22px; height: 22px; right: 0; bottom: 0; cursor: se-resize; z-index: 31; }
        /* transform: none — react-resizable's base CSS rotates edge handles 45°,
           which turns these strips into huge invisible boxes that steal clicks
           from the tile header buttons and body content. The strips also stop
           short of the corner so they never cover the se handle. */
        .lab-editing .react-grid-item > .react-resizable-handle-e { width: 12px; cursor: e-resize; top: 0; height: calc(100% - 26px); right: 0; margin: 0; transform: none; background: none; }
        .lab-editing .react-grid-item > .react-resizable-handle-s { height: 12px; cursor: s-resize; left: 0; width: calc(100% - 26px); bottom: 0; margin: 0; transform: none; background: none; }
        .lab-editing .react-grid-item > .react-resizable-handle-e::after, .lab-editing .react-grid-item > .react-resizable-handle-s::after { display: none; }
        .lab-editing .react-grid-item { perspective: 1000px; }
        .react-grid-item:has(> .cfg-popover-open) { z-index: 40; }
      `}</style>

      <header className="sticky top-0 z-20 border-b border-border bg-background/80 backdrop-blur-sm safe-top">
        <div className={cn('flex h-14 items-center gap-3 transition-[padding] duration-200', PAD_X[pad])}>
          <Link to="/" className="-ml-2 rounded-lg p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <BarChart3 className="h-4 w-4 text-muted-foreground" />
          <span className="hidden text-base font-bold sm:inline">Reading Stats</span>
          <a
            href={docsLink(DOCS.stats)}
            target="_blank"
            rel="noopener noreferrer"
            title="What do these mean? — read the stats docs"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <HelpCircle className="h-3.5 w-3.5" />
          </a>
          <div className="ml-auto flex items-center gap-2">
            <SyncStatusBadge />
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
          </div>
        </div>

        {/* board tabs (pills, like the Stats page) + board tools */}
        <div className="overflow-x-auto border-t border-border/50">
          <div className={cn('flex items-center gap-1 py-1.5 transition-[padding] duration-200', PAD_X[pad])}>
            {tabs.map((t) => (
              <div
                key={t.id}
                className={cn(
                  'group/tab flex shrink-0 items-center rounded-md transition-all',
                  activeTabId === t.id ? 'bg-muted text-foreground shadow-sm' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                )}
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
                      if (e.key === 'Escape') {
                        e.stopPropagation() // cancel the rename only, not edit mode
                        setRenamingId(null)
                      }
                    }}
                    className="w-24 bg-transparent px-3 py-1.5 text-xs font-medium text-foreground outline-none"
                  />
                ) : (
                  <button
                    type="button"
                    onClick={() => setActiveTabId(t.id)}
                    onDoubleClick={() => editMode && setRenamingId(t.id)}
                    title={editMode ? 'Double-click to rename' : undefined}
                    className="whitespace-nowrap px-3 py-1.5 text-xs font-medium"
                  >
                    {t.label}
                  </button>
                )}
                {editMode && renamingId !== t.id && (
                  <>
                    <button
                      type="button"
                      onClick={() => setRenamingId(t.id)}
                      aria-label={`Rename ${t.label}`}
                      title="Rename board"
                      className={cn('rounded p-0.5 text-muted-foreground transition hover:text-foreground', tabs.length > 1 ? '' : 'mr-1.5')}
                    >
                      <Pencil className="h-3 w-3" />
                    </button>
                    {tabs.length > 1 && (
                      <button
                        type="button"
                        onClick={() => deleteTab(t.id)}
                        aria-label={`Delete ${t.label}`}
                        title="Delete board"
                        className="mr-1.5 rounded p-0.5 text-muted-foreground transition hover:text-foreground"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    )}
                  </>
                )}
              </div>
            ))}
            {editMode && (
              <div className="relative shrink-0">
                <button
                  type="button"
                  onClick={() => setTabMenuOpen((o) => !o)}
                  aria-label="Add board"
                  title="Add board"
                  className={cn(
                    'flex items-center rounded-md px-2 py-1.5 transition hover:bg-muted/50 hover:text-foreground',
                    tabMenuOpen ? 'bg-muted/50 text-foreground' : 'text-muted-foreground',
                  )}
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
                {tabMenuOpen && (
                  <>
                    <div className="fixed inset-0 z-40" onPointerDown={() => setTabMenuOpen(false)} />
                    <div className="absolute left-0 top-full z-50 mt-1.5 w-48 rounded-lg border border-border bg-card p-1 text-xs shadow-xl">
                      <button
                        type="button"
                        onClick={() => addTabWith('New board', [], [], true)}
                        className="w-full rounded-md px-2 py-1.5 text-left text-foreground transition hover:bg-muted"
                      >
                        Empty board
                      </button>
                      <button
                        type="button"
                        onClick={() => addTabWith(`${active.label} copy`, active.tiles.map((x) => ({ ...x, config: { ...x.config } })), active.layout.map((l) => ({ ...l })))}
                        className="w-full truncate rounded-md px-2 py-1.5 text-left text-foreground transition hover:bg-muted"
                      >
                        Duplicate “{active.label}”
                      </button>
                      <button
                        type="button"
                        onClick={() => importInputRef.current?.click()}
                        className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-foreground transition hover:bg-muted"
                      >
                        <Upload className="h-3 w-3 text-muted-foreground" /> Import board…
                      </button>
                      <div className="my-1 border-t border-border" />
                      <p className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Start from default</p>
                      {TAB_DEFS.map((d) => (
                        <button
                          key={d.id}
                          type="button"
                          onClick={() => {
                            const b = buildTab(d)
                            addTabWith(d.label, b.tiles, b.layout)
                          }}
                          className="w-full rounded-md px-2 py-1.5 text-left text-foreground transition hover:bg-muted"
                        >
                          {d.label}
                        </button>
                      ))}
                    </div>
                    <input
                      ref={importInputRef}
                      type="file"
                      accept="application/json,.json"
                      className="hidden"
                      onChange={(e) => {
                        const f = e.target.files?.[0]
                        if (f) importBoard(f)
                        e.target.value = ''
                      }}
                    />
                  </>
                )}
              </div>
            )}

            {/* board tools — hidden below sm, where the stacked view can't arrange anyway */}
            <div className="ml-auto hidden shrink-0 items-center gap-1.5 pl-3 sm:flex">
              {saveState !== 'idle' && (
                <span
                  title={saveState === 'error' ? 'Saving to the server failed — changes are kept locally' : undefined}
                  className={cn('flex items-center gap-1 text-[10px]', saveState === 'error' ? 'text-red-500' : 'text-muted-foreground')}
                >
                  {saveState === 'saving' ? <Loader2 className="h-3 w-3 animate-spin" /> : saveState === 'saved' ? <Check className="h-3 w-3" /> : <CloudOff className="h-3 w-3" />}
                  {saveState === 'saving' ? 'Saving' : saveState === 'saved' ? 'Saved' : 'Save failed'}
                </span>
              )}
              {editMode && (
                <>
                  <div className="flex items-center gap-0.5 rounded-lg bg-muted p-0.5 text-xs">
                    <span className="px-1.5 text-muted-foreground">Padding</span>
                    {(['none', 'bit', 'lot'] as const).map((w) => (
                      <button
                        key={w}
                        type="button"
                        onClick={() => setPad(w)}
                        className={cn(
                          'rounded-md px-2 py-0.5 font-medium transition',
                          pad === w ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
                        )}
                      >
                        {PAD_LABEL[w]}
                      </button>
                    ))}
                  </div>
                  <button type="button" onClick={() => setAddOpen(true)} className="flex items-center gap-1 rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-muted">
                    <Plus className="h-3.5 w-3.5" /> Add tile
                  </button>
                  <button type="button" onClick={reset} className="flex items-center gap-1 rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground transition hover:bg-muted">
                    <RotateCcw className="h-3.5 w-3.5" /> Reset
                  </button>
                  <button type="button" onClick={exportBoard} title="Export this board as a JSON file (share or back up)" className="flex items-center gap-1 rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground transition hover:bg-muted">
                    <Download className="h-3.5 w-3.5" /> Export
                  </button>
                </>
              )}
              <button
                type="button"
                onClick={exportImage}
                disabled={shotBusy}
                title="Save this board as an image"
                aria-label="Save board as image"
                className="flex items-center rounded-md border border-border bg-card p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-50"
              >
                {shotBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ImageDown className="h-3.5 w-3.5" />}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!hintDismissed) dismissHint()
                  setEditMode((e) => !e)
                }}
                className={cn(
                  'flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs font-medium transition',
                  editMode
                    ? 'border-primary bg-primary text-primary-foreground hover:bg-primary/90'
                    : 'border-border bg-card text-foreground hover:bg-muted',
                )}
              >
                {editMode ? <Check className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}
                {editMode ? 'Done' : 'Edit'}
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* edit mode gets extra bottom room so the last tile's resize handles have
          somewhere to scroll to */}
      <main className={cn('py-6 transition-[padding] duration-200', PAD_X[pad], editMode && 'pb-[35vh]')}>
      {/* one-time hint that the page is editable */}
      {stats && !neverRead && !hintDismissed && !editMode && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
          <Sparkles className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="text-foreground">
            This page is yours — every tile can be moved, resized, swapped or removed, per board. Hit{' '}
            <button
              type="button"
              onClick={() => {
                dismissHint()
                setEditMode(true)
              }}
              className="font-semibold text-primary hover:underline"
            >
              Edit
            </button>{' '}
            to start. Reset always brings the defaults back.
          </span>
          <button type="button" onClick={dismissHint} aria-label="Dismiss" className="ml-auto rounded p-0.5 text-muted-foreground transition hover:text-foreground">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Session-free ranges shouldn't blank the whole board — Library-tab tiles
          (growth, categories, per-book table) still have data to show. */}
      {stats && !neverRead && stats.headline.total_sessions === 0 && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <BarChart3 className="h-3.5 w-3.5 shrink-0 opacity-60" />
          No reading sessions in this range — session-based tiles will be empty.
        </div>
      )}

      {loading && !stats ? (
        <div className="flex justify-center py-32">
          <BookAnimation variant="refresh" className="block h-10 w-10 text-primary" />
        </div>
      ) : neverRead ? (
        <div className="flex flex-col items-center justify-center gap-4 py-32 text-muted-foreground">
          <BarChart3 className="h-16 w-16 opacity-20" />
          <p className="text-sm font-medium text-foreground">No reading data yet</p>
          <p className="max-w-xs text-center text-xs">
            Reading stats will appear here once you start using the TomeSync KOReader plugin.
          </p>
          <Link to="/settings" className="text-xs text-primary hover:underline">
            Download the plugin from Settings
          </Link>
        </div>
      ) : stats ? (
        active.tiles.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-24 text-muted-foreground">
            <Plus className="h-10 w-10 opacity-20" />
            <p className="text-sm">This board is empty.</p>
            <p className="text-xs">{editMode ? 'Use “Add tile” to build your ' + active.label + ' board.' : 'Hit Edit, then Add tile.'}</p>
          </div>
        ) : (
          <div ref={boardRef}>
            <FreeGrid tiles={active.tiles} layout={active.layout} ctx={{ stats, estimates }} editMode={editMode} onLayoutChange={setActiveLayout} onRemove={removeTile} onDuplicate={duplicateTile} onConfigChange={setConfig} />
          </div>
        )
      ) : (
        <p className="py-32 text-center text-sm text-muted-foreground">Couldn’t load stats.</p>
      )}
      </main>

      {addOpen && stats && (
        <AddWidgetModal ctx={{ stats, estimates }} present={new Set(active.tiles.map((t) => t.defId))} onAdd={addWidget} onClose={() => setAddOpen(false)} />
      )}

      {/* undo bar — tile removal and board deletion are recoverable for ~6s */}
      {undo && (
        <div className="fixed bottom-6 left-1/2 z-[70] flex -translate-x-1/2 items-center gap-3 rounded-lg border border-border bg-card px-3 py-2 text-sm shadow-xl">
          <span className="text-muted-foreground">{undo.label}</span>
          <button
            type="button"
            onClick={() => {
              undo.restore()
              setUndo(null)
            }}
            className="font-medium text-primary transition hover:underline"
          >
            Undo
          </button>
          <button type="button" onClick={() => setUndo(null)} aria-label="Dismiss" className="rounded p-0.5 text-muted-foreground transition hover:text-foreground">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  )
}

export default StatsPage
