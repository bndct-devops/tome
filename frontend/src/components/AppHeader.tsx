import { type ReactNode, type RefObject } from 'react'
import { Menu, Search, Upload, X } from 'lucide-react'
import { useAuth, isMember } from '@/contexts/AuthContext'
import { TomeMark } from '@/components/TomeMark'
import { SyncStatusBadge } from '@/components/SyncStatusBadge'
import { NotificationBell } from '@/components/NotificationBell'
import { WhatsNewPanel } from '@/components/WhatsNewPanel'
import { CommandPalette } from '@/components/CommandPalette'

/**
 * The one top navbar — menu button, wordmark, a search slot, and the right-side
 * cluster (page actions, sync badge, bell, Upload). Rendered by AppShell for
 * the standalone pages and by DashboardPage directly, so the two can't drift
 * apart again (the AppShell copy once shipped an Upload button whose icon was
 * missing — a blank pill on phones).
 */
export function AppHeader({ onMenuClick, search, actions, onUploadClick }: {
  onMenuClick: () => void
  search: ReactNode
  actions?: ReactNode
  onUploadClick?: () => void
}) {
  const { user } = useAuth()
  // z-40: above content-level stickies (page toolbars at z-20, timeline axis at
  // z-30) so the notification dropdown isn't cut, below modals at z-50.
  return (
    <header className="border-b border-border bg-card/50 backdrop-blur-sm sticky top-0 z-40 shrink-0 safe-top">
      <div className="px-4 h-14 flex items-center gap-3">
        <button
          className="md:hidden flex items-center justify-center w-8 h-8 text-muted-foreground hover:text-foreground transition-colors rounded-lg hover:bg-muted shrink-0"
          onClick={onMenuClick}
          aria-label="Open navigation"
        >
          <Menu className="w-5 h-5" />
        </button>
        <div className="flex items-center gap-2 mr-2 shrink-0 group cursor-default">
          <TomeMark className="w-5 h-5 text-primary logo-bob" strokeWidth={7} />
          <span className="font-semibold text-sm">Tome</span>
        </div>
        {search}
        <div className="flex items-center gap-1.5 ml-auto">
          {actions}
          <SyncStatusBadge />
          <NotificationBell />
          {onUploadClick && isMember(user) && (
            <button
              onClick={onUploadClick}
              // The bar's one real action — solid accent so it anchors the right
              // cluster instead of blending into the neutral chips around it.
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-primary text-primary-foreground hover:bg-primary/90 transition-all touch-feedback"
            >
              <Upload className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Upload</span>
            </button>
          )}
        </div>
      </div>
      {/* Post-upgrade release-notes panel — mounted here because both shell
          variants (AppShell and DashboardPage) render this header. */}
      <WhatsNewPanel />
      {/* Cmd+K palette — same reasoning: one mount that exists on every page. */}
      <CommandPalette />
    </header>
  )
}

/** The header's search box. With `onSubmit` it's a form (Enter submits — the
 *  standalone pages jump to the dashboard results); without, a live filter. */
export function HeaderSearch({ value, onChange, onClear, onSubmit, inputRef, placeholder = 'Search books… (/)' }: {
  value: string
  onChange: (v: string) => void
  onClear: () => void
  onSubmit?: (e: React.FormEvent) => void
  inputRef?: RefObject<HTMLInputElement | null>
  placeholder?: string
}) {
  const inner = (
    <>
      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
      <input
        ref={inputRef}
        aria-label="Search books"
        // On phones the box can shrink to a sliver (page actions squeeze it) and
        // the placeholder clips mid-letter — hide it there; the icon says enough.
        // Rests quiet (tinted fill, no border), sharpens on focus — filled AND
        // bordered at rest made it the heaviest element in the whole bar.
        className="w-full h-8 pl-9 pr-8 rounded-lg bg-muted/60 border border-transparent text-sm placeholder:text-muted-foreground max-sm:placeholder:text-transparent transition-colors hover:bg-muted focus:bg-card focus:border-border focus:outline-none focus:ring-1 focus:ring-ring"
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
      />
      {value && (
        <button
          type="button"
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          aria-label="Clear search"
          onClick={onClear}
        >
          <X className="w-3.5 h-3.5" />
        </button>
      )}
    </>
  )
  return onSubmit ? (
    <form onSubmit={onSubmit} className="relative flex-1 sm:max-w-md">{inner}</form>
  ) : (
    <div className="relative flex-1 sm:max-w-md">{inner}</div>
  )
}
