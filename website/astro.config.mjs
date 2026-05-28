import { defineConfig } from 'astro/config'
import react from '@astrojs/react'
import sitemap from '@astrojs/sitemap'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  // Custom-domain deployment. CNAME file in public/ drives GitHub Pages.
  site: 'https://tome.bndct.sh',
  integrations: [react(), sitemap()],
  vite: {
    plugins: [tailwindcss()],
    // Force Vite to pre-bundle React's CJS entry points so named exports
    // (createRoot/hydrateRoot) resolve in dev — otherwise island hydration
    // fails with "does not provide an export named 'createRoot'".
    optimizeDeps: { include: ['react', 'react-dom', 'react-dom/client', 'react/jsx-runtime'] },
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
