/* eslint-disable react-refresh/only-export-components -- reader theme data lives beside its chrome */
import { type CSSProperties, type ReactNode } from 'react'
import { Trans } from '@lingui/react/macro'
import { t, msg } from '@lingui/core/macro'
import { i18n, type MessageDescriptor } from '@lingui/core'
import { ArrowLeft, ChevronLeft, ChevronRight, Minus, Plus, X } from 'lucide-react'
import { Pill, PillGroup } from '@/components/page'
import { cn } from '@/lib/utils'

// The reader's chrome (bars, panels, drawer) in the shell vocabulary. The
// reader has its own page themes (light / sepia / dark), so the chrome is
// drawn in the page's colours: the shell tokens (--background, --secondary,
// --foreground ...) are redefined on the reader root, and the shared Pill and
// PillGroup pick them up without any reader-specific styling.

export type ReaderTheme = 'light' | 'sepia' | 'dark'

export const THEMES: Record<ReaderTheme, { bg: string; text: string; label: MessageDescriptor }> = {
  light: { bg: '#ffffff', text: '#1a1a1a', label: msg`Light` },
  sepia: { bg: '#f7f3e9', text: '#5c4a32', label: msg`Sepia` },
  dark:  { bg: '#1c1c1e', text: '#e5e5e7', label: msg`Dark` },
}

/* eslint-disable lingui/no-unlocalized-strings -- CSS values */
const mix = (fg: string, pct: number, bg: string) => `color-mix(in srgb, ${fg} ${pct}%, ${bg})`

/** Shell tokens redefined in the reader theme's colours, for the reader root. */
export function readerVars(theme: ReaderTheme): CSSProperties {
  const { bg, text } = THEMES[theme]
  const dark = theme === 'dark'
  const primary = dark ? 'oklch(0.64 0.12 22)' : 'oklch(0.45 0.115 18)'
  return {
    background: bg,
    color: text,
    '--background': bg,
    '--foreground': text,
    '--card': bg,
    '--secondary': mix(text, dark ? 10 : 7, bg),
    '--surface-hover': mix(text, dark ? 16 : 12, bg),
    // Strong enough for the footer caption and card footers on the sepia page.
    '--muted-foreground': mix(text, dark ? 70 : 80, bg),
    '--border': mix(text, 12, bg),
    '--primary': primary,
    '--primary-foreground': 'oklch(0.98 0.005 85)',
    '--ring': primary,
  } as CSSProperties
}
/* eslint-enable lingui/no-unlocalized-strings */

/** 12px small-caps label over a settings row. */
export function ChromeLabel({ children }: { children: ReactNode }) {
  return <p className="mb-2.5 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">{children}</p>
}

/** The Preview tag: a small-caps chip. */
export function PreviewChip() {
  return (
    <span
      className="inline-flex h-6 shrink-0 items-center rounded-full bg-secondary px-2.5 text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground"
      title={t`Preview: progress and stats are not updated`}
    >
      <Trans>Preview</Trans>
    </span>
  )
}

/** Icon pill for the bars; `active` gives it the filled look of a pressed toggle. */
export function ChromeIcon({ active, className, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <Pill
      icon
      variant={active ? 'secondary' : 'quiet'}
      aria-pressed={active}
      aria-label={rest['aria-label'] ?? (typeof rest.title === 'string' ? rest.title : undefined)}
      className={cn('shrink-0 [&_svg]:h-4 [&_svg]:w-4', active ? 'text-foreground' : 'text-foreground/70', className)}
      {...rest}
    />
  )
}

/** Top bar: back, title (truncates), Preview tag, then the actions. */
export function ReaderTopBar({ title, preview, onBack, children, className }: {
  title: string
  preview: boolean
  onBack: () => void
  children?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('z-10 flex min-h-12 shrink-0 items-center gap-1 bg-background/85 px-2 backdrop-blur safe-top sm:gap-1.5 sm:px-3', className)}>
      <ChromeIcon onClick={onBack} title={t`Back`}>
        <ArrowLeft />
      </ChromeIcon>
      <span data-reader-title className="ml-1 min-w-0 flex-1 truncate text-[14px] font-medium text-foreground" title={title}>
        {title}
      </span>
      {preview && <PreviewChip />}
      <div className="flex shrink-0 items-center gap-0.5 sm:gap-1">{children}</div>
    </div>
  )
}

