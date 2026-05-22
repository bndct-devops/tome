// Tiny event-based theme store. Shared across React islands without a
// store library — toggle dispatches an event, listeners react. Initial
// value is read from localStorage / system preference.
export type Theme = 'light' | 'dark' | 'amber'

const KEY = 'tome_site_theme'
const EVT = 'tome:theme'

export function readTheme(): Theme {
  if (typeof window === 'undefined') return 'light'
  const stored = localStorage.getItem(KEY) as Theme | null
  if (stored === 'light' || stored === 'dark' || stored === 'amber') return stored
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function setTheme(theme: Theme, originX?: number, originY?: number): void {
  const apply = () => {
    localStorage.setItem(KEY, theme)
    const root = document.documentElement
    root.classList.remove('theme-light', 'theme-dark', 'theme-amber')
    root.classList.add(`theme-${theme}`)
    document.dispatchEvent(new CustomEvent<Theme>(EVT, { detail: theme }))
  }
  // Use the View Transitions API for a radial sweep when available
  const vt = (document as any).startViewTransition?.bind(document)
  if (!vt) return apply()
  if (originX !== undefined && originY !== undefined) {
    document.documentElement.style.setProperty('--vt-origin-x', `${originX}px`)
    document.documentElement.style.setProperty('--vt-origin-y', `${originY}px`)
  }
  vt(apply)
}

export function subscribe(cb: (t: Theme) => void): () => void {
  const handler = (e: Event) => cb((e as CustomEvent<Theme>).detail)
  document.addEventListener(EVT, handler)
  return () => document.removeEventListener(EVT, handler)
}
