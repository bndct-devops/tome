import { type ReactNode, type RefObject } from 'react'
import { Menu, Search, Upload, X } from 'lucide-react'
import { useAuth, isMember } from '@/contexts/AuthContext'
import { TomeMark } from '@/components/TomeMark'
import { SyncStatusBadge } from '@/components/SyncStatusBadge'
import { NotificationBell } from '@/components/NotificationBell'

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
  return (
    <header className="border-b border-border bg-card/50 backdrop-blur-sm sticky top-0 z-20 shrink-0 safe-top">
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
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-border bg-card hover:bg-muted transition-all touch-feedback"
            >
              <Upload className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Upload</span>
            </button>
          )}
        </div>
      </div>
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
        className="w-full h-8 pl-9 pr-8 rounded-lg bg-muted border border-border text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
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
