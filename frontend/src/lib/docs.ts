// Public docs site lives at tome.bndct.sh. Centralised here so contextual
// "Learn more →" links across the app point at one source of truth.

export const DOCS_BASE = 'https://tome.bndct.sh'

export const docsUrl = (path = ''): string => `${DOCS_BASE}${path}`

// Resolve the current Tome theme so we can pass it to the docs site as a
// query param — picks up where the app left off without a flash.
function currentTheme(): 'light' | 'dark' | 'amber' | null {
  if (typeof document === 'undefined') return null
  const cl = document.documentElement.classList
  if (cl.contains('theme-amber')) return 'amber'
  if (cl.contains('theme-dark')) return 'dark'
  if (cl.contains('theme-light')) return 'light'
  // Custom themes — fall back to whatever Tailwind's dark class says.
  return cl.contains('dark') ? 'dark' : 'light'
}

// Wrap any DOCS.* URL to forward the active theme. The docs site reads the
// param on first load, persists it, then strips it for a clean URL.
export function docsLink(url: string): string {
  const theme = currentTheme()
  if (!theme) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}theme=${theme}`
}

// Named pages — keeps deep-links typo-safe.
export const DOCS = {
  home:           docsUrl('/docs'),
  installation:   docsUrl('/docs/installation'),
  firstRun:       docsUrl('/docs/first-run'),
  reader:         docsUrl('/docs/reader'),
  series:         docsUrl('/docs/series'),
  stats:          docsUrl('/docs/stats'),
  bindery:        docsUrl('/docs/bindery'),
  koreader:       docsUrl('/docs/koreader'),
  opds:           docsUrl('/docs/opds'),
  scribe:         docsUrl('/docs/scribe'),
  apiTokens:      docsUrl('/docs/api-tokens'),
  configuration:  docsUrl('/docs/configuration'),
  usersAndRoles:  docsUrl('/docs/users-and-roles'),
  troubleshooting: docsUrl('/docs/troubleshooting'),
  changelog:      docsUrl('/docs/changelog'),
} as const
