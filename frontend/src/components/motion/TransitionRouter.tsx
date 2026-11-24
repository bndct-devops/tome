/**
 * TransitionRouter: BrowserRouter with a short cross-fade between pages.
 *
 *   <TransitionRouter><App /></TransitionRouter>   (in place of <BrowserRouter>)
 *
 * Moving to another page (another top-level path, another Dashboard tab,
 * another Settings or Admin section) runs one root view transition of 120 ms
 * (styles/view-transitions.css). The router's update is applied inside the
 * transition as a React transition, so a lazy page keeps the old one on
 * screen until its chunk is in, and the transition waits for that commit, at
 * most 250 ms (the cap lives in lib/coverTransition `capturedUpdate`, the one
 * place every view transition starts), then proceeds with whatever is there.
 * Input is never held: the transition overlay lets the pointer through, and a
 * navigation while one fades ends that fade at once.
 *
 * Skipped (the page swaps as before): replace navigations, changes inside one
 * page (filters, a series detail, a book's next volume), the reader, while a
 * cover transition runs (it brings its own), under reduced motion and where
 * the API is missing.
 */
import { startTransition, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Router, UNSAFE_createBrowserHistory as createBrowserHistory, type Location } from 'react-router-dom'
import { crossFade, insideTransitionUpdate, noteRouteCommit, routeCommitted, transitionsSupported } from '@/lib/coverTransition'

const SECTIONED = ['settings', 'admin']

/** What makes a page a different page: its first path segment, plus the tab
 *  on the Dashboard and the section on Settings and Admin. */
function pageOf(loc: Pick<Location, 'pathname' | 'search'>): string {
  const parts = loc.pathname.split('/').filter(Boolean)
  const head = parts[0] ?? ''
  if (head === '') return `/:${new URLSearchParams(loc.search).get('tab') ?? 'home'}`
  if (SECTIONED.includes(head)) return `${head}/${parts[1] ?? ''}`
  return head
}

type State = { action: ReturnType<typeof createBrowserHistory>['action']; location: Location }

export function TransitionRouter({ children }: { children: ReactNode }) {
  const [history] = useState(() => createBrowserHistory({ window, v5Compat: true }))
  const [state, setState] = useState<State>(() => ({ action: history.action, location: history.location }))
  // The location last handed to React.
  const shown = useRef<Location | null>(null)
  // History events heard so far, and whether an applied one has yet to commit:
  // a fade's update runs late, after newer events may have been applied.
  const seq = useRef(0)
  const pending = useRef(false)

  useLayoutEffect(() => history.listen(({ action, location }) => {
    const n = ++seq.current
    const prev = shown.current ?? location
    shown.current = location
    const apply = () => {
      pending.current = true
      startTransition(() => setState({ action, location }))
    }
    // Part of a transition already on screen (a cover, a section switch).
    if (insideTransitionUpdate()) { apply(); return }
    const head = location.pathname.split('/')[1]
    const skip = action === 'REPLACE'
      || pageOf(prev) === pageOf(location)
      || head === 'reader' || prev.pathname.startsWith('/reader')
      || !transitionsSupported()
    if (skip) { apply(); return }
    // Another fade still on screen: crossFade ends it and swaps plainly.
    crossFade(() => {
      // A newer event was applied before this ran: this location is stale and
      // must not land after it. Wait for that one's commit instead.
      if (n !== seq.current) return pending.current ? routeCommitted() : undefined
      const landed = routeCommitted()
      apply()
      return landed
    })
  }), [history])

  // The new page has committed: let a waiting transition capture it.
  useLayoutEffect(() => {
    shown.current = state.location
    pending.current = false
    noteRouteCommit()
  }, [state])

  return (
    <Router location={state.location} navigationType={state.action} navigator={history}>
      {children}
    </Router>
  )
}
