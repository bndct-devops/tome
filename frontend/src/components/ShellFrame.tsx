import type { ReactNode } from 'react'

/**
 * The app's frame: sidebar on the left, header above the content, and the
 * content itself as a rounded panel set into the frame colour. Both shells
 * (AppShell for the standalone pages, DashboardPage for Home/Library/Series)
 * render through this so they cannot drift apart.
 *
 * h-dvh, not h-screen: 100vh overshoots the visible area under mobile browser
 * toolbars. On phones the panel is full-bleed; the inset only reads with a
 * sidebar next to it.
 */
export function ShellFrame({ sidebar, header, children }: { sidebar: ReactNode; header: ReactNode; children: ReactNode }) {
  return (
    <div className="h-dvh bg-frame flex overflow-hidden">
      {sidebar}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {header}
        {/* overflow-x-hidden: WebKit counts pre-transform layout boxes as
            scrollable overflow and lets the page pan sideways on phones. */}
        <main className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden overscroll-contain bg-background md:rounded-[var(--radius-panel)] md:mr-2 md:mb-2 md:ring-1 md:ring-panel-ring">
          {children}
        </main>
      </div>
    </div>
  )
}
