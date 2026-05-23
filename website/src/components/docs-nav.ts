// Single source of truth for the docs sidebar & prev/next navigation.
// Edit here to add or reorder pages.
export interface DocItem { href: string; title: string }
export interface DocGroup { label: string; items: DocItem[] }

export const DOCS_NAV: DocGroup[] = [
  {
    label: 'Getting started',
    items: [
      { href: '/docs',                    title: 'Welcome' },
      { href: '/docs/installation',       title: 'Installation' },
      { href: '/docs/first-run',          title: 'First-run setup' },
    ],
  },
  {
    label: 'Features',
    items: [
      { href: '/docs/reader',             title: 'Built-in reader' },
      { href: '/docs/series',             title: 'Series & arcs' },
      { href: '/docs/stats',              title: 'Reading stats' },
      { href: '/docs/bindery',            title: 'Bindery (auto-import)' },
    ],
  },
  {
    label: 'Integrations',
    items: [
      { href: '/docs/koreader',           title: 'KOReader plugin' },
      { href: '/docs/opds',               title: 'OPDS feed' },
      { href: '/docs/scribe',             title: 'Scribe (CLI)' },
      { href: '/docs/api-tokens',         title: 'API tokens' },
    ],
  },
  {
    label: 'Reference',
    items: [
      { href: '/docs/configuration',      title: 'Configuration & env vars' },
      { href: '/docs/users-and-roles',    title: 'Users, roles, libraries' },
      { href: '/docs/troubleshooting',    title: 'Troubleshooting' },
      { href: '/docs/changelog',          title: 'Changelog' },
    ],
  },
]

// Flat list, in order — used to compute prev/next.
export const DOCS_FLAT: (DocItem & { groupLabel: string })[] = DOCS_NAV.flatMap(g =>
  g.items.map(item => ({ ...item, groupLabel: g.label })),
)

export function findAdjacent(path: string) {
  const idx = DOCS_FLAT.findIndex(item => item.href === path)
  if (idx === -1) return { prev: null, next: null, current: null }
  return {
    prev: idx > 0 ? DOCS_FLAT[idx - 1] : null,
    next: idx < DOCS_FLAT.length - 1 ? DOCS_FLAT[idx + 1] : null,
    current: DOCS_FLAT[idx],
  }
}