/** Bottom bar: prev, centre caption, progress bar + percentage, next. */
export function ReaderBottomBar({ onPrev, onNext, caption, progress, className }: {
  onPrev: () => void
  onNext: () => void
  caption: ReactNode
  progress: number
  className?: string
}) {
  return (
    <div className={cn('z-10 flex h-11 shrink-0 items-center gap-2 bg-background/85 px-1.5 backdrop-blur sm:gap-4 sm:px-3', className)}>
      <ChromeIcon onClick={onPrev} title={t`Previous page`}><ChevronLeft /></ChromeIcon>
      <span className="min-w-0 flex-1 truncate text-center text-[13px] text-muted-foreground">{caption}</span>
      <div className="flex shrink-0 items-center gap-2">
        <div className="h-1 w-16 overflow-hidden rounded-full bg-secondary sm:w-24">
          <div className="h-full rounded-full bg-foreground/50 transition-all duration-300" style={{ width: `${progress}%` }} />
        </div>
        <span className="w-9 text-right text-[13px] proportional-nums text-muted-foreground">{progress}%</span>
      </div>
      <ChromeIcon onClick={onNext} title={t`Next page`}><ChevronRight /></ChromeIcon>
    </div>
  )
}

/** A side panel (settings, contents) with a small-caps title and a close pill. */
export function ReaderPanel({ side, title, onClose, children, className }: {
  side: 'left' | 'right'
  title: ReactNode
  onClose: () => void
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'absolute inset-y-0 z-20 flex w-72 max-w-[85vw] shrink-0 flex-col overflow-y-auto bg-background',
        side === 'left' ? 'left-0 shadow-[1px_0_0_var(--border)]' : 'right-0 shadow-[-1px_0_0_var(--border)]',
        className,
      )}
    >
      <div className="flex items-center justify-between py-2 pl-5 pr-2">
        <span className="text-[12px] font-bold uppercase tracking-[0.1em] text-muted-foreground">{title}</span>
        <ChromeIcon onClick={onClose} title={t`Close`}><X /></ChromeIcon>
      </div>
      {children}
    </div>
  )
}

/** Theme choice: pills with a swatch of each page colour. */
export function ThemeSetting({ value, onChange }: { value: ReaderTheme; onChange: (t: ReaderTheme) => void }) {
  return (
    <div>
      <ChromeLabel><Trans>Theme</Trans></ChromeLabel>
      <PillGroup
        size="sm"
        aria-label={t`Theme`}
        value={value}
        onChange={onChange}
        options={(Object.keys(THEMES) as ReaderTheme[]).map(k => ({
          value: k,
          label: (
            <span className="inline-flex items-center gap-1.5">
              <span className="h-3 w-3 rounded-full" style={{ background: THEMES[k].bg, boxShadow: `inset 0 0 0 1px ${THEMES[k].text}55` }} />
              {i18n._(THEMES[k].label)}
            </span>
          ),
        }))}
      />
    </div>
  )
}

/** Font size stepper: two icon pills around the value. */
export function FontSizeSetting({ value, onChange }: { value: number; onChange: (delta: number) => void }) {
  return (
    <div>
      <ChromeLabel><Trans>Font size</Trans></ChromeLabel>
      <div className="flex items-center gap-3">
        <Pill icon onClick={() => onChange(-10)} disabled={value <= 60} aria-label={t`Smaller text`}><Minus className="h-4 w-4" /></Pill>
        <span className="w-14 text-center text-[14px] font-medium tabular-nums">{value}%</span>
        <Pill icon onClick={() => onChange(10)} disabled={value >= 200} aria-label={t`Larger text`}><Plus className="h-4 w-4" /></Pill>
      </div>
    </div>
  )
}

/** Generic labelled PillGroup row for the panel. */
export function ChoiceSetting<T extends string>({ label, value, onChange, options, hint }: {
  label: ReactNode
  value: T
  onChange: (v: T) => void
  options: { value: T; label: ReactNode }[]
  hint?: ReactNode
}) {
  return (
    <div>
      <ChromeLabel>{label}</ChromeLabel>
      <PillGroup size="sm" value={value} onChange={onChange} options={options} />
      {hint && <p className="mt-2 text-[13px] text-muted-foreground">{hint}</p>}
    </div>
  )
}


