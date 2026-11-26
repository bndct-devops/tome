// Last-change date of a source file, taken from git history at build time.
// File mtime is useless here: in CI every file's mtime is the checkout time,
// so every page would claim it was updated on the day of the deploy.
//
// Returns null when there is no trustworthy answer (no git, an untracked file,
// or a shallow clone, where every file would report the one fetched commit).
// Callers then show no date at all, which is better than a wrong one.
import { execFileSync } from 'node:child_process'
import path from 'node:path'

const cache = new Map()
let shallow

function git(args, cwd) {
  return execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
}

function isShallow(cwd) {
  if (shallow === undefined) {
    try {
      shallow = git(['rev-parse', '--is-shallow-repository'], cwd) !== 'false'
    } catch {
      shallow = true
    }
  }
  return shallow
}

/**
 * Newest commit date among the given files (paths relative to the website
 * root, `..` allowed for repo-root files such as CHANGELOG.md).
 * @param {...string} files
 * @returns {Date | null}
 */
export function lastUpdated(...files) {
  const cwd = process.cwd()
  if (isShallow(cwd)) return null
  let newest = null
  for (const file of files) {
    const abs = path.resolve(cwd, file)
    if (!cache.has(abs)) {
      let date = null
      try {
        const out = git(['log', '-1', '--format=%cI', '--', abs], cwd)
        if (out) date = new Date(out)
      } catch { /* no git: leave null */ }
      cache.set(abs, date)
    }
    const date = cache.get(abs)
    if (date && (!newest || date > newest)) newest = date
  }
  return newest
}

/**
 * Source files behind a built URL path such as `/docs/koreader/`.
 * @param {string} pathname
 * @returns {string[]}
 */
export function sourcesFor(pathname) {
  const clean = pathname.replace(/^\/+|\/+$/g, '')
  const files = clean
    ? [`src/pages/${clean}.astro`, `src/pages/${clean}/index.astro`]
    : ['src/pages/index.astro']
  // The changelog page renders the repo-root CHANGELOG.md.
  if (clean === 'docs/changelog') files.push('../CHANGELOG.md')
  return files
}

/**
 * @param {Date} date
 * @returns {string} e.g. "22 Nov 2026"
 */
export function formatDate(date) {
  return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
}
