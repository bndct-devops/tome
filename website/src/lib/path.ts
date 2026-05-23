// Prefix internal absolute paths with Astro's configured `base`. External URLs
// (http://, mailto:, anchors) and relative paths pass through untouched.
//
// Use everywhere a hardcoded "/docs/foo" or "/shots/x.png" would otherwise be
// emitted — otherwise the deployed site at /tome/ will 404.

const RAW_BASE = import.meta.env.BASE_URL || '/'
const BASE = RAW_BASE === '/' ? '' : RAW_BASE.replace(/\/$/, '')

export function withBase(path: string): string {
  if (!path) return path
  if (/^([a-z]+:|\/\/|#|mailto:|tel:)/i.test(path)) return path
  if (!path.startsWith('/')) return path
  return `${BASE}${path}`
}

// Strip the base prefix from a runtime pathname so it can be compared against
// logical paths like "/docs/foo" (used by sidebar isActive checks etc.).
export function stripBase(pathname: string): string {
  if (!BASE) return pathname
  return pathname.startsWith(BASE) ? pathname.slice(BASE.length) || '/' : pathname
}
