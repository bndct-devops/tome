import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import { VitePWA } from 'vite-plugin-pwa'
import { lingui, linguiTransformerBabelPreset } from '@lingui/vite-plugin'
import babel from '@rolldown/plugin-babel'
import fs from 'fs'
import { createRequire } from 'module'

// Every Lucide icon by name, as its own lazily loaded chunk (src/lib/icons.tsx).
// Importing it from 'lucide-react' would keep the whole set in the main bundle,
// since the app imports single icons from there too; icons/index.js holds the
// primary names only, so the old names the picker also offered are mapped here.
function lucideAll() {
  const id = 'virtual:lucide-all'
  return {
    name: 'lucide-all',
    resolveId: (s: string) => (s === id ? '\0' + id : null),
    load(s: string) {
      if (s !== '\0' + id) return null
      const dir = path.dirname(createRequire(import.meta.url).resolve('lucide-react/dist/esm/lucide-react.js'))
      const fileToName: Record<string, string> = {}
      for (const m of fs.readFileSync(path.join(dir, 'icons/index.js'), 'utf8').matchAll(/default as (\w+) \} from '\.\/([\w-]+)\.js'/g)) fileToName[m[2]] = m[1]
      const aliases: Record<string, string> = {}
      for (const m of fs.readFileSync(path.join(dir, 'lucide-react.js'), 'utf8').matchAll(/export \{ ([^}]+) \} from '\.\/icons\/([\w-]+)\.js'/g)) {
        const primary = fileToName[m[2]]
        if (!primary) continue
        for (const a of m[1].matchAll(/default as (\w+)/g)) if (a[1] !== primary) aliases[a[1]] = primary
      }
      return `export * from ${JSON.stringify(path.join(dir, 'icons/index.js'))}\nexport const aliases = ${JSON.stringify(aliases)}\n`
    },
  }
}

export default defineConfig({
  // Some deps (react-draggable, prop-types — pulled in by react-grid-layout) read
  // process.env.NODE_ENV at runtime. Vite replaces it in production builds but not in
  // dev, so without this the browser hits an undefined `process` on first interaction.
  define: {
    'process.env.NODE_ENV': JSON.stringify(process.env.NODE_ENV || 'development'),
  },
  plugins: [
    react(),
    // Lingui: <Trans>, t`` and msg`` macros are compiled away by a Babel plugin.
    // @vitejs/plugin-react v6 no longer runs Babel itself, so the macro runs via
    // @rolldown/plugin-babel (the preset scopes it to our sources). lingui() turns
    // `import ... from './messages.po'` into compiled message catalogs.
    lingui(),
    babel({ presets: [linguiTransformerBabelPreset()] }),
    tailwindcss(),
    lucideAll(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'Tome',
        short_name: 'Tome',
        description: 'Self-hosted ebook library',
        theme_color: '#863bff',
        background_color: '#09090b',
        display: 'standalone',
        start_url: '/',
        icons: [
          {
            src: '/pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: '/pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
          },
          {
            src: '/pwa-512x512-maskable.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // The SPA navigation fallback serves index.html for navigations — but it
        // must NOT swallow full-page navigations to server routes, or the service
        // worker returns the app shell instead of letting them reach the backend.
        // This broke the OIDC handshake (window.location → /api/auth/oidc/login
        // and the IdP's redirect to /callback both got the SPA shell), and would
        // do the same to any /api or /opds navigation (downloads, plugin, feeds).
        navigateFallbackDenylist: [/^\/api\//, /^\/opds\//],
        // Cache static assets; skip large book files
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024, // 5 MB
        runtimeCaching: [
          {
            // Cache API responses for book metadata (not downloads)
            urlPattern: /^\/api\/books(\?.*)?$/,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-books',
              networkTimeoutSeconds: 10,
              expiration: {
                maxEntries: 50,
                maxAgeSeconds: 60 * 60, // 1 hour
              },
            },
          },
          {
            // Versioned covers (?v=updated_at) never change under their URL,
            // so keep them across restarts and browser cache clean-ups.
            // Workbox matches against the full URL, so no leading ^/.
            urlPattern: /\/api\/books\/\d+\/cover\?(?:.*&)?v=/,
            handler: 'CacheFirst',
            options: {
              cacheName: 'book-covers-v2',
              cacheableResponse: { statuses: [200] },
              expiration: {
                maxEntries: 5000,
                maxAgeSeconds: 365 * 24 * 60 * 60,
              },
            },
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: true,
    port: Number(process.env.VITE_PORT) || 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8080',
        changeOrigin: true,
      },
      '/opds': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
})
