import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Trans } from '@lingui/react/macro'
import { AlertCircle, RotateCw } from 'lucide-react'

// After a deploy, an open tab still points at the old build's chunk names, so
// opening a page that was not loaded yet fails to import its script. One
// automatic reload picks up the new build. The timestamp guard stops a loop
// when the reload does not help (server down, broken build).
const RELOAD_KEY = 'tome_chunk_reload_at'
const RELOAD_GUARD_MS = 30_000

export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  const text = `${error.name} ${error.message}`
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|ChunkLoadError|Unable to preload CSS/i.test(text)
}

/** Reload once for a failed chunk import. Returns false when a reload was tried recently. */
export function reloadForChunkError(): boolean {
  try {
    const last = Number(sessionStorage.getItem(RELOAD_KEY) || 0)
    if (Date.now() - last < RELOAD_GUARD_MS) return false
    sessionStorage.setItem(RELOAD_KEY, String(Date.now()))
  } catch {
    // No sessionStorage: never reload automatically, show the fallback instead.
    return false
  }
  window.location.reload()
  return true
}

interface Props {
  /** Changing this (the current path) clears a caught error, so navigating away recovers. */
  resetKey: string
  children: ReactNode
}

interface State {
  error: Error | null
}

export class RouteErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Page failed to render', error, info.componentStack)
    if (isChunkLoadError(error)) reloadForChunkError()
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    if (this.state.error) return <ErrorFallback />
    return this.props.children
  }
}

function ErrorFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="max-w-sm w-full rounded-xl bg-card ring-1 ring-panel-ring p-6 text-center">
        <AlertCircle className="w-6 h-6 text-destructive mx-auto mb-3" />
        <h1 className="text-[15px] font-semibold text-foreground">
          <Trans>This page could not be shown</Trans>
        </h1>
        <p className="mt-1.5 text-[13px] text-muted-foreground leading-snug">
          <Trans>Tome may have been updated since this tab was opened. Reloading usually fixes it.</Trans>
        </p>
        <button
          onClick={() => window.location.reload()}
          className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-[13px] font-medium hover:opacity-90 transition-opacity"
        >
          <RotateCw className="w-3.5 h-3.5" />
          <Trans>Reload</Trans>
        </button>
      </div>
    </div>
  )
}
