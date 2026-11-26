import { defineConfig } from 'astro/config'
import react from '@astrojs/react'
import sitemap from '@astrojs/sitemap'
import tailwindcss from '@tailwindcss/vite'
import { lastUpdated, sourcesFor } from './src/lib/lastUpdated.mjs'

// Internal pages: built and reachable by URL, but kept out of the sitemap
// (each also carries a noindex robots tag).
const INTERNAL_PAGES = ['/mockups/', '/og/', '/preview-og/']

export default defineConfig({
  // Custom-domain deployment. CNAME file in public/ drives GitHub Pages.
  site: 'https://tome.bndct.sh',
  integrations: [
    react(),
    sitemap({
      filter: (page) => !INTERNAL_PAGES.includes(new URL(page).pathname),
      // lastmod from git history, so it only moves when the page really changed.
      serialize(item) {
        const date = lastUpdated(...sourcesFor(new URL(item.url).pathname))
        if (date) item.lastmod = date.toISOString()
        return item
      },
    }),
  ],
  vite: {
    plugins: [tailwindcss()],
    // Force Vite to pre-bundle React's CJS entry points so named exports
    // (createRoot/hydrateRoot) resolve in dev — otherwise island hydration
    // fails with "does not provide an export named 'createRoot'".
    optimizeDeps: { include: ['react', 'react-dom', 'react-dom/client', 'react/jsx-runtime'] },
    // The changelog page ?raw-imports the repo-root CHANGELOG.md (one level up
    // from the website root), so allow the dev server to read the parent dir.
    server: { fs: { allow: ['..'] } },
  },
  // Bind to all interfaces so phones on the same wifi can hit the dev server.
  server: { host: true, port: 4321 },
  markdown: {
    shikiConfig: {
      themes: { light: 'github-light', dark: 'github-dark' },
      wrap: true,
    },
  },
})
