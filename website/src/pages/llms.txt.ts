// /llms.txt: a plain-text map of the site for AI assistants (llmstxt.org).
// Built from the docs navigation and each page's own description, so a new
// docs page shows up here without anyone remembering this file.
import type { APIRoute } from 'astro'
import { DOCS_NAV } from '../components/docs-nav'

const SITE = 'https://tome.bndct.sh'

// Raw source of every docs page, to read the description it passes to DocsLayout.
const sources = import.meta.glob('./docs/*.astro', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

function describe(href: string): string {
  const slug = href === '/docs' ? 'index' : href.replace('/docs/', '')
  const source = sources[`./docs/${slug}.astro`] ?? ''
  return source.match(/<DocsLayout[^>]*\bdescription="([^"]*)"/)?.[1] ?? ''
}

function line(title: string, path: string, description: string): string {
  const url = `${SITE}${path.endsWith('/') ? path : path + '/'}`
  return description ? `- [${title}](${url}): ${description}` : `- [${title}](${url})`
}

export const GET: APIRoute = () => {
  const out: string[] = [
    '# Tome',
    '',
    '> Tome is a free, open-source (AGPL-3.0), self-hosted ebook and manga library server. It has a built-in browser reader for EPUB, CBZ/CBR and PDF, a KOReader plugin (TomeSync) that records every reading session and syncs reading positions both ways, reading stats built from those sessions, and an OPDS feed. It runs as a single Docker image with SQLite and needs no external services.',
    '',
    'Key facts:',
    '',
    '- Source code: https://github.com/bndct-devops/tome',
    '- Container image: ghcr.io/bndct-devops/tome',
    '- Stack: Python (FastAPI), React, SQLite. One container, no separate database server.',
    '- Formats: EPUB, PDF, CBZ, CBR and MOBI.',
    '- E-readers: KOReader through the TomeSync plugin or the standard KOSync protocol, any OPDS client, and Xteink readers on CrossPoint or CrossInk firmware (experimental).',
    '- Multi-user with three roles (admin, member, guest), per-user libraries and optional single sign-on (OIDC).',
    '- Not included: reading an existing Calibre database, format conversion, Kobo store sync (in the works).',
    '',
    '## Overview',
    '',
    line('Why Tome', '/why', 'How Tome compares with Calibre-Web, Kavita, Komga, Grimmory, BookOrbit and other self-hosted ebook servers, and when each is the better choice.'),
    line('Performance benchmark', '/benchmark', 'How Tome ingests large ebook libraries, with measured numbers.'),
    line('Blog', '/blog', 'Posts about building Tome, the KOReader plugin and the iPhone app.'),
  ]

  for (const group of DOCS_NAV) {
    out.push('', `## Docs: ${group.label}`, '')
    for (const item of group.items) out.push(line(item.title, item.href, describe(item.href)))
  }

  return new Response(out.join('\n') + '\n', {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  })
}
