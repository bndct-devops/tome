import { defineConfig } from 'astro/config'
import react from '@astrojs/react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  integrations: [react()],
  vite: { plugins: [tailwindcss()] },
  // Bind to all interfaces so phones on the same wifi can hit the dev server.
  server: { host: true, port: 4321 },
})
