import { defineConfig } from 'astro/config'
import react from '@astrojs/react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  // Project Pages serves at https://bndct-devops.github.io/tome/ — both
  // settings below need to match that for asset URLs + sitemap to be correct.
  // Swap to a custom-domain `site` (and remove `base`) when DNS lands.
  site: 'https://bndct-devops.github.io',
  base: '/tome',
  integrations: [react()],
  vite: { plugins: [tailwindcss()] },
  // Bind to all interfaces so phones on the same wifi can hit the dev server.
  server: { host: true, port: 4321 },
  markdown: {
    shikiConfig: {
      themes: { light: 'github-light', dark: 'github-dark' },
      wrap: true,
    },
  },
})
